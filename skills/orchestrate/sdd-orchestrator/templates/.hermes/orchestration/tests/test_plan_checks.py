"""Plan checks: editable-path normalization at TASKS and budget-vs-plan sizing."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import sdd  # noqa: E402
import validate_protocol  # noqa: E402
from test_protocol import executor_fixture  # noqa: E402


class NormalizationTests(unittest.TestCase):
    def test_normalize_strips_one_trailing_slash_and_appends_glob(self) -> None:
        import plan_checks

        self.assertEqual("tasks/x/**", plan_checks.normalize_editable_path("tasks/x/"))
        self.assertEqual("tasks/x", plan_checks.normalize_editable_path("tasks/x"))
        self.assertEqual("src/a.ts", plan_checks.normalize_editable_path("src/a.ts"))
        self.assertEqual("src/**", plan_checks.normalize_editable_path("src/**"))
        self.assertEqual("tasks/x//**", plan_checks.normalize_editable_path("tasks/x//"))

    def test_normalization_records_from_and_to(self) -> None:
        import plan_checks

        normalized, records = plan_checks.normalize_editable_paths(["tasks/x/", "docs/a.md"])
        self.assertEqual(["docs/a.md", "tasks/x/**"], normalized)
        self.assertEqual([
            {"from": "tasks/x/", "to": "tasks/x/**"},
        ], records)


class TasksImpactFileValidationTests(unittest.TestCase):
    def _errors(self, impact_files: list[str]) -> list[dict]:
        fixture = executor_fixture("TASKS")
        fixture["executor_result"]["stage_payload"]["impact_files"] = impact_files
        return validate_protocol.validate_payload("TASKS", fixture)

    def test_tasks_trailing_slash_entry_validates(self) -> None:
        self.assertEqual([], self._errors(["tasks/x/"]))

    def test_tasks_plain_file_entry_is_returned_unchanged(self) -> None:
        self.assertEqual([], self._errors(["src/a.ts"]))

    def test_tasks_existing_glob_is_returned_unchanged(self) -> None:
        self.assertEqual([], self._errors(["src/**"]))

    def test_tasks_unsafe_entries_are_refused(self) -> None:
        for entry in ("../x", "/abs", "**", "a//b", "tasks/x//"):
            with self.subTest(entry=entry):
                errors = self._errors([entry])
                self.assertTrue(any("is not a safe editable path pattern" in item["reason"] for item in errors), errors)


class PlanImpactFileValidationTests(unittest.TestCase):
    def _errors(self, impact_files: list[str], *, validate_impact_files: bool) -> list[dict]:
        fixture = executor_fixture("PLAN")
        fixture["executor_result"]["stage_payload"]["impact_files"] = impact_files
        return validate_protocol.validate_payload("PLAN", fixture, validate_impact_files=validate_impact_files)

    def test_decision_doc_plan_unsafe_impact_files_are_refused_by_validation(self) -> None:
        errors = self._errors(["../x"], validate_impact_files=True)
        self.assertTrue(any("is not a safe editable path pattern" in item["reason"] for item in errors), errors)

    def test_code_plan_impact_files_are_not_checked_without_the_flag(self) -> None:
        self.assertEqual([], self._errors(["../x"], validate_impact_files=False))


class FoldNormalizationTests(unittest.TestCase):
    def test_code_plan_fold_preserves_acceptance_slice_ids(self) -> None:
        import acceptance_verifiers

        delivery = {"acceptance": {
            "AC-01": {"slice_id": "S1"},
            "AC-02": {"slice_id": "S2"},
        }}
        acceptance_verifiers.fold_plan_tasks(
            delivery, stage="PLAN", profile="CODE", impact_files=[],
            approve_slices=lambda planned: planned,
        )
        self.assertEqual(["S1", "S2"], [check["slice_id"] for check in delivery["acceptance"].values()])

    def test_decision_doc_plan_fold_uses_doc_slice(self) -> None:
        import acceptance_verifiers

        delivery = {"acceptance": {"AC-01": {"slice_id": "S1"}}}
        acceptance_verifiers.fold_plan_tasks(
            delivery, stage="PLAN", profile="DECISION_DOC", impact_files=["docs/a.md"],
            approve_slices=lambda planned: planned,
        )
        self.assertEqual("DOC", delivery["acceptance"]["AC-01"]["slice_id"])

    def test_tasks_fold_normalizes_and_records(self) -> None:
        result = {"executor_result": {
            "schema_version": 3,
            "stage": {"value": "TASKS", "status": "SUCCESS"},
            "executor": {"name": "CODEX", "invocation_type": "EXTERNAL_CLI"},
            "consulted_paths": [], "modified_paths": [], "created_paths": [], "validated_symbols": [],
            "commands": [], "blockers": [],
            "context_assessment": {"facts": [], "assumptions": [], "unresolved_questions": []},
            "acceptance_checks": [{"id": "AC-1", "criterion": "c", "verification_method": "m", "verifier": "AGENT", "slice_id": "S1", "status": "PLANNED", "evidence": None, "waiver": None}],
            "stage_payload": {"summary": "s", "tasks": [], "impact_files": ["tasks/x/"], "decisions": []},
            "tdd_slices": [], "next_step": {"stage": "IMPLEMENT", "action": "continue"},
        }}
        data = {"delivery": {"profile": "CODE"}}
        action = {"stage": "TASKS", "role": None, "id": "a", "final": "f"}
        with mock.patch.object(sdd, "slice_hashes", return_value=["h"]):
            sdd.apply_result(ctx=object(), data=data, action=action, result=result, digest="d")
        self.assertEqual(["tasks/x/**"], data["delivery"]["slices"]["editable_paths"])
        self.assertEqual([{"from": "tasks/x/", "to": "tasks/x/**"}], data["delivery"]["editable_path_normalizations"])


if __name__ == "__main__":
    unittest.main()
