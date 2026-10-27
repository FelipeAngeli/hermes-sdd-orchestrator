#!/usr/bin/env python3
"""Which paths the opt-in workspace audit leaves out as test-run artifacts.

Running a project's tests rewrites, on every run, files the worker never edits: the test
runner's cache, coverage data and bytecode caches. They are git-ignored, so the audit
(``runtime/dispatch_audit.py``), which hashes ignored files too, used to report each of
them as an ``UNDECLARED_CHANGE`` and stop every slice that ran the tests. This module
applies an explicit, narrow list instead of any broad pattern; the ecosystem names
themselves live in ``detect_stack.py`` (``TEST_RUN_ARTIFACT_*``, ``BYTECODE_*``), the only
module allowed to hold them. A path is left out only when **all** of these hold:

* it is untracked and git-ignored now (a tracked file, or an unignored one, always counts),
  or it was listed in the baseline, **was git-ignored there** and is now gone from both
  ``git status`` and the disk;
* it matches one exact rule: a path under a root artifact tree that the baseline rules ignored
  from outside the tree (``roots``) and whose suffix is not code
  (``TEST_RUN_ARTIFACT_CODE_SUFFIXES``), a root artifact file name, or a root file name
  starting with an artifact prefix and not ending in a code suffix; or a bytecode file directly inside a bytecode-cache
  directory whose name follows the interpreter scheme (``BYTECODE_NAME``) and whose source
  ``<stem>`` sibling exists in the parent directory;
* when it exists, it is a regular file (never a link or directory), and a bytecode file
  has a header that is timestamp-based (flags word 0, the ``\\r\\n`` magic tail) and records
  exactly the sibling source's mtime and size, modulo 2**32, as the interpreter checks it.

The root trees include the type-checker and linter caches (``GATE_CACHE_TREES``): a worker
that runs those tools rewrites them every time, and no gate reads them because
``runtime/gate_env.py`` gives every gate run fresh ones. Which roots a comparison uses is
decided by the baseline (``baseline_roots``).

Hash-based bytecode (flags 1 or 3) always counts: checking it needs the interpreter's own
source hash, which depends on the project's interpreter version. Known limit: a crafted
timestamp-based file whose header copies its source's mtime and size is left out, and an
interpreter that reads that cache directory would run it (see ``policies/EXECUTORS.md``).
Standard library only; nothing here writes the repository.
"""
from __future__ import annotations

import re
import stat
import struct
from pathlib import Path
from typing import Any, Iterable

from detect_stack import (BYTECODE_CACHE_DIR, BYTECODE_NAME, BYTECODE_SOURCE_SUFFIX, TEST_RUN_ARTIFACT_CODE_SUFFIXES,
                          TEST_RUN_ARTIFACT_FILES, TEST_RUN_ARTIFACT_PREFIXES, TEST_RUN_ARTIFACT_TREES,
                          TEST_RUNNER_ARTIFACT_TREES)

#: The artifact tree roots, exposed so the caller can ask Git which of them its rules ignore.
TREE_ROOTS = TEST_RUN_ARTIFACT_TREES
_BYTECODE = re.compile(BYTECODE_NAME)
_HEADER = 16


def roots(sources: dict[str, str]) -> tuple[str, ...]:
    """The artifact tree roots an ignore rule **outside the tree** covers.

    ``sources`` maps each root of ``TREE_ROOTS`` to the ignore file that matched it
    (``git check-ignore -v``), or to an empty string when nothing did. A rule the worker
    wrote inside the tree (``.pytest_cache/.gitignore`` holding ``*``) is rejected: it would
    otherwise make the subtree, and its own change, invisible to the audit.
    """
    return tuple(root for root in TREE_ROOTS if (sources.get(root) or "") and not sources[root].startswith(root + "/"))


def _outside_trees(rules: dict[str, str] | None) -> dict[str, str]:
    return {path: digest for path, digest in (rules or {}).items() if not path.startswith(tuple(root + "/" for root in TREE_ROOTS))}


def baseline_roots(baseline: dict[str, Any] | None, current: Iterable[str], rules: dict[str, str]) -> tuple[str, ...]:
    """The artifact tree roots a snapshot uses: the baseline's for every tree it evaluated.

    ``current`` holds the roots the current rules ignore from outside the tree and ``rules`` the
    current ``ignore_rules``. Without a baseline, or with one stored before ``artifact_roots``
    existed (15.19.0), the current rules decide. Otherwise the baseline's ``artifact_roots`` decide
    for every tree it evaluated (``artifact_trees``; a baseline without that field, stored by
    15.19.1, evaluated ``TEST_RUNNER_ARTIFACT_TREES``). A tree added to the list since then is
    decided by the current rules, and only when every ignore rule outside the artifact trees is
    unchanged since the baseline, so the current answer is the one the baseline's rules gave; a
    rule inside a tree never decides whether that tree is ignored. A changed rule leaves the new
    trees out of the list and is itself reported (``IGNORE_RULES_CHANGED``).
    """
    current = tuple(current)
    if baseline is None or "artifact_roots" not in baseline:
        return current
    evaluated = tuple(baseline.get("artifact_trees") or TEST_RUNNER_ARTIFACT_TREES)
    same = _outside_trees(baseline.get("ignore_rules")) == _outside_trees(rules)
    recorded = tuple(baseline["artifact_roots"])
    return tuple(root for root in TREE_ROOTS if root in recorded or (root not in evaluated and same and root in current))


def _bytecode_source(path: str) -> str | None:
    """``<dir>/<stem><suffix>`` for ``<dir>/<cache-dir>/<name>``, or ``None`` when the name does not qualify."""
    parts = path.split("/")
    match = _BYTECODE.fullmatch(parts[-1])
    if len(parts) < 2 or parts[-2] != BYTECODE_CACHE_DIR or not match:
        return None
    return "/".join([*parts[:-2], match.group("stem") + BYTECODE_SOURCE_SUFFIX])


def candidate(path: str, roots: Iterable[str] = TEST_RUN_ARTIFACT_TREES) -> bool:
    """True when ``path`` matches one of the exact name rules (content and Git status are checked apart).

    ``roots`` are the artifact tree roots that qualify: the caller passes the roots the
    **baseline** rules ignored from outside the tree, so a ``.pytest_cache/.gitignore`` the
    worker wrote (``*``) cannot make the subtree eligible (``dispatch_audit.artifact_roots``).
    """
    parts = path.split("/")
    code = parts[-1].lower().endswith(TEST_RUN_ARTIFACT_CODE_SUFFIXES)
    if len(parts) > 1 and parts[0] in tuple(roots):
        return not code
    if len(parts) == 1 and (path in TEST_RUN_ARTIFACT_FILES or path.startswith(TEST_RUN_ARTIFACT_PREFIXES)):
        return not code
    return _bytecode_source(path) is not None


def _regular(path: Path) -> Any:
    try:
        info = path.lstat()
    except OSError:
        return None
    return info if stat.S_ISREG(info.st_mode) else None


def _bytecode_matches(repo: Path, path: str) -> bool:
    """A timestamp-based header recording exactly the sibling source's mtime and size."""
    source = _regular(repo / (_bytecode_source(path) or ""))
    if source is None:
        return False
    try:
        with (repo / path).open("rb") as handle:
            header = handle.read(_HEADER)
    except OSError:
        return False
    if len(header) < _HEADER or header[2:4] != b"\r\n":
        return False
    flags, mtime, size = struct.unpack("<III", header[4:_HEADER])
    return flags == 0 and mtime == int(source.st_mtime) & 0xFFFFFFFF and size == source.st_size & 0xFFFFFFFF


def eligible(repo: Path, path: str, *, present: bool, roots: Iterable[str] = TEST_RUN_ARTIFACT_TREES) -> bool:
    """For an untracked, git-ignored ``path``: is it a test-run artifact the audit leaves out?

    A removed artifact (``present`` false: the caller checked that the baseline listed it,
    that the baseline had it git-ignored and that it is gone from ``git status``) needs the
    name rule and must really be absent from disk, so a tracked file reverted to its
    committed content still counts; deleting a cache runs nothing. One that exists must
    also be a regular file, and a bytecode file must match its sibling source.
    """
    if not candidate(path, roots):
        return False
    if not present:
        try:
            (Path(repo) / path).lstat()
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return False
    if _regular(Path(repo) / path) is None:
        return False
    return _bytecode_source(path) is None or _bytecode_matches(Path(repo), path)


def select(repo: Path, ignored: Iterable[str], removed: Iterable[str], *,
           roots: Iterable[str] = TEST_RUN_ARTIFACT_TREES) -> list[str]:
    """The artifacts among the paths git-ignored now and the paths the baseline had ignored and that are gone.

    ``removed`` holds only the baseline paths the **baseline** had git-ignored
    (``dispatch_audit``), so an untracked, unignored file with an artifact name that the
    dispatch deleted is still reported.
    """
    roots = tuple(roots)
    found = {path for path in ignored if eligible(repo, path, present=True, roots=roots)}
    found |= {path for path in removed if eligible(repo, path, present=False, roots=roots)}
    return sorted(found)


def skipped(before: dict[str, Any], after: dict[str, Any]) -> set[str]:
    """Paths left out of the comparison, decided at compare time for both snapshots.

    The after-snapshot's list is authoritative: it was taken against the baseline, so it also
    covers a baseline written before this list existed (15.19.0) that still holds those
    files. A path the baseline marked as an artifact is also left out once it is gone.
    """
    now = set(after.get("test_artifacts") or [])
    gone = {path for path in before.get("test_artifacts") or [] if path not in (after.get("files") or {})}
    return now | gone
