#!/usr/bin/env python3
"""Create and validate deterministic previews for BOUNDED_AUTO rounds.

This module only processes JSON snapshots. It never reads STATE, invokes an
executor, runs a gate, mutates Git, or activates BOUNDED_AUTO.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

try:
    import jsonschema
except ImportError as exc:  # pragma: no cover - environment preflight
    raise RuntimeError("bounded_run_planner.py requires the existing jsonschema dependency") from exc

PLAN_VERSION = 1
LOCAL_DELIVERY_PLAN_VERSION = 2
TARGET = "NEXT_HUMAN_CHECKPOINT"
CREATED_AT = "1970-01-01T00:00:00Z"  # Deliberately stable: plan hashes are deterministic.
FSM_STAGES = ("SPECIFY", "CLARIFY", "PLAN", "TASKS", "IMPLEMENT", "TEST", "REVIEW", "DONE")
BUDGET_KEYS = (
    "stage_transitions", "executor_calls", "corrective_retries", "tdd_slices",
    "investigation_expansions", "review_cycles", "ci_runs", "external_mutations",
)
BUDGET_SOURCES = {
    "stage_transitions": ("stage_transitions", "used", "max"),
    "executor_calls": ("executor_calls", "used", "max"),
    "corrective_retries": ("corrective_retries", "used_current_action", "max_per_action"),
    "tdd_slices": ("tdd_slices", "used", "max"),
    "investigation_expansions": ("investigation_expansions", "used_current_stage", "max_per_stage"),
    "review_cycles": ("review_cycles", "used", "max"),
    "ci_runs": ("ci_runs", "used", "max"),
    "external_mutations": ("external_mutations", "used", "max"),
}
AUTO_SAFE = {
    "RECOVER_PENDING_ACTION", "TEST_FOCUSED", "FORMAT_DART_CHANGED_FILES", "ANALYZE",
    "EVALUATE_DONE_WITH_CI_DISABLED", "EVALUATE_DONE", "STATE_TRANSACTION_UPDATE",
    "VERIFY_BASELINE_OWNERSHIP", "EVALUATE_BUDGETS", "CLOSE_BOUNDED_RUN",
}
AUTO_WITH_BUDGET = {"SPECIFY", "CLARIFY", "PLAN", "TASKS", "IMPLEMENT_SLICE", "REVIEW", "CI"}
HUMAN_REQUIRED = {
    "REOPEN_STAGE_AFTER_RETRY_EXHAUSTED", "RESOLVE_RECOVERY", "PROTECTED_FILE_CHANGE",
    "MATERIAL_SCOPE_CHANGE", "ARCHITECTURE_DECISION", "BACKEND_MUTATION", "DEV_E2E",
    "COMMIT", "PUSH", "LINEAR_UPDATE", "OBSIDIAN_WRITE", "DESTRUCTIVE_ACTION",
    "EXTERNAL_CONTRACT_CHANGE", "WAIT_OR_MANUAL_REVIEW",
}
ACTION_TRANSITIONS = {
    "SPECIFY": ("SPECIFY", "CLARIFY"),
    "CLARIFY": ("CLARIFY", "PLAN"),
    "PLAN": ("PLAN", "TASKS"),
    "TASKS": ("TASKS", "IMPLEMENT"),
    "IMPLEMENT_SLICE": ("IMPLEMENT", "IMPLEMENT"),
    "TEST_FOCUSED": ("TEST", "TEST"),
    "FORMAT_DART_CHANGED_FILES": ("TEST", "TEST"),
    "ANALYZE": ("TEST", "REVIEW"),
    "REVIEW": ("REVIEW", "REVIEW"),
    "CI": ("REVIEW", "DONE"),
    "EVALUATE_DONE": ("REVIEW", "DONE"),
    "EVALUATE_DONE_WITH_CI_DISABLED": ("REVIEW", "DONE"),
    "CLOSE_BOUNDED_RUN": ("REVIEW", "DONE"),
}
EXTERNAL_MUTATION_ACTIONS = {
    "BACKEND_MUTATION", "DEV_E2E", "COMMIT", "PUSH", "LINEAR_UPDATE",
    "OBSIDIAN_WRITE", "DESTRUCTIVE_ACTION", "EXTERNAL_CONTRACT_CHANGE",
}


class PlannerError(ValueError):
    """A snapshot, plan, or CLI request is invalid."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def zero_budgets() -> dict[str, int]:
    return {key: 0 for key in BUDGET_KEYS}


def budget_value_schema() -> dict[str, Any]:
    return {
        "type": "object", "additionalProperties": False, "required": list(BUDGET_KEYS),
        "properties": {key: {"type": "integer", "minimum": 0} for key in BUDGET_KEYS},
    }


def local_delivery_schema() -> dict[str, Any]:
    budget_values = budget_value_schema()
    workspace = {"type": "object", "additionalProperties": False, "required": ["path", "branch", "head", "git_common_dir"], "properties": {"path": {"type": "string", "minLength": 1}, "branch": {"type": "string", "minLength": 1}, "head": {"type": "string", "pattern": "^[a-f0-9]{40,64}$"}, "git_common_dir": {"type": "string", "minLength": 1}}}
    authorization = {"type": "object", "additionalProperties": False, "required": ["id", "request_evidence", "ticket", "canonical_scope", "scope_sha256", "workspace", "total_limits"], "properties": {"id": {"type": "string", "minLength": 1}, "request_evidence": {"type": "string", "minLength": 1}, "ticket": {"type": "string", "minLength": 1}, "canonical_scope": {"type": "string", "minLength": 1}, "scope_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"}, "workspace": workspace, "total_limits": budget_values}}
    return {"type": "object", "additionalProperties": False, "required": ["authorization", "usage_ledger"], "properties": {"authorization": authorization, "usage_ledger": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["action_id", "cost"], "properties": {"action_id": {"type": "string", "minLength": 1}, "cost": budget_values}}}}}


def snapshot_schema() -> dict[str, Any]:
    integer = {"type": "integer", "minimum": 0}
    budget = {"type": "object", "additionalProperties": False, "required": ["max", "used"], "properties": {"max": integer, "used": integer}}
    return {
        "type": "object", "additionalProperties": False,
        "required": ["schema_version", "workspace", "state", "loop", "gates", "implementation", "recovery", "restrictions"],
        "properties": {
            "schema_version": {"enum": [1, 2]},
            "local_delivery": local_delivery_schema(),
            "workspace": {"type": "object", "additionalProperties": False, "required": ["path", "branch", "head", "git_common_dir"], "properties": {"path": {"type": "string"}, "branch": {"type": "string"}, "head": {"type": "string"}, "git_common_dir": {"type": "string"}}},
            "state": {"type": "object", "additionalProperties": False, "required": ["sha256", "ticket", "stage", "status", "completed", "skipped", "next_action", "blockers", "protected_preexisting", "agent_owned"], "properties": {"sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"}, "ticket": {"type": "string"}, "stage": {"enum": list(FSM_STAGES)}, "status": {"type": "string"}, "completed": {"type": "array", "items": {"type": "string"}}, "skipped": {"type": "array", "items": {"type": "string"}}, "next_action": {"type": ["string", "null"]}, "blockers": {"type": "array", "items": {"type": "string"}}, "protected_preexisting": {"type": "array", "items": {"type": "string"}}, "agent_owned": {"type": "array", "items": {"type": "string"}}}},
            "loop": {"type": "object", "additionalProperties": False, "required": ["mode", "loop_active", "budgets", "stop_reason", "human_approval_required"], "properties": {"mode": {"enum": ["MANUAL", "PAUSED", "BOUNDED_AUTO"]}, "loop_active": {"type": "boolean"}, "budgets": {"type": "object", "additionalProperties": False, "required": list(BUDGET_KEYS), "properties": {**{key: budget for key in ("stage_transitions", "executor_calls", "tdd_slices", "review_cycles", "ci_runs", "external_mutations")}, "corrective_retries": {"type": "object", "additionalProperties": False, "required": ["max_per_action", "used_current_action"], "properties": {"max_per_action": integer, "used_current_action": integer}}, "investigation_expansions": {"type": "object", "additionalProperties": False, "required": ["max_per_stage", "used_current_stage"], "properties": {"max_per_stage": integer, "used_current_stage": integer}}}}, "stop_reason": {"type": "string"}, "human_approval_required": {"type": "boolean"}}},
            "gates": {"type": "object", "additionalProperties": False, "required": ["focused_tests", "format", "analyze", "review", "ci", "project_ci_enabled"], "properties": {"focused_tests": {"type": "string"}, "format": {"type": "string"}, "analyze": {"type": "string"}, "review": {"type": "string"}, "ci": {"type": "string"}, "project_ci_enabled": {"type": "boolean"}}},
            "implementation": {"type": "object", "additionalProperties": False, "required": ["planned_slices", "completed_slices", "next_slice", "all_slices_green", "canonical_focused_test_command_available", "changed_dart_files_available"], "properties": {"planned_slices": {"type": "array", "items": {"type": "string"}}, "completed_slices": {"type": "array", "items": {"type": "string"}}, "next_slice": {"type": ["string", "null"]}, "all_slices_green": {"type": "boolean"}, "canonical_focused_test_command_available": {"type": "boolean"}, "changed_dart_files_available": {"type": "boolean"}}},
            "recovery": {"type": "object", "additionalProperties": False, "required": ["decision", "journal_status", "current_action_id", "artifact_present"], "properties": {"decision": {"enum": ["DISPATCH_ALLOWED", "RECONCILE_ARTIFACT", "CORRECTIVE_RETRY_AVAILABLE", "STATE_COMMIT_REQUIRED", "ALREADY_COMMITTED", "WAIT_OR_MANUAL_REVIEW", "BLOCKED", "RELEASED"]}, "journal_status": {"type": "string"}, "current_action_id": {"type": ["string", "null"]}, "artifact_present": {"type": "boolean"}}},
            "restrictions": {"type": "object", "additionalProperties": False, "required": ["external_mutation_requested", "protected_file_required", "scope_change_required", "architecture_decision_required"], "properties": {"external_mutation_requested": {"type": "boolean"}, "protected_file_required": {"type": "boolean"}, "scope_change_required": {"type": "boolean"}, "architecture_decision_required": {"type": "boolean"}}},
        },
        "allOf": [
            {"if": {"properties": {"schema_version": {"const": 2}}}, "then": {"required": ["local_delivery"]}},
            {"if": {"properties": {"schema_version": {"const": 1}}}, "then": {"not": {"required": ["local_delivery"]}}},
        ],
    }


def validate_snapshot(value: Any) -> None:
    errors = sorted(jsonschema.Draft202012Validator(snapshot_schema()).iter_errors(value), key=lambda error: list(error.path))
    if errors:
        raise PlannerError("INVALID_SNAPSHOT: " + "; ".join(error.message for error in errors[:3]))
    for key, (source, used_key, max_key) in BUDGET_SOURCES.items():
        if value["loop"]["budgets"][source][used_key] > value["loop"]["budgets"][source][max_key]:
            raise PlannerError(f"INVALID_SNAPSHOT: budget {key} used exceeds its maximum")
    if value["schema_version"] == 2:
        validate_local_delivery_snapshot(value)


def local_delivery_usage(snapshot: dict[str, Any]) -> dict[str, int]:
    used = zero_budgets()
    for entry in snapshot["local_delivery"]["usage_ledger"]:
        for key in BUDGET_KEYS:
            used[key] += entry["cost"][key]
    return used


def validate_local_delivery_snapshot(snapshot: dict[str, Any]) -> None:
    local_delivery = snapshot["local_delivery"]
    authorization = local_delivery["authorization"]
    if snapshot["loop"]["mode"] != "BOUNDED_AUTO":
        raise PlannerError("INVALID_SNAPSHOT: LOCAL_DELIVERY requires BOUNDED_AUTO mode")
    if not snapshot["loop"]["loop_active"]:
        raise PlannerError("INVALID_SNAPSHOT: LOCAL_DELIVERY requires an active loop")
    if authorization["total_limits"]["external_mutations"] != 0:
        raise PlannerError("INVALID_SNAPSHOT: LOCAL_DELIVERY requires zero external_mutations")
    if authorization["scope_sha256"] != sha256_json(authorization["canonical_scope"]):
        raise PlannerError("INVALID_SNAPSHOT: authorization scope_sha256 does not match canonical_scope")
    if authorization["ticket"] != snapshot["state"]["ticket"]:
        raise PlannerError("INVALID_SNAPSHOT: authorization ticket drifts from state ticket")
    if authorization["workspace"] != snapshot["workspace"]:
        raise PlannerError("INVALID_SNAPSHOT: authorization workspace drifts from snapshot workspace")

    action_ids = [entry["action_id"] for entry in local_delivery["usage_ledger"]]
    if len(action_ids) != len(set(action_ids)):
        raise PlannerError("INVALID_SNAPSHOT: duplicate ledger action_id")

    ledger_usage = local_delivery_usage(snapshot)
    for key, (source, used_key, limit_key) in BUDGET_SOURCES.items():
        raw_loop_budget = snapshot["loop"]["budgets"][source]
        authorized_limit = authorization["total_limits"][key]
        if raw_loop_budget[limit_key] != authorized_limit:
            raise PlannerError("INVALID_SNAPSHOT: loop budget limits drift from authorization total_limits")
        if raw_loop_budget[used_key] != ledger_usage[key]:
            raise PlannerError("INVALID_SNAPSHOT: loop budget use drifts from cumulative usage ledger")
        if ledger_usage[key] > authorized_limit:
            raise PlannerError(f"INVALID_SNAPSHOT: budget {key} used exceeds its maximum")


def plan_schema() -> dict[str, Any]:
    return json.loads(Path(__file__).with_name("BOUNDED_RUN_PLAN_SCHEMA.json").read_text(encoding="utf-8"))


def classify_action(action: str) -> dict[str, Any]:
    if action in AUTO_SAFE:
        classification, executor = "AUTO_SAFE", "HOST"
    elif action in AUTO_WITH_BUDGET:
        classification, executor = "AUTO_WITH_BUDGET", "HOST" if action == "CI" else "CODEX"
    elif action in HUMAN_REQUIRED:
        classification, executor = "HUMAN_REQUIRED", "NONE"
    else:
        raise PlannerError(f"UNKNOWN_ACTION: {action}")
    return {"action": action, "classification": classification, "executor": executor, "human_approval_required": classification == "HUMAN_REQUIRED"}


def budget_values(snapshot: dict[str, Any]) -> tuple[dict[str, int], dict[str, int]]:
    limits, used = zero_budgets(), zero_budgets()
    for key, (source, used_key, max_key) in BUDGET_SOURCES.items():
        limits[key] = snapshot["loop"]["budgets"][source][max_key]
        used[key] = snapshot["loop"]["budgets"][source][used_key]
    if snapshot.get("schema_version") == 2:
        return snapshot["local_delivery"]["authorization"]["total_limits"], local_delivery_usage(snapshot)
    return limits, used


def cost_for(action: str, *, stage_transition: bool = False) -> dict[str, int]:
    cost = zero_budgets()
    if stage_transition:
        cost["stage_transitions"] = 1
    if action in AUTO_WITH_BUDGET:
        cost["executor_calls"] = 1
    if action == "IMPLEMENT_SLICE":
        cost["tdd_slices"] = 1
    if action == "REVIEW":
        cost["review_cycles"] = 1
    if action == "CI":
        cost["ci_runs"] = 1
    return cost


def canonical_action_spec(action: str, stage: str, transition: str) -> dict[str, Any]:
    kind = classify_action(action)
    expected_transition = ACTION_TRANSITIONS.get(action)
    if expected_transition is not None:
        if (stage, transition) != expected_transition:
            raise PlannerError(f"INVALID_ACTION_TRANSITION: {action} must use {expected_transition[0]} -> {expected_transition[1]}")
    elif stage != transition:
        raise PlannerError(f"INVALID_ACTION_TRANSITION: {action} must remain in {stage}")
    return {
        **kind,
        "budget_cost": cost_for(action, stage_transition=stage != transition),
        "external_mutation": action in EXTERNAL_MUTATION_ACTIONS,
    }


def action_entry(sequence: int, stage: str, action: str, *, transition: str, conditional: bool = True) -> dict[str, Any]:
    spec = canonical_action_spec(action, stage, transition)
    return {
        "sequence": sequence, "id": f"action-{sequence}", "stage": stage, "action": action,
        "classification": spec["classification"], "executor": spec["executor"], "budget_cost": spec["budget_cost"],
        "preconditions": ["approved_plan_hash_matches", "state_identity_unchanged", "recovery_probe_valid"],
        "success_transition": transition,
        "stop_conditions": ["BLOCKER", "CONTRACT_FAILURE", "GATE_FAILURE", "BUDGET_REACHED", "HUMAN_REQUIRED", "PLAN_STALE"],
        "conditional_on_previous_success": conditional, "external_mutation": spec["external_mutation"],
        "human_approval_required": spec["human_approval_required"],
    }


def create_plan(snapshot: dict[str, Any], target: str = TARGET) -> dict[str, Any]:
    validate_snapshot(snapshot)
    if target != TARGET:
        raise PlannerError(f"UNSUPPORTED_TARGET: {target}")
    limits, used = budget_values(snapshot)
    projected = zero_budgets()
    entries: list[dict[str, Any]] = []
    termination = "NEXT_HUMAN_CHECKPOINT"
    warnings: list[str] = ["Preview only: this plan cannot activate BOUNDED_AUTO or execute an action."]
    stage, status = snapshot["state"]["stage"], snapshot["state"]["status"]

    def append(action: str, action_stage: str, transition: str) -> bool:
        nonlocal termination
        spec = canonical_action_spec(action, action_stage, transition)
        cost = spec["budget_cost"]
        exhausted = next((key for key in BUDGET_KEYS if used[key] + projected[key] + cost[key] > limits[key]), None)
        if exhausted:
            termination = "BUDGET_REACHED"
            warnings.append(f"Budget reached before {action}: {exhausted}.")
            return False
        projected.update({key: projected[key] + cost[key] for key in BUDGET_KEYS})
        entries.append(action_entry(len(entries) + 1, action_stage, action, transition=transition))
        return True

    restrictions = snapshot["restrictions"]
    restriction_action = next(
        (
            candidate
            for candidate in (
                ("PROTECTED_FILE_CHANGE", "protected_file_required"),
                ("MATERIAL_SCOPE_CHANGE", "scope_change_required"),
                ("BACKEND_MUTATION", "external_mutation_requested"),
                ("ARCHITECTURE_DECISION", "architecture_decision_required"),
            )
            if restrictions[candidate[1]]
        ),
        None,
    )
    if snapshot["state"]["blockers"] or snapshot["recovery"]["decision"] == "BLOCKED":
        termination = "BLOCKED"
        warnings.append("Active blocker prevents automatic planning.")
    elif restriction_action:
        append(restriction_action[0], stage, stage)
        termination = "HUMAN_REQUIRED"
    elif snapshot["loop"]["human_approval_required"]:
        append("WAIT_OR_MANUAL_REVIEW", stage, stage)
        termination = "HUMAN_REQUIRED"
    else:
        recovery = snapshot["recovery"]["decision"]
        if recovery == "WAIT_OR_MANUAL_REVIEW":
            append("WAIT_OR_MANUAL_REVIEW", stage, stage)
            termination = "HUMAN_REQUIRED"
        elif recovery in {"RECONCILE_ARTIFACT", "STATE_COMMIT_REQUIRED"}:
            append("RECOVER_PENDING_ACTION", stage, stage)
            termination = "RECOVERY_RECONCILIATION_REQUIRED"
        elif recovery == "CORRECTIVE_RETRY_AVAILABLE":
            append("RESOLVE_RECOVERY", stage, stage)
            termination = "HUMAN_REQUIRED"
        elif stage == "REVIEW" and status == "APPROVED" and not completion_gates_are_compatible(snapshot["gates"]):
            termination = "BLOCKED"
            warnings.append("Completion gates contradict the approved REVIEW state.")
        else:
            plan_normal_path(snapshot, stage, status, append)

    remaining = {key: limits[key] - used[key] - projected[key] for key in BUDGET_KEYS}
    payload = {
        "plan_version": PLAN_VERSION,
        "plan_id": "brp-" + sha256_json({"snapshot": snapshot, "target": target})[:16],
        "created_at": CREATED_AT,
        "input": {"state_sha256": snapshot["state"]["sha256"], "workspace_path": snapshot["workspace"]["path"], "git_common_dir": snapshot["workspace"]["git_common_dir"], "branch": snapshot["workspace"]["branch"], "head": snapshot["workspace"]["head"], "ticket": snapshot["state"]["ticket"], "start_stage": stage, "start_status": status, "start_mode": snapshot["loop"]["mode"], "target_checkpoint": target},
        "authorization": {"required": True, "plan_sha256": "0" * 64, "expires_on_state_change": True},
        "budgets": {"limits": limits, "currently_used": used, "projected_use": projected, "remaining_after_plan": remaining},
        "recovery": {"decision": snapshot["recovery"]["decision"], "first_action": entries[0]["action"] if entries else None},
        "implementation_baseline": {
            "planned_slices": list(snapshot["implementation"]["planned_slices"]),
            "completed_slices": list(snapshot["implementation"]["completed_slices"]),
        },
        "gate_baseline": copy.deepcopy(snapshot["gates"]),
        "actions": entries,
        "termination": {"expected_reason": termination, "expected_stage": entries[-1]["success_transition"] if entries else stage, "expected_status": "PAUSED" if termination != "BLOCKED" else "BLOCKED", "next_human_checkpoint": "PLAN_APPROVAL_OR_STOP", "planned_action_count": len(entries)},
        "warnings": warnings,
    }
    if snapshot["schema_version"] == 2:
        authorization = copy.deepcopy(snapshot["local_delivery"]["authorization"])
        payload["plan_version"] = LOCAL_DELIVERY_PLAN_VERSION
        payload["local_delivery"] = {
            "authorization": authorization,
            "authorization_sha256": sha256_json(authorization),
            "usage_floor": copy.deepcopy(used),
            "reserved_use": copy.deepcopy(projected),
        }
    payload["authorization"]["plan_sha256"] = hash_plan(payload)
    return payload


def completion_gates_are_compatible(gates: dict[str, Any]) -> bool:
    if (
        gates["focused_tests"] != "PASS"
        or gates["format"] != "PASS"
        or gates["analyze"] != "PASS"
        or gates["review"] != "APPROVED"
    ):
        return False
    return (
        gates["ci"] == "PENDING"
        if gates["project_ci_enabled"]
        else gates["ci"] == "DISABLED_BY_PROJECT_POLICY"
    )


def test_gate_sequence(gates: dict[str, Any]) -> list[str]:
    """Return the required TEST actions from an immutable gate baseline."""
    sequence: list[str] = []
    if gates["focused_tests"] != "PASS":
        sequence.append("TEST_FOCUSED")
    if gates["format"] != "PASS":
        sequence.append("FORMAT_DART_CHANGED_FILES")
    if gates["analyze"] != "PASS":
        sequence.append("ANALYZE")
    return [*sequence, "REVIEW"]


def plan_normal_path(snapshot: dict[str, Any], stage: str, status: str, append: Any) -> None:
    gates, impl = snapshot["gates"], snapshot["implementation"]
    sequence = {"SPECIFY": [("SPECIFY", "CLARIFY"), ("CLARIFY", "PLAN"), ("PLAN", "TASKS"), ("TASKS", "IMPLEMENT")]}
    if stage == "SPECIFY":
        for action, transition in sequence["SPECIFY"]:
            if not append(action, action, transition):
                return
    elif stage == "CLARIFY":
        for action, transition in [("CLARIFY", "PLAN"), ("PLAN", "TASKS"), ("TASKS", "IMPLEMENT")]:
            if not append(action, action, transition): return
    elif stage == "PLAN":
        for action, transition in [("PLAN", "TASKS"), ("TASKS", "IMPLEMENT")]:
            if not append(action, action, transition): return
    elif stage == "TASKS":
        append("TASKS", "TASKS", "IMPLEMENT")
    elif stage == "IMPLEMENT":
        if not impl["all_slices_green"]:
            remaining_slices = [item for item in impl["planned_slices"] if item not in impl["completed_slices"]]
            for _slice in remaining_slices:
                if not append("IMPLEMENT_SLICE", "IMPLEMENT", "IMPLEMENT"): return
        else:
            for action in test_gate_sequence(gates):
                transition = "REVIEW" if action == "ANALYZE" else "TEST"
                stage = "REVIEW" if action == "REVIEW" else "TEST"
                if not append(action, stage, transition if action != "REVIEW" else "REVIEW"):
                    return
    elif stage == "TEST":
        for action in test_gate_sequence(gates):
            transition = "REVIEW" if action == "ANALYZE" else "TEST"
            action_stage = "REVIEW" if action == "REVIEW" else "TEST"
            if not append(action, action_stage, transition if action != "REVIEW" else "REVIEW"):
                return
    elif stage == "REVIEW" and status == "APPROVED":
        if gates["project_ci_enabled"]:
            append("CI", "REVIEW", "DONE")
        else:
            append("EVALUATE_DONE_WITH_CI_DISABLED", "REVIEW", "DONE")


def hash_plan(plan: dict[str, Any]) -> str:
    material = copy.deepcopy(plan)
    material["authorization"].pop("plan_sha256", None)
    return sha256_json(material)


def _is_prefix(actions: list[str], sequence: list[str]) -> bool:
    return actions == sequence[:len(actions)]


def _test_prefix_is_budget_limited(plan: dict[str, Any], sequence: list[str]) -> bool:
    names = [entry["action"] for entry in plan["actions"]]
    if len(names) == len(sequence):
        return True
    if plan["termination"]["expected_reason"] != "BUDGET_REACHED":
        return False
    next_action = sequence[len(names)]
    cost = cost_for(next_action, stage_transition=next_action == "ANALYZE")
    return any(
        plan["budgets"]["currently_used"][key]
        + plan["budgets"]["projected_use"][key]
        + cost[key]
        > plan["budgets"]["limits"][key]
        for key in BUDGET_KEYS
    )


def _actions_match_planner_grammar(plan: dict[str, Any]) -> bool:
    """Accept only ordered subsequences that ``create_plan`` can emit.

    Budget exhaustion permits prefixes. TEST action requirements are derived
    from the immutable gate baseline; every normal path is a prefix. Terminal
    recovery and restriction plans contain exactly one known
    terminal action (or no action when a budget blocks it).
    """
    names = [entry["action"] for entry in plan["actions"]]
    if not names:
        return True
    start_stage = plan["input"]["start_stage"]
    start_status = plan["input"]["start_status"]
    terminal_actions = {
        "PROTECTED_FILE_CHANGE", "MATERIAL_SCOPE_CHANGE", "BACKEND_MUTATION",
        "ARCHITECTURE_DECISION", "WAIT_OR_MANUAL_REVIEW",
        "RECOVER_PENDING_ACTION", "RESOLVE_RECOVERY",
    }
    if names[0] in terminal_actions:
        entry = plan["actions"][0]
        return (
            len(names) == 1
            and entry["stage"] == start_stage
            and entry["success_transition"] == start_stage
        )

    normal_paths = {
        "SPECIFY": ["SPECIFY", "CLARIFY", "PLAN", "TASKS"],
        "CLARIFY": ["CLARIFY", "PLAN", "TASKS"],
        "PLAN": ["PLAN", "TASKS"],
        "TASKS": ["TASKS"],
    }
    if start_stage in normal_paths:
        return _is_prefix(names, normal_paths[start_stage])
    if start_stage == "IMPLEMENT":
        baseline = plan["implementation_baseline"]
        remaining_slices = [
            item for item in baseline["planned_slices"]
            if item not in baseline["completed_slices"]
        ]
        test_sequence = test_gate_sequence(plan["gate_baseline"])
        return (
            _is_prefix(names, ["IMPLEMENT_SLICE"] * len(remaining_slices))
            or (
                _is_prefix(names, test_sequence)
                and _test_prefix_is_budget_limited(plan, test_sequence)
            )
        )
    if start_stage == "TEST":
        sequence = test_gate_sequence(plan["gate_baseline"])
        return _is_prefix(names, sequence) and _test_prefix_is_budget_limited(plan, sequence)
    if start_stage == "REVIEW" and start_status == "APPROVED":
        gates = plan["gate_baseline"]
        if not completion_gates_are_compatible(gates):
            return False
        expected = "CI" if gates["project_ci_enabled"] else "EVALUATE_DONE_WITH_CI_DISABLED"
        return names == [expected]
    return False


def validate_plan_actions(plan: dict[str, Any], *, source_snapshot: dict[str, Any] | None = None) -> str | None:
    """Validate canonical entries and their ordered planner grammar.

    Drivers use this shared validator without a historical snapshot; plan
    validation also supplies the source snapshot to bind the list exactly to
    the deterministic planner output.
    """
    actions = plan["actions"]
    for expected_sequence, entry in enumerate(actions, start=1):
        if entry["sequence"] != expected_sequence or entry["id"] != f"action-{expected_sequence}":
            return f"INVALID_PLAN: action {expected_sequence} has a non-contiguous sequence or id"
        try:
            spec = canonical_action_spec(entry["action"], entry["stage"], entry["success_transition"])
        except PlannerError as exc:
            return f"INVALID_PLAN: action {expected_sequence} contradicts the canonical contract: {exc}"
        for field in ("classification", "executor", "human_approval_required", "budget_cost", "external_mutation"):
            if entry[field] != spec[field]:
                return f"INVALID_PLAN: action {expected_sequence} {field} contradicts the canonical contract"
    if not _actions_match_planner_grammar(plan):
        return "INVALID_PLAN: actions are not an ordered subsequence of the planner grammar"
    if source_snapshot is not None and actions != create_plan(source_snapshot)["actions"]:
        return "INVALID_PLAN: actions do not match the source snapshot planner output"
    return None


def validate_plan(plan: Any, snapshot: Any) -> list[str]:
    try:
        validate_snapshot(snapshot)
    except PlannerError as exc:
        return [str(exc)]
    errors = sorted(jsonschema.Draft202012Validator(plan_schema()).iter_errors(plan), key=lambda error: list(error.path))
    if errors:
        return ["INVALID_PLAN: " + "; ".join(error.message for error in errors[:3])]
    if plan["authorization"]["plan_sha256"] != hash_plan(plan):
        return ["INVALID_PLAN: plan_sha256 does not match canonical plan content"]
    if snapshot["schema_version"] == 2:
        local_error = validate_local_delivery_plan(plan, snapshot)
        if local_error:
            return [local_error]
    semantic_error = validate_plan_actions(plan)
    if semantic_error:
        return [semantic_error]
    stale = (
        plan["input"]["state_sha256"] != snapshot["state"]["sha256"]
        or plan["input"]["workspace_path"] != snapshot["workspace"]["path"]
        or plan["input"]["git_common_dir"] != snapshot["workspace"]["git_common_dir"]
        or plan["input"]["branch"] != snapshot["workspace"]["branch"]
        or plan["input"]["head"] != snapshot["workspace"]["head"]
        or plan["recovery"]["decision"] != snapshot["recovery"]["decision"]
        or plan["budgets"]["limits"] != budget_values(snapshot)[0]
        or plan["budgets"]["currently_used"] != budget_values(snapshot)[1]
        or plan["gate_baseline"] != snapshot["gates"]
    )
    if stale:
        return ["PLAN_STALE"]
    source_error = validate_plan_actions(plan, source_snapshot=snapshot)
    if source_error:
        return [source_error]
    return []


def validate_local_delivery_plan(plan: dict[str, Any], snapshot: dict[str, Any]) -> str | None:
    if plan["plan_version"] != LOCAL_DELIVERY_PLAN_VERSION:
        return "INVALID_PLAN: LOCAL_DELIVERY requires plan version 2"
    local = plan.get("local_delivery")
    if local is None:
        return "INVALID_PLAN: LOCAL_DELIVERY plan lacks local_delivery binding"
    authorization = snapshot["local_delivery"]["authorization"]
    if local["authorization"] != authorization:
        return "INVALID_PLAN: LOCAL_DELIVERY authorization drift"
    if local["authorization_sha256"] != sha256_json(authorization):
        return "INVALID_PLAN: LOCAL_DELIVERY authorization hash drift"
    _, used = budget_values(snapshot)
    if local["usage_floor"] != used:
        return "INVALID_PLAN: LOCAL_DELIVERY usage floor regressed or drifted"
    if local["reserved_use"] != plan["budgets"]["projected_use"]:
        return "INVALID_PLAN: LOCAL_DELIVERY reservation drift"
    limits = authorization["total_limits"]
    for key in BUDGET_KEYS:
        if used[key] + local["reserved_use"][key] > limits[key]:
            return "INVALID_PLAN: LOCAL_DELIVERY reservation exceeds total authorization"
    return None


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create deterministic BOUNDED_AUTO previews without executing them.")
    commands = parser.add_subparsers(dest="command", required=True)
    plan_parser = commands.add_parser("plan")
    plan_parser.add_argument("--snapshot", required=True)
    plan_parser.add_argument("--target", required=True, choices=[TARGET])
    plan_parser.add_argument("--output")
    plan_parser.add_argument("--json", action="store_true")
    validate_parser = commands.add_parser("validate")
    validate_parser.add_argument("--plan", required=True)
    validate_parser.add_argument("--snapshot", required=True)
    validate_parser.add_argument("--json", action="store_true")
    classify_parser = commands.add_parser("classify")
    classify_parser.add_argument("--action", required=True)
    classify_parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "classify":
            result = classify_action(args.action)
            emit(result) if args.json else print(result["classification"])
            return 0
        if args.command == "plan":
            result = create_plan(json.loads(Path(args.snapshot).read_text(encoding="utf-8")), args.target)
            if args.output:
                Path(args.output).write_bytes(canonical_json(result) + b"\n")
            emit(result) if args.json else print(result["authorization"]["plan_sha256"])
            return 0
        errors = validate_plan(json.loads(Path(args.plan).read_text(encoding="utf-8")), json.loads(Path(args.snapshot).read_text(encoding="utf-8")))
        result = {"valid": not errors, "errors": errors}
        emit(result) if args.json else print("VALID" if not errors else "; ".join(errors))
        return 0 if not errors else 2
    except (OSError, json.JSONDecodeError, PlannerError) as exc:
        result = {"valid": False, "errors": [str(exc)]}
        emit(result) if getattr(args, "json", False) else print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
