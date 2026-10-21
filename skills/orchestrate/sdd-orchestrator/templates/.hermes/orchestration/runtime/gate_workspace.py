"""Workspace facts for the validation gates: changed files and fingerprint, read with hardened git.

The gates read the worker's repository, so git runs with a minimal environment, without
the system or global configuration, and with repository settings that can run programs
switched off (``core.fsmonitor``, ``core.hooksPath``, ``core.untrackedCache``). Renames
are disabled so a moved file reports both its old and new path. A failing git call
returns ``None`` (the caller treats it as INCONCLUSIVE), never an empty list.

Residual limit: other repository-local settings (for example a ``filter.<name>.clean``
driver bound through ``.gitattributes``) are still honoured by git, so a worker must
not be able to write the repository's ``.git`` directory.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Any

GIT_TIMEOUT = 60
GIT_SAFE_ENV = ("PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")
GIT_OVERRIDES = ("-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false", "-c", f"core.hooksPath={os.devnull}",
                 "-c", "status.relativePaths=false")


def git_env() -> dict[str, str]:
    env = {key: value for key in GIT_SAFE_ENV if (value := os.environ.get(key)) is not None}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0",
               HOME=os.devnull)
    return env


def git(repo: Path, *args: str) -> subprocess.CompletedProcess | None:
    try:
        completed = subprocess.run(["git", *GIT_OVERRIDES, "-C", str(repo), *args], capture_output=True, check=False,
                                   timeout=GIT_TIMEOUT, env=git_env())
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed if completed.returncode == 0 else None


def changed_files(repo: Path) -> list[str] | None:
    """Every path ``git status`` reports (tracked changes and untracked files), or None when git fails."""
    completed = git(repo, "status", "--porcelain", "-z", "--untracked-files=all", "--no-renames")
    if completed is None:
        return None
    files = [entry[3:] for entry in completed.stdout.decode("utf-8", "replace").split("\0") if len(entry) >= 4]
    return sorted(set(files))


def fingerprint(repo: Path) -> dict[str, Any]:
    """HEAD plus the content hash of every changed file: the exact state a command ran against."""
    head = git(repo, "rev-parse", "HEAD")
    changed = changed_files(repo)
    hashes: dict[str, str | None] = {}
    for path in changed or []:
        target = repo / path
        hashes[path] = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() and not target.is_symlink() else None
    return {"head": head.stdout.decode("utf-8", "replace").strip() if head else None,
            "changed": hashes if changed is not None else None}
