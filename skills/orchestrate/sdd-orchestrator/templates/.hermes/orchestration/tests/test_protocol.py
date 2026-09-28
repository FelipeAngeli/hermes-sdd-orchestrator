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
            "schema_version": 2,
            "stage": {"value": stage, "status": status},
            "executor": {"name": "CODEX", "invocation_type": "EXTERNAL_CLI"},
            "consulted_paths": [{"path": "src/example.ext"}],
            "modified_paths": [],
            "created_paths": [],
            "validated_symbols": [{"symbol": "example", "path": "src/example.ext", "exists": True}],
            "commands": [],
            "blockers": [],
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
            "schema_version": 2,
            "status": status,
            "reviewed_paths": [{"path": "src/example.ext"}],
            "findings": [] if status == "APPROVED" else [{"severity": "medium", "path": "src/example.ext", "description": "synthetic finding", "evidence": "synthetic evidence"}],
            "baseline": {"preserved": True, "violations": []},
            "ownership": {"valid": True, "violations": []},
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

    def assertAccepted(self, action: str, payload: dict) -> None:
        self.assertEqual([], validate_payload(action, payload), msg=validate_payload(action, payload))

    def assertRejected(self, action: str, payload: object) -> None:
        self.assertTrue(validate_payload(action, payload), msg="fixture unexpectedly accepted")

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
