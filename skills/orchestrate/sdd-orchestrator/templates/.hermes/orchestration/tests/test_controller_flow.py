"""Controller-flow fixes: planner recovery enum, IDLE guidance, budget resets, driver next steps,
DECISION_DOC profile, prompt ceiling and context-graph tolerance of installed skills."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ORCHESTRATION = Path(__file__).resolve().parents[1]
RUNTIME = ORCHESTRATION / "runtime"
TEMPLATES = ORCHESTRATION.parents[1]
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bounded_loop_driver  # noqa: E402
import bounded_run_driver as driver  # noqa: E402
import bounded_run_planner as planner  # noqa: E402
import context_graph  # noqa: E402
import state_format  # noqa: E402
import stage_context  # noqa: E402
from test_bounded_run_planner import snapshot as planner_snapshot  # noqa: E402


class PlannerRecoveryTests(unittest.TestCase):
    def test_archive_interrupted_required_is_a_known_recovery_decision(self) -> None:
        value = planner_snapshot()
        value["recovery"].update({"decision": "ARCHIVE_INTERRUPTED_REQUIRED", "journal_status": "PROCESS_FINISHED"})
        plan = planner.create_plan(value)
        self.assertEqual(["RECOVER_PENDING_ACTION"], [entry["action"] for entry in plan["actions"]])
        self.assertEqual("RECOVERY_RECONCILIATION_REQUIRED", plan["termination"]["expected_reason"])

    def test_idle_snapshot_is_refused_with_a_start_command(self) -> None:
        value = planner_snapshot()
        value["state"]["stage"] = "IDLE"
        with self.assertRaises(planner.PlannerError) as caught:
            planner.create_plan(value)
        self.assertIn("IDLE_NO_DEMAND", str(caught.exception))
        self.assertIn("sdd.py start", str(caught.exception))

    def test_cli_errors_carry_next_step_and_next_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "snapshot.json"
            value = planner_snapshot()
            value["state"]["stage"] = "IDLE"
            path.write_text(json.dumps(value), encoding="utf-8")
            completed = subprocess.run([sys.executable, str(RUNTIME / "bounded_run_planner.py"), "plan", "--snapshot", str(path),
                                        "--target", "NEXT_HUMAN_CHECKPOINT", "--json"], capture_output=True, text=True, check=False)
        result = json.loads(completed.stdout)
        self.assertEqual(2, completed.returncode)
        self.assertTrue(result["next_step"])
        self.assertIn("sdd.py", result["next_command"])


class BudgetResetTests(unittest.TestCase):
    def test_resets_are_explicit(self) -> None:
        budgets = {"corrective_retries": {"max_per_action": 1, "used_current_action": 1},
                   "investigation_expansions": {"max_per_stage": 1, "used_current_stage": 1},
                   "executor_calls": {"max": 8, "used": 3}}
        after_rollover = planner.reset_budgets(budgets, "ROLLOVER")
        self.assertEqual(0, after_rollover["corrective_retries"]["used_current_action"])
        self.assertEqual(1, after_rollover["investigation_expansions"]["used_current_stage"])
        self.assertEqual(3, after_rollover["executor_calls"]["used"])
        after_transition = planner.reset_budgets(budgets, "STAGE_TRANSITION")
        self.assertEqual(0, after_transition["investigation_expansions"]["used_current_stage"])
        self.assertEqual(0, after_transition["corrective_retries"]["used_current_action"])
        self.assertEqual(1, budgets["corrective_retries"]["used_current_action"], "input is not mutated")
        with self.assertRaises(planner.PlannerError):
            planner.reset_budgets(budgets, "NEW_ROUND")


class DriverNextStepTests(unittest.TestCase):
    def test_every_stop_carries_next_step_and_next_command(self) -> None:
        from test_bounded_run_driver import runtime, snapshot  # type: ignore

        value = snapshot()
        value["loop"].update({"mode": "MANUAL", "loop_active": False})
        plan = planner.create_plan(value)
        value["runtime"] = runtime(value, plan)
        result = driver.evaluate_next(value, plan)
        self.assertEqual("MANUAL_ACTION_COMPLETE", result["stop_reason"])
        self.assertTrue(result["next_step"])
        self.assertIn("sdd.py", result["next_command"] or "")

    def test_budget_stop_reasons_are_singular(self) -> None:
        self.assertEqual("EXECUTOR_CALL_BUDGET_REACHED", driver.BUDGET_STOP_REASONS["executor_calls"])
        self.assertEqual("TDD_SLICE_BUDGET_REACHED", driver.BUDGET_STOP_REASONS["tdd_slices"])
        self.assertEqual("RETRY_BUDGET_REACHED", driver.BUDGET_STOP_REASONS["corrective_retries"])

    def test_legacy_loop_driver_is_deprecated(self) -> None:
        self.assertTrue(bounded_loop_driver.DEPRECATED)
        self.assertIn("bounded_run_driver", bounded_loop_driver.__doc__)


class DecisionDocProfileTests(unittest.TestCase):
    def state(self) -> dict:
        return {"schema_version": 2, "stage": {"current": "PLAN", "status": "RUNNING", "completed": ["SPECIFY", "CLARIFY"], "skipped": []}}

    def test_decision_doc_skips_tasks_and_test_with_reason(self) -> None:
        moved = state_format.apply_transition(self.state(), "IMPLEMENT", profile="DECISION_DOC", provenance={"action_id": "a"})
        self.assertEqual("IMPLEMENT", moved["stage"]["current"])
        self.assertEqual([{"stage": "TASKS", "reason": "not part of the DECISION_DOC delivery profile"}], moved["stage"]["skipped"])
        self.assertEqual({"action_id": "a"}, moved["stage_provenance"]["PLAN"])
        reviewed = state_format.apply_transition(moved, "REVIEW", profile="DECISION_DOC")
        self.assertIn("TEST", state_format.skipped_stage_names(reviewed))

    def test_code_profile_never_skips_tasks(self) -> None:
        with self.assertRaises(state_format.StateFormatError):
            state_format.apply_transition(self.state(), "IMPLEMENT", profile="CODE")

    def test_only_clarify_may_be_skipped_with_a_reason(self) -> None:
        start = {"schema_version": 2, "stage": {"current": "SPECIFY", "status": "RUNNING", "completed": [], "skipped": []}}
        with self.assertRaises(state_format.StateFormatError):
            state_format.apply_transition(start, "PLAN", profile="CODE")
        moved = state_format.apply_transition(start, "PLAN", profile="CODE", clarify_skip_reason="no material question")
        self.assertEqual([{"stage": "CLARIFY", "reason": "no material question"}], moved["stage"]["skipped"])

    def test_reopen_needs_a_reason(self) -> None:
        review = {"schema_version": 2, "stage": {"current": "REVIEW", "status": "RUNNING", "completed": ["IMPLEMENT", "TEST"], "skipped": []}}
        with self.assertRaises(state_format.StateFormatError):
            state_format.apply_transition(review, "IMPLEMENT", profile="CODE")
        reopened = state_format.apply_transition(review, "IMPLEMENT", profile="CODE", reopen_reason="review findings")
        self.assertEqual("IMPLEMENT", reopened["stage"]["current"])
        self.assertNotIn("TEST", reopened["stage"]["completed"])

    def test_planner_accepts_a_decision_doc_snapshot(self) -> None:
        value = planner_snapshot()
        value["state"]["stage"] = "PLAN"
        value["state"]["skipped"] = ["TASKS", "TEST"]
        plan = planner.create_plan(value)
        self.assertTrue(plan["actions"])

    def test_dump_round_trips_and_appends_a_log_line(self) -> None:
        text = "# STATE\n\n```yaml\nschema_version: 2\nstage:\n  current: IDLE\n```\n\n## Execution Log\n\n- init\n"
        data = state_format.parse(text)
        data["stage"]["current"] = "SPECIFY"
        out = state_format.dump(text, data, log="started")
        self.assertEqual(data, state_format.parse(out))
        self.assertTrue(out.rstrip().endswith("- started"))


class PromptCeilingTests(unittest.TestCase):
    def test_prompt_larger_than_max_prompt_bytes_is_refused_with_next_step(self) -> None:
        from test_stage_context import context  # type: ignore

        value = context("SPECIFY")
        value["limits"]["max_prompt_bytes"] = 2048
        result = stage_context.check(value, prompt_bytes=4096)
        self.assertFalse(result["valid"])
        self.assertEqual("PROMPT_TOO_LARGE", result["errors"][0]["code"])
        self.assertIn("sdd.py prepare", result["next_step"])
        self.assertTrue(stage_context.check(value, prompt_bytes=1024)["valid"])

    def test_default_ceiling_applies_without_the_limit(self) -> None:
        from test_stage_context import context  # type: ignore

        value = context("SPECIFY")
        self.assertFalse(stage_context.check(value, prompt_bytes=stage_context.DEFAULT_MAX_PROMPT_BYTES + 1)["valid"])


class NotApplicableGateTests(unittest.TestCase):
    def test_a_confirmed_not_applicable_gate_satisfies_done(self) -> None:
        gates = {"focused_tests": "PASS", "format": "NOT_APPLICABLE", "analyze": "PASS", "review": "APPROVED",
                 "ci": "DISABLED_BY_PROJECT_POLICY", "project_ci_enabled": False}
        self.assertTrue(planner.gate_passed(gates, "format"))
        self.assertIsNone(planner.gate_precondition_error("EVALUATE_DONE", gates))
        self.assertTrue(driver._done_gates_pass({"gates": gates}))
        gates["format"] = "PENDING"
        self.assertEqual("DONE_GATES_NOT_PASSED", planner.gate_precondition_error("EVALUATE_DONE", gates))


class DispatchQuestionTests(unittest.TestCase):
    def test_a_sub_agent_dispatch_needs_the_dispatch_question(self) -> None:
        from test_stage_context import context  # type: ignore

        value = context("PLAN")
        value["project_context"].update({"status": "MISSING", "checked_head": None, "evidence": None, "gaps": []})
        missing = stage_context.check(value, role="PROJECT_CONTEXT_GUARDIAN")
        self.assertIn("DISPATCH_QUESTION_REQUIRED", [item["code"] for item in missing["errors"]])
        value["dispatch"] = {"pending_decision": "d", "deterministic_attempt": "git", "if_empty": "proceed"}
        self.assertNotIn("DISPATCH_QUESTION_REQUIRED", [item["code"] for item in stage_context.check(value, role="PROJECT_CONTEXT_GUARDIAN")["errors"]])


class ContextGraphInstalledSkillsTests(unittest.TestCase):
    def test_template_skills_with_nested_frontmatter_never_become_findings(self) -> None:
        notes = [(str(path.relative_to(TEMPLATES)), path.read_text(encoding="utf-8"))
                 for path in sorted((TEMPLATES / ".hermes" / "skills").rglob("SKILL.md"))]
        self.assertTrue(notes)
        graph = context_graph.build(notes)
        self.assertEqual([], graph["findings"])

    def test_nested_frontmatter_of_a_non_graph_note_is_ignored_but_graph_notes_stay_strict(self) -> None:
        nested = "---\nname: x\nmetadata:\n  hermes:\n    tags: [a]\n---\nbody\n"
        self.assertEqual([], context_graph.build([("skill.md", nested)])["findings"])
        strict = "---\ngraph_node: x\ngraph_kind: MODULE\nmetadata:\n  hermes: y\n---\n"
        self.assertEqual("GRAPH_FRONTMATTER_INVALID", context_graph.build([("node.md", strict)])["findings"][0]["code"])

    def test_vault_scan_skips_the_controller_tree(self) -> None:
        self.assertIn(".hermes", context_graph.SKIPPED_NOTE_ROOTS)
        self.assertTrue(context_graph.skipped_note(".hermes/skills/sdd-tdd/SKILL.md"))
        self.assertFalse(context_graph.skipped_note("concepts/auth.md"))


if __name__ == "__main__":
    unittest.main()
