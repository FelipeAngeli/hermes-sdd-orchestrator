#!/usr/bin/env python3
"""OpenCode worker: hermetic configuration, seatbelt wrapper, argv, version pin and probe.

OpenCode never runs bare. Every call is ``codex sandbox`` (macOS seatbelt, no model call)
around ``opencode run --format json --pure --auto``, with:

* a hermetic environment: ``OPENCODE_PURE`` and the disables in ``HERMETIC_ENV`` (no project
  ``opencode.json``/``.opencode/`` plugins, no external or Claude Code skills, no default
  plugins, no autoupdate/share/LSP download), plus an ``OPENCODE_PERMISSION`` that denies
  ``webfetch``, ``websearch``, ``codesearch``, ``task`` and every ``external_directory``
  except the controller's ``--read-dir`` paths, which are never editable, and denies the
  ``read``/``bash`` tools on OpenCode's credential store (``auth.json``, ``opencode.db*``);
* per-dispatch ``XDG_CONFIG_HOME``/``XDG_CACHE_HOME``/``XDG_STATE_HOME``/``TMPDIR`` inside
  ``<data>/sdd-runtime/<dispatch>/``, where ``<data>`` is OpenCode's own data directory
  (``$XDG_DATA_HOME/opencode``, default ``~/.local/share/opencode``). That directory holds
  the logged-in account and is used in place: nothing is copied, exported or created there
  besides OpenCode's own state and the dispatch directory, which is deleted afterwards;
* a seatbelt whose only writable roots are the workspace (the repository for a writing
  stage, the dispatch directory otherwise) and ``<data>``; ``/tmp``, ``$TMPDIR`` and ``.git``
  stay read-only. Codex's read-only mode cannot add a writable root, so a read-only stage
  runs workspace-write with the dispatch directory as workspace and ``--dir <repository>``.
  The seatbelt network switch is all-or-nothing and the provider needs it, so it is on.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import opencode_transport
from redaction import redact

PINNED_VERSION = "1.18.34"
REQUIRED_HELP_FLAGS = ("--format", "--model", "--pure", "--dir", "--auto", "--file")
SANDBOX_BINARY = "codex"
SANDBOX_HELP_FLAGS = ("--config",)
HERMETIC_ENV = {name: "1" for name in (
    "OPENCODE_PURE", "OPENCODE_DISABLE_PROJECT_CONFIG", "OPENCODE_DISABLE_EXTERNAL_SKILLS", "OPENCODE_DISABLE_CLAUDE_CODE",
    "OPENCODE_DISABLE_CLAUDE_CODE_PROMPT", "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS", "OPENCODE_DISABLE_DEFAULT_PLUGINS",
    "OPENCODE_DISABLE_AUTOUPDATE", "OPENCODE_DISABLE_SHARE", "OPENCODE_DISABLE_LSP_DOWNLOAD")}
#: Inherited OPENCODE_* names that are never passed: credentials and anything that could load another config.
DROPPED_ENV = frozenset({"OPENCODE_API_KEY", "OPENCODE_CONSOLE_TOKEN", "OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR", "OPENCODE_CONFIG_CONTENT",
                         "OPENCODE_PERMISSION", "OPENCODE_SERVER_PASSWORD", "OPENCODE_SERVER_USERNAME"})
_SECRET_SUFFIXES = ("_KEY", "_TOKEN", "_SECRET", "_PASSWORD")
DENIED_TOOLS = ("webfetch", "websearch", "codesearch", "task")
#: OpenCode's credential store (``<data>/auth.json``, ``<data>/opencode.db*``). Verified on 1.18.34:
#: ``read`` rules match the path *relative to the project*, so an absolute pattern never matches and
#: these globs end in the data directory's own name; ``external_directory: deny`` already refuses the
#: ``read`` tool outside the project, and ``bash`` rules match the command text. The seatbelt still
#: lets a process read the directory, so the stored stream drops tool output too.
CREDENTIAL_READ_DENY = ("*opencode/auth.json", "*opencode/opencode.db*")
CREDENTIAL_BASH_DENY = ("*auth.json*", "*opencode.db*")
RUNTIME_FOLDER = "sdd-runtime"
PROBE_PROMPT = "Reply with the single word OK."
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def data_dir(env: dict[str, str]) -> Path:
    base = env.get("XDG_DATA_HOME") or str(Path(env.get("HOME") or Path.home()) / ".local" / "share")
    return Path(base) / "opencode"


def dispatch_dir(env: dict[str, str], final: Path) -> Path:
    """Deterministic per-dispatch directory inside OpenCode's data directory."""
    digest = hashlib.sha256(str(final).encode("utf-8")).hexdigest()[:10]
    return data_dir(env) / RUNTIME_FOLDER / f"{_SAFE_NAME.sub('-', final.name)[:60]}-{digest}"


def filter_env(env: dict[str, str]) -> dict[str, str]:
    """Drop credentials and config redirections from an already allow-listed worker environment."""
    return {name: value for name, value in env.items()
            if not (name.startswith("OPENCODE_") and (name in DROPPED_ENV or name.endswith(_SECRET_SUFFIXES)))}


def _variants(directory: str) -> list[str]:
    resolved = str(Path(directory).resolve())
    return [directory] if resolved == directory else [directory, resolved]


def permission(read_dirs: list[str], writing: bool) -> dict[str, Any]:
    """OPENCODE_PERMISSION: explicit denies (never ``ask``); later keys win, so allows follow the ``*`` deny/allow."""
    external: dict[str, str] = {"*": "deny"}
    edit: dict[str, str] = {"*": "allow" if writing else "deny"}
    if writing:
        edit.update({"../*": "deny", ".git": "deny", ".git/*": "deny"})
    for directory in read_dirs:
        for variant in _variants(directory):
            external[variant] = external[f"{variant}/*"] = "allow"
            edit[variant] = edit[f"{variant}/*"] = "deny"
    return {**{tool: "deny" for tool in DENIED_TOOLS}, "external_directory": external, "edit": edit,
            "read": {"*": "allow", **{pattern: "deny" for pattern in CREDENTIAL_READ_DENY}},
            "bash": {"*": "allow", **{pattern: "deny" for pattern in CREDENTIAL_BASH_DENY}}}


def config_sha256(permission_value: dict[str, Any]) -> str:
    canonical = json.dumps({"env": HERMETIC_ENV, "permission": permission_value}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def hermetic_env(base: dict[str, str], directory: Path, permission_value: dict[str, Any]) -> dict[str, str]:
    env = filter_env(base)
    env.update(HERMETIC_ENV)
    env.update({"XDG_CONFIG_HOME": str(directory / "config"), "XDG_CACHE_HOME": str(directory / "cache"),
                "XDG_STATE_HOME": str(directory / "state"), "TMPDIR": str(directory / "tmp"),
                "OPENCODE_PERMISSION": json.dumps(permission_value, separators=(",", ":"))})
    return env


def sandbox_prefix(sandbox_binary: str, data: Path) -> list[str]:
    roots = json.dumps([str(data)], ensure_ascii=False)
    return [sandbox_binary, "sandbox", "-c", 'sandbox_mode="workspace-write"', "-c", "sandbox_workspace_write.network_access=true",
            "-c", "sandbox_workspace_write.exclude_slash_tmp=true", "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=true",
            "-c", f"sandbox_workspace_write.writable_roots={roots}", "--"]


def build(*, binary: str, settings: dict[str, Any], repository: Path, schema_file: Path, read_dirs: list[str], writing: bool,
          base_env: dict[str, str], final: Path) -> dict[str, Any]:
    """argv, cwd, env and the launcher record for one dispatch; nothing is written here."""
    directory = dispatch_dir(base_env, final)
    value = permission(read_dirs, writing)
    sandbox = shutil.which(SANDBOX_BINARY, path=base_env.get("PATH")) or SANDBOX_BINARY
    argv = [*sandbox_prefix(sandbox, data_dir(base_env)), binary, "run", "--format", "json", "--pure", "--auto",
            "--dir", str(repository), "-m", settings["model"], "-f", str(schema_file)]
    return {"argv": argv, "cwd": repository if writing else directory / "work", "env": hermetic_env(base_env, directory, value),
            "dispatch_dir": directory, "record": {"executor_version": PINNED_VERSION, "model": settings["model"],
                                                  "opencode_config_sha256": config_sha256(value)}}


def prepare(directory: Path) -> None:
    for name in ("config", "cache", "state", "tmp", "work"):
        (directory / name).mkdir(parents=True, exist_ok=True)


def cleanup(directory: Path | None) -> None:
    if directory is not None and directory.parent.name == RUNTIME_FOLDER:
        shutil.rmtree(directory, ignore_errors=True)


def model_error(model: str | None) -> str | None:
    if not model or "/" not in model:
        return "opencode needs an explicit provider/model (for example opencode-go/deepseek-v4.1-flash) in policies/EXECUTORS.md."
    return None


def preflight_extra(binary: str, result: dict[str, Any], env: dict[str, str], run_quiet: Any) -> dict[str, Any] | None:
    """Version pin, seatbelt wrapper and data directory; a BLOCKED payload or None."""
    version = (result.get("version") or "").strip()
    if version != PINNED_VERSION:
        return {**result, "status": "BLOCKED", "reason": "EXECUTOR_CLI_UNSUPPORTED", "pinned_version": PINNED_VERSION,
                "next_step": f"Install opencode {PINNED_VERSION} exactly (found {version or 'unknown'}). The launcher pins that version "
                             "because its hermetic variables and permission rules were verified only on it; select another executor "
                             "for these stages in policies/EXECUTORS.md meanwhile.", "next_command": None}
    sandbox = shutil.which(SANDBOX_BINARY, path=env.get("PATH"))
    help_text = ""
    if sandbox:
        try:
            completed = run_quiet([sandbox, "sandbox", "--help"], 30, env=env)
            help_text = completed.stdout + completed.stderr
        except (OSError, subprocess.TimeoutExpired):
            help_text = ""
    if not sandbox or any(flag not in help_text for flag in SANDBOX_HELP_FLAGS):
        return {**result, "status": "BLOCKED", "reason": "EXECUTOR_UNAVAILABLE",
                "next_step": "opencode always runs inside `codex sandbox`: install the codex CLI (its `sandbox` subcommand with --config) "
                             "and put it on PATH.", "next_command": None}
    if not data_dir(env).is_dir():
        return {**result, "status": "BLOCKED", "reason": "EXECUTOR_UNAVAILABLE",
                "next_step": f"OpenCode's data directory {data_dir(env)} does not exist: log in once with the opencode CLI yourself.",
                "next_command": None}
    return None


def model_probe(*, binary: str, model: str | None, probe_timeout: int, tail_characters: int, worker_env: dict[str, str],
                run_quiet: Any) -> dict[str, Any]:
    """One minimal sandboxed call (read-only layout, no repository) that proves login and model."""
    data = data_dir(worker_env)
    (data / RUNTIME_FOLDER).mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="probe-", dir=data / RUNTIME_FOLDER))
    try:
        prepare(directory)
        env = hermetic_env(worker_env, directory, permission([], writing=False))
        sandbox = shutil.which(SANDBOX_BINARY, path=worker_env.get("PATH")) or SANDBOX_BINARY
        argv = [*sandbox_prefix(sandbox, data), binary, "run", "--format", "json", "--pure", "--dir", str(directory / "work"),
                *(["-m", model] if model else []), PROBE_PROMPT]
        try:
            completed = run_quiet(argv, probe_timeout, cwd=str(directory / "work"), env=env)
        except subprocess.TimeoutExpired:
            return {"model_check": "TIMEOUT", "probe_exit_code": 124}
        outcome, _, _ = opencode_transport.classify(completed.stdout.encode("utf-8"), completed.returncode)
        return {"model_check": "ACCEPTED" if outcome in {"OK", "INVALID_OUTPUT"} else "REJECTED", "probe_exit_code": completed.returncode,
                "probe_stderr_tail": redact(completed.stderr[-tail_characters:])}
    finally:
        cleanup(directory)
