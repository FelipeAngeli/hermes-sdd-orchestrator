"""EvidenceCollector and DecisionLog: content-addressed evidence and an append-only, hash-chained decision log.

Evidence records are JSON objects stored once under ``<store>/evidence/<aa>/<sha256>.json``;
their reference is ``sha256:<hex>`` of the canonical JSON, so the same evidence always
has the same reference (idempotent) and a changed file no longer matches its reference
(``EvidenceError`` on read). Every record carries ``schema_version`` so formats can evolve.

Gate decisions are appended to ``<store>/decisions.jsonl``. Each line carries
``prev_hash`` (the previous line's ``record_hash``) and its own ``record_hash``; ``verify``
recomputes the chain, so an edited or deleted past decision is detected. Decisions are
never rewritten: a new evaluation is a new line with ``previous_decision_id``.

Every string in an evidence record is passed through ``redaction.redact`` before it is
stored (commands, output tails, artifact summaries). The store is owner-private (0700 directories, 0600 files) and written without following
symbolic links, reusing ``loop_store``'s helpers.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterator

import loop_model
import loop_store
import redaction

if os.name == "posix":
    import fcntl

EVIDENCE_VERSION = 1
GENESIS_HASH = "0" * 64


class EvidenceError(Exception):
    """Evidence or the decision log does not match its recorded hash."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def content_ref(record: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical(record)).hexdigest()


def _redacted(value: Any) -> Any:
    """Mask credential shapes in every string, so commands, output and artifacts never store a secret."""
    if isinstance(value, str):
        return redaction.redact(value)
    if isinstance(value, list):
        return [_redacted(item) for item in value]
    if isinstance(value, dict):
        return {key: _redacted(item) for key, item in value.items()}
    return value


def _private_store(root: Path) -> None:
    try:
        loop_store.private_dir(root, create=True)
    except loop_model.LoopError as error:
        raise EvidenceError("STORE_PATH_UNSAFE", error.message) from error


class EvidenceCollector:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def path_for(self, ref: str) -> Path:
        if not isinstance(ref, str) or not ref.startswith("sha256:") or len(ref) != 71 or not all(c in "0123456789abcdef" for c in ref[7:]):
            raise EvidenceError("EVIDENCE_REF_INVALID", f"{ref!r} is not a sha256 evidence reference.")
        digest = ref[7:]
        return self.root / "evidence" / digest[:2] / f"{digest}.json"

    def put(self, record: dict[str, Any]) -> str:
        """Store ``record`` once (every string redacted) and return its reference; storing it again is a no-op."""
        record = _redacted({"schema_version": EVIDENCE_VERSION, **record})
        ref = content_ref(record)
        path = self.path_for(ref)
        _private_store(self.root)
        for directory in (self.root / "evidence", path.parent):
            loop_store.private_dir(directory, create=True)
        if path.exists():
            self.get(ref)  # verifies the stored bytes
            return ref
        loop_store.atomic_write_json(path, record)
        return ref

    def get(self, ref: str) -> dict[str, Any] | None:
        path = self.path_for(ref)
        if not path.exists() and not path.is_symlink():
            return None
        try:
            record = json.loads(loop_store._read_text(path))
        except (OSError, ValueError, loop_model.LoopError) as error:
            raise EvidenceError("EVIDENCE_CORRUPT", f"{ref} is unreadable: {error}") from error
        if not isinstance(record, dict) or content_ref(record) != ref:
            raise EvidenceError("EVIDENCE_TAMPERED", f"{ref} no longer matches its content hash.")
        return record


def _record_hash(record: dict[str, Any]) -> str:
    body = {key: value for key, value in record.items() if key != "record_hash"}
    return hashlib.sha256(canonical(body)).hexdigest()


class DecisionLog:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.path = self.root / "decisions.jsonl"

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        _private_store(self.root)
        lock = self.root / "decisions.lock"
        descriptor = os.open(lock, os.O_RDWR | os.O_CREAT | loop_store.NOFOLLOW, 0o600)
        try:
            if os.name == "posix":
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists() and not self.path.is_symlink():
            return []
        loop_store._regular(self.path)
        return [json.loads(line) for line in self.path.read_bytes().decode("utf-8").splitlines() if line.strip()]

    def verify(self) -> int:
        """Recompute the hash chain; return the number of decisions or raise ``EvidenceError``."""
        previous = GENESIS_HASH
        records = self.records()
        for index, record in enumerate(records, start=1):
            if record.get("prev_hash") != previous or record.get("record_hash") != _record_hash(record):
                raise EvidenceError("DECISION_LOG_TAMPERED", f"decision {index} does not match the hash chain.")
            previous = record["record_hash"]
        return len(records)

    def append(self, decision: dict[str, Any]) -> dict[str, Any]:
        with self._locked():
            self.verify()
            records = self.records()
            record = _redacted(dict(decision))
            record["prev_hash"] = records[-1]["record_hash"] if records else GENESIS_HASH
            record["record_hash"] = _record_hash(record)
            line = canonical(record) + b"\n"
            loop_store._regular(self.path)
            descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | loop_store.NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "ab") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
            return record

    def verified_records(self) -> list[dict[str, Any]]:
        """The records, after the whole hash chain is checked (raises ``DECISION_LOG_TAMPERED``)."""
        self.verify()
        return self.records()

    def find(self, idempotency_key: str) -> dict[str, Any] | None:
        """The latest decision with this key, read from a verified log."""
        for record in reversed(self.verified_records()):
            if record.get("idempotency_key") == idempotency_key:
                return record
        return None

    def history(self, gate_id: str, task_id: str) -> list[dict[str, Any]]:
        return [record for record in self.verified_records() if record.get("gate_id") == gate_id and record.get("task_id") == task_id]
