from __future__ import annotations

import sys
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import prompt_rules  # noqa: E402


class RetryRecipeTests(unittest.TestCase):
    def context(self) -> dict:
        return {
            "check_verifiers": {"AC-1": ["pytest tests/feature -q", "ruff check src/feature"]},
            "current_slice_ids": ["S1"],
            "completed_slice_ids": [],
            "editable_paths": ["src/feature/*"],
            "required_commands": ["pytest tests/feature -q"],
        }

    def test_evidence_citation_recipe_is_concrete(self) -> None:
        recipe = prompt_rules.retry_recipe([
            {"path": "$.executor_result.acceptance_checks[0].evidence",
             "reason": "AGENT PASS evidence for AC-1 must cite, in backticks, one of its bound verifier commands recorded as exit 0 with PASS"},
        ], self.context())
        self.assertIn("Rewrite AC-1 evidence citing literally, in backticks, `pytest tests/feature -q`", recipe[0])
        self.assertIn("record that command in commands[] with exit_code 0 and result PASS", recipe[0])

    def test_required_verification_recipe_is_concrete(self) -> None:
        recipe = prompt_rules.retry_recipe([
            {"path": "$.executor_result.commands", "reason": "required verification `pytest tests/feature -q` was not recorded as passing"},
        ], self.context())
        self.assertIn("Run `pytest tests/feature -q` and record it in commands[] with exit 0/PASS", recipe[0])

    def test_success_blockers_recipe(self) -> None:
        recipe = prompt_rules.retry_recipe([
            {"path": "$.executor_result.blockers", "reason": "SUCCESS cannot include blockers"},
        ], self.context())
        self.assertEqual(["Either fix and empty blockers, or set stage.status to BLOCKED."], recipe)

    def test_tdd_slice_count_and_ids_recipes(self) -> None:
        for reason in ("IMPLEMENT SUCCESS requires at least one TDD slice", "reported TDD slice ids must exactly match current_slice_ids"):
            recipe = prompt_rules.retry_recipe([{"path": "$.executor_result.tdd_slices", "reason": reason}], self.context())
            self.assertIn("tdd_slices must be exactly [S1]", recipe[0])

    def test_red_recipes_share_one_instruction(self) -> None:
        for reason in ("accepted RED requires a non-zero exit code", "accepted RED requires an expected functional failure",
                       "infrastructure failure is not valid RED evidence"):
            recipe = prompt_rules.retry_recipe([{"path": "$.executor_result.tdd_slices[0].red_exit_code", "reason": reason}], self.context())
            self.assertIn("set red_failure_kind EXPECTED_FUNCTIONAL", recipe[0])

    def test_green_recipe(self) -> None:
        recipe = prompt_rules.retry_recipe([
            {"path": "$.executor_result.tdd_slices[0].green_exit_code", "reason": "accepted GREEN requires exit code 0"},
        ], self.context())
        self.assertIn("exit_code 0", recipe[0])

    def test_write_scope_recipe_is_concrete(self) -> None:
        recipe = prompt_rules.retry_recipe([
            {"path": "$.executor_result.modified_paths[0]", "reason": "src/other.py is outside the slice editable_paths"},
        ], self.context())
        self.assertIn("Remove changes to src/other.py", recipe[0])
        self.assertIn("allowed: src/feature/*", recipe[0])

    def test_future_and_current_slice_recipes(self) -> None:
        for reason in ("future slice checks must remain PLANNED", "future slice checks cannot carry evidence"):
            recipe = prompt_rules.retry_recipe([{"path": "$.executor_result.acceptance_checks[0].status", "reason": reason}], self.context())
            self.assertIn("PLANNED", recipe[0])
        for reason in ("current and completed slice checks require PASS or WAIVED", "current and completed slice checks require evidence"):
            recipe = prompt_rules.retry_recipe([{"path": "$.executor_result.acceptance_checks[0].status", "reason": reason}], self.context())
            self.assertIn("PASS or WAIVED", recipe[0])

    def test_only_json_recipe_is_limited_to_parse_and_transport_errors(self) -> None:
        expected = ["Your final message must be only the JSON object, no prose, no code fence."]
        for error in ({"path": "$", "reason": "OUTPUT_INVALID"},
                      {"path": "$", "reason": "invalid JSON final message: Expecting value"},
                      {"path": "$", "reason": "Search output is not valid JSON."},
                      {"path": "$", "reason": "expected executor_result envelope for IMPLEMENT"},
                      {"path": "$", "reason": "final message must be a JSON object"}):
            recipe = prompt_rules.retry_recipe([error], self.context())
            self.assertEqual(expected, recipe)

    def test_expected_version_recipe_is_concrete(self) -> None:
        recipe = prompt_rules.retry_recipe([{"path": "$.executor_result.schema_version", "reason": "expected version 3"}], self.context())
        self.assertEqual(["Set executor_result.schema_version to 3."], recipe)

    def test_stage_value_recipe_is_concrete(self) -> None:
        recipe = prompt_rules.retry_recipe([{"path": "$.executor_result.stage.value", "reason": "must equal requested action IMPLEMENT"}], self.context())
        self.assertEqual(["Set executor_result.stage.value to IMPLEMENT."], recipe)

    def test_controller_data_recipes_return_blocked(self) -> None:
        expected = ["This is a controller-data problem you cannot fix: return stage.status BLOCKED with a blocker quoting this error."]
        for path, reason in (("$", "editable_paths patterns are unsafe or match everything: ['*']"),
                             ("$", "role SECURITY_REVIEWER is not a read-only role allowed in IMPLEMENT"),
                             ("$.executor_result.acceptance_checks[0].evidence",
                              "AGENT PASS requires controller-owned check_verifiers")):
            recipe = prompt_rules.retry_recipe([{"path": path, "reason": reason}], self.context())
            self.assertEqual(expected, recipe)

    def test_schema_enum_error_is_not_only_json(self) -> None:
        reason = "'IMPLEMENT' is not one of ['SPECIFY', 'CLARIFY', 'PLAN', 'TASKS', 'IMPLEMENT', 'TEST']"
        recipe = prompt_rules.retry_recipe([{"path": "$.executor_result.stage.value", "reason": reason}], self.context())
        self.assertEqual([f"Fix: {reason} at $.executor_result.stage.value."], recipe)

    def test_unknown_reason_is_generic(self) -> None:
        recipe = prompt_rules.retry_recipe([{"path": "$.executor_result.stage.value", "reason": "some future validator reason"}], self.context())
        self.assertEqual(["Fix: some future validator reason at $.executor_result.stage.value."], recipe)

    def test_recipe_is_deduplicated_in_error_order(self) -> None:
        recipe = prompt_rules.retry_recipe([
            {"path": "$.executor_result.blockers", "reason": "SUCCESS cannot include blockers"},
            {"path": "$.executor_result.tdd_slices", "reason": "IMPLEMENT SUCCESS requires at least one TDD slice"},
            {"path": "$.executor_result.blockers", "reason": "SUCCESS cannot include blockers"},
        ], self.context())
        self.assertEqual(["Either fix and empty blockers, or set stage.status to BLOCKED.", "tdd_slices must be exactly [S1]; each needs red_exit_code != 0, red_failure_kind EXPECTED_FUNCTIONAL, non-empty expected_failure, green_command and green_exit_code 0."], recipe)


if __name__ == "__main__":
    unittest.main()
