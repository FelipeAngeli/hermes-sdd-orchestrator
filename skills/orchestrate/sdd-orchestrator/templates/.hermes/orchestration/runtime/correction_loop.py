#!/usr/bin/env python3
"""Decide whether one failed action may be corrected again, or must pause.

The correction loop is execute a slice → collect evidence → verify → correct
the specific failure → verify again. This module is pure: it reads a
controller-owned JSON record and returns a decision. It never invokes an
executor, runs a verifier, writes STATE, or mutates Git. The controller
performs every effect and appends the attempt to the record afterwards.

Every exit is explicit: a passing verification, a configured limit, a repeated
hypothesis, or unchanged evidence. There is no unbounded mode.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

import jsonschema

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "CORRECTION_LOOP_SCHEMA.json"

CORRECT = "CORRECT"
VERIFIED = "VERIFIED"
STOP = "STOP"
DECISIONS = (CORRECT, VERIFIED, STOP)
LOOP_STOP_REASONS = (
    "RETRY_BUDGET_REACHED",
    "EXECUTOR_CALL_BUDGET_REACHED",
    "COST_BUDGET_REACHED",
    "NO_NEW_HYPOTHESIS",
    "NO_PROGRESS",
)
TIERS = ("DETERMINISTIC", "WORKER", "ESCALATED")
NEXT_STEPS = {
    "RETRY_BUDGET_REACHED": "Review the recorded attempts and either raise max_attempts explicitly or reopen the stage.",
    "EXECUTOR_CALL_BUDGET_REACHED": "Authorize additional executor calls or narrow the slice before retrying.",
    "COST_BUDGET_REACHED": "Authorize more cost or choose a cheaper tier; usage is recorded in the loop record.",
    "NO_NEW_HYPOTHESIS": "Supply a new, specific hypothesis for the failing checks; repeating a tried one is refused.",
    "NO_PROGRESS": "The last correction changed nothing observable; investigate the failure before another attempt.",
}


class LoopError(ValueError):
    """The correction record is malformed or internally inconsistent."""


def _normalized(text: str) -> str:
    return " ".join(text.lower().split())


def validate_record(value: Any) -> dict[str, Any]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(value), key=lambda error: list(error.path))
    if errors:
        raise LoopError("INVALID_RECORD: " + "; ".join(error.message for error in errors[:3]))
    attempts = value["attempts"]
    for expected, item in enumerate(attempts, start=1):
        if item["attempt"] != expected:
            raise LoopError("INVALID_RECORD: attempts must be numbered contiguously from 1")
        if item["tier"] == "ESCALATED" and not (item["escalation_reason"] or "").strip():
            raise LoopError("INVALID_RECORD: an ESCALATED attempt must record its escalation_reason")
        if item["verification"] == "PASS" and item["failed_check_ids"]:
            raise LoopError("INVALID_RECORD: a PASS attempt cannot list failed checks")
    if any(item["verification"] == "PASS" for item in attempts[:-1]):
        raise LoopError("INVALID_RECORD: no attempt may follow a PASS")
    worker_attempts = sum(item["tier"] != "DETERMINISTIC" for item in attempts)
    if value["usage"]["executor_calls"] < worker_attempts:
        raise LoopError("INVALID_RECORD: usage.executor_calls is below the recorded worker attempts")
    return value


def _stop(value: dict[str, Any], reason: str) -> dict[str, Any]:
    attempts = value["attempts"]
    last = attempts[-1] if attempts else None
    return {
        "decision": STOP,
        "end_turn": True,
        "stop_reason": reason,
        "next_attempt": None,
        "next_step": NEXT_STEPS[reason],
        "escalation_reason": None,
        "usage": copy.deepcopy(value["usage"]),
        "limits": copy.deepcopy(value["limits"]),
        "evidence": {
            "attempts": len(attempts),
            "hypotheses": [item["hypothesis"] for item in attempts],
            "failed_check_ids": list(last["failed_check_ids"]) if last else [],
            "last_evidence_digest": last["evidence_digest"] if last else None,
        },
        "state_updates": {
            "mode": "PAUSED",
            "loop_active": False,
            "stage_status": "PAUSED",
            "stop_reason": reason,
        },
    }


def decide(value: Any) -> dict[str, Any]:
    """Return the next loop decision for a validated correction record."""
    record = validate_record(copy.deepcopy(value))
    attempts, limits, usage, proposed = record["attempts"], record["limits"], record["usage"], record["proposed"]
    if attempts and attempts[-1]["verification"] == "PASS":
        return {
            "decision": VERIFIED, "end_turn": False, "stop_reason": "NONE", "next_attempt": None,
            "next_step": "Commit the verified result to STATE and continue with the next planned action.",
            "escalation_reason": None, "usage": copy.deepcopy(usage), "limits": copy.deepcopy(limits),
            "evidence": {"attempts": len(attempts), "failed_check_ids": [],
                         "last_evidence_digest": attempts[-1]["evidence_digest"]},
        }
    if len(attempts) >= limits["max_attempts"]:
        return _stop(record, "RETRY_BUDGET_REACHED")
    if len(attempts) >= 2:
        previous, last = attempts[-2], attempts[-1]
        if last["evidence_digest"] == previous["evidence_digest"] or last["change_digest"] == previous["change_digest"]:
            return _stop(record, "NO_PROGRESS")
    if proposed is None or not proposed["hypothesis"].strip():
        return _stop(record, "NO_NEW_HYPOTHESIS")
    if _normalized(proposed["hypothesis"]) in {_normalized(item["hypothesis"]) for item in attempts}:
        return _stop(record, "NO_NEW_HYPOTHESIS")
    if proposed["tier"] == "ESCALATED":
        if not attempts:
            raise LoopError("ESCALATION_UNJUSTIFIED: escalate only after a concrete recorded failure")
        if not (proposed["escalation_reason"] or "").strip():
            raise LoopError("ESCALATION_UNJUSTIFIED: an escalated attempt needs an escalation_reason")
    if proposed["tier"] != "DETERMINISTIC" and usage["executor_calls"] >= limits["max_executor_calls"]:
        return _stop(record, "EXECUTOR_CALL_BUDGET_REACHED")
    if limits["max_cost_units"] is not None:
        if usage["cost_units"] is None:
            return _stop(record, "COST_BUDGET_REACHED")
        projected = usage["cost_units"] + (proposed["estimated_cost_units"] or 0)
        if projected > limits["max_cost_units"]:
            return _stop(record, "COST_BUDGET_REACHED")
    return {
        "decision": CORRECT,
        "end_turn": False,
        "stop_reason": "NONE",
        "next_attempt": len(attempts) + 1,
        "next_step": "Dispatch one correction for the failed checks, then verify again with the same verifier.",
        "hypothesis": proposed["hypothesis"],
        "tier": proposed["tier"],
        "escalation_reason": proposed["escalation_reason"] if proposed["tier"] == "ESCALATED" else None,
        "usage": copy.deepcopy(usage),
        "limits": copy.deepcopy(limits),
        "evidence": {
            "attempts": len(attempts),
            "failed_check_ids": list(attempts[-1]["failed_check_ids"]) if attempts else [],
            "last_evidence_digest": attempts[-1]["evidence_digest"] if attempts else None,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Decide the next bounded correction step without executing it.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("decide", "validate"):
        command = commands.add_parser(name)
        command.add_argument("--record", required=True, help="Controller-owned correction loop record JSON.")
        command.add_argument("--json", action="store_true", help="Emit structured JSON.")
    args = parser.parse_args(argv)
    try:
        value = json.loads(Path(args.record).read_text(encoding="utf-8"))
        result = decide(value) if args.command == "decide" else (validate_record(value) and {"valid": True})
    except (OSError, json.JSONDecodeError, LoopError) as exc:
        result = {"valid": False, "errors": [str(exc)]}
        print(json.dumps(result, sort_keys=True) if args.json else str(exc))
        return 2
    print(json.dumps(result, sort_keys=True) if args.json else result.get("decision", "VALID"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
