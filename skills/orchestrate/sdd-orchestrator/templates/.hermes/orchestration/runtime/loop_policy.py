"""Failure classification, retry eligibility and backoff for the loop engine.

Pure functions: they read a node, its runtime record and the limits, and return
what to do next. Nothing here sleeps, writes or calls a handler.

Classes of failure:
- ``TRANSIENT``: the operation may succeed if repeated unchanged (network reset,
  rate limit, a crashed process).
- ``TIMEOUT``: the node exceeded its deadline; its effect is unknown.
- ``FUNCTIONAL``: the operation ran and the result is wrong; repeating it unchanged
  cannot help, so it is corrected (``on_failure.reopen``) or escalated.
- ``UNKNOWN``: an unexpected exception or malformed result; never retried.

A non-idempotent node is retried automatically only when the failure proves that
no effect was applied (``effect_applied: false``). Otherwise the effect is
ambiguous and a human (or a reconciler that proves the effect) decides.
"""
from __future__ import annotations

import json
from typing import Any

FAILURE_CLASSES = ("TRANSIENT", "TIMEOUT", "FUNCTIONAL", "UNKNOWN")
RESULT_STATUSES = ("SUCCEEDED", "FAILED")
RETRY = "RETRY"
CORRECT = "CORRECT"
HALT = "HALT"
#: Halt reasons that ``policy.on_unrecoverable_failure: FAIL`` turns into a terminal FAILED.
FAILABLE_REASONS = ("FUNCTIONAL_FAILURE", "UNKNOWN_FAILURE", "TRANSIENT_RETRIES_EXHAUSTED", "TIMEOUT_RETRIES_EXHAUSTED",
                    "NO_PROGRESS", "NO_NEW_HYPOTHESIS", "HANDLER_MISSING")


class NodeFailure(Exception):
    """Raised by a handler to report a classified failure."""

    failure_class = "UNKNOWN"

    def __init__(self, code: str, message: str, *, effect_applied: bool | None = None, **details: Any) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.effect_applied = effect_applied
        self.details = details

    def as_failure(self) -> dict[str, Any]:
        failure = {"class": self.failure_class, "code": self.code, "message": self.message, "effect_applied": self.effect_applied}
        failure.update(self.details)
        return failure


class TransientFailure(NodeFailure):
    failure_class = "TRANSIENT"


class FunctionalFailure(NodeFailure):
    failure_class = "FUNCTIONAL"


def failure_from_exception(error: BaseException) -> dict[str, Any]:
    if isinstance(error, NodeFailure):
        return error.as_failure()
    return {"class": "UNKNOWN", "code": type(error).__name__, "message": str(error)[:500], "effect_applied": None}


def timeout_failure(seconds: float) -> dict[str, Any]:
    return {"class": "TIMEOUT", "code": "NODE_TIMEOUT", "message": f"no result within {seconds} seconds", "effect_applied": None}


def normalize_result(value: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return ``(success, failure)``; a malformed result is an UNKNOWN failure, never a success."""
    if not isinstance(value, dict) or value.get("status") not in RESULT_STATUSES:
        return None, {"class": "UNKNOWN", "code": "RESULT_INVALID", "message": "handler result must be a dict with status SUCCEEDED or FAILED",
                      "effect_applied": None}
    if value["status"] == "SUCCEEDED":
        output = value.get("output", {})
        try:
            json.dumps({"output": output, "tokens": value.get("tokens"), "needs_input": value.get("needs_input")},
                       sort_keys=True, allow_nan=False, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError, UnicodeEncodeError):
            return None, {"class": "UNKNOWN", "code": "RESULT_INVALID", "message": "handler output is not JSON with string keys",
                          "effect_applied": None}
        return {"output": output if output is not None else {}, "tokens": value.get("tokens"),
                "tokens_method": value.get("tokens_method"), "needs_input": value.get("needs_input")}, None
    try:
        failure = dict(value.get("failure") or {})
        json.dumps(failure, sort_keys=True, allow_nan=False, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        return None, {"class": "UNKNOWN", "code": "RESULT_INVALID", "message": "handler failure is not JSON with string keys",
                      "effect_applied": None}
    if failure.get("class") not in FAILURE_CLASSES:
        failure["class"] = "UNKNOWN"
    failure.setdefault("code", "FAILED")
    failure.setdefault("message", "")
    failure.setdefault("effect_applied", None)
    return None, failure


def backoff_seconds(retry_index: int, limits: dict[str, Any]) -> float:
    """Deterministic exponential backoff: base, 2×base, 4×base … capped at the maximum (no jitter)."""
    return float(min(limits["backoff_base_seconds"] * (2 ** retry_index), limits["backoff_max_seconds"]))


def effect_is_ambiguous(node: dict[str, Any], failure: dict[str, Any]) -> bool:
    return not node["idempotent"] and failure.get("effect_applied") is not False


def classify(node: dict[str, Any], record: dict[str, Any], failure: dict[str, Any], limits: dict[str, Any]) -> dict[str, Any]:
    """Decide RETRY (with delay), CORRECT (functional failure of a verifier) or HALT (with reason)."""
    kind = failure["class"]
    if kind == "FUNCTIONAL":
        if node.get("on_failure"):
            return {"decision": CORRECT}
        return {"decision": HALT, "reason": "FUNCTIONAL_FAILURE"}
    if kind == "UNKNOWN":
        return {"decision": HALT, "reason": "UNKNOWN_FAILURE"}
    if effect_is_ambiguous(node, failure):
        return {"decision": HALT, "reason": "EFFECT_AMBIGUOUS"}
    counter, maximum, exhausted = (("timeout_retries", "max_timeout_retries", "TIMEOUT_RETRIES_EXHAUSTED") if kind == "TIMEOUT"
                                   else ("transient_retries", "max_transient_retries", "TRANSIENT_RETRIES_EXHAUSTED"))
    if record[counter] >= limits[maximum]:
        return {"decision": HALT, "reason": exhausted}
    return {"decision": RETRY, "counter": counter, "delay": backoff_seconds(record[counter], limits)}


def idempotency_key(execution_id: str, node_id: str, generation: int) -> str:
    """Stable for every attempt of one node generation (retries and crash replays included), so an
    external system that receives it can recognise a replay; a correction opens a new generation."""
    return f"{execution_id}:{node_id}:g{generation}"


def attempt_id(execution_id: str, node_id: str, generation: int, attempt: int) -> str:
    """Unique per attempt, for logs and audit; never a deduplication key."""
    return f"{idempotency_key(execution_id, node_id, generation)}:a{attempt}"


def next_step(reason: str) -> str:
    return NEXT_STEPS.get(reason, "Inspect the execution with `loop_cli.py status` and resolve the pending decision.")


NEXT_STEPS = {
    "TRANSIENT_RETRIES_EXHAUSTED": "Automatic retries are spent; check the dependency, then `resolve --decision retry` or `cancel`.",
    "TIMEOUT_RETRIES_EXHAUSTED": "The node timed out again; raise its timeout in the definition (new execution) or `resolve --decision retry`.",
    "UNKNOWN_FAILURE": "An unexpected error is never retried automatically; investigate, then `resolve --decision retry` or `cancel`.",
    "HANDLER_MISSING": "No handler is registered for the node; register it, then `resolve --decision retry`.",
    "FUNCTIONAL_FAILURE": "The node ran and failed functionally; fix the cause, then `resolve --decision retry` or `cancel`.",
    "EFFECT_AMBIGUOUS": "A non-idempotent node may or may not have applied its effect; check the external system, then "
                        "`resolve --decision mark-applied` (with the observed output) or `retry`; never guess.",
    "RUN_BUDGET_EXHAUSTED": "The node-run budget is spent; `resolve --decision raise-budget --value max_node_runs=<n>` with the user's words.",
    "TOKEN_BUDGET_EXHAUSTED": "The token budget is spent; `resolve --decision raise-budget --value max_tokens=<n>` with the user's words.",
    "TIME_BUDGET_EXHAUSTED": "The time budget is spent; `resolve --decision raise-budget --value max_wall_clock_seconds=<n>` with the user's words.",
    "CORRECTION_BUDGET_EXHAUSTED": "The correction cycles are spent; `resolve --decision raise-budget --value max_correction_cycles=<n>` or `cancel`.",
    "NO_PROGRESS": "The last correction changed nothing observable; investigate and `resolve --decision correct --value '<new hypothesis>'`.",
    "NO_NEW_HYPOTHESIS": "No new hypothesis for the failure; `resolve --decision correct --value '<new hypothesis>'` with the user's idea.",
    "APPROVAL_REQUIRED": "A human must approve or reject the node: `resolve --decision approve|reject` with their words.",
    "EXTERNAL_APPROVAL_REQUIRED": "The node changes an external system; only the user's explicit approval lets it run.",
    "INPUT_REQUIRED": "Ask the user the pending question and record the answer with `resolve --decision provide-input`.",
    "NO_RUNNABLE_NODE": "No node can run and the loop is not complete; inspect node statuses with `status`.",
    "NODE_STILL_RUNNING": "An earlier timed-out attempt of this non-idempotent node is still running in this process; "
                          "wait for it to end (or restart the runner), check its effect, then `resolve --decision retry` or `mark-applied`.",
}
