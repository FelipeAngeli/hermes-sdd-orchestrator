#!/usr/bin/env python3
"""Fail-closed reader of the ``opencode run --format json`` event stream.

OpenCode 1.18.34 prints one JSON event per line: ``step_start``, ``text`` (``part.text``),
``tool_use``, ``reasoning``, ``step_finish`` (``part.reason``, ``part.tokens``, ``part.cost``)
and ``error``. The worker's final message is the text of the last step, which must end
with ``reason: "stop"``. Exactly one final result is accepted; every other shape is one
explicit outcome class that the launcher maps onto its existing statuses:

``OK`` → ``ARTIFACT_READY``; ``INVALID_OUTPUT`` → ``OUTPUT_INVALID`` (kept as evidence);
``PARTIAL`` → ``EXECUTOR_NO_OUTPUT``; ``ERROR_EVENT`` and ``CRASH`` → ``EXECUTOR_FAILED``;
``TIMEOUT`` → ``EXECUTOR_TIMEOUT`` (decided by the launcher's own clock).
"""
from __future__ import annotations

import json
import math
from typing import Any

from executor_transport import normalize_codex_last_message

OUTCOMES = ("OK", "INVALID_OUTPUT", "PARTIAL", "TIMEOUT", "CRASH", "ERROR_EVENT")
OUTCOME_STATUS = {
    "OK": "ARTIFACT_READY", "INVALID_OUTPUT": "OUTPUT_INVALID", "PARTIAL": "EXECUTOR_NO_OUTPUT",
    "TIMEOUT": "EXECUTOR_TIMEOUT", "CRASH": "EXECUTOR_FAILED", "ERROR_EVENT": "EXECUTOR_FAILED",
}
FINISH_REASON = "stop"


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def parse_events(stdout: bytes) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Every line must be one JSON object with a string ``type``; otherwise (None, reason)."""
    events: list[dict[str, Any]] = []
    for number, line in enumerate(stdout.decode("utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return None, f"line {number} is not JSON"
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            return None, f"line {number} is not an event object"
        events.append(event)
    return events, None


def usage(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Sum of every ``step_finish`` token counter and cost (``MEASURED``), or None without one."""
    finishes = [event.get("part") or {} for event in events if event["type"] == "step_finish"]
    if not finishes:
        return None
    totals: dict[str, Any] = {"method": "MEASURED", "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
                              "cache_write_tokens": 0, "cost_usd": 0.0, "num_turns": len(finishes)}
    for part in finishes:
        tokens = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
        cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
        for key, value in (("input_tokens", tokens.get("input")), ("output_tokens", tokens.get("output")),
                           ("cache_read_tokens", cache.get("read")), ("cache_write_tokens", cache.get("write"))):
            counted = _count(value)
            totals[key] = None if counted is None or totals[key] is None else totals[key] + counted
        cost = part.get("cost")
        valid = isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0
        totals["cost_usd"] = None if not valid or totals["cost_usd"] is None else round(totals["cost_usd"] + cost, 9)
    return totals


def classify(stdout: bytes, exit_code: int, timed_out: bool = False) -> tuple[str, bytes | None, dict[str, Any]]:
    """Return (outcome, final bytes or None, diagnostics) for one finished OpenCode process."""
    if timed_out:
        return "TIMEOUT", None, {"opencode_outcome": "TIMEOUT"}
    events, problem = parse_events(stdout)
    diagnostics: dict[str, Any] = {}
    if events is not None:
        measured = usage(events)
        if measured is not None:
            diagnostics["usage"] = measured
        errors = [event for event in events if event["type"] == "error"]
        if errors:
            error = errors[-1].get("error") if isinstance(errors[-1].get("error"), dict) else {}
            diagnostics["opencode_error"] = str(error.get("name") or "error")[:120]
            return "ERROR_EVENT", None, {**diagnostics, "opencode_outcome": "ERROR_EVENT"}
    if exit_code != 0:
        return "CRASH", None, {**diagnostics, "opencode_outcome": "CRASH"}
    if events is None:
        return "INVALID_OUTPUT", stdout or None, {**diagnostics, "opencode_outcome": "INVALID_OUTPUT", "opencode_stream": problem}
    starts = [index for index, event in enumerate(events) if event["type"] == "step_start"]
    last = events[starts[-1]:] if starts else []
    finishes = [event for event in last if event["type"] == "step_finish"]
    if not finishes or (finishes[-1].get("part") or {}).get("reason") != FINISH_REASON:
        return "PARTIAL", None, {**diagnostics, "opencode_outcome": "PARTIAL"}
    texts = [(event.get("part") or {}).get("text") for event in last if event["type"] == "text"]
    texts = [text for text in texts if isinstance(text, str) and text.strip()]
    if not texts:
        return "PARTIAL", None, {**diagnostics, "opencode_outcome": "PARTIAL"}
    if len(texts) != 1:
        joined = "\n".join(texts).encode("utf-8")
        return "INVALID_OUTPUT", joined, {**diagnostics, "opencode_outcome": "INVALID_OUTPUT", "opencode_stream": "several final texts"}
    data, is_object, extra = normalize_codex_last_message(texts[0].encode("utf-8"))
    diagnostics.update(extra)
    if not is_object:
        return "INVALID_OUTPUT", data, {**diagnostics, "opencode_outcome": "INVALID_OUTPUT"}
    return "OK", data, {**diagnostics, "opencode_outcome": "OK"}
