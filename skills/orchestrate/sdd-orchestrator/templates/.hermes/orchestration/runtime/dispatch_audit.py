#!/usr/bin/env python3
"""Opt-in independent workspace audit of one writing dispatch (IMPLEMENT, TEST).

The controller otherwise trusts the ``modified_paths``/``created_paths`` a worker
declares. With ``"workspace_audit": "enforce"`` in the project-owned
``policies/EXECUTORS.md``, it instead compares the real repository before and after
the dispatch with what the result declares, for **every** executor (claude, codex,
opencode alike). The snapshot (``snapshot``, schema 3) records:

* every dirty, untracked **and ignored** file (``git status --porcelain -z
  --untracked-files=all --ignored``) with its content hash, size and executable bit;
* ``HEAD`` (``git rev-parse --verify HEAD``), a hash of every ref plus the symbolic
  ``HEAD`` (``git for-each-ref``, ``git symbolic-ref``) and a hash of the index entries
  with their flags (``git ls-files -s -v``; nothing is written to the object store);
* the hash of every ``.gitignore``, of ``.git/info/exclude`` and of the
  ``core.excludesFile`` setting (``ignore_rules``);
* one hash of the full diff: ``git diff --binary HEAD`` + ``--cached`` + unstaged;
* the absolute Git directory (``git_dir``), the hash of the effective configuration with
  its origins and includes (``config_sha256``) and of the hooks, both the effective
  ``core.hooksPath`` directory and ``<git-dir>/hooks`` (``hooks_sha256``).

Every ``git`` call is pinned with ``--git-dir``/``--work-tree`` to the Git directory
recorded at ``prepare`` and overrides the settings that change what Git reports (see
``_OVERRIDES``), so a worker that writes ``.git/config`` cannot redirect the audit.

Known limits (documented, not enforced): an embedded repository or submodule that already
existed is not inspected inside; ``_hash`` digests at most about 8 MiB of a file plus its
size; every ignored file is hashed with no overall time budget; and a crafted bytecode
cache whose header copies its source's mtime and size is left out (see ``audit_scope``).

A changed file that is not declared (``UNDECLARED_CHANGE``), a declared path outside the
slice's ``editable_paths`` (``OUTSIDE_EDITABLE_PATHS``), any change to an ignore rule
(``IGNORE_RULES_CHANGED``, even when declared) and a moved HEAD, ref, index or diff, Git directory, configuration or hooks
(``GIT_STATE_MOVED``) all stop the loop with ``WORKSPACE_AUDIT_FAILED``; the result is not
folded into STATE. Every ``git`` call is checked: a non-zero exit or a timeout raises
``AuditError``, which the controller reports as ``AUDIT_UNVERIFIABLE``, never as a pass.

The only paths left out are the controller's own files when they physically live in the
repository (``exclusions``: its orchestration folder, runtime, STATE, journal and
history) and an explicit, narrow list of git-ignored test-run artifacts (``audit_scope``:
the test runner's root cache, root coverage data and bytecode caches that match their
source), recorded as ``test_artifacts`` but filtered at compare time on both snapshots,
so a baseline written before that list existed is handled too; everything else, ignored
or not, counts. The baseline is taken once at
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

import audit_scope

MODES = ("off", "enforce")
DEFAULT_MODE = "off"
AUDITED_STAGES = ("IMPLEMENT", "TEST")
STOP_REASON = "WORKSPACE_AUDIT_FAILED"
SCHEMA_VERSION = 3
#: Why one path (or Git object) failed the audit.
FINDINGS = ("UNDECLARED_CHANGE", "OUTSIDE_EDITABLE_PATHS", "IGNORE_RULES_CHANGED", "GIT_STATE_MOVED", "AUDIT_UNVERIFIABLE")
GIT_STATE = (("head", "HEAD"), ("refs_sha256", "refs"), ("index_sha256", "index"), ("git_dir", "git_dir"),
             ("config_sha256", "config"), ("hooks_sha256", "hooks"))
INFO_EXCLUDE = ".git/info/exclude"
_MAX_BYTES = 8 * 1024 * 1024
_TIMEOUT = 120


class AuditError(RuntimeError):
    """A snapshot could not be taken or read; the audit must stop, not pass."""


def enabled(policy: dict[str, Any] | None, stage: str) -> bool:
    return bool(policy) and policy.get("workspace_audit") == "enforce" and stage in AUDITED_STAGES


#: Overridden on every call, because a worker that can write ``.git/config`` (or a file it
#: includes) could otherwise change what status/diff/ls-files report: point ``core.worktree``
#: at a pristine shadow copy, turn the repository bare, make status skip untracked files or
#: serve a stale untracked cache, or run a program through ``core.fsmonitor``. The location
#: itself is pinned with ``--git-dir``/``--work-tree`` (see ``_git``); ``diff`` also gets
#: ``--no-ext-diff --no-textconv`` (``diff.external`` cannot be emptied with ``-c``).
_OVERRIDES = ("core.worktree=", "core.bare=false", "core.fsmonitor=false", "core.untrackedCache=false",
              "status.showUntrackedFiles=all")
_DIFF = ("diff", "--binary", "--no-ext-diff", "--no-textconv")


def _git(repo: Path, *arguments: str, git_dir: str | None = None, allowed: tuple[int, ...] = (0,)) -> bytes:
    pinned = ["--git-dir", git_dir, "--work-tree", str(repo)] if git_dir else []
    argv = ["git", "-C", str(repo), *pinned, *(item for value in _OVERRIDES for item in ("-c", value)), *arguments]
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


def _status(repo: Path, git_dir: str) -> dict[str, str]:
    """Every listed path with its two-letter status (``!!`` is untracked and ignored)."""
    raw = _git(repo, "status", "--porcelain", "-z", "--untracked-files=all", "--ignored", git_dir=git_dir).decode("utf-8", "replace")
    files: dict[str, str] = {}
    second = False
    for entry in (item for item in raw.split("\0") if item):
        if second:
            files[entry] = "R2"  # the second half of a rename/copy entry is a real path too
            second = False
            continue
        status, path = entry[:2], entry[3:]
        files[path] = status
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


def _ignore_rules(repo: Path, paths: list[str], git_dir: str) -> dict[str, str]:
    tracked = [item for item in _git(repo, "ls-files", "-z", git_dir=git_dir).decode("utf-8", "replace").split("\0") if item]
    rules = {path: _hash(repo / path) for path in {*tracked, *paths} if path == ".gitignore" or path.endswith("/.gitignore")}
    exclude = Path(_git(repo, "rev-parse", "--git-path", "info/exclude", git_dir=git_dir).decode("utf-8", "replace").strip())
    rules[INFO_EXCLUDE] = _hash(exclude if exclude.is_absolute() else repo / exclude)
    rules["core.excludesFile"] = _sha(_git(repo, "config", "--get", "core.excludesFile", git_dir=git_dir, allowed=(0, 1)))
    return rules


def _tree(root: Path) -> bytes:
    """Every entry under ``root`` (not following links) with its content hash."""
    if not root.is_dir():
        return f"{root}={_hash(root)}".encode("utf-8", "replace")
    entries = [str(root)]
    for folder, folders, files in os.walk(root):
        folders.sort()
        for name in sorted(files + [item for item in folders if (Path(folder) / item).is_symlink()]):
            entries.append(f"{(Path(folder) / name).relative_to(root).as_posix()}={_hash(Path(folder) / name)}")
    return "\0".join(entries).encode("utf-8", "replace")


def _hooks(repo: Path, git_dir: str) -> str:
    """The effective hooks directory (``core.hooksPath`` included) and ``<git-dir>/hooks``, with their contents."""
    effective = Path(_git(repo, "rev-parse", "--git-path", "hooks", git_dir=git_dir).decode("utf-8", "replace").strip())
    effective = effective if effective.is_absolute() else repo / effective
    return _sha(_tree(effective) + b"\0\0" + _tree(Path(git_dir) / "hooks"))


def snapshot(repo: Path, exclude: list[str] | tuple[str, ...] = (), git_dir: str | None = None,
             baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    """The repository's observable state; raises ``AuditError`` when any ``git`` call fails.

    Every ``git`` call is pinned to ``git_dir`` (the one recorded at ``prepare``; resolved now
    when absent) and to ``repo`` as the work tree, so neither a ``core.worktree`` in
    ``.git/config`` nor a ``.git`` replaced by a gitfile can redirect them. The directory Git
    resolves now is recorded as ``git_dir``, so a replaced ``.git`` is reported as well.
    ``test_artifacts`` lists the ignored test-run artifacts present now and, against
    ``baseline`` (the before-snapshot, when this is the after one), those removed since
    (``audit_scope``); every path stays in ``files``, the filter runs at compare time.
    """
    repo = Path(repo)
    skip = tuple(exclude)
    resolved = str(Path(_git(repo, "rev-parse", "--absolute-git-dir").decode("utf-8", "replace").strip()).resolve())
    git_dir = git_dir or resolved
    config = _git(repo, "config", "--list", "-z", "--show-origin", "--includes", git_dir=git_dir)
    status = {path: code for path, code in _status(repo, git_dir).items() if not _excluded(path, skip)}
    paths = sorted(status)
    removed = [path for path in (baseline or {}).get("files") or {} if path not in status and not _excluded(path, skip)]
    artifacts = audit_scope.select(repo, (path for path in paths if status[path] == "!!"), removed)
    head = _git(repo, "rev-parse", "--verify", "--quiet", "HEAD", git_dir=git_dir, allowed=(0, 1)).decode("utf-8", "replace").strip() or "unborn"
    refs = _git(repo, "for-each-ref", "--format=%(refname) %(objectname)", git_dir=git_dir) + b"\0" \
        + _git(repo, "symbolic-ref", "-q", "HEAD", git_dir=git_dir, allowed=(0, 1))
    diff = (_git(repo, *_DIFF, "HEAD", git_dir=git_dir) if head != "unborn" else b"") + b"\0" \
        + _git(repo, *_DIFF, "--cached", git_dir=git_dir) + b"\0" + _git(repo, *_DIFF, git_dir=git_dir)
    return {"schema_version": SCHEMA_VERSION, "files": {path: _hash(repo / path) for path in paths}, "head": head,
            "refs_sha256": _sha(refs), "index_sha256": _sha(_git(repo, "ls-files", "-s", "-v", "-z", git_dir=git_dir)),
            "diff_sha256": _sha(diff), "ignore_rules": _ignore_rules(repo, paths, git_dir), "git_dir": resolved,
            "config_sha256": _sha(config), "hooks_sha256": _hooks(repo, git_dir), "excluded": list(skip),
            "test_artifacts": artifacts}


def load(path: Path) -> dict[str, Any]:
    """A stored snapshot, or ``AuditError`` when it is absent, unreadable or not a current-schema snapshot."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AuditError(f"the pre-dispatch snapshot {Path(path).name} is missing or unreadable") from exc
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION or not isinstance(value.get("files"), dict):
        raise AuditError(f"the pre-dispatch snapshot {Path(path).name} is not a schema-{SCHEMA_VERSION} snapshot")
    return value


def changed_paths(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Paths whose status or content differs between the two snapshots, test-run artifacts left out."""
    old, new, skip = before.get("files") or {}, after.get("files") or {}, audit_scope.skipped(before, after)
    return sorted({path for path in set(old) | set(new) if old.get(path) != new.get(path) and path not in skip})


def declared_paths(result: dict[str, Any]) -> set[str]:
    body = result.get("executor_result") or {}
    return {item.get("path") for key in ("modified_paths", "created_paths") for item in body.get(key) or [] if item.get("path")}


def _finding(path: str, finding: str, detail: str) -> dict[str, str]:
    return {"path": path, "finding": finding, "detail": detail}


def audit(*, before: dict[str, Any], after: dict[str, Any], result: dict[str, Any], editable_paths: list[str],
          path_matches: Any) -> list[dict[str, str]]:
    """Return one finding per path, ignore rule or Git object the result does not account for."""
    declared = declared_paths(result)
    findings = [_finding(label, "GIT_STATE_MOVED", f"the repository {label} changed during the dispatch"
                         " (a worker never commits, stages or edits the Git configuration or hooks)")
                for key, label in GIT_STATE if before.get(key) != after.get(key)]
    old_rules, new_rules, skip = before.get("ignore_rules") or {}, after.get("ignore_rules") or {}, audit_scope.skipped(before, after)
    rules = sorted(path for path in set(old_rules) | set(new_rules) if old_rules.get(path) != new_rules.get(path) and path not in skip)
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
