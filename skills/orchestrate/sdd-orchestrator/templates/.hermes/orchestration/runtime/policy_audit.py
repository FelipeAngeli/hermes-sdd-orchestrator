"""SecurityAudit: an append-only, hash-chained JSONL record of every policy decision.

Each record carries ``prev_sha256`` (the previous record's ``sha256``, or 64 zeros)
and its own ``sha256`` over the canonical JSON of everything else, so editing,
deleting or reordering any record breaks the chain at that point; ``verify`` names
the first broken record. Appends hold an exclusive ``flock`` on the log, so
concurrent hooks cannot fork the chain. Records keep only codes, ids and hashes:
command text and free text are never stored (only their SHA-256), and every string
passes ``redaction.redact`` as a last net.

The chain proves integrity against edits that do not also rewrite every later hash;
an attacker who can rewrite the whole file can forge a new chain. ``verify`` also
returns the head hash so a caller can pin it somewhere the agent cannot write.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

import redaction

# Short key names redaction.redact leaves alone (pass=, pw:, pin=); the audit never needs their values.
_SHORT_SECRET = re.compile(r"(?i)(?<![A-Za-z0-9])(pass|pw|pin|otp|code)(\s*[=:]\s*)[^\s,;&]+")

GENESIS = "0" * 64
LOG_NAME = "security-audit.jsonl"
MAX_FIELD = 120
MAX_RECORD_BYTES = 8192


def _clean(value: Any, depth: int = 0) -> Any:
    if depth > 3:
        return None
    if isinstance(value, str):
        return _SHORT_SECRET.sub(r"\1\2[REDACTED]", redaction.redact(value[:MAX_FIELD]))
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_clean(item, depth + 1) for item in list(value)[:20]]
    if isinstance(value, dict):
        return {str(key)[:40]: _clean(item, depth + 1) for key, item in list(value.items())[:30]}
    return None


def _digest(body: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()


def _open_private(path: Path, flags: int) -> int:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0), 0o600)
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        os.close(descriptor)
        raise OSError(f"{path.name} must be a private regular file (0600)")
    return descriptor


def _last_hash(handle: Any) -> str:
    handle.seek(0, os.SEEK_END)
    size = handle.tell()
    if size == 0:
        return GENESIS
    handle.seek(max(0, size - MAX_RECORD_BYTES - 2))
    lines = handle.read().splitlines()
    record = json.loads(lines[-1].decode("utf-8"))
    return record["sha256"]


class SecurityAudit:
    def __init__(self, state_dir: str | Path) -> None:
        self.path = Path(state_dir) / LOG_NAME

    def append(self, event: str, **fields: Any) -> dict[str, Any]:
        """Append one record under an exclusive lock; returns it (with its ``sha256``)."""
        descriptor = _open_private(self.path, os.O_RDWR | os.O_CREAT | os.O_APPEND)
        with os.fdopen(descriptor, "r+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                body = {"event": str(event)[:60], **{key: _clean(value) for key, value in fields.items()},
                        "prev_sha256": _last_hash(handle)}
                record = {**body, "sha256": _digest(body)}
                line = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"
                if len(line) > MAX_RECORD_BYTES:
                    raise ValueError("audit record too large")
                handle.seek(0, os.SEEK_END)
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return record

    def verify(self) -> dict[str, Any]:
        """``{"valid", "records", "head", "broken_at", "error"}``; a missing log is valid and empty."""
        if not self.path.exists():
            return {"valid": True, "records": 0, "head": GENESIS, "broken_at": None, "error": None}
        previous, count = GENESIS, 0
        try:
            descriptor = _open_private(self.path, os.O_RDONLY)
        except OSError as error:
            return {"valid": False, "records": 0, "head": None, "broken_at": 0, "error": f"unreadable: {error}"}
        with os.fdopen(descriptor, "rb") as handle:
            for number, raw in enumerate(handle, start=1):
                digest = None
                try:
                    record = json.loads(raw.decode("utf-8"))
                    body = {key: value for key, value in record.items() if key != "sha256"}
                    if record.get("prev_sha256") == previous and record.get("sha256") == _digest(body):
                        digest = record["sha256"]
                except (ValueError, UnicodeError, AttributeError):
                    digest = None
                if digest is None:
                    return {"valid": False, "records": count, "head": previous, "broken_at": number, "error": "chain broken"}
                previous, count = digest, number
        return {"valid": True, "records": count, "head": previous, "broken_at": None, "error": None}

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with os.fdopen(_open_private(self.path, os.O_RDONLY), "rb") as handle:
            return [json.loads(line) for line in handle if line.strip()]
