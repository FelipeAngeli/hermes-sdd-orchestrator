#!/usr/bin/env python3
"""Atomic write-ahead journal for executor action recovery; standard library only."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import secrets
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

JOURNAL_VERSION = 1
STATUSES = {"IDLE", "PREPARED", "DISPATCHED", "PROCESS_FINISHED", "ARTIFACT_READY", "VALIDATED", "STATE_COMMITTED", "RELEASED", "BLOCKED", "INTERRUPTED"}
TRANSITIONS = {
    "IDLE": {"PREPARED"}, "PREPARED": {"DISPATCHED", "BLOCKED"},
    "DISPATCHED": {"PROCESS_FINISHED", "BLOCKED"}, "PROCESS_FINISHED": {"ARTIFACT_READY", "BLOCKED", "INTERRUPTED"},
    "ARTIFACT_READY": {"VALIDATED", "BLOCKED"}, "VALIDATED": {"STATE_COMMITTED", "BLOCKED"},
    "STATE_COMMITTED": {"RELEASED", "BLOCKED"}, "RELEASED": set(), "BLOCKED": set(), "INTERRUPTED": set(),
}

class JournalError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


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


def _sha256_descriptor(descriptor: int) -> str:
    digest = hashlib.sha256()
    with os.fdopen(os.dup(descriptor), "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_create_history(workspace_path: str, history_dir: Path, ticket: str, action_id: str, content: bytes) -> tuple[Path, str]:
    """Persist immutable history through workspace-anchored directory descriptors only."""
    if Path(ticket).name != ticket or Path(action_id).name != action_id:
        raise JournalError("HISTORY_PATH_UNSAFE", "Ticket and action id must be single path components.")
    workspace = Path(workspace_path)
    if not workspace.is_absolute():
        raise JournalError("HISTORY_PATH_UNSAFE", "Workspace path must be absolute.")
    history_root = history_dir if history_dir.is_absolute() else workspace / history_dir
    try:
        relative_history = history_root.relative_to(workspace)
    except ValueError as exc:
        raise JournalError("HISTORY_PATH_UNSAFE", "History path must be inside the journal workspace.") from exc
    if not relative_history.parts or any(part in {"", ".", ".."} for part in relative_history.parts):
        raise JournalError("HISTORY_PATH_UNSAFE", "History path must name a directory below the workspace.")

    descriptors: list[int] = []
    components: list[tuple[int, str, os.stat_result]] = []
    temporary: str | None = None
    file_descriptor: int | None = None
    final_name = f"{action_id}.json"
    display_path = workspace.joinpath(*relative_history.parts, ticket, final_name)
    try:
        workspace_fd = os.open(str(workspace), _history_directory_flags())
        descriptors.append(workspace_fd)
        parent_fd = workspace_fd
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
    workspace = Path(value["workspace"]["path"]).resolve()
    if not candidate.is_absolute() or candidate.is_symlink():
        raise JournalError("STATE_PATH_UNSAFE", "state_path must be an in-worktree non-symlink absolute path.")
    try:
        candidate.resolve().relative_to(workspace)
    except ValueError as exc:
        raise JournalError("STATE_PATH_UNSAFE", "state_path is outside the expected workspace.") from exc
    return candidate


def state_commit_hash(value: dict[str, Any]) -> str:
    state_path = safe_state_path(value)
    if not state_path.is_file():
        raise JournalError("STATE_COMMIT_FILE_MISSING", "STATE file is missing or not a regular file.")
    return sha256(state_path)


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
    return {"decision": "ROLLED_OVER", "archived_path": str(archived_path), "archived_sha256": archived_sha256, "new_journal_status": persisted["action"]["status"], "recovery_after_rollover": recovery}


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


def recovery_decision(value: dict[str, Any]) -> dict[str, str]:
    validate_journal(value)
    status = value["action"]["status"]
    exists = artifact_present(value)
    if status == "RELEASED": return {"decision": "RELEASED", "reason": "action lock already released"}
    if status == "INTERRUPTED": return {"decision": "BLOCKED", "reason": "interrupted action cannot be redispatched", "stop_reason": "ACTION_RECOVERY_REQUIRED"}
    if status == "IDLE":
        if is_pristine_idle(value):
            return {"decision": "DISPATCH_ALLOWED", "reason": "pristine idle journal has no action to recover"}
        return {"decision": "BLOCKED", "reason": "idle journal contains transaction evidence", "stop_reason": "DIRTY_OR_INCONSISTENT_IDLE"}
    if exists and value["artifact"]["validation_status"] == "INVALID":
        if corrective_retry_allowed(value["action"]["attempt"] - 1, 1):
            return {"decision": "CORRECTIVE_RETRY_AVAILABLE", "reason": "invalid artifact"}
        return {"decision": "BLOCKED", "reason": "invalid artifact retry budget reached", "stop_reason": "RETRY_BUDGET_REACHED"}
    if status in {"VALIDATED", "STATE_COMMITTED"} and value["state_commit"]["state_path"]:
        try:
            actual_hash = state_commit_hash(value)
        except JournalError as exc:
            return {"decision": "BLOCKED", "reason": str(exc), "stop_reason": exc.code, "incident_required": exc.code}
        before = value["state_commit"]["expected_before_hash"]
        after = value["state_commit"]["expected_after_hash"]
        committed = value["state_commit"]["committed_after_hash"]
        if status == "VALIDATED":
            if actual_hash == after: return {"decision": "ALREADY_COMMITTED", "reason": "STATE is persisted; reconcile journal only"}
            if actual_hash == before: return {"decision": "STATE_COMMIT_REQUIRED", "reason": "STATE is not yet persisted; controller reconciliation required"}
            return {"decision": "BLOCKED", "reason": "STATE differs from both prepared hashes", "stop_reason": "STATE_DESYNC", "incident_required": "STATE_DESYNC"}
        if value["state_commit"]["verified"] and actual_hash == committed:
            return {"decision": "ALREADY_COMMITTED", "reason": "verified STATE commit is still present; release only"}
        return {"decision": "BLOCKED", "reason": "Recorded state commit diverges from STATE", "stop_reason": "STATE_DESYNC", "incident_required": "STATE_DESYNC"}
    if status == "STATE_COMMITTED": return {"decision": "BLOCKED", "reason": "state commit lacks verifiable STATE metadata", "stop_reason": "STATE_DESYNC", "incident_required": "STATE_DESYNC"}
    if exists and status in {"DISPATCHED", "PROCESS_FINISHED", "ARTIFACT_READY", "VALIDATED"}: return {"decision": "RECONCILE_ARTIFACT", "reason": "artifact must be classified before dispatch"}
    if status == "PREPARED" and not value["process"]["started_at"] and not exists: return {"decision": "DISPATCH_ALLOWED", "reason": "prepared action has not started"}
    if status == "DISPATCHED" and not exists and value["process"]["exit_code"] is None: return {"decision": "WAIT_OR_MANUAL_REVIEW", "reason": "process result is unknown"}
    return {"decision": "BLOCKED", "reason": "journal state requires recovery"}


def dispatch_allowed(value: dict[str, Any]) -> bool:
    return recovery_decision(value)["decision"] == "DISPATCH_ALLOWED"


def require_dispatch_allowed(value: dict[str, Any]) -> None:
    decision = recovery_decision(value)
    if decision["decision"] != "DISPATCH_ALLOWED":
        raise JournalError("ARTIFACT_PENDING" if artifact_present(value) else "ACTION_RECOVERY_REQUIRED", decision["reason"])


def prepare_action(path: Path, next_value: dict[str, Any]) -> None:
    if path.exists():
        previous = load_journal(path)
        if not is_pristine_idle(previous):
            require_dispatch_allowed(previous)
            raise JournalError("ACTION_RECOVERY_REQUIRED", "Existing action requires recovery before a new action can be prepared.")
    validate_journal(next_value)
    if next_value["action"]["status"] != "PREPARED":
        raise JournalError("INVALID_TRANSITION", "New action must start PREPARED.")
    atomic_write(path, next_value)


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
    return identifier


def _save_transition(path: Path, target: str, update: dict[str, Any] | None = None) -> dict[str, Any]:
    value = transition(load_journal(path), target)
    if update:
        for section, fields in update.items(): value[section].update(fields)
    atomic_write(path, value)
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", required=True, help="Path to ACTION_JOURNAL.json.")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON.")
    parser.add_argument("--payload", help="JSON payload for init or prepare.")
    parser.add_argument("--started", action="store_true", help="Record executor dispatch start.")
    parser.add_argument("--finished", action="store_true", help="Record executor process completion.")
    parser.add_argument("--exit-code", type=int, help="Executor process exit code with --finished.")
    parser.add_argument("--state-path", help="Absolute non-symlink STATE path inside the journal workspace.")
    parser.add_argument("--expected-before-hash", help="SHA-256 of STATE before the controller writes it.")
    parser.add_argument("--expected-after-hash", help="SHA-256 expected after the controller atomically writes STATE.")
    parser.add_argument("--committed-after-hash", help="Compatibility check only; never trusted as proof of STATE persistence.")
    parser.add_argument("--history-dir", help="Append-only history root required by rollover or archival recovery.")
    parser.add_argument("--invalid-field", action="append", default=[], help="Field that made the artifact invalid; required by invalid classification.")
    parser.add_argument("command", choices=("init", "prepare", "record-process", "record-artifact", "mark-validated", "classify-invalid", "recover-blocked-invalid", "prepare-state-commit", "mark-state-committed", "release", "rollover", "archive-interrupted", "archive-invalid", "block", "inspect", "recover"))
    args = parser.parse_args(argv); path = Path(args.journal)
    try:
        if args.command == "init":
            if path.exists(): raise JournalError("JOURNAL_EXISTS", "Existing journal is never overwritten.")
            if not args.payload: raise JournalError("PAYLOAD_REQUIRED", "init requires --payload workspace JSON.")
            workspace = json.loads(args.payload); value = empty_journal(workspace); atomic_write(path, value)
        elif args.command == "prepare":
            if not args.payload: raise JournalError("PAYLOAD_REQUIRED", "prepare requires full --payload JSON.")
            prepare_action(path, json.loads(args.payload)); value = load_journal(path)
        elif args.command == "record-process":
            current = load_journal(path)
            if args.started and args.finished: raise JournalError("PAYLOAD_INVALID", "record-process accepts one of --started or --finished.")
            if args.started:
                value = _save_transition(path, "DISPATCHED", {"process": {"started_at": now()}})
            elif args.finished:
                if args.exit_code is None: raise JournalError("PAYLOAD_REQUIRED", "--finished requires --exit-code.")
                value = _save_transition(path, "PROCESS_FINISHED", {"process": {"finished_at": now(), "exit_code": args.exit_code}})
            else: raise JournalError("PAYLOAD_REQUIRED", "record-process requires --started or --finished.")
        elif args.command == "record-artifact":
            value = load_journal(path); artifact = Path(value["action"]["final_message_path"])
            value = _save_transition(path, "ARTIFACT_READY", {"artifact": {"exists": artifact.is_file(), "sha256": sha256(artifact) if artifact.is_file() else None}})
        elif args.command == "mark-validated": value = _save_transition(path, "VALIDATED", {"artifact": {"validation_status": "VALID"}})
        elif args.command == "classify-invalid": value = classify_invalid_artifact(path, args.invalid_field)
        elif args.command == "recover-blocked-invalid": value = recover_blocked_pending_artifact(path, args.invalid_field)
        elif args.command == "prepare-state-commit":
            if not args.state_path or not args.expected_before_hash or not args.expected_after_hash:
                raise JournalError("PAYLOAD_REQUIRED", "prepare-state-commit requires --state-path, --expected-before-hash, and --expected-after-hash.")
            value = prepare_state_commit(path, Path(args.state_path), args.expected_before_hash, args.expected_after_hash)
        elif args.command == "mark-state-committed":
            value = mark_state_committed(path, caller_expected_after_hash=args.expected_after_hash or args.committed_after_hash)
        elif args.command == "release": value = release_action(path)
        elif args.command == "rollover":
            if not args.history_dir: raise JournalError("PAYLOAD_REQUIRED", "rollover requires --history-dir.")
            value = rollover_journal(path, Path(args.history_dir))
        elif args.command == "archive-interrupted":
            if not args.history_dir: raise JournalError("PAYLOAD_REQUIRED", "archive-interrupted requires --history-dir.")
            value = archive_interrupted_journal(path, Path(args.history_dir))
        elif args.command == "archive-invalid":
            if not args.history_dir: raise JournalError("PAYLOAD_REQUIRED", "archive-invalid requires --history-dir.")
            value = archive_invalid_journal(path, Path(args.history_dir))
        elif args.command == "block": value = _save_transition(path, "BLOCKED")
        elif args.command == "inspect": value = load_journal(path)
        else: value = recovery_decision(load_journal(path))
        print(json.dumps(value, sort_keys=True) if args.json else value)
        return 0
    except (JournalError, json.JSONDecodeError) as exc:
        payload = {"status": getattr(exc, "code", "PAYLOAD_INVALID"), "message": str(exc)}
        print(json.dumps(payload, sort_keys=True) if args.json else payload)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
