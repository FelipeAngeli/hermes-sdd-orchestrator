#!/usr/bin/env python3
"""Sandbox probe and per-stage Codex settings helpers for the executor launcher.

Holds the path containment helpers the launcher uses (the installer keeps its own), the
Codex-only stage settings (``sandbox_network``, ``codex_home``, ``env_from``) and the
advisory sandbox probe used by ``preflight --probe-sandbox``. Standard library only.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

SANDBOX_NETWORK_ENABLED = "SANDBOX_NETWORK_ENABLED"
SANDBOX_NETWORK_REQUIRED = "SANDBOX_NETWORK_REQUIRED"
CODEX_NETWORK_ACCESS_CONFIG = "sandbox_workspace_write.network_access=true"

_ENV_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_FORBIDDEN_ENV_PREFIXES = ("TYPESAFE_", "JEV_", "HERMES_")
_LOADER_ENV_PREFIXES = ("LD_", "DYLD_")
_LOADER_ENV_NAMES = frozenset({"NODE_OPTIONS", "PYTHONPATH", "PYTHONSTARTUP", "BASH_ENV", "ENV"})
_MAX_ENV_FROM = 4
_PROBE_TIMEOUT_SECONDS = 20

_TCP_LISTEN_SCRIPT = (
    'const net=require("net");const s=net.createServer();s.on("error",()=>process.exit(1));'
    's.listen(0,"127.0.0.1",()=>s.close(()=>process.exit(0)));'
)
_UNIX_LISTEN_SCRIPT = (
    'const net=require("net"),path=require("path");const sock=path.join(process.cwd(),"sdd-probe.sock");'
    'const s=net.createServer();s.on("error",()=>process.exit(1));'
    "s.listen(sock,()=>s.close(()=>process.exit(0)));"
)


def _identity(path: Path) -> tuple[int, int] | None:
    try:
        status = path.stat()
    except OSError:
        return None
    return status.st_dev, status.st_ino


def _inside(path: Path, root: Path) -> bool:
    """True when ``path`` is ``root`` or below it, comparing files rather than spellings.

    ``Path.resolve()`` keeps the caller's letter case on a case-insensitive filesystem
    (``/X/REPO`` and ``/x/repo`` are the same directory), so the textual test alone lets an
    alias escape. Each existing ancestor of ``path`` is compared to ``root`` by
    ``(st_dev, st_ino)``, as the installer does for ``OBSIDIAN_VAULT_OVERLAPS_TARGET``;
    the textual test stays for paths that do not exist yet.
    """
    resolved, base = path.resolve(), root.resolve()
    if resolved == base or base in resolved.parents:
        return True
    target = _identity(base)
    if target is None:
        return False
    return any(_identity(candidate) == target for candidate in (resolved, *resolved.parents))


def stage_extension_error(stage: str, entry: dict[str, Any], writing_stages: frozenset[str], worker_env_names: frozenset[str],
                             worker_env_prefixes: tuple[str, ...], executor_env_prefixes: dict[str, tuple[str, ...]]) -> str | None:
    """Return the POLICY_INVALID message for the Codex-only stage keys, or ``None`` when valid."""
    sandbox_network = entry.get("sandbox_network")
    if sandbox_network is not None:
        if not isinstance(sandbox_network, bool):
            return "sandbox_network must be true or false."
        effective = entry.get("sandbox") or ("workspace-write" if stage in writing_stages else "read-only")
        if entry["executor"] != "codex" or effective != "workspace-write":
            return "sandbox_network is valid only for codex with the workspace-write sandbox."
    codex_home = entry.get("codex_home")
    if codex_home is not None:
        if entry["executor"] != "codex":
            return "codex_home is valid only for codex stages."
        if not isinstance(codex_home, str) or not Path(codex_home).is_absolute():
            return "codex_home must be null or an absolute path string."
    env_from = entry.get("env_from")
    if env_from is not None:
        if entry["executor"] != "codex":
            return "env_from is valid only for codex stages."
        if not isinstance(env_from, list) or len(env_from) > _MAX_ENV_FROM:
            return f"env_from must be null or a list of at most {_MAX_ENV_FROM} variable names."
        for name in env_from:
            if not isinstance(name, str) or not _ENV_NAME_PATTERN.fullmatch(name):
                return "env_from names must match ^[A-Z][A-Z0-9_]{0,63}$."
            if name.startswith(_FORBIDDEN_ENV_PREFIXES):
                return f"env_from cannot pass {name}: TYPESAFE_*, JEV_* and HERMES_* stay with the controller."
            if name in worker_env_names:
                return f"env_from cannot pass {name}: already passed to the worker."
            executor_prefixes = tuple(prefix for prefixes in executor_env_prefixes.values() for prefix in prefixes)
            if name.startswith((*worker_env_prefixes, *executor_prefixes)):
                return f"env_from cannot pass {name}: already passed to the worker."
            if name.startswith(_LOADER_ENV_PREFIXES) or name in _LOADER_ENV_NAMES:
                return f"env_from cannot pass {name}: loader-affecting variables stay with the launcher."
    prompt_profile = entry.get("prompt_profile")
    if prompt_profile is not None and prompt_profile not in {"standard", "compact"}:
        return "prompt_profile must be null or one of standard, compact."
    return None


def codex_home_error(settings: dict[str, Any], repository: Path, controller_root: Path) -> str | None:
    """Return the dispatch-time codex_home violation, or ``None`` when it is usable."""
    codex_home = settings.get("codex_home")
    if not codex_home:
        return None
    path = Path(codex_home)
    if not path.is_dir():
        return f"codex_home {codex_home} is not an existing directory."
    if _inside(path, repository) or _inside(path, controller_root):
        return "codex_home must not be inside the repository or the controller directory."
    return None


def apply_codex_env(executor: str, settings: dict[str, Any] | None, base_env: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Resolve the Codex-only env additions; values stay in the returned dict, never logged."""
    additions: dict[str, str] = {}
    missing: list[str] = []
    if executor != "codex" or not settings:
        return additions, missing
    for name in settings.get("env_from") or []:
        if name in base_env:
            additions[name] = base_env[name]
        else:
            missing.append(name)
    codex_home = settings.get("codex_home")
    if codex_home:
        additions["CODEX_HOME"] = codex_home
    return additions, missing


def preflight_stage_env(executor: str, settings: dict[str, Any] | None, result: dict[str, Any],
                        worker_environment: Any) -> tuple[dict[str, str] | None, dict[str, Any] | None]:
    """Resolve the stage worker env before probing; return (worker_env, blocked payload)."""
    if settings is not None and settings.get("codex_home") and not Path(settings["codex_home"]).is_dir():
        return None, {**result, "status": "BLOCKED", "reason": "CODEX_HOME_INVALID",
                      "next_step": f"codex_home {settings['codex_home']} is not an existing directory. Create it or fix codex_home in policies/EXECUTORS.md.",
                      "next_command": None}
    try:
        return worker_environment(executor, settings), None
    except Exception as exc:
        if getattr(exc, "status", None) == "EXECUTOR_ENV_MISSING":
            return None, {**result, "status": "BLOCKED", "reason": "EXECUTOR_ENV_MISSING", "detail": str(exc),
                          "next_step": getattr(exc, "next_step", None), "next_command": None}
        raise


def _probe_command(binary: str, network: bool, command: list[str]) -> list[str]:
    argv = [binary, "sandbox", "-c", 'sandbox_mode="workspace-write"']
    if network:
        argv += ["-c", CODEX_NETWORK_ACCESS_CONFIG]
    argv += ["--", *command]
    return argv


def _run_probe(argv: list[str], timeout: int, env: dict[str, str], scratch: str, run_quiet: Any) -> str:
    try:
        completed = run_quiet(argv, timeout, cwd=scratch, env=env)
    except subprocess.TimeoutExpired:
        return "ERROR"
    except OSError:
        return "ERROR"
    return "OK" if completed.returncode == 0 else "BLOCKED"


def _sandbox_config_supported(binary: str, env: dict[str, str], scratch: str, run_quiet: Any) -> bool:
    try:
        completed = run_quiet([binary, "sandbox", "--help"], _PROBE_TIMEOUT_SECONDS, cwd=scratch, env=env)
    except (subprocess.TimeoutExpired, OSError):
        return False
    output = completed.stdout + completed.stderr
    return "--config" in output


def _docker_probe(binary: str, network: bool, env: dict[str, str], scratch: str, run_quiet: Any) -> str:
    try:
        baseline = run_quiet(["docker", "info"], _PROBE_TIMEOUT_SECONDS, cwd=scratch, env=env)
    except (subprocess.TimeoutExpired, OSError):
        return "SKIPPED"
    if baseline.returncode != 0:
        return "SKIPPED"
    return _run_probe(_probe_command(binary, network, ["docker", "info"]), _PROBE_TIMEOUT_SECONDS, env, scratch, run_quiet)


def probe_sandbox(*, binary: str, settings: dict[str, Any], env: dict[str, str], run_quiet: Any) -> dict[str, Any]:
    """Run the three sandbox probes without a model call; never enables network by itself."""
    network = bool(settings.get("sandbox_network"))
    result: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="sdd-sandbox-probe-") as scratch:
        if not _sandbox_config_supported(binary, env, scratch, run_quiet):
            result["tcp_listen"] = "UNSUPPORTED"
            result["unix_listen"] = "UNSUPPORTED"
            result["docker"] = "UNSUPPORTED"
            result["network_access"] = network
            return result
        if shutil.which("node"):
            result["tcp_listen"] = _run_probe(
                _probe_command(binary, network, ["node", "-e", _TCP_LISTEN_SCRIPT]),
                _PROBE_TIMEOUT_SECONDS, env, scratch, run_quiet)
            result["unix_listen"] = _run_probe(
                _probe_command(binary, network, ["node", "-e", _UNIX_LISTEN_SCRIPT]),
                _PROBE_TIMEOUT_SECONDS, env, scratch, run_quiet)
        else:
            result["tcp_listen"] = "SKIPPED"
            result["unix_listen"] = "SKIPPED"
        if shutil.which("docker"):
            result["docker"] = _docker_probe(binary, network, env, scratch, run_quiet)
        else:
            result["docker"] = "SKIPPED"
    result["network_access"] = network
    return result

def preflight_sandbox_extra(*, do_probe: bool, binary: str, settings: dict[str, Any], worker_env: dict[str, str],
                            run_quiet: Any) -> tuple[dict[str, Any] | None, list[str], str | None]:
    """Compute the preflight sandbox probe, warnings and advisory next_step for a stage."""
    warnings: list[str] = []
    next_step: str | None = None
    if settings.get("sandbox_network"):
        warnings.append(SANDBOX_NETWORK_ENABLED)
    if not (do_probe and settings["executor"] == "codex" and settings.get("sandbox") == "workspace-write"):
        return None, warnings, next_step
    sandbox_probe = probe_sandbox(binary=binary, settings=settings, env=worker_env, run_quiet=run_quiet)
    if not settings.get("sandbox_network") and any(status == "BLOCKED" for key, status in sandbox_probe.items() if key != "network_access"):
        warnings.append(SANDBOX_NETWORK_REQUIRED)
        next_step = ("The sandbox probe was blocked. Set \"sandbox_network\": true on that stage in policies/EXECUTORS.md "
                     "(preferred; pinned, needs `sdd.py confirm-policy`) or add `[sandbox_workspace_write] network_access = true` to the CODEX_HOME config used.")
    return sandbox_probe, warnings, next_step

def model_probe(*, executor: str, binary: str, model: str | None, isolation_args: tuple[str, ...], probe_timeout: int,
                tail_characters: int, worker_env: dict[str, str], run_quiet: Any) -> dict[str, Any]:
    """One minimal real call that proves login and model acceptance; never used by the dispatch path."""
    with tempfile.TemporaryDirectory(prefix="sdd-executor-probe-") as scratch:
        if executor == "claude":
            argv = [binary, "-p", "--output-format", "json", "--no-session-persistence", *isolation_args, "--max-turns", "1", "--tools", ""]
            argv += ["--model", model] if model else []
            argv.append("Reply with the single word OK.")
        else:
            argv = [binary, "exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only", "--color", "never", "-C", scratch]
            argv += ["--model", model] if model else []
            argv += ["-o", str(Path(scratch) / "probe.txt"), "Reply with the single word OK."]
        try:
            completed = run_quiet(argv, probe_timeout, cwd=scratch, env=worker_env)
        except subprocess.TimeoutExpired:
            return {"model_check": "TIMEOUT", "probe_exit_code": 124}
        accepted = completed.returncode == 0
        if accepted and executor == "claude":
            try:
                accepted = not json.loads(completed.stdout).get("is_error", False)
            except (json.JSONDecodeError, AttributeError):
                accepted = False
        return {"model_check": "ACCEPTED" if accepted else "REJECTED", "probe_exit_code": completed.returncode,
                "probe_stderr_tail": completed.stderr[-tail_characters:]}

