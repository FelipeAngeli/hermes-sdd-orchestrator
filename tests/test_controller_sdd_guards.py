"""sdd.py subcommands that guard human decisions and STATE, beyond the end-to-end happy path."""
from __future__ import annotations

import json
import shlex
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_controller_e2e as e2e  # noqa: E402

run = e2e.run


class SddGuardTests(e2e.ControllerEndToEndTests):
    """Reuses the installed fixture (real installer, fake vault, fake executors)."""

    test_a_demand_reaches_done_only_through_printed_commands = None  # type: ignore[assignment]
    test_snapshot_is_generated_and_accepted_by_the_planner = None  # type: ignore[assignment]
    test_decision_doc_profile_skips_tasks_and_test = None  # type: ignore[assignment]

    def call(self, *arguments: str) -> tuple[int, dict]:
        completed = run([*self.sdd, *arguments], cwd=self.repo, env=self.env)
        return completed.returncode, json.loads(completed.stdout)

    def test_status_is_compact_and_always_names_the_next_command(self) -> None:
        completed = run([*self.sdd, "status"], cwd=self.repo, env=self.env)
        status = json.loads(completed.stdout)
        self.assertLess(len(completed.stdout), 2048)
        for key in ("stage", "status", "mode", "ticket", "recovery", "budgets_left", "next_action", "next_command", "next_step", "paths"):
            self.assertIn(key, status)

    def test_every_error_carries_next_step_and_next_command(self) -> None:
        code, result = self.call("transition", "--to", "PLAN", "--artifact", "/nonexistent")
        self.assertNotEqual(0, code)
        self.assertIn("next_step", result)
        self.assertIn("next_command", result)

    def test_start_refuses_a_second_demand(self) -> None:
        self.call("start", "--ticket", "a-1", "--title", "t", "--objective", "o")
        code, result = self.call("start", "--ticket", "a-2", "--title", "t", "--objective", "o")
        self.assertNotEqual(0, code)
        self.assertIn("sdd.py", result["next_command"])

    def test_waive_needs_a_known_human_check_and_an_allowed_approver(self) -> None:
        self.call("start", "--ticket", "w-1", "--title", "t", "--objective", "o")
        code, result = self.call("waive", "--check", "AC-9", "--by", "requester", "--quote", "ok", "--reason", "r")
        self.assertNotEqual(0, code)
        self.assertTrue(result["next_step"])
        code, _ = self.call("waive", "--check", "AC-9", "--by", "someone-else", "--quote", "ok", "--reason", "r")
        self.assertNotEqual(0, code)

    def test_transition_cannot_skip_stages(self) -> None:
        self.call("start", "--ticket", "s-1", "--title", "t", "--objective", "o")
        code, result = self.call("transition", "--to", "IMPLEMENT", "--artifact", str(self.repo / "AGENTS.md"))
        self.assertNotEqual(0, code)
        self.assertIn("next_command", result)

    def test_manifest_carries_hashes_and_the_prompt_ceiling(self) -> None:
        self.call("start", "--ticket", "m-1", "--title", "t", "--objective", "o")
        output = Path(self.temp.name) / "m.json"
        code, result = self.call("manifest", "--stage", "SPECIFY", "--output", str(output))
        self.assertEqual(0, code, result)
        manifest = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(49152, manifest["limits"]["max_prompt_bytes"])
        self.assertTrue(all(len(source["sha256"]) == 64 for source in manifest["sources"]))
        checked = run(shlex.split(result["next_command"]), cwd=self.repo, env=self.env)
        self.assertEqual(0, checked.returncode, checked.stdout)


if __name__ == "__main__":
    unittest.main()
