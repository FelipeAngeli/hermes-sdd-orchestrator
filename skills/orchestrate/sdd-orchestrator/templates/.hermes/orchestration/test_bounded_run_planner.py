"""Synthetic isolated tests for the deterministic bounded-run planner."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bounded_run_planner as planner  # noqa: E402


ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "bounded_run_planner.py"


def snapshot(*, stage: str = "SPECIFY", status: str = "IN_PROGRESS", mode: str = "MANUAL") -> dict:
    return {
        "schema_version": 1,
        "workspace": {
            "path": "/tmp/worktree with spaces",
            "branch": "dev",
            "head": "a" * 40,
            "git_common_dir": "/tmp/repository/.git",
        },
        "state": {
            "sha256": "b" * 64,
            "ticket": "APP-999",
            "stage": stage,
            "status": status,
            "completed": [],
            "skipped": [],
            "next_action": None,
            "blockers": [],
            "protected_preexisting": [],
            "agent_owned": [],
        },
        "loop": {
            "mode": mode,
            "loop_active": False,
            "budgets": {
                "stage_transitions": {"max": 3, "used": 0},
                "executor_calls": {"max": 8, "used": 0},
                "corrective_retries": {"max_per_action": 1, "used_current_action": 0},
                "tdd_slices": {"max": 3, "used": 0},
                "investigation_expansions": {"max_per_stage": 1, "used_current_stage": 0},
                "review_cycles": {"max": 2, "used": 0},
                "ci_runs": {"max": 1, "used": 0},
                "external_mutations": {"max": 0, "used": 0},
            },
            "stop_reason": "NONE",
            "human_approval_required": False,
        },
        "gates": {
            "focused_tests": "PENDING",
            "format": "PENDING",
            "analyze": "PENDING",
            "review": "PENDING",
            "ci": "PENDING",
            "project_ci_enabled": False,
        },
        "implementation": {
            "planned_slices": ["slice-1", "slice-2", "slice-3", "slice-4"],
            "completed_slices": [],
            "next_slice": "slice-1",
            "all_slices_green": False,
            "canonical_focused_test_command_available": True,
            "changed_dart_files_available": True,
        },
        "recovery": {
            "decision": "DISPATCH_ALLOWED",
            "journal_status": "IDLE",
            "current_action_id": None,
            "artifact_present": False,
        },
        "restrictions": {
            "external_mutation_requested": False,
            "protected_file_required": False,
            "scope_change_required": False,
            "architecture_decision_required": False,
        },
    }


def actions(plan: dict) -> list[str]:
    return [action["action"] for action in plan["actions"]]


def local_delivery_snapshot() -> dict:
    value = snapshot(mode="BOUNDED_AUTO")
    value["loop"]["loop_active"] = True
    value["schema_version"] = 2
    limits = planner.zero_budgets()
    for key, budget in value["loop"]["budgets"].items():
        limits[key] = budget.get("max", budget.get("max_per_action", budget.get("max_per_stage")))
    value["local_delivery"] = {
        "authorization": {
            "id": "local-request-001",
            "request_evidence": "User explicitly requested LOCAL_DELIVERY for APP-999.",
            "ticket": "APP-999",
            "canonical_scope": "bounded local planner contract",
            "scope_sha256": planner.sha256_json("bounded local planner contract"),
            "workspace": copy.deepcopy(value["workspace"]),
            "total_limits": limits,
        },
        "usage_ledger": [],
    }
    return value


def inject_canonical_state_transaction_update(plan: dict) -> None:
    plan["actions"].insert(
        1,
        planner.action_entry(
            2,
            "CLARIFY",
            "STATE_TRANSACTION_UPDATE",
            transition="CLARIFY",
        ),
    )
    for sequence, entry in enumerate(plan["actions"], start=1):
        entry.update({"sequence": sequence, "id": f"action-{sequence}"})


class BoundedRunPlannerTests(unittest.TestCase):
    def plan(self, value: dict) -> dict:
        result = planner.create_plan(value, "NEXT_HUMAN_CHECKPOINT")
        self.assertEqual([], planner.validate_plan(result, value))
        return result

    def test_manual_plan_requires_future_exact_authorization(self) -> None:
        plan = self.plan(snapshot())
        self.assertTrue(plan["authorization"]["required"])
        self.assertTrue(plan["authorization"]["expires_on_state_change"])
        self.assertEqual("MANUAL", plan["input"]["start_mode"])
        self.assertNotEqual("BOUNDED_AUTO", plan["input"]["start_mode"])

    def test_local_delivery_binds_projection_to_immutable_request_and_cumulative_floor(self) -> None:
        value = local_delivery_snapshot()
        plan = self.plan(value)

        local = plan["local_delivery"]
        self.assertEqual(2, plan["plan_version"])
        self.assertEqual("local-request-001", local["authorization"]["id"])
        self.assertEqual(planner.sha256_json(local["authorization"]), local["authorization_sha256"])
        self.assertEqual(planner.zero_budgets(), local["usage_floor"])
        self.assertEqual(plan["budgets"]["projected_use"], local["reserved_use"])

    def test_local_delivery_requires_bounded_auto_active_and_zero_external_mutations(self) -> None:
        valid = local_delivery_snapshot()
        self.plan(valid)

        manual = copy.deepcopy(valid)
        manual["loop"]["mode"] = "MANUAL"
        with self.assertRaisesRegex(planner.PlannerError, "LOCAL_DELIVERY requires BOUNDED_AUTO mode"):
            planner.create_plan(manual)

        paused = copy.deepcopy(valid)
        paused["loop"]["mode"] = "PAUSED"
        with self.assertRaisesRegex(planner.PlannerError, "LOCAL_DELIVERY requires BOUNDED_AUTO mode"):
            planner.create_plan(paused)

        inactive = copy.deepcopy(valid)
        inactive["loop"]["loop_active"] = False
        with self.assertRaisesRegex(planner.PlannerError, "LOCAL_DELIVERY requires an active loop"):
            planner.create_plan(inactive)

        external_mutations = copy.deepcopy(valid)
        external_mutations["local_delivery"]["authorization"]["total_limits"]["external_mutations"] = 1
        external_mutations["loop"]["budgets"]["external_mutations"]["max"] = 1
        with self.assertRaisesRegex(planner.PlannerError, "LOCAL_DELIVERY requires zero external_mutations"):
            planner.create_plan(external_mutations)

    def test_local_delivery_ledger_is_cumulative_and_rejects_duplicates_overuse_and_drift(self) -> None:
        value = local_delivery_snapshot()
        cost = planner.zero_budgets()
        cost["executor_calls"] = 1
        value["local_delivery"]["usage_ledger"] = [{"action_id": "action-a", "cost": cost}]
        value["loop"]["budgets"]["executor_calls"]["used"] = 1
        plan = self.plan(value)
        self.assertEqual(1, plan["local_delivery"]["usage_floor"]["executor_calls"])

        duplicate = copy.deepcopy(value)
        duplicate["local_delivery"]["usage_ledger"].append({"action_id": "action-a", "cost": cost})
        with self.assertRaisesRegex(planner.PlannerError, "duplicate ledger action_id"):
            planner.create_plan(duplicate)
        overuse = copy.deepcopy(value)
        overuse["local_delivery"]["usage_ledger"][0]["cost"]["executor_calls"] = 99
        overuse["loop"]["budgets"]["executor_calls"]["used"] = 99
        with self.assertRaisesRegex(planner.PlannerError, "used exceeds"):
            planner.create_plan(overuse)
        drift = copy.deepcopy(value)
        drift["local_delivery"]["authorization"]["ticket"] = "APP-OTHER"
        with self.assertRaisesRegex(planner.PlannerError, "authorization ticket"):
            planner.create_plan(drift)

    def test_local_delivery_plan_rejects_regressed_floor_or_authorization_hash(self) -> None:
        value = local_delivery_snapshot()
        cost = planner.zero_budgets()
        cost["executor_calls"] = 1
        value["local_delivery"]["usage_ledger"] = [{"action_id": "action-a", "cost": cost}]
        value["loop"]["budgets"]["executor_calls"]["used"] = 1
        plan = self.plan(value)
        plan["local_delivery"]["usage_floor"]["executor_calls"] = 0
        self.assertTrue(planner.validate_plan(plan, value)[0].startswith("INVALID_PLAN"))
        plan = self.plan(value)
        plan["local_delivery"]["authorization_sha256"] = "0" * 64
        plan["authorization"]["plan_sha256"] = planner.hash_plan(plan)
        self.assertTrue(planner.validate_plan(plan, value)[0].startswith("INVALID_PLAN"))

    def test_local_delivery_rejects_raw_loop_budget_limits_or_usage_drift(self) -> None:
        value = local_delivery_snapshot()
        cost = planner.zero_budgets()
        cost["executor_calls"] = 1
        value["local_delivery"]["usage_ledger"] = [{"action_id": "action-a", "cost": cost}]
        value["loop"]["budgets"]["executor_calls"]["used"] = 1

        limits_drift = copy.deepcopy(value)
        limits_drift["loop"]["budgets"]["executor_calls"]["max"] = 7
        with self.assertRaisesRegex(planner.PlannerError, "loop budget limits drift"):
            planner.create_plan(limits_drift)

        usage_reset = copy.deepcopy(value)
        usage_reset["loop"]["budgets"]["executor_calls"]["used"] = 0
        with self.assertRaisesRegex(planner.PlannerError, "loop budget use drifts"):
            planner.create_plan(usage_reset)

    def test_specify_healthy_plans_bounded_sequence(self) -> None:
        plan = self.plan(snapshot())
        self.assertEqual(["SPECIFY", "CLARIFY", "PLAN"], actions(plan))
        self.assertEqual("BUDGET_REACHED", plan["termination"]["expected_reason"])

    def test_recovery_probe_owns_pristine_idle_dispatchability(self) -> None:
        dispatch_allowed = snapshot()
        dispatch_allowed["recovery"].update({"decision": "DISPATCH_ALLOWED", "journal_status": "IDLE"})
        self.assertEqual(["SPECIFY", "CLARIFY", "PLAN"], actions(self.plan(dispatch_allowed)))

        blocked = snapshot()
        blocked["recovery"].update({"decision": "BLOCKED", "journal_status": "IDLE"})
        plan = self.plan(blocked)
        self.assertEqual([], plan["actions"])
        self.assertEqual("BLOCKED", plan["termination"]["expected_reason"])

    def test_reconcile_artifact_is_first_action(self) -> None:
        value = snapshot()
        value["recovery"]["decision"] = "RECONCILE_ARTIFACT"
        self.assertEqual("RECOVER_PENDING_ACTION", actions(self.plan(value))[0])

    def test_wait_recovery_requires_human_without_dispatch(self) -> None:
        value = snapshot()
        value["recovery"]["decision"] = "WAIT_OR_MANUAL_REVIEW"
        plan = self.plan(value)
        self.assertEqual(["WAIT_OR_MANUAL_REVIEW"], actions(plan))
        self.assertEqual("HUMAN_REQUIRED", plan["actions"][0]["classification"])

    def test_blocker_produces_empty_blocked_plan(self) -> None:
        value = snapshot()
        value["state"]["blockers"] = ["blocked"]
        plan = self.plan(value)
        self.assertEqual([], plan["actions"])
        self.assertEqual("BLOCKED", plan["termination"]["expected_reason"])

    def test_protected_scope_and_external_restrictions_require_human(self) -> None:
        for field in ("protected_file_required", "scope_change_required", "external_mutation_requested"):
            with self.subTest(field=field):
                value = snapshot()
                value["restrictions"][field] = True
                plan = self.plan(value)
                self.assertEqual("HUMAN_REQUIRED", plan["actions"][0]["classification"])
                self.assertTrue(plan["actions"][0]["human_approval_required"])
                self.assertEqual(field == "external_mutation_requested", plan["actions"][0]["external_mutation"])

    def test_test_format_analyze_are_auto_safe(self) -> None:
        value = snapshot(stage="IMPLEMENT", status="IN_PROGRESS")
        value["implementation"].update({"completed_slices": ["slice-1"], "next_slice": None, "all_slices_green": True})
        plan = self.plan(value)
        planned = plan["actions"]
        self.assertEqual(["TEST_FOCUSED", "FORMAT_DART_CHANGED_FILES", "ANALYZE"], [entry["action"] for entry in planned[:3]])
        self.assertTrue(all(entry["classification"] == "AUTO_SAFE" for entry in planned[:3]))

    def test_all_green_slices_keep_gates_in_test_then_move_to_review(self) -> None:
        value = snapshot(stage="IMPLEMENT", status="IN_PROGRESS")
        value["implementation"].update({"completed_slices": ["slice-1"], "next_slice": None, "all_slices_green": True})
        planned = self.plan(value)["actions"]
        self.assertEqual(
            ["TEST_FOCUSED", "FORMAT_DART_CHANGED_FILES", "ANALYZE", "REVIEW"],
            [entry["action"] for entry in planned],
        )
        self.assertEqual(
            [("TEST", "TEST"), ("TEST", "TEST"), ("TEST", "REVIEW"), ("REVIEW", "REVIEW")],
            [(entry["stage"], entry["success_transition"]) for entry in planned],
        )

    def test_test_gate_baseline_round_trips_each_pass_prefix_and_ci_policy(self) -> None:
        gate_actions = ["TEST_FOCUSED", "FORMAT_DART_CHANGED_FILES", "ANALYZE"]
        for passed_count in range(4):
            for ci_enabled in (False, True):
                with self.subTest(passed_count=passed_count, ci_enabled=ci_enabled):
                    value = snapshot(stage="TEST", status="IN_PROGRESS")
                    value["gates"].update({
                        gate: "PASS" for gate in ("focused_tests", "format", "analyze")[:passed_count]
                    })
                    value["gates"]["project_ci_enabled"] = ci_enabled
                    plan = self.plan(value)
                    self.assertEqual(value["gates"], plan["gate_baseline"])
                    self.assertEqual(gate_actions[passed_count:] + ["REVIEW"], actions(plan))

    def test_rehashed_test_subset_cannot_bypass_pending_gate_baseline(self) -> None:
        value = snapshot(stage="TEST", status="IN_PROGRESS")
        plan = self.plan(value)
        plan["actions"] = [entry for entry in plan["actions"] if entry["action"] == "ANALYZE"]
        plan["actions"][0].update({"sequence": 1, "id": "action-1"})
        plan["authorization"]["plan_sha256"] = planner.hash_plan(plan)

        self.assertIn("INVALID_PLAN", planner.validate_plan_actions(plan))
        self.assertIn("INVALID_PLAN", planner.validate_plan(plan, value)[0])

    def test_test_prefix_is_valid_only_when_its_next_gate_exhausts_a_budget(self) -> None:
        value = snapshot(stage="TEST", status="IN_PROGRESS")
        value["loop"]["budgets"]["review_cycles"]["max"] = 0

        plan = self.plan(value)

        self.assertEqual(
            ["TEST_FOCUSED", "FORMAT_DART_CHANGED_FILES", "ANALYZE"],
            actions(plan),
        )
        self.assertEqual("BUDGET_REACHED", plan["termination"]["expected_reason"])

    def test_only_fsm_stages_are_valid_for_snapshots_and_plans(self) -> None:
        self.assertEqual(
            ("SPECIFY", "CLARIFY", "PLAN", "TASKS", "IMPLEMENT", "TEST", "REVIEW", "DONE"),
            planner.FSM_STAGES,
        )
        with self.assertRaises(planner.PlannerError):
            planner.create_plan(snapshot(stage="CI"))
        plan = self.plan(snapshot())
        plan["actions"][0]["stage"] = "FORMAT"
        self.assertTrue(planner.validate_plan(plan, snapshot())[0].startswith("INVALID_PLAN"))
        plan = self.plan(snapshot())
        plan["actions"][0]["success_transition"] = "ANALYZE"
        self.assertTrue(planner.validate_plan(plan, snapshot())[0].startswith("INVALID_PLAN"))

    def test_analyze_review_consumes_executor_and_review_budget(self) -> None:
        value = snapshot(stage="TEST", status="IN_PROGRESS")
        value["gates"].update({"focused_tests": "PASS", "format": "PASS", "analyze": "PASS"})
        plan = self.plan(value)
        review = next(entry for entry in plan["actions"] if entry["action"] == "REVIEW")
        self.assertEqual(1, review["budget_cost"]["executor_calls"])
        self.assertEqual(1, review["budget_cost"]["review_cycles"])

    def test_review_approved_ci_disabled_evaluates_done_without_ci(self) -> None:
        value = snapshot(stage="REVIEW", status="APPROVED")
        value["gates"].update({"focused_tests": "PASS", "format": "PASS", "analyze": "PASS", "review": "APPROVED", "ci": "DISABLED_BY_PROJECT_POLICY", "project_ci_enabled": False})
        plan = self.plan(value)
        self.assertEqual(["EVALUATE_DONE_WITH_CI_DISABLED"], actions(plan))
        self.assertEqual(("REVIEW", "DONE"), (plan["actions"][0]["stage"], plan["actions"][0]["success_transition"]))

    def test_review_approved_blocks_completion_when_required_gates_or_ci_policy_conflict(self) -> None:
        cases = (
            ("focused tests failed", {"focused_tests": "FAIL", "project_ci_enabled": True, "ci": "PENDING"}),
            ("format pending", {"format": "PENDING", "project_ci_enabled": True, "ci": "PENDING"}),
            ("analyze timeout", {"analyze": "TIMEOUT", "project_ci_enabled": True, "ci": "PENDING"}),
            ("review pending", {"review": "PENDING", "project_ci_enabled": True, "ci": "PENDING"}),
            ("enabled ci disabled", {"project_ci_enabled": True, "ci": "DISABLED_BY_PROJECT_POLICY"}),
            ("disabled ci pending", {"project_ci_enabled": False, "ci": "PENDING"}),
            ("disabled ci passed", {"project_ci_enabled": False, "ci": "PASS"}),
        )
        for name, override in cases:
            with self.subTest(name=name):
                value = snapshot(stage="REVIEW", status="APPROVED")
                value["gates"].update({
                    "focused_tests": "PASS", "format": "PASS", "analyze": "PASS",
                    "review": "APPROVED", "project_ci_enabled": True, "ci": "PENDING",
                    **override,
                })

                plan = self.plan(value)

                self.assertEqual([], actions(plan))
                self.assertEqual("BLOCKED", plan["termination"]["expected_reason"])
                self.assertEqual("BLOCKED", plan["termination"]["expected_status"])

    def test_review_approved_ci_enabled_runs_ci_only_after_all_prerequisite_gates_pass(self) -> None:
        value = snapshot(stage="REVIEW", status="APPROVED")
        value["gates"].update({
            "focused_tests": "PASS", "format": "PASS", "analyze": "PASS",
            "review": "APPROVED", "project_ci_enabled": True, "ci": "PENDING",
        })

        self.assertEqual(["CI"], actions(self.plan(value)))

    def test_ci_disabled_never_becomes_pass(self) -> None:
        value = snapshot(stage="REVIEW", status="APPROVED")
        value["gates"].update({"focused_tests": "PASS", "format": "PASS", "analyze": "PASS", "review": "APPROVED", "ci": "DISABLED_BY_PROJECT_POLICY", "project_ci_enabled": False})
        plan = self.plan(value)
        self.assertEqual("DISABLED_BY_PROJECT_POLICY", plan["gate_baseline"]["ci"])

    def test_ci_enabled_respects_single_run_budget(self) -> None:
        value = snapshot(stage="REVIEW", status="APPROVED")
        value["gates"].update({"focused_tests": "PASS", "format": "PASS", "analyze": "PASS", "review": "APPROVED", "project_ci_enabled": True})
        plan = self.plan(value)
        self.assertIn("CI", actions(plan))
        ci = next(entry for entry in plan["actions"] if entry["action"] == "CI")
        self.assertEqual(("REVIEW", "DONE"), (ci["stage"], ci["success_transition"]))
        value["loop"]["budgets"]["ci_runs"]["used"] = 1
        self.assertNotIn("CI", actions(self.plan(value)))

    def test_slices_and_transition_and_executor_limits(self) -> None:
        value = snapshot(stage="IMPLEMENT")
        plan = self.plan(value)
        self.assertEqual(["IMPLEMENT_SLICE", "IMPLEMENT_SLICE", "IMPLEMENT_SLICE"], actions(plan))
        value = snapshot()
        value["loop"]["budgets"]["stage_transitions"]["used"] = 2
        self.assertEqual(["SPECIFY"], actions(self.plan(value)))
        value = snapshot()
        value["loop"]["budgets"]["executor_calls"]["used"] = 7
        self.assertEqual(["SPECIFY"], actions(self.plan(value)))

    def test_implementation_baseline_records_initial_slice_cursor_and_is_schema_required(self) -> None:
        value = snapshot(stage="IMPLEMENT")
        value["implementation"].update({
            "completed_slices": ["slice-1"],
            "next_slice": "slice-2",
        })

        plan = self.plan(value)

        self.assertEqual(
            {"planned_slices": ["slice-1", "slice-2", "slice-3", "slice-4"], "completed_slices": ["slice-1"]},
            plan["implementation_baseline"],
        )
        plan.pop("implementation_baseline")
        self.assertTrue(planner.validate_plan(plan, value)[0].startswith("INVALID_PLAN"))

    def test_hash_is_deterministic_and_staleness_detects_state_identity_recovery(self) -> None:
        first = self.plan(snapshot())
        second = self.plan(snapshot())
        self.assertEqual(first["authorization"]["plan_sha256"], second["authorization"]["plan_sha256"])
        for mutate in (
            lambda value: value["state"].update({"sha256": "c" * 64}),
            lambda value: value["workspace"].update({"branch": "other"}),
            lambda value: value["workspace"].update({"head": "d" * 40}),
            lambda value: value["loop"]["budgets"]["executor_calls"].update({"used": 1}),
            lambda value: value["recovery"].update({"decision": "RELEASED"}),
        ):
            with self.subTest(mutate=mutate):
                value = snapshot()
                mutate(value)
                self.assertIn("PLAN_STALE", planner.validate_plan(first, value))

    def test_human_actions_are_never_auto_executable(self) -> None:
        for action in ("COMMIT", "PUSH", "LINEAR_UPDATE", "OBSIDIAN_WRITE", "DEV_E2E", "BACKEND_MUTATION"):
            with self.subTest(action=action):
                result = planner.classify_action(action)
                self.assertEqual("HUMAN_REQUIRED", result["classification"])
                self.assertTrue(result["human_approval_required"])

    def test_rehashed_action_tampering_is_rejected_by_semantic_validation(self) -> None:
        mutations = (
            ("invalid canonical transition", lambda entry: entry.update({"success_transition": "IMPLEMENT"})),
            ("duplicate sequence", lambda entry: entry.update({"sequence": 2, "id": "action-2"})),
            ("wrong classification", lambda entry: entry.update({"classification": "AUTO_SAFE", "executor": "HOST", "human_approval_required": False})),
            ("wrong budget cost", lambda entry: entry["budget_cost"].update({"executor_calls": 0})),
            ("wrong external mutation", lambda entry: entry.update({"external_mutation": True})),
        )
        for name, mutate in mutations:
            with self.subTest(name=name):
                value = snapshot()
                plan = self.plan(value)
                mutate(plan["actions"][0])
                plan["authorization"]["plan_sha256"] = planner.hash_plan(plan)
                self.assertTrue(planner.validate_plan(plan, value)[0].startswith("INVALID_PLAN"))

    def test_should_reject_a_canonical_state_transaction_update_injected_after_specify(self) -> None:
        value = snapshot()
        plan = self.plan(value)

        inject_canonical_state_transaction_update(plan)
        plan["authorization"]["plan_sha256"] = planner.hash_plan(plan)

        self.assertTrue(planner.validate_plan(plan, value)[0].startswith("INVALID_PLAN"))

    def test_should_round_trip_validate_representative_planner_grammars(self) -> None:
        clarify = snapshot(stage="CLARIFY")
        implementation = snapshot(stage="IMPLEMENT")
        test_with_passed_gates = snapshot(stage="TEST")
        test_with_passed_gates["gates"].update({
            "focused_tests": "PASS", "format": "PASS", "analyze": "PASS",
        })
        recovery = snapshot()
        recovery["recovery"].update({
            "decision": "RECONCILE_ARTIFACT", "journal_status": "ARTIFACT_READY",
            "current_action_id": "APP-999-parent", "artifact_present": True,
        })
        restricted = snapshot()
        restricted["restrictions"]["protected_file_required"] = True
        review_ci_disabled = snapshot(stage="REVIEW", status="APPROVED")
        review_ci_disabled["gates"].update({
            "focused_tests": "PASS", "format": "PASS", "analyze": "PASS",
            "review": "APPROVED", "ci": "DISABLED_BY_PROJECT_POLICY",
        })
        review_ci_enabled = copy.deepcopy(review_ci_disabled)
        review_ci_enabled["gates"].update({"project_ci_enabled": True, "ci": "PENDING"})

        for name, value in (
            ("specify", snapshot()),
            ("clarify", clarify),
            ("implementation slices", implementation),
            ("test gates omitted", test_with_passed_gates),
            ("recovery", recovery),
            ("restriction", restricted),
            ("ci disabled", review_ci_disabled),
            ("ci enabled", review_ci_enabled),
        ):
            with self.subTest(name=name):
                plan = planner.create_plan(value)
                self.assertEqual([], planner.validate_plan(plan, value))

    def test_snapshot_schema_accepts_corrective_retry_available(self) -> None:
        value = snapshot()
        value["recovery"].update({
            "decision": "CORRECTIVE_RETRY_AVAILABLE", "journal_status": "ARTIFACT_READY",
            "current_action_id": "APP-999-parent", "artifact_present": True,
        })

        self.assertIsNone(planner.validate_snapshot(value))

    def test_corrective_retry_available_requires_human_recovery_without_normal_dispatch(self) -> None:
        value = snapshot()
        value["recovery"].update({
            "decision": "CORRECTIVE_RETRY_AVAILABLE", "journal_status": "ARTIFACT_READY",
            "current_action_id": "APP-999-parent", "artifact_present": True,
        })

        plan = self.plan(value)

        self.assertEqual(["RESOLVE_RECOVERY"], actions(plan))
        self.assertEqual("HUMAN_REQUIRED", plan["actions"][0]["classification"])
        self.assertTrue(plan["actions"][0]["human_approval_required"])
        self.assertEqual("HUMAN_REQUIRED", plan["termination"]["expected_reason"])

    def test_cli_has_no_execution_flags_and_rejects_unknown_properties(self) -> None:
        help_result = subprocess.run([sys.executable, str(SCRIPT), "--help"], text=True, capture_output=True, check=False, timeout=20)
        self.assertEqual(0, help_result.returncode)
        for prohibited in ("--execute", "--apply", "--force", "--ignore-budget", "--ignore-recovery", "--auto-approve"):
            self.assertNotIn(prohibited, help_result.stdout)
        value = snapshot()
        value["unknown"] = True
        with self.assertRaises(planner.PlannerError):
            planner.create_plan(value, "NEXT_HUMAN_CHECKPOINT")
        plan = self.plan(snapshot())
        plan["unknown"] = True
        self.assertTrue(planner.validate_plan(plan, snapshot())[0].startswith("INVALID_PLAN"))

    def test_paths_with_spaces_and_cli_json(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bounded planner spaces ") as directory:
            base = Path(directory)
            source = base / "snapshot with spaces.json"
            output = base / "plan with spaces.json"
            source.write_text(json.dumps(snapshot()), encoding="utf-8")
            result = subprocess.run([sys.executable, str(SCRIPT), "plan", "--snapshot", str(source), "--target", "NEXT_HUMAN_CHECKPOINT", "--output", str(output), "--json"], text=True, capture_output=True, check=False, timeout=20)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue(output.is_file())
            self.assertEqual(json.loads(result.stdout)["authorization"]["plan_sha256"], json.loads(output.read_text(encoding="utf-8"))["authorization"]["plan_sha256"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
