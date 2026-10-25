"""Validated configuration for the opt-in context engine.

A missing or invalid configuration disables the engine (fail-open): callers get
``(None, reason)`` and must forward the original request unchanged.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path(__file__).parents[1] / "policies" / "CONTEXT_ENGINE.md"
EVALUATORS = ("none", "typesafe", "jev-ai")
_REQUIRED = {
    "schema_version",
    "enabled",
    "dry_run",
    "apply_enabled",
    "trigger_estimated_tokens",
    "bytes_per_token",
    "max_concurrency",
    "evaluator",
    "evaluator_timeout_seconds",
    "send_code_to_evaluator",
    "segment_target_chars",
    "max_segment_chars",
    "max_payload_bytes",
    "recent_turns_protected",
    "thresholds",
    "cache",
    "stages",
}
_BOOLEANS = ("enabled", "dry_run", "apply_enabled", "send_code_to_evaluator")
_THRESHOLDS = ("fact_loss_max", "constraint_loss_max")


def _json_block(text: str) -> Any:
    parts = text.split("```json")
    if len(parts) != 2 or "```" not in parts[1]:
        raise ValueError("CONFIG_INVALID")
    return json.loads(parts[1].split("```", 1)[0])


def _integer(value: Any, minimum: int, maximum: int | None = None) -> bool:
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return value >= minimum and (maximum is None or value <= maximum)


def _probability(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and 0 <= value <= 1


def _valid_thresholds(value: Any, *, partial: bool) -> bool:
    if not isinstance(value, dict) or set(value) - set(_THRESHOLDS):
        return False
    if not partial and set(value) != set(_THRESHOLDS):
        return False
    return all(_probability(item) for item in value.values())


def _valid_stages(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for rules in value.values():
        if not isinstance(rules, dict) or set(rules) - {"eligible_kinds", "thresholds"}:
            return False
        kinds = rules.get("eligible_kinds", [])
        if not isinstance(kinds, list) or not all(isinstance(kind, str) for kind in kinds):
            return False
        if "thresholds" in rules and not _valid_thresholds(rules["thresholds"], partial=True):
            return False
    return True


def _valid(config: Any) -> bool:
    if not isinstance(config, dict) or not _REQUIRED <= set(config):
        return False
    if config["schema_version"] != 1:
        return False
    if any(not isinstance(config[name], bool) for name in _BOOLEANS):
        return False
    if config["evaluator"] not in EVALUATORS:
        return False
    numbers = (
        _integer(config["trigger_estimated_tokens"], 0),
        _integer(config["bytes_per_token"], 1),
        _integer(config["max_concurrency"], 1, 8),
        _integer(config["segment_target_chars"], 1),
        _integer(config["max_segment_chars"], 1),
        _integer(config["max_payload_bytes"], 1024),
        _integer(config["recent_turns_protected"], 0),
    )
    if not all(numbers) or config["max_segment_chars"] < config["segment_target_chars"]:
        return False
    timeout = config["evaluator_timeout_seconds"]
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
        return False
    cache = config["cache"]
    if not isinstance(cache, dict) or not _integer(cache.get("ttl_seconds"), 0) or not _integer(cache.get("max_entries"), 1):
        return False
    state_dir = config.get("state_dir")
    if state_dir is not None and not isinstance(state_dir, str):
        return False
    return _valid_thresholds(config["thresholds"], partial=False) and _valid_stages(config["stages"])


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> tuple[dict[str, Any] | None, str | None]:
    """Return ``(config, None)`` or ``(None, CONFIG_MISSING|CONFIG_INVALID)``; never raises."""
    path = Path(path)
    if not path.is_file():
        return None, "CONFIG_MISSING"
    try:
        config = _json_block(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None, "CONFIG_INVALID"
    if not _valid(config):
        return None, "CONFIG_INVALID"
    return config, None


def stage_rules(config: dict[str, Any], stage: str) -> dict[str, Any]:
    """Return a stage's eligible kinds and its thresholds merged over the defaults."""
    base = dict(config["stages"].get(stage, {"eligible_kinds": []}))
    base["thresholds"] = {**config["thresholds"], **base.get("thresholds", {})}
    return base


def policy_sha256(config: dict[str, Any]) -> str:
    """Hash the full effective policy so any policy change invalidates cached verdicts."""
    encoded = json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def default_state_dir(controller_root: str | Path) -> Path:
    """Owner-private state location outside the repository, keyed by the controller root."""
    digest = hashlib.sha256(str(Path(controller_root).resolve()).encode("utf-8")).hexdigest()[:16]
    return Path.home() / ".hermes" / "context-engine" / digest
