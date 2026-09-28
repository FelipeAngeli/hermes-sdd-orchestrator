"""Synthetic protocol fixtures only; these are not product-execution evidence."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema.exceptions import SchemaError

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))
import validate_protocol  # noqa: E402
from validate_protocol import validate_json_text, validate_payload  # noqa: E402


def executor_fixture(stage: str, status: str = "SUCCESS") -> dict:
    return {
        "executor_result": {
            "schema_version": 3,
            "stage": {"value": stage, "status": status},
            "executor": {"name": "CODEX", "invocation_type": "EXTERNAL_CLI"},
            "consulted_paths": [{"path": "src/example.ext"}],
            "modified_paths": [],
            "created_paths": [],
            "validated_symbols": [{"symbol": "example", "path": "src/example.ext", "exists": True}],
            "commands": [],
            "blockers": [],
            "context_assessment": {
                "facts": [{"statement": "synthetic fact", "evidence": "synthetic evidence"}],
                "assumptions": [],
                "unresolved_questions": [],
            },
            "acceptance_checks": [{
                "id": "AC-1",
                "criterion": "synthetic outcome is verified",
                "verification_method": "run the synthetic focused check",
                "verifier": "AGENT",
                "slice_id": "synthetic-slice-1" if stage in {"TASKS", "IMPLEMENT", "TEST"} else None,
                "status": "PASS" if stage in {"IMPLEMENT", "TEST"} else "PLANNED",
                "evidence": "synthetic passing evidence" if stage in {"IMPLEMENT", "TEST"} else None,
            }],
            "stage_payload": {"summary": "synthetic test fixture", "tasks": [], "impact_files": [], "decisions": []},
            "tdd_slices": [],
            "next_step": {"stage": "PLAN", "action": "synthetic next action"},
        }
    }


def successful_implementation() -> dict:
    fixture = executor_fixture("IMPLEMENT")
    fixture["executor_result"]["tdd_slices"] = [{
        "id": "synthetic-slice-1",
        "objective": "validate synthetic protocol behavior",
        "test_file": "tests/synthetic_protocol_test.ext",
        "red_command": "synthetic red command",
        "red_exit_code": 1,
        "expected_failure": "expected functional assertion failure",
        "red_failure_kind": "EXPECTED_FUNCTIONAL",
        "minimal_implementation": "synthetic minimal implementation",
        "green_command": "synthetic green command",
        "green_exit_code": 0,
        "green_result": "synthetic green result",
    }]
    return fixture


def review_fixture(status: str = "APPROVED") -> dict:
    return {
        "review_result": {
            "schema_version": 3,
            "status": status,
            "reviewed_paths": [{"path": "src/example.ext"}],
            "findings": [] if status == "APPROVED" else [{"severity": "medium", "path": "src/example.ext", "description": "synthetic finding", "evidence": "synthetic evidence"}],
            "baseline": {"preserved": True, "violations": []},
            "ownership": {"valid": True, "violations": []},
            "acceptance": {
                "verified": status == "APPROVED",
                "expected_check_ids": ["AC-1"],
                "checks": [{
                    "id": "AC-1",
                    "criterion": "synthetic outcome is verified",
                    "verification_method": "run the synthetic focused check",
                    "verifier": "AGENT",
                    "slice_id": "synthetic-slice-1",
                    "status": "PASS" if status == "APPROVED" else "FAIL",
                    "evidence": "synthetic review evidence",
                }],
            },
            "e2e": {"files_modified": False, "execution_performed": False, "violation": False},
            "forbidden_actions": {"violations": []},
            "gate_status": {"focused_tests": "PASS", "format": "PASS", "analyze": "PASS", "ci": "DISABLED_BY_PROJECT_POLICY"},
            "next_step": {"action": "EVALUATE_DONE_WITH_CI_DISABLED"},
        }
    }


class ProtocolValidationTests(unittest.TestCase):
    def _executor_schema(self, cache_id: int = 0, cache_namespace: str = "") -> dict:
        return {
            "$id": f"urn:protocol-cache:{cache_namespace}:{cache_id}",
            "type": "object",
            "required": ["executor_result"],
        }

    def _validate_with_schema(self, schema_path: Path, payload: dict) -> list[dict[str, str]]:
        with patch.object(validate_protocol, "_schema_for", return_value=(schema_path, "executor_result")):
            return validate_payload("SPECIFY", payload)

    def _expected_acceptance(self, action: str, payload: dict) -> dict[str, dict[str, str | None]] | None:
        if action in {"IMPLEMENT", "TEST"} and "executor_result" in payload:
            return {
                check["id"]: {
                    "criterion": check["criterion"],
                    "verification_method": check["verification_method"],
                    "verifier": check["verifier"],
                    "slice_id": check["slice_id"],
                }
                for check in payload["executor_result"]["acceptance_checks"]
            }
        if action == "REVIEW" and "review_result" in payload:
            return {
                check["id"]: {
                    "criterion": check["criterion"],
                    "verification_method": check["verification_method"],
                    "verifier": check["verifier"],
                    "slice_id": check["slice_id"],
                }
                for check in payload["review_result"]["acceptance"]["checks"]
            }
        return None

    def _current_slice_ids(self, action: str, payload: dict) -> set[str] | None:
        if action == "IMPLEMENT" and "executor_result" in payload:
            return {item["id"] for item in payload["executor_result"]["tdd_slices"]}
        return None

    def assertAccepted(self, action: str, payload: dict) -> None:
        expected = self._expected_acceptance(action, payload)
        current = self._current_slice_ids(action, payload)
        errors = validate_payload(
            action,
            payload,
            expected_acceptance=expected,
            current_slice_ids=current,
            completed_slice_ids=set() if action == "IMPLEMENT" else None,
        )
        self.assertEqual([], errors, msg=errors)

    def assertRejected(self, action: str, payload: object) -> None:
        expected = self._expected_acceptance(action, payload) if isinstance(payload, dict) else None
        current = self._current_slice_ids(action, payload) if isinstance(payload, dict) else None
        self.assertTrue(
            validate_payload(
                action,
                payload,
                expected_acceptance=expected,
                current_slice_ids=current,
                completed_slice_ids=set() if action == "IMPLEMENT" else None,
            ),
            msg="fixture unexpectedly accepted",
        )

    def test_accepts_specify_clarify_plan_tasks_and_test(self) -> None:
        for action in ("SPECIFY", "CLARIFY", "PLAN", "TASKS", "TEST"):
            with self.subTest(action=action):
                fixture = executor_fixture(action)
                fixture["executor_result"]["next_step"]["stage"] = "REVIEW" if action == "TEST" else "PLAN"
                self.assertAccepted(action, fixture)

    def test_accepts_implement_success_with_consistent_tdd(self) -> None:
        self.assertAccepted("IMPLEMENT", successful_implementation())

    def test_accepts_implement_blocked_with_partial_evidence(self) -> None:
        fixture = successful_implementation()
        fixture["executor_result"]["stage"]["status"] = "BLOCKED"
        fixture["executor_result"]["blockers"] = [{"type": "SYNTHETIC", "description": "synthetic blocked state"}]
        fixture["executor_result"]["tdd_slices"][0]["green_command"] = None
        fixture["executor_result"]["tdd_slices"][0]["green_exit_code"] = None
        fixture["executor_result"]["tdd_slices"][0]["green_result"] = None
        self.assertAccepted("IMPLEMENT", fixture)

    def test_accepts_review_approved_with_ci_disabled(self) -> None:
        self.assertAccepted("REVIEW", review_fixture())

    def test_accepts_review_changes_required(self) -> None:
        self.assertAccepted("REVIEW", review_fixture("CHANGES_REQUIRED"))

    def test_rejects_successful_executor_result_with_blockers(self) -> None:
        fixture = executor_fixture("SPECIFY")
        fixture["executor_result"]["blockers"] = [{"type": "SYNTHETIC", "description": "synthetic contradiction"}]

        self.assertRejected("SPECIFY", fixture)

    def test_requires_context_assessment_and_acceptance_checks(self) -> None:
        for field in ("context_assessment", "acceptance_checks"):
            with self.subTest(field=field):
                fixture = executor_fixture("SPECIFY")
                fixture["executor_result"].pop(field)
                self.assertRejected("SPECIFY", fixture)

    def test_rejects_success_with_material_unresolved_context(self) -> None:
        scenarios = (
            ("assumptions", {"statement": "synthetic assumption", "material": True, "validation": None}),
            ("unresolved_questions", {"question": "Which behavior is intended?", "material": True, "impact": "changes the observable outcome"}),
        )
        for field, item in scenarios:
            with self.subTest(field=field):
                fixture = executor_fixture("PLAN")
                fixture["executor_result"]["context_assessment"][field] = [item]
                self.assertRejected("PLAN", fixture)

    def test_accepts_specify_success_that_routes_material_questions_to_clarify(self) -> None:
        fixture = executor_fixture("SPECIFY")
        fixture["executor_result"]["context_assessment"]["unresolved_questions"] = [{
            "question": "Which observable behavior is intended?",
            "material": True,
            "impact": "changes the acceptance outcome",
        }]
        fixture["executor_result"]["next_step"] = {
            "stage": "CLARIFY",
            "action": "resolve the material product question",
        }

        self.assertAccepted("SPECIFY", fixture)

    def test_accepts_planned_checks_before_implementation(self) -> None:
        for action in ("SPECIFY", "CLARIFY", "PLAN", "TASKS"):
            with self.subTest(action=action):
                fixture = executor_fixture(action)
                self.assertEqual("PLANNED", fixture["executor_result"]["acceptance_checks"][0]["status"])
                self.assertAccepted(action, fixture)

    def test_rejects_non_planned_checks_before_implementation(self) -> None:
        for action in ("SPECIFY", "CLARIFY", "PLAN", "TASKS"):
            for stage_status in ("SUCCESS", "BLOCKED", "TIMEOUT", "INVALID"):
                with self.subTest(action=action, stage_status=stage_status):
                    fixture = executor_fixture(action, stage_status)
                    fixture["executor_result"]["acceptance_checks"][0].update({
                        "status": "PASS",
                        "evidence": "premature evidence",
                    })
                    self.assertRejected(action, fixture)

    def test_rejects_successful_tasks_without_assigned_acceptance_checks(self) -> None:
        for mutation in ("empty", "unassigned"):
            with self.subTest(mutation=mutation):
                fixture = executor_fixture("TASKS")
                checks = fixture["executor_result"]["acceptance_checks"]
                if mutation == "empty":
                    checks.clear()
                else:
                    checks[0]["slice_id"] = None
                self.assertRejected("TASKS", fixture)

    def test_rejects_whitespace_only_context_and_acceptance_evidence(self) -> None:
        mutations = (
            lambda result: result["context_assessment"]["facts"][0].update({"statement": " "}),
            lambda result: result["context_assessment"]["facts"][0].update({"evidence": "\t"}),
            lambda result: result["acceptance_checks"][0].update({"criterion": " "}),
            lambda result: result["acceptance_checks"][0].update({"verification_method": "\n"}),
            lambda result: result["acceptance_checks"][0].update({"evidence": " "}),
        )
        for mutate in mutations:
            fixture = executor_fixture("TEST")
            mutate(fixture["executor_result"])
            self.assertRejected("TEST", fixture)

    def test_accepts_implement_success_with_future_slice_checks_still_planned(self) -> None:
        fixture = successful_implementation()
        fixture["executor_result"]["acceptance_checks"].append({
            "id": "AC-2",
            "criterion": "future synthetic outcome",
            "verification_method": "run the later slice check",
            "verifier": "AGENT",
            "slice_id": "synthetic-slice-2",
            "status": "PLANNED",
            "evidence": None,
        })

        self.assertAccepted("IMPLEMENT", fixture)

    def test_accepts_implement_success_with_completed_and_future_slice_checks(self) -> None:
        fixture = successful_implementation()
        checks = fixture["executor_result"]["acceptance_checks"]
        checks.extend([
            {
                "id": "AC-0",
                "criterion": "completed synthetic outcome",
                "verification_method": "run the earlier slice check",
                "verifier": "AGENT",
                "slice_id": "synthetic-slice-0",
                "status": "PASS",
                "evidence": "earlier slice evidence",
            },
            {
                "id": "AC-2",
                "criterion": "future synthetic outcome",
                "verification_method": "run the later slice check",
                "verifier": "AGENT",
                "slice_id": "synthetic-slice-2",
                "status": "PLANNED",
                "evidence": None,
            },
        ])
        expected = self._expected_acceptance("IMPLEMENT", fixture)

        self.assertEqual([], validate_payload(
            "IMPLEMENT",
            fixture,
            expected_acceptance=expected,
            current_slice_ids={"synthetic-slice-1"},
            completed_slice_ids={"synthetic-slice-0"},
        ))

    def test_rejects_implement_success_with_future_slice_marked_pass(self) -> None:
        fixture = successful_implementation()
        fixture["executor_result"]["acceptance_checks"].append({
            "id": "AC-2",
            "criterion": "future synthetic outcome",
            "verification_method": "run the later slice check",
            "verifier": "AGENT",
            "slice_id": "synthetic-slice-2",
            "status": "PASS",
            "evidence": "premature future evidence",
        })

        self.assertRejected("IMPLEMENT", fixture)

    def test_rejects_implement_without_controller_completed_slice_context(self) -> None:
        fixture = successful_implementation()
        expected = self._expected_acceptance("IMPLEMENT", fixture)

        errors = validate_payload(
            "IMPLEMENT",
            fixture,
            expected_acceptance=expected,
            current_slice_ids={"synthetic-slice-1"},
        )
        self.assertTrue(errors, msg="omitted completed-slice context unexpectedly accepted")

    def test_rejects_implement_that_adds_a_worker_selected_current_slice(self) -> None:
        fixture = successful_implementation()
        fixture["executor_result"]["tdd_slices"].append({
            "id": "synthetic-slice-2",
            "objective": "unauthorized future slice",
            "test_file": "tests/future_slice_test.ext",
            "red_command": "synthetic future red command",
            "red_exit_code": 1,
            "expected_failure": "expected future functional failure",
            "red_failure_kind": "EXPECTED_FUNCTIONAL",
            "minimal_implementation": "unauthorized future implementation",
            "green_command": "synthetic future green command",
            "green_exit_code": 0,
            "green_result": "synthetic future green result",
        })
        fixture["executor_result"]["acceptance_checks"].append({
            "id": "AC-2",
            "criterion": "future synthetic outcome",
            "verification_method": "run the later slice check",
            "verifier": "AGENT",
            "slice_id": "synthetic-slice-2",
            "status": "PASS",
            "evidence": "worker-selected future evidence",
        })
        expected = self._expected_acceptance("IMPLEMENT", fixture)

        errors = validate_payload(
            "IMPLEMENT",
            fixture,
            expected_acceptance=expected,
            current_slice_ids={"synthetic-slice-1"},
            completed_slice_ids=set(),
        )
        self.assertTrue(errors, msg="worker-selected current slice unexpectedly accepted")

    def test_rejects_implement_success_without_check_for_current_slice(self) -> None:
        fixture = successful_implementation()
        fixture["executor_result"]["acceptance_checks"][0]["slice_id"] = "synthetic-slice-2"

        self.assertRejected("IMPLEMENT", fixture)

    def test_rejects_duplicate_acceptance_check_ids(self) -> None:
        fixture = executor_fixture("PLAN")
        fixture["executor_result"]["acceptance_checks"].append(
            dict(fixture["executor_result"]["acceptance_checks"][0])
        )

        self.assertRejected("PLAN", fixture)

    def test_rejects_implement_and_test_success_without_passing_acceptance_evidence(self) -> None:
        for action in ("IMPLEMENT", "TEST"):
            for mutation in ("missing", "planned", "no-evidence"):
                with self.subTest(action=action, mutation=mutation):
                    fixture = successful_implementation() if action == "IMPLEMENT" else executor_fixture("TEST")
                    checks = fixture["executor_result"]["acceptance_checks"]
                    if mutation == "missing":
                        checks.clear()
                    elif mutation == "planned":
                        checks[0]["status"] = "PLANNED"
                        checks[0]["evidence"] = None
                    else:
                        checks[0]["evidence"] = None
                    self.assertRejected(action, fixture)

    def test_rejects_approved_review_without_independent_acceptance_evidence(self) -> None:
        for mutation in ("not-verified", "missing", "failed", "no-evidence"):
            with self.subTest(mutation=mutation):
                fixture = review_fixture()
                acceptance = fixture["review_result"]["acceptance"]
                if mutation == "not-verified":
                    acceptance["verified"] = False
                elif mutation == "missing":
                    acceptance["checks"].clear()
                elif mutation == "failed":
                    acceptance["checks"][0]["status"] = "FAIL"
                else:
                    acceptance["checks"][0]["evidence"] = ""
                self.assertRejected("REVIEW", fixture)

    def test_rejects_approved_review_with_missing_or_substituted_acceptance_ids(self) -> None:
        for mutation in ("missing", "substituted", "duplicate"):
            with self.subTest(mutation=mutation):
                fixture = review_fixture()
                acceptance = fixture["review_result"]["acceptance"]
                if mutation == "missing":
                    acceptance["expected_check_ids"].append("AC-2")
                elif mutation == "substituted":
                    acceptance["checks"][0]["id"] = "AC-X"
                else:
                    acceptance["expected_check_ids"].append("AC-1")
                self.assertRejected("REVIEW", fixture)

    def test_rejects_review_with_contradictory_baseline_or_ownership(self) -> None:
        for status in ("APPROVED", "CHANGES_REQUIRED", "BLOCKED"):
            for section in ("baseline", "ownership"):
                with self.subTest(status=status, section=section):
                    fixture = review_fixture(status)
                    fixture["review_result"][section]["violations"] = [{"path": "src/contradiction.ext"}]
                    self.assertRejected("REVIEW", fixture)

    def test_rejects_approved_review_with_whitespace_evidence(self) -> None:
        fixture = review_fixture()
        fixture["review_result"]["acceptance"]["checks"][0]["evidence"] = " "

        self.assertRejected("REVIEW", fixture)

    def test_rejects_review_with_whitespace_only_finding_evidence(self) -> None:
        fixture = review_fixture("CHANGES_REQUIRED")
        fixture["review_result"]["findings"][0]["evidence"] = " \t "

        self.assertRejected("REVIEW", fixture)

    def test_rejects_review_without_authoritative_acceptance(self) -> None:
        for status in ("APPROVED", "CHANGES_REQUIRED", "BLOCKED"):
            with self.subTest(status=status):
                self.assertTrue(validate_payload("REVIEW", review_fixture(status)))

    def test_rejects_executor_without_authoritative_acceptance_for_every_status(self) -> None:
        for action in ("IMPLEMENT", "TEST"):
            for stage_status in ("SUCCESS", "BLOCKED", "TIMEOUT", "INVALID"):
                with self.subTest(action=action, stage_status=stage_status):
                    fixture = successful_implementation() if action == "IMPLEMENT" else executor_fixture("TEST")
                    fixture["executor_result"]["stage"]["status"] = stage_status
                    errors = validate_payload(
                        action,
                        fixture,
                        current_slice_ids={"synthetic-slice-1"} if action == "IMPLEMENT" else None,
                        completed_slice_ids=set() if action == "IMPLEMENT" else None,
                    )
                    self.assertTrue(errors, msg="omitted authoritative mapping unexpectedly accepted")

    def test_rejects_empty_authoritative_acceptance_for_every_executor_status(self) -> None:
        for action in ("IMPLEMENT", "TEST"):
            for stage_status in ("SUCCESS", "BLOCKED", "TIMEOUT", "INVALID"):
                with self.subTest(action=action, stage_status=stage_status):
                    fixture = successful_implementation() if action == "IMPLEMENT" else executor_fixture("TEST")
                    fixture["executor_result"]["stage"]["status"] = stage_status
                    fixture["executor_result"]["acceptance_checks"] = []
                    errors = validate_payload(
                        action,
                        fixture,
                        expected_acceptance={},
                        current_slice_ids={"synthetic-slice-1"} if action == "IMPLEMENT" else None,
                        completed_slice_ids=set() if action == "IMPLEMENT" else None,
                    )
                    self.assertTrue(errors, msg="empty authoritative mapping unexpectedly accepted")

    def test_rejects_executor_changes_to_authoritative_mapping(self) -> None:
        mutations = (
            ("verifier", "HUMAN"),
            ("verification_method", "self attest"),
            ("criterion", "substituted outcome"),
            ("slice_id", "synthetic-slice-X"),
            ("slice_id", None),
            ("id", "AC-X"),
        )
        for action in ("IMPLEMENT", "TEST"):
            for stage_status in ("SUCCESS", "BLOCKED", "TIMEOUT", "INVALID"):
                for field, value in mutations:
                    with self.subTest(action=action, stage_status=stage_status, field=field):
                        fixture = successful_implementation() if action == "IMPLEMENT" else executor_fixture("TEST")
                        fixture["executor_result"]["stage"]["status"] = stage_status
                        authoritative = self._expected_acceptance(action, fixture)
                        fixture["executor_result"]["acceptance_checks"][0][field] = value
                        errors = validate_payload(
                            action,
                            fixture,
                            expected_acceptance=authoritative,
                            current_slice_ids={"synthetic-slice-1"} if action == "IMPLEMENT" else None,
                            completed_slice_ids=set() if action == "IMPLEMENT" else None,
                        )
                        self.assertTrue(errors, msg=f"mutated {field} unexpectedly accepted")

    def test_rejects_review_changes_to_authoritative_mapping(self) -> None:
        mutations = (
            ("verifier", "HUMAN"),
            ("verification_method", "self attest"),
            ("criterion", "substituted outcome"),
            ("slice_id", "synthetic-slice-X"),
            ("slice_id", None),
            ("id", "AC-X"),
        )
        for status in ("APPROVED", "CHANGES_REQUIRED", "BLOCKED"):
            for field, value in mutations:
                with self.subTest(status=status, field=field):
                    fixture = review_fixture(status)
                    authoritative = self._expected_acceptance("REVIEW", fixture)
                    fixture["review_result"]["acceptance"]["checks"][0][field] = value
                    errors = validate_payload("REVIEW", fixture, expected_acceptance=authoritative)
                    self.assertTrue(errors, msg=f"mutated review {field} unexpectedly accepted")

    def test_rejects_review_that_changes_authoritative_acceptance(self) -> None:
        authoritative = {
            "AC-1": {
                "criterion": "synthetic outcome is verified",
                "verification_method": "run the synthetic focused check",
                "verifier": "AGENT",
                "slice_id": "synthetic-slice-1",
            },
            "AC-2": {
                "criterion": "second synthetic outcome",
                "verification_method": "run the second synthetic focused check",
                "verifier": "AGENT",
                "slice_id": "synthetic-slice-2",
            },
        }
        for status in ("APPROVED", "CHANGES_REQUIRED", "BLOCKED"):
            for scenario in ("omitted", "substituted"):
                with self.subTest(status=status, scenario=scenario):
                    fixture = review_fixture(status)
                    if scenario == "substituted":
                        fixture["review_result"]["acceptance"]["checks"][0]["criterion"] = "different outcome"
                    errors = validate_payload("REVIEW", fixture, expected_acceptance=authoritative)
                    self.assertTrue(errors, msg="review unexpectedly accepted against authoritative criteria")

    def test_rejects_approved_review_with_e2e_violation(self) -> None:
        fixture = review_fixture()
        fixture["review_result"]["e2e"]["violation"] = True

        self.assertRejected("REVIEW", fixture)

    def test_rejects_implement_success_without_tdd_slices(self) -> None:
        self.assertRejected("IMPLEMENT", executor_fixture("IMPLEMENT"))

    def test_rejects_green_nonzero_and_red_zero(self) -> None:
        for field, value in (("green_exit_code", 1), ("red_exit_code", 0)):
            with self.subTest(field=field):
                fixture = successful_implementation()
                fixture["executor_result"]["tdd_slices"][0][field] = value
                self.assertRejected("IMPLEMENT", fixture)

    def test_rejects_path_strings_stage_payload_absence_and_done(self) -> None:
        for mutator in (
            lambda f: f["executor_result"].update({"modified_paths": ["src/example.ext"]}),
            lambda f: f["executor_result"].update({"created_paths": ["src/example.ext"]}),
            lambda f: f["executor_result"].pop("stage_payload"),
            lambda f: f["executor_result"]["next_step"].update({"stage": "DONE"}),
        ):
            fixture = executor_fixture("SPECIFY")
            mutator(fixture)
            self.assertRejected("SPECIFY", fixture)

    def test_rejects_wrong_envelope_unknown_property_yaml_and_v1(self) -> None:
        self.assertRejected("REVIEW", executor_fixture("TEST"))
        self.assertRejected("IMPLEMENT", review_fixture())
        unknown = executor_fixture("SPECIFY")
        unknown["executor_result"]["unknown"] = True
        self.assertRejected("SPECIFY", unknown)
        self.assertTrue(validate_json_text("SPECIFY", "executor_result:\n  schema_version: 2"))
        version_one = executor_fixture("SPECIFY")
        version_one["executor_result"]["schema_version"] = 1
        self.assertRejected("SPECIFY", version_one)

    def test_reuses_same_schema_content_but_reads_current_schema_file(self) -> None:
        fixture = executor_fixture("SPECIFY")
        with tempfile.TemporaryDirectory() as temporary_directory:
            schema_path = Path(temporary_directory) / "schema.json"
            schema_path.write_text(json.dumps(self._executor_schema(cache_namespace=temporary_directory)), encoding="utf-8")
            with patch.object(validate_protocol.Draft202012Validator, "check_schema", wraps=validate_protocol.Draft202012Validator.check_schema) as check_schema:
                self.assertEqual([], self._validate_with_schema(schema_path, fixture))
                self.assertEqual([], self._validate_with_schema(schema_path, fixture))
            self.assertEqual(1, check_schema.call_count)

            schema_path.write_text(json.dumps({"type": "object", "required": ["different_envelope"]}), encoding="utf-8")
            self.assertTrue(self._validate_with_schema(schema_path, fixture))

    def test_rejects_malformed_or_invalid_current_schema_after_valid_cache(self) -> None:
        fixture = executor_fixture("SPECIFY")
        with tempfile.TemporaryDirectory() as temporary_directory:
            schema_path = Path(temporary_directory) / "schema.json"
            schema_path.write_text(json.dumps(self._executor_schema(cache_namespace=temporary_directory)), encoding="utf-8")
            self.assertEqual([], self._validate_with_schema(schema_path, fixture))

            schema_path.write_text("{", encoding="utf-8")
            self.assertTrue(self._validate_with_schema(schema_path, fixture))

            schema_path.write_text(json.dumps({"type": 42}), encoding="utf-8")
            with self.assertRaises(SchemaError):
                self._validate_with_schema(schema_path, fixture)

    def test_evicts_least_recently_used_schema_when_cache_is_bounded(self) -> None:
        fixture = executor_fixture("SPECIFY")
        with tempfile.TemporaryDirectory() as temporary_directory:
            schema_path = Path(temporary_directory) / "schema.json"
            with patch.object(validate_protocol.Draft202012Validator, "check_schema", wraps=validate_protocol.Draft202012Validator.check_schema) as check_schema:
                for cache_id in range(33):
                    schema_path.write_text(json.dumps(self._executor_schema(cache_id, temporary_directory)), encoding="utf-8")
                    self.assertEqual([], self._validate_with_schema(schema_path, fixture))
                schema_path.write_text(json.dumps(self._executor_schema(cache_namespace=temporary_directory)), encoding="utf-8")
                self.assertEqual([], self._validate_with_schema(schema_path, fixture))
            self.assertEqual(34, check_schema.call_count)


if __name__ == "__main__":
    unittest.main(verbosity=2)
