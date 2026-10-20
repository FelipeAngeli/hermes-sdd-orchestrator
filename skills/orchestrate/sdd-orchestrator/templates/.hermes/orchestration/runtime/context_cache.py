"""Private, metadata-only verdict cache for the context engine.

Keys hash the segment SHA-256 with the task, the user constraints, the full policy,
the evaluator and the engine version, so changing any of them is a miss. Entries
hold only the verdict, the two probabilities and a timestamp, never segment text.
A missing, corrupt or malformed file is an empty cache.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable


def _valid_entry(value: Any) -> bool:
    if not isinstance(value, dict) or value.get("verdict") not in ("OMIT", "PRESERVE"):
        return False
    stamp = value.get("timestamp")
    if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not math.isfinite(stamp):
        return False
    probabilities = value.get("probabilities")
    return isinstance(probabilities, dict)


class ContextCache:
    """Verdicts keyed by every evaluator-visible identity input."""

    def __init__(self, path: str | Path, ttl_seconds: int, max_entries: int, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self.ttl = ttl_seconds
        self.maximum = max_entries
        self.clock = clock
        self.entries: dict[str, dict[str, Any]] = {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            return
        if isinstance(value, dict) and isinstance(value.get("entries"), dict):
            self.entries = {key: item for key, item in value["entries"].items() if isinstance(key, str) and _valid_entry(item)}

    @staticmethod
    def key(*parts: str) -> str:
        """Hash the identity parts into one cache key."""
        return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode("utf-8")).hexdigest()

    def get(self, key: str) -> dict[str, Any] | None:
        """Return an unexpired entry, or ``None``."""
        value = self.entries.get(key)
        if value is None:
            return None
        if self.clock() - value["timestamp"] > self.ttl:
            del self.entries[key]
            return None
        return value

    def put(self, key: str, verdict: str, probabilities: dict[str, float]) -> None:
        """Store a valid evaluator verdict (never segment text)."""
        self.entries[key] = {"verdict": verdict, "probabilities": dict(probabilities), "timestamp": self.clock()}

    def save(self) -> None:
        """Atomically write the newest ``max_entries`` entries with owner-only permissions."""
        newest = sorted(self.entries.items(), key=lambda item: item[1]["timestamp"], reverse=True)[: self.maximum]
        from context_audit import _private_dir

        _private_dir(self.path.parent)
        descriptor, temporary = tempfile.mkstemp(dir=self.path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump({"entries": dict(newest)}, output, sort_keys=True)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
