"""Workspace facts for the validation gates: which files changed, decided from file contents, not git's caches.

A worker controls the repository's files and ``.git``, so the gates never ask git to
compare content: git would apply the worker's attributes (``ident``, ``eol``, filter
drivers), its stat cache (``core.checkStat``, ``core.trustctime``) and its index flags
(``skip-worktree``, ``assume-unchanged``), each of which can hide an edit or run a
program. Instead:

- the reference tree is the controller's ``base`` (a full object id that is an ancestor
  of HEAD) or, without a base, HEAD. Its commit and every tree under it are read with
  ``git cat-file --batch`` and each object is re-hashed here, so a commit or tree object
  the worker rewrote in ``.git/objects`` is refused instead of trusted;
- every tracked path is compared on disk here: the git blob id of the file's raw bytes
  (or of a symlink's target), its executable bit and its type; a missing path, or one
  under a tracked directory that is no longer a real directory (for example a symlink),
  is changed;
- the working tree is walked here (the top-level ``.git`` skipped, nested repositories
  walked like any directory); an untracked path counts unless a ``.gitignore`` that is
  unchanged from the reference tree ignores it (``git check-ignore --no-index`` reports
  which rule decided), so a worker-written or edited ignore rule, ``.git/info/exclude``
  or ``core.excludesFile`` hides nothing; a directory ignored that way is not walked,
  except that nothing under a tracked directory is hidden by a rule that ignores that
  directory itself;
- the only git calls are ``rev-parse``, ``merge-base``, ``cat-file --batch`` and
  ``check-ignore``, none of which reads the index or file contents, so filters,
  attributes, hooks and fsmonitor are never involved; git runs with no system or global
  config, no replace refs or grafts, no lazy fetch from a promisor remote
  (``GIT_NO_LAZY_FETCH``, which would run the worker's ``core.sshCommand``),
  ``GIT_DIR``/``GIT_WORK_TREE`` pinned to the repository, and the settings that change
  ignore matching pinned (``FIXED_OVERRIDES``);
- ignored paths are outside the change list but not outside the fingerprint: the name,
  type, size, mtime and ctime of every ignored entry go into ``ignored``, so a planted
  cache (a ``.pyc`` Python would load) is new evidence and a stale PASS is not reused.

Submodules (gitlinks) are not supported: their content is another repository, so a tree
with one cannot be scanned. Exceeding ``MAX_ENTRIES`` walked entries or ``MAX_SCAN_BYTES``
compared means the change list cannot be decided (``changed`` is None). ``MAX_FILES``
changed paths, ``MAX_HASH_BYTES`` per fingerprinted file, unreadable or special files and
changed symlinks (whose target content is not hashed) make the fingerprint incomplete;
the engine never reuses an incomplete decision.
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
INCOMPLETE_MARKERS = ("oversized", "unreadable", "special", "symlink")


def git_env(repo: Path | None = None) -> dict[str, str]:
    env = {key: value for key in GIT_SAFE_ENV if (value := os.environ.get(key)) is not None}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0",
               GIT_NO_REPLACE_OBJECTS="1", GIT_GRAFT_FILE=os.devnull, GIT_NO_LAZY_FETCH="1", HOME=os.devnull)
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


def _objects(repo: Path, ids: list[str], kind: bytes) -> dict[str, bytes] | None:
    """Bodies of these objects, each re-hashed here: git does not re-hash a loose object when it reads one."""
    if not ids:
        return {}
    read = git(repo, "cat-file", "--batch", data="".join(oid + "\n" for oid in ids).encode("ascii"))
    if read is None:
        return None
    out, position, bodies = read.stdout, 0, {}
    for oid in ids:
        end = out.find(b"\n", position)
        header = out[position:end].split(b" ") if end >= 0 else []
        if len(header) != 3 or header[0].decode("ascii", "replace") != oid or header[1] != kind:
            return None
        size = int(header[2])
        body = out[end + 1:end + 1 + size]
        position = end + 2 + size
        if len(body) != size or hashlib.new("sha1" if len(oid) == 40 else "sha256", kind + b" %d\0" % size + body).hexdigest() != oid:
            return None
        bodies[oid] = body
    return bodies


def _tree(repo: Path, ref: str) -> dict[str, tuple[str, str]] | None:
    """Path -> (mode, blob id) of every entry under the commit ``ref``, from verified objects only."""
    commit = _objects(repo, [ref], b"commit")
    first = commit[ref].split(b"\n", 1)[0] if commit else b""
    if not first.startswith(b"tree "):
        return None
    width = 20 if len(ref) == 40 else 32
    entries, level = {}, [("", first[5:].decode("ascii"))]
    while level:
        bodies = _objects(repo, sorted({oid for _, oid in level}), b"tree")
        if bodies is None:
            return None
        following = []
        for prefix, oid in level:
            body, position = bodies[oid], 0
            while position < len(body):
                space, nul = body.find(b" ", position), body.find(b"\0", position)
                if space < 0 or nul < space:
                    return None
                mode, name = body[position:space].decode("ascii"), os.fsdecode(body[space + 1:nul])
                child = body[nul + 1:nul + 1 + width].hex()
                position = nul + 1 + width
                if len(child) != 2 * width or not name or "/" in name or name in (".", "..", ".git"):
                    return None
                if mode == "40000":
                    following.append((prefix + name + "/", child))
                else:
                    entries[prefix + name] = (mode, child)
        level = following
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


def _tracked_dirs(tree: dict[str, tuple[str, str]]) -> set[str]:
    return {str(parent) for path in tree for parent in Path(path).parents if str(parent) != "."}


def _broken_dirs(repo: Path, tree: dict[str, tuple[str, str]]) -> set[str]:
    """Tracked directories that are no longer real directories on disk (missing, a file or a symlink)."""
    return {directory for directory in _tracked_dirs(tree) if (repo / directory).is_symlink() or not (repo / directory).is_dir()}


def _tracked_changes(repo: Path, tree: dict[str, tuple[str, str]]) -> tuple[set[str] | None, bool]:
    """Tracked paths whose type, executable bit or bytes differ on disk (None past ``MAX_SCAN_BYTES``); and completeness."""
    changed, complete, budget = set(), True, MAX_SCAN_BYTES
    # A tracked directory replaced by a symlink (or a file) would make lstat() of its children follow the link.
    broken = _broken_dirs(repo, tree)
    changed |= {directory for directory in broken if (repo / directory).is_symlink() or (repo / directory).exists()}
    for path, (mode, oid) in tree.items():
        target = repo / path
        if any(str(parent) in broken for parent in Path(path).parents):
            changed.add(path)
            continue
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
        budget -= info.st_size
        if budget < 0:
            return None, False
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


def _untracked(repo: Path, tree: dict[str, tuple[str, str]], trusted: set[str]) -> tuple[list[str], list[str]] | None:
    """Untracked files and the ignored entries on disk, walked level by level; None when the walk is too large or git fails."""
    broken = _broken_dirs(repo, tree)
    tracked_dirs = _tracked_dirs(tree) - broken
    # A rule that ignores a tracked directory itself hides nothing under it: git tracks files there, so it is not a cache.
    exposed = _ignored(repo, sorted(directory + "/" for directory in tracked_dirs), trusted)
    if exposed is None:
        return None
    exposed = {directory.rstrip("/") for directory in exposed}
    found, hidden, level, seen = [], [], [""], 0
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
                if relative in broken:
                    continue  # already reported as changed by _tracked_changes, and not walked through
                if entry.is_dir(follow_symlinks=False):
                    (directories if relative in tracked_dirs else candidates).append(relative + "/")
                elif relative not in tree:
                    candidates.append(relative)
        ignored = _ignored(repo, candidates, trusted)
        if ignored is None:
            return None
        for candidate in candidates:
            parent = candidate.rstrip("/").rpartition("/")[0]
            if candidate in ignored and not any(parent == top or parent.startswith(top + "/") for top in exposed):
                hidden.append(candidate)
            else:
                (directories if candidate.endswith("/") else found).append(candidate)
        level = directories
    return found, hidden


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
    changed, complete = _tracked_changes(repo, tree)
    if changed is None:
        result["reason"] = "more than MAX_SCAN_BYTES to compare"
        return result
    trusted = {path for path in tree if (path == ".gitignore" or path.endswith("/.gitignore")) and path not in changed}
    walked = _untracked(repo, tree, trusted)
    if walked is None:
        result["reason"] = "the working tree could not be walked"
        return result
    untracked, result["ignored"] = walked
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
        return "symlink"
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


def _ignored_digest(repo: Path, roots: list[str]) -> str | None:
    """Name, type, size, mtime and ctime of every entry under the ignored paths (ctime cannot be set back by the worker)."""
    digest, pending, seen = hashlib.sha256(), sorted(root.rstrip("/") for root in roots), 0
    while pending:
        relative = pending.pop()
        try:
            info = (repo / relative).lstat()
        except OSError:
            return None
        seen += 1
        if seen > MAX_ENTRIES:
            return None
        digest.update(os.fsencode(relative) + b"\0%o %d %d %d\0" % (info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
        if stat.S_ISDIR(info.st_mode):
            try:
                pending.extend(sorted(relative + "/" + name for name in os.listdir(repo / relative)))
            except OSError:
                return None
    return digest.hexdigest()


def fingerprint(repo: Path, base: str | None = None) -> dict[str, Any]:
    """HEAD, base, the content hash of every changed file and the metadata of every ignored entry."""
    found = scan(repo, base)
    changed = found["changed"]
    hashes = {path: _hash(repo / path) for path in (changed or [])[:MAX_FILES]}
    ignored = _ignored_digest(repo, found.get("ignored", [])) if changed is not None else None
    complete = found["complete"] and ignored is not None and not any(value in INCOMPLETE_MARKERS for value in hashes.values())
    return {"head": found["head"], "base": base, "changed": hashes if changed is not None else None, "ignored": ignored,
            "complete": complete}
