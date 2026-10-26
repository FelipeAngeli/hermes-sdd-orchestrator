#!/usr/bin/env python3
"""Opt-in independent workspace audit of one writing dispatch (IMPLEMENT, TEST).

The controller otherwise trusts the ``modified_paths``/``created_paths`` a worker
declares. With ``"workspace_audit": "enforce"`` in the project-owned
``policies/EXECUTORS.md``, it instead compares the real worktree before and after the
dispatch — ``git status --porcelain --untracked-files=all`` plus the content hash of
every dirty file and the staged/worktree diff hash — with what the result declares, for
**every** executor (claude, codex, opencode alike).

A file that changed but was not declared, or that lies outside the slice's
``editable_paths``, is a ``WORKSPACE_AUDIT_FAILED`` stop: the result is not folded into
STATE. The default (``off``, or an absent field) runs none of this and leaves every
executor's behaviour byte for byte as before.

Only repository paths are considered. Git-ignored paths (the controller's own
``.hermes/`` runtime under ``--local-storage``) never appear in the snapshot, and an
Obsidian container lives outside the repository, so controller files can never count as
an undeclared worker change. Standard library only; nothing here writes the repository.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

MODES = ("off", "enforce")
DEFAULT_MODE = "off"
AUDITED_STAGES = ("IMPLEMENT", "TEST")
STOP_REASON = "WORKSPACE_AUDIT_FAILED"
#: Why one path failed the audit.
FINDINGS = ("UNDECLARED_CHANGE", "OUTSIDE_EDITABLE_PATHS")
_MAX_BYTES = 8 * 1024 * 1024


def enabled(policy: dict[str, Any] | None, stage: str) -> bool:
    return bool(policy) and policy.get("workspace_audit") == "enforce" and stage in AUDITED_STAGES


def _git(repo: Path, *arguments: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *arguments], capture_output=True, timeout=120, check=False).stdout


def _dirty(repo: Path) -> list[str]:
    raw = _git(repo, "status", "--porcelain", "-z", "--untracked-files=all").decode("utf-8", "replace")
    files: list[str] = []
    skip = False
    for entry in (item for item in raw.split("\0") if item):
        if skip:
            files.append(entry)  # the second half of a rename/copy entry is a real path too
            skip = False
            continue
        status, path = entry[:2], entry[3:]
        files.append(path)
        skip = "R" in status or "C" in status
    return sorted(set(files))


def _hash(path: Path) -> str:
    try:
        if path.is_symlink():
            return "symlink:" + hashlib.sha256(str(path.readlink()).encode("utf-8")).hexdigest()
        if not path.is_file():
            return "absent"
        with path.open("rb") as handle:
            digest = hashlib.sha256()
            while chunk := handle.read(1 << 20):
                digest.update(chunk)
                if digest.block_size and handle.tell() > _MAX_BYTES:
                    break
            return digest.hexdigest()
    except OSError:
        return "unreadable"


def snapshot(repo: Path) -> dict[str, Any]:
    """The worktree's observable state: dirty paths with content hashes plus the diff hash."""
    files = {path: _hash(repo / path) for path in _dirty(repo)}
    diff = hashlib.sha256(_git(repo, "diff", "HEAD", "--binary") + _git(repo, "diff", "--binary")).hexdigest()
    return {"schema_version": 1, "files": files, "diff_sha256": diff}


def changed_paths(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Paths whose status or content differs between the two snapshots."""
    old, new = before.get("files") or {}, after.get("files") or {}
    return sorted({path for path in set(old) | set(new) if old.get(path) != new.get(path)})


def declared_paths(result: dict[str, Any]) -> set[str]:
    body = result.get("executor_result") or {}
    return {item.get("path") for key in ("modified_paths", "created_paths") for item in body.get(key) or [] if item.get("path")}


def audit(*, before: dict[str, Any], after: dict[str, Any], result: dict[str, Any], editable_paths: list[str],
          path_matches: Any) -> list[dict[str, str]]:
    """Return one finding per path that really changed but was not declared, or is out of scope."""
    declared = declared_paths(result)
    findings: list[dict[str, str]] = []
    for path in changed_paths(before, after):
        if path not in declared:
            findings.append({"path": path, "finding": "UNDECLARED_CHANGE",
                             "detail": "the file changed during the dispatch but the result does not declare it"})
        elif editable_paths and not any(path_matches(path, pattern) for pattern in editable_paths):
            findings.append({"path": path, "finding": "OUTSIDE_EDITABLE_PATHS",
                             "detail": "the file is declared but lies outside the slice editable_paths"})
    return findings


def stop_message(stage: str, findings: list[dict[str, str]]) -> str:
    listed = ", ".join(f"{item['path']} ({item['finding']})" for item in findings[:8])
    return (f"The workspace audit of this {stage} dispatch found {len(findings)} path(s) the result does not account for: {listed}."
            " The result was not folded into STATE.")
