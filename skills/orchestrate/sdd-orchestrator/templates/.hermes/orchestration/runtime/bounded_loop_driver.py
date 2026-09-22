#!/usr/bin/env python3
"""Decide whether an authorized BOUNDED_AUTO round may continue in the same turn.

This module never invokes an executor, writes STATE, mutates Git, or activates
BOUNDED_AUTO. Expected progress (STATE hash, budgets used, recovery after
rollover) is not PLAN_STALE; workspace identity drift is.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, Callable

import jsonschema

import bounded_run_planner as planner

CONTINUE = "CONTINUE"
STOP = "STOP"
ROLLOVER_REQUIRED = "ROLLOVER_REQUIRED"


class DriverError(ValueError):
    """A snapshot, plan, or CLI request is invalid."""


def implementation_cursor_matches_plan(snapshot: dict[str, Any], plan: dict[str, Any]) -> bool:
    """Return whether post-baseline completed slices advance in planned order."""
    baseline = plan["implementation_baseline"]
    baseline_completed = baseline["completed_slices"]
    current_completed = snapshot["implementation"]["completed_slices"]
    if current_completed[: len(baseline_completed)] != baseline_completed:
        return False
    pending_slices = [
        item for item in baseline["planned_slices"] if item not in baseline_completed
    ]
    post_baseline = current_completed[len(baseline_completed) :]
    return post_baseline == pending_slices[: len(post_baseline)]


def remaining_actions(snapshot: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, Any]]:
    if not implementation_cursor_matches_plan(snapshot, plan):
        return []
    completed = set(snapshot["state"]["completed"]) | set(snapshot["state"]["skipped"])
    baseline = plan["implementation_baseline"]
    baseline_completed = baseline["completed_slices"]
    slice_done = len(snapshot["implementation"]["completed_slices"]) - len(baseline_completed)
    slice_seen = 0
    remaining: list[dict[str, Any]] = []
    for entry in plan["actions"]:
        action = entry["action"]
        if action == "IMPLEMENT_SLICE":
            slice_seen += 1
            if slice_seen <= slice_done:
                continue
            remaining.append(entry)
            continue
        if action not in completed:
            remaining.append(entry)
    return remaining


def accepted_action_prefix(snapshot: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the contiguous plan prefix proven complete by STATE and slice evidence."""
    completed = set(snapshot["state"]["completed"]) | set(snapshot["state"]["skipped"])
    baseline_completed = plan["implementation_baseline"]["completed_slices"]
    current_completed = snapshot["implementation"]["completed_slices"]
    if current_completed[: len(baseline_completed)] != baseline_completed:
        raise DriverError("PLAN_CURSOR_MISMATCH: implementation completion regressed")
    completed_slices = len(current_completed) - len(baseline_completed)
    slice_index = 0
    prefix: list[dict[str, Any]] = []
    gap_seen = False
    for entry in plan["actions"]:
        if entry["action"] == "IMPLEMENT_SLICE":
            slice_index += 1
            accepted = slice_index <= completed_slices
        else:
            accepted = entry["action"] in completed
        if accepted and gap_seen:
            raise DriverError("PLAN_CURSOR_MISMATCH: completed actions are not a plan prefix")
        if accepted:
            prefix.append(entry)
        else:
            gap_seen = True
    return prefix


def _stop(reason: str, *, loop_active: bool = False) -> dict[str, Any]:
    payload = {
        "decision": STOP,
        "end_turn": True,
        "next_action": None,
        "loop_active": loop_active,
        "stop_reason": reason,
        "reason": reason,
    }
    return payload


def _continue(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "decision": CONTINUE,
        "end_turn": False,
        "next_action": entry["action"],
        "next_action_id": entry["id"],
        "classification": entry["classification"],
        "executor": entry["executor"],
        "loop_active": True,
        "stop_reason": "NONE",
        "reason": "next planned action is dispatchable",
        "action": entry,
    }


def _validate(snapshot: dict[str, Any], plan: dict[str, Any]) -> None:
    planner.validate_snapshot(snapshot)
    errors = sorted(jsonschema.Draft202012Validator(planner.plan_schema()).iter_errors(plan), key=lambda error: list(error.path))
    if errors:
        raise DriverError("INVALID_PLAN: " + "; ".join(error.message for error in errors[:3]))
    if (snapshot["schema_version"] == 2) != (plan["plan_version"] == planner.LOCAL_DELIVERY_PLAN_VERSION):
        raise DriverError("INVALID_PLAN: snapshot schema version and plan version must match")
    if plan["authorization"]["plan_sha256"] != planner.hash_plan(plan):
        raise DriverError("INVALID_PLAN: plan_sha256 does not match canonical plan content")
    semantic_error = planner.validate_plan_actions(plan)
    if semantic_error:
        raise DriverError(semantic_error)
    accepted_actions = accepted_action_prefix(snapshot, plan)
    gate_error = planner.gate_progress_error(
        plan["gate_baseline"], snapshot["gates"], [entry["action"] for entry in accepted_actions],
    )
    if gate_error:
        raise DriverError(f"INVALID_PLAN: gate progress invalid: {gate_error}")
    if snapshot["schema_version"] == 2:
        _validate_local_delivery_binding(snapshot, plan, accepted_actions)


def _validate_local_delivery_binding(
    snapshot: dict[str, Any], plan: dict[str, Any], accepted_actions: list[dict[str, Any]],
) -> None:
    local = plan["local_delivery"]
    authorization = snapshot["local_delivery"]["authorization"]
    if local["authorization"] != authorization:
        raise DriverError("INVALID_PLAN: LOCAL_DELIVERY authorization drift")
    if local["authorization_sha256"] != planner.sha256_json(authorization):
        raise DriverError("INVALID_PLAN: LOCAL_DELIVERY authorization hash drift")
    if plan["budgets"]["limits"] != authorization["total_limits"]:
        raise DriverError("INVALID_PLAN: LOCAL_DELIVERY limits drift")
    if local["reserved_use"] != plan["budgets"]["projected_use"]:
        raise DriverError("INVALID_PLAN: LOCAL_DELIVERY reservation drift")
    ledger_error = planner.validate_local_delivery_ledger_progress(snapshot, plan, accepted_actions)
    if ledger_error:
        raise DriverError(ledger_error)
    used = planner.local_delivery_usage(snapshot)
    for key in planner.BUDGET_KEYS:
        if used[key] > authorization["total_limits"][key]:
            raise DriverError("INVALID_PLAN: LOCAL_DELIVERY usage exceeds authorization")


def identity_stale(snapshot: dict[str, Any], plan: dict[str, Any]) -> bool:
    inp, workspace = plan["input"], snapshot["workspace"]
    return (
        inp["workspace_path"] != workspace["path"]
        or inp["git_common_dir"] != workspace["git_common_dir"]
        or inp["branch"] != workspace["branch"]
        or inp["head"] != workspace["head"]
        or inp["ticket"] != snapshot["state"]["ticket"]
        or plan["budgets"]["limits"] != planner.budget_values(snapshot)[0]
        or plan["implementation_baseline"]["planned_slices"] != snapshot["implementation"]["planned_slices"]
    )


def evaluate_next(snapshot: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    try:
        _validate(snapshot, plan)
    except planner.PlannerError as exc:
        raise DriverError(str(exc)) from exc
    if identity_stale(snapshot, plan):
        return _stop("PLAN_STALE")
    if not implementation_cursor_matches_plan(snapshot, plan):
        return _stop("PLAN_CURSOR_MISMATCH")
    mode = snapshot["loop"]["mode"]
    if mode != "BOUNDED_AUTO":
        return _stop("MODE_NOT_BOUNDED_AUTO")
    if not snapshot["loop"]["loop_active"]:
        return _stop("LOOP_INACTIVE")
    existing_stop = snapshot["loop"]["stop_reason"]
    if existing_stop not in {"NONE", "", None}:
        return _stop(existing_stop)
    if snapshot["state"]["blockers"] or snapshot["recovery"]["decision"] == "BLOCKED":
        return _stop("BLOCKED")
    if snapshot["loop"]["human_approval_required"]:
        return _stop("HUMAN_APPROVAL_REQUIRED")
    if any(snapshot["restrictions"].values()):
        return _stop("HUMAN_REQUIRED")
    remaining = remaining_actions(snapshot, plan)
    recovery = snapshot["recovery"]["decision"]
    if recovery == "RELEASED":
        if not remaining:
            return _stop(plan["termination"]["expected_reason"] or "NO_NEXT_ACTION")
        return {
            "decision": ROLLOVER_REQUIRED,
            "end_turn": False,
            "next_action": remaining[0]["action"],
            "loop_active": True,
            "stop_reason": "NONE",
            "reason": "RELEASED journal must roll over before prepare",
            "action": remaining[0],
        }
    if recovery != "DISPATCH_ALLOWED":
        return _stop(recovery)
    if not remaining:
        return _stop(plan["termination"]["expected_reason"] or "NO_NEXT_ACTION")
    entry = remaining[0]
    expected = snapshot["state"]["next_action"] or snapshot["state"]["stage"]
    if expected not in {entry["action"], entry["stage"]}:
        return _stop("PLAN_CURSOR_MISMATCH")
    if entry["human_approval_required"] or entry["classification"] == "HUMAN_REQUIRED":
        return _stop("HUMAN_REQUIRED")
    gate_precondition = planner.gate_precondition_error(entry["action"], snapshot["gates"])
    if gate_precondition:
        return _stop(gate_precondition)
    limits, used = planner.budget_values(snapshot)
    exhausted = next((key for key in planner.BUDGET_KEYS if used[key] + entry["budget_cost"][key] > limits[key]), None)
    if exhausted:
        return _stop("BUDGET_REACHED")
    return _continue(entry)


def run_until_stop(
    snapshot: dict[str, Any],
    plan: dict[str, Any],
    dispatch: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    current = copy.deepcopy(snapshot)
    executed: list[str] = []
    while True:
        decision = evaluate_next(current, plan)
        if decision["decision"] != CONTINUE:
            result = dict(decision)
            result["executed"] = executed
            return result
        entry = decision["action"]
        current = dispatch(entry, current)
        executed.append(entry["action"])
        if identity_stale(current, plan):
            result = _stop("PLAN_STALE")
            result["executed"] = executed
            return result
        remaining = remaining_actions(current, plan)
        if remaining and remaining[0]["id"] == entry["id"]:
            result = _stop("NO_PROGRESS")
            result["executed"] = executed
            return result


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Decide the next authorized BOUNDED_AUTO action without executing it.")
    commands = parser.add_subparsers(dest="command", required=True)
    next_parser = commands.add_parser("next")
    next_parser.add_argument("--snapshot", required=True)
    next_parser.add_argument("--plan", required=True)
    next_parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        result = evaluate_next(snapshot, plan)
        public = {key: value for key, value in result.items() if key != "action"}
        emit(public) if args.json else print(public["decision"])
        return 0
    except (OSError, json.JSONDecodeError, DriverError, planner.PlannerError) as exc:
        payload = {"valid": False, "errors": [str(exc)], "decision": STOP, "end_turn": True}
        emit(payload) if getattr(args, "json", False) else print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
