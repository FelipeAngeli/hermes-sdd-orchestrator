"""Metrics collected by the opt-in context engine for one optimization run."""
from __future__ import annotations

from typing import Any


class Metrics:
    """Counters and derived values; token figures are bytes divided by ``bytes_per_token``."""

    def __init__(self, before: int = 0) -> None:
        self.values: dict[str, Any] = {
            "input_tokens_before": before,
            "input_tokens_after": before,
            "evaluator_calls": 0,
            "evaluator_input_tokens": 0,
            "evaluator_output_tokens": 0,
            "evaluator_calls_without_usage": 0,
            "estimated_saving": 0,
            "estimated_net_saving": 0,
            "effective_saving": 0,
            "net_saving": 0,
            "latency_ms": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "cache_hit_ratio": 0.0,
            "segments_total": 0,
            "segments_protected": 0,
            "segments_preserved": 0,
            "segments_omitted": 0,
            "segments_rejected": 0,
            "evaluation_errors": {},
            "protection_rules": {},
            "requirement_impact": {"before": [], "after": [], "preserved": True},
        }

    def count(self, field: str, key: str) -> None:
        """Increment ``field[key]`` in a reason-keyed counter."""
        bucket = self.values[field]
        bucket[key] = bucket.get(key, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        """Return the metrics after recalculating derived values."""
        values = self.values
        lookups = values["cache_hits"] + values["cache_misses"]
        values["cache_hit_ratio"] = values["cache_hits"] / lookups if lookups else 0.0
        values["estimated_net_saving"] = values["estimated_saving"] - values["evaluator_input_tokens"]
        values["net_saving"] = values["effective_saving"] - values["evaluator_input_tokens"]
        return values
