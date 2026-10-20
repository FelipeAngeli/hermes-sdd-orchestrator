"""BUDGET_BELOW_PLAN: refuse IMPLEMENT when the plan exceeds the remaining slice/executor budget."""
from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import plan_checks  # noqa: E402
import sdd  # noqa: E402


def budget_state(planned: int, *, tdd_max: int = 6, tdd_used: int = 0, exec_max: int = 12, exec_used: int = 0) -> dict:
    return {
        "stage": {"current": "TASKS", "status": "RUNNING", "completed": ["SPECIFY", "CLARIFY", "PLAN"], "skipped": []},
        "delivery": {
            "profile": "CODE",
            "accepted": {"tasks": {}},
            "slices": {"planned": [f"S{i}" for i in range(1, planned + 1)], "completed": [], "editable_paths": ["tasks/x/**"]},
        },
        "loop": {"budgets": {
            "stage_transitions": {"max": 10, "used": 0},
            "tdd_slices": {"max": tdd_max, "used": tdd_used},
            "executor_calls": {"max": exec_max, "used": exec_used},
        }},
    }


class BudgetBelowPlanTests(unittest.TestCase):
    def test_nine_slices_with_six_tdd_slices_stops_with_deficit_three(self) -> None:
        deficit = plan_checks.budget_below_plan(budget_state(9), "CODE")
        self.assertEqual({"tdd_slices": 3, "executor_calls": 0}, deficit)

    def test_short_executor_calls_stop(self) -> None:
        deficit = plan_checks.budget_below_plan(budget_state(9, tdd_max=9, exec_max=10), "CODE")
        self.assertEqual({"tdd_slices": 0, "executor_calls": 1}, deficit)

    def test_enough_budget_does_not_stop(self) -> None:
        self.assertIsNone(plan_checks.budget_below_plan(budget_state(9, tdd_max=9), "CODE"))

    def test_after_a_raise_the_transition_proceeds(self) -> None:
        data = budget_state(9)
        self.assertEqual({"tdd_slices": 3, "executor_calls": 0}, plan_checks.budget_below_plan(data, "CODE"))
        data["loop"]["budgets"]["tdd_slices"]["max"] = 9
        self.assertIsNone(plan_checks.budget_below_plan(data, "CODE"))


class FakeTransitionCtx:
    def __init__(self, state: dict) -> None:
        self.state = state
        self.stage = state["stage"]["current"]
        self.profile = state["delivery"]["profile"]
        self.delivery = state["delivery"]
        self.ticket = "APP-1"
        self.repo = Path(".")

    def require_pristine(self) -> None:
        return None

    def write_state(self, data: dict, log: str) -> None:
        self.written = data
        self.log = log


class FakeNextCtx:
    def __init__(self, state: dict) -> None:
        self.state = state
        self.stage = state["stage"]["current"]
        self.profile = state["delivery"]["profile"]
        self.delivery = state["delivery"]


class BudgetNextStopTests(unittest.TestCase):
    def test_next_stops_before_printing_tasks_to_implement_transition(self) -> None:
        ctx = FakeNextCtx(budget_state(9))
        result = sdd.transition_step(ctx)
        self.assertEqual("STOP", result["status"])
        self.assertEqual("BUDGET_BELOW_PLAN", result["stop_reason"])
        self.assertEqual({"tdd_slices": 3, "executor_calls": 0}, result["deficit"])
        self.assertIn("budget --raise tdd_slices --by 3", result["next_command"])

    def test_after_a_raise_next_prints_the_transition(self) -> None:
        data = budget_state(9)
        data["loop"]["budgets"]["tdd_slices"]["max"] = 9
        result = sdd.transition_step(FakeNextCtx(data))
        self.assertEqual("TRANSITION", result["step"])
        self.assertIn("transition --to IMPLEMENT", result["commands"][0])


class BudgetTransitionGuardTests(unittest.TestCase):
    def test_transition_refuses_with_deficit_and_raise_commands(self) -> None:
        ctx = FakeTransitionCtx(budget_state(9))
        with self.assertRaises(sdd.SddError) as caught:
            sdd.cmd_transition(ctx, argparse.Namespace(to="IMPLEMENT", artifact=None, skip_reason=None, reason=None, quote=None))
        self.assertEqual("BUDGET_BELOW_PLAN", caught.exception.code)
        self.assertEqual({"tdd_slices": 3, "executor_calls": 0}, caught.exception.extra["deficit"])
        self.assertEqual(1, len(caught.exception.extra["next_commands"]))
        self.assertIn("budget --raise tdd_slices --by 3", caught.exception.next_command)


if __name__ == "__main__":
    unittest.main()
