#!/usr/bin/env python3
"""Per-executor dispatch table for the executor launcher.

Every executor-specific step the launcher takes is looked up here by executor name, never
derived from an ``if claude ... else codex`` fallback: the help probe, the command line,
the side files written before the process starts, whether a stage writes, and the extra
launch record. An executor missing from ``EXECUTORS`` is ``POLICY_INVALID`` in the
launcher. The Claude and Codex command lines are pinned byte for byte by
``tests/test_executor_golden.py``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

EXECUTORS = ("claude", "codex")
#: Every Claude call (dispatch and probe) loads only the user's own settings and no MCP server or skill.
#: ``-p`` runs with cwd = the repository and skips the workspace-trust dialog, so without these flags
#: the CLI would load ``.claude/settings.json``/``settings.local.json`` (hooks run as the user, outside
#: every tool restriction), ``.mcp.json`` servers and ``.claude/skills`` — all files a writing worker
#: can create. ``--bare`` is not used: it also stops OAuth/keychain auth (``ANTHROPIC_API_KEY`` only).
CLAUDE_ISOLATION_ARGS = ("--setting-sources", "user", "--strict-mcp-config", "--disable-slash-commands")
REQUIRED_HELP_FLAGS = {
    "claude": ("--print", "--output-format", "--json-schema", "--no-session-persistence", "--model", "--tools", "--add-dir",
               "--setting-sources", "--strict-mcp-config", "--disable-slash-commands"),
    "codex": ("--output-schema", "--output-last-message", "--ephemeral", "--model", "--sandbox", "--cd", "--add-dir", "--config"),
}
HELP_ARGS = {"claude": ("--help",), "codex": ("exec", "--help")}
#: Codex treats ``--add-dir`` as an extra *writable* root; a writing Codex worker gets none at all.
CODEX_NO_EXTRA_WRITABLE_ROOTS = "sandbox_workspace_write.writable_roots=[]"
CODEX_NETWORK_ACCESS_CONFIG = "sandbox_workspace_write.network_access=true"
#: Claude tools that cannot change a file; any other tool (or ``default``) makes the worker a writer.
READ_ONLY_CLAUDE_TOOLS = frozenset({"Read", "Grep", "Glob", "LS", "NotebookRead"})
#: Executors whose ``sandbox`` field (``read-only``/``workspace-write``) decides whether they write.
SANDBOXED_EXECUTORS = frozenset({"codex"})


def help_argv(executor: str, binary: str) -> list[str]:
    return [binary, *HELP_ARGS[executor]]


def side_paths(final: Path) -> tuple[Path, Path]:
    return final.with_name(final.name + ".transport-schema.json"), final.with_name(final.name + ".last-message.tmp")


def is_writing_worker(settings: dict[str, Any]) -> bool:
    """True for a Codex ``workspace-write`` sandbox or Claude with any non-read tool."""
    if settings["executor"] in SANDBOXED_EXECUTORS:
        return settings.get("sandbox") == "workspace-write"
    tools = (settings.get("tools") or "").strip()
    if tools == "default":
        return True
    return any(tool.strip() and tool.strip() not in READ_ONLY_CLAUDE_TOOLS for tool in tools.split(","))


def _claude(context: dict[str, Any]) -> dict[str, Any]:
    settings = context["settings"]
    add_dirs = [*context["add_dirs"], *context["read_dirs"]]
    argv = [context["binary"], "-p", "--output-format", "json", "--json-schema",
            json.dumps(context["schema"], separators=(",", ":"), ensure_ascii=False),
            "--no-session-persistence", *CLAUDE_ISOLATION_ARGS, "--tools", settings["tools"]]
    if settings.get("max_turns"):
        argv += ["--max-turns", str(settings["max_turns"])]
    if settings.get("model"):
        argv += ["--model", settings["model"]]
    if settings.get("permission_mode"):
        argv += ["--permission-mode", settings["permission_mode"]]
    for directory in add_dirs:
        argv += ["--add-dir", directory]
    return {"argv": argv, "side_files": []}


def _codex(context: dict[str, Any]) -> dict[str, Any]:
    settings = context["settings"]
    schema_file, last_message = side_paths(context["final"])
    argv = [context["binary"], "exec", "--ephemeral", "--color", "never", "--cd", str(context["repository"]), "--sandbox", settings["sandbox"],
            "--output-schema", str(schema_file), "--output-last-message", str(last_message)]
    if settings.get("model"):
        argv += ["--model", settings["model"]]
    if is_writing_worker(settings):
        argv += ["--config", CODEX_NO_EXTRA_WRITABLE_ROOTS]
        if settings.get("sandbox_network"):
            argv += ["--config", CODEX_NETWORK_ACCESS_CONFIG]
    for directory in context["add_dirs"]:
        argv += ["--add-dir", directory]
    argv.append("-")
    return {"argv": argv, "side_files": [schema_file], "stale_files": [last_message, context["final"].with_name(context["final"].name + ".raw-message.txt")]}


BUILDERS = {"claude": _claude, "codex": _codex}


def launch_plan(*, settings: dict[str, Any], schema: dict[str, Any], repository: Path, final: Path, add_dirs: list[str], binary: str,
                read_dirs: list[str] | None = None, worker_env: dict[str, str] | None = None) -> dict[str, Any]:
    """argv plus the side files the executor needs; executor-specific extras (cwd, env, record) are optional keys."""
    context = {"settings": settings, "schema": schema, "repository": repository, "final": final, "add_dirs": list(add_dirs),
               "binary": binary, "read_dirs": list(read_dirs or []), "worker_env": worker_env}
    return BUILDERS[settings["executor"]](context)


#: Bounded-run plan label of every external worker that is not OpenCode. It predates per-executor labels and is
#: kept so plans approved before OpenCode existed stay byte-identical; the real executor is EXECUTORS.md's.
LEGACY_PLAN_LABEL = "CODEX"


def plan_label(action: str) -> str:
    """``OPENCODE`` when EXECUTORS.md runs the planner action's stage on opencode, else the legacy ``CODEX`` label."""
    stage = "IMPLEMENT" if action == "IMPLEMENT_SLICE" else action
    try:
        import executor_launch

        executor = executor_launch.stage_settings(executor_launch.load_policy(), stage, executor=None, model=None, timeout=None)["executor"]
    except Exception:  # an unreadable policy is refused at dispatch; the plan keeps the legacy label
        return LEGACY_PLAN_LABEL
    return "OPENCODE" if executor == "opencode" else LEGACY_PLAN_LABEL
