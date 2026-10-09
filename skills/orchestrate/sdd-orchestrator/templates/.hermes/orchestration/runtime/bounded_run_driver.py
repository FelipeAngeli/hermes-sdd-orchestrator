#!/usr/bin/env python3
"""Decide the next isolated action in an approved BOUNDED_AUTO round.

The driver is pure: it reads JSON fixtures only. It never invokes an executor,
writes STATE, mutates Git, activates BOUNDED_AUTO, or bypasses a control. It is
the only bounded-run driver; ``bounded_loop_driver.py`` is deprecated. MANUAL
progress is driven by ``sdd.py next``. Every stop carries ``next_step`` and
``next_command`` from ``stop_reasons.py``.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

import jsonschema

import bounded_run_planner as planner
import stop_reasons

EXECUTE_NEXT = "EXECUTE_NEXT"
ROLLOVER_REQUIRED = "ROLLOVER_REQUIRED"
COMPLETE = "COMPLETE"
STOP_BUDGET = "STOP_BUDGET"
STOP_HUMAN_REQUIRED = "STOP_HUMAN_REQUIRED"
STOP_BLOCKED = "STOP_BLOCKED"
STOP_PLAN_STALE = "STOP_PLAN_STALE"
STOP_RECOVERY = "STOP_RECOVERY"
REPLAN_REQUIRED = "REPLAN_REQUIRED"

#: Budget key -> stop reason (singular names, as listed in LOOP_POLICY.md §9).
BUDGET_STOP_REASONS = {
    "stage_transitions": "STAGE_TRANSITION_BUDGET_REACHED",
    "executor_calls": "EXECUTOR_CALL_BUDGET_REACHED",
    "corrective_retries": "RETRY_BUDGET_REACHED",
    "tdd_slices": "TDD_SLICE_BUDGET_REACHED",
    "investigation_expansions": "INVESTIGATION_BUDGET_REACHED",
    "review_cycles": "REVIEW_CYCLE_BUDGET_REACHED",
    "ci_runs": "CI_RUN_BUDGET_REACHED",
    "external_mutations": "EXTERNAL_MUTATION_REQUIRED",
}
#: Gate failure value -> stop reason.
GATE_STOP_REASONS = {
    ("focused_tests", "FAIL"): "FOCUSED_TESTS_FAILED", ("format", "FAIL"): "FORMAT_FAILED",
    ("analyze", "FAIL"): "ANALYZE_FAILED", ("ci", "FAIL"): "CI_FAILED", ("ci", "TIMEOUT"): "CI_TIMEOUT",
    ("review", "BLOCKED"): "REVIEW_BLOCKED", ("review", "CHANGES_REQUIRED"): "REVIEW_CHANGES_REQUIRED",
}

RUNTIME_KEYS = {
    "current_plan_id", "approved_plan_sha256", "planned_action_count",
    "current_sequence", "actions_executed", "started_at", "last_action_id",
    "expected_predecessor_state_sha256",
}


class DriverError(ValueError):
    """The runtime snapshot or approved plan is malformed."""


def _without_runtime(snapshot: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(snapshot)
    value.pop("runtime", None)
    value.get("loop", {}).pop("last_run", None)
    return value


def bind(
    snapshot: dict[str, Any], plan: dict[str, Any], *, started_at: str,
    approved_plan_sha256: str | None = None,
) -> dict[str, Any]:
    """Bind a validated preview to a fresh deterministic runtime snapshot."""
    if "runtime" in snapshot:
        raise DriverError("INVALID_RUNTIME: bind requires a snapshot without runtime")
    if not isinstance(started_at, str) or not started_at:
        raise DriverError("INVALID_RUNTIME: started_at must be supplied explicitly")

    errors = planner.validate_plan(plan, snapshot)
    if errors:
        raise DriverError("; ".join(errors))

    plan_hash = plan["authorization"]["plan_sha256"]
    if snapshot["schema_version"] == 1:
        if approved_plan_sha256 != plan_hash:
            raise DriverError(
                "APPROVAL_REQUIRED: legacy schema requires the exact "
                "approved_plan_sha256"
            )
    elif approved_plan_sha256 is not None and approved_plan_sha256 != plan_hash:
        raise DriverError(
            "INVALID_PLAN: approved_plan_sha256 does not match the validated plan"
        )

    bound = copy.deepcopy(snapshot)
    bound["runtime"] = {
        "current_plan_id": plan["plan_id"],
        "approved_plan_sha256": plan_hash,
        "planned_action_count": len(plan["actions"]),
        "current_sequence": 0,
        "actions_executed": 0,
        "started_at": started_at,
        "last_action_id": None,
        "expected_predecessor_state_sha256": snapshot["state"]["sha256"],
    }
    return bound


def _validate(snapshot: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(snapshot.get("runtime"), dict) or set(snapshot["runtime"]) != RUNTIME_KEYS:
        raise DriverError("INVALID_RUNTIME: runtime must contain exactly the required counters and predecessor hash")
    try:
        planner.validate_snapshot(_without_runtime(snapshot))
    except planner.PlannerError as exc:
        raise DriverError(str(exc)) from exc
    errors = sorted(jsonschema.Draft202012Validator(planner.plan_schema()).iter_errors(plan), key=lambda error: list(error.path))
    if errors:
        raise DriverError("INVALID_PLAN: " + "; ".join(error.message for error in errors[:3]))
    if plan["authorization"]["plan_sha256"] != planner.hash_plan(plan):
        raise DriverError("INVALID_PLAN: plan_sha256 does not match canonical content")
    semantic_error = planner.validate_plan_actions(plan)
    if semantic_error:
        raise DriverError(semantic_error)
    runtime = snapshot["runtime"]
    if runtime["current_plan_id"] != plan["plan_id"] or runtime["approved_plan_sha256"] != plan["authorization"]["plan_sha256"]:
        raise DriverError("PLAN_STALE: runtime approval does not bind this exact plan")
    if runtime["planned_action_count"] != len(plan["actions"]):
        raise DriverError("PLAN_STALE: planned_action_count differs from approved plan")
    if type(runtime["current_sequence"]) is not int or type(runtime["actions_executed"]) is not int:
        raise DriverError("INVALID_RUNTIME: sequence counters must be integers")
    if runtime["current_sequence"] != runtime["actions_executed"] or not 0 <= runtime["current_sequence"] <= len(plan["actions"]):
        raise DriverError("SEQUENCE_SKIPPED: runtime sequence is not a valid accepted-action cursor")
    completed_actions = [entry["action"] for entry in plan["actions"][:runtime["current_sequence"]]]
    gate_error = planner.gate_progress_error(plan["gate_baseline"], snapshot["gates"], completed_actions)
    if gate_error:
        raise DriverError(f"INVALID_PLAN: gate progress invalid: {gate_error}")
    predecessor = runtime["expected_predecessor_state_sha256"]
    if not isinstance(predecessor, str) or predecessor != snapshot["state"]["sha256"]:
        raise DriverError("PLAN_STALE: persisted STATE does not match the expected predecessor hash")
    _validate_local_delivery_progress(snapshot, plan, runtime)
    return runtime


def _validate_local_delivery_progress(
    snapshot: dict[str, Any], plan: dict[str, Any], runtime: dict[str, Any],
) -> None:
    """Validate cumulative v2 use against the immutable approved projection."""
    if (snapshot["schema_version"] == 2) != (plan["plan_version"] == 2):
        raise DriverError("INVALID_PLAN: LOCAL_DELIVERY snapshot and plan versions must match")
    if snapshot["schema_version"] != 2:
        return
    local_snapshot = snapshot["local_delivery"]
    local_plan = plan.get("local_delivery")
    if local_plan is None:
        raise DriverError("INVALID_PLAN: LOCAL_DELIVERY binding is missing")
    authorization = local_snapshot["authorization"]
    if local_plan["authorization"] != authorization:
        raise DriverError("INVALID_PLAN: LOCAL_DELIVERY authorization drift")
    if local_plan["authorization_sha256"] != planner.sha256_json(authorization):
        raise DriverError("INVALID_PLAN: LOCAL_DELIVERY authorization hash drift")
    if plan["budgets"]["limits"] != authorization["total_limits"]:
        raise DriverError("INVALID_PLAN: LOCAL_DELIVERY limits drift from authorization")
    if local_plan["reserved_use"] != plan["budgets"]["projected_use"]:
        raise DriverError("INVALID_PLAN: LOCAL_DELIVERY reservation drift")
    accepted_actions = plan["actions"][:runtime["current_sequence"]]
    ledger_error = planner.validate_local_delivery_ledger_progress(snapshot, plan, accepted_actions)
    if ledger_error:
        raise DriverError(ledger_error)
    used = planner.local_delivery_usage(snapshot)
    for key in planner.BUDGET_KEYS:
        if used[key] > authorization["total_limits"][key]:
            raise DriverError("INVALID_PLAN: LOCAL_DELIVERY usage exceeds authorization")


def _done_gates_pass(snapshot: dict[str, Any]) -> bool:
    gates = snapshot["gates"]
    return (
        gates["focused_tests"] == "PASS"
        and planner.gate_passed(gates, "format")
        and planner.gate_passed(gates, "analyze")
        and gates["review"] == "APPROVED"
        and (
            gates["ci"] == "PASS"
            if gates["project_ci_enabled"]
            else gates["ci"] == "DISABLED_BY_PROJECT_POLICY"
        )
    )


def _gate_failure(snapshot: dict[str, Any]) -> str | None:
    for name, value in snapshot["gates"].items():
        if name == "project_ci_enabled":
            continue
        if (name, value) in GATE_STOP_REASONS:
            return GATE_STOP_REASONS[(name, value)]
        if value in {"FAIL", "TIMEOUT", "BLOCKED"}:
            return "GATE_TIMEOUT" if value == "TIMEOUT" else "BLOCKED"
    return None


def _local_delivery_cursor_is_valid(
    snapshot: dict[str, Any], plan: dict[str, Any], runtime: dict[str, Any],
) -> bool:
    implementation = snapshot["implementation"]
    baseline = plan["implementation_baseline"]
    planned = baseline["planned_slices"]
    completed_at_approval = baseline["completed_slices"]
    if not planned:
        return True
    accepted_slices = sum(
        entry["action"] == "IMPLEMENT_SLICE"
        for entry in plan["actions"][:runtime["current_sequence"]]
    )
    expected_completed = planned[:len(completed_at_approval) + accepted_slices]
    completed = implementation["completed_slices"]
    if (
        implementation["planned_slices"] != planned
        or completed != expected_completed
    ):
        return False
    is_complete = completed == planned
    return (
        implementation["all_slices_green"] == is_complete
        and implementation["next_slice"] == (None if is_complete else planned[len(completed)])
    )


def _action_precondition_failure(
    snapshot: dict[str, Any], plan: dict[str, Any], runtime: dict[str, Any], entry: dict[str, Any],
) -> str | None:
    action, gates, implementation = entry["action"], snapshot["gates"], snapshot["implementation"]
    if snapshot["schema_version"] == 2 and not _local_delivery_cursor_is_valid(snapshot, plan, runtime):
        return "IMPLEMENTATION_CURSOR_INVALID"
    if (
        action in {"TEST_FOCUSED", "FORMAT_CHANGED_FILES", "ANALYZE", "REVIEW"}
        and implementation["planned_slices"]
        and not implementation["all_slices_green"]
    ):
        return "IMPLEMENTATION_SLICES_NOT_GREEN"
    return planner.gate_precondition_error(action, gates)


def _replan(snapshot: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    return {
        "decision": REPLAN_REQUIRED,
        "terminal_reason": "PLAN_COMPLETE",
        "end_turn": False,
        "action": None,
        "sequence": runtime["current_sequence"],
        "stop_reason": "NONE",
        "requires_replan": True,
        "next_step": "The schema 2 projection is exhausted under a still-valid LOCAL_DELIVERY authorization: regenerate the snapshot, plan and bind again without asking the user.",
        "next_command": stop_reasons.describe("PLAN_COMPLETE")["next_command"],
        "event": _event(snapshot, action=None, decision=REPLAN_REQUIRED, stop_reason="NONE"),
    }


def _identity_stale(snapshot: dict[str, Any], plan: dict[str, Any]) -> bool:
    workspace, source = snapshot["workspace"], plan["input"]
    return (
        source["workspace_path"] != workspace["path"]
        or source["git_common_dir"] != workspace["git_common_dir"]
        or source["branch"] != workspace["branch"]
        or source["head"] != workspace["head"]
        or source["ticket"] != snapshot["state"]["ticket"]
        or plan["budgets"]["limits"] != planner.budget_values(_without_runtime(snapshot))[0]
    )


def _event(snapshot: dict[str, Any], *, action: str | None, decision: str, stop_reason: str) -> dict[str, Any]:
    runtime = snapshot["runtime"]
    _, budget = planner.budget_values(_without_runtime(snapshot))
    recovery = snapshot["recovery"]["decision"]
    state_hash = snapshot["state"]["sha256"]
    return {
        "sequence": runtime["current_sequence"] + (1 if action else 0),
        "action": action,
        "decision": decision,
        "budget_before": budget,
        "budget_after": copy.deepcopy(budget),
        "recovery_before": recovery,
        "recovery_after": recovery,
        "journal_action_id": snapshot["recovery"]["current_action_id"],
        "state_hash_before": state_hash,
        "state_hash_after": state_hash,
        "stop_reason": stop_reason,
    }


def _last_run(snapshot: dict[str, Any], stop_reason: str) -> dict[str, Any]:
    runtime, budgets = snapshot["runtime"], snapshot["loop"]["budgets"]
    return {
        "id": snapshot["runtime"]["current_plan_id"],
        "stop_reason": stop_reason,
        "actions_executed": runtime["actions_executed"],
        "stage_transitions": budgets["stage_transitions"]["used"],
        "executor_calls": budgets["executor_calls"]["used"],
    }


def _stop(snapshot: dict[str, Any], decision: str, stop_reason: str, *, blocked: bool = False) -> dict[str, Any]:
    described = stop_reasons.describe(stop_reason)
    return {
        "next_step": described["next_step"],
        "next_command": described["next_command"],
        "decision": decision,
        "terminal_reason": stop_reason,
        "end_turn": True,
        "action": None,
        "sequence": snapshot["runtime"]["current_sequence"],
        "stop_reason": stop_reason,
        "state_updates": {
            "loop_active": False,
            "mode": "PAUSED",
            "current_action": None,
            "stop_reason": stop_reason,
            "last_run": _last_run(snapshot, stop_reason),
            "stage_status": "BLOCKED" if blocked else "PAUSED",
        },
        "event": _event(snapshot, action=None, decision=decision, stop_reason=stop_reason),
    }


def _budget_stop(snapshot: dict[str, Any], entry: dict[str, Any] | None) -> dict[str, Any] | None:
    limits, used = planner.budget_values(_without_runtime(snapshot))
    costs = entry["budget_cost"] if entry else planner.zero_budgets()
    exhausted = next((key for key in planner.BUDGET_KEYS if used[key] + costs[key] > limits[key]), None)
    if exhausted is None and entry is None:
        exhausted = next((key for key in planner.BUDGET_KEYS if used[key] >= limits[key]), None)
    if exhausted is None:
        return None
    return _stop(snapshot, STOP_BUDGET, BUDGET_STOP_REASONS[exhausted])


def _closed_round(snapshot: dict[str, Any], plan: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    recorded = snapshot["loop"].get("last_run", {}).get("stop_reason") or snapshot["loop"]["stop_reason"]
    if recorded not in {"NONE", "", None}:
        return _stop(snapshot, COMPLETE, recorded, blocked=recorded == "BLOCKED")
    sequence = runtime["current_sequence"]
    if sequence == len(plan["actions"]):
        budget = _budget_stop(snapshot, None)
        return budget or _stop(snapshot, COMPLETE, "PLAN_COMPLETE")
    budget = _budget_stop(snapshot, plan["actions"][sequence])
    if budget:
        return budget
    return _stop(snapshot, COMPLETE, "LOOP_INACTIVE")


def _recovery_stop(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The journal needs reconciliation first; ``sdd.py next`` prints the exact recovery command."""
    result = _stop(snapshot, STOP_RECOVERY, "RECOVERY_RECONCILIATION_REQUIRED")
    result["recovery"] = snapshot["recovery"]["decision"]
    return result


def evaluate_next(snapshot: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    runtime = _validate(snapshot, plan)
    if _identity_stale(snapshot, plan):
        return _stop(snapshot, STOP_PLAN_STALE, "PLAN_STALE")
    if (
        snapshot["state"]["blockers"]
        or snapshot["state"]["status"] == "BLOCKED"
        or snapshot["recovery"]["decision"] == "BLOCKED"
    ):
        return _stop(snapshot, STOP_BLOCKED, "BLOCKED", blocked=True)
    if snapshot["loop"]["mode"] == "MANUAL":
        return _stop(snapshot, COMPLETE, "MANUAL_ACTION_COMPLETE")
    if snapshot["state"]["status"] == "DONE" or snapshot["state"]["stage"] == "DONE":
        if not _done_gates_pass(snapshot):
            return _stop(snapshot, STOP_BLOCKED, "DONE_GATES_NOT_PASSED", blocked=True)
        closed = _stop(snapshot, COMPLETE, "DONE")
        closed["state_updates"]["stage_status"] = "COMPLETED"
        return closed
    if snapshot["loop"]["mode"] != "BOUNDED_AUTO" or not snapshot["loop"]["loop_active"]:
        return _closed_round(snapshot, plan, runtime)
    if snapshot["loop"]["stop_reason"] not in {"NONE", "", None}:
        return _closed_round(snapshot, plan, runtime)
    if snapshot["loop"]["human_approval_required"] or any(snapshot["restrictions"].values()):
        return _stop(snapshot, STOP_HUMAN_REQUIRED, "HUMAN_REQUIRED")
    gate_failure = _gate_failure(snapshot)
    if gate_failure:
        return _stop(snapshot, STOP_BLOCKED, gate_failure, blocked=True)
    if snapshot["recovery"]["decision"] == "CORRECTIVE_RETRY_AVAILABLE":
        retries = snapshot["loop"]["budgets"]["corrective_retries"]
        if retries["used_current_action"] >= retries["max_per_action"]:
            return _stop(snapshot, STOP_HUMAN_REQUIRED, "CORRECTIVE_RETRY_EXHAUSTED")
    if snapshot["recovery"]["decision"] not in {"DISPATCH_ALLOWED", "RELEASED", "CORRECTIVE_RETRY_AVAILABLE"}:
        return _recovery_stop(snapshot)

    sequence = runtime["current_sequence"]
    if sequence == len(plan["actions"]):
        if snapshot["schema_version"] == 2:
            if plan["actions"]:
                return _replan(snapshot, runtime)
            if plan["termination"]["expected_reason"] == "BUDGET_REACHED":
                return _stop(snapshot, STOP_BUDGET, "BUDGET_REACHED")
            return _stop(snapshot, COMPLETE, "PLAN_EMPTY")
        budget = _budget_stop(snapshot, None)
        if budget:
            return budget
        return _stop(snapshot, COMPLETE, "PLAN_COMPLETE")
    entry = plan["actions"][sequence]
    expected = snapshot["state"]["next_action"] or snapshot["state"]["stage"]
    if expected not in {entry["action"], entry["stage"]}:
        return _stop(snapshot, STOP_BLOCKED, "SEQUENCE_SKIPPED", blocked=True)
    if entry["human_approval_required"] or entry["classification"] == "HUMAN_REQUIRED":
        return _stop(snapshot, STOP_HUMAN_REQUIRED, "HUMAN_REQUIRED")
    precondition_failure = _action_precondition_failure(snapshot, plan, runtime, entry)
    if precondition_failure:
        return _stop(snapshot, STOP_BLOCKED, precondition_failure, blocked=True)
    budget = _budget_stop(snapshot, entry)
    if budget:
        return budget
    if snapshot["recovery"]["decision"] == "RELEASED":
        return {
            "decision": ROLLOVER_REQUIRED,
            "end_turn": False,
            "action": entry["action"],
            "sequence": sequence + 1,
            "stop_reason": "NONE",
            "requires_rollover": True,
            "next_step": "Roll the RELEASED journal over to a pristine IDLE journal, confirm recover returns DISPATCH_ALLOWED, then call next again.",
            "next_command": stop_reasons.sdd_command() + " next",
            "event": _event(snapshot, action=entry["action"], decision=ROLLOVER_REQUIRED, stop_reason="NONE"),
        }
    if snapshot["recovery"]["decision"] != "DISPATCH_ALLOWED":
        return _recovery_stop(snapshot)
    return {
        "decision": EXECUTE_NEXT,
        "end_turn": False,
        "action": entry["action"],
        "action_id": entry["id"],
        "sequence": sequence + 1,
        "stop_reason": "NONE",
        "requires_rollover": False,
        "next_step": f"Execute {entry['action']} now through the printed batch of `sdd.py next`, then call the driver again.",
        "next_command": stop_reasons.sdd_command() + " next",
        "event": _event(snapshot, action=entry["action"], decision=EXECUTE_NEXT, stop_reason="NONE"),
    }


def inspect(snapshot: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    runtime = _validate(snapshot, plan)
    next_event = evaluate_next(snapshot, plan)
    return {
        "valid": True,
        "plan_id": runtime["current_plan_id"],
        "current_sequence": runtime["current_sequence"],
        "planned_action_count": runtime["planned_action_count"],
        "next": next_event["decision"],
        "terminal_reason": next_event.get("terminal_reason", next_event["stop_reason"]),
    }


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect or decide one approved bounded runtime action without executing it.")
    commands = parser.add_subparsers(dest="command", required=True)
    bind_parser = commands.add_parser("bind")
    bind_parser.add_argument("--snapshot", required=True)
    bind_parser.add_argument("--plan", required=True)
    bind_parser.add_argument("--started-at", required=True)
    bind_parser.add_argument("--approved-plan-sha256")
    bind_parser.add_argument("--json", action="store_true")
    for name in ("inspect", "validate", "next"):
        command = commands.add_parser(name)
        command.add_argument("--snapshot", required=True)
        command.add_argument("--plan", required=True)
        command.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        if args.command == "bind":
            result = bind(
                snapshot,
                plan,
                started_at=args.started_at,
                approved_plan_sha256=args.approved_plan_sha256,
            )
        elif args.command == "inspect":
            result = inspect(snapshot, plan)
        elif args.command == "validate":
            _validate(snapshot, plan)
            result = {"valid": True}
        else:
            result = evaluate_next(snapshot, plan)
        emit(result) if args.json else print(result["decision"] if "decision" in result else "VALID")
        return 0
    except (OSError, json.JSONDecodeError, DriverError, planner.PlannerError) as exc:
        step, command = planner.next_step_for(str(exc))
        result = {"valid": False, "errors": [str(exc)], "next_step": step, "next_command": command}
        emit(result) if getattr(args, "json", False) else print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
