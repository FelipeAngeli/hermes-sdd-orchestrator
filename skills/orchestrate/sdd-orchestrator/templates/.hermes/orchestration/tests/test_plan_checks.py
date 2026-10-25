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


def tasks_result(slice_ids: list[str], check_slices: dict[str, str]) -> dict:
    """A TASKS executor_result whose checks are listed in their own (string-sorted) order."""
    return {
        "schema_version": 3,
        "stage": {"value": "TASKS", "status": "SUCCESS"},
        "executor": {"name": "CODEX", "invocation_type": "EXTERNAL_CLI"},
        "consulted_paths": [], "modified_paths": [], "created_paths": [], "validated_symbols": [],
        "commands": [], "blockers": [],
        "context_assessment": {"facts": [], "assumptions": [], "unresolved_questions": []},
        "acceptance_checks": [
            {"id": check, "criterion": "c", "verification_method": "m", "verifier": "AGENT",
             "slice_id": slice_id, "status": "PLANNED", "evidence": None, "waiver": None}
            for check, slice_id in check_slices.items()
        ],
        "stage_payload": {"summary": "s", "tasks": [], "impact_files": ["poc/"], "decisions": []},
        "tdd_slices": [
            {"id": slice_id, "objective": "o", "test_file": "t.py", "red_command": "r", "red_exit_code": 1,
             "expected_failure": "e", "red_failure_kind": "EXPECTED_FUNCTIONAL", "minimal_implementation": "m",
             "green_command": "r", "green_exit_code": 0, "green_result": "g"}
            for slice_id in slice_ids
        ],
        "next_step": {"stage": "IMPLEMENT", "action": "continue"},
    }


class TasksSliceOrderTests(unittest.TestCase):
    """IMPLEMENT runs slices in the order TASKS declared in `tdd_slices`, never in acceptance-check order."""

    def test_fold_uses_tdd_slices_order_not_acceptance_order(self) -> None:
        import acceptance_verifiers

        # Checks sorted as strings (AC1, AC10, AC2...) put the last slice first.
        result = tasks_result(["S01", "S02", "S03"], {"AC1": "S03", "AC10": "S03", "AC2": "S01", "AC3": "S02"})
        delivery = {"acceptance": {item["id"]: {"slice_id": item["slice_id"]} for item in result["acceptance_checks"]}}
        acceptance_verifiers.fold_plan_tasks(
            delivery, stage="TASKS", profile="CODE", impact_files=[],
            slice_order=[item["id"] for item in result["tdd_slices"]],
            approve_slices=lambda planned: planned,
        )
        self.assertEqual(["S01", "S02", "S03"], delivery["slices"]["planned"])
        self.assertEqual(["S01", "S02", "S03"], delivery["approved_slice_sha256s"])

    def test_validation_refuses_a_tdd_slice_without_an_acceptance_check(self) -> None:
        result = tasks_result(["S01", "S02"], {"AC1": "S01"})
        errors = validate_protocol.validate_payload("TASKS", {"executor_result": result})
        self.assertTrue(any("S02" in item["reason"] and "no acceptance check" in item["reason"] for item in errors), errors)

    def test_validation_refuses_a_check_assigned_to_an_undeclared_slice(self) -> None:
        result = tasks_result(["S01"], {"AC1": "S01", "AC11": "S00"})
        errors = validate_protocol.validate_payload("TASKS", {"executor_result": result})
        self.assertTrue(any("S00" in item["reason"] and "not declared in tdd_slices" in item["reason"] for item in errors), errors)

    def test_validation_accepts_checks_that_cover_exactly_the_declared_slices(self) -> None:
        result = tasks_result(["S01", "S02"], {"AC2": "S02", "AC1": "S01"})
        self.assertEqual([], validate_protocol.validate_payload("TASKS", {"executor_result": result}))

    def test_apply_result_folds_tasks_slices_in_declared_order(self) -> None:
        # The APPPEEK-E3 shape: checks string-sorted, the last slice listed first.
        result = {"executor_result": tasks_result(
            ["S01", "S02", "S03"], {"AC1": "S03", "AC10": "S03", "AC2": "S01", "AC3": "S02"})}
        data = {"delivery": {"profile": "CODE"}}
        action = {"stage": "TASKS", "role": None, "id": "a", "final": "f"}
        with mock.patch.object(sdd, "slice_hashes", side_effect=lambda ctx, data, planned: planned):
            sdd.apply_result(ctx=object(), data=data, action=action, result=result, digest="d")
        self.assertEqual(["S01", "S02", "S03"], data["delivery"]["slices"]["planned"])

    def test_without_declared_tdd_slices_the_acceptance_order_is_kept(self) -> None:
        import acceptance_verifiers

        result = tasks_result([], {"AC-1": "S2", "AC-2": "S1"})
        delivery = {"acceptance": {item["id"]: {"slice_id": item["slice_id"]} for item in result["acceptance_checks"]}}
        acceptance_verifiers.fold_plan_tasks(
            delivery, stage="TASKS", profile="CODE", impact_files=[],
            slice_order=[item["id"] for item in result["tdd_slices"]],
            approve_slices=lambda planned: planned,
        )
        self.assertEqual(["S2", "S1"], delivery["slices"]["planned"])
        self.assertEqual([], validate_protocol.validate_payload("TASKS", {"executor_result": result}))


if __name__ == "__main__":
    unittest.main()
