"""Strict, fail-closed configuration for the opt-in delegation manager."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path(__file__).parents[1] / "policies" / "DELEGATION.md"
_KEYS = {"schema_version", "enabled", "concurrency_enabled", "max_concurrent_worktrees", "max_children_per_parent", "max_depth", "default_token_budget", "bytes_per_token", "on_budget_exceeded", "session_binding", "worktree_management", "auto_delegate_on_conflict", "on_parent_cancel"}


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Refuse duplicate keys: a reader would otherwise see a different value than the loader."""
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate key")
    return dict(pairs)


def _integer(value: Any, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != _KEYS or not _integer(value.get("schema_version"), 1, 1):
        return False
    if not all(isinstance(value[key], bool) for key in ("enabled", "concurrency_enabled", "auto_delegate_on_conflict")):
        return False
    if value["auto_delegate_on_conflict"] or not all((_integer(value["max_concurrent_worktrees"], 1, 3), _integer(value["max_children_per_parent"], 1, 3), _integer(value["max_depth"], 1, 2), _integer(value["bytes_per_token"], 1, 16))):
        return False
    budget = value["default_token_budget"]
    return ((budget is None or _integer(budget, 1, 2**63 - 1)) and value["on_budget_exceeded"] == "BLOCK" and value["session_binding"] == "EXPLICIT" and value["worktree_management"] == "PREPARED_ONLY" and value["on_parent_cancel"] in {"CASCADE", "DETACH_READONLY"})


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> tuple[dict[str, Any] | None, str | None]:
    path = Path(path)
    if not path.is_file():
        return None, "CONFIG_MISSING"
    try:
        raw = path.read_bytes()
        parts = raw.decode("utf-8").split("```json")
        value = json.loads(parts[1].split("```", 1)[0], object_pairs_hook=_unique) if len(parts) == 2 and "```" in parts[1] else None
    except (OSError, UnicodeError, ValueError, IndexError):
        return None, "CONFIG_INVALID"
    if not _valid(value):
        return None, "CONFIG_INVALID"
    value["sha256"] = hashlib.sha256(raw).hexdigest()
    return value, None


def effective_limits(config: dict[str, Any]) -> dict[str, int]:
    active = bool(config.get("enabled") and config.get("concurrency_enabled"))
    return {"max_concurrent_worktrees": min(3, config["max_concurrent_worktrees"]) if active else 1,
            "max_children_per_parent": min(3, config["max_children_per_parent"]) if active else 1,
            "max_depth": min(2, config["max_depth"])}
