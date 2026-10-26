#!/usr/bin/env python3
"""Opt-in independent workspace audit of one writing dispatch (IMPLEMENT, TEST).

The controller otherwise trusts the ``modified_paths``/``created_paths`` a worker
declares. With ``"workspace_audit": "enforce"`` in the project-owned
``policies/EXECUTORS.md``, it instead compares the real repository before and after
the dispatch with what the result declares, for **every** executor (claude, codex,
opencode alike). The snapshot (``snapshot``, schema 2) records:

* every dirty, untracked **and ignored** file (``git status --porcelain -z
  --untracked-files=all --ignored``) with its content hash, size and executable bit;
* ``HEAD`` (``git rev-parse --verify HEAD``), a hash of every ref plus the symbolic
  ``HEAD`` (``git for-each-ref``, ``git symbolic-ref``) and a hash of the index entries
  with their flags (``git ls-files -s -v``; nothing is written to the object store);
* the hash of every ``.gitignore``, of ``.git/info/exclude`` and of the
  ``core.excludesFile`` setting (``ignore_rules``);
* one hash of the full diff: ``git diff --binary HEAD`` + ``--cached`` + unstaged.

A changed file that is not declared (``UNDECLARED_CHANGE``), a declared path outside the
slice's ``editable_paths`` (``OUTSIDE_EDITABLE_PATHS``), any change to an ignore rule
(``IGNORE_RULES_CHANGED``, even when declared) and a moved HEAD, ref, index or diff
(``GIT_STATE_MOVED``) all stop the loop with ``WORKSPACE_AUDIT_FAILED``; the result is not
folded into STATE. Every ``git`` call is checked: a non-zero exit or a timeout raises
``AuditError``, which the controller reports as ``AUDIT_UNVERIFIABLE``, never as a pass.

The only paths left out are the controller's own files when they physically live in the
repository (``exclusions``: its orchestration folder, runtime, STATE, journal and
history); everything else, ignored or not, counts. The baseline is taken once at
``prepare`` and carried across the corrective retries of the same action
(``prepare_record``), so a change left by a failed attempt is still reported. The default
(``off``, or an absent field) runs none of this. Standard library only; nothing here
writes the repository.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

MODES = ("off", "enforce")
DEFAULT_MODE = "off"
AUDITED_STAGES = ("IMPLEMENT", "TEST")
STOP_REASON = "WORKSPACE_AUDIT_FAILED"
SCHEMA_VERSION = 2
#: Why one path (or Git object) failed the audit.
FINDINGS = ("UNDECLARED_CHANGE", "OUTSIDE_EDITABLE_PATHS", "IGNORE_RULES_CHANGED", "GIT_STATE_MOVED", "AUDIT_UNVERIFIABLE")
GIT_STATE = (("head", "HEAD"), ("refs_sha256", "refs"), ("index_sha256", "index"))
INFO_EXCLUDE = ".git/info/exclude"
_MAX_BYTES = 8 * 1024 * 1024
_TIMEOUT = 120


class AuditError(RuntimeError):
    """A snapshot could not be taken or read; the audit must stop, not pass."""


def enabled(policy: dict[str, Any] | None, stage: str) -> bool:
    return bool(policy) and policy.get("workspace_audit") == "enforce" and stage in AUDITED_STAGES


def _git(repo: Path, *arguments: str, allowed: tuple[int, ...] = (0,)) -> bytes:
    argv = ["git", "-C", str(repo), "-c", "core.fsmonitor=false", *arguments]
    try:
        completed = subprocess.run(argv, capture_output=True, timeout=_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise AuditError(f"git {arguments[0]} could not run ({exc.__class__.__name__})") from exc
    if completed.returncode not in allowed:
        detail = (completed.stderr or b"").decode("utf-8", "replace").strip()[:200]
        raise AuditError(f"git {' '.join(arguments[:2])} exited {completed.returncode}: {detail}")
    return completed.stdout


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _excluded(path: str, exclude: tuple[str, ...]) -> bool:
    return any(path == item or path.startswith(item.rstrip("/") + "/") for item in exclude)


def exclusions(repo: Path, paths: list[Path]) -> list[str]:
    """Repository-relative paths of the controller's own files that physically live inside ``repo``."""
    root = Path(repo).resolve()
    found: list[str] = []
    for path in paths:
        try:
            relative = Path(path).resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        if relative not in {"", "."} and relative not in found:
            found.append(relative)
    return found


def _status(repo: Path) -> list[str]:
    raw = _git(repo, "status", "--porcelain", "-z", "--untracked-files=all", "--ignored").decode("utf-8", "replace")
    files: list[str] = []
    second = False
    for entry in (item for item in raw.split("\0") if item):
        if second:
            files.append(entry)  # the second half of a rename/copy entry is a real path too
            second = False
            continue
        status, path = entry[:2], entry[3:]
        files.append(path)
        second = "R" in status or "C" in status
    return files


def _hash(path: Path) -> str:
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            return "symlink:" + _sha(os.readlink(path).encode("utf-8"))
        if stat.S_ISDIR(info.st_mode):
            return "directory"
        if not stat.S_ISREG(info.st_mode):
            return "special"
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1 << 20):
                digest.update(chunk)
                if handle.tell() > _MAX_BYTES:
                    break
        return f"{digest.hexdigest()}:{info.st_size}:{'x' if info.st_mode & 0o111 else '-'}"
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "unreadable"


def _ignore_rules(repo: Path, paths: list[str]) -> dict[str, str]:
    tracked = [item for item in _git(repo, "ls-files", "-z").decode("utf-8", "replace").split("\0") if item]
    rules = {path: _hash(repo / path) for path in {*tracked, *paths} if path == ".gitignore" or path.endswith("/.gitignore")}
    exclude = Path(_git(repo, "rev-parse", "--git-path", "info/exclude").decode("utf-8", "replace").strip())
    rules[INFO_EXCLUDE] = _hash(exclude if exclude.is_absolute() else repo / exclude)
    rules["core.excludesFile"] = _sha(_git(repo, "config", "--get", "core.excludesFile", allowed=(0, 1)))
    return rules


def snapshot(repo: Path, exclude: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    """The repository's observable state; raises ``AuditError`` when any ``git`` call fails."""
    repo = Path(repo)
    skip = tuple(exclude)
    paths = sorted({path for path in _status(repo) if not _excluded(path, skip)})
    head = _git(repo, "rev-parse", "--verify", "--quiet", "HEAD", allowed=(0, 1)).decode("utf-8", "replace").strip() or "unborn"
    refs = _git(repo, "for-each-ref", "--format=%(refname) %(objectname)") + b"\0" + _git(repo, "symbolic-ref", "-q", "HEAD", allowed=(0, 1))
    diff = (_git(repo, "diff", "--binary", "HEAD") if head != "unborn" else b"") + b"\0" + _git(repo, "diff", "--binary", "--cached") \
        + b"\0" + _git(repo, "diff", "--binary")
    return {"schema_version": SCHEMA_VERSION, "files": {path: _hash(repo / path) for path in paths}, "head": head,
            "refs_sha256": _sha(refs), "index_sha256": _sha(_git(repo, "ls-files", "-s", "-v", "-z")), "diff_sha256": _sha(diff),
            "ignore_rules": _ignore_rules(repo, paths), "excluded": list(skip)}


def load(path: Path) -> dict[str, Any]:
    """A stored snapshot, or ``AuditError`` when it is absent, unreadable or not a schema-2 snapshot."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AuditError(f"the pre-dispatch snapshot {Path(path).name} is missing or unreadable") from exc
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION or not isinstance(value.get("files"), dict):
        raise AuditError(f"the pre-dispatch snapshot {Path(path).name} is not a schema-{SCHEMA_VERSION} snapshot")
    return value


def changed_paths(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Paths whose status or content differs between the two snapshots."""
    old, new = before.get("files") or {}, after.get("files") or {}
    return sorted({path for path in set(old) | set(new) if old.get(path) != new.get(path)})


def declared_paths(result: dict[str, Any]) -> set[str]:
    body = result.get("executor_result") or {}
    return {item.get("path") for key in ("modified_paths", "created_paths") for item in body.get(key) or [] if item.get("path")}


def _finding(path: str, finding: str, detail: str) -> dict[str, str]:
    return {"path": path, "finding": finding, "detail": detail}


def audit(*, before: dict[str, Any], after: dict[str, Any], result: dict[str, Any], editable_paths: list[str],
          path_matches: Any) -> list[dict[str, str]]:
    """Return one finding per path, ignore rule or Git object the result does not account for."""
    declared = declared_paths(result)
    findings = [_finding(label, "GIT_STATE_MOVED", f"the repository {label} changed during the dispatch (a worker never commits or stages)")
                for key, label in GIT_STATE if before.get(key) != after.get(key)]
    old_rules, new_rules = before.get("ignore_rules") or {}, after.get("ignore_rules") or {}
    rules = sorted(path for path in set(old_rules) | set(new_rules) if old_rules.get(path) != new_rules.get(path))
    findings += [_finding(path, "IGNORE_RULES_CHANGED", "an ignore rule changed during the dispatch; it could hide files from Git")
                 for path in rules]
    changed = [path for path in changed_paths(before, after) if path not in rules]
    for path in changed:
        if path not in declared:
            findings.append(_finding(path, "UNDECLARED_CHANGE", "the file changed during the dispatch but the result does not declare it"))
        elif editable_paths and not any(path_matches(path, pattern) for pattern in editable_paths):
            findings.append(_finding(path, "OUTSIDE_EDITABLE_PATHS", "the file is declared but lies outside the slice editable_paths"))
    if not findings and not changed and before.get("diff_sha256") != after.get("diff_sha256"):
        findings.append(_finding("diff", "GIT_STATE_MOVED", "the HEAD/staged/unstaged diff changed although no listed file did"))
    return findings


def unverifiable(detail: str) -> list[dict[str, str]]:
    return [_finding("workspace", "AUDIT_UNVERIFIABLE", f"the audit could not be verified, so it fails closed: {detail}")]


def baseline_path(run_dir: Path, action_id: str) -> Path:
    return Path(run_dir) / f"{action_id}.workspace-before.json"


def prepare_record(*, repo: Path, run_dir: Path, action_id: str, key: str, retry: bool, previous: dict[str, Any] | None,
                   exclude: list[str]) -> dict[str, str]:
    """Write (or carry) the baseline of one action; return the record kept in ``delivery.workspace_audit``.

    A corrective retry (``retry``) of the same action series (``key``) keeps the series'
    first baseline, so whatever a failed attempt left behind is still compared. Raises
    ``AuditError`` when the snapshot cannot be taken or the carried baseline cannot be read.
    """
    previous = previous or {}
    baseline = previous.get("baseline_action_id") if retry and previous.get("key") == key else None
    if baseline:
        load(baseline_path(run_dir, baseline))
    else:
        baseline = action_id
        target = baseline_path(run_dir, action_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(snapshot(repo, exclude), sort_keys=True, indent=1) + "\n", encoding="utf-8")
    return {"action_id": action_id, "key": key, "mode": "enforce", "baseline_action_id": baseline}


def stop_message(stage: str, findings: list[dict[str, str]]) -> str:
    listed = ", ".join(f"{item['path']} ({item['finding']})" for item in findings[:8])
    return (f"The workspace audit of this {stage} dispatch found {len(findings)} path(s) the result does not account for: {listed}."
            " The result was not folded into STATE.")
