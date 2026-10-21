"""Workspace facts for the validation gates: which files changed, decided from file contents, not git's caches.

A worker controls the repository's files and ``.git``, so the gates never ask git to
compare content: git would apply the worker's attributes (``ident``, ``eol``, filter
drivers), its stat cache (``core.checkStat``, ``core.trustctime``) and its index flags
(``skip-worktree``, ``assume-unchanged``), each of which can hide an edit or run a
program. Instead:

- the reference tree is the controller's ``base`` (a full object id that is an ancestor
  of HEAD) or, without a base, HEAD; ``git ls-tree`` lists its paths, modes and blob ids;
- every tracked path is compared on disk here: the git blob id of the file's raw bytes
  (or of a symlink's target), its executable bit and its type; a missing path is changed;
- the working tree is walked here (the top-level ``.git`` skipped, nested repositories
  walked like any directory); an untracked path counts unless a ``.gitignore`` that is
  unchanged from the reference tree ignores it (``git check-ignore --no-index`` reports
  which rule decided), so a worker-written or edited ignore rule, ``.git/info/exclude``
  or ``core.excludesFile`` hides nothing; a directory ignored that way is not walked;
- the only git calls are ``rev-parse``, ``merge-base``, ``ls-tree`` and ``check-ignore``,
  none of which reads the index or file contents, so filters, attributes, hooks and
  fsmonitor are never involved; git runs with no system or global config, no replace
  refs or grafts, ``GIT_DIR``/``GIT_WORK_TREE`` pinned to the repository, and the
  settings that change ignore matching pinned (``FIXED_OVERRIDES``).

Submodules (gitlinks) are not supported: their content is another repository, so a tree
with one makes the scan incomplete. Limits (``MAX_FILES`` changed paths,
``MAX_ENTRIES`` walked entries, ``MAX_SCAN_BYTES`` hashed, ``MAX_HASH_BYTES`` per
fingerprinted file) and unreadable or special files also make it incomplete; the engine
never reuses an incomplete decision.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Any

GIT_TIMEOUT = 60
GIT_SAFE_ENV = ("PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")
MAX_HASH_BYTES = 64 * 1024 * 1024
MAX_FILES = 5000
MAX_ENTRIES = 200_000
MAX_SCAN_BYTES = 4 * 1024 * 1024 * 1024
CHUNK = 1024 * 1024
OBJECT_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
#: The settings that change how ``check-ignore`` matches paths; pinned so repository config cannot change the result.
FIXED_OVERRIDES = (f"core.excludesFile={os.devnull}", "core.ignoreCase=false", "core.precomposeUnicode=false")
INCOMPLETE_MARKERS = ("oversized", "unreadable", "special")


def git_env(repo: Path | None = None) -> dict[str, str]:
    env = {key: value for key in GIT_SAFE_ENV if (value := os.environ.get(key)) is not None}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0",
               GIT_NO_REPLACE_OBJECTS="1", GIT_GRAFT_FILE=os.devnull, HOME=os.devnull)
    if repo is not None:
        env.update(GIT_DIR=str(repo / ".git"), GIT_WORK_TREE=str(repo))
    return env


def git(repo: Path, *args: str, data: bytes | None = None, ok_codes: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess | None:
    settings = [part for setting in FIXED_OVERRIDES for part in ("-c", setting)]
    try:
        completed = subprocess.run(["git", *settings, *args], cwd=repo, capture_output=True, check=False, timeout=GIT_TIMEOUT,
                                   env=git_env(repo), input=data, stdin=None if data is not None else subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed if completed.returncode in ok_codes else None


def valid_base(repo: Path, base: str) -> bool:
    """A full object id of a commit that is an ancestor of HEAD (a ref name could be moved by the worker)."""
    return bool(OBJECT_ID.match(base)) and git(repo, "merge-base", "--is-ancestor", base, "HEAD") is not None


def _tree(repo: Path, ref: str) -> dict[str, tuple[str, str]] | None:
    """Path -> (mode, blob id) of every entry in the reference tree."""
    listed = git(repo, "ls-tree", "-r", "-z", "--full-tree", ref)
    if listed is None:
        return None
    entries = {}
    for record in listed.stdout.split(b"\0"):
        if not record:
            continue
        meta, _, path = record.partition(b"\t")
        mode, _kind, oid = meta.decode("ascii").split(" ")
        entries[os.fsdecode(path)] = (mode, oid)
    return entries


def _blob_id(path: Path, info: os.stat_result, algorithm: str) -> str:
    """The id git gives these exact bytes (no filter, attribute or line-ending conversion)."""
    digest = hashlib.new(algorithm)
    if stat.S_ISLNK(info.st_mode):
        target = os.fsencode(os.readlink(path))
        digest.update(b"blob %d\0" % len(target) + target)
        return digest.hexdigest()
    digest.update(b"blob %d\0" % info.st_size)
    with path.open("rb") as handle:
        while block := handle.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def _tracked_changes(repo: Path, tree: dict[str, tuple[str, str]], budget: list[int]) -> tuple[set[str], bool]:
    """Tracked paths whose type, executable bit or bytes differ on disk; and whether the comparison was complete."""
    changed, complete = set(), True
    for path, (mode, oid) in tree.items():
        target = repo / path
        try:
            info = target.lstat()
        except FileNotFoundError:
            changed.add(path)
            continue
        except OSError:
            changed.add(path)
            complete = False
            continue
        if stat.S_ISLNK(info.st_mode):
            expected = "120000"
        elif stat.S_ISREG(info.st_mode):
            expected = "100755" if info.st_mode & stat.S_IXUSR else "100644"
        else:
            changed.add(path)
            continue
        if expected != ("100644" if mode == "100664" else mode):
            changed.add(path)
            continue
        budget[0] -= info.st_size
        if budget[0] < 0:
            return changed, False
        try:
            if _blob_id(target, info, "sha1" if len(oid) == 40 else "sha256") != oid:
                changed.add(path)
        except OSError:
            changed.add(path)
            complete = False
    return changed, complete


def _ignored(repo: Path, paths: list[str], trusted: set[str]) -> set[str] | None:
    """The paths whose deciding ignore rule comes from a .gitignore unchanged from the reference tree."""
    if not paths:
        return set()
    data = b"".join(os.fsencode(path) + b"\0" for path in paths)
    # Exit 1 means "nothing ignored"; -v -n prints every path with the source of its last matching rule (empty if none).
    checked = git(repo, "check-ignore", "--no-index", "-v", "-n", "-z", "--stdin", data=data, ok_codes=(0, 1))
    if checked is None:
        return None
    fields = checked.stdout.split(b"\0")
    hidden = set()
    for start in range(0, len(fields) - 3, 4):
        source, _line, pattern, path = fields[start:start + 4]
        if os.fsdecode(source) in trusted and not pattern.startswith(b"!"):
            hidden.add(os.fsdecode(path))
    return hidden


def _untracked(repo: Path, tree: dict[str, tuple[str, str]], trusted: set[str]) -> list[str] | None:
    """Untracked files on disk, walked level by level; None when the walk is too large or git fails."""
    tracked_dirs = {str(Path(path).parent) for path in tree} | {str(parent) for path in tree for parent in Path(path).parents}
    found, level, seen = [], [""], 0
    while level:
        directories, candidates = [], []
        for directory in level:
            try:
                entries = list(os.scandir(repo / directory if directory else repo))
            except OSError:
                return None
            for entry in entries:
                relative = directory + entry.name
                if not directory and entry.name == ".git":
                    continue
                seen += 1
                if seen > MAX_ENTRIES:
                    return None
                if entry.is_dir(follow_symlinks=False):
                    (directories if relative in tracked_dirs else candidates).append(relative + "/")
                elif relative not in tree:
                    candidates.append(relative)
        ignored = _ignored(repo, candidates, trusted)
        if ignored is None:
            return None
        for candidate in candidates:
            if candidate not in ignored:
                (directories if candidate.endswith("/") else found).append(candidate)
        level = directories
    return found


def scan(repo: Path, base: str | None = None) -> dict[str, Any]:
    """Changed paths relative to ``base`` (or HEAD) and whether the result can be trusted as complete."""
    result: dict[str, Any] = {"head": None, "base": base, "changed": None, "complete": False}
    if git(repo, "rev-parse", "--git-dir") is None:
        result["reason"] = "not a git repository"
        return result
    head = git(repo, "rev-parse", "--verify", "--quiet", "HEAD^{commit}", ok_codes=(0, 1))
    head_id = head.stdout.decode("ascii").strip() or None if head else None
    result["head"] = head_id
    if base and not valid_base(repo, base):
        result["reason"] = "invalid base"
        return result
    reference = base or head_id
    tree = _tree(repo, reference) if reference else {}
    if tree is None:
        result["reason"] = "git failed"
        return result
    if any(mode == "160000" for mode, _ in tree.values()):
        result["reason"] = "submodules are not supported"
        return result
    budget = [MAX_SCAN_BYTES]
    changed, complete = _tracked_changes(repo, tree, budget)
    trusted = {path for path in tree if (path == ".gitignore" or path.endswith("/.gitignore")) and path not in changed}
    untracked = _untracked(repo, tree, trusted)
    if untracked is None:
        result["reason"] = "the working tree could not be walked"
        return result
    result["changed"] = sorted(changed | set(untracked))
    result["complete"] = complete and head_id is not None and len(result["changed"]) <= MAX_FILES
    return result


def changed_files(repo: Path, base: str | None = None) -> list[str] | None:
    """Every changed, deleted or untracked path (relative to ``base`` when given), or None when it cannot be decided."""
    return scan(repo, base)["changed"]


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
    found = scan(repo, base)
    changed = found["changed"]
    hashes = {path: _hash(repo / path) for path in (changed or [])[:MAX_FILES]}
    complete = found["complete"] and not any(value in INCOMPLETE_MARKERS for value in hashes.values())
    return {"head": found["head"], "base": base, "changed": hashes if changed is not None else None, "complete": complete}
