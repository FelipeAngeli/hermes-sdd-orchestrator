"""Optional security-policy calls for the loop engine (``LoopEngine(policy=...)``).

``policy`` is a ``policy_hooks.PolicyDispatcher.dispatch``-shaped callable,
``policy(hook, action) -> verdict``. With ``policy=None`` (the default) none of
these functions is consulted and the engine behaves exactly as before. A DENY halts
the node with ``POLICY_DENIED``; an action that still needs a human approval halts
with ``POLICY_APPROVAL_REQUIRED``; any other non-ALLOW answer is treated as a DENY.
"""
from __future__ import annotations

from typing import Any, Callable

PolicyHook = Callable[[str, dict[str, Any]], dict[str, Any]]
POLICY_HALTS = {"DENY": "POLICY_DENIED", "REQUIRE_APPROVAL": "POLICY_APPROVAL_REQUIRED"}
AGENT_PARAMS = ("stage", "tools", "allowed_tools", "sandbox", "permission_mode")


def agent_action(node: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    """``before_agent_start`` action from the node's declared permissions (its ``params``)."""
    action = {"kind": "agent_start", "agent_id": node["id"], "attempts": len(record["attempts"])}
    action.update({key: node["params"][key] for key in AGENT_PARAMS if key in node["params"]})
    return action


def retry_action(execution: dict[str, Any], name: str, record: dict[str, Any]) -> dict[str, Any]:
    """``before_loop_retry`` action: the attempt count and the execution's own limits and usage."""
    return {"kind": "loop_retry", "node": name, "attempts": len(record["attempts"]),
            "limits": {key: execution["limits"][key] for key in ("max_tokens", "max_node_runs", "max_wall_clock_seconds")},
            "usage": {key: execution["usage"][key] for key in ("tokens", "node_runs", "active_seconds")}}


def halt_reason(policy: PolicyHook | None, hook: str, action: dict[str, Any]) -> tuple[str | None, str | None]:
    """``(reason, prompt)`` to halt on, or ``(None, None)`` when there is no policy or it allows the action."""
    if policy is None:
        return None, None
    verdict = policy(hook, action)
    decision = verdict.get("decision") if isinstance(verdict, dict) else None
    if decision == "ALLOW":
        return None, None
    reasons = verdict.get("reasons") if isinstance(verdict, dict) else None
    return POLICY_HALTS.get(decision, "POLICY_DENIED"), ", ".join(reasons or []) or None


def record_completion(policy: PolicyHook | None, name: str, output: Any) -> None:
    """``after_task_completion``: audit the completed node with its gate verdict (the gate engine owns the block)."""
    if policy is None:
        return
    gate = output.get("gate") if isinstance(output, dict) else None
    verdict = gate.get("verdict") if isinstance(gate, dict) else None
    refs = [gate["decision_id"]] if isinstance(gate, dict) and isinstance(gate.get("decision_id"), str) else []
    policy("after_task_completion", {"kind": "task_completion", "task_id": name, "verdict": verdict, "evidence_refs": refs})
