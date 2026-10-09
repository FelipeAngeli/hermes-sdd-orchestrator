#!/usr/bin/env python3
"""Canonical Claude/Codex executor launcher; standard library only.

The controller never hand-builds an executor command line. This module derives
the transport schema, checks the CLI, builds the exact argv from
``policies/EXECUTORS.md`` and runs one journaled dispatch in the foreground with
a hard timeout. Process completion is always recorded in the action journal.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from os import environ as process_environment
from pathlib import Path
from typing import Any

RUNTIME_ROOT = Path(__file__).resolve().parent
ORCHESTRATION_ROOT = RUNTIME_ROOT.parent
#: Directory holding ``.hermes/`` (the repository, or the Obsidian project container).
CONTROLLER_ROOT = ORCHESTRATION_ROOT.parent.parent
CONTROLLER_HERMES_ROOT = ORCHESTRATION_ROOT.parent
SCHEMAS_ROOT = ORCHESTRATION_ROOT / "schemas"
SUB_AGENTS_ROOT = ORCHESTRATION_ROOT / "sub-agents"
DEFAULT_POLICY_PATH = ORCHESTRATION_ROOT / "policies" / "EXECUTORS.md"
ACTION_JOURNAL_SCRIPT = RUNTIME_ROOT / "action_journal.py"
VALIDATE_PROTOCOL_SCRIPT = RUNTIME_ROOT / "validate_protocol.py"

EXECUTORS = ("claude", "codex")
STAGES = ("SPECIFY", "CLARIFY", "PLAN", "TASKS", "IMPLEMENT", "TEST", "REVIEW")
CODEX_SANDBOXES = ("read-only", "workspace-write")
# Schema keywords removed from every transport schema: the Claude CLI rejects a
# ``$schema`` meta-schema reference it cannot resolve, and identifiers/comments
# carry no validation meaning. Local ``$ref`` values are inlined and ``$defs``
# dropped so the schema is self-contained for both CLIs.
TRANSPORT_DROPPED_KEYWORDS = ("$schema", "$id", "$comment")
LAUNCH_STATUSES = (
    "READY", "BLOCKED", "ARTIFACT_READY", "OUTPUT_INVALID", "EXECUTOR_NO_OUTPUT", "EXECUTOR_FAILED",
    "EXECUTOR_TIMEOUT", "LAUNCHER_ERROR", "JOURNAL_FINISH_FAILED", "DISPATCH_NOT_ALLOWED",
    "JOURNAL_NOT_PREPARED", "JOURNAL_MISMATCH", "PROMPT_HASH_MISMATCH", "ARTIFACT_PENDING",
    "EXECUTOR_UNAVAILABLE", "JOURNAL_REFUSED", "POLICY_INVALID", "ROLE_UNKNOWN", "STAGE_UNKNOWN",
    "PROMPT_MISSING", "REPOSITORY_INVALID", "SCHEMA_UNSUPPORTED", "ADD_DIR_WRITABLE_REFUSED", "EXECUTOR_POLICY_UNSAFE",
    "CONTROLLER_WRITABLE_BY_WORKER", "LAUNCHER_INTERRUPTED",
)
PREFLIGHT_REASONS = ("EXECUTOR_UNAVAILABLE", "EXECUTOR_CLI_UNSUPPORTED", "MODEL_INVALID", "MODEL_REJECTED")
MODEL_CHECKS = ("NOT_PROBED", "ACCEPTED", "REJECTED", "TIMEOUT")
POLICY_SOURCES = ("FILE", "DEFAULTS")
DESCRIPTION = "Canonical Claude/Codex executor launcher for journaled SDD dispatch."
TIMEOUT_EXIT_CODE = 124
LAUNCHER_ERROR_EXIT_CODE = 125
NOT_FOUND_EXIT_CODE = 127
TERMINATE_GRACE_SECONDS = 5
TAIL_CHARACTERS = 2000
PROBE_TIMEOUT_SECONDS = 120
MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\[\]-]{0,127}$")
REQUIRED_HELP_FLAGS = {
    "claude": ("--print", "--output-format", "--json-schema", "--no-session-persistence", "--model", "--tools", "--add-dir"),
    "codex": ("--output-schema", "--output-last-message", "--ephemeral", "--model", "--sandbox", "--cd", "--add-dir", "--config"),
}

DEFAULT_STAGE_POLICY: dict[str, dict[str, Any]] = {
    "SPECIFY": {"executor": "claude", "model": None, "timeout_seconds": 600, "max_turns": 40},
    "CLARIFY": {"executor": "claude", "model": None, "timeout_seconds": 600, "max_turns": 40},
    "PLAN": {"executor": "claude", "model": None, "timeout_seconds": 900, "max_turns": 60},
    "TASKS": {"executor": "codex", "model": None, "timeout_seconds": 600, "max_turns": None},
    "IMPLEMENT": {"executor": "codex", "model": None, "timeout_seconds": 900, "max_turns": None},
    "TEST": {"executor": "codex", "model": None, "timeout_seconds": 600, "max_turns": None},
    "REVIEW": {"executor": "claude", "model": None, "timeout_seconds": 600, "max_turns": 40},
}
STAGE_KEYS = {"executor", "model", "timeout_seconds", "max_turns", "tools", "permission_mode", "sandbox"}
DEFAULT_CLAUDE_TOOLS = "Read,Grep,Glob"
#: Claude tools that cannot change a file; any other tool (or ``default``) makes the worker a writer.
READ_ONLY_CLAUDE_TOOLS = frozenset({"Read", "Grep", "Glob", "LS", "NotebookRead"})
WRITING_STAGES = {"IMPLEMENT", "TEST"}
POLICY_KEYS = {"executors_version", "stages", "allow_bypass_permissions"}
BYPASS_PERMISSION_MODE = "bypassPermissions"
#: Codex treats ``--add-dir`` as an extra *writable* root; a writing Codex worker gets none at all.
CODEX_NO_EXTRA_WRITABLE_ROOTS = "sandbox_workspace_write.writable_roots=[]"
#: Environment the worker inherits: nothing else (no TYPESAFE_*, JEV_*, HERMES_* or foreign *_API_KEY/*_TOKEN).
WORKER_ENV_NAMES = frozenset({
    "PATH", "HOME", "LANG", "TMPDIR", "TEMP", "TMP", "USER", "LOGNAME", "SHELL", "TERM", "TZ", "COLORTERM",
    "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "no_proxy", "all_proxy",
})
WORKER_ENV_PREFIXES = ("LC_",)
EXECUTOR_ENV_PREFIXES = {"claude": ("ANTHROPIC_", "CLAUDE_"), "codex": ("OPENAI_", "CODEX_")}


class LaunchError(Exception):
    def __init__(self, status: str, message: str, next_step: str, next_command: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.next_step = next_step
        self.next_command = next_command

    def payload(self) -> dict[str, Any]:
        return {"status": self.status, "message": str(self), "next_step": self.next_step, "next_command": self.next_command}


def _command(*parts: str | Path) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def _python() -> str:
    return sys.executable or "python3"


# --------------------------------------------------------------------------- policy

def _extract_json_block(text: str) -> str | None:
    match = re.search(r"^```json[ \t]*\n(.*?)^```[ \t]*$", text, re.M | re.S)
    return match.group(1) if match else None


def load_policy(path: Path | None = None) -> dict[str, Any]:
    """Return the validated stage → executor policy, falling back to defaults when the file is absent."""
    policy_path = path or DEFAULT_POLICY_PATH
    fix = f"Fix the fenced json block in {policy_path} (see policies/EXECUTORS.md in the template)."
    stages = copy.deepcopy(DEFAULT_STAGE_POLICY)
    if not policy_path.is_file():
        return {"source": "DEFAULTS", "path": str(policy_path), "stages": stages, "allow_bypass_permissions": False}
    block = _extract_json_block(policy_path.read_text(encoding="utf-8"))
    if block is None:
        raise LaunchError("POLICY_INVALID", "EXECUTORS.md has no fenced json block.", fix)
    try:
        value = json.loads(block)
    except json.JSONDecodeError as exc:
        raise LaunchError("POLICY_INVALID", f"EXECUTORS.md json block is not valid JSON: {exc}", fix) from exc
    if not isinstance(value, dict) or set(value) - POLICY_KEYS or value.get("executors_version") != 1:
        raise LaunchError("POLICY_INVALID", "EXECUTORS.md must contain {executors_version: 1, stages: {...}} and optionally allow_bypass_permissions only.", fix)
    allow_bypass = value.get("allow_bypass_permissions", False)
    if not isinstance(allow_bypass, bool):
        raise LaunchError("POLICY_INVALID", "allow_bypass_permissions must be true or false.", fix)
    configured = value.get("stages", {})
    if not isinstance(configured, dict):
        raise LaunchError("POLICY_INVALID", "stages must be an object.", fix)
    for stage, entry in configured.items():
        if stage not in STAGES:
            raise LaunchError("POLICY_INVALID", f"Unknown stage {stage!r}; allowed: {', '.join(STAGES)}.", fix)
        if not isinstance(entry, dict) or set(entry) - STAGE_KEYS:
            raise LaunchError("POLICY_INVALID", f"{stage} accepts only {sorted(STAGE_KEYS)}.", fix)
        merged = {**stages[stage], **entry}
        _validate_stage_entry(stage, merged, fix)
        if merged.get("permission_mode") == BYPASS_PERMISSION_MODE and not allow_bypass:
            raise LaunchError("EXECUTOR_POLICY_UNSAFE",
                              f"{stage}: permission_mode {BYPASS_PERMISSION_MODE} lets the worker run any tool without a check.",
                              f"Remove permission_mode from {stage} in {policy_path}, or, as the project owner who accepts that risk, "
                              "add \"allow_bypass_permissions\": true next to executors_version.")
        stages[stage] = merged
    return {"source": "FILE", "path": str(policy_path), "stages": stages, "allow_bypass_permissions": allow_bypass}


def _validate_stage_entry(stage: str, entry: dict[str, Any], fix: str) -> None:
    def bad(message: str) -> LaunchError:
        return LaunchError("POLICY_INVALID", f"{stage}: {message}", fix)

    if entry["executor"] not in EXECUTORS:
        raise bad(f"executor must be one of {EXECUTORS}.")
    model = entry.get("model")
    if model is not None and (not isinstance(model, str) or not MODEL_PATTERN.match(model)):
        raise bad("model must be null or a plain model name.")
    timeout = entry.get("timeout_seconds")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 7200:
        raise bad("timeout_seconds must be an integer between 1 and 7200.")
    turns = entry.get("max_turns")
    if turns is not None and (not isinstance(turns, int) or isinstance(turns, bool) or not 1 <= turns <= 500):
        raise bad("max_turns must be null or an integer between 1 and 500.")
    tools = entry.get("tools")
    if tools is not None and not isinstance(tools, str):
        raise bad("tools must be a string such as \"Read,Grep,Glob\", \"\" or \"default\".")
    mode = entry.get("permission_mode")
    if mode is not None and (not isinstance(mode, str) or not re.fullmatch(r"[A-Za-z]+", mode)):
        raise bad("permission_mode must be null or a Claude permission mode name.")
    sandbox = entry.get("sandbox")
    if sandbox is not None and sandbox not in CODEX_SANDBOXES:
        raise bad(f"sandbox must be null or one of {CODEX_SANDBOXES}.")


def stage_settings(policy: dict[str, Any], stage: str, *, executor: str | None, model: str | None, timeout: int | None) -> dict[str, Any]:
    if stage not in STAGES:
        raise LaunchError("STAGE_UNKNOWN", f"Unknown stage {stage!r}.", f"Use one of {', '.join(STAGES)}.")
    settings = dict(policy["stages"][stage])
    if executor:
        settings["executor"] = executor
    if model:
        settings["model"] = model
    if timeout is not None:
        settings["timeout_seconds"] = timeout
    _validate_stage_entry(stage, settings, "Pass a valid --executor, --model or --timeout override.")
    if settings["executor"] == "claude":
        settings.setdefault("tools", None)
        if settings["tools"] is None:
            settings["tools"] = DEFAULT_CLAUDE_TOOLS
    elif settings.get("sandbox") is None:
        settings["sandbox"] = "workspace-write" if stage in WRITING_STAGES else "read-only"
    return settings


def is_writing_worker(settings: dict[str, Any]) -> bool:
    """True when the worker can change files: a Codex workspace-write sandbox or Claude with any non-read tool."""
    if settings["executor"] == "codex":
        return settings.get("sandbox") == "workspace-write"
    tools = (settings.get("tools") or "").strip()
    if tools == "default":
        return True
    return any(tool.strip() and tool.strip() not in READ_ONLY_CLAUDE_TOOLS for tool in tools.split(","))


def _inside(path: Path, root: Path) -> bool:
    resolved, base = path.resolve(), root.resolve()
    return resolved == base or base in resolved.parents


def check_extra_dirs(stage: str, settings: dict[str, Any], repository: Path, add_dirs: list[str], read_dirs: list[str]) -> None:
    """Refuse any extra directory a writing worker could write outside the repository (ADD_DIR_WRITABLE_REFUSED).

    ``--add-dir`` is writable for Codex and for a Claude worker with write tools, so a writing worker
    gets it only inside the repository and never under the controller's ``.hermes`` directory.
    ``--read-dir`` is only for reading: a read-only Claude worker receives it as ``--add-dir``; Codex
    never needs it (both sandboxes read the filesystem); a writing Claude worker cannot hold it.
    """
    writing = is_writing_worker(settings)
    for directory in add_dirs:
        path = Path(directory)
        if not path.is_absolute():
            raise LaunchError("ADD_DIR_WRITABLE_REFUSED", f"--add-dir {directory} must be an absolute path.", "Pass absolute directories only.")
        if writing and (not _inside(path, repository) or _inside(path, CONTROLLER_HERMES_ROOT)):
            raise LaunchError("ADD_DIR_WRITABLE_REFUSED",
                              f"{stage} runs a writing worker ({settings['executor']}); --add-dir {directory} would be writable outside the repository.",
                              "Drop --add-dir. Pass directories the worker only reads with --read-dir; the controller container and runtime are never writable by a worker.")
    for directory in read_dirs:
        if not Path(directory).is_absolute():
            raise LaunchError("ADD_DIR_WRITABLE_REFUSED", f"--read-dir {directory} must be an absolute path.", "Pass absolute directories only.")
        if writing and settings["executor"] == "claude" and not _inside(Path(directory), repository):
            raise LaunchError("ADD_DIR_WRITABLE_REFUSED",
                              f"{stage} gives Claude write tools ({settings.get('tools')}); Claude --add-dir {directory} would also be writable.",
                              "Run this stage on codex (its workspace-write sandbox reads the filesystem and writes only the repository), "
                              "or keep the Claude tools read-only (Read,Grep,Glob) in policies/EXECUTORS.md.")


MIGRATE_SCRIPT = RUNTIME_ROOT / "migrate_to_vault.py"


def check_controller_isolation(stage: str, settings: dict[str, Any], repository: Path) -> None:
    """Refuse to dispatch a writing worker that shares a filesystem with the controller.

    A worker that can write the repository can write everything the controller keeps
    there: ``policies/GATES.md`` (whose commands run on the host, outside every worker
    sandbox), ``policies/EXECUTORS.md`` (which chooses the next worker's binary, tools
    and sandbox), ``runtime/*.py`` (the controller's own code), the stage briefs, STATE
    and the action journal.

    Detection cannot close this: every anchor the controller could compare against — the
    pinned policy hashes in STATE, ``fingerprints.state_before`` in the journal, the
    journal history — is itself written inside that same repository, so a worker that
    edits a file and its anchor in one step leaves nothing to detect. The anchor is only
    evidence when it is out of the worker's reach.

    So the dispatch is refused instead. The controller must live outside the repository
    (Obsidian storage), which is prevention rather than detection: the container is never
    a writable root for any worker. A read-only worker is unaffected — it cannot rewrite
    the controller in the first place.
    """
    if not is_writing_worker(settings):
        return
    if not _inside(CONTROLLER_HERMES_ROOT, repository):
        return
    raise LaunchError(
        "CONTROLLER_WRITABLE_BY_WORKER",
        f"{stage} runs a writing worker ({settings['executor']}) whose writable root {repository} contains the controller "
        f"at {CONTROLLER_HERMES_ROOT}: it could rewrite the gate commands, the executor policy, the controller's own runtime, "
        "STATE or the journal, together with every hash the controller would check them against.",
        "Move the controller out of the repository before any writing stage: `migrate_to_vault.py --repo <repository> --apply` "
        "installs it in the Obsidian container, which is never a writable root for a worker. Until then this stage cannot be "
        "dispatched; a read-only stage still runs. Run it through `sdd.py`, which prints the exact command and records the stop.",
        _command(_python(), MIGRATE_SCRIPT, "--repo", repository, "--apply"))


# --------------------------------------------------------------------------- schema

def _resolve_pointer(document: dict[str, Any], reference: str) -> Any:
    if not reference.startswith("#/"):
        raise LaunchError("SCHEMA_UNSUPPORTED", f"Only local $ref values are supported: {reference}", "Inline the remote schema in the source schema file.")
    node: Any = document
    for raw in reference[2:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or key not in node:
            raise LaunchError("SCHEMA_UNSUPPORTED", f"Unresolvable $ref {reference}", "Fix the $ref in the source schema file.")
        node = node[key]
    return node


def transport_schema(source: dict[str, Any]) -> dict[str, Any]:
    """Return a self-contained schema both CLIs accept: no $schema/$id/$comment, local $refs inlined, no $defs."""

    def inline(node: Any, stack: tuple[str, ...]) -> Any:
        if isinstance(node, list):
            return [inline(item, stack) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            reference = node["$ref"]
            if reference in stack:
                raise LaunchError("SCHEMA_UNSUPPORTED", f"Recursive $ref {reference} cannot be inlined.", "Remove the recursion from the source schema.")
            target = inline(_resolve_pointer(source, reference), (*stack, reference))
            siblings = {key: inline(value, stack) for key, value in node.items() if key != "$ref" and key not in TRANSPORT_DROPPED_KEYWORDS}
            return {**target, **siblings} if isinstance(target, dict) else target
        return {
            key: inline(value, stack)
            for key, value in node.items()
            if key not in TRANSPORT_DROPPED_KEYWORDS and key not in {"$defs", "definitions"}
        }

    return inline(source, ())


def _frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    block = text.split("---", 2)[1]
    return dict(line.split(": ", 1) for line in block.strip().splitlines() if ": " in line)


def schema_path_for(stage: str, role: str | None) -> Path:
    if stage not in STAGES:
        raise LaunchError("STAGE_UNKNOWN", f"Unknown stage {stage!r}.", f"Use one of {', '.join(STAGES)}.")
    if role:
        for brief in sorted(SUB_AGENTS_ROOT.glob("*.md")):
            meta = _frontmatter(brief)
            if meta.get("role") != role:
                continue
            allowed = [item.strip() for item in meta.get("allowed_stages", "").strip("[]").split(",") if item.strip()]
            if stage not in allowed:
                raise LaunchError("ROLE_UNKNOWN", f"Role {role} is not allowed in {stage} (allowed: {allowed}).", "Pick a role whose sub-agent brief lists this stage in allowed_stages.")
            return (brief.parent / meta["result_schema"]).resolve()
        raise LaunchError("ROLE_UNKNOWN", f"No sub-agent brief declares role {role}.", "Use the `role:` value of a file under sub-agents/.")
    name = "REVIEW_RESULT_SCHEMA.json" if stage == "REVIEW" else "EXECUTOR_RESULT_SCHEMA.json"
    return SCHEMAS_ROOT / name


def stage_transport_schema(stage: str, role: str | None = None) -> dict[str, Any]:
    return transport_schema(json.loads(schema_path_for(stage, role).read_text(encoding="utf-8")))


# --------------------------------------------------------------------------- preflight

def _run_quiet(argv: list[str], timeout: int, cwd: str | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env, stdin=subprocess.DEVNULL, check=False)


def preflight(executor: str, model: str | None, probe: bool) -> dict[str, Any]:
    binary = shutil.which(executor)
    if binary is None:
        return {"status": "BLOCKED", "executor": executor, "reason": "EXECUTOR_UNAVAILABLE",
                "next_step": f"Install the {executor} CLI and put it on PATH, or select the other executor for these stages in policies/EXECUTORS.md.",
                "next_command": None}
    result: dict[str, Any] = {"executor": executor, "path": binary}
    try:
        version = _run_quiet([binary, "--version"], 30)
        help_argv = [binary, "--help"] if executor == "claude" else [binary, "exec", "--help"]
        help_text = _run_quiet(help_argv, 30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {**result, "status": "BLOCKED", "reason": "EXECUTOR_UNAVAILABLE", "detail": str(exc),
                "next_step": f"Make `{executor} --version` run quickly and successfully, then rerun preflight.", "next_command": None}
    result["version"] = (version.stdout or version.stderr).strip().splitlines()[0] if (version.stdout or version.stderr).strip() else None
    missing = [flag for flag in REQUIRED_HELP_FLAGS[executor] if flag not in help_text.stdout + help_text.stderr]
    result["missing_flags"] = missing
    if version.returncode != 0 or missing:
        return {**result, "status": "BLOCKED", "reason": "EXECUTOR_CLI_UNSUPPORTED",
                "next_step": f"Upgrade the {executor} CLI: it must support {', '.join(REQUIRED_HELP_FLAGS[executor])}.", "next_command": None}
    if model is not None and not MODEL_PATTERN.match(model):
        return {**result, "status": "BLOCKED", "reason": "MODEL_INVALID",
                "next_step": "Use a plain model name (letters, digits, . _ : / - [ ]) in policies/EXECUTORS.md.", "next_command": None}
    result["model"] = model
    result["model_check"] = "NOT_PROBED"
    if probe:
        probe_result = _probe(executor, binary, model)
        result.update(probe_result)
        if probe_result["model_check"] != "ACCEPTED":
            return {**result, "status": "BLOCKED", "reason": "MODEL_REJECTED",
                    "next_step": f"Fix the {executor} login or pick a model this CLI accepts, then rerun preflight with --probe.",
                    "next_command": _command(_python(), Path(__file__), "preflight", "--executor", executor, *(["--model", model] if model else []), "--probe")}
    return {**result, "status": "READY", "next_step": "The executor is ready; dispatch with `executor_launch.py run`." + ("" if probe else " Add --probe to confirm login and model with one minimal call."),
            "next_command": None}


def _probe(executor: str, binary: str, model: str | None) -> dict[str, Any]:
    """One minimal real call that proves login and model acceptance; never used by the dispatch path."""
    with tempfile.TemporaryDirectory(prefix="sdd-executor-probe-") as scratch:
        if executor == "claude":
            argv = [binary, "-p", "--output-format", "json", "--no-session-persistence", "--max-turns", "1", "--tools", ""]
            argv += ["--model", model] if model else []
            argv.append("Reply with the single word OK.")
        else:
            argv = [binary, "exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only", "--color", "never", "-C", scratch]
            argv += ["--model", model] if model else []
            argv += ["-o", str(Path(scratch) / "probe.txt"), "Reply with the single word OK."]
        try:
            completed = _run_quiet(argv, PROBE_TIMEOUT_SECONDS, cwd=scratch, env=worker_environment(executor))
        except subprocess.TimeoutExpired:
            return {"model_check": "TIMEOUT", "probe_exit_code": TIMEOUT_EXIT_CODE}
        accepted = completed.returncode == 0
        if accepted and executor == "claude":
            try:
                accepted = not json.loads(completed.stdout).get("is_error", False)
            except (json.JSONDecodeError, AttributeError):
                accepted = False
        return {"model_check": "ACCEPTED" if accepted else "REJECTED", "probe_exit_code": completed.returncode,
                "probe_stderr_tail": completed.stderr[-TAIL_CHARACTERS:]}


# --------------------------------------------------------------------------- build

def _side_paths(final: Path) -> tuple[Path, Path]:
    return final.with_name(final.name + ".transport-schema.json"), final.with_name(final.name + ".last-message.tmp")


def build_argv(*, stage: str, settings: dict[str, Any], schema: dict[str, Any], repository: Path, final: Path, add_dirs: list[str], binary: str,
               read_dirs: list[str] | None = None) -> list[str]:
    schema_file, last_message = _side_paths(final)
    if settings["executor"] == "claude":
        add_dirs = [*add_dirs, *(read_dirs or [])]
        argv = [binary, "-p", "--output-format", "json", "--json-schema", json.dumps(schema, separators=(",", ":"), ensure_ascii=False),
                "--no-session-persistence", "--tools", settings["tools"]]
        if settings.get("max_turns"):
            argv += ["--max-turns", str(settings["max_turns"])]
        if settings.get("model"):
            argv += ["--model", settings["model"]]
        if settings.get("permission_mode"):
            argv += ["--permission-mode", settings["permission_mode"]]
        for directory in add_dirs:
            argv += ["--add-dir", directory]
        return argv
    argv = [binary, "exec", "--ephemeral", "--color", "never", "--cd", str(repository), "--sandbox", settings["sandbox"],
            "--output-schema", str(schema_file), "--output-last-message", str(last_message)]
    if settings.get("model"):
        argv += ["--model", settings["model"]]
    if is_writing_worker(settings):
        argv += ["--config", CODEX_NO_EXTRA_WRITABLE_ROOTS]
    for directory in add_dirs:
        argv += ["--add-dir", directory]
    argv.append("-")
    return argv


def _read_journal(journal: Path) -> dict[str, Any] | None:
    try:
        return json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _repository(args: argparse.Namespace, journal_value: dict[str, Any] | None) -> Path:
    recorded = (journal_value or {}).get("workspace", {}).get("path") if isinstance(journal_value, dict) else None
    chosen = args.repo or recorded
    if not chosen:
        raise LaunchError("REPOSITORY_INVALID", "No repository: the journal has no workspace.path and --repo was not given.",
                          "Pass --repo <repository> or prepare the journal first.")
    path = Path(chosen)
    if not path.is_absolute() or not path.is_dir():
        raise LaunchError("REPOSITORY_INVALID", f"Repository {chosen} is not an existing absolute directory.", "Pass the absolute repository path with --repo.")
    if args.repo and recorded and Path(args.repo).resolve() != Path(recorded).resolve():
        raise LaunchError("JOURNAL_MISMATCH", "--repo differs from the journal workspace.path.", "Omit --repo; the journal workspace is authoritative.")
    return path


def _plan(args: argparse.Namespace) -> dict[str, Any]:
    policy = load_policy(Path(args.policy) if args.policy else None)
    settings = stage_settings(policy, args.stage, executor=args.executor, model=args.model, timeout=args.timeout)
    schema = stage_transport_schema(args.stage, args.role)
    journal = Path(args.journal)
    journal_value = _read_journal(journal)
    repository = _repository(args, journal_value)
    final = Path(args.final)
    if not final.is_absolute():
        raise LaunchError("JOURNAL_MISMATCH", "--final must be absolute.", "Pass the absolute final-message path recorded in the journal.")
    prompt = Path(args.prompt_file)
    if not prompt.is_file():
        raise LaunchError("PROMPT_MISSING", f"Prompt file {prompt} does not exist.", "Write the prompt file, record its SHA-256 in the prepared journal, then retry.")
    check_extra_dirs(args.stage, settings, repository, list(args.add_dir), list(args.read_dir))
    check_controller_isolation(args.stage, settings, repository)
    binary = shutil.which(settings["executor"]) or settings["executor"]
    argv = build_argv(stage=args.stage, settings=settings, schema=schema, repository=repository, final=final, add_dirs=list(args.add_dir), binary=binary,
                      read_dirs=list(args.read_dir))
    return {"policy": policy, "settings": settings, "schema": schema, "journal": journal, "journal_value": journal_value,
            "repository": repository, "final": final, "prompt": prompt, "argv": argv}


def _archive_command(journal: Path) -> str:
    return _command(_python(), ACTION_JOURNAL_SCRIPT, "--journal", journal, "--json", "archive-interrupted",
                    "--history-dir", journal.parent / "action-journal-history")


def _classify_command(journal: Path) -> str:
    return _command(_python(), ACTION_JOURNAL_SCRIPT, "--journal", journal, "--json", "classify-invalid", "--invalid-field", "<field>")


def _validate_command(stage: str, final: Path) -> str:
    return _command(_python(), VALIDATE_PROTOCOL_SCRIPT, "--action", stage, "--result", final, "--context", "<verifier-context.json>", "--json")


# --------------------------------------------------------------------------- journal

def _journal_cli(journal: Path, *arguments: str) -> tuple[int, dict[str, Any]]:
    completed = subprocess.run([_python(), str(ACTION_JOURNAL_SCRIPT), "--journal", str(journal), "--json", *arguments],
                               capture_output=True, text=True, check=False, timeout=120)
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        payload = {"status": "JOURNAL_OUTPUT_INVALID", "message": (completed.stdout + completed.stderr)[-TAIL_CHARACTERS:]}
    return completed.returncode, payload if isinstance(payload, dict) else {"value": payload}


def _journal_supports(flag: str) -> bool:
    completed = subprocess.run([_python(), str(ACTION_JOURNAL_SCRIPT), "--help"], capture_output=True, text=True, check=False, timeout=60)
    return flag in completed.stdout


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check_dispatch(plan: dict[str, Any], stage: str) -> str:
    journal: Path = plan["journal"]
    code, recovery = _journal_cli(journal, "recover")
    if code != 0 or recovery.get("decision") != "DISPATCH_ALLOWED":
        raise LaunchError("DISPATCH_NOT_ALLOWED", f"Journal recovery is {recovery.get('decision') or recovery.get('status')}: {recovery.get('reason') or recovery.get('message')}",
                          "Resolve the journal per policies/ACTION_RECOVERY.md; never dispatch without DISPATCH_ALLOWED.",
                          _command(_python(), ACTION_JOURNAL_SCRIPT, "--journal", journal, "--json", "recover"))
    code, value = _journal_cli(journal, "inspect")
    action = value.get("action") if code == 0 else None
    prepare_hint = "Prepare the action first with action_journal.py prepare (status PREPARED, this stage, this final path and prompt SHA-256)."
    if not isinstance(action, dict) or action.get("status") != "PREPARED":
        raise LaunchError("JOURNAL_NOT_PREPARED", f"Journal action status is {action.get('status') if isinstance(action, dict) else 'unreadable'}, not PREPARED.", prepare_hint)
    if action.get("stage") != stage:
        raise LaunchError("JOURNAL_MISMATCH", f"Journal stage {action.get('stage')} differs from --stage {stage}.", prepare_hint)
    if not action.get("final_message_path") or Path(action["final_message_path"]).resolve() != plan["final"].resolve():
        raise LaunchError("JOURNAL_MISMATCH", "--final differs from the journal final_message_path.", "Pass --final exactly as recorded in the prepared journal.")
    recorded_executor = action.get("executor")
    if recorded_executor and str(recorded_executor).lower() != plan["settings"]["executor"]:
        raise LaunchError("JOURNAL_MISMATCH", f"Journal executor {recorded_executor} differs from the selected executor {plan['settings']['executor']}.",
                          "Prepare the journal with the executor selected by policies/EXECUTORS.md, or pass the matching --executor.")
    prompt_hash = _file_sha256(plan["prompt"])
    if action.get("prompt_hash") != prompt_hash:
        raise LaunchError("PROMPT_HASH_MISMATCH", "Prompt file SHA-256 differs from the journal prompt_hash.",
                          "Dispatch the exact prompt that was prepared, or prepare a new action for the changed prompt.")
    if plan["final"].exists():
        raise LaunchError("ARTIFACT_PENDING", "The final-message file already exists.", "Classify the existing artifact before any dispatch (ACTION_RECOVERY.md).",
                          _command(_python(), ACTION_JOURNAL_SCRIPT, "--journal", journal, "--json", "recover"))
    return prompt_hash


# --------------------------------------------------------------------------- run

def worker_environment(executor: str) -> dict[str, str]:
    """Allow-listed environment for the worker: base variables plus the executor's own auth and config."""
    prefixes = (*WORKER_ENV_PREFIXES, *EXECUTOR_ENV_PREFIXES[executor])
    return {name: value for name, value in process_environment.items() if name in WORKER_ENV_NAMES or name.startswith(prefixes)}


class LauncherInterrupted(BaseException):
    """SIGTERM/SIGHUP received while a dispatch is in flight; converted so the finally blocks run."""

    def __init__(self, signum: int) -> None:
        super().__init__(f"launcher received signal {signum}")
        self.signum = signum


class _SignalGuard:
    """Turn the first SIGTERM/SIGHUP into LauncherInterrupted; later ones are ignored.

    ``hold()`` blocks the signals around journal writes so a signal can never split them;
    a blocked signal is delivered (and raised) on ``release()``.
    """

    SIGNALS = (signal.SIGTERM, signal.SIGHUP)

    def __init__(self) -> None:
        self.raised = False
        self.previous: dict[int, Any] = {}

    def _handler(self, signum: int, _frame: Any) -> None:
        if not self.raised:
            self.raised = True
            raise LauncherInterrupted(signum)

    def __enter__(self) -> "_SignalGuard":
        try:
            for sig in self.SIGNALS:
                self.previous[sig] = signal.signal(sig, self._handler)
        except ValueError:  # not the main thread: keep the default behavior
            self.previous.clear()
        return self

    def hold(self) -> None:
        if self.previous:
            signal.pthread_sigmask(signal.SIG_BLOCK, self.SIGNALS)

    def release(self) -> None:
        if self.previous:
            signal.pthread_sigmask(signal.SIG_UNBLOCK, self.SIGNALS)

    def __exit__(self, *_exc: Any) -> None:
        self.raised = True  # a signal still pending after the journal is recorded is dropped
        self.release()
        for sig, handler in self.previous.items():
            signal.signal(sig, handler)


def _terminate_group(process: subprocess.Popen[bytes]) -> None:
    """SIGTERM the whole group, wait the grace period, then SIGKILL whatever is left of the group."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.wait(timeout=TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.wait(timeout=TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass


def _spawn(argv: list[str], *, cwd: Path, prompt: Path, timeout: int, env: dict[str, str]) -> tuple[int, bytes, bytes, bool]:
    """Run in the foreground in its own process group; kill the whole group on timeout."""
    with prompt.open("rb") as stdin, tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(argv, cwd=str(cwd), stdin=stdin, stdout=out, stderr=err, start_new_session=True, env=env)
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_group(process)
        except BaseException:
            _terminate_group(process)
            raise
        else:
            # A finished leader may leave background children holding resources; stop them too.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        out.seek(0)
        err.seek(0)
        return (TIMEOUT_EXIT_CODE if timed_out else process.returncode), out.read(), err.read(), timed_out


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = ""
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def extract_result(executor: str, stdout: bytes, last_message: Path) -> tuple[bytes | None, bool, dict[str, Any]]:
    """Return (final bytes, is_json_object, diagnostics). Raw non-JSON output is kept as evidence."""
    diagnostics: dict[str, Any] = {}
    if executor == "claude":
        text = stdout.decode("utf-8", errors="replace")
        try:
            envelope = json.loads(text)
        except json.JSONDecodeError:
            return (stdout if text.strip() else None), False, {"envelope": "NOT_JSON"}
        if not isinstance(envelope, dict):
            return stdout, False, {"envelope": "NOT_OBJECT"}
        diagnostics = {"envelope_subtype": envelope.get("subtype"), "envelope_is_error": envelope.get("is_error")}
        structured = envelope.get("structured_output")
        if isinstance(structured, dict):
            return (json.dumps(structured, indent=2, ensure_ascii=False) + "\n").encode("utf-8"), True, diagnostics
        result = envelope.get("result")
        if not isinstance(result, str) or not result.strip():
            return None, False, diagnostics
        try:
            is_object = isinstance(json.loads(result), dict)
        except json.JSONDecodeError:
            is_object = False
        return result.encode("utf-8"), is_object, diagnostics
    if not last_message.is_file():
        return None, False, {"last_message": "MISSING"}
    data = last_message.read_bytes()
    if not data.strip():
        return None, False, {"last_message": "EMPTY"}
    try:
        return data, isinstance(json.loads(data.decode("utf-8")), dict), diagnostics
    except (json.JSONDecodeError, UnicodeDecodeError):
        return data, False, diagnostics


def run(args: argparse.Namespace) -> dict[str, Any]:
    plan = _plan(args)
    journal: Path = plan["journal"]
    settings = plan["settings"]
    if shutil.which(settings["executor"]) is None:
        raise LaunchError("EXECUTOR_UNAVAILABLE", f"{settings['executor']} is not on PATH.", "Install the CLI or select another executor in policies/EXECUTORS.md; the journal stays PREPARED.",
                          _command(_python(), Path(__file__), "preflight", "--executor", settings["executor"]))
    prompt_hash = _check_dispatch(plan, args.stage)
    with _SignalGuard() as guard:
        return _dispatch(args, plan, prompt_hash, guard)


def _dispatch(args: argparse.Namespace, plan: dict[str, Any], prompt_hash: str, guard: _SignalGuard) -> dict[str, Any]:
    journal: Path = plan["journal"]
    settings = plan["settings"]
    schema_file, last_message = _side_paths(plan["final"])
    started = ["record-process", "--started"]
    if _journal_supports("--prompt-sha256"):
        started += ["--prompt-sha256", prompt_hash]
    guard.hold()
    code, payload = _journal_cli(journal, *started)
    if code != 0:
        guard.release()
        raise LaunchError("JOURNAL_REFUSED", f"record-process --started refused: {payload.get('status')}: {payload.get('message')}",
                          "Nothing was dispatched. Resolve the journal error, then retry run.",
                          _command(_python(), ACTION_JOURNAL_SCRIPT, "--journal", journal, "--json", "recover"))
    exit_code = LAUNCHER_ERROR_EXIT_CODE
    stdout = stderr = b""
    timed_out = False
    launcher_error: str | None = None
    interrupted: int | None = None
    began = time.monotonic()
    try:
        guard.release()  # a signal that arrived while recording --started is raised here, inside the try
        if settings["executor"] == "codex":
            _atomic_write(schema_file, json.dumps(plan["schema"], indent=2, ensure_ascii=False).encode("utf-8"))
            if last_message.exists():
                last_message.unlink()
        exit_code, stdout, stderr, timed_out = _spawn(plan["argv"], cwd=plan["repository"], prompt=plan["prompt"], timeout=settings["timeout_seconds"],
                                                      env=worker_environment(settings["executor"]))
    except LauncherInterrupted as exc:  # _spawn already killed the executor process group
        exit_code, interrupted, launcher_error = LAUNCHER_ERROR_EXIT_CODE, exc.signum, str(exc)
    except FileNotFoundError as exc:
        exit_code, launcher_error = NOT_FOUND_EXIT_CODE, str(exc)
    except Exception as exc:  # recorded below; the journal must never stay DISPATCHED
        launcher_error = f"{type(exc).__name__}: {exc}"
    finally:
        guard.hold()
        finish_code, finish_payload = _journal_cli(journal, "record-process", "--finished", "--exit-code", str(exit_code))
    duration = round(time.monotonic() - began, 3)
    base = {"executor": settings["executor"], "stage": args.stage, "exit_code": exit_code, "final": str(plan["final"]),
            "duration_seconds": duration, "stderr_tail": stderr.decode("utf-8", errors="replace")[-TAIL_CHARACTERS:]}
    try:
        if finish_code != 0:
            return {**base, "status": "JOURNAL_FINISH_FAILED", "next_step": f"record-process --finished failed ({finish_payload.get('status')}). Inspect the journal; do not redispatch.",
                    "next_command": _command(_python(), ACTION_JOURNAL_SCRIPT, "--journal", journal, "--json", "inspect")}
        if interrupted is not None:
            return {**base, "status": "LAUNCHER_INTERRUPTED", "signal": interrupted,
                    "next_step": "The launcher was stopped by a signal; the executor process group was killed and exit 125 recorded. Archive the interrupted action, then `sdd.py next` prepares the retry.",
                    "next_command": _archive_command(journal)}
        if launcher_error is not None:
            return {**base, "status": "LAUNCHER_ERROR", "message": launcher_error, "next_step": "The process result is recorded. Archive the interrupted action, fix the launcher error, then prepare a retry with parent_action_id.",
                    "next_command": _archive_command(journal)}
        if timed_out:
            return {**base, "status": "EXECUTOR_TIMEOUT", "next_step": "Archive the interrupted action, then prepare one retry with a new action_id, parent_action_id and reduced context (LOOP_POLICY.md); a repeated timeout blocks.",
                    "next_command": _archive_command(journal)}
        if exit_code != 0:
            return {**base, "status": "EXECUTOR_FAILED", "next_step": "The executor process failed. Archive the interrupted action; check stderr_tail and preflight before a retry.",
                    "next_command": _archive_command(journal)}
        data, is_object, diagnostics = extract_result(settings["executor"], stdout, last_message)
        base.update(diagnostics)
        if data is None:
            return {**base, "status": "EXECUTOR_NO_OUTPUT", "next_step": "The executor exited 0 without a final message. Archive the interrupted action before any retry.",
                    "next_command": _archive_command(journal)}
        _atomic_write(plan["final"], data)
        code, artifact = _journal_cli(journal, "record-artifact")
        if code != 0:
            return {**base, "status": "JOURNAL_FINISH_FAILED", "next_step": f"record-artifact failed ({artifact.get('status')}). Inspect the journal before any other action.",
                    "next_command": _command(_python(), ACTION_JOURNAL_SCRIPT, "--journal", journal, "--json", "inspect")}
        if not is_object:
            return {**base, "status": "OUTPUT_INVALID", "next_step": "The final message is not a JSON object (CONTRACT_INVALID). Classify it invalid, then apply the corrective-retry rule.",
                    "next_command": _classify_command(journal)}
        return {**base, "status": "ARTIFACT_READY", "next_step": "Validate the final message against its contract before any STATE change.",
                "next_command": _validate_command(args.stage, plan["final"])}
    finally:
        # The transport schema is disposable; an uncopied Codex last message stays as diagnostic evidence.
        for side in (schema_file, last_message) if plan["final"].exists() else (schema_file,):
            try:
                side.unlink()
            except OSError:
                pass


# --------------------------------------------------------------------------- CLI

def _add_launch_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--stage", required=True, choices=STAGES)
    parser.add_argument("--executor", choices=EXECUTORS, help="Override the executor selected by policies/EXECUTORS.md.")
    parser.add_argument("--role", help="Read-only/audit sub-agent role whose brief selects the result schema.")
    parser.add_argument("--prompt-file", required=True, help="Prompt file sent on stdin; its SHA-256 must equal the journal prompt_hash.")
    parser.add_argument("--journal", required=True, help="ACTION_JOURNAL.json of the prepared action.")
    parser.add_argument("--final", required=True, help="Absolute final-message path recorded in the journal.")
    parser.add_argument("--model", help="Override the stage model from policies/EXECUTORS.md.")
    parser.add_argument("--timeout", type=int, help="Override timeout_seconds (1-7200).")
    parser.add_argument("--repo", help="Repository used as cwd; defaults to the journal workspace.path.")
    parser.add_argument("--add-dir", action="append", default=[],
                        help="Extra directory passed as the CLI's --add-dir (writable for Codex and for Claude with write tools; a writing worker gets it only inside the repository); repeatable.")
    parser.add_argument("--read-dir", action="append", default=[],
                        help="Directory the worker only reads (e.g. the Obsidian controller container): --add-dir for a read-only Claude worker, omitted for Codex; repeatable.")
    parser.add_argument("--policy", help="EXECUTORS.md path; defaults to policies/EXECUTORS.md.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    commands = parser.add_subparsers(dest="command", required=True)
    schema = commands.add_parser("schema", help="Print the transport JSON schema for a stage.")
    schema.add_argument("--stage", required=True, choices=STAGES)
    schema.add_argument("--role", help="Sub-agent role whose brief selects the result schema.")
    check = commands.add_parser("preflight", help="Check CLI presence, version and flags.")
    check.add_argument("--executor", required=True, choices=EXECUTORS)
    check.add_argument("--model", help="Model name to check.")
    check.add_argument("--probe", action="store_true", help="Make one minimal real call to confirm login and model.")
    _add_launch_arguments(commands.add_parser("build", help="Print the exact argv run would execute."))
    _add_launch_arguments(commands.add_parser("run", help="Run one journaled dispatch in the foreground."))
    args = parser.parse_args(argv)
    try:
        if args.command == "schema":
            print(json.dumps(stage_transport_schema(args.stage, args.role), indent=2, ensure_ascii=False))
            return 0
        if args.command == "preflight":
            result = preflight(args.executor, args.model, args.probe)
        elif args.command == "build":
            plan = _plan(args)
            schema_file, _ = _side_paths(plan["final"])
            result = {"status": "READY", "executor": plan["settings"]["executor"], "argv": plan["argv"], "cwd": str(plan["repository"]),
                      "stdin": str(plan["prompt"]), "timeout_seconds": plan["settings"]["timeout_seconds"], "policy_source": plan["policy"]["source"],
                      "transport_schema_file": str(schema_file) if plan["settings"]["executor"] == "codex" else None,
                      "next_step": "Dispatch only through `executor_launch.py run` with the same arguments.", "next_command": None}
        else:
            result = run(args)
    except LaunchError as exc:
        result = exc.payload()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("status") in {"READY", "ARTIFACT_READY"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
