"""Human control of a loop execution: cancellation and recorded decisions.

Every decision needs a human actor, a named person (``--by``) and their literal
words (``--quote``); it is appended to ``execution.decisions`` and committed as a
``HUMAN_DECISION`` event. No decision is ever taken automatically. A name that
contains a program or agent token is refused for every decision, and a SECURITY
approval is accepted only from a person listed in ``policy.security_approvers``.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import loop_correction
import loop_model
import loop_state
from loop_store import LoopStore, atomic_write_json

DECISIONS = ("approve", "reject", "provide-input", "retry", "mark-applied", "correct", "raise-budget")
#: Name tokens that identify a program or an AI executor, not a person (matched per token, case-insensitive).
MACHINE_ACTORS = ("agent", "engine", "controller", "executor", "worker", "hermes", "loop", "bot", "system", "auto",
                  "approver", "claude", "codex", "opencode", "gpt", "openai", "anthropic", "llm", "ai", "jev", "assistant",
                  "copilot", "gemini", "deepseek", "script", "ci", "automation")
RETRYABLE_REASONS = ("EFFECT_AMBIGUOUS", "TRANSIENT_RETRIES_EXHAUSTED", "TIMEOUT_RETRIES_EXHAUSTED", "UNKNOWN_FAILURE",
                     "FUNCTIONAL_FAILURE", "HANDLER_MISSING", "NODE_STILL_RUNNING")
_TOKENS = re.compile(r"[a-z]+|[0-9]+")


def _now() -> float:
    return time.time()


def request_cancel(store: Path | str, execution_id: str, *, by: str, reason: str) -> dict[str, Any]:
    """Cancel now when no runner holds the execution; otherwise leave a request the runner honours next step."""
    loop_store = LoopStore(store)
    if not (by or "").strip() or not (reason or "").strip():
        raise loop_model.LoopError("QUOTE_REQUIRED", "cancel needs --by and --reason.", "Repeat with who cancels and why.")
    try:
        with loop_store.runner_lock(execution_id):
            execution = loop_store.load(execution_id, repair=True)
            if loop_state.is_terminal(execution):
                return {"status": execution["status"], "execution_id": execution_id, "note": "already terminal"}
            execution["updated_at"] = _now()
            loop_store.commit(execution, *loop_state.cancel_all(execution, execution["updated_at"], by, reason))
            loop_store.clear_cancel_request(execution_id)
            return {"status": "CANCELLED", "execution_id": execution_id}
    except loop_model.LoopError as error:
        if error.code != "LOOP_ALREADY_RUNNING":
            raise
    loop_store.require(execution_id)
    atomic_write_json(loop_store.cancel_request_path(execution_id), {"by": by, "reason": reason, "at": _now()})
    return {"status": "CANCEL_REQUESTED", "execution_id": execution_id,
            "note": "a runner holds the execution; it finishes the current node and then cancels the rest"}


def is_machine_name(name: str) -> bool:
    """True for an empty name or one with any program/agent token (``hermes-agent``, ``AutoApprover``, ``Claude``)."""
    text = (name or "").strip()
    if not text:
        return True
    split = re.sub(r"([a-z])([A-Z])", r"\1 \2", text).lower()
    tokens = set(_TOKENS.findall(split)) | set(_TOKENS.findall(text.lower()))
    return any(token in MACHINE_ACTORS for token in tokens) or any(word in text.lower() for word in MACHINE_ACTORS if len(word) >= 5)


def _check_actor(actor: str, by: str, quote: str, category: str | None, approvers: list[str]) -> None:
    if actor != "human":
        raise loop_model.LoopError("HUMAN_ACTOR_REQUIRED", "only a human decision can be recorded.",
                                   "Ask the user and record their answer with their own words.")
    machine = is_machine_name(by)
    if category == "SECURITY" and (machine or (by or "").strip().lower() not in {item.lower() for item in approvers}):
        raise loop_model.LoopError("SECURITY_APPROVAL_HUMAN_ONLY", f"{by!r} is not a listed security approver; security approvals are never automatic.",
                                   f"Ask one of {', '.join(approvers)} and record the approval with their literal words.")
    if machine:
        raise loop_model.LoopError("HUMAN_ACTOR_REQUIRED", f"{by!r} does not name a person.", "Pass --by with the person deciding.")
    if not (quote or "").strip():
        raise loop_model.LoopError("QUOTE_REQUIRED", "the user's literal words are required.", "Pass --quote with what the user said.")


def _not_allowed(decision: str, pending: dict[str, Any]) -> loop_model.LoopError:
    return loop_model.LoopError("DECISION_NOT_ALLOWED", f"{decision} does not apply to a pending {pending['kind']} ({pending['reason']}).",
                                f"Use one of {', '.join(pending['allowed_decisions'])}.")


def _raise_budget(execution: dict[str, Any], value: str | None, expected: str) -> str:
    key, _, raw = (value or "").partition("=")
    key = key.strip()
    if key != expected or not raw.strip().isdigit():
        raise loop_model.LoopError("BUDGET_RAISE_INVALID", f"{value!r} is not {expected}=<integer>.",
                                   f"The exhausted budget is {expected}; pass --value {expected}=<n>.")
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
        if reason not in ("EFFECT_AMBIGUOUS", "NODE_STILL_RUNNING") or record is None:
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
        key = _raise_budget(execution, value, loop_state.BUDGET_REASONS[reason])
        if node in execution["corrections"] and record is not None and record["attempts"] \
                and record["attempts"][-1]["result"] == "FAILED":
            verdict = loop_correction.redecide(execution, node)
            loop_state.clear_halt(execution)
            if verdict["decision"] == "CORRECT":
                return loop_correction.apply(execution, nodes, node, verdict["hypothesis"], "verifier")
            return loop_state.halt(execution, spec, verdict["reason"], _now(), node=node)
        if record is not None and record["status"] in ("BLOCKED", "RUNNING"):
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
        execution = loop_store.load(execution_id, repair=True)
        spec = loop_store.load_definition(execution_id, execution["definition_sha256"])
        loop_state.verify_integrity(execution, spec, loop_store.events(execution_id))
        pending = execution["pending"]
        if pending is None or loop_state.is_terminal(execution):
            raise loop_model.LoopError("NOTHING_PENDING", f"execution {execution_id} has no pending decision ({execution['status']}).",
                                       "Run `status`; only a halted execution accepts a decision.")
        _check_actor(actor, by, quote, pending.get("category"), spec["policy"]["security_approvers"])
        target = pending["node"] if node is None else node
        if target != pending["node"]:
            raise loop_model.LoopError("NODE_MISMATCH", f"the pending decision is for {pending['node']!r}, not {node!r}.",
                                       "Pass --node with the pending node.")
        now = _now()
        generation = execution["nodes"][target]["generation"] if target else None
        execution["decisions"].append({"decision": decision, "by": by, "quote": quote, "node": target, "value": value,
                                       "generation": generation, "at": now})
        outcome = _apply(execution, spec, decision, target, value)
        execution["updated_at"] = now
        event, data = outcome or ("HUMAN_DECISION", {})
        loop_store.commit(execution, event, {**data, "decision": decision, "node": target, "generation": generation})
        return loop_state.summary(execution)
