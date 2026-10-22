"""Pure mutations of a LoopExecution: creation, halts, outcomes, reopen and cancellation.

Every function changes the execution dict in place and returns the event name and
data the caller commits through ``LoopStore.commit``. Nothing here performs I/O.
"""
from __future__ import annotations

from typing import Any

import loop_model
import loop_policy

#: Pending human-decision kinds and the decisions each one accepts.
PENDING_KINDS = {
    "APPROVAL": ("approve", "reject"),
    "INPUT": ("provide-input",),
    "DECISION": ("retry", "mark-applied", "correct", "raise-budget"),
    "BUDGET": ("raise-budget",),
}
CORRECTION_REASONS = ("NO_PROGRESS", "NO_NEW_HYPOTHESIS", "CORRECTION_BUDGET_EXHAUSTED")
BUDGET_REASONS = {
    "RUN_BUDGET_EXHAUSTED": "max_node_runs",
    "TOKEN_BUDGET_EXHAUSTED": "max_tokens",
    "TIME_BUDGET_EXHAUSTED": "max_wall_clock_seconds",
    "CORRECTION_BUDGET_EXHAUSTED": "max_correction_cycles",
}


def node_record() -> dict[str, Any]:
    return {
        "status": "PENDING", "generation": 1, "attempts": [], "transient_retries": 0, "timeout_retries": 0,
        "output": None, "partial": None, "correction": None, "human_input": None, "approval": None,
        "retry_authorized": False, "last_failure": None,
    }


def new_execution(execution_id: str, spec: dict[str, Any], definition_sha256: str, now: float) -> dict[str, Any]:
    return {
        "schema_version": 1, "execution_id": execution_id, "loop_id": spec["loop_id"], "definition_sha256": definition_sha256,
        "status": "PENDING", "created_at": now, "updated_at": now, "limits": dict(spec["limits"]),
        "usage": {"node_runs": 0, "tokens": 0, "tokens_method": "UNMEASURED", "active_seconds": 0.0, "correction_cycles": 0},
        "nodes": {node["id"]: node_record() for node in spec["nodes"]},
        "corrections": {}, "pending": None, "halt": None, "outcome": None, "decisions": [],
        "checkpoint": {"seq": 0, "at": now, "event": "NONE"},
    }


def is_terminal(execution: dict[str, Any]) -> bool:
    return execution["status"] in loop_model.TERMINAL_STATES


def outputs(execution: dict[str, Any]) -> dict[str, Any]:
    return {name: record["output"] for name, record in execution["nodes"].items() if record["status"] == "COMPLETED"}


def finalize(execution: dict[str, Any], status: str, reason: str, now: float) -> tuple[str, dict[str, Any]]:
    """Write the LoopOutcome and make the execution terminal."""
    execution["status"] = status
    execution["pending"] = None
    execution["outcome"] = {
        "status": status, "reason": reason, "hermes_stop_reason": loop_model.hermes_stop_reason(reason),
        "outputs": outputs(execution), "at": now,
        "node_statuses": {name: record["status"] for name, record in execution["nodes"].items()},
    }
    return f"EXECUTION_{status}", {"reason": reason}


def halt(execution: dict[str, Any], spec: dict[str, Any], reason: str, now: float, *, node: str | None = None,
         kind: str = "DECISION", category: str | None = None, prompt: str | None = None) -> tuple[str, dict[str, Any]]:
    """Stop the runner for a human decision (or fail it terminally when the policy says FAIL)."""
    execution["halt"] = {"reason": reason, "hermes_stop_reason": loop_model.hermes_stop_reason(reason), "node": node,
                         "next_step": loop_policy.next_step(reason), "at": now}
    if spec["policy"]["on_unrecoverable_failure"] == "FAIL" and reason in loop_policy.FAILABLE_REASONS:
        if node is not None:
            execution["nodes"][node]["status"] = "FAILED"
        return finalize(execution, "FAILED", reason, now)
    status = {"APPROVAL": "WAITING_APPROVAL", "INPUT": "WAITING_INPUT"}.get(kind, "BLOCKED")
    execution["status"] = status
    if node is not None:
        execution["nodes"][node]["status"] = status
    execution["pending"] = {"kind": kind, "node": node, "reason": reason, "category": category, "prompt": prompt,
                            "allowed_decisions": list(PENDING_KINDS[kind])}
    return "EXECUTION_HALTED", {"reason": reason, "node": node}


def clear_halt(execution: dict[str, Any]) -> None:
    execution["halt"] = None
    execution["pending"] = None
    execution["status"] = "PENDING"


def cancel_all(execution: dict[str, Any], now: float, by: str, reason: str) -> tuple[str, dict[str, Any]]:
    for record in execution["nodes"].values():
        if record["status"] not in loop_model.TERMINAL_STATES:
            record["status"] = "CANCELLED"
    execution["decisions"].append({"decision": "cancel", "by": by, "quote": reason, "node": None, "value": None, "at": now})
    return finalize(execution, "CANCELLED", "CANCELLED", now)


def reopen(execution: dict[str, Any], nodes: dict[str, dict[str, Any]], target: str) -> list[str]:
    """Reset ``target`` and everything downstream of it to PENDING in a new generation; keep history."""
    affected = [name for name in nodes if name in loop_model.downstream(nodes, target)]
    for name in affected:
        record = execution["nodes"][name]
        record.update({"status": "PENDING", "generation": record["generation"] + 1, "output": None, "partial": None,
                       "transient_retries": 0, "timeout_retries": 0, "retry_authorized": False, "last_failure": None,
                       "approval": None})
        if name != target:
            record["correction"] = None
    return affected


def merge_tokens(usage: dict[str, Any], tokens: Any, method: Any) -> None:
    if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 0:
        return
    usage["tokens"] += tokens
    method = method if method in ("MEASURED", "ESTIMATED") else "ESTIMATED"
    if method == "ESTIMATED" or usage["tokens_method"] == "ESTIMATED":
        usage["tokens_method"] = "ESTIMATED"
    else:
        usage["tokens_method"] = "MEASURED"


def record_attempt_tokens(attempt: dict[str, Any], tokens: Any, method: Any) -> None:
    """Keep the attempt's own token count beside the execution total, for per-node observability.

    Recorded for failed attempts too, while ``usage.tokens`` (the ``max_tokens`` budget) still counts
    successful attempts only. Invalid counts are recorded as unknown (``None``), never as zero; the
    method follows ``merge_tokens`` (anything but ``MEASURED`` is ``ESTIMATED``).
    """
    if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 0:
        attempt["tokens"], attempt["tokens_method"] = None, "UNMEASURED"
        return
    attempt["tokens"] = tokens
    attempt["tokens_method"] = "MEASURED" if method == "MEASURED" else "ESTIMATED"


def _decision_key(entry: dict[str, Any]) -> tuple[Any, Any, Any]:
    return entry.get("decision"), entry.get("node"), entry.get("generation")


def verify_integrity(execution: dict[str, Any], spec: dict[str, Any], events: list[dict[str, Any]]) -> None:
    """Refuse an execution whose limits or approvals are not explained by the definition and recorded decisions.

    Every recorded decision must also appear in the event log, limits must equal the
    definition limits plus the recorded raises (within the hard ceilings), and an
    approval must match a recorded human decision of the same node generation.
    """
    def tampered(message: str) -> loop_model.LoopError:
        return loop_model.LoopError("EXECUTION_TAMPERED", message,
                                    "execution.json was changed outside the loop runtime; restore it from a backup or start a new execution.")

    committed = [event for event in events if event.get("seq") == execution["checkpoint"]["seq"]]
    if not committed or committed[-1].get("state_sha256") != loop_model.digest(execution):
        raise tampered("execution.json does not match the state hash of its last committed event.")
    logged = {_decision_key(event) for event in events if "decision" in event}
    expected = dict(spec["limits"])
    for entry in execution["decisions"]:
        if entry.get("decision") == "cancel":
            continue
        if _decision_key(entry) not in logged:
            raise tampered(f"decision {entry.get('decision')!r} on {entry.get('node')!r} is not in events.jsonl.")
        if entry.get("decision") == "raise-budget":
            key, _, raw = str(entry.get("value") or "").partition("=")
            if key in loop_model.RAISABLE_LIMITS and raw.isdigit():
                expected[key] = int(raw)
    for key, ceiling in loop_model.LIMIT_CEILINGS.items():
        value = execution["limits"].get(key)
        if value is not None and value > ceiling:
            raise tampered(f"{key}={value} is above the hard ceiling {ceiling}.")
    if execution["limits"] != expected:
        raise tampered("limits differ from the definition plus the recorded raise-budget decisions.")
    recorded = {_decision_key(entry) for entry in execution["decisions"]}
    for name, record in execution["nodes"].items():
        approval = record.get("approval")
        if approval and (approval.get("decision"), name, record["generation"]) not in recorded:
            raise tampered(f"{name} carries an approval no human decision recorded.")


def summary(execution: dict[str, Any]) -> dict[str, Any]:
    return {
        "execution_id": execution["execution_id"], "loop_id": execution["loop_id"], "status": execution["status"],
        "nodes": {name: record["status"] for name, record in execution["nodes"].items()},
        "pending": execution["pending"], "halt": execution["halt"], "outcome": execution["outcome"],
        "usage": execution["usage"], "checkpoint": execution["checkpoint"],
    }
