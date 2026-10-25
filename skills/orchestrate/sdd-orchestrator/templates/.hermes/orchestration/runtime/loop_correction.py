"""Bounded correction for verifier nodes (TEST/REVIEW) on top of ``correction_loop``.

When a node with ``on_failure.reopen`` fails functionally, this module records the
failure as an attempt of a ``CORRECTION_LOOP_SCHEMA`` record, asks the existing
``correction_loop.decide`` whether one more correction is allowed, and on CORRECT
reopens only the target and its downstream nodes with a delimited corrective task.

The first attempt of every record is the original change (a fixed baseline
hypothesis); later attempts carry the hypothesis that produced their change, so a
repeated hypothesis, an unchanged change or an equivalent failure (same normalized
evidence and failed checks) stops the loop with NO_NEW_HYPOTHESIS or NO_PROGRESS.
"""
from __future__ import annotations

from typing import Any

import correction_loop
import loop_model
import loop_state

BASELINE_HYPOTHESIS = "original change before any correction"
#: correction_loop stop reasons → loop-engine halt reasons.
LOOP_REASON_MAP = {
    "RETRY_BUDGET_REACHED": "CORRECTION_BUDGET_EXHAUSTED",
    "EXECUTOR_CALL_BUDGET_REACHED": "RUN_BUDGET_EXHAUSTED",
    "COST_BUDGET_REACHED": "TOKEN_BUDGET_EXHAUSTED",
    "NO_NEW_HYPOTHESIS": "NO_NEW_HYPOTHESIS",
    "NO_PROGRESS": "NO_PROGRESS",
}


def _normalized(text: Any) -> str:
    return " ".join(str(text or "").lower().split())


def evidence_digest(failure: dict[str, Any]) -> str:
    """Equivalent failures share a digest: normalized evidence text plus the sorted failed checks."""
    evidence = failure.get("evidence", failure.get("message", ""))
    return loop_model.digest({"evidence": _normalized(evidence), "checks": sorted(str(item) for item in failure.get("failed_checks") or [])})


def change_digest(output: Any) -> str:
    return loop_model.digest(output)


def _attempt(index: int, hypothesis: str, change: str, failure: dict[str, Any]) -> dict[str, Any]:
    return {"attempt": index, "hypothesis": hypothesis, "tier": "WORKER", "escalation_reason": None,
            "change_digest": change, "evidence_digest": evidence_digest(failure), "verification": "FAIL",
            "failed_check_ids": [str(item) for item in failure.get("failed_checks") or []], "cost_units": None}


def _record(execution: dict[str, Any], verifier: str, proposed: str | None) -> dict[str, Any]:
    entry = execution["corrections"][verifier]
    limits, usage = execution["limits"], execution["usage"]
    attempts = entry["attempts"]
    remaining_runs = max(0, limits["max_node_runs"] - usage["node_runs"])
    return {
        "schema_version": 1, "ticket": execution["loop_id"], "action": verifier,
        "limits": {"max_attempts": limits["max_correction_cycles"] + 1, "max_executor_calls": len(attempts) + remaining_runs,
                   "max_cost_units": None},
        "usage": {"executor_calls": len(attempts), "cost_units": None},
        "attempts": attempts,
        "proposed": None if not (proposed or "").strip() else {"hypothesis": proposed, "tier": "WORKER", "escalation_reason": None,
                                                                "estimated_cost_units": None},
    }


def target_cycles(execution: dict[str, Any], target: str) -> int:
    """Correction cycles already spent on one target, across every verifier that reopens it."""
    return int((execution["corrections"].get("_cycles_by_target") or {}).get(target, 0))


def _decide(execution: dict[str, Any], verifier: str, proposed: str | None) -> dict[str, Any]:
    target = execution["corrections"][verifier]["target"]
    if target_cycles(execution, target) >= execution["limits"]["max_correction_cycles"]:
        return {"decision": "HALT", "reason": "CORRECTION_BUDGET_EXHAUSTED"}
    decision = correction_loop.decide(_record(execution, verifier, proposed))
    if decision["decision"] == correction_loop.CORRECT:
        return {"decision": "CORRECT", "hypothesis": decision["hypothesis"]}
    return {"decision": "HALT", "reason": LOOP_REASON_MAP[decision["stop_reason"]]}


def on_verifier_failure(execution: dict[str, Any], nodes: dict[str, dict[str, Any]], verifier: str,
                        failure: dict[str, Any]) -> dict[str, Any]:
    """Record the failed verification; return ``{"decision": "CORRECT"|"HALT", ...}`` without mutating other nodes."""
    target = nodes[verifier]["on_failure"]["reopen"]
    change = change_digest(execution["nodes"][target]["output"])
    entry = execution["corrections"].setdefault(verifier, {"target": target, "attempts": [], "pending_hypothesis": None})
    hypothesis = entry["pending_hypothesis"] or BASELINE_HYPOTHESIS
    entry["attempts"].append(_attempt(len(entry["attempts"]) + 1, hypothesis, change, failure))
    entry["last_failure"] = failure
    return _decide(execution, verifier, failure.get("hypothesis"))


def tried_hypotheses(execution: dict[str, Any], verifier: str) -> set[str]:
    entry = execution["corrections"].get(verifier) or {"attempts": []}
    return {_normalized(item["hypothesis"]) for item in entry["attempts"]}


def redecide(execution: dict[str, Any], verifier: str) -> dict[str, Any]:
    """Re-ask ``correction_loop`` after a budget raise, with the last failure's own hypothesis."""
    entry = execution["corrections"][verifier]
    return _decide(execution, verifier, (entry.get("last_failure") or {}).get("hypothesis"))


def apply(execution: dict[str, Any], nodes: dict[str, dict[str, Any]], verifier: str, hypothesis: str,
          source: str) -> tuple[str, dict[str, Any]]:
    """Reopen the target and its downstream nodes with a delimited corrective task."""
    entry = execution["corrections"][verifier]
    target = entry["target"]
    used = target_cycles(execution, target)
    if used >= execution["limits"]["max_correction_cycles"]:
        raise loop_model.LoopError("CORRECTION_BUDGET_EXHAUSTED", f"{target} already used {used} correction cycles.",
                                   "Raise max_correction_cycles with `resolve --decision raise-budget` first.")
    failure = entry.get("last_failure") or {}
    cycle = used + 1
    affected = loop_state.reopen(execution, nodes, target)
    execution["nodes"][target]["correction"] = {
        "cycle": cycle, "verifier": verifier, "hypothesis": hypothesis, "source": source,
        "failed_checks": [str(item) for item in failure.get("failed_checks") or []],
        "cause": failure.get("cause"), "evidence": failure.get("evidence", failure.get("message")),
        "scope": f"Correct only what makes {verifier} fail; do not change unrelated behavior.",
    }
    entry["pending_hypothesis"] = hypothesis
    execution["corrections"].setdefault("_cycles_by_target", {})[target] = cycle
    execution["usage"]["correction_cycles"] += 1
    return "CORRECTION_APPLIED", {"verifier": verifier, "target": target, "cycle": cycle, "rerun": affected, "source": source}


def on_verifier_pass(execution: dict[str, Any], verifier: str) -> None:
    """A passing verification closes its correction record; a later failure starts a new baseline."""
    entry = execution["corrections"].pop(verifier, None)
    if entry is not None:
        execution["corrections"].setdefault("_closed", []).append({"verifier": verifier, "attempts": len(entry["attempts"])})
