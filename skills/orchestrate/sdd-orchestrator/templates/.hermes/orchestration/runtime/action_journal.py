#!/usr/bin/env python3
"""Atomic write-ahead journal for executor action recovery; standard library only."""
from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import json
import os
import secrets
import shlex
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    import obsidian_binding
except ImportError:  # loaded by file path (installer) or a partial copy: legacy in-worktree paths only
    obsidian_binding = None  # type: ignore[assignment]

JOURNAL_VERSION = 1
SCRIPT = Path(os.path.abspath(__file__))
HISTORY_DIRECTORY_NAME = "action-journal-history"
RETRY_MODES = ("FULL_REPLACEMENT", "METADATA_OVERLAY", "ADOPT_PARENT_ARTIFACT")
RECOVERY_DECISIONS = (
    "DISPATCH_ALLOWED", "RECONCILE_ARTIFACT", "STATE_COMMIT_REQUIRED", "ALREADY_COMMITTED",
    "CORRECTIVE_RETRY_AVAILABLE", "WAIT_OR_MANUAL_REVIEW", "ARCHIVE_INTERRUPTED_REQUIRED", "BLOCKED", "RELEASED",
)
STATUSES = {"IDLE", "PREPARED", "DISPATCHED", "PROCESS_FINISHED", "ARTIFACT_READY", "VALIDATED", "STATE_COMMITTED", "RELEASED", "BLOCKED", "INTERRUPTED"}
TRANSITIONS = {
    "IDLE": {"PREPARED"}, "PREPARED": {"DISPATCHED", "BLOCKED"},
    "DISPATCHED": {"PROCESS_FINISHED", "BLOCKED"}, "PROCESS_FINISHED": {"ARTIFACT_READY", "BLOCKED", "INTERRUPTED"},
    "ARTIFACT_READY": {"VALIDATED", "BLOCKED"}, "VALIDATED": {"STATE_COMMITTED", "BLOCKED"},
    "STATE_COMMITTED": {"RELEASED", "BLOCKED"}, "RELEASED": set(), "BLOCKED": set(), "INTERRUPTED": set(),
}

# Every error code maps to an actionable next step; commands are rendered by ``error_command``.
NEXT_STEPS: dict[str, str] = {
    "JOURNAL_INVALID": "The journal file is missing, unreadable or does not match ACTION_JOURNAL_SCHEMA.json; never hand-edit it. Restore it from the last good copy or stop and ask a human.",
    "JOURNAL_REQUIRED": "Pass --journal with the canonical journal path; `paths` prints it for this worktree in either storage mode.",
    "JOURNAL_EXISTS": "init never overwrites a journal; run recover on the existing one.",
    "JOURNAL_WRITE_FAILED": "The previous journal is intact; fix the filesystem problem (space, permissions) and repeat the same command.",
    "PAYLOAD_REQUIRED": "Repeat the command with the missing option named in the message.",
    "PAYLOAD_INVALID": "Repeat the command with valid JSON or a single valid option combination.",
    "INVALID_TRANSITION": "The command does not apply to the current status; run recover and follow its next_command.",
    "ACTION_RECOVERY_REQUIRED": "An earlier action is still open; run recover and follow its next_command before preparing another action.",
    "ARTIFACT_PENDING": "A final-message file already exists for this action; run recover and reconcile it before any dispatch.",
    "DISPATCH_NOT_PREPARED": "Dispatch is recorded only once, from PREPARED; run recover and follow its next_command instead of launching the executor.",
    "PROMPT_HASH_MISMATCH": "The prompt about to be sent is not the one prepared; send exactly the prepared prompt, or block and archive this action and prepare a new one for the new prompt.",
    "ADOPTION_DISPATCH_FORBIDDEN": "An ADOPT_PARENT_ARTIFACT action never dispatches an executor; run record-artifact to adopt the parent's file.",
    "PROCESS_NOT_STARTED": "No dispatch was recorded, so no process result can be recorded; the action is still PREPARED (or idle). Run recover.",
    "PROCESS_RESULT_CONFLICT": "A different process result is already recorded; it is kept. Inspect the journal and continue from recover.",
    "ARTIFACT_MISSING": "No regular final-message file exists (missing, directory or symlink); the journal stays PROCESS_FINISHED. Archive the action as interrupted, then prepare a retry.",
    "ARTIFACT_CHANGED": "The final-message file changed after it was recorded; its evidence is no longer trustworthy. Block the action and archive it, then prepare a new one.",
    "ARTIFACT_CLASSIFIED_INVALID": "The artifact is already classified INVALID; follow recover (archive-invalid and a corrective retry) instead of marking it valid.",
    "ADOPTION_NOT_ALLOWED": "Adoption needs an INTERRUPTED parent of the same ticket and stage whose own final-message path is adopted; otherwise prepare a FULL_REPLACEMENT retry.",
    "ADOPTION_PARENT_NOT_FOUND": "The parent is not in this worktree's history; archive it first (archive-interrupted) or check parent_action_id and --history-dir (see `paths`).",
    "ADOPTION_ARTIFACT_MISMATCH": "The parent's file is missing, not a regular file, or its SHA-256 differs from parent_artifact_sha256; recompute the hash of the exact file or prepare a FULL_REPLACEMENT retry.",
    "ARCHIVE_REASON_REQUIRED": "archive-blocked records why the action is abandoned; repeat it with --reason.",
    "JOURNAL_ARCHIVE_BLOCKED_NOT_ALLOWED": "archive-blocked accepts only BLOCKED, a stale INTERRUPTED or a dirty IDLE journal; run recover for the live action's next_command.",
    "JOURNAL_ARCHIVE_INTERRUPTED_NOT_ALLOWED": "archive-interrupted needs PROCESS_FINISHED without an artifact; run recover and follow its next_command.",
    "JOURNAL_ARCHIVE_INTERRUPTED_INCOMPLETE": "The journal lacks an action id or ticket; block it and use archive-blocked.",
    "JOURNAL_ARCHIVE_INTERRUPTED_INVALID": "The archive was written but the new journal is not pristine; stop and ask a human to inspect it.",
    "JOURNAL_PROCESS_RESULT_REQUIRED": "Record the process result first with record-process --finished --exit-code <exit-code>.",
    "JOURNAL_ARCHIVE_INVALID_NOT_ALLOWED": "archive-invalid needs a present artifact classified INVALID; run recover and follow its next_command.",
    "JOURNAL_ARCHIVE_INVALID_INVALID": "The archive was written but the new journal is not pristine; stop and ask a human to inspect it.",
    "JOURNAL_HISTORY_CONFLICT": "History already holds different content for this action; the live journal is unchanged. Do not delete history; stop and ask a human to compare both files.",
    "HISTORY_PATH_UNSAFE": "Use the canonical history directory printed by `paths` (a real directory, no symlink or `..`, inside this worktree or its bound vault runtime).",
    "RUNTIME_PATH_UNSAFE": "The bound vault runtime path contains a symlink or traversal; replace the link with a real directory and retry.",
    "RUNTIME_BINDING_UNSAFE": "The controller's .hermes/obsidian.json is invalid; repair the binding (obsidian_binding.py) before using the journal.",
    "STATE_PATH_REQUIRED": "Run prepare-state-commit with the canonical STATE path printed by `paths`.",
    "STATE_PATH_UNSAFE": "Use the canonical STATE path printed by `paths`: this worktree's bound runtime STATE.md in Obsidian mode, or an in-worktree non-symlink path in local mode.",
    "STATE_COMMIT_FILE_MISSING": "STATE.md is missing or not a regular file; restore it before committing.",
    "STATE_COMMIT_HASH_MISMATCH": "STATE does not hash to the expected value; run recover to decide between commit, reconcile and STATE_DESYNC.",
    "STATE_COMMIT_NOT_PREPARED": "Run prepare-state-commit before writing STATE.",
    "STATE_NOT_COMMITTED": "STATE still has its old content; write the prepared snapshot atomically, then repeat mark-state-committed.",
    "STATE_COMMIT_UNVERIFIED": "Release needs a verified STATE commit; run recover and follow its next_command.",
    "JOURNAL_ROLLOVER_NOT_ALLOWED": "Rollover applies only to RELEASED; run recover and follow its next_command.",
    "JOURNAL_ROLLOVER_UNVERIFIED": "The STATE commit is not verified; run recover.",
    "JOURNAL_ROLLOVER_INCOMPLETE": "The released action lacks required evidence; block it and use archive-blocked.",
    "JOURNAL_ROLLOVER_INVALID": "The archive was written but the new journal is not pristine; stop and ask a human to inspect it.",
    "JOURNAL_ARCHIVE_BLOCKED_INVALID": "The archive was written but the new journal is not pristine; stop and ask a human to inspect it.",
    "INVALID_ARTIFACT_CLASSIFICATION_NOT_ALLOWED": "Classification needs an unchanged, uncommitted ARTIFACT_READY artifact and at least one --invalid-field; run recover.",
    "INVALID_ARTIFACT_RECOVERY_NOT_ALLOWED": "recover-blocked-invalid applies only to BLOCKED; run recover.",
    "PARENT_EVIDENCE_MISMATCH": "Parent evidence cannot be reused; prepare a FULL_REPLACEMENT retry.",
    "METADATA_OVERLAY_INVALID": "Correct only fields listed in allowed_corrections, or prepare a FULL_REPLACEMENT retry.",
    "WORKSPACE_REQUIRED": "Run `paths` from inside the repository worktree or pass --repo <worktree>.",
    "CONTROLLER_WORKSPACE_MISMATCH": "This repository-local controller belongs to another worktree; run the controller installed in this worktree.",
}


class JournalError(Exception):
    def __init__(self, code: str, message: str, *, next_step: str | None = None, next_command: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.next_step = next_step or NEXT_STEPS.get(code, "Run recover and follow its next_command.")
        self.next_command = next_command


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise JournalError("JOURNAL_INVALID", f"{label} must contain exactly {sorted(expected)}")


def validate_journal(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise JournalError("JOURNAL_INVALID", "Journal root must be an object.")
    _keys(value, {"journal_version", "workspace", "action", "fingerprints", "process", "artifact", "state_commit", "incidents"}, "root")
    if value["journal_version"] != JOURNAL_VERSION:
        raise JournalError("JOURNAL_INVALID", "Unsupported journal version.")
    _keys(value["workspace"], {"path", "branch", "head", "git_common_dir"}, "workspace")
    _keys(value["action"], {"id", "ticket", "stage", "name", "status", "executor", "schema_path", "protocol_version", "prompt_hash", "final_message_path", "attempt", "retry_mode", "parent_action_id", "parent_artifact_path", "parent_artifact_sha256", "invalid_fields", "allowed_corrections"}, "action")
    _keys(value["fingerprints"], {"baseline", "ownership", "state_before"}, "fingerprints")
    _keys(value["process"], {"started_at", "finished_at", "exit_code"}, "process")
    _keys(value["artifact"], {"exists", "sha256", "validation_status"}, "artifact")
    _keys(value["state_commit"], {"state_path", "expected_before_hash", "expected_after_hash", "committed_after_hash", "committed_at", "verified"}, "state_commit")
    if value["action"]["status"] not in STATUSES:
        raise JournalError("JOURNAL_INVALID", "Unknown action status.")
    if not isinstance(value["incidents"], list) or not all(isinstance(item, str) for item in value["incidents"]):
        raise JournalError("JOURNAL_INVALID", "incidents must be a string array.")
    if not isinstance(value["artifact"]["exists"], bool) or not isinstance(value["state_commit"]["verified"], bool):
        raise JournalError("JOURNAL_INVALID", "Boolean fields must be booleans.")
    for key in ("state_path", "expected_before_hash", "expected_after_hash", "committed_after_hash", "committed_at"):
        if value["state_commit"][key] is not None and not isinstance(value["state_commit"][key], str):
            raise JournalError("JOURNAL_INVALID", f"state_commit.{key} must be string or null.")
    if value["action"]["retry_mode"] is not None and value["action"]["retry_mode"] not in RETRY_MODES:
        raise JournalError("JOURNAL_INVALID", f"action.retry_mode must be one of {list(RETRY_MODES)} or null.")
    for key in ("invalid_fields", "allowed_corrections"):
        if not isinstance(value["action"][key], list) or not all(isinstance(item, str) for item in value["action"][key]):
            raise JournalError("JOURNAL_INVALID", f"action.{key} must be a string array.")
    return value


def load_journal(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise JournalError("JOURNAL_INVALID", f"Cannot read valid journal: {exc}") from exc
    return validate_journal(value)


def atomic_write(path: Path, value: dict[str, Any]) -> None:
    validate_journal(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = None
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            json.dump(value, handle, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        try:
            directory_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    except OSError as exc:
        raise JournalError("JOURNAL_WRITE_FAILED", f"Atomic journal write failed: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def canonical_json(value: dict[str, Any]) -> bytes:
    validate_journal(value)
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


_history_before_final_write_hook: Callable[[Path], None] | None = None


def _history_directory_flags() -> int:
    """Return the flags required for trusted directory traversal on supported hosts."""
    return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


def _open_or_create_history_directory(parent_fd: int, component: str) -> tuple[int, os.stat_result]:
    """Create one history component through a trusted parent, then reopen it safely."""
    try:
        descriptor = os.open(component, _history_directory_flags(), dir_fd=parent_fd)
    except FileNotFoundError:
        try:
            os.mkdir(component, dir_fd=parent_fd)
        except FileExistsError:
            pass
        descriptor = os.open(component, _history_directory_flags(), dir_fd=parent_fd)
    info = os.fstat(descriptor)
    if not stat.S_ISDIR(info.st_mode):
        os.close(descriptor)
        raise OSError("history component is not a directory")
    return descriptor, info


def _verify_history_directory(parent_fd: int, component: str, expected: os.stat_result) -> None:
    """Detect replacement after opening without using a pathname for persistence."""
    current = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
    if (
        not stat.S_ISDIR(current.st_mode)
        or current.st_dev != expected.st_dev
        or current.st_ino != expected.st_ino
    ):
        raise OSError("history component changed after validation")


def _open_trusted_absolute_directory(path: Path) -> tuple[list[int], list[tuple[int, str, os.stat_result]]]:
    """Open an absolute directory one component at a time without following links."""
    if not path.is_absolute():
        raise OSError("history root is not absolute")
    descriptors = [os.open(path.anchor, _history_directory_flags())]
    components: list[tuple[int, str, os.stat_result]] = []
    try:
        parent_fd = descriptors[-1]
        for component in path.parts[1:]:
            descriptor = os.open(component, _history_directory_flags(), dir_fd=parent_fd)
            info = os.fstat(descriptor)
            if not stat.S_ISDIR(info.st_mode):
                os.close(descriptor)
                raise OSError("history root component is not a directory")
            components.append((parent_fd, component, info))
            descriptors.append(descriptor)
            parent_fd = descriptor
        return descriptors, components
    except Exception:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise


def _path_without_symlink_or_traversal(path: Path) -> Path:
    """Normalize an absolute path without ever resolving a symlink.

    The vault runtime is a separate trust boundary: resolving a link and then
    comparing would authorize a location the binding never named, so every
    existing component is checked with ``lstat`` instead.
    """
    if not path.is_absolute() or ".." in path.parts:
        raise JournalError("RUNTIME_PATH_UNSAFE", "Runtime path must be absolute and contain no traversal.")
    normalized = Path(os.path.abspath(str(path)))
    current = Path(normalized.anchor)
    for component in normalized.parts[1:]:
        current /= component
        try:
            if stat.S_ISLNK(os.lstat(current).st_mode):
                raise JournalError("RUNTIME_PATH_UNSAFE", "Runtime path must not traverse a symlink.")
        except FileNotFoundError:
            break  # descendants of a missing component cannot exist
    return normalized


def _installed_binding() -> Any:
    """Load the binding installed with this controller, never the environment override.

    The binding comes only from ``obsidian_binding._installed_container_binding``
    (the controller's own container or install root, never a ``--repo``
    argument), and ``HERMES_OBSIDIAN_VAULT`` is ignored: an override could
    otherwise redirect history or STATE writes to an arbitrary vault.
    """
    if obsidian_binding is None:
        return None
    source = obsidian_binding._installed_container_binding()
    if source is None:
        return None
    try:
        binding = obsidian_binding.load_path(source)
        raw_vault = binding.raw.get("vault_path")
        if not isinstance(raw_vault, str) or not os.path.isabs(raw_vault):
            raise obsidian_binding.BindingError("BINDING_INVALID", "vault_path must be an absolute path.")
        return dataclasses.replace(binding, vault_path=Path(raw_vault))
    except obsidian_binding.BindingError as exc:
        raise JournalError("RUNTIME_BINDING_UNSAFE", f"Installed Obsidian binding is invalid: {exc.code}.") from exc


def _external_runtime_roots(workspace: Path) -> tuple[Path, Path] | None:
    """This worktree's runtime directory in the installed binding's vault, or None without a binding.

    Returns ``(trusted, lexical)``: ``trusted`` has the vault root resolved
    once (the binding chose it, e.g. macOS ``/tmp`` -> ``/private/tmp``) and
    no symlink or traversal anywhere below the vault; ``lexical`` keeps the
    binding's own spelling so callers may pass either.
    """
    binding = _installed_binding()
    if binding is None:
        return None
    lexical = Path(os.path.abspath(str(obsidian_binding.runtime_dir(binding, workspace))))
    try:
        vault = Path(os.path.realpath(str(binding.vault_path)))
    except OSError as exc:
        raise JournalError("RUNTIME_PATH_UNSAFE", "Cannot resolve the bound vault.") from exc
    trusted = _path_without_symlink_or_traversal(vault / lexical.relative_to(Path(os.path.abspath(str(binding.vault_path)))))
    return trusted, lexical


def _external_runtime_root(workspace: Path) -> Path | None:
    roots = _external_runtime_roots(workspace)
    return None if roots is None else roots[0]


def _rebase_on_runtime(path: Path, roots: tuple[Path, Path]) -> Path | None:
    """``path`` relative to the trusted runtime, accepting either spelling; None when outside it."""
    trusted, lexical = roots
    for root in (trusted, lexical):
        try:
            return path.relative_to(root)
        except ValueError:
            continue
    return None


def _history_anchor(workspace_path: str, history_dir: Path) -> tuple[Path, Path, Path]:
    """Return the trusted descriptor root, the relative history path and the display root.

    Legacy history inside the worktree is opened from the resolved worktree
    root while its child components stay lexical, so an in-worktree symlink is
    still rejected by the descriptor walk (resolving first would follow it).
    The lexical display root keeps a caller's macOS ``/var`` spelling. History
    outside the worktree is accepted only below this worktree's runtime
    directory in the controller's own vault binding.
    """
    workspace = Path(workspace_path)
    if not workspace.is_absolute():
        raise JournalError("HISTORY_PATH_UNSAFE", "Workspace path must be absolute.")
    history_root = history_dir if history_dir.is_absolute() else workspace / history_dir
    if ".." in history_root.parts:
        raise JournalError("HISTORY_PATH_UNSAFE", "History path must not contain traversal.")
    history_lexical = Path(os.path.abspath(str(history_root)))
    workspace_lexical = Path(os.path.abspath(str(workspace)))
    try:
        relative = history_lexical.relative_to(workspace_lexical)
    except ValueError:
        pass
    else:
        try:
            trusted_workspace = workspace_lexical.resolve(strict=True)
        except OSError as exc:
            raise JournalError("HISTORY_PATH_UNSAFE", "Cannot resolve journal workspace.") from exc
        return trusted_workspace, relative, workspace_lexical
    try:
        roots = _external_runtime_roots(workspace_lexical)
    except JournalError as exc:
        raise JournalError("HISTORY_PATH_UNSAFE", str(exc)) from exc
    if roots is None:
        raise JournalError("HISTORY_PATH_UNSAFE", "History path is outside the journal workspace and no vault runtime is bound.")
    relative = _rebase_on_runtime(history_lexical, roots)
    if relative is None:
        raise JournalError("HISTORY_PATH_UNSAFE", "History path is outside this worktree's bound vault runtime.")
    display = roots[0] if history_lexical.is_relative_to(roots[0]) else roots[1]
    return roots[0], relative, display


def _sha256_descriptor(descriptor: int) -> str:
    digest = hashlib.sha256()
    with os.fdopen(os.dup(descriptor), "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_create_history(workspace_path: str, history_dir: Path, ticket: str, action_id: str, content: bytes) -> tuple[Path, str]:
    """Persist immutable history through descriptors anchored at the worktree or its bound vault runtime."""
    if Path(ticket).name != ticket or Path(action_id).name != action_id:
        raise JournalError("HISTORY_PATH_UNSAFE", "Ticket and action id must be single path components.")
    anchor, relative_history, display_root = _history_anchor(workspace_path, history_dir)
    if not relative_history.parts or any(part in {"", ".", ".."} for part in relative_history.parts):
        raise JournalError("HISTORY_PATH_UNSAFE", "History path must name a directory below the workspace.")

    descriptors: list[int] = []
    components: list[tuple[int, str, os.stat_result]] = []
    temporary: str | None = None
    file_descriptor: int | None = None
    final_name = f"{action_id}.json"
    display_path = display_root.joinpath(*relative_history.parts, ticket, final_name)
    try:
        # Every component from "/" down is opened without following links and
        # re-verified before persistence, so no ancestor can be swapped.
        descriptors, components = _open_trusted_absolute_directory(anchor)
        parent_fd = descriptors[-1]
        for component in (*relative_history.parts, ticket):
            child_fd, info = _open_or_create_history_directory(parent_fd, component)
            components.append((parent_fd, component, info))
            descriptors.append(child_fd)
            parent_fd = child_fd

        hook = _history_before_final_write_hook
        if hook is not None:
            hook(display_path.parent)
        for component_parent_fd, component, info in components:
            _verify_history_directory(component_parent_fd, component, info)

        temporary = f".{final_name}.{os.getpid()}.{secrets.token_hex(16)}.tmp"
        file_descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_fd,
        )
        with os.fdopen(file_descriptor, "wb") as handle:
            file_descriptor = None
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, final_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd, follow_symlinks=False)
        except FileExistsError:
            existing_fd = os.open(final_name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd)
            try:
                if not stat.S_ISREG(os.fstat(existing_fd).st_mode) or _sha256_descriptor(existing_fd) != hashlib.sha256(content).hexdigest():
                    raise JournalError("JOURNAL_HISTORY_CONFLICT", f"History already exists with different content: {display_path}")
            finally:
                os.close(existing_fd)
        else:
            os.fsync(parent_fd)
        for component_parent_fd, component, info in components:
            _verify_history_directory(component_parent_fd, component, info)
        return display_path, hashlib.sha256(content).hexdigest()
    except JournalError:
        raise
    except OSError as exc:
        raise JournalError("HISTORY_PATH_UNSAFE", f"Unsafe history path or race detected: {exc}") from exc
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        if temporary and descriptors:
            try:
                os.unlink(temporary, dir_fd=descriptors[-1])
            except OSError:
                pass
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def empty_journal(workspace: dict[str, str]) -> dict[str, Any]:
    return {"journal_version": 1, "workspace": workspace, "action": {"id": None, "ticket": None, "stage": None, "name": None, "status": "IDLE", "executor": None, "schema_path": None, "protocol_version": None, "prompt_hash": None, "final_message_path": None, "attempt": 0, "retry_mode": None, "parent_action_id": None, "parent_artifact_path": None, "parent_artifact_sha256": None, "invalid_fields": [], "allowed_corrections": []}, "fingerprints": {"baseline": None, "ownership": None, "state_before": None}, "process": {"started_at": None, "finished_at": None, "exit_code": None}, "artifact": {"exists": False, "sha256": None, "validation_status": "PENDING"}, "state_commit": {"state_path": None, "expected_before_hash": None, "expected_after_hash": None, "committed_after_hash": None, "committed_at": None, "verified": False}, "incidents": []}


def transition(value: dict[str, Any], target: str) -> dict[str, Any]:
    validate_journal(value)
    current = value["action"]["status"]
    if target not in TRANSITIONS[current]:
        raise JournalError("INVALID_TRANSITION", f"{current} cannot transition to {target}.")
    changed = copy.deepcopy(value)
    changed["action"]["status"] = target
    return changed


def artifact_present(value: dict[str, Any]) -> bool:
    location = value["action"]["final_message_path"]
    return bool(value["artifact"]["exists"] or (location and Path(location).is_file()))


def _is_adoption(value: dict[str, Any]) -> bool:
    return value["action"]["retry_mode"] == "ADOPT_PARENT_ARTIFACT"


def is_pristine_idle(value: dict[str, Any]) -> bool:
    """Return whether an IDLE journal proves no executor transaction began."""
    action = value["action"]
    state_commit = value["state_commit"]
    return (
        action["status"] == "IDLE"
        and all(action[key] is None for key in (
            "id", "ticket", "stage", "name", "executor", "schema_path", "protocol_version",
            "prompt_hash", "final_message_path", "retry_mode", "parent_action_id",
            "parent_artifact_path", "parent_artifact_sha256",
        ))
        and action["attempt"] == 0
        and action["invalid_fields"] == []
        and action["allowed_corrections"] == []
        and all(value["fingerprints"][key] is None for key in ("baseline", "ownership", "state_before"))
        and value["process"] == {"started_at": None, "finished_at": None, "exit_code": None}
        and value["artifact"] == {"exists": False, "sha256": None, "validation_status": "PENDING"}
        and all(state_commit[key] is None for key in (
            "state_path", "expected_before_hash", "expected_after_hash", "committed_after_hash", "committed_at",
        ))
        and state_commit["verified"] is False
        and value["incidents"] == []
    )


def safe_state_path(value: dict[str, Any]) -> Path:
    state_path = value["state_commit"]["state_path"]
    if not state_path:
        raise JournalError("STATE_PATH_REQUIRED", "State commit has no state_path.")
    candidate = Path(state_path)
    if not candidate.is_absolute() or ".." in candidate.parts or candidate.is_symlink():
        raise JournalError("STATE_PATH_UNSAFE", "state_path must be an absolute non-symlink path without traversal.")
    candidate = Path(os.path.abspath(str(candidate)))
    workspace = Path(os.path.abspath(value["workspace"]["path"]))
    try:
        candidate.relative_to(workspace)
    except ValueError:
        pass
    else:
        # Legacy in-worktree STATE: the resolved path must stay inside the
        # resolved worktree, which also rejects a symlinked ancestor escape.
        try:
            candidate.resolve().relative_to(workspace.resolve())
        except ValueError as exc:
            raise JournalError("STATE_PATH_UNSAFE", "state_path escapes the worktree through a symlink.") from exc
        return candidate
    try:
        roots = _external_runtime_roots(workspace)
    except JournalError as exc:
        raise JournalError("STATE_PATH_UNSAFE", str(exc)) from exc
    if roots is None:
        raise JournalError("STATE_PATH_UNSAFE", "state_path is outside the worktree and no vault runtime is bound.")
    if _rebase_on_runtime(candidate, roots) != Path("STATE.md"):
        raise JournalError("STATE_PATH_UNSAFE", "state_path is not this worktree's bound runtime STATE.md.")
    trusted = roots[0] / "STATE.md"
    try:
        _path_without_symlink_or_traversal(trusted)
    except JournalError as exc:
        raise JournalError("STATE_PATH_UNSAFE", str(exc)) from exc
    return trusted


def state_commit_hash(value: dict[str, Any]) -> str:
    state_path = safe_state_path(value)
    digest = regular_file_sha256(state_path)
    if digest is None:
        raise JournalError("STATE_COMMIT_FILE_MISSING", "STATE file is missing or not a regular file.")
    return digest


def regular_file_sha256(path: Path) -> str | None:
    """SHA-256 of a regular file opened without following a final symlink; None otherwise."""
    try:
        descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None
        return _sha256_descriptor(descriptor)
    finally:
        os.close(descriptor)


def prepare_state_commit(path: Path, state_path: Path, expected_before_hash: str, expected_after_hash: str) -> dict[str, Any]:
    value = load_journal(path)
    if value["action"]["status"] != "VALIDATED":
        raise JournalError("INVALID_TRANSITION", "State commit can be prepared only from VALIDATED.")
    changed = copy.deepcopy(value)
    changed["state_commit"].update({"state_path": str(state_path), "expected_before_hash": expected_before_hash, "expected_after_hash": expected_after_hash, "committed_after_hash": None, "committed_at": None, "verified": False})
    actual_before = state_commit_hash(changed)
    if actual_before != expected_before_hash:
        raise JournalError("STATE_COMMIT_HASH_MISMATCH", "STATE hash does not match expected_before_hash.")
    atomic_write(path, changed)
    return changed


def mark_state_committed(path: Path, *, caller_expected_after_hash: str | None = None) -> dict[str, Any]:
    value = load_journal(path)
    if value["action"]["status"] != "VALIDATED":
        raise JournalError("INVALID_TRANSITION", "State commit can be marked only from VALIDATED.")
    expected_after = value["state_commit"]["expected_after_hash"]
    expected_before = value["state_commit"]["expected_before_hash"]
    if not expected_after or not expected_before:
        raise JournalError("STATE_COMMIT_NOT_PREPARED", "Expected STATE hashes must be recorded before writing STATE.")
    if caller_expected_after_hash is not None and caller_expected_after_hash != expected_after:
        raise JournalError("STATE_COMMIT_HASH_MISMATCH", "Caller hash disagrees with journal expected_after_hash.")
    actual_hash = state_commit_hash(value)
    if actual_hash == expected_before:
        raise JournalError("STATE_NOT_COMMITTED", "STATE still matches expected_before_hash.")
    if actual_hash != expected_after:
        raise JournalError("STATE_COMMIT_HASH_MISMATCH", "Reread STATE hash does not match expected_after_hash.")
    return _save_transition(path, "STATE_COMMITTED", {"state_commit": {"verified": True, "committed_at": now(), "committed_after_hash": actual_hash}})


def reconcile_already_committed(path: Path) -> dict[str, Any]:
    return mark_state_committed(path)


def release_action(path: Path) -> dict[str, Any]:
    value = load_journal(path)
    if value["action"]["status"] != "STATE_COMMITTED" or not value["state_commit"]["verified"]:
        raise JournalError("STATE_COMMIT_UNVERIFIED", "Release requires a reread and verified STATE_COMMITTED journal.")
    return _save_transition(path, "RELEASED")


def _mirror_to_wiki(value: dict[str, Any], outcome: str) -> dict[str, Any]:
    """Record a finished action in the project's Obsidian wiki; never fails the journal."""
    try:
        import wiki_journal
    except Exception as error:  # a partial install must not break recovery
        return {"status": "SKIPPED", "reason": "WIKI_JOURNAL_UNAVAILABLE", "detail": str(error)}
    return wiki_journal.mirror_action(value, outcome=outcome)


def rollover_journal(path: Path, history_dir: Path) -> dict[str, str]:
    """Archive a verified released action and atomically open a pristine journal."""
    value = load_journal(path)
    action = value["action"]
    artifact = value["artifact"]
    commit = value["state_commit"]
    if action["status"] != "RELEASED":
        raise JournalError("JOURNAL_ROLLOVER_NOT_ALLOWED", "Rollover requires a RELEASED action journal.")
    if not commit["verified"]:
        raise JournalError("JOURNAL_ROLLOVER_UNVERIFIED", "Rollover requires a verified STATE commit.")
    if not action["id"] or not action["ticket"]:
        raise JournalError("JOURNAL_ROLLOVER_INCOMPLETE", "Rollover requires action id and ticket.")
    if not artifact["exists"] or not artifact["sha256"] or artifact["validation_status"] not in {"VALID", "INVALID"}:
        raise JournalError("JOURNAL_ROLLOVER_INCOMPLETE", "Rollover requires a classified artifact.")
    if not commit["committed_after_hash"] or not commit["expected_after_hash"]:
        raise JournalError("JOURNAL_ROLLOVER_INCOMPLETE", "Rollover requires completed STATE commit evidence.")
    archived_path, archived_sha256 = atomic_create_history(
        value["workspace"]["path"], history_dir, action["ticket"], action["id"], canonical_json(value),
    )
    fresh = empty_journal(value["workspace"])
    atomic_write(path, fresh)
    persisted = load_journal(path)
    if not is_pristine_idle(persisted):
        raise JournalError("JOURNAL_ROLLOVER_INVALID", "New journal is not pristine IDLE.")
    recovery = recovery_decision(persisted)["decision"]
    if recovery != "DISPATCH_ALLOWED":
        raise JournalError("JOURNAL_ROLLOVER_INVALID", "New journal does not allow dispatch.")
    wiki = _mirror_to_wiki(value, "RELEASED")
    return {"decision": "ROLLED_OVER", "archived_path": str(archived_path), "archived_sha256": archived_sha256, "new_journal_status": persisted["action"]["status"], "recovery_after_rollover": recovery, "wiki": wiki,
            "next_step": "Run recover; on DISPATCH_ALLOWED prepare the next action from STATE.", "next_command": journal_command(path, "recover")}


def archive_interrupted_allowed(value: dict[str, Any]) -> None:
    """PROCESS_FINISHED with a known process result and no valid artifact may be archived."""
    validate_journal(value)
    action = value["action"]
    process = value["process"]
    commit = value["state_commit"]
    if action["status"] != "PROCESS_FINISHED":
        raise JournalError("JOURNAL_ARCHIVE_INTERRUPTED_NOT_ALLOWED", "archive-interrupted requires PROCESS_FINISHED without a valid artifact.")
    if process["exit_code"] is None or not process["finished_at"] or not process["started_at"]:
        raise JournalError("JOURNAL_PROCESS_RESULT_REQUIRED", "archive-interrupted requires recorded process result evidence.")
    if artifact_present(value) or value["artifact"]["exists"] or value["artifact"]["validation_status"] == "VALID" or value["artifact"]["sha256"]:
        raise JournalError("JOURNAL_ARCHIVE_INTERRUPTED_NOT_ALLOWED", "A present or valid artifact cannot be archived as interrupted.")
    if commit["verified"] or commit["committed_after_hash"]:
        raise JournalError("JOURNAL_ARCHIVE_INTERRUPTED_NOT_ALLOWED", "A STATE_COMMITTED action cannot be archived as interrupted.")
    if not action["id"] or not action["ticket"]:
        raise JournalError("JOURNAL_ARCHIVE_INTERRUPTED_INCOMPLETE", "archive-interrupted requires action id and ticket.")


def archive_interrupted_journal(path: Path, history_dir: Path) -> dict[str, str]:
    """Archive a process-finished action that produced no valid artifact, then open a pristine journal.

    RELEASED/rollover remains the success path. This primitive records INTERRUPTED, never SUCCESS.
    """
    value = load_journal(path)
    archive_interrupted_allowed(value)
    interrupted = transition(value, "INTERRUPTED")
    archived_path, archived_sha256 = atomic_create_history(
        value["workspace"]["path"], history_dir, interrupted["action"]["ticket"], interrupted["action"]["id"], canonical_json(interrupted),
    )
    atomic_write(path, empty_journal(value["workspace"]))
    persisted = load_journal(path)
    if not is_pristine_idle(persisted):
        raise JournalError("JOURNAL_ARCHIVE_INTERRUPTED_INVALID", "New journal is not pristine IDLE.")
    recovery = recovery_decision(persisted)["decision"]
    if recovery != "DISPATCH_ALLOWED":
        raise JournalError("JOURNAL_ARCHIVE_INTERRUPTED_INVALID", "New journal does not allow dispatch.")
    return {
        "decision": "INTERRUPTED",
        "archived_path": str(archived_path),
        "archived_sha256": archived_sha256,
        "new_journal_status": persisted["action"]["status"],
        "recovery_after_archive": recovery,
        "parent_action_id": interrupted["action"]["id"],
        "wiki": _mirror_to_wiki(interrupted, "INTERRUPTED"),
        "next_step": (
            f"Prepare the retry with a new action_id, a distinct final-message path and parent_action_id {interrupted['action']['id']} "
            "(retry_mode FULL_REPLACEMENT). If the parent's final message exists and is complete, prepare it with retry_mode "
            "ADOPT_PARENT_ARTIFACT instead (final_message_path = parent_artifact_path = that file, parent_artifact_sha256 = its SHA-256)."
        ),
        "next_command": journal_command(path, "prepare", "--payload", "'<action-json>'"),
    }


def archive_blocked_journal(path: Path, history_dir: Path, reason: str) -> dict[str, Any]:
    """Archive a BLOCKED, stale INTERRUPTED or dirty IDLE journal with its reason, then open a pristine journal."""
    if not reason or not reason.strip():
        raise JournalError("ARCHIVE_REASON_REQUIRED", "archive-blocked requires a non-empty --reason.",
                           next_command=journal_command(path, "archive-blocked", "--history-dir", str(history_dir), "--reason", "'<reason>'"))
    value = load_journal(path)
    status = value["action"]["status"]
    if not (status in {"BLOCKED", "INTERRUPTED"} or (status == "IDLE" and not is_pristine_idle(value))):
        raise JournalError("JOURNAL_ARCHIVE_BLOCKED_NOT_ALLOWED", f"archive-blocked does not apply to {'a pristine ' if status == 'IDLE' else ''}{status}.",
                           next_command=journal_command(path, "recover"))
    archived = copy.deepcopy(value)
    archived["incidents"].append(f"ARCHIVED_BLOCKED: {' '.join(reason.split())}")
    ticket = archived["action"]["ticket"] or "NO-TICKET"
    action_id = archived["action"]["id"] or f"NO-ACTION-{hashlib.sha256(canonical_json(archived)).hexdigest()[:16]}"
    archived_path, archived_sha256 = atomic_create_history(value["workspace"]["path"], history_dir, ticket, action_id, canonical_json(archived))
    atomic_write(path, empty_journal(value["workspace"]))
    persisted = load_journal(path)
    if not is_pristine_idle(persisted):
        raise JournalError("JOURNAL_ARCHIVE_BLOCKED_INVALID", "New journal is not pristine IDLE.")
    recovery = recovery_decision(persisted, journal_path=path)
    return {
        "decision": "ARCHIVED_BLOCKED",
        "archived_path": str(archived_path),
        "archived_sha256": archived_sha256,
        "new_journal_status": persisted["action"]["status"],
        "recovery_after_archive": recovery["decision"],
        "parent_action_id": archived["action"]["id"],
        "wiki": _mirror_to_wiki(archived, "BLOCKED"),
        "next_step": "The blocked action is history; it is never redispatched. Prepare a new action when the controller decides how to continue.",
        "next_command": recovery["next_command"],
    }


def archive_invalid_allowed(value: dict[str, Any]) -> None:
    """Validate the fail-closed preconditions for invalid-artifact recovery."""
    validate_journal(value)
    action = value["action"]
    artifact = value["artifact"]
    commit = value["state_commit"]
    artifact_path = Path(action["final_message_path"]) if action["final_message_path"] else None
    if (
        not artifact_path
        or "BLOCKED" not in TRANSITIONS[action["status"]]
        or not action["id"]
        or not action["ticket"]
        or not artifact["exists"]
        or artifact["validation_status"] != "INVALID"
        or not artifact["sha256"]
        or not artifact_path.is_file()
        or sha256(artifact_path) != artifact["sha256"]
        or commit["verified"]
    ):
        raise JournalError("JOURNAL_ARCHIVE_INVALID_NOT_ALLOWED", "archive-invalid requires a present, classified invalid artifact without a verified STATE commit.")
    # Archival preserves evidence; the controller alone owns retry budgeting.


def archive_invalid_journal(path: Path, history_dir: Path) -> dict[str, str]:
    """Archive an invalid parent as BLOCKED before opening a pristine retry journal."""
    value = load_journal(path)
    archive_invalid_allowed(value)
    blocked = transition(value, "BLOCKED")
    archived_path, archived_sha256 = atomic_create_history(
        value["workspace"]["path"], history_dir, blocked["action"]["ticket"], blocked["action"]["id"], canonical_json(blocked),
    )
    atomic_write(path, empty_journal(value["workspace"]))
    persisted = load_journal(path)
    if not is_pristine_idle(persisted):
        raise JournalError("JOURNAL_ARCHIVE_INVALID_INVALID", "New journal is not pristine IDLE.")
    recovery = recovery_decision(persisted)["decision"]
    if recovery != "DISPATCH_ALLOWED":
        raise JournalError("JOURNAL_ARCHIVE_INVALID_INVALID", "New journal does not allow dispatch.")
    return {
        "decision": "ARCHIVED_INVALID",
        "archived_path": str(archived_path),
        "archived_sha256": archived_sha256,
        "new_journal_status": persisted["action"]["status"],
        "recovery_after_archive": recovery,
        "parent_action_id": blocked["action"]["id"],
        "parent_artifact_path": blocked["action"]["final_message_path"],
        "parent_artifact_sha256": blocked["artifact"]["sha256"],
        "wiki": _mirror_to_wiki(blocked, "INVALID"),
        "next_step": "Prepare one corrective retry with a new action_id, parent_action_id, parent_artifact_path and parent_artifact_sha256 from this result (FULL_REPLACEMENT or METADATA_OVERLAY).",
        "next_command": journal_command(path, "prepare", "--payload", "'<action-json>'"),
    }


def _invalid_classification_allowed(value: dict[str, Any], invalid_fields: list[str]) -> None:
    validate_journal(value)
    artifact = value["artifact"]
    commit = value["state_commit"]
    artifact_path = Path(value["action"]["final_message_path"]) if value["action"]["final_message_path"] else None
    if (
        not invalid_fields
        or any(not isinstance(field, str) or not field for field in invalid_fields)
        or not artifact_path
        or not artifact["exists"]
        or artifact["validation_status"] not in {"PENDING", "INVALID"}
        or not artifact["sha256"]
        or not artifact_path.is_file()
        or sha256(artifact_path) != artifact["sha256"]
        or commit["verified"]
        or commit["committed_after_hash"]
    ):
        raise JournalError("INVALID_ARTIFACT_CLASSIFICATION_NOT_ALLOWED", "Invalid classification requires a present, uncommitted artifact and non-empty invalid fields.")


def classify_invalid_artifact(path: Path, invalid_fields: list[str]) -> dict[str, Any]:
    """Classify an ARTIFACT_READY result as invalid without changing its stage."""
    value = load_journal(path)
    if value["action"]["status"] != "ARTIFACT_READY":
        raise JournalError("INVALID_ARTIFACT_CLASSIFICATION_NOT_ALLOWED", "classify-invalid requires ARTIFACT_READY.")
    _invalid_classification_allowed(value, invalid_fields)
    changed = copy.deepcopy(value)
    changed["artifact"]["validation_status"] = "INVALID"
    changed["action"]["invalid_fields"] = invalid_fields
    atomic_write(path, changed)
    return changed


def recover_blocked_pending_artifact(path: Path, invalid_fields: list[str]) -> dict[str, Any]:
    """Repair a legacy blocked invalid artifact only after a known process result."""
    value = load_journal(path)
    process = value["process"]
    if value["action"]["status"] != "BLOCKED":
        raise JournalError("INVALID_ARTIFACT_RECOVERY_NOT_ALLOWED", "recover-blocked-invalid requires BLOCKED.")
    if not process["started_at"] or not process["finished_at"] or process["exit_code"] is None:
        raise JournalError("INVALID_ARTIFACT_CLASSIFICATION_NOT_ALLOWED", "recover-blocked-invalid requires a known process result.")
    _invalid_classification_allowed(value, invalid_fields)
    changed = copy.deepcopy(value)
    changed["action"]["status"] = "ARTIFACT_READY"
    changed["artifact"]["validation_status"] = "INVALID"
    changed["action"]["invalid_fields"] = invalid_fields
    atomic_write(path, changed)
    return changed


def _quote(value: str | Path) -> str:
    text = str(value)
    return text if text.startswith("<") and text.endswith(">") else shlex.quote(text)


def journal_command(journal_path: Path | None, command: str, *options: str) -> str:
    """Exact CLI string for this controller; ``<...>`` marks a value the controller must fill in."""
    journal = _quote(journal_path) if journal_path is not None else "<journal>"
    rendered = " ".join(_quote(option) if not option.startswith("--") and not option.startswith("'<") else option for option in options)
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(SCRIPT))} --journal {journal} --json {command}{(' ' + rendered) if rendered else ''}"


def paths_command() -> str:
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(SCRIPT))} --json paths"


def canonical_history_dir(journal_path: Path | None) -> str | Path:
    """History lives next to the journal in both storage modes: ``<journal dir>/action-journal-history``."""
    return journal_path.parent / HISTORY_DIRECTORY_NAME if journal_path is not None else "<history-dir>"


def canonical_state_path(journal_path: Path | None) -> str | Path:
    return journal_path.parent / "STATE.md" if journal_path is not None else "<state>"


def _decision(decision: str, reason: str, next_step: str, next_command: str, **extra: str) -> dict[str, str]:
    return {"decision": decision, "reason": reason, "next_step": next_step, "next_command": next_command, **extra}


def recovery_decision(value: dict[str, Any], *, journal_path: Path | None = None) -> dict[str, str]:
    """Classify the journal; every result names the next step and the exact next command."""
    validate_journal(value)
    action = value["action"]
    status = action["status"]
    exists = artifact_present(value)
    history = canonical_history_dir(journal_path)
    cmd = lambda command, *options: journal_command(journal_path, command, *options)  # noqa: E731
    archive_blocked = cmd("archive-blocked", "--history-dir", str(history), "--reason", "'<reason>'")
    recover = cmd("recover")
    if status == "RELEASED":
        return _decision("RELEASED", "action lock already released",
                         "Archive the released action and open a pristine journal with rollover, then run recover again before preparing the next action.",
                         cmd("rollover", "--history-dir", str(history)))
    if status == "INTERRUPTED":
        return _decision("BLOCKED", "interrupted action cannot be redispatched",
                         "A live INTERRUPTED journal is never redispatched; archive it with a reason, then prepare a new action with parent_action_id.",
                         archive_blocked, stop_reason="ACTION_RECOVERY_REQUIRED")
    if status == "IDLE":
        if is_pristine_idle(value):
            return _decision("DISPATCH_ALLOWED", "pristine idle journal has no action to recover",
                             "Prepare the next action with a complete PREPARED payload (new action_id, prompt SHA-256, distinct final-message path).",
                             cmd("prepare", "--payload", "'<action-json>'"))
        return _decision("BLOCKED", "idle journal contains transaction evidence",
                         "The IDLE journal carries stray evidence; archive it with a reason to open a pristine journal.",
                         archive_blocked, stop_reason="DIRTY_OR_INCONSISTENT_IDLE")
    if status == "BLOCKED":
        return _decision("BLOCKED", "action was blocked",
                         "Archive the blocked action with a reason to open a pristine journal, then prepare a new action. If it was blocked only because its artifact was invalid, recover-blocked-invalid --invalid-field <field> reopens it for a corrective retry instead.",
                         archive_blocked, stop_reason="ACTION_BLOCKED")
    if exists and value["artifact"]["validation_status"] == "INVALID":
        archive_invalid = cmd("archive-invalid", "--history-dir", str(history))
        if corrective_retry_allowed(action["attempt"] - 1, 1):
            return _decision("CORRECTIVE_RETRY_AVAILABLE", "invalid artifact",
                             "Archive the invalid parent, then prepare one corrective retry with parent_action_id, parent_artifact_path and parent_artifact_sha256 from the archive result.",
                             archive_invalid)
        return _decision("BLOCKED", "invalid artifact retry budget reached",
                         "Archive the invalid action, then stop: the corrective-retry budget is spent and a human decides the next step for this stage.",
                         archive_invalid, stop_reason="RETRY_BUDGET_REACHED")
    if status in {"VALIDATED", "STATE_COMMITTED"} and value["state_commit"]["state_path"]:
        desync_step = "Record a STATE_DESYNC incident and restore STATE to the prepared before/after snapshot, then run recover; if STATE cannot be restored, block the action and archive it with archive-blocked."
        try:
            actual_hash = state_commit_hash(value)
        except JournalError as exc:
            return _decision("BLOCKED", str(exc), f"{exc.next_step} Then run recover again; if it cannot be fixed, block the action and archive it with archive-blocked.",
                             recover, stop_reason=exc.code, incident_required=exc.code)
        before = value["state_commit"]["expected_before_hash"]
        after = value["state_commit"]["expected_after_hash"]
        committed = value["state_commit"]["committed_after_hash"]
        if status == "VALIDATED":
            if actual_hash == after:
                return _decision("ALREADY_COMMITTED", "STATE is persisted; reconcile journal only",
                                 "STATE already holds the prepared snapshot; mark the commit (it rereads STATE), then release and roll over. Never redispatch.",
                                 cmd("mark-state-committed"))
            if actual_hash == before:
                return _decision("STATE_COMMIT_REQUIRED", "STATE is not yet persisted; controller reconciliation required",
                                 "Write the prepared STATE snapshot atomically (its SHA-256 must equal expected_after_hash), then mark the commit. Never redispatch.",
                                 cmd("mark-state-committed"))
            return _decision("BLOCKED", "STATE differs from both prepared hashes", desync_step, cmd("block"),
                             stop_reason="STATE_DESYNC", incident_required="STATE_DESYNC")
        if value["state_commit"]["verified"] and actual_hash == committed:
            return _decision("ALREADY_COMMITTED", "verified STATE commit is still present; release only",
                             "Release the action, then roll over.", cmd("release"))
        return _decision("BLOCKED", "Recorded state commit diverges from STATE", desync_step, cmd("block"),
                         stop_reason="STATE_DESYNC", incident_required="STATE_DESYNC")
    if status == "STATE_COMMITTED":
        return _decision("BLOCKED", "state commit lacks verifiable STATE metadata",
                         "Record a STATE_DESYNC incident, block the action and archive it with archive-blocked.", cmd("block"),
                         stop_reason="STATE_DESYNC", incident_required="STATE_DESYNC")
    if status == "PREPARED" and _is_adoption(value):
        return _decision("RECONCILE_ARTIFACT", "adoption of the parent's artifact is prepared",
                         "Record the adopted parent artifact (its SHA-256 is verified again), then validate it and continue the normal VALIDATED -> STATE_COMMITTED path. Never dispatch an executor for this action.",
                         cmd("record-artifact"))
    if exists and status in {"DISPATCHED", "PROCESS_FINISHED", "ARTIFACT_READY", "VALIDATED"}:
        reason = "artifact must be classified before dispatch"
        if status == "DISPATCHED":
            return _decision("RECONCILE_ARTIFACT", reason,
                             "A final message exists but the process result was never recorded; record it (exit code of the executor, 1 when unknown), then record the artifact.",
                             cmd("record-process", "--finished", "--exit-code", "<exit-code>"))
        if status == "PROCESS_FINISHED":
            return _decision("RECONCILE_ARTIFACT", reason, "Record the final message's existence and SHA-256.", cmd("record-artifact"))
        if status == "ARTIFACT_READY":
            validator = SCRIPT.parent / "validate_protocol.py"
            return _decision("RECONCILE_ARTIFACT", reason,
                             "Validate the final message (validate_protocol.py with the stage context); on success run mark-validated, otherwise classify-invalid --invalid-field <field>.",
                             f"{shlex.quote(sys.executable)} {shlex.quote(str(validator))} --action {shlex.quote(str(action['stage']))} --result {_quote(action['final_message_path'])} --json")
        return _decision("RECONCILE_ARTIFACT", reason,
                         "Before writing STATE, record its current and intended SHA-256 with prepare-state-commit; then write STATE atomically and run mark-state-committed.",
                         cmd("prepare-state-commit", "--state-path", str(canonical_state_path(journal_path)),
                             "--expected-before-hash", "<sha256-of-current-STATE>", "--expected-after-hash", "<sha256-of-new-STATE>"))
    if status == "PREPARED" and not value["process"]["started_at"] and not exists:
        return _decision("DISPATCH_ALLOWED", "prepared action has not started",
                         "Record the dispatch with the prepared prompt hash immediately before launching the executor in the foreground; after it exits (even on error or timeout) record --finished with its exit code.",
                         cmd("record-process", "--started", "--prompt-sha256", str(action["prompt_hash"] or "<prompt-sha256>")))
    if status == "PREPARED" and exists:
        return _decision("BLOCKED", "a final-message file already exists for an undispatched action",
                         "The final-message path is occupied by a file this action never produced; block and archive the action, then prepare a new one with a distinct final-message path.",
                         cmd("block"), stop_reason="ARTIFACT_PENDING")
    if status == "DISPATCHED" and not exists and value["process"]["exit_code"] is None:
        return _decision("WAIT_OR_MANUAL_REVIEW", "process result is unknown",
                         "If the executor is still running, wait for it in the foreground. If it ended, timed out or the launcher crashed, record its exit code (124 for a timeout, 1 when unknown); recover then names archive-interrupted.",
                         cmd("record-process", "--finished", "--exit-code", "<exit-code>"))
    if status == "PROCESS_FINISHED" and not exists:
        return _decision("ARCHIVE_INTERRUPTED_REQUIRED", f"executor process ended with exit code {value['process']['exit_code']} without a final message",
                         f"Archive the action as INTERRUPTED; then prepare a retry with a new action_id and parent_action_id {action['id']} (FULL_REPLACEMENT), or, if the parent's final message appears later, prepare it with retry_mode ADOPT_PARENT_ARTIFACT.",
                         cmd("archive-interrupted", "--history-dir", str(history)), stop_reason="EXECUTOR_PROCESS_ENDED_WITHOUT_ARTIFACT")
    return _decision("BLOCKED", "journal state is inconsistent",
                     "The journal combines evidence no command produces; block the action and archive it with archive-blocked.",
                     cmd("block"), stop_reason="JOURNAL_INCONSISTENT")


def dispatch_allowed(value: dict[str, Any]) -> bool:
    return recovery_decision(value)["decision"] == "DISPATCH_ALLOWED"


def require_dispatch_allowed(value: dict[str, Any], *, journal_path: Path | None = None) -> None:
    decision = recovery_decision(value, journal_path=journal_path)
    if decision["decision"] != "DISPATCH_ALLOWED":
        raise JournalError("ARTIFACT_PENDING" if artifact_present(value) else "ACTION_RECOVERY_REQUIRED", decision["reason"],
                           next_step=decision["next_step"], next_command=decision["next_command"])


def _read_history_entry(workspace_path: str, history_dir: Path, ticket: str, action_id: str) -> dict[str, Any] | None:
    """Read one archived action through no-follow descriptors; None when it does not exist."""
    if not ticket or not action_id or Path(ticket).name != ticket or Path(action_id).name != action_id:
        return None
    anchor, relative, _ = _history_anchor(workspace_path, history_dir)
    descriptors: list[int] = []
    try:
        descriptors, _ = _open_trusted_absolute_directory(anchor)
        for component in (*relative.parts, ticket):
            descriptors.append(os.open(component, _history_directory_flags(), dir_fd=descriptors[-1]))
        descriptor = os.open(f"{action_id}.json", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), dir_fd=descriptors[-1])
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                return None
            with os.fdopen(os.dup(descriptor), "rb") as handle:
                return validate_journal(json.loads(handle.read().decode("utf-8")))
        finally:
            os.close(descriptor)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, JournalError):
        return None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def require_adoption_allowed(value: dict[str, Any], history_dir: Path) -> None:
    """ADOPT_PARENT_ARTIFACT: adopt the INTERRUPTED parent's own final message, verified by SHA-256, without dispatch."""
    action = value["action"]
    parent_id = action["parent_action_id"]
    adopted = action["parent_artifact_path"]
    if (
        not parent_id or not adopted or not action["parent_artifact_sha256"]
        or action["final_message_path"] != adopted
        or any(value["process"][key] is not None for key in ("started_at", "finished_at", "exit_code"))
    ):
        raise JournalError("ADOPTION_NOT_ALLOWED", "ADOPT_PARENT_ARTIFACT needs parent_action_id, parent_artifact_path equal to final_message_path, parent_artifact_sha256 and no process evidence.")
    parent = _read_history_entry(value["workspace"]["path"], history_dir, str(action["ticket"]), str(parent_id))
    if parent is None:
        raise JournalError("ADOPTION_PARENT_NOT_FOUND", f"Parent {parent_id} is not archived under {history_dir}.")
    if (
        parent["action"]["status"] != "INTERRUPTED"
        or parent["action"]["ticket"] != action["ticket"]
        or parent["action"]["stage"] != action["stage"]
        or parent["action"]["final_message_path"] != adopted
        or parent["workspace"]["path"] != value["workspace"]["path"]
    ):
        raise JournalError("ADOPTION_NOT_ALLOWED", "Only the final message of an INTERRUPTED parent of the same ticket, stage and worktree can be adopted.")
    if regular_file_sha256(Path(adopted)) != action["parent_artifact_sha256"]:
        raise JournalError("ADOPTION_ARTIFACT_MISMATCH", "The adopted file is missing, irregular, or does not match parent_artifact_sha256.")


def prepare_action(path: Path, next_value: dict[str, Any], *, history_dir: Path | None = None) -> None:
    if path.exists():
        previous = load_journal(path)
        if not is_pristine_idle(previous):
            require_dispatch_allowed(previous, journal_path=path)
            raise JournalError("ACTION_RECOVERY_REQUIRED", "Existing action requires recovery before a new action can be prepared.",
                               next_command=journal_command(path, "recover"))
    validate_journal(next_value)
    if next_value["action"]["status"] != "PREPARED":
        raise JournalError("INVALID_TRANSITION", "New action must start PREPARED.")
    if _is_adoption(next_value):
        require_adoption_allowed(next_value, history_dir or path.parent / HISTORY_DIRECTORY_NAME)
    atomic_write(path, next_value)
    _mirror_to_wiki(next_value, "PREPARED")


def record_process_started(path: Path, prompt_sha256: str | None = None) -> dict[str, Any]:
    """Dispatch guard: only a dispatchable PREPARED action with the prepared prompt may start."""
    value = load_journal(path)
    recover = journal_command(path, "recover")
    if value["action"]["status"] != "PREPARED":
        raise JournalError("DISPATCH_NOT_PREPARED", f"Dispatch requires PREPARED, journal is {value['action']['status']}.", next_command=recover)
    if _is_adoption(value):
        raise JournalError("ADOPTION_DISPATCH_FORBIDDEN", "An ADOPT_PARENT_ARTIFACT action never dispatches an executor.",
                           next_command=journal_command(path, "record-artifact"))
    decision = recovery_decision(value, journal_path=path)
    if artifact_present(value):
        raise JournalError("ARTIFACT_PENDING", "The final-message path already holds a file; dispatch would overwrite it.",
                           next_step=decision["next_step"], next_command=decision["next_command"])
    if decision["decision"] != "DISPATCH_ALLOWED":
        raise JournalError("DISPATCH_NOT_PREPARED", decision["reason"], next_command=recover)
    if prompt_sha256 is not None and prompt_sha256 != value["action"]["prompt_hash"]:
        raise JournalError("PROMPT_HASH_MISMATCH", "--prompt-sha256 differs from the prepared prompt_hash.",
                           next_command=journal_command(path, "block"))
    return _save_transition(path, "DISPATCHED", {"process": {"started_at": now()}})


def record_process_finished(path: Path, exit_code: int) -> dict[str, Any]:
    """Record the executor's exit; repeating the same result is a no-op so a launcher can call it in ``finally``."""
    value = load_journal(path)
    status = value["action"]["status"]
    process = value["process"]
    if status == "DISPATCHED":
        return _save_transition(path, "PROCESS_FINISHED", {"process": {"finished_at": now(), "exit_code": exit_code}})
    if process["started_at"] and process["exit_code"] is None and status == "BLOCKED":
        changed = copy.deepcopy(value)
        changed["process"].update(finished_at=now(), exit_code=exit_code)
        atomic_write(path, changed)
        return changed
    if process["started_at"] and process["exit_code"] is not None:
        if process["exit_code"] == exit_code:
            return value
        raise JournalError("PROCESS_RESULT_CONFLICT", f"Exit code {process['exit_code']} is already recorded.",
                           next_command=journal_command(path, "recover"))
    raise JournalError("PROCESS_NOT_STARTED", f"No dispatch was recorded (journal is {status}).", next_command=journal_command(path, "recover"))


def record_artifact(path: Path) -> dict[str, Any]:
    """Record the final message; a missing or irregular file never transitions."""
    value = load_journal(path)
    action = value["action"]
    adoption = action["status"] == "PREPARED" and _is_adoption(value)
    if action["status"] != "PROCESS_FINISHED" and not adoption:
        raise JournalError("INVALID_TRANSITION", f"record-artifact requires PROCESS_FINISHED (or a prepared adoption), journal is {action['status']}.",
                           next_command=journal_command(path, "recover"))
    digest = regular_file_sha256(Path(action["final_message_path"])) if action["final_message_path"] else None
    if adoption:
        if digest is None or digest != action["parent_artifact_sha256"]:
            raise JournalError("ADOPTION_ARTIFACT_MISMATCH", "The adopted file is missing, irregular, or no longer matches parent_artifact_sha256.",
                               next_command=journal_command(path, "block"))
        changed = copy.deepcopy(value)
        changed["action"]["status"] = "ARTIFACT_READY"
        changed["artifact"].update(exists=True, sha256=digest)
        atomic_write(path, changed)
        return changed
    if digest is None:
        raise JournalError("ARTIFACT_MISSING", "The final-message path is missing or is not a regular file; the journal stays PROCESS_FINISHED.",
                           next_command=journal_command(path, "archive-interrupted", "--history-dir", str(canonical_history_dir(path))))
    return _save_transition(path, "ARTIFACT_READY", {"artifact": {"exists": True, "sha256": digest}})


def mark_validated(path: Path) -> dict[str, Any]:
    """Mark the recorded artifact valid only while its bytes still match the recorded SHA-256."""
    value = load_journal(path)
    if value["action"]["status"] != "ARTIFACT_READY":
        raise JournalError("INVALID_TRANSITION", f"mark-validated requires ARTIFACT_READY, journal is {value['action']['status']}.",
                           next_command=journal_command(path, "recover"))
    if value["artifact"]["validation_status"] == "INVALID":
        raise JournalError("ARTIFACT_CLASSIFIED_INVALID", "The artifact is classified INVALID.", next_command=journal_command(path, "recover"))
    location = value["action"]["final_message_path"]
    if not value["artifact"]["exists"] or regular_file_sha256(Path(location)) != value["artifact"]["sha256"]:
        raise JournalError("ARTIFACT_CHANGED", "The final message no longer matches the recorded SHA-256.", next_command=journal_command(path, "block"))
    return _save_transition(path, "VALIDATED", {"artifact": {"validation_status": "VALID"}})


def corrective_retry_allowed(used: int, maximum: int = 1) -> bool:
    return used < maximum


def metadata_overlay(parent: dict[str, Any], corrections: dict[str, Any], allowed: list[str], *, baseline: str, ownership: str, parent_baseline: str, parent_ownership: str, product_changed: bool) -> dict[str, Any]:
    if not parent.get("evidence_functional") or baseline != parent_baseline or ownership != parent_ownership or product_changed:
        raise JournalError("PARENT_EVIDENCE_MISMATCH", "Parent evidence cannot be reused.")
    if any(key not in allowed for key in corrections):
        raise JournalError("METADATA_OVERLAY_INVALID", "Correction is outside allowed metadata fields.")
    result = copy.deepcopy(parent)
    for key, value in corrections.items(): result[key] = copy.deepcopy(value)
    return result


def record_incident(path: Path, *, ticket: str, stage: str, action_id: str, incident_type: str, summary: str, artifact_path: str, state_reference: str, recovery: str, currently_blocking: bool, timestamp: str | None = None) -> str:
    stamp = timestamp or now()
    identifier = f"INC-{stamp.replace('-', '').replace(':', '').replace('T', '-').replace('Z', '')}-{incident_type}"
    entry = f"\n## {identifier}\n\nID: {identifier}\ntimestamp: {stamp}\nticket: {ticket}\nstage: {stage}\naction_id: {action_id}\ntype: {incident_type}\nsummary: {summary}\nartifact_path: {artifact_path}\nstate_reference: {state_reference}\nrecovery: {recovery}\ncurrently_blocking: {'YES' if currently_blocking else 'NO'}\npromotion_approved: NO\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(entry)
        handle.flush(); os.fsync(handle.fileno())
    _record_incident_in_wiki(path, identifier, entry, ticket=ticket, stage=stage)
    return identifier


def _record_incident_in_wiki(path: Path, identifier: str, entry: str, *, ticket: str, stage: str) -> None:
    """Mirror the incident into the wiki of the journal's workspace; never fails the caller."""
    try:
        import wiki_journal

        # Read only the workspace path: an incident is often about a journal that no longer validates.
        journal = json.loads((path.parent / "ACTION_JOURNAL.json").read_text(encoding="utf-8"))
        wiki_journal.safe_record_for_workspace(
            Path(journal["workspace"]["path"]), kind="incident", title=identifier, body=entry.strip(),
            ticket=ticket, stage=stage,
        )
    except Exception:
        pass


def _save_transition(path: Path, target: str, update: dict[str, Any] | None = None) -> dict[str, Any]:
    value = transition(load_journal(path), target)
    if update:
        for section, fields in update.items(): value[section].update(fields)
    atomic_write(path, value)
    return value


def _git_toplevel(directory: Path) -> Path | None:
    try:
        result = subprocess.run(["git", "-C", str(directory), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    top = result.stdout.strip()
    return Path(top) if result.returncode == 0 and top else None


def canonical_paths(repo: Path | None = None) -> dict[str, str]:
    """Canonical runtime paths of a worktree for this controller, in either storage mode."""
    orchestration = SCRIPT.parent.parent
    install_root = orchestration.parent.parent
    repository_local = (install_root / ".git").exists()
    if repository_local:
        workspace = install_root
        given = _git_toplevel(repo) if repo is not None else None
        if given is not None and os.path.realpath(given) != os.path.realpath(workspace):
            raise JournalError("CONTROLLER_WORKSPACE_MISMATCH", f"This controller is installed in {workspace}, not {given}.")
    else:
        found = _git_toplevel(repo or Path.cwd())
        if found is None:
            raise JournalError("WORKSPACE_REQUIRED", "Cannot find the Git worktree; the vault runtime is keyed per worktree.",
                               next_command=paths_command() + " --repo <worktree>")
        workspace = found
    runtime = _external_runtime_root(workspace)
    storage = "OBSIDIAN"
    if runtime is None:
        if not repository_local:
            raise JournalError("WORKSPACE_REQUIRED", "This controller is neither inside a worktree nor bound to a vault.")
        runtime, storage = orchestration, "LOCAL"
    journal = runtime / "ACTION_JOURNAL.json"
    return {
        "status": "OK",
        "storage": storage,
        "workspace": str(workspace),
        "runtime_dir": str(runtime),
        "journal": str(journal),
        "history_dir": str(runtime / HISTORY_DIRECTORY_NAME),
        "state": str(runtime / "STATE.md"),
        "incidents": str(runtime / "INCIDENTS.md"),
        "next_step": "Pass journal as --journal, history_dir as --history-dir and state as --state-path; run recover before any action.",
        "next_command": journal_command(journal, "recover"),
    }


COMMANDS = (
    "init", "prepare", "record-process", "record-artifact", "mark-validated", "classify-invalid", "recover-blocked-invalid",
    "prepare-state-commit", "mark-state-committed", "release", "rollover", "archive-interrupted", "archive-invalid",
    "archive-blocked", "block", "inspect", "recover", "paths",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", help="Path to ACTION_JOURNAL.json; required by every command except paths (which prints it).")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON.")
    parser.add_argument("--payload", help="JSON payload for init or prepare.")
    parser.add_argument("--started", action="store_true", help="Record executor dispatch start; requires PREPARED.")
    parser.add_argument("--finished", action="store_true", help="Record executor process completion; repeating the same exit code is a no-op.")
    parser.add_argument("--exit-code", type=int, help="Executor process exit code with --finished (124 for a timeout).")
    parser.add_argument("--prompt-sha256", help="With --started: SHA-256 of the prompt being sent; must equal the prepared prompt_hash.")
    parser.add_argument("--state-path", help="Canonical STATE path (see paths): the bound vault runtime STATE.md, or an in-worktree non-symlink path.")
    parser.add_argument("--expected-before-hash", help="SHA-256 of STATE before the controller writes it.")
    parser.add_argument("--expected-after-hash", help="SHA-256 expected after the controller atomically writes STATE.")
    parser.add_argument("--committed-after-hash", help="Compatibility check only; never trusted as proof of STATE persistence.")
    parser.add_argument("--history-dir", help="Append-only history root (see paths); required by rollover and archives, read by an ADOPT_PARENT_ARTIFACT prepare.")
    parser.add_argument("--invalid-field", action="append", default=[], help="Field that made the artifact invalid; required by invalid classification.")
    parser.add_argument("--reason", help="Why a blocked action is abandoned; required by archive-blocked.")
    parser.add_argument("--repo", help="Worktree for paths (default: the current directory).")
    parser.add_argument("command", choices=COMMANDS)
    args = parser.parse_args(argv)
    path = Path(args.journal) if args.journal else None
    try:
        if args.command == "paths":
            value = canonical_paths(Path(args.repo) if args.repo else None)
            print(json.dumps(value, sort_keys=True) if args.json else value)
            return 0
        if path is None:
            raise JournalError("JOURNAL_REQUIRED", f"{args.command} requires --journal.", next_command=paths_command())
        if args.command == "init":
            if path.exists(): raise JournalError("JOURNAL_EXISTS", "Existing journal is never overwritten.", next_command=journal_command(path, "recover"))
            if not args.payload: raise JournalError("PAYLOAD_REQUIRED", "init requires --payload workspace JSON.")
            workspace = json.loads(args.payload); value = empty_journal(workspace); atomic_write(path, value)
        elif args.command == "prepare":
            if not args.payload: raise JournalError("PAYLOAD_REQUIRED", "prepare requires full --payload JSON.")
            prepare_action(path, json.loads(args.payload), history_dir=Path(args.history_dir) if args.history_dir else None); value = load_journal(path)
        elif args.command == "record-process":
            if args.started and args.finished: raise JournalError("PAYLOAD_INVALID", "record-process accepts one of --started or --finished.")
            if args.started:
                value = record_process_started(path, args.prompt_sha256)
            elif args.finished:
                if args.exit_code is None: raise JournalError("PAYLOAD_REQUIRED", "--finished requires --exit-code.")
                value = record_process_finished(path, args.exit_code)
            else: raise JournalError("PAYLOAD_REQUIRED", "record-process requires --started or --finished.")
        elif args.command == "record-artifact": value = record_artifact(path)
        elif args.command == "mark-validated": value = mark_validated(path)
        elif args.command == "classify-invalid": value = classify_invalid_artifact(path, args.invalid_field)
        elif args.command == "recover-blocked-invalid": value = recover_blocked_pending_artifact(path, args.invalid_field)
        elif args.command == "prepare-state-commit":
            if not args.state_path or not args.expected_before_hash or not args.expected_after_hash:
                raise JournalError("PAYLOAD_REQUIRED", "prepare-state-commit requires --state-path, --expected-before-hash, and --expected-after-hash.")
            value = prepare_state_commit(path, Path(args.state_path), args.expected_before_hash, args.expected_after_hash)
        elif args.command == "mark-state-committed":
            value = mark_state_committed(path, caller_expected_after_hash=args.expected_after_hash or args.committed_after_hash)
        elif args.command == "release": value = release_action(path)
        elif args.command in {"rollover", "archive-interrupted", "archive-invalid", "archive-blocked"}:
            if not args.history_dir:
                raise JournalError("PAYLOAD_REQUIRED", f"{args.command} requires --history-dir.",
                                   next_command=journal_command(path, args.command, "--history-dir", str(canonical_history_dir(path))))
            history = Path(args.history_dir)
            if args.command == "rollover": value = rollover_journal(path, history)
            elif args.command == "archive-interrupted": value = archive_interrupted_journal(path, history)
            elif args.command == "archive-invalid": value = archive_invalid_journal(path, history)
            else: value = archive_blocked_journal(path, history, args.reason or "")
        elif args.command == "block":
            value = _save_transition(path, "BLOCKED"); _mirror_to_wiki(value, "BLOCKED")
        elif args.command == "inspect": value = load_journal(path)
        else: value = recovery_decision(load_journal(path), journal_path=path)
        print(json.dumps(value, sort_keys=True) if args.json else value)
        return 0
    except (JournalError, json.JSONDecodeError) as exc:
        code = getattr(exc, "code", "PAYLOAD_INVALID")
        payload = {"status": code, "message": str(exc), "next_step": getattr(exc, "next_step", NEXT_STEPS[code])}
        command = getattr(exc, "next_command", None)
        if command is None and path is not None and code not in {"PAYLOAD_REQUIRED", "PAYLOAD_INVALID", "JOURNAL_INVALID", "JOURNAL_EXISTS"}:
            command = journal_command(path, "recover")
        if command:
            payload["next_command"] = command
        print(json.dumps(payload, sort_keys=True) if args.json else payload)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
