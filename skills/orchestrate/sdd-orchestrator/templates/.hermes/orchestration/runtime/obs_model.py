"""Event model, token provenance and cost estimation for the read-only observability engine.

Every observability event is a flat dict with the identifiers in ``IDS`` (``None`` when a
source does not have one), an ``event_type``, a ``timestamp`` (epoch seconds) and
``attrs``. Events are derived on demand from the stores the runtime already writes; this
module never persists them, so there is no second source of truth.

Token figures always carry a ``method``: ``MEASURED`` (reported by a provider or CLI),
``ESTIMATED`` (bytes divided by bytes-per-token, or a price-table computation) or
``SIMULATED`` (scripted by a benchmark). The three are summed separately and never mixed.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Iterable

RUNTIME = Path(__file__).resolve().parent
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from redaction import redact  # noqa: E402

IDS = ("trace_id", "run_id", "loop_id", "session_id", "agent_id", "parent_agent_id", "task_id", "stage")
METHODS = ("MEASURED", "ESTIMATED", "SIMULATED")
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
#: Free text that may hold prompts, goals, human quotes or tool output never enters an event.
FORBIDDEN_ATTRS = frozenset({
    "prompt", "goal", "goals", "objective", "summary", "quote", "output", "outputs", "partial", "message",
    "context", "content", "body", "title", "human_input", "hypothesis", "description", "stderr_tail",
    "path", "cwd", "final_message_path", "schema_path", "state_path", "workspace", "request", "evidence",
})
MAX_TEXT = 160
DEFAULT_PRICES = RUNTIME.parent / "policies" / "PRICES.json"


def trace_id(*parts: Any) -> str:
    """A stable trace id derived from the run identity, so re-reading a store yields the same id."""
    material = "\x1f".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _clean(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return None
    if isinstance(value, str):
        return redact(value)[:MAX_TEXT]
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    if isinstance(value, dict):
        return {str(key): _clean(item, depth + 1) for key, item in value.items() if str(key) not in FORBIDDEN_ATTRS}
    if isinstance(value, (list, tuple)):
        return [_clean(item, depth + 1) for item in list(value)[:64]]
    return redact(str(value))[:MAX_TEXT]


def event(event_type: str, timestamp: Any, *, source: str, attrs: dict[str, Any] | None = None, **ids: Any) -> dict[str, Any]:
    """Build one sanitized event; unknown identifier names are rejected."""
    unknown = set(ids) - set(IDS)
    if unknown:
        raise ValueError(f"OBS_ID_UNKNOWN: {sorted(unknown)}")
    record: dict[str, Any] = {name: (_clean(ids.get(name)) if ids.get(name) is not None else None) for name in IDS}
    record["event_type"] = event_type
    record["timestamp"] = float(timestamp) if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool) else None
    record["source"] = source
    record["attrs"] = _clean(attrs or {})
    return record


def tokens(method: str, source: str, **values: Any) -> dict[str, Any]:
    """A token record; non-integer or negative values become ``None`` (unknown), never zero."""
    if method not in METHODS:
        raise ValueError("OBS_TOKEN_METHOD_INVALID")
    record: dict[str, Any] = {"method": method, "source": source}
    for name in (*TOKEN_FIELDS, "total_tokens"):
        value = values.get(name)
        record[name] = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    if record["total_tokens"] is None and record["input_tokens"] is not None and record["output_tokens"] is not None:
        record["total_tokens"] = record["input_tokens"] + record["output_tokens"]
    return record


def add_tokens(bucket: dict[str, dict[str, int]], record: dict[str, Any] | None) -> None:
    """Accumulate a token record into ``bucket[method]``; unknown fields are not counted as zero."""
    if not record:
        return
    target = bucket.setdefault(record["method"], {name: 0 for name in (*TOKEN_FIELDS, "total_tokens")} | {"records": 0, "unknown_fields": 0})
    target["records"] += 1
    for name in (*TOKEN_FIELDS, "total_tokens"):
        if record.get(name) is None:
            target["unknown_fields"] += 1
        else:
            target[name] += record[name]


def load_prices(path: Path | str | None = None) -> dict[str, Any]:
    """Load the versioned price table; a missing or malformed table yields an empty one (everything unpriced)."""
    location = Path(path) if path else DEFAULT_PRICES
    try:
        raw = location.read_bytes()
        table = json.loads(raw)
    except (OSError, ValueError):
        return {"version": None, "sha256": None, "models": {}, "error": "PRICE_TABLE_UNAVAILABLE"}
    if not isinstance(table, dict) or not isinstance(table.get("models"), dict):
        return {"version": None, "sha256": None, "models": {}, "error": "PRICE_TABLE_INVALID"}
    table["sha256"] = hashlib.sha256(raw).hexdigest()
    return table


def price_for(prices: dict[str, Any], model: str | None) -> dict[str, Any] | None:
    if not model:
        return None
    models = prices.get("models") or {}
    entry = models.get(model)
    if entry is None:
        for alias, target in (prices.get("aliases") or {}).items():
            if alias == model:
                entry = models.get(target)
    return entry if isinstance(entry, dict) and isinstance(entry.get("input"), (int, float)) else None


def estimate_cost(record: dict[str, Any] | None, model: str | None, prices: dict[str, Any]) -> dict[str, Any]:
    """USD list-price estimate for a token record; never a bill, and ``None`` when anything needed is unknown."""
    price = price_for(prices, model)
    if not record or price is None:
        return {"usd": None, "method": "UNPRICED", "reason": "NO_PRICE" if record else "NO_TOKENS"}
    inp, out = record.get("input_tokens"), record.get("output_tokens")
    if inp is None or out is None:
        return {"usd": None, "method": "UNPRICED", "reason": "TOKEN_SPLIT_UNKNOWN"}
    read, write = record.get("cache_read_tokens") or 0, record.get("cache_write_tokens") or 0
    usd = (inp * price["input"] + out * price["output"] + read * price.get("cache_read", price["input"])
           + write * price.get("cache_write", price["input"])) / 1_000_000
    return {"usd": round(usd, 6), "method": "ESTIMATED", "token_method": record["method"], "price_version": prices.get("version")}


def percentile(values: Iterable[float], fraction: float) -> float | None:
    ordered = sorted(value for value in values if isinstance(value, (int, float)))
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return round(ordered[index], 3)
