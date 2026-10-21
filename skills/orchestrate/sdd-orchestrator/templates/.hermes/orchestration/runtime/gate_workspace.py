"""Workspace facts for the validation gates: changed files and fingerprint, read with hardened git.

The gates read the worker's repository, so nothing the worker can write may run a
program or hide a change:

- git runs with a minimal environment, no system or global configuration, no system
  attributes, and overrides for every repository setting that can run a program or
  redirect the scan (``core.fsmonitor``, ``core.hooksPath``, ``core.worktree``,
  ``core.excludesFile``, ``core.attributesFile``, ``diff.external``, and each locally
  declared ``filter.<name>`` driver, whose clean, smudge and process programs are
  emptied);
- with a controller-owned ``base`` commit, changed files are ``git diff <base>`` plus
  untracked files that only ``.gitignore`` files exclude (``.git/info/exclude`` and
  ``core.excludesFile`` are ignored), so committing a change or hiding it in a local
  exclude file does not make it disappear; without a base, ``git status`` is used;
- renames are disabled, so a moved file reports both paths;
- index flags that hide edits (``assume-unchanged``, ``skip-worktree``) are listed by
  ``hidden_by_index``; a failing git call returns ``None``, never an empty list.

Files are hashed in chunks; a file over ``MAX_HASH_BYTES`` or a change set over
``MAX_FILES`` makes the fingerprint incomplete, which the engine never reuses.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path
from typing import Any

GIT_TIMEOUT = 60
GIT_SAFE_ENV = ("PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")
MAX_HASH_BYTES = 64 * 1024 * 1024
MAX_FILES = 5000
CHUNK = 1024 * 1024
FILTER_KEY = re.compile(r"^filter\.(.+)\.[a-z]+$")


def git_env() -> dict[str, str]:
    env = {key: value for key in GIT_SAFE_ENV if (value := os.environ.get(key)) is not None}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_ATTR_NOSYSTEM="1", GIT_OPTIONAL_LOCKS="0",
               GIT_TERMINAL_PROMPT="0", HOME=os.devnull)
    return env


def _raw(repo: Path, settings: list[str], *args: str) -> subprocess.CompletedProcess | None:
    try:
        completed = subprocess.run(["git", *settings, "-C", str(repo), *args], capture_output=True, check=False,
                                   timeout=GIT_TIMEOUT, env=git_env(), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed if completed.returncode == 0 else None


def overrides(repo: Path) -> list[str]:
    """Configuration overrides that neutralise every program-running or scan-redirecting repository setting."""
    found = ["-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false", "-c", f"core.hooksPath={os.devnull}",
             "-c", f"core.worktree={repo}", "-c", f"core.excludesFile={os.devnull}", "-c", f"core.attributesFile={os.devnull}",
             "-c", "status.relativePaths=false", "-c", "diff.external="]
    listed = _raw(repo, found, "config", "--local", "--includes", "--name-only", "--get-regexp", r"^filter\.")
    names = {match.group(1) for line in (listed.stdout.decode("utf-8", "replace").splitlines() if listed else [])
             if (match := FILTER_KEY.match(line.strip()))}
    for name in sorted(names):
        found += ["-c", f"filter.{name}.clean=", "-c", f"filter.{name}.smudge=", "-c", f"filter.{name}.process=",
                  "-c", f"filter.{name}.required=false"]
    return found


def git(repo: Path, *args: str) -> subprocess.CompletedProcess | None:
    return _raw(repo, overrides(repo), *args)


def _split(completed: subprocess.CompletedProcess | None) -> list[str] | None:
    if completed is None:
        return None
    return [item for item in completed.stdout.decode("utf-8", "replace").split("\0") if item]


def changed_files(repo: Path, base: str | None = None) -> list[str] | None:
    """Every changed or untracked path (relative to ``base`` when given), or None when git fails."""
    if base:
        if git(repo, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}") is None:
            return None
        tracked = _split(git(repo, "diff", "--name-only", "--no-renames", "--no-ext-diff", "--no-textconv", "-z", base, "--"))
        untracked = _split(git(repo, "ls-files", "--others", "--exclude-per-directory=.gitignore", "-z"))
        if tracked is None or untracked is None:
            return None
        return sorted(set(tracked) | set(untracked))
    entries = _split(git(repo, "status", "--porcelain", "-z", "--untracked-files=all", "--no-renames"))
    return None if entries is None else sorted({entry[3:] for entry in entries if len(entry) >= 4})


def hidden_by_index(repo: Path) -> list[str] | None:
    """Tracked paths whose edits the index is told to ignore (assume-unchanged or skip-worktree)."""
    entries = _split(git(repo, "ls-files", "-v", "-z"))
    if entries is None:
        return None
    return sorted(entry[2:] for entry in entries if len(entry) > 2 and (entry[0].islower() or entry[0] == "S"))


def _hash(path: Path) -> str | None:
    if not path.is_file() or path.is_symlink():
        return None
    if path.stat().st_size > MAX_HASH_BYTES:
        return "oversized"
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def fingerprint(repo: Path, base: str | None = None) -> dict[str, Any]:
    """HEAD, base and the content hash of every changed file: the exact state a command ran against."""
    head = git(repo, "rev-parse", "HEAD")
    changed = changed_files(repo, base)
    hashes = {path: _hash(repo / path) for path in (changed or [])[:MAX_FILES]}
    complete = head is not None and changed is not None and len(changed) <= MAX_FILES and "oversized" not in hashes.values()
    return {"head": head.stdout.decode("utf-8", "replace").strip() if head else None, "base": base,
            "changed": hashes if changed is not None else None, "complete": complete}
