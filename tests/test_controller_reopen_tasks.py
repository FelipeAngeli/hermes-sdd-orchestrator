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



def load_tests(loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: str | None) -> unittest.TestSuite:
    suite = unittest.TestSuite()
    suite.addTest(ReopenToTasksTests("test_reopen_to_tasks_after_slice_manifest_refusal_redispatches_tasks"))
    return suite


if __name__ == "__main__":
    unittest.main()
