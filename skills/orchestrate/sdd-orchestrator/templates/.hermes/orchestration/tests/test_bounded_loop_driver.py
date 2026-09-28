"""Isolated TDD coverage for the bounded-loop runtime driver.

The driver decides whether an authorized BOUNDED_AUTO round may dispatch the
next planned action in the same turn. It never invokes an executor.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))

import bounded_run_planner as planner
from test_bounded_run_planner import local_delivery_snapshot, snapshot

import bounded_loop_driver as driver


SCRIPT = RUNTIME / "bounded_loop_driver.py"


def authorized_plan(value: dict | None = None) -> dict:
    return planner.create_plan(value or snapshot(), "NEXT_HUMAN_CHECKPOINT")


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


def smoke4_after_specify(before: dict | None = None) -> dict:
    """Post-SPECIFY snapshot matching the smoke-4 evidence: CLARIFY/WAITING, loop still active."""
    value = copy.deepcopy(before or snapshot())
    value["state"].update({
        "sha256": "c" * 64,
        "stage": "CLARIFY",
        "status": "WAITING",
        "completed": ["SPECIFY"],
        "next_action": "CLARIFY",
    })
    value["loop"].update({
        "mode": "BOUNDED_AUTO",
        "loop_active": True,
        "stop_reason": "NONE",
        "human_approval_required": False,
    })
    value["loop"]["budgets"]["stage_transitions"]["used"] = 1
    value["loop"]["budgets"]["executor_calls"]["used"] = 1
    value["recovery"].update({
        "decision": "DISPATCH_ALLOWED",
        "journal_status": "IDLE",
        "current_action_id": None,
        "artifact_present": False,
    })
    return value


def advance(current: dict, entry: dict) -> dict:
    value = copy.deepcopy(current)
    action = entry["action"]
    if action not in value["state"]["completed"]:
        value["state"]["completed"] = [*value["state"]["completed"], action]
    value["state"]["stage"] = entry["success_transition"]
    value["state"]["status"] = "WAITING"
    value["state"]["next_action"] = entry["success_transition"]
    value["state"]["sha256"] = bytes.fromhex(value["state"]["sha256"]).hex()[::-1].ljust(64, "0")[:64]
    for key, (source, used_key, _max_key) in planner.BUDGET_SOURCES.items():
        value["loop"]["budgets"][source][used_key] += entry["budget_cost"][key]
    value["recovery"].update({"decision": "DISPATCH_ALLOWED", "journal_status": "IDLE", "artifact_present": False})
    value["loop"]["loop_active"] = True
    value["loop"]["mode"] = "BOUNDED_AUTO"
    value["loop"]["stop_reason"] = "NONE"
    return value


class BoundedLoopDriverTests(unittest.TestCase):
    def test_after_specify_rollover_continues_to_clarify_without_ending_turn(self) -> None:
        before = snapshot()
        plan = authorized_plan(before)
        after = smoke4_after_specify(before)
        self.assertIn("PLAN_STALE", planner.validate_plan(plan, after))

        decision = driver.evaluate_next(after, plan)

        self.assertEqual("CONTINUE", decision["decision"])
        self.assertFalse(decision["end_turn"])
        self.assertEqual("CLARIFY", decision["next_action"])
        self.assertTrue(decision["loop_active"])
        self.assertEqual("NONE", decision["stop_reason"])

    def test_rehashed_test_subset_cannot_bypass_pending_gate_baseline(self) -> None:
        value = snapshot(stage="TEST", status="WAITING", mode="BOUNDED_AUTO")
        value["loop"]["loop_active"] = True
        value["state"]["next_action"] = "TEST"
        plan = authorized_plan(value)
        plan["actions"] = [entry for entry in plan["actions"] if entry["action"] == "ANALYZE"]
        plan["actions"][0].update({"sequence": 1, "id": "action-1"})
        plan["authorization"]["plan_sha256"] = planner.hash_plan(plan)

        with self.assertRaisesRegex(driver.DriverError, "INVALID_PLAN"):
            driver.evaluate_next(value, plan)

    def test_local_delivery_authorization_drift_is_rejected_before_dispatch(self) -> None:
        value = local_delivery_snapshot()
        plan = planner.create_plan(value)
        plan["local_delivery"]["authorization"]["id"] = "forged-authorization"
        plan["local_delivery"]["authorization_sha256"] = planner.sha256_json(
            plan["local_delivery"]["authorization"]
        )
        plan["authorization"]["plan_sha256"] = planner.hash_plan(plan)

        with self.assertRaisesRegex(driver.DriverError, "authorization drift"):
            driver.evaluate_next(value, plan)

    def test_local_delivery_rejects_gate_baseline_substitution_and_live_regression(self) -> None:
        pending = local_delivery_snapshot()
        pending["state"].update({"stage": "TEST", "next_action": "TEST"})
        substituted = planner.create_plan(copy.deepcopy(pending))
        substituted["gate_baseline"].update({"focused_tests": "PASS", "format": "PASS"})
        substituted["actions"] = [
            entry for entry in substituted["actions"]
            if entry["action"] not in {"TEST_FOCUSED", "FORMAT_CHANGED_FILES"}
        ]
        for sequence, entry in enumerate(substituted["actions"], start=1):
            entry.update({"sequence": sequence, "id": f"action-{sequence}"})
        substituted["authorization"]["plan_sha256"] = planner.hash_plan(substituted)

        with self.assertRaisesRegex(driver.DriverError, "gate baseline"):
            driver.evaluate_next(pending, substituted)

        approved = local_delivery_snapshot()
        approved["state"].update({
            "stage": "REVIEW",
            "status": "APPROVED",
            "next_action": "REVIEW",
        })
        approved["gates"].update({
            "focused_tests": "PASS",
            "format": "PASS",
            "analyze": "PASS",
            "review": "APPROVED",
            "ci": "DISABLED_BY_PROJECT_POLICY",
        })
        plan = planner.create_plan(copy.deepcopy(approved))
        approved["gates"]["focused_tests"] = "PENDING"

        with self.assertRaisesRegex(driver.DriverError, "gate progress"):
            driver.evaluate_next(approved, plan)

    def test_local_delivery_rejects_completed_actions_without_cumulative_budget_charge(self) -> None:
        """Bug caught: completed actions could advance without consuming authorized budget."""
        executor_progress = local_delivery_snapshot()
        executor_plan = planner.create_plan(copy.deepcopy(executor_progress))
        executor_progress["state"].update({
            "stage": "CLARIFY",
            "completed": ["SPECIFY"],
            "next_action": "CLARIFY",
        })

        with self.assertRaisesRegex(driver.DriverError, "ledger length does not match accepted actions"):
            driver.evaluate_next(executor_progress, executor_plan)

        slice_progress = local_delivery_snapshot()
        slice_progress["state"].update({"stage": "IMPLEMENT", "next_action": "IMPLEMENT_SLICE"})
        slice_plan = planner.create_plan(copy.deepcopy(slice_progress))
        slice_progress["implementation"].update({
            "completed_slices": ["slice-1"],
            "next_slice": "slice-2",
        })

        with self.assertRaisesRegex(driver.DriverError, "ledger length does not match accepted actions"):
            driver.evaluate_next(slice_progress, slice_plan)

    def test_local_delivery_rejects_reused_historical_ledger_charge(self) -> None:
        """Bug caught: an old ledger charge could be reused to pay for a new plan action."""
        value = local_delivery_snapshot()
        historical_cost = planner.zero_budgets()
        historical_cost["executor_calls"] = 1
        value["local_delivery"]["usage_ledger"] = [
            {"action_id": "prior-plan:action-1", "cost": historical_cost}
        ]
        value["loop"]["budgets"]["executor_calls"]["used"] = 1
        plan = planner.create_plan(copy.deepcopy(value))
        plan["local_delivery"]["usage_floor"] = planner.zero_budgets()
        plan["local_delivery"]["usage_ledger_size"] = 0
        plan["local_delivery"]["usage_ledger_sha256"] = planner.sha256_json([])
        plan["authorization"]["plan_sha256"] = planner.hash_plan(plan)
        value["state"].update({
            "stage": "CLARIFY",
            "completed": ["SPECIFY"],
            "next_action": "CLARIFY",
        })

        with self.assertRaisesRegex(driver.DriverError, "ledger action id drift"):
            driver.evaluate_next(value, plan)

    def test_local_delivery_accepts_exact_plan_specific_ledger_charge(self) -> None:
        """Bug caught: valid charged progress could be rejected despite exact ID and cost evidence."""
        value = local_delivery_snapshot()
        plan = planner.create_plan(copy.deepcopy(value))
        accepted = plan["actions"][0]
        value["state"].update({
            "stage": accepted["success_transition"],
            "completed": [accepted["action"]],
            "next_action": accepted["success_transition"],
        })
        value["local_delivery"]["usage_ledger"].append({
            "action_id": f'{plan["plan_id"]}:{accepted["id"]}',
            "cost": copy.deepcopy(accepted["budget_cost"]),
        })
        for key, cost in accepted["budget_cost"].items():
            source, used_key, _ = planner.BUDGET_SOURCES[key]
            value["loop"]["budgets"][source][used_key] += cost

        decision = driver.evaluate_next(value, plan)

        self.assertEqual("CONTINUE", decision["decision"])
        self.assertEqual("CLARIFY", decision["next_action"])

    def test_local_delivery_zero_cost_action_still_requires_ledger_identity(self) -> None:
        """Bug caught: zero-cost host actions could advance without plan-specific identity evidence."""
        value = local_delivery_snapshot()
        value["state"].update({"stage": "TEST", "next_action": "TEST"})
        value["gates"]["focused_tests"] = "PASS"
        plan = planner.create_plan(copy.deepcopy(value))
        accepted = plan["actions"][0]
        self.assertEqual("FORMAT_CHANGED_FILES", accepted["action"])
        self.assertEqual(planner.zero_budgets(), accepted["budget_cost"])
        value["state"]["completed"] = [accepted["action"]]
        value["gates"]["format"] = "PASS"

        with self.assertRaisesRegex(driver.DriverError, "ledger length"):
            driver.evaluate_next(value, plan)

        value["local_delivery"]["usage_ledger"].append({
            "action_id": f'{plan["plan_id"]}:{accepted["id"]}',
            "cost": planner.zero_budgets(),
        })
        decision = driver.evaluate_next(value, plan)
        self.assertEqual("CONTINUE", decision["decision"])
        self.assertEqual("ANALYZE", decision["next_action"])

    def test_local_delivery_rejects_plan_specific_entry_with_wrong_cost(self) -> None:
        """Bug caught: a correct action ID paired with an undercharged cost could be accepted."""
        value = local_delivery_snapshot()
        plan = planner.create_plan(copy.deepcopy(value))
        accepted = plan["actions"][0]
        self.assertNotEqual(planner.zero_budgets(), accepted["budget_cost"])
        value["state"].update({
            "stage": accepted["success_transition"],
            "completed": [accepted["action"]],
            "next_action": accepted["success_transition"],
        })
        value["local_delivery"]["usage_ledger"].append({
            "action_id": f'{plan["plan_id"]}:{accepted["id"]}',
            "cost": planner.zero_budgets(),
        })

        with self.assertRaisesRegex(driver.DriverError, "ledger action cost drift"):
            driver.evaluate_next(value, plan)

    def test_runtime_loop_dispatches_remaining_actions_without_human_continue(self) -> None:
        before = snapshot()
        plan = authorized_plan(before)
        executed: list[str] = []

        def dispatch(entry: dict, current: dict) -> dict:
            executed.append(entry["action"])
            return advance(current, entry)

        result = driver.run_until_stop(smoke4_after_specify(before), plan, dispatch)

        self.assertEqual(["CLARIFY", "PLAN"], executed)
        self.assertEqual("STOP", result["decision"])
        self.assertTrue(result["end_turn"])
        self.assertEqual("BUDGET_REACHED", result["stop_reason"])

    def test_released_journal_requires_rollover_and_does_not_end_turn(self) -> None:
        after = smoke4_after_specify()
        after["recovery"].update({"decision": "RELEASED", "journal_status": "RELEASED"})
        decision = driver.evaluate_next(after, authorized_plan())
        self.assertEqual("ROLLOVER_REQUIRED", decision["decision"])
        self.assertFalse(decision["end_turn"])
        self.assertEqual("CLARIFY", decision["next_action"])

    def test_stop_when_budget_blocker_human_or_inactive(self) -> None:
        plan = authorized_plan()
        exhausted = smoke4_after_specify()
        exhausted["loop"]["budgets"]["stage_transitions"]["used"] = 3
        blocked = smoke4_after_specify()
        blocked["state"]["blockers"] = ["blocked"]
        human = smoke4_after_specify()
        human["loop"]["human_approval_required"] = True
        inactive = smoke4_after_specify()
        inactive["loop"]["loop_active"] = False
        manual = smoke4_after_specify()
        manual["loop"]["mode"] = "MANUAL"
        restricted = smoke4_after_specify()
        restricted["restrictions"]["protected_file_required"] = True

        cases = (
            (exhausted, "BUDGET_REACHED"),
            (blocked, "BLOCKED"),
            (human, "HUMAN_APPROVAL_REQUIRED"),
            (inactive, "LOOP_INACTIVE"),
            (manual, "MODE_NOT_BOUNDED_AUTO"),
            (restricted, "HUMAN_REQUIRED"),
        )
        for value, stop_reason in cases:
            with self.subTest(stop_reason=stop_reason):
                decision = driver.evaluate_next(value, plan)
                self.assertEqual("STOP", decision["decision"])
                self.assertTrue(decision["end_turn"])
                self.assertEqual(stop_reason, decision["stop_reason"])
                self.assertIsNone(decision["next_action"])

    def test_workspace_identity_change_is_stale_but_expected_progress_is_not(self) -> None:
        before = snapshot()
        plan = authorized_plan(before)
        progressed = smoke4_after_specify(before)
        self.assertEqual("CONTINUE", driver.evaluate_next(progressed, plan)["decision"])
        drifted = copy.deepcopy(progressed)
        drifted["workspace"]["branch"] = "other"
        decision = driver.evaluate_next(drifted, plan)
        self.assertEqual("STOP", decision["decision"])
        self.assertEqual("PLAN_STALE", decision["stop_reason"])

    def test_rehashed_semantically_tampered_next_action_is_rejected_before_dispatch(self) -> None:
        before = snapshot()
        current = smoke4_after_specify(before)
        mutations = (
            (
                "unknown action",
                lambda entry: entry.update({"action": "FORGED_UNKNOWN_ACTION"}),
            ),
            (
                "wrong canonical classification",
                lambda entry: entry.update({"classification": "AUTO_SAFE"}),
            ),
            (
                "forged completion bypassing prerequisite gates",
                lambda entry: entry.update({"action": "EVALUATE_DONE_WITH_CI_DISABLED"}),
            ),
        )
        for name, mutate in mutations:
            with self.subTest(name=name):
                plan = authorized_plan(before)
                mutate(plan["actions"][1])
                plan["authorization"]["plan_sha256"] = planner.hash_plan(plan)

                with self.assertRaisesRegex(driver.DriverError, "INVALID_PLAN"):
                    driver.evaluate_next(current, plan)

    def test_should_reject_a_canonical_state_transaction_update_injected_after_specify(self) -> None:
        before = snapshot()
        plan = authorized_plan(before)

        inject_canonical_state_transaction_update(plan)
        plan["authorization"]["plan_sha256"] = planner.hash_plan(plan)

        with self.assertRaisesRegex(driver.DriverError, "INVALID_PLAN"):
            driver.evaluate_next(before, plan)

    def test_implementation_cursor_uses_plan_baseline_and_detects_planned_slice_drift(self) -> None:
        before = snapshot(stage="IMPLEMENT")
        before["implementation"].update({"completed_slices": ["slice-1"], "next_slice": "slice-2"})
        plan = authorized_plan(before)
        current = copy.deepcopy(before)
        current["loop"].update({"mode": "BOUNDED_AUTO", "loop_active": True})
        current["state"]["next_action"] = "IMPLEMENT_SLICE"

        self.assertEqual(["action-1", "action-2", "action-3"], [entry["id"] for entry in driver.remaining_actions(current, plan)])
        current["implementation"]["completed_slices"].append("slice-2")
        self.assertEqual(["action-2", "action-3"], [entry["id"] for entry in driver.remaining_actions(current, plan)])
        self.assertEqual("CONTINUE", driver.evaluate_next(current, plan)["decision"])

        current["implementation"]["planned_slices"].append("slice-5")
        decision = driver.evaluate_next(current, plan)
        self.assertEqual("STOP", decision["decision"])
        self.assertEqual("PLAN_STALE", decision["stop_reason"])

    def test_implementation_cursor_stops_when_post_baseline_slices_are_out_of_order(self) -> None:
        before = snapshot(stage="IMPLEMENT")
        before["implementation"].update({"completed_slices": ["slice-1"], "next_slice": "slice-2"})
        plan = authorized_plan(before)
        current = copy.deepcopy(before)
        current["loop"].update({"mode": "BOUNDED_AUTO", "loop_active": True})
        current["state"]["next_action"] = "IMPLEMENT_SLICE"
        current["implementation"]["completed_slices"].append("slice-3")

        self.assertEqual([], driver.remaining_actions(current, plan))
        decision = driver.evaluate_next(current, plan)

        self.assertEqual("STOP", decision["decision"])
        self.assertTrue(decision["end_turn"])
        self.assertEqual("PLAN_CURSOR_MISMATCH", decision["stop_reason"])

    def test_run_until_stop_stops_without_semantic_progress_and_never_redispatches_action_id(self) -> None:
        before = snapshot()
        plan = authorized_plan(before)
        for mutate in (False, True):
            with self.subTest(mutate=mutate):
                calls: list[str] = []

                def dispatch(entry: dict, current: dict) -> dict:
                    calls.append(entry["id"])
                    if not mutate:
                        return copy.deepcopy(current)
                    changed = copy.deepcopy(current)
                    changed["state"]["sha256"] = "d" * 64
                    changed["loop"]["budgets"]["executor_calls"]["used"] += 1
                    changed["recovery"]["journal_status"] = "PREPARED"
                    return changed

                result = driver.run_until_stop(smoke4_after_specify(before), plan, dispatch)

                self.assertEqual(["action-2"], calls)
                self.assertEqual(["CLARIFY"], result["executed"])
                self.assertEqual("STOP", result["decision"])
                self.assertTrue(result["end_turn"])
                self.assertEqual("NO_PROGRESS", result["stop_reason"])

    def test_run_until_stop_consumes_each_implementation_action_once_when_slices_advance(self) -> None:
        before = snapshot(stage="IMPLEMENT")
        before["implementation"].update({"completed_slices": ["slice-1"], "next_slice": "slice-2"})
        plan = authorized_plan(before)
        current = copy.deepcopy(before)
        current["loop"].update({"mode": "BOUNDED_AUTO", "loop_active": True})
        current["state"]["next_action"] = "IMPLEMENT_SLICE"
        calls: list[str] = []

        def dispatch(entry: dict, current: dict) -> dict:
            calls.append(entry["id"])
            advanced = advance(current, entry)
            next_slice = next(
                item for item in advanced["implementation"]["planned_slices"]
                if item not in advanced["implementation"]["completed_slices"]
            )
            advanced["implementation"]["completed_slices"].append(next_slice)
            return advanced

        result = driver.run_until_stop(current, plan, dispatch)

        self.assertEqual(["action-1", "action-2", "action-3"], calls)
        self.assertEqual(["IMPLEMENT_SLICE", "IMPLEMENT_SLICE", "IMPLEMENT_SLICE"], result["executed"])
        self.assertEqual("STOP", result["decision"])
        self.assertEqual(plan["termination"]["expected_reason"], result["stop_reason"])

    def test_cli_has_no_execution_flags_and_emits_continue_for_smoke4(self) -> None:
        help_result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
        self.assertEqual(0, help_result.returncode)
        for prohibited in ("--execute", "--apply", "--force", "--ignore-budget", "--auto-approve", "--run"):
            self.assertNotIn(prohibited, help_result.stdout)

        before = snapshot()
        plan = authorized_plan(before)
        with tempfile.TemporaryDirectory(prefix="bounded driver spaces ") as directory:
            base = Path(directory)
            snapshot_path = base / "snapshot with spaces.json"
            plan_path = base / "plan with spaces.json"
            snapshot_path.write_text(json.dumps(smoke4_after_specify(before)), encoding="utf-8")
            plan_path.write_bytes(planner.canonical_json(plan) + b"\n")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "next", "--snapshot", str(snapshot_path), "--plan", str(plan_path), "--json"],
                text=True,
                capture_output=True,
                check=False,
                timeout=20,
            )
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual("CONTINUE", payload["decision"])
        self.assertFalse(payload["end_turn"])
        self.assertEqual("CLARIFY", payload["next_action"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
