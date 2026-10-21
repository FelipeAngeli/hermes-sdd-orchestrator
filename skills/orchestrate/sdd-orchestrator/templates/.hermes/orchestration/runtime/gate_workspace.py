"""Workspace facts for the validation gates: changed files and fingerprint, read with hardened git.

The gates read the worker's repository, so nothing the worker can write may run a
program or hide a change:

- git runs with a minimal environment: no system or global configuration, no system
  attributes, no replace refs or grafts, ``GIT_DIR`` and ``GIT_WORK_TREE`` pinned to the
  repository (so ``core.worktree`` cannot point the scan at a decoy), and overrides for
  settings that run programs or trust stale metadata (``FIXED_OVERRIDES``; external diff
  drivers are off through ``--no-ext-diff``, and ``status`` never runs one), plus emptied
  clean, smudge and process programs for every ``filter.<name>`` git reads in any scope;
- with a controller-owned ``base`` (a full object id that is an ancestor of HEAD),
  changes are computed against a fresh temporary index built from the base tree, so
  commits since the base, stat-cache settings and index flags (``assume-unchanged``,
  ``skip-worktree``) cannot hide an edit; an untracked file counts unless a ``.gitignore``
  that exists unchanged in the base tree ignores it, so a worker-written or edited
  ignore rule hides nothing;
- without a base, ``git status`` is used, and index flags make the fingerprint
  incomplete (``hidden_by_index``);
- renames are disabled, so a moved file reports both paths; a failing git call or an
  invalid base returns ``None``, never an empty list.

Files are hashed in chunks; a file over ``MAX_HASH_BYTES``, an unreadable or special
file, or a change set over ``MAX_FILES`` make the fingerprint incomplete, which the
engine never reuses.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

GIT_TIMEOUT = 60
GIT_SAFE_ENV = ("PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")
MAX_HASH_BYTES = 64 * 1024 * 1024
MAX_FILES = 5000
CHUNK = 1024 * 1024
FILTER_KEY = re.compile(r"^filter\.(.+)\.[a-z]+$")
OBJECT_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
#: Overridden on every call: none of these may run a program, redirect the scan or trust stale metadata.
FIXED_OVERRIDES = ("core.fsmonitor=false", "core.untrackedCache=false", f"core.hooksPath={os.devnull}",
                   f"core.excludesFile={os.devnull}", f"core.attributesFile={os.devnull}", "status.relativePaths=false",
                   "core.fileMode=true",
                   "extensions.worktreeConfig=false", "core.sparseCheckout=false")
INCOMPLETE_MARKERS = ("oversized", "unreadable", "special")


def git_env(repo: Path | None = None, index: Path | None = None) -> dict[str, str]:
    env = {key: value for key in GIT_SAFE_ENV if (value := os.environ.get(key)) is not None}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_ATTR_NOSYSTEM="1", GIT_OPTIONAL_LOCKS="0",
               GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1", GIT_GRAFT_FILE=os.devnull, HOME=os.devnull)
    if repo is not None:
        env.update(GIT_DIR=str(repo / ".git"), GIT_WORK_TREE=str(repo))
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    return env


def _raw(repo: Path, settings: list[str], *args: str, index: Path | None = None, data: bytes | None = None,
         ok_codes: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess | None:
    try:
        completed = subprocess.run(["git", *settings, *args], cwd=repo, capture_output=True, check=False, timeout=GIT_TIMEOUT,
                                   env=git_env(repo, index), input=data, stdin=None if data is not None else subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed if completed.returncode in ok_codes else None


def overrides(repo: Path) -> list[str]:
    """Configuration overrides that neutralise every program-running or scan-redirecting repository setting."""
    found = [part for setting in FIXED_OVERRIDES for part in ("-c", setting)]
    # Listed with the worktree extension still on and no scope flag, so repository, worktree and included config all count.
    listed = _raw(repo, [], "config", "--includes", "--name-only", "--get-regexp", r"^filter\.")
    names = {match.group(1) for line in (listed.stdout.decode("utf-8", "replace").splitlines() if listed else [])
             if (match := FILTER_KEY.match(line.strip()))}
    for name in sorted(names):
        found += ["-c", f"filter.{name}.clean=", "-c", f"filter.{name}.smudge=", "-c", f"filter.{name}.process=",
                  "-c", f"filter.{name}.required=false"]
    return found


def git(repo: Path, *args: str, index: Path | None = None, data: bytes | None = None,
        ok_codes: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess | None:
    return _raw(repo, overrides(repo), *args, index=index, data=data, ok_codes=ok_codes)


def _split(completed: subprocess.CompletedProcess | None) -> list[str] | None:
    if completed is None:
        return None
    return [item for item in completed.stdout.decode("utf-8", "replace").split("\0") if item]


def valid_base(repo: Path, base: str) -> bool:
    """A full object id of a commit that is an ancestor of HEAD (a ref name could be moved by the worker)."""
    return (bool(OBJECT_ID.match(base)) and git(repo, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}") is not None
            and git(repo, "merge-base", "--is-ancestor", base, "HEAD") is not None)


def _changed_since(repo: Path, base: str) -> list[str] | None:
    with tempfile.TemporaryDirectory(prefix="gate-index-") as scratch:
        index = Path(scratch) / "index"
        if git(repo, "read-tree", base, index=index) is None:
            return None
        tracked = _split(git(repo, "diff", "--name-only", "--no-renames", "--no-ext-diff", "--no-textconv", "-z", index=index))
        untracked = _split(git(repo, "ls-files", "--others", "-z", index=index))
        if tracked is None or untracked is None:
            return None
        listed = _split(git(repo, "ls-tree", "-r", "--name-only", "-z", base))
        if listed is None:
            return None
        trusted = {path for path in set(listed) - set(tracked) if path == ".gitignore" or path.endswith("/.gitignore")}
        hidden = _ignored_by_trusted(repo, untracked, trusted, index)
        if hidden is None:
            return None
        return sorted(set(tracked) | (set(untracked) - hidden))


def _ignored_by_trusted(repo: Path, paths: list[str], trusted: set[str], index: Path) -> set[str] | None:
    """The paths whose deciding ignore rule comes from a .gitignore that exists unchanged in the base tree."""
    if not paths:
        return set()
    data = b"".join(os.fsencode(path) + b"\0" for path in paths)
    # Exit 1 means "nothing ignored"; -v -n prints every path with the source of its last matching rule (empty if none).
    checked = git(repo, "check-ignore", "-v", "-n", "-z", "--stdin", index=index, data=data, ok_codes=(0, 1))
    if checked is None:
        return None
    fields = checked.stdout.decode("utf-8", "replace").split("\0")
    hidden = set()
    for start in range(0, len(fields) - 3, 4):
        source, _line, pattern, path = fields[start:start + 4]
        if source in trusted and not pattern.startswith("!"):
            hidden.add(path)
    return hidden


def changed_files(repo: Path, base: str | None = None) -> list[str] | None:
    """Every changed or untracked path (relative to ``base`` when given), or None when git fails or the base is invalid."""
    if base:
        return _changed_since(repo, base) if valid_base(repo, base) else None
    entries = _split(git(repo, "status", "--porcelain", "-z", "--untracked-files=all", "--no-renames"))
    return None if entries is None else sorted({entry[3:] for entry in entries if len(entry) >= 4})


def hidden_by_index(repo: Path) -> list[str] | None:
    """Tracked paths whose edits the index is told to ignore (assume-unchanged or skip-worktree)."""
    entries = _split(git(repo, "ls-files", "-v", "-z"))
    if entries is None:
        return None
    return sorted(entry[2:] for entry in entries if len(entry) > 2 and (entry[0].islower() or entry[0] == "S"))


def _hash(path: Path) -> str | None:
    """Content hash; ``None`` for a deleted path or a directory, a marker for anything that cannot be hashed safely."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        return "unreadable"
    if stat.S_ISLNK(info.st_mode):
        return "symlink:" + hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest()
    if stat.S_ISDIR(info.st_mode):
        return None
    if not stat.S_ISREG(info.st_mode):
        return "special"
    if info.st_size > MAX_HASH_BYTES:
        return "oversized"
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while block := handle.read(CHUNK):
                digest.update(block)
    except OSError:
        return "unreadable"
    return digest.hexdigest()


def fingerprint(repo: Path, base: str | None = None) -> dict[str, Any]:
    """HEAD, base and the content hash of every changed file: the exact state a command ran against."""
    head = git(repo, "rev-parse", "HEAD")
    changed = changed_files(repo, base)
    hidden = [] if base else hidden_by_index(repo)
    hashes = {path: _hash(repo / path) for path in (changed or [])[:MAX_FILES]}
    complete = (head is not None and changed is not None and len(changed) <= MAX_FILES and hidden == []
                and not any(value in INCOMPLETE_MARKERS for value in hashes.values()))
    return {"head": head.stdout.decode("utf-8", "replace").strip() if head else None, "base": base,
            "changed": hashes if changed is not None else None, "hidden_by_index": hidden, "complete": complete}
