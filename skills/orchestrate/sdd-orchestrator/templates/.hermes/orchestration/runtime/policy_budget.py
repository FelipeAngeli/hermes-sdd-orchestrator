"""BudgetGuard: bounded tokens, attempts, time and concurrency, with raises only by explicit rule.

The guard answers a single question per action: does it fit the limits the policy
and the loop definition set? It keeps no counters of its own; the counts come from
the stores that already own them (the loop execution's ``usage``, the agent's
attempt history, the live child count the caller passes), so there is no second
state of truth.

A budget raise is a separate action (``raise_budget``) and needs an explicit rule:
the limit must be listed in ``budget_raise.allowed_limits``, the new value may not
exceed ``max_factor`` times the current one, and it must come from a named human
with their quoted words. Anything else is DENY; nothing raises a budget silently.
"""
from __future__ import annotations

from typing import Any

import policy_approval

USAGE_KEYS = {"max_tokens": "tokens", "max_node_runs": "node_runs", "max_wall_clock_seconds": "active_seconds"}


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def check(policy: dict[str, Any], *, limits: dict[str, Any] | None = None, usage: dict[str, Any] | None = None,
          attempts: int | None = None, concurrent_agents: int | None = None) -> list[str]:
    """Reason codes for every limit the action would exceed; empty means it fits."""
    reasons: list[str] = []
    limits, usage = limits or {}, usage or {}
    for limit, key in USAGE_KEYS.items():
        ceiling, used = limits.get(limit), usage.get(key)
        if ceiling is not None and (not isinstance(used, (int, float)) or isinstance(used, bool) or used >= ceiling):
            reasons.append(f"budget_{key}_exhausted")
    if attempts is not None and (_int(attempts) is None or attempts >= policy["budgets"]["max_attempts_per_node"]):
        reasons.append("budget_attempts_exhausted")
    if concurrent_agents is not None and (_int(concurrent_agents) is None
                                          or concurrent_agents >= policy["delegation"]["max_concurrent_agents"]):
        reasons.append("budget_concurrency_exhausted")
    return reasons


def raise_budget(policy: dict[str, Any], *, limit: Any, current: Any, requested: Any, by: Any, quote: Any) -> list[str]:
    """Reason codes that refuse a raise; empty means the explicit rule allows it."""
    rule = policy["budget_raise"]
    if limit not in rule["allowed_limits"]:
        return ["budget_raise_limit_not_allowed"]
    if _int(current) is None or _int(requested) is None or requested <= current:
        return ["budget_raise_invalid"]
    if current == 0 or requested > current * rule["max_factor"]:
        return ["budget_raise_above_factor"]
    if not policy_approval.is_human_approver(by) or not isinstance(quote, str) or not quote.strip():
        return ["budget_raise_not_human"]
    return []
