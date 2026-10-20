"""Human control of a loop execution: cancellation and recorded decisions.

Every decision needs a human actor, a named person (``--by``) and their literal
words (``--quote``); it is appended to ``execution.decisions`` and committed as a
``HUMAN_DECISION`` event. No decision is ever taken automatically, and a SECURITY
approval refuses any machine-sounding approver.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import loop_correction
import loop_model
import loop_state
from loop_store import LoopStore, atomic_write_json

DECISIONS = ("approve", "reject", "provide-input", "retry", "mark-applied", "correct", "raise-budget")
#: Approver names that identify a program, not a person.
MACHINE_ACTORS = ("agent", "engine", "controller", "executor", "worker", "hermes", "loop", "bot", "system", "auto")
RETRYABLE_REASONS = ("EFFECT_AMBIGUOUS", "TRANSIENT_RETRIES_EXHAUSTED", "TIMEOUT_RETRIES_EXHAUSTED", "UNKNOWN_FAILURE",
                     "FUNCTIONAL_FAILURE", "HANDLER_MISSING")


def _now() -> float:
    return time.time()


def request_cancel(store: Path | str, execution_id: str, *, by: str, reason: str) -> dict[str, Any]:
    """Cancel now when no runner holds the execution; otherwise leave a request the runner honours next step."""
    loop_store = LoopStore(store)
    if not (by or "").strip() or not (reason or "").strip():
        raise loop_model.LoopError("QUOTE_REQUIRED", "cancel needs --by and --reason.", "Repeat with who cancels and why.")
    try:
        with loop_store.runner_lock(execution_id):
            execution = loop_store.load(execution_id)
            if loop_state.is_terminal(execution):
                return {"status": execution["status"], "execution_id": execution_id, "note": "already terminal"}
            execution["updated_at"] = _now()
            loop_store.commit(execution, *loop_state.cancel_all(execution, execution["updated_at"], by, reason))
            loop_store.clear_cancel_request(execution_id)
            return {"status": "CANCELLED", "execution_id": execution_id}
    except loop_model.LoopError as error:
        if error.code != "LOOP_ALREADY_RUNNING":
            raise
    loop_store.load(execution_id)
    atomic_write_json(loop_store.cancel_request_path(execution_id), {"by": by, "reason": reason, "at": _now()})
    return {"status": "CANCEL_REQUESTED", "execution_id": execution_id,
            "note": "a runner holds the execution; it finishes the current node and then cancels the rest"}


def _check_actor(actor: str, by: str, quote: str, category: str | None) -> None:
    if actor != "human":
        raise loop_model.LoopError("HUMAN_ACTOR_REQUIRED", "only a human decision can be recorded.",
                                   "Ask the user and record their answer with their own words.")
    machine = (by or "").strip().lower() in MACHINE_ACTORS or not (by or "").strip()
    if machine and category == "SECURITY":
        raise loop_model.LoopError("SECURITY_APPROVAL_HUMAN_ONLY", f"{by!r} is not a person; security approvals are never automatic.",
                                   "Ask the user; record the approval with --by <person> and their literal words.")
    if machine:
        raise loop_model.LoopError("HUMAN_ACTOR_REQUIRED", f"{by!r} does not name a person.", "Pass --by with the person deciding.")
    if not (quote or "").strip():
        raise loop_model.LoopError("QUOTE_REQUIRED", "the user's literal words are required.", "Pass --quote with what the user said.")


def _not_allowed(decision: str, pending: dict[str, Any]) -> loop_model.LoopError:
    return loop_model.LoopError("DECISION_NOT_ALLOWED", f"{decision} does not apply to a pending {pending['kind']} ({pending['reason']}).",
                                f"Use one of {', '.join(pending['allowed_decisions'])}.")


def _raise_budget(execution: dict[str, Any], value: str | None) -> str:
    key, _, raw = (value or "").partition("=")
    key = key.strip()
    if key not in loop_model.RAISABLE_LIMITS or not raw.strip().isdigit():
        raise loop_model.LoopError("BUDGET_RAISE_INVALID", f"{value!r} is not <limit>=<integer>.",
                                   f"Use one of {', '.join(loop_model.RAISABLE_LIMITS)}, e.g. max_node_runs=120.")
    new = int(raw)
    current = execution["limits"][key]
    if (current is not None and new <= current) or new > loop_model.LIMIT_CEILINGS[key]:
        raise loop_model.LoopError("BUDGET_RAISE_INVALID", f"{key} must rise above {current} and stay within {loop_model.LIMIT_CEILINGS[key]}.",
                                   "Pass a larger value within the hard ceiling.")
    execution["limits"][key] = new
    return key


def _apply(execution: dict[str, Any], spec: dict[str, Any], decision: str, node: str | None, value: str | None) -> tuple[str, dict[str, Any]] | None:
    pending = execution["pending"]
    nodes = loop_model.node_map(spec)
    record = execution["nodes"][node] if node else None
    reason = pending["reason"]
    if record is None and decision != "raise-budget":
        raise _not_allowed(decision, pending)
    if decision in ("approve", "reject"):
        if pending["kind"] != "APPROVAL":
            raise _not_allowed(decision, pending)
        record["approval"] = {"decision": decision, "category": pending["category"]}
        if decision == "reject":
            record["status"] = "FAILED"
            return loop_state.finalize(execution, "FAILED", "APPROVAL_REJECTED", _now())
        record["status"] = "PENDING"
    elif decision == "provide-input":
        if pending["kind"] != "INPUT" or value is None:
            raise _not_allowed(decision, pending)
        if nodes[node]["kind"] == "wait":
            record.update({"status": "COMPLETED", "output": {"input": value}, "human_input": value})
        else:
            record.update({"status": "PENDING", "human_input": value})
    elif decision == "retry":
        if reason not in RETRYABLE_REASONS or record is None:
            raise _not_allowed(decision, pending)
        record.update({"status": "PENDING", "retry_authorized": True})
    elif decision == "mark-applied":
        if reason != "EFFECT_AMBIGUOUS" or record is None:
            raise _not_allowed(decision, pending)
        try:
            output = json.loads(value) if value else {}
        except ValueError as error:
            raise loop_model.LoopError("VALUE_INVALID", "mark-applied --value must be JSON (the observed output).", "Pass JSON.") from error
        record.update({"status": "COMPLETED", "output": output, "partial": None})
    elif decision == "correct":
        if reason not in loop_state.CORRECTION_REASONS or node not in execution["corrections"]:
            raise _not_allowed(decision, pending)
        hypothesis = (value or "").strip()
        if not hypothesis or " ".join(hypothesis.lower().split()) in loop_correction.tried_hypotheses(execution, node):
            raise loop_model.LoopError("NO_NEW_HYPOTHESIS", "the hypothesis is empty or was already tried.", "Record a new, specific hypothesis.")
        loop_state.clear_halt(execution)
        return loop_correction.apply(execution, nodes, node, hypothesis, "human")
    elif decision == "raise-budget":
        if reason not in loop_state.BUDGET_REASONS:
            raise _not_allowed(decision, pending)
        key = _raise_budget(execution, value)
        if reason == "CORRECTION_BUDGET_EXHAUSTED" and node in execution["corrections"]:
            verdict = loop_correction.redecide(execution, node)
            loop_state.clear_halt(execution)
            if verdict["decision"] == "CORRECT":
                return loop_correction.apply(execution, nodes, node, verdict["hypothesis"], "verifier")
            return loop_state.halt(execution, spec, verdict["reason"], _now(), node=node)
        if record is not None and record["status"] == "BLOCKED":
            record["status"] = "PENDING"
        loop_state.clear_halt(execution)
        return "BUDGET_RAISED", {"limit": key}
    loop_state.clear_halt(execution)
    return None


def resolve(store: Path | str, execution_id: str, *, node: str | None, decision: str, by: str, quote: str,
            actor: str, value: str | None = None) -> dict[str, Any]:
    """Record one human decision for the pending halt; the next ``run`` continues from it."""
    if decision not in DECISIONS:
        raise loop_model.LoopError("DECISION_UNKNOWN", f"{decision!r} is not a decision.", f"Use one of {', '.join(DECISIONS)}.")
    loop_store = LoopStore(store)
    with loop_store.runner_lock(execution_id):
        execution = loop_store.load(execution_id)
        spec = loop_store.load_definition(execution_id, execution["definition_sha256"])
        pending = execution["pending"]
        if pending is None or loop_state.is_terminal(execution):
            raise loop_model.LoopError("NOTHING_PENDING", f"execution {execution_id} has no pending decision ({execution['status']}).",
                                       "Run `status`; only a halted execution accepts a decision.")
        _check_actor(actor, by, quote, pending.get("category"))
        target = pending["node"] if node is None else node
        if target != pending["node"]:
            raise loop_model.LoopError("NODE_MISMATCH", f"the pending decision is for {pending['node']!r}, not {node!r}.",
                                       "Pass --node with the pending node.")
        now = _now()
        execution["decisions"].append({"decision": decision, "by": by, "quote": quote, "node": target, "value": value, "at": now})
        outcome = _apply(execution, spec, decision, target, value)
        execution["updated_at"] = now
        event, data = outcome or ("HUMAN_DECISION", {})
        loop_store.commit(execution, event, {"decision": decision, "node": target, **data})
        return loop_state.summary(execution)
