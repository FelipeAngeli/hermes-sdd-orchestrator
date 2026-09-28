"""TDD coverage for the bounded runtime continuation driver."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))
import action_journal as journal  # noqa: E402
import bounded_run_driver as driver  # noqa: E402
import bounded_run_planner as planner  # noqa: E402


ROOT = RUNTIME


def snapshot(*, stage: str = "SPECIFY") -> dict:
    return {
        "schema_version": 1,
        "workspace": {
            "path": "/tmp/bounded-driver-worktree",
            "branch": "dev",
            "head": "a" * 40,
            "git_common_dir": "/tmp/bounded-driver-repository/.git",
        },
        "state": {
            "sha256": "b" * 64,
            "ticket": "APP-999",
            "stage": stage,
            "status": "WAITING",
            "completed": [],
            "skipped": [],
            "next_action": stage,
            "blockers": [],
            "protected_preexisting": [],
            "agent_owned": [],
        },
        "loop": {
            "mode": "BOUNDED_AUTO",
            "loop_active": True,
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
        "gates": {"focused_tests": "PENDING", "format": "PENDING", "analyze": "PENDING", "review": "PENDING", "ci": "PENDING", "project_ci_enabled": False},
        "implementation": {"planned_slices": [], "completed_slices": [], "next_slice": None, "all_slices_green": False, "canonical_focused_test_command_available": True, "changed_files_available": True},
        "recovery": {"decision": "DISPATCH_ALLOWED", "journal_status": "IDLE", "current_action_id": None, "artifact_present": False},
        "restrictions": {"external_mutation_requested": False, "protected_file_required": False, "scope_change_required": False, "architecture_decision_required": False},
    }


def local_delivery_snapshot(*, stage: str = "IMPLEMENT") -> dict:
    value = snapshot(stage=stage)
    limits, _ = planner.budget_values(value)
    value["schema_version"] = 2
    authorization = {
        "id": "local-delivery-APP-999",
        "request_evidence": "T2B local driver authorization",
        "ticket": value["state"]["ticket"],
        "canonical_scope": "T2B bounded local delivery",
        "scope_sha256": planner.sha256_json("T2B bounded local delivery"),
        "workspace": copy.deepcopy(value["workspace"]),
        "total_limits": limits,
    }
    value["local_delivery"] = {"authorization": authorization, "usage_ledger": []}
    return value


def runtime(value: dict, plan: dict, *, sequence: int = 0) -> dict:
    return {
        "current_plan_id": plan["plan_id"],
        "approved_plan_sha256": plan["authorization"]["plan_sha256"],
        "planned_action_count": len(plan["actions"]),
        "current_sequence": sequence,
        "actions_executed": sequence,
        "started_at": "2026-09-16T00:00:00Z",
        "last_action_id": None,
        "expected_predecessor_state_sha256": value["state"]["sha256"],
    }


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


class BoundedRunDriverTests(unittest.TestCase):
    def test_bind_rejects_invalid_inputs_and_never_activates_manual_snapshot(self) -> None:
        legacy = snapshot()
        legacy["loop"].update({"mode": "MANUAL", "loop_active": False})
        legacy_plan = planner.create_plan(copy.deepcopy(legacy))
        with self.assertRaisesRegex(driver.DriverError, "APPROVAL_REQUIRED"):
            driver.bind(legacy, legacy_plan, started_at="2026-09-17T00:00:00Z")
        bound = driver.bind(
            legacy,
            legacy_plan,
            started_at="2026-09-17T00:00:00Z",
            approved_plan_sha256=legacy_plan["authorization"]["plan_sha256"],
        )
        self.assertEqual("MANUAL", bound["loop"]["mode"])
        self.assertFalse(bound["loop"]["loop_active"])

        invalid_plan = copy.deepcopy(legacy_plan)
        invalid_plan["authorization"]["plan_sha256"] = "0" * 64
        with self.assertRaisesRegex(driver.DriverError, "plan_sha256"):
            driver.bind(
                legacy,
                invalid_plan,
                started_at="2026-09-17T00:00:00Z",
                approved_plan_sha256="0" * 64,
            )
        invalid_snapshot = copy.deepcopy(legacy)
        invalid_snapshot["state"]["sha256"] = "not-a-hash"
        with self.assertRaisesRegex(driver.DriverError, "INVALID_SNAPSHOT"):
            driver.bind(
                invalid_snapshot,
                legacy_plan,
                started_at="2026-09-17T00:00:00Z",
                approved_plan_sha256=legacy_plan["authorization"]["plan_sha256"],
            )

    def test_runtime_sequence_type_is_rejected_before_plan_slicing(self) -> None:
        for invalid in ("0", True):
            with self.subTest(invalid=invalid):
                value, plan = self.approved()
                value["runtime"]["current_sequence"] = invalid
                value["runtime"]["actions_executed"] = invalid

                with self.assertRaisesRegex(driver.DriverError, "sequence counters must be integers"):
                    driver.evaluate_next(value, plan)

    def test_cli_bind_creates_a_complete_runtime_for_a_valid_persisted_plan(self) -> None:
        """Protocol simulation, not product E2E or an actual Codex execution."""
        value = local_delivery_snapshot()
        value["implementation"].update({
            "planned_slices": ["one", "two", "three", "four"],
            "completed_slices": [],
            "next_slice": "one",
        })
        value["loop"]["budgets"]["tdd_slices"]["max"] = 12
        value["local_delivery"]["authorization"]["total_limits"]["tdd_slices"] = 12

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot_path = root / "snapshot.json"
            plan_path = root / "plan.json"
            snapshot_path.write_text(json.dumps(value), encoding="utf-8")
            planned = subprocess.run(
                [sys.executable, str(ROOT / "bounded_run_planner.py"), "plan", "--snapshot", str(snapshot_path), "--target", "NEXT_HUMAN_CHECKPOINT", "--output", str(plan_path), "--json"],
                text=True, capture_output=True, check=False, timeout=20,
            )
            self.assertEqual(0, planned.returncode, planned.stderr)
            validated = subprocess.run(
                [sys.executable, str(ROOT / "bounded_run_planner.py"), "validate", "--snapshot", str(snapshot_path), "--plan", str(plan_path), "--json"],
                text=True, capture_output=True, check=False, timeout=20,
            )
            self.assertEqual(0, validated.returncode, validated.stderr)

            bound = subprocess.run(
                [sys.executable, str(ROOT / "bounded_run_driver.py"), "bind", "--snapshot", str(snapshot_path), "--plan", str(plan_path), "--started-at", "2026-09-17T00:00:00Z", "--json"],
                text=True, capture_output=True, check=False, timeout=20,
            )

            self.assertEqual(0, bound.returncode, bound.stderr)
            runtime_snapshot = json.loads(bound.stdout)
            self.assertEqual(set(driver.RUNTIME_KEYS), set(runtime_snapshot["runtime"]))
            self.assertEqual(0, runtime_snapshot["runtime"]["current_sequence"])
            self.assertEqual(value["state"]["sha256"], runtime_snapshot["runtime"]["expected_predecessor_state_sha256"])
            bound_path = root / "bound.json"
            bound_path.write_text(bound.stdout, encoding="utf-8")
            for command in ("next", "inspect"):
                result = subprocess.run(
                    [sys.executable, str(ROOT / "bounded_run_driver.py"), command, "--snapshot", str(bound_path), "--plan", str(plan_path), "--json"],
                    text=True, capture_output=True, check=False, timeout=20,
                )
                self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("EXECUTE_NEXT", json.loads(result.stdout)["next"])

    def approved(self, value: dict | None = None) -> tuple[dict, dict]:
        value = value or snapshot()
        plan = planner.create_plan(copy.deepcopy(value))
        value["runtime"] = runtime(value, plan)
        return value, plan

    def succeed(self, value: dict, plan: dict) -> dict:
        entry = plan["actions"][value["runtime"]["current_sequence"]]
        result = copy.deepcopy(value)
        index = result["runtime"]["current_sequence"] + 1
        state_hash = hashlib.sha256(f"state-{index}".encode()).hexdigest()
        result["state"].update({
            "sha256": state_hash,
            "stage": entry["success_transition"],
            "next_action": entry["success_transition"],
            "completed": result["state"]["completed"] + [entry["action"]],
        })
        for key, cost in entry["budget_cost"].items():
            source, used_key, _ = planner.BUDGET_SOURCES[key]
            result["loop"]["budgets"][source][used_key] += cost
        result["runtime"].update({
            "current_sequence": index,
            "actions_executed": index,
            "last_action_id": entry["id"],
            "expected_predecessor_state_sha256": state_hash,
        })
        result["recovery"].update({"decision": "RELEASED", "journal_status": "RELEASED", "current_action_id": entry["id"]})
        return result

    def rollover(self, value: dict) -> dict:
        result = copy.deepcopy(value)
        result["recovery"].update({"decision": "DISPATCH_ALLOWED", "journal_status": "IDLE", "current_action_id": None})
        return result

    def local_approved(self, value: dict | None = None) -> tuple[dict, dict]:
        return self.approved(value or local_delivery_snapshot())

    def local_succeed(self, value: dict, plan: dict, *, slices_green: bool = False) -> dict:
        entry = plan["actions"][value["runtime"]["current_sequence"]]
        result = self.succeed(value, plan)
        result["local_delivery"]["usage_ledger"].append({
            "action_id": f"{plan['plan_id']}:{entry['id']}",
            "cost": copy.deepcopy(entry["budget_cost"]),
        })
        if entry["action"] == "IMPLEMENT_SLICE":
            implementation = result["implementation"]
            completed = [*implementation["completed_slices"], implementation["next_slice"]]
            is_complete = slices_green or completed == implementation["planned_slices"]
            implementation.update({
                "completed_slices": completed,
                "next_slice": None if is_complete else implementation["planned_slices"][len(completed)],
                "all_slices_green": is_complete,
            })
            if is_complete:
                result["state"].update({"stage": "TEST", "next_action": "TEST"})
        return result

    def test_cli_protocol_simulation_replans_to_done_without_renewing_global_budget(self) -> None:
        """Protocol simulation only: fixtures model accepted actions; no worker runs."""
        value = local_delivery_snapshot()
        value["implementation"].update({
            "planned_slices": ["one", "two", "three", "four"],
            "completed_slices": [], "next_slice": "one",
        })
        value["loop"]["budgets"]["tdd_slices"]["max"] = 12
        value["local_delivery"]["authorization"]["total_limits"]["tdd_slices"] = 12
        limits = copy.deepcopy(value["local_delivery"]["authorization"]["total_limits"])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def bind_plan(current: dict) -> tuple[dict, dict]:
                raw_snapshot, plan_path = root / "snapshot.json", root / "plan.json"
                raw_snapshot.write_text(json.dumps(driver._without_runtime(current)), encoding="utf-8")
                planned = subprocess.run(
                    [sys.executable, str(ROOT / "bounded_run_planner.py"), "plan", "--snapshot", str(raw_snapshot), "--target", "NEXT_HUMAN_CHECKPOINT", "--output", str(plan_path), "--json"],
                    text=True, capture_output=True, check=False, timeout=20,
                )
                self.assertEqual(0, planned.returncode, planned.stderr)
                validated = subprocess.run(
                    [sys.executable, str(ROOT / "bounded_run_planner.py"), "validate", "--snapshot", str(raw_snapshot), "--plan", str(plan_path), "--json"],
                    text=True, capture_output=True, check=False, timeout=20,
                )
                self.assertEqual(0, validated.returncode, validated.stderr)
                bound = subprocess.run(
                    [sys.executable, str(ROOT / "bounded_run_driver.py"), "bind", "--snapshot", str(raw_snapshot), "--plan", str(plan_path), "--started-at", "2026-09-17T00:00:00Z", "--json"],
                    text=True, capture_output=True, check=False, timeout=20,
                )
                self.assertEqual(0, bound.returncode, bound.stderr)
                return json.loads(bound.stdout), json.loads(plan_path.read_text(encoding="utf-8"))

            value, plan = bind_plan(value)
            for index in range(4):
                decision = driver.evaluate_next(value, plan)
                self.assertEqual("EXECUTE_NEXT", decision["decision"])
                self.assertFalse(decision["end_turn"])
                value = self.rollover(self.local_succeed(value, plan, slices_green=index == 3))
            self.assertEqual("REPLAN_REQUIRED", driver.evaluate_next(value, plan)["decision"])
            self.assertFalse(driver.evaluate_next(value, plan)["end_turn"])

            value, plan = bind_plan(value)
            for action, gate in (("TEST_FOCUSED", "focused_tests"), ("FORMAT_CHANGED_FILES", "format"), ("ANALYZE", "analyze")):
                self.assertEqual(action, driver.evaluate_next(value, plan)["action"])
                value = self.rollover(self.local_succeed(value, plan))
                value["gates"][gate] = "PASS"
            self.assertEqual("REVIEW", driver.evaluate_next(value, plan)["action"])
            value = self.rollover(self.local_succeed(value, plan))
            value["gates"]["review"] = "APPROVED"
            value["gates"]["ci"] = "DISABLED_BY_PROJECT_POLICY"
            value["state"]["status"] = "APPROVED"

            value, plan = bind_plan(value)
            next_action = driver.evaluate_next(value, plan)
            self.assertEqual("EVALUATE_DONE_WITH_CI_DISABLED", next_action["action"], next_action)
            value = self.rollover(self.local_succeed(value, plan))
            value["state"].update({"stage": "DONE", "status": "DONE", "next_action": "DONE"})
            done = driver.evaluate_next(value, plan)
            self.assertEqual("COMPLETE", done["decision"])
            self.assertTrue(done["end_turn"])
            self.assertEqual(limits, value["local_delivery"]["authorization"]["total_limits"])
            ledger_ids = [entry["action_id"] for entry in value["local_delivery"]["usage_ledger"]]
            self.assertEqual(len(ledger_ids), len(set(ledger_ids)))

    def test_local_delivery_continues_four_slices_then_requires_new_projection_without_renewing_totals(self) -> None:
        value = local_delivery_snapshot()
        value["implementation"].update({
            "planned_slices": ["one", "two", "three", "four"],
            "completed_slices": [], "next_slice": "one",
        })
        value["loop"]["budgets"]["tdd_slices"]["max"] = 8
        value["local_delivery"]["authorization"]["total_limits"]["tdd_slices"] = 8
        value, plan = self.local_approved(value)
        original_limits = copy.deepcopy(value["local_delivery"]["authorization"]["total_limits"])

        for index in range(4):
            self.assertEqual("IMPLEMENT_SLICE", driver.evaluate_next(value, plan)["action"])
            value = self.rollover(self.local_succeed(value, plan, slices_green=index == 3))

        replan = driver.evaluate_next(value, plan)
        self.assertEqual("REPLAN_REQUIRED", replan["decision"])
        self.assertFalse(replan["end_turn"])
        self.assertEqual(original_limits, value["local_delivery"]["authorization"]["total_limits"])
        self.assertEqual(4, planner.local_delivery_usage(value)["tdd_slices"])

        next_plan = planner.create_plan(driver._without_runtime(value))
        value["runtime"] = runtime(value, next_plan)
        self.assertEqual("TEST_FOCUSED", driver.evaluate_next(value, next_plan)["action"])

    def test_local_delivery_rejects_usage_regression_authorization_drift_and_hash_drift(self) -> None:
        value = local_delivery_snapshot()
        value["implementation"].update({"planned_slices": ["one"], "next_slice": "one"})
        value, plan = self.local_approved(value)
        progressed = copy.deepcopy(value)
        progressed["runtime"].update({"current_sequence": 1, "actions_executed": 1})
        with self.assertRaisesRegex(driver.DriverError, "ledger length does not match accepted actions"):
            driver.evaluate_next(progressed, plan)

        for field, replacement in (("id", "other"), ("scope_sha256", "0" * 64)):
            with self.subTest(field=field):
                invalid = copy.deepcopy(progressed)
                invalid["local_delivery"]["authorization"][field] = replacement
                with self.assertRaisesRegex(driver.DriverError, "LOCAL_DELIVERY|scope_sha256"):
                    driver.evaluate_next(invalid, plan)

    def test_local_delivery_blocks_implementation_cursor_contradictions_before_dispatch(self) -> None:
        value = local_delivery_snapshot()
        value["implementation"].update({
            "planned_slices": ["one", "two"], "completed_slices": [], "next_slice": "one",
        })
        value, plan = self.local_approved(value)
        progressed = self.rollover(self.local_succeed(value, plan))
        cases = (
            ("wrong next slice", value, lambda current: current["implementation"].update({"next_slice": "two"})),
            ("regressed completed slices", progressed, lambda current: current["implementation"].update({"completed_slices": []})),
            ("skipped completed slice", progressed, lambda current: current["implementation"].update({"completed_slices": ["two"]})),
            ("false all green", value, lambda current: current["implementation"].update({"all_slices_green": True, "next_slice": None})),
            ("green before all planned slices", progressed, lambda current: current["implementation"].update({"all_slices_green": True, "next_slice": None})),
        )
        for name, source, mutate in cases:
            with self.subTest(name=name):
                invalid = copy.deepcopy(source)
                mutate(invalid)

                result = driver.evaluate_next(invalid, plan)

                self.assertEqual("STOP_BLOCKED", result["decision"])
                self.assertEqual("IMPLEMENTATION_CURSOR_INVALID", result["stop_reason"])
                self.assertNotEqual("EXECUTE_NEXT", result["decision"])

    def test_local_delivery_retry_exhaustion_requires_explicit_controller_clearance(self) -> None:
        value = local_delivery_snapshot()
        value["implementation"].update({
            "planned_slices": ["one", "two"], "completed_slices": [], "next_slice": "one",
        })
        value, plan = self.local_approved(value)
        value["loop"].update({
            "stop_reason": "CORRECTIVE_RETRY_EXHAUSTED",
            "last_run": {"stop_reason": "CORRECTIVE_RETRY_EXHAUSTED", "actions_executed": 0},
        })

        same_action = driver.evaluate_next(value, plan)

        self.assertNotEqual("EXECUTE_NEXT", same_action["decision"])
        self.assertEqual("CORRECTIVE_RETRY_EXHAUSTED", same_action["stop_reason"])

        next_action = self.rollover(self.local_succeed(value, plan))
        next_action["loop"]["stop_reason"] = "CORRECTIVE_RETRY_EXHAUSTED"
        blocked_next_action = driver.evaluate_next(next_action, plan)
        self.assertEqual("COMPLETE", blocked_next_action["decision"])
        self.assertEqual("CORRECTIVE_RETRY_EXHAUSTED", blocked_next_action["stop_reason"])

        # The authorized controller, not a cursor comparison, clears this stop.
        next_action["loop"]["stop_reason"] = "NONE"
        self.assertEqual("EXECUTE_NEXT", driver.evaluate_next(next_action, plan)["decision"])

    def test_local_delivery_blocks_unreconciled_retry_gate_failure_and_fraudulent_done(self) -> None:
        cases = (
            ("unknown artifact", lambda value: value["recovery"].update({"decision": "RECONCILE_ARTIFACT", "artifact_present": True}), "STOP_RECOVERY"),
            ("gate failed", lambda value: value["gates"].update({"focused_tests": "FAIL"}), "STOP_BLOCKED"),
            ("fraudulent done", lambda value: value["state"].update({"stage": "DONE", "status": "DONE"}), "STOP_BLOCKED"),
        )
        for name, mutate, expected in cases:
            with self.subTest(name=name):
                value, plan = self.local_approved()
                mutate(value)
                self.assertEqual(expected, driver.evaluate_next(value, plan)["decision"])

        exhausted = local_delivery_snapshot()
        exhausted["loop"]["budgets"]["corrective_retries"]["max_per_action"] = 0
        exhausted["local_delivery"]["authorization"]["total_limits"]["corrective_retries"] = 0
        exhausted, plan = self.local_approved(exhausted)
        exhausted["recovery"].update({"decision": "CORRECTIVE_RETRY_AVAILABLE", "current_action_id": "action-1"})
        self.assertEqual("STOP_HUMAN_REQUIRED", driver.evaluate_next(exhausted, plan)["decision"])

    def test_local_delivery_stops_for_external_protected_human_and_legacy_remains_complete(self) -> None:
        for restriction in ("external_mutation_requested", "protected_file_required"):
            with self.subTest(restriction=restriction):
                value, plan = self.local_approved()
                value["restrictions"][restriction] = True
                self.assertEqual("STOP_HUMAN_REQUIRED", driver.evaluate_next(value, plan)["decision"])
        value, plan = self.local_approved()
        value["loop"]["human_approval_required"] = True
        self.assertEqual("STOP_HUMAN_REQUIRED", driver.evaluate_next(value, plan)["decision"])

        legacy = snapshot()
        legacy["loop"]["budgets"]["external_mutations"]["max"] = 1
        legacy, legacy_plan = self.approved(legacy)
        legacy["runtime"].update({"current_sequence": len(legacy_plan["actions"]), "actions_executed": len(legacy_plan["actions"])})
        self.assertEqual("PLAN_COMPLETE", driver.evaluate_next(legacy, legacy_plan)["terminal_reason"])

    def test_bounded_auto_continues_to_clarify_after_healthy_specify_rollover(self) -> None:
        value = snapshot()
        plan = planner.create_plan(copy.deepcopy(value))
        value["runtime"] = runtime(value, plan, sequence=1)
        value["state"].update({"stage": "CLARIFY", "next_action": "CLARIFY", "completed": ["SPECIFY"]})

        result = driver.evaluate_next(value, plan)

        self.assertEqual("EXECUTE_NEXT", result["decision"])
        self.assertEqual(2, result["sequence"])
        self.assertEqual("CLARIFY", result["action"])

    def test_rehashed_test_subset_cannot_bypass_pending_gate_baseline(self) -> None:
        value = snapshot(stage="TEST")
        plan = planner.create_plan(copy.deepcopy(value))
        plan["actions"] = [entry for entry in plan["actions"] if entry["action"] == "ANALYZE"]
        plan["actions"][0].update({"sequence": 1, "id": "action-1"})
        plan["authorization"]["plan_sha256"] = planner.hash_plan(plan)
        value["runtime"] = runtime(value, plan)

        with self.assertRaisesRegex(driver.DriverError, "INVALID_PLAN"):
            driver.evaluate_next(value, plan)

    def test_local_delivery_rejects_gate_baseline_substitution_and_live_regression(self) -> None:
        pending = local_delivery_snapshot(stage="TEST")
        pending["state"]["next_action"] = "TEST"
        substituted = planner.create_plan(copy.deepcopy(pending))
        substituted["gate_baseline"].update({"focused_tests": "PASS", "format": "PASS"})
        substituted["actions"] = [
            entry for entry in substituted["actions"]
            if entry["action"] not in {"TEST_FOCUSED", "FORMAT_CHANGED_FILES"}
        ]
        for sequence, entry in enumerate(substituted["actions"], start=1):
            entry.update({"sequence": sequence, "id": f"action-{sequence}"})
        substituted["authorization"]["plan_sha256"] = planner.hash_plan(substituted)
        pending["runtime"] = runtime(pending, substituted)

        with self.assertRaisesRegex(driver.DriverError, "gate baseline"):
            driver.evaluate_next(pending, substituted)

        approved = local_delivery_snapshot(stage="REVIEW")
        approved["state"].update({"status": "APPROVED", "next_action": "REVIEW"})
        approved["gates"].update({
            "focused_tests": "PASS",
            "format": "PASS",
            "analyze": "PASS",
            "review": "APPROVED",
            "ci": "DISABLED_BY_PROJECT_POLICY",
        })
        plan = planner.create_plan(copy.deepcopy(approved))
        approved["runtime"] = runtime(approved, plan)
        approved["gates"]["focused_tests"] = "PENDING"

        with self.assertRaisesRegex(driver.DriverError, "gate progress"):
            driver.evaluate_next(approved, plan)

    def test_test_gate_progress_does_not_stale_the_immutable_plan_baseline(self) -> None:
        value = snapshot(stage="TEST")
        plan = planner.create_plan(copy.deepcopy(value))
        value["gates"]["focused_tests"] = "PASS"
        value["state"].update({
            "completed": ["TEST_FOCUSED"],
            "next_action": "TEST",
        })
        value["runtime"] = runtime(value, plan, sequence=1)

        result = driver.evaluate_next(value, plan)

        self.assertEqual("FORMAT_CHANGED_FILES", result["action"])

    def test_manual_pauses_after_an_action(self) -> None:
        value = snapshot()
        value["loop"].update({"mode": "MANUAL", "loop_active": False})
        value, plan = self.approved(value)
        result = driver.evaluate_next(value, plan)
        self.assertEqual("COMPLETE", result["decision"])
        self.assertEqual("MANUAL_ACTION_COMPLETE", result["stop_reason"])
        self.assertEqual("MANUAL_ACTION_COMPLETE", result["terminal_reason"])
        self.assertFalse(result["state_updates"]["loop_active"])

    def test_contradictory_blocked_signals_stop_before_manual_done_or_advance(self) -> None:
        cases = (
            ("blocker", lambda value: value["state"].update({"blockers": ["contract invalid"]})),
            ("blocked status", lambda value: value["state"].update({"status": "BLOCKED"})),
            ("blocked recovery", lambda value: value["recovery"].update({"decision": "BLOCKED"})),
        )
        for name, contradict in cases:
            with self.subTest(name=name):
                value = snapshot()
                if name == "blocker":
                    value["loop"].update({"mode": "MANUAL", "loop_active": False})
                elif name == "blocked status":
                    value["state"].update({"stage": "DONE"})
                contradict(value)
                value, plan = self.approved(value)

                result = driver.evaluate_next(value, plan)

                self.assertEqual("STOP_BLOCKED", result["decision"])
                self.assertEqual("BLOCKED", result["stop_reason"])
                self.assertEqual("BLOCKED", result["state_updates"]["stage_status"])

    def test_released_requires_rollover_before_next_action(self) -> None:
        value, plan = self.approved()
        released = self.succeed(value, plan)
        result = driver.evaluate_next(released, plan)
        self.assertEqual("ROLLOVER_REQUIRED", result["decision"])
        self.assertTrue(result["requires_rollover"])
        self.assertEqual("CLARIFY", result["action"])

    def test_rollover_with_dispatch_allowed_continues(self) -> None:
        value, plan = self.approved()
        rolled_over = self.rollover(self.succeed(value, plan))
        result = driver.evaluate_next(rolled_over, plan)
        self.assertEqual("EXECUTE_NEXT", result["decision"])
        self.assertEqual("CLARIFY", result["action"])

    def test_corrective_retry_available_stops_for_recovery_without_dispatch(self) -> None:
        value = snapshot()
        value["recovery"].update({
            "decision": "CORRECTIVE_RETRY_AVAILABLE", "journal_status": "ARTIFACT_READY",
            "current_action_id": "APP-999-parent", "artifact_present": True,
        })
        value, plan = self.approved(value)

        result = driver.evaluate_next(value, plan)

        self.assertEqual(["RESOLVE_RECOVERY"], [entry["action"] for entry in plan["actions"]])
        self.assertEqual("STOP_HUMAN_REQUIRED", result["decision"])
        self.assertTrue(result["end_turn"])
        self.assertEqual("HUMAN_REQUIRED", result["stop_reason"])
        self.assertNotEqual("EXECUTE_NEXT", result["decision"])

    def test_plan_stale_stops_on_workspace_drift(self) -> None:
        value, plan = self.approved()
        value["workspace"]["head"] = "f" * 40
        result = driver.evaluate_next(value, plan)
        self.assertEqual("STOP_PLAN_STALE", result["decision"])
        self.assertFalse(result["state_updates"]["loop_active"])

    def test_stage_transition_budget_stops_before_unplanned_tasks(self) -> None:
        value, plan = self.approved()
        for expected in ("SPECIFY", "CLARIFY", "PLAN"):
            self.assertEqual(expected, driver.evaluate_next(value, plan)["action"])
            value = self.rollover(self.succeed(value, plan))
        result = driver.evaluate_next(value, plan)
        self.assertEqual("STOP_BUDGET", result["decision"])
        self.assertEqual("STAGE_TRANSITION_BUDGET_REACHED", result["stop_reason"])
        self.assertEqual("STAGE_TRANSITION_BUDGET_REACHED", result["state_updates"]["last_run"]["stop_reason"])
        self.assertEqual("TASKS", value["state"]["stage"])
        self.assertEqual(3, value["runtime"]["actions_executed"])
        self.assertEqual(3, value["loop"]["budgets"]["stage_transitions"]["used"])

    def test_last_reading_after_budget_stop_preserves_stage_transition_reason(self) -> None:
        value, plan = self.approved()
        for expected in ("SPECIFY", "CLARIFY", "PLAN"):
            self.assertEqual(expected, driver.evaluate_next(value, plan)["action"])
            value = self.rollover(self.succeed(value, plan))
        first = driver.evaluate_next(value, plan)
        paused = copy.deepcopy(value)
        paused["loop"]["mode"] = first["state_updates"]["mode"]
        paused["loop"]["loop_active"] = first["state_updates"]["loop_active"]
        paused["loop"]["stop_reason"] = first["state_updates"]["stop_reason"]
        paused["loop"]["last_run"] = first["state_updates"]["last_run"]

        last = driver.evaluate_next(paused, plan)

        self.assertEqual("STOP_BUDGET", first["decision"])
        self.assertEqual("STAGE_TRANSITION_BUDGET_REACHED", first["stop_reason"])
        self.assertEqual("COMPLETE", last["decision"])
        self.assertEqual("STAGE_TRANSITION_BUDGET_REACHED", last["terminal_reason"])
        self.assertEqual("STAGE_TRANSITION_BUDGET_REACHED", last["stop_reason"])
        self.assertEqual("STAGE_TRANSITION_BUDGET_REACHED", last["state_updates"]["last_run"]["stop_reason"])
        self.assertEqual("STAGE_TRANSITION_BUDGET_REACHED", first["state_updates"]["stop_reason"])
        self.assertEqual("STAGE_TRANSITION_BUDGET_REACHED", last["state_updates"]["stop_reason"])
        observed = driver.inspect(paused, plan)
        self.assertEqual("COMPLETE", observed["next"])
        self.assertEqual("STAGE_TRANSITION_BUDGET_REACHED", observed["terminal_reason"])

    def test_closed_bounded_run_preserves_last_run_stage_transition_budget_reason(self) -> None:
        value, plan = self.approved()
        value["loop"].update({
            "mode": "PAUSED",
            "loop_active": False,
            "stop_reason": "NONE",
            "last_run": {"stop_reason": "STAGE_TRANSITION_BUDGET_REACHED"},
        })

        result = driver.evaluate_next(value, plan)

        self.assertEqual("COMPLETE", result["decision"])
        self.assertEqual("STAGE_TRANSITION_BUDGET_REACHED", result["terminal_reason"])
        self.assertNotEqual("EXECUTE_NEXT", result["decision"])

    def test_closed_bounded_done_run_preserves_done_reason(self) -> None:
        value, plan = self.approved()
        value["state"].update({"stage": "DONE", "status": "DONE"})
        value["gates"].update({"focused_tests": "PASS", "format": "PASS", "analyze": "PASS", "review": "APPROVED", "ci": "DISABLED_BY_PROJECT_POLICY"})
        value["loop"].update({"mode": "PAUSED", "loop_active": False})

        result = driver.evaluate_next(value, plan)

        self.assertEqual("COMPLETE", result["decision"])
        self.assertEqual("DONE", result["terminal_reason"])
        self.assertNotEqual("EXECUTE_NEXT", result["decision"])

    def test_closed_bounded_run_preserves_executor_call_budget_reason(self) -> None:
        value, plan = self.approved()
        value["loop"].update({
            "mode": "PAUSED",
            "loop_active": False,
            "stop_reason": "NONE",
            "last_run": {"stop_reason": "EXECUTOR_CALLS_BUDGET_REACHED"},
        })

        result = driver.evaluate_next(value, plan)

        self.assertEqual("COMPLETE", result["decision"])
        self.assertEqual("EXECUTOR_CALLS_BUDGET_REACHED", result["terminal_reason"])
        self.assertNotEqual("EXECUTE_NEXT", result["decision"])

    def test_closed_bounded_run_preserves_human_required_reason(self) -> None:
        value, plan = self.approved()
        value["loop"].update({
            "mode": "PAUSED",
            "loop_active": False,
            "stop_reason": "NONE",
            "last_run": {"stop_reason": "HUMAN_REQUIRED"},
        })

        result = driver.evaluate_next(value, plan)

        self.assertEqual("COMPLETE", result["decision"])
        self.assertEqual("HUMAN_REQUIRED", result["terminal_reason"])
        self.assertNotEqual("EXECUTE_NEXT", result["decision"])

    def test_closed_bounded_run_preserves_blocker_reason(self) -> None:
        value, plan = self.approved()
        value["loop"].update({
            "mode": "PAUSED",
            "loop_active": False,
            "stop_reason": "NONE",
            "last_run": {"stop_reason": "BLOCKED"},
        })

        result = driver.evaluate_next(value, plan)

        self.assertEqual("COMPLETE", result["decision"])
        self.assertEqual("BLOCKED", result["terminal_reason"])
        self.assertEqual("BLOCKED", result["state_updates"]["stage_status"])
        self.assertNotEqual("EXECUTE_NEXT", result["decision"])

    def test_executor_call_budget_stops_before_dispatch(self) -> None:
        value, plan = self.approved()
        value["loop"]["budgets"]["executor_calls"]["used"] = 8
        result = driver.evaluate_next(value, plan)
        self.assertEqual("STOP_BUDGET", result["decision"])
        self.assertEqual("EXECUTOR_CALLS_BUDGET_REACHED", result["stop_reason"])

    def test_human_required_stops(self) -> None:
        value, plan = self.approved()
        value["loop"]["human_approval_required"] = True
        self.assertEqual("STOP_HUMAN_REQUIRED", driver.evaluate_next(value, plan)["decision"])

    def test_blocker_stops(self) -> None:
        value, plan = self.approved()
        value["state"]["blockers"] = ["contract invalid"]
        result = driver.evaluate_next(value, plan)
        self.assertEqual("STOP_BLOCKED", result["decision"])
        self.assertEqual("BLOCKED", result["state_updates"]["stage_status"])

    def test_inconclusive_recovery_stops(self) -> None:
        value, plan = self.approved()
        value["recovery"]["decision"] = "WAIT_OR_MANUAL_REVIEW"
        self.assertEqual("STOP_RECOVERY", driver.evaluate_next(value, plan)["decision"])

    def test_action_outside_plan_or_skipped_sequence_blocks(self) -> None:
        value, plan = self.approved()
        value["state"]["next_action"] = "TASKS"
        result = driver.evaluate_next(value, plan)
        self.assertEqual("STOP_BLOCKED", result["decision"])
        self.assertEqual("SEQUENCE_SKIPPED", result["stop_reason"])

    def test_runtime_plan_hash_mismatch_is_rejected(self) -> None:
        value, plan = self.approved()
        value["runtime"]["approved_plan_sha256"] = "0" * 64
        with self.assertRaises(driver.DriverError):
            driver.evaluate_next(value, plan)

    def test_driver_rejects_plan_actions_outside_the_fsm(self) -> None:
        for field, invalid_stage in (("stage", "FORMAT"), ("success_transition", "CI")):
            with self.subTest(field=field):
                value, plan = self.approved()
                plan["actions"][0][field] = invalid_stage
                plan["authorization"]["plan_sha256"] = planner.hash_plan(plan)
                value["runtime"]["approved_plan_sha256"] = plan["authorization"]["plan_sha256"]
                with self.assertRaisesRegex(driver.DriverError, "INVALID_PLAN"):
                    driver.evaluate_next(value, plan)

    def test_driver_rejects_rehashed_semantically_tampered_actions(self) -> None:
        mutations = (
            ("invalid canonical transition", lambda entry: entry.update({"success_transition": "IMPLEMENT"})),
            ("duplicate sequence", lambda entry: entry.update({"sequence": 2, "id": "action-2"})),
            ("wrong classification", lambda entry: entry.update({"classification": "AUTO_SAFE", "executor": "HOST", "human_approval_required": False})),
            ("wrong budget cost", lambda entry: entry["budget_cost"].update({"executor_calls": 0})),
            ("wrong external mutation", lambda entry: entry.update({"external_mutation": True})),
        )
        for name, mutate in mutations:
            with self.subTest(name=name):
                value, plan = self.approved()
                mutate(plan["actions"][0])
                plan["authorization"]["plan_sha256"] = planner.hash_plan(plan)
                value["runtime"]["approved_plan_sha256"] = plan["authorization"]["plan_sha256"]
                with self.assertRaisesRegex(driver.DriverError, "INVALID_PLAN"):
                    driver.evaluate_next(value, plan)

    def test_should_reject_a_canonical_state_transaction_update_injected_after_specify(self) -> None:
        value, plan = self.approved()

        inject_canonical_state_transaction_update(plan)
        plan["authorization"]["plan_sha256"] = planner.hash_plan(plan)
        value["runtime"]["approved_plan_sha256"] = plan["authorization"]["plan_sha256"]
        value["runtime"]["planned_action_count"] = len(plan["actions"])

        with self.assertRaisesRegex(driver.DriverError, "INVALID_PLAN"):
            driver.evaluate_next(value, plan)

    def test_successful_sequence_uses_real_local_journal_rollovers(self) -> None:
        value, plan = self.approved()
        with tempfile.TemporaryDirectory(prefix="bounded driver journal ") as directory:
            root = Path(directory)
            journal_path = root / "ACTION_JOURNAL.json"
            workspace = {"path": str(root), "branch": "dev", "head": "a" * 40, "git_common_dir": str(root / ".git")}
            journal.atomic_write(journal_path, journal.empty_journal(workspace))
            executed: list[str] = []
            for sequence in range(3):
                decision = driver.evaluate_next(value, plan)
                self.assertEqual("EXECUTE_NEXT", decision["decision"])
                executed.append(decision["action"])
                prepared = journal.empty_journal(workspace)
                prepared["action"].update({
                    "id": f"APP-999-{sequence + 1}", "ticket": "APP-999", "stage": decision["action"], "name": decision["action"].lower(),
                    "status": "PREPARED", "executor": "CODEX", "schema_path": "schema.json", "protocol_version": 2,
                    "prompt_hash": hashlib.sha256(b"prompt").hexdigest(), "final_message_path": str(root / f"result-{sequence}.json"), "attempt": 1, "retry_mode": "FULL_REPLACEMENT",
                })
                journal.prepare_action(journal_path, prepared)
                active = journal.load_journal(journal_path)
                for status in ("DISPATCHED", "PROCESS_FINISHED", "ARTIFACT_READY", "VALIDATED", "STATE_COMMITTED", "RELEASED"):
                    active = journal.transition(active, status)
                Path(active["action"]["final_message_path"]).write_text("{}", encoding="utf-8")
                active["artifact"].update({"exists": True, "sha256": hashlib.sha256(b"{}").hexdigest(), "validation_status": "VALID"})
                active["state_commit"].update({"state_path": str(root / "STATE.md"), "expected_before_hash": "a" * 64, "expected_after_hash": "b" * 64, "committed_after_hash": "b" * 64, "committed_at": "2026-09-16T00:00:00Z", "verified": True})
                journal.atomic_write(journal_path, active)
                value = self.succeed(value, plan)
                if sequence < 2:
                    self.assertEqual("ROLLOVER_REQUIRED", driver.evaluate_next(value, plan)["decision"])
                    rollover = journal.rollover_journal(journal_path, root / "history")
                    self.assertEqual("DISPATCH_ALLOWED", rollover["recovery_after_rollover"])
                    value = self.rollover(value)
            result = driver.evaluate_next(value, plan)
        self.assertEqual(["SPECIFY", "CLARIFY", "PLAN"], executed)
        self.assertEqual("STOP_BUDGET", result["decision"])
        self.assertFalse(result["state_updates"]["loop_active"])
        self.assertEqual("STAGE_TRANSITION_BUDGET_REACHED", result["state_updates"]["last_run"]["stop_reason"])
        self.assertEqual(3, result["state_updates"]["last_run"]["actions_executed"])
        self.assertEqual(3, result["state_updates"]["last_run"]["stage_transitions"])

    def test_no_extra_action_after_terminal_stop(self) -> None:
        value, plan = self.approved()
        value["loop"]["loop_active"] = False
        first = driver.evaluate_next(value, plan)
        second = driver.evaluate_next(value, plan)
        self.assertEqual("COMPLETE", first["decision"])
        self.assertEqual(first["decision"], second["decision"])

    def test_each_decision_records_complete_immutable_observability(self) -> None:
        value, plan = self.approved()
        decision = driver.evaluate_next(value, plan)

        event = decision["event"]
        self.assertEqual(
            {
                "sequence", "action", "decision", "budget_before", "budget_after",
                "recovery_before", "recovery_after", "journal_action_id",
                "state_hash_before", "state_hash_after", "stop_reason",
            },
            set(event),
        )
        self.assertEqual(event["budget_before"], event["budget_after"])
        self.assertEqual(event["recovery_before"], event["recovery_after"])
        self.assertEqual(event["state_hash_before"], event["state_hash_after"])
        self.assertEqual("EXECUTE_NEXT", event["decision"])

    def test_done_closes_the_run_without_an_invalid_loop_mode(self) -> None:
        value, plan = self.approved()
        value["state"].update({"stage": "DONE", "status": "DONE"})
        value["gates"].update({"focused_tests": "PASS", "format": "PASS", "analyze": "PASS", "review": "APPROVED", "ci": "DISABLED_BY_PROJECT_POLICY"})
        result = driver.evaluate_next(value, plan)
        self.assertEqual("COMPLETE", result["decision"])
        self.assertEqual("PAUSED", result["state_updates"]["mode"])
        self.assertEqual("COMPLETED", result["state_updates"]["stage_status"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
