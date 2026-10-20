"""Durable storage for loop executions: atomic checkpoints, an ordered event log and locks.

Layout under the store directory passed by the caller (never a default inside a repository):

    <store>/<execution_id>/definition.json   normalized LoopDefinition (immutable)
    <store>/<execution_id>/execution.json    LoopExecution with its LoopCheckpoint (atomic replace)
    <store>/<execution_id>/events.jsonl      append-only transition log (write-ahead of execution.json)
    <store>/<execution_id>/runner.lock       exclusive lock held by the single runner or a control command
    <store>/<execution_id>/cancel-request.json  cancellation requested while a runner holds the lock

Write order for every checkpoint: append the event (fsync), then replace execution.json
(fsync file and directory). An event whose ``seq`` is above the committed checkpoint
is uncommitted and is truncated on the next load, so the log never claims a
transition the execution file does not hold.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterator

import loop_model

if os.name == "posix":
    import fcntl
else:  # pragma: no cover - exercised only on Windows
    import msvcrt

ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_json(path: Path, value: Any) -> None:
    data = json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False).encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise
    _fsync_directory(path.parent)


class LoopStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def directory(self, execution_id: str) -> Path:
        if not isinstance(execution_id, str) or not ID_PATTERN.match(execution_id):
            raise loop_model.LoopError("EXECUTION_ID_INVALID", f"{execution_id!r} is not a safe execution id.",
                                       "Use 1-128 letters, digits, '.', '_' or '-', starting with a letter or digit.")
        path = self.root / execution_id
        if path.is_symlink():
            raise loop_model.LoopError("STORE_PATH_UNSAFE", f"{path} is a symlink.", "Use a real directory for the store.")
        return path

    def exists(self, execution_id: str) -> bool:
        return (self.directory(execution_id) / "execution.json").is_file()

    def create(self, execution_id: str, definition: dict[str, Any], execution: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        directory = self.directory(execution_id)
        directory.mkdir(mode=0o700)
        atomic_write_json(directory / "definition.json", definition)
        self.commit(execution, "EXECUTION_STARTED", {})

    def load_definition(self, execution_id: str, expected_sha256: str) -> dict[str, Any]:
        path = self.directory(execution_id) / "definition.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise loop_model.LoopError("EXECUTION_CORRUPT", f"definition.json is unreadable: {error}",
                                       "Restore the execution directory from a backup; never edit it by hand.") from error
        if loop_model.digest(raw) != expected_sha256:
            raise loop_model.LoopError("DEFINITION_CHANGED", "definition.json no longer matches the hash recorded at start.",
                                       "Restore the original definition or start a new execution; a running loop never adopts a changed plan.")
        return raw

    def load(self, execution_id: str) -> dict[str, Any]:
        directory = self.directory(execution_id)
        path = directory / "execution.json"
        if not path.is_file():
            raise loop_model.LoopError("EXECUTION_NOT_FOUND", f"no execution {execution_id!r} in {self.root}.", "Check --store and --execution.")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise loop_model.LoopError("EXECUTION_CORRUPT", f"execution.json is unreadable: {error}",
                                       "Restore the execution directory from a backup; never edit it by hand.") from error
        errors = loop_model.schema_errors(value, loop_model.EXECUTION_SCHEMA_PATH)
        if errors:
            raise loop_model.LoopError("EXECUTION_CORRUPT", "; ".join(errors), "Restore the execution directory from a backup.")
        self._truncate_uncommitted_events(directory, value["checkpoint"]["seq"])
        return value

    def _truncate_uncommitted_events(self, directory: Path, committed: int) -> None:
        path = directory / "events.jsonl"
        if not path.is_file():
            return
        kept: list[str] = []
        changed = False
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                seq = json.loads(line)["seq"]
            except (ValueError, KeyError, TypeError):
                changed = True
                break
            if seq > committed:
                changed = True
                break
            kept.append(line)
        if changed:
            temporary = directory / ".events.jsonl.tmp"
            temporary.write_text("".join(item + "\n" for item in kept), encoding="utf-8")
            os.replace(temporary, path)
            _fsync_directory(directory)

    def commit(self, execution: dict[str, Any], event: str, data: dict[str, Any]) -> None:
        """Write-ahead the event, then atomically replace execution.json with the new checkpoint."""
        directory = self.directory(execution["execution_id"])
        seq = execution["checkpoint"]["seq"] + 1
        at = execution["updated_at"]
        execution["checkpoint"] = {"seq": seq, "at": at, "event": event}
        line = json.dumps({"seq": seq, "at": at, "event": event, **data}, sort_keys=True, ensure_ascii=False)
        with open(directory / "events.jsonl", "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        atomic_write_json(directory / "execution.json", execution)

    def events(self, execution_id: str) -> list[dict[str, Any]]:
        path = self.directory(execution_id) / "events.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    @contextlib.contextmanager
    def runner_lock(self, execution_id: str) -> Iterator[None]:
        """Exclusive, non-blocking lock: one runner or control command per execution at a time."""
        directory = self.directory(execution_id)
        directory.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(directory / "runner.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                if os.name == "posix":
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:  # pragma: no cover
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise loop_model.LoopError("LOOP_ALREADY_RUNNING", f"execution {execution_id} is held by another runner or command.",
                                           "Wait for the other runner to stop, or request cancellation with `loop_cli.py cancel`.") from error
            yield
        finally:
            os.close(descriptor)

    def cancel_request_path(self, execution_id: str) -> Path:
        return self.directory(execution_id) / "cancel-request.json"

    def read_cancel_request(self, execution_id: str) -> dict[str, Any] | None:
        path = self.cancel_request_path(execution_id)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return {"by": "unknown", "reason": "unreadable cancel request", "at": None}

    def clear_cancel_request(self, execution_id: str) -> None:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self.cancel_request_path(execution_id))
