"""Durable storage for loop executions: atomic checkpoints, an ordered event log and locks.

Layout under the store directory passed by the caller (never a default inside a repository):

    <store>/<execution_id>/definition.json   normalized LoopDefinition (immutable)
    <store>/<execution_id>/execution.json    LoopExecution with its LoopCheckpoint (atomic replace)
    <store>/<execution_id>/events.jsonl      append-only transition log (write-ahead of execution.json)
    <store>/<execution_id>/runner.lock       exclusive lock held by the single runner or a control command
    <store>/<execution_id>/cancel-request.json  cancellation requested while a runner holds the lock

The store root and every execution directory are owner-private (0700, owned by the
current user, never a symlink); files are written 0600 and never through a symlink.

Every event carries ``state_sha256``, the canonical hash of the execution it commits, so
an edit of execution.json alone no longer matches the last event (``loop_state.verify_integrity``).

Write order for every checkpoint: write the new state to execution.pending.json, append
the event (fsync), then replace execution.json (fsync file and directory). After a crash
a caller holding the runner lock (``repair=True``) rolls forward to the pending state
when the trailing event is complete, or trims a torn line; a lock-free reader such as
``status`` writes nothing.
"""
from __future__ import annotations

import contextlib
import errno
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any, Iterator

import loop_model

if os.name == "posix":
    import fcntl
else:  # pragma: no cover - exercised only on Windows
    import msvcrt

ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def private_dir(path: Path, *, create: bool) -> None:
    """Accept only an owner-private real directory; create it 0700 when asked."""
    if path.is_symlink():
        raise loop_model.LoopError("STORE_PATH_UNSAFE", f"{path} is a symlink.", "Use a real, owner-private directory.")
    if not path.exists():
        if not create:
            raise loop_model.LoopError("EXECUTION_NOT_FOUND", f"{path} does not exist.", "Check --store and --execution.")
        path.mkdir(parents=True, mode=0o700)
        os.chmod(path, 0o700)
        return
    metadata = path.stat()
    foreign = hasattr(os, "getuid") and metadata.st_uid != os.getuid()
    if not path.is_dir() or (os.name == "posix" and stat.S_IMODE(metadata.st_mode) & 0o077) or foreign:
        raise loop_model.LoopError("STORE_PATH_UNSAFE", f"{path} is not an owner-private (0700) directory of this user.",
                                   f"chmod 700 {path} (and make sure you own it), or use another store.")


def _tampered(message: str) -> loop_model.LoopError:
    return loop_model.LoopError("EXECUTION_TAMPERED", message,
                                "execution.json or events.jsonl was changed outside the loop runtime; restore both from the same backup.")


def _regular(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise loop_model.LoopError("STORE_PATH_UNSAFE", f"{path} is a symlink or not a regular file.", "Restore the file; never link it.")


def _replace_atomically(path: Path, data: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
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


def atomic_write_json(path: Path, value: Any) -> None:
    _regular(path)
    _replace_atomically(path, json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False).encode("utf-8"))


def _read_text(path: Path) -> str:
    _regular(path)
    descriptor = os.open(path, os.O_RDONLY | NOFOLLOW)
    with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
        return handle.read()


class LoopStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def directory(self, execution_id: str) -> Path:
        if not isinstance(execution_id, str) or not ID_PATTERN.fullmatch(execution_id):
            raise loop_model.LoopError("EXECUTION_ID_INVALID", f"{execution_id!r} is not a safe execution id.",
                                       "Use 1-128 letters, digits, '.', '_' or '-', starting with a letter or digit.")
        path = self.root / execution_id
        if path.is_symlink():
            raise loop_model.LoopError("STORE_PATH_UNSAFE", f"{path} is a symlink.", "Use a real directory for the store.")
        return path

    def exists(self, execution_id: str) -> bool:
        path = self.directory(execution_id) / "execution.json"
        return path.is_file() and not path.is_symlink()

    def require(self, execution_id: str) -> Path:
        """The execution directory, checked owner-private; never created (control commands use this)."""
        if not self.exists(execution_id):
            raise loop_model.LoopError("EXECUTION_NOT_FOUND", f"no execution {execution_id!r} in {self.root}.", "Check --store and --execution.")
        private_dir(self.root, create=False)
        directory = self.directory(execution_id)
        private_dir(directory, create=False)
        return directory

    def create(self, execution_id: str, definition: dict[str, Any], execution: dict[str, Any]) -> None:
        """Build the execution in a private staging directory, then rename it into place in one step,
        so a crash during ``start`` never leaves a half-created execution behind."""
        private_dir(self.root, create=True)
        directory = self.directory(execution_id)
        if directory.exists():
            raise FileExistsError(directory)
        staging = Path(tempfile.mkdtemp(prefix=f".creating-{execution_id}.", dir=self.root))
        try:
            os.chmod(staging, 0o700)
            atomic_write_json(staging / "definition.json", definition)
            self._commit_in(staging, execution, "EXECUTION_STARTED", {})
            try:
                os.rename(staging, directory)
            except OSError as error:
                if error.errno in (errno.EEXIST, errno.ENOTEMPTY):
                    raise FileExistsError(directory) from error
                raise
        except BaseException:
            for child in staging.iterdir() if staging.exists() else ():
                with contextlib.suppress(OSError):
                    child.unlink()
            with contextlib.suppress(OSError):
                staging.rmdir()
            raise
        _fsync_directory(self.root)

    def load_definition(self, execution_id: str, expected_sha256: str) -> dict[str, Any]:
        path = self.require(execution_id) / "definition.json"
        try:
            raw = json.loads(_read_text(path))
        except (OSError, ValueError) as error:
            raise loop_model.LoopError("EXECUTION_CORRUPT", f"definition.json is unreadable: {error}",
                                       "Restore the execution directory from a backup; never edit it by hand.") from error
        if loop_model.digest(raw) != expected_sha256:
            raise loop_model.LoopError("DEFINITION_CHANGED", "definition.json no longer matches the hash recorded at start.",
                                       "Restore the original definition or start a new execution; a running loop never adopts a changed plan.")
        return raw

    def load(self, execution_id: str, *, repair: bool = False) -> dict[str, Any]:
        """Read an execution. ``repair`` (runner lock held) trims uncommitted events; otherwise nothing is written."""
        directory = self.require(execution_id)
        try:
            value = json.loads(_read_text(directory / "execution.json"))
        except (OSError, ValueError) as error:
            raise loop_model.LoopError("EXECUTION_CORRUPT", f"execution.json is unreadable: {error}",
                                       "Restore the execution directory from a backup; never edit it by hand.") from error
        errors = loop_model.schema_errors(value, loop_model.EXECUTION_SCHEMA_PATH)
        if errors:
            raise loop_model.LoopError("EXECUTION_CORRUPT", "; ".join(errors), "Restore the execution directory from a backup.")
        if value["execution_id"] != execution_id:
            raise loop_model.LoopError("EXECUTION_CORRUPT", "execution.json belongs to another execution id.", "Restore the directory.")
        if repair and self._reconcile_events(directory, value["checkpoint"]["seq"]):
            return self.load(execution_id, repair=True)
        return value

    def _reconcile_events(self, directory: Path, committed: int) -> bool:
        """Bring execution.json and events.jsonl back to one committed state; True when it rolled forward.

        A crash leaves at most one trailing line after the checkpoint. A torn (unparseable)
        line is trimmed. A complete ``checkpoint + 1`` event is never thrown away: the state
        it commits was written to ``execution.pending.json`` before the event, so recovery
        rolls forward to it when its hash matches, and refuses (EXECUTION_TAMPERED) otherwise.
        """
        path = directory / "events.jsonl"
        if not path.exists():
            return False
        _regular(path)
        lines = [line for line in path.read_bytes().split(b"\n") if line.strip()]
        seqs: list[int | None] = []
        parsed: list[dict[str, Any] | None] = []
        for line in lines:
            try:
                item = json.loads(line.decode("utf-8"))
                seqs.append(int(item["seq"]))
                parsed.append(item)
            except (ValueError, KeyError, TypeError, UnicodeDecodeError):
                seqs.append(None)
                parsed.append(None)
        if committed not in seqs:
            raise _tampered("the event of the committed checkpoint is missing from events.jsonl.")
        position = len(seqs) - 1 - seqs[::-1].index(committed)
        if seqs[:position + 1] != list(range(1, committed + 1)):
            raise _tampered("events.jsonl is not the contiguous sequence 1..checkpoint.")
        tail = seqs[position + 1:]
        # A crash leaves at most one trailing line: the next event (seq = checkpoint + 1) or a torn write.
        if len(tail) > 1 or (tail and tail[0] not in (None, committed + 1)):
            raise _tampered(f"{len(tail)} event(s) follow the committed checkpoint; an older execution.json was restored.")
        if not tail:
            return False
        if tail[0] is None:
            _replace_atomically(path, b"".join(line + b"\n" for line in lines[:position + 1]))
            return False
        pending_path = directory / "execution.pending.json"
        try:
            pending = json.loads(_read_text(pending_path))
        except (OSError, ValueError, loop_model.LoopError):
            pending = None
        if pending is None or loop_model.digest(pending) != parsed[-1].get("state_sha256"):
            raise _tampered(f"event {committed + 1} is complete but execution.json is at {committed}, and no matching pending state exists.")
        atomic_write_json(directory / "execution.json", pending)
        return True

    def commit(self, execution: dict[str, Any], event: str, data: dict[str, Any]) -> None:
        """Write-ahead the event, then atomically replace execution.json with the new checkpoint."""
        self._commit_in(self.directory(execution["execution_id"]), execution, event, data)

    def _commit_in(self, directory: Path, execution: dict[str, Any], event: str, data: dict[str, Any]) -> None:
        previous = execution["checkpoint"]
        seq = previous["seq"] + 1
        at = execution["updated_at"]
        execution["checkpoint"] = {"seq": seq, "at": at, "event": event}
        try:
            record = {**data, "seq": seq, "at": at, "event": event, "state_sha256": loop_model.digest(execution)}
            line = json.dumps(record, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError) as error:
            execution["checkpoint"] = previous
            raise loop_model.LoopError("STATE_NOT_SERIALIZABLE", f"the execution cannot be saved as canonical JSON: {error}",
                                       "Return JSON-serializable outputs with string keys from handlers.") from error
        atomic_write_json(directory / "execution.pending.json", execution)
        events = directory / "events.jsonl"
        _regular(events)
        descriptor = os.open(events, os.O_WRONLY | os.O_APPEND | os.O_CREAT | NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        atomic_write_json(directory / "execution.json", execution)

    def events(self, execution_id: str) -> list[dict[str, Any]]:
        """Events in order; an unreadable line is reported as ``{"event": "UNREADABLE"}`` instead of raising."""
        path = self.directory(execution_id) / "events.jsonl"
        if not path.exists():
            return []
        _regular(path)
        found: list[dict[str, Any]] = []
        for line in path.read_bytes().split(b"\n"):
            if not line.strip():
                continue
            try:
                found.append(json.loads(line.decode("utf-8")))
            except (ValueError, UnicodeDecodeError):
                found.append({"event": "UNREADABLE"})
        return found

    @contextlib.contextmanager
    def runner_lock(self, execution_id: str) -> Iterator[None]:
        """Exclusive, non-blocking lock on an existing execution: one runner or control command at a time."""
        directory = self.require(execution_id)
        lock = directory / "runner.lock"
        _regular(lock)
        descriptor = os.open(lock, os.O_RDWR | os.O_CREAT | NOFOLLOW, 0o600)
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
            return json.loads(_read_text(path))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, loop_model.LoopError):
            return {"by": "unknown", "reason": "unreadable cancel request", "at": None}

    def clear_cancel_request(self, execution_id: str) -> None:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self.cancel_request_path(execution_id))
