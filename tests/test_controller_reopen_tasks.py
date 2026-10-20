"""End-to-end controller test for `sdd.py reopen --to TASKS` after a slice-data manifest refusal."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_controller_sdd_guards as guards  # noqa: E402


class ReopenToTasksTests(guards.SddGuardTests):
    """Reuses the guard fixtures; runs only the reopen-to-TASKS scenario."""

    def test_reopen_to_tasks_after_slice_manifest_refusal_redispatches_tasks(self) -> None:
        self.craft(stage="IMPLEMENT")
        self.edit_state(lambda data: data["delivery"].update({
            "slices": {"planned": ["S1"], "completed": [], "editable_paths": []},
        }) or data["delivery"]["accepted"].pop("implement-s1", None))

        code, stopped = self.call("next")
        self.assertEqual(0, code, stopped)
        self.assertEqual("MANIFEST_INVALID", stopped.get("stop_reason"), stopped)
        self.assertIn("reopen --to TASKS", stopped["next_command"])

        code, reopened = self.run_printed(stopped["next_command"])
        self.assertEqual(0, code, reopened)
        self.assertEqual("REOPENED", reopened["status"], reopened)

        code, redispatched = self.call("next")
        self.assertEqual(0, code, redispatched)
        self.assertEqual("TASKS", redispatched.get("stage"), redispatched)
        self.assertEqual("PREPARE", redispatched.get("step"), redispatched)

    def test_transition_to_tasks_cannot_bypass_completed_slice_guard(self) -> None:
        self.craft(stage="IMPLEMENT")
        code, refused = self.call("transition", "--to", "TASKS", "--reason", "unsafe", "--quote", "approved")
        self.assertNotEqual(0, code, refused)
        self.assertEqual("STEP_MISMATCH", refused["status"])
        self.assertIn("completed", refused["message"])

    def test_decision_doc_implement_cannot_reopen_to_tasks_by_either_command(self) -> None:
        self.craft(profile="DECISION_DOC", stage="IMPLEMENT", slice_id="DOC")
        for command in (("transition", "--to", "TASKS", "--reason", "unsafe", "--quote", "approved"),
                        ("reopen", "--to", "TASKS", "--reason", "unsafe", "--quote", "approved")):
            with self.subTest(command=command):
                code, refused = self.call(*command)
                self.assertNotEqual(0, code, refused)
                self.assertEqual("STEP_MISMATCH", refused["status"])

    def test_next_stops_forward_budget_deficit_with_the_exact_raise_command(self) -> None:
        self.craft(stage="TASKS")
        self.edit_state(lambda data: data["delivery"].update({"slices": {
            "planned": [f"S{number}" for number in range(1, 10)], "completed": [], "editable_paths": ["src/feature.py"],
        }}) or data["loop"]["budgets"]["tdd_slices"].update({"max": 6, "used": 0})
                           or data["loop"]["budgets"]["executor_calls"].update({"max": 20, "used": 0}))
        code, stopped = self.call("next")
        self.assertEqual(0, code, stopped)
        self.assertEqual("BUDGET_BELOW_PLAN", stopped["stop_reason"])
        self.assertEqual({"tdd_slices": 3, "executor_calls": 0}, stopped["deficit"])
        self.assertIn("budget --raise tdd_slices --by 3", stopped["next_command"])

    def test_review_reopen_uses_only_the_pending_fix_slice_for_budget(self) -> None:
        self.craft(stage="REVIEW", review="CHANGES_REQUIRED")
        self.edit_state(lambda data: data["delivery"].update({"slices": {
            "planned": ["S1", "S2", "S3"], "completed": ["S1", "S2", "S3"], "editable_paths": ["src/feature.py"],
        }}) or data["loop"]["budgets"]["tdd_slices"].update({"max": 6, "used": 3})
                           or data["loop"]["budgets"]["executor_calls"].update({"max": 20, "used": 0}))
        code, reopened = self.call("reopen", "--reason", "review findings", "--quote", "fix them")
        self.assertEqual(0, code, reopened)
        self.assertEqual("REOPENED", reopened["status"])
        self.assertEqual(["S1", "S2", "S3", "FIX1"], self.state()["delivery"]["slices"]["planned"])



def load_tests(loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: str | None) -> unittest.TestSuite:
    suite = unittest.TestSuite()
    for name in (
        "test_reopen_to_tasks_after_slice_manifest_refusal_redispatches_tasks",
        "test_transition_to_tasks_cannot_bypass_completed_slice_guard",
        "test_decision_doc_implement_cannot_reopen_to_tasks_by_either_command",
        "test_next_stops_forward_budget_deficit_with_the_exact_raise_command",
        "test_review_reopen_uses_only_the_pending_fix_slice_for_budget",
    ):
        suite.addTest(ReopenToTasksTests(name))
    return suite


if __name__ == "__main__":
    unittest.main()
