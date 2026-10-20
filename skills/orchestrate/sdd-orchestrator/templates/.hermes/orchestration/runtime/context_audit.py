"""Private, recoverable audit storage for context-engine runs.

Each run that reaches the evaluator stores the original input bytes, its SHA-256,
one JSONL record per segment decision and the final metrics. Directories are 0700,
files are 0600 and written atomically. ``recover`` returns the exact original bytes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import tempfile
import time
from pathlib import Path
from typing import Any

RUN_ID = re.compile(r"^[0-9a-f]{8,16}_[A-Za-z0-9_-]{16}$")


def _private_dir(path: Path) -> None:
    """Create ``path`` 0700, or accept an existing directory only if it is already owner-private."""
    if path.is_symlink():
        raise ValueError("STATE_DIR_NOT_PRIVATE")
    if path.exists():
        metadata = path.stat()
        if not path.is_dir() or stat.S_IMODE(metadata.st_mode) & 0o077 or (hasattr(os, "getuid") and metadata.st_uid != os.getuid()):
            raise ValueError("STATE_DIR_NOT_PRIVATE")
        return
    path.mkdir(parents=True, mode=0o700)
    os.chmod(path, 0o700)


def _write(path: Path, data: bytes) -> None:
    _private_dir(path.parent)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _run_dir(root: str | Path, run_id: str) -> Path:
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise ValueError("AUDIT_RUN_ID_INVALID")
    return Path(root) / "audit" / run_id


class ContextAudit:
    """One private audit directory per run."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.run: str | None = None

    def start(self, raw: bytes) -> str:
        """Create a unique run, store the original bytes and their digest, and return the run id."""
        _private_dir(self.root)
        _private_dir(self.root / "audit")
        self.run = f"{int(time.time()):x}_{secrets.token_urlsafe(12)}"
        location = self.root / "audit" / self.run
        _private_dir(location)
        _write(location / "original.bin", raw)
        _write(location / "sha256", hashlib.sha256(raw).hexdigest().encode("ascii"))
        return self.run

    def record(self, decision: dict[str, Any]) -> None:
        """Append one decision record (metadata only) to the run's private JSONL log."""
        path = _run_dir(self.root, self.run or "") / "decisions.jsonl"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            os.write(descriptor, json.dumps(decision, sort_keys=True).encode("utf-8") + b"\n")
        finally:
            os.close(descriptor)

    def finish(self, metrics: dict[str, Any]) -> None:
        """Persist the run's final metrics."""
        _write(_run_dir(self.root, self.run or "") / "metrics.json", json.dumps(metrics, sort_keys=True).encode("utf-8"))


def recover(root: str | Path, run_id: str) -> bytes:
    """Return the run's original bytes after validating the run id and the stored SHA-256."""
    location = _run_dir(root, run_id)
    raw = (location / "original.bin").read_bytes()
    if hashlib.sha256(raw).hexdigest() != (location / "sha256").read_text(encoding="ascii").strip():
        raise ValueError("AUDIT_SHA256_MISMATCH")
    return raw


def purge(root: str | Path, older_than_days: int) -> int:
    """Delete audit runs older than ``older_than_days`` days and return how many were removed."""
    if older_than_days < 0:
        raise ValueError("PURGE_DAYS_INVALID")
    audit = Path(root) / "audit"
    if not audit.is_dir():
        return 0
    cutoff = time.time() - older_than_days * 86400
    removed = 0
    for entry in audit.iterdir():
        if entry.is_dir() and not entry.is_symlink() and RUN_ID.fullmatch(entry.name) and entry.stat().st_mtime < cutoff:
            shutil.rmtree(entry)
            removed += 1
    return removed
