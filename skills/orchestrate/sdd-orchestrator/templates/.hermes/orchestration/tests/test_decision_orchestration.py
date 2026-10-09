"""Typed, subordinate Jev decision orchestration."""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from typing import Any

import jsonschema

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
MODULE = RUNTIME / "decision_orchestration.py"


def load_module() -> Any:
    spec = importlib.util.spec_from_file_location("sdd_decision_orchestration", MODULE)
    if spec is None or spec.loader is None:
        raise AssertionError("decision orchestration is not importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def request(mode: str = "OFF") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "decision_id": "APP-1-plan-route",
        "ticket": "APP-1",
        "kind": "AGENT_SELECTION",
        "mode": mode,
        "binding": {"stage": "PLAN", "state_sha256": "a" * 64},
        "baseline": {"value": "STAGE_AGENT", "user_locked": False, "deterministic_ready": True},
        "candidates": {
            "STAGE_AGENT": "Use the normal stage worker.",
            "DATA_FLOW_TRACER": "Run one bounded read-only impact trace first.",
        },
        "state": {
            "summary": "A shared contract may affect several modules.",
            "signals": {"shared_contract": True, "changed_files": 3},
            "evidence_ids": ["git:abc123", "check:AC-1"],
        },
    }


def jev_report(value: str = "DATA_FLOW_TRACER", confidence: float = 0.91) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "DECIDED",
        "provenance": "LIVE_JEV",
        "provider": "typesafe",
        "ticket": "APP-1",
        "model": "jev-1.13.0",
        "threshold": 0.70,
        "fingerprint": "b" * 64,
        "decisions": {
            "selection": {
                "value": value,
                "confidence": confidence,
                "disposition": "DECIDED",
                "reason": "AT_OR_ABOVE_THRESHOLD",
            }
        },
        "summary": {"decided": 1, "review": 0},
        "usage": {"input_tokens": 31, "output_tokens": 7},
        "billing": {"credits_charged": "1"},
    }


def review_report() -> dict[str, Any]:
    report = jev_report()
    report["status"] = "REVIEW"
    report["decisions"]["selection"] = {
        "value": None,
        "confidence": 0.63,
        "disposition": "REVIEW",
        "reason": "BELOW_THRESHOLD",
    }
    report["summary"] = {"decided": 0, "review": 1}
    return report


class DecisionOrchestrationTests(unittest.TestCase):
    def test_off_never_calls_jev_and_keeps_the_deterministic_baseline(self) -> None:
        module = load_module()

        receipt = module.decide(
            request("OFF"),
            lambda payload: self.fail("OFF reached the Jev evaluator"),
        )

        self.assertEqual("OFF", receipt["configured_mode"])
        self.assertEqual("OFF", receipt["effective_mode"])
        self.assertEqual("STAGE_AGENT", receipt["outcome"]["selected"])
        self.assertFalse(receipt["outcome"]["applied"])
        self.assertEqual("NOT_CALLED", receipt["recommendation"]["provenance"])
        self.assertEqual(0, receipt["metrics"]["jev_calls"])

    def test_shadow_records_disagreement_and_metrics_without_applying_it(self) -> None:
        module = load_module()
        calls: list[dict[str, Any]] = []

        receipt = module.decide(
            request("SHADOW"),
            lambda payload: calls.append(payload) or jev_report(),
        )

        self.assertEqual(1, len(calls))
        self.assertEqual("choice", calls[0]["questions"]["selection"]["type"])
        self.assertEqual(set(request()["candidates"]), set(calls[0]["questions"]["selection"]["criteria"]))
        self.assertEqual("SHADOW", receipt["effective_mode"])
        self.assertEqual("STAGE_AGENT", receipt["outcome"]["selected"])
        self.assertFalse(receipt["outcome"]["applied"])
        self.assertEqual("DISAGREE", receipt["outcome"]["agreement"])
        self.assertEqual("DATA_FLOW_TRACER", receipt["recommendation"]["value"])
        self.assertEqual(31, receipt["metrics"]["input_tokens"])
        self.assertEqual({"credits_charged": "1"}, receipt["metrics"]["billing"])

    def test_active_applies_only_a_decided_eligible_recommendation(self) -> None:
        module = load_module()

        receipt = module.decide(request("ACTIVE"), lambda payload: jev_report())

        self.assertEqual("ACTIVE", receipt["effective_mode"])
        self.assertEqual("DATA_FLOW_TRACER", receipt["outcome"]["selected"])
        self.assertTrue(receipt["outcome"]["applied"])
        self.assertFalse(receipt["outcome"]["requires_review"])
        self.assertEqual("JEV_DECISION_APPLIED", receipt["outcome"]["reason"])

    def test_active_review_preserves_baseline_and_requires_human_resolution(self) -> None:
        module = load_module()

        receipt = module.decide(request("ACTIVE"), lambda payload: review_report())

        self.assertEqual("FALLBACK", receipt["effective_mode"])
        self.assertEqual("STAGE_AGENT", receipt["outcome"]["selected"])
        self.assertFalse(receipt["outcome"]["applied"])
        self.assertTrue(receipt["outcome"]["requires_review"])
        self.assertEqual("JEV_REVIEW_REQUIRED", receipt["outcome"]["reason"])

    def test_fallback_mode_keeps_baseline_on_review_without_blocking(self) -> None:
        module = load_module()

        receipt = module.decide(request("FALLBACK"), lambda payload: review_report())

        self.assertEqual("FALLBACK", receipt["effective_mode"])
        self.assertEqual("STAGE_AGENT", receipt["outcome"]["selected"])
        self.assertFalse(receipt["outcome"]["requires_review"])
        self.assertEqual("JEV_FALLBACK_USED", receipt["outcome"]["reason"])

    def test_fallback_mode_applies_a_decided_recommendation(self) -> None:
        module = load_module()

        receipt = module.decide(request("FALLBACK"), lambda payload: jev_report())

        self.assertEqual("DATA_FLOW_TRACER", receipt["outcome"]["selected"])
        self.assertTrue(receipt["outcome"]["applied"])
        self.assertEqual("JEV_DECISION_APPLIED", receipt["outcome"]["reason"])

    def test_completion_never_overrides_a_deterministic_veto(self) -> None:
        module = load_module()
        value = request("ACTIVE")
        value["kind"] = "COMPLETION_ASSESSMENT"
        value["baseline"] = {"value": "INCOMPLETE", "user_locked": False, "deterministic_ready": False}
        value["candidates"] = {
            "INCOMPLETE": "Deterministic acceptance or gates remain unsatisfied.",
            "COMPLETE": "Every deterministic acceptance check and gate is satisfied.",
        }

        receipt = module.decide(value, lambda payload: jev_report("COMPLETE"))

        self.assertEqual("INCOMPLETE", receipt["outcome"]["selected"])
        self.assertFalse(receipt["outcome"]["applied"])
        self.assertEqual("DETERMINISTIC_COMPLETION_VETO", receipt["outcome"]["reason"])

    def test_unknown_recommendation_is_rejected_instead_of_becoming_a_route(self) -> None:
        module = load_module()

        with self.assertRaisesRegex(module.DecisionError, "DECISION_REPORT_INVALID"):
            module.decide(request("ACTIVE"), lambda payload: jev_report("INVENTED_AGENT"))

    def test_malformed_governor_report_is_rejected_with_a_stable_error(self) -> None:
        module = load_module()

        with self.assertRaisesRegex(module.DecisionError, "DECISION_REPORT_INVALID"):
            module.decide(request("ACTIVE"), lambda payload: {"status": "DECIDED"})

    def test_user_locked_route_cannot_be_replaced_in_active_mode(self) -> None:
        module = load_module()
        value = request("ACTIVE")
        value["baseline"]["user_locked"] = True

        receipt = module.decide(value, lambda payload: jev_report())

        self.assertEqual("STAGE_AGENT", receipt["outcome"]["selected"])
        self.assertFalse(receipt["outcome"]["applied"])
        self.assertEqual("EXPLICIT_CHOICE_PRESERVED", receipt["outcome"]["reason"])

    def test_secret_like_state_is_rejected_before_the_evaluator(self) -> None:
        module = load_module()
        value = request("SHADOW")
        value["state"]["summary"] = "Authorization: Bearer secret-token-value"

        with self.assertRaisesRegex(module.DecisionError, "DECISION_STATE_SENSITIVE"):
            module.decide(value, lambda payload: self.fail("sensitive state reached Jev"))

    def test_secret_like_candidate_criteria_is_rejected_before_the_evaluator(self) -> None:
        module = load_module()
        value = request("SHADOW")
        value["candidates"]["DATA_FLOW_TRACER"] = "Authorization: Bearer secret-token-value"

        with self.assertRaisesRegex(module.DecisionError, "DECISION_STATE_SENSITIVE"):
            module.decide(value, lambda payload: self.fail("sensitive criteria reached Jev"))

    def test_invalid_mode_is_rejected_before_the_evaluator(self) -> None:
        module = load_module()
        value = request("TURBO")

        with self.assertRaisesRegex(module.DecisionError, "DECISION_REQUEST_INVALID"):
            module.decide(value, lambda payload: self.fail("invalid request reached Jev"))

    def test_public_contract_schemas_accept_the_request_and_receipt(self) -> None:
        module = load_module()
        value = request("SHADOW")
        receipt = module.decide(value, lambda payload: jev_report())
        off_receipt = module.decide(request("OFF"), lambda payload: self.fail("OFF called Jev"))
        request_schema = json.loads((SCHEMAS / "DECISION_REQUEST_SCHEMA.json").read_text(encoding="utf-8"))
        receipt_schema = json.loads((SCHEMAS / "DECISION_RECEIPT_SCHEMA.json").read_text(encoding="utf-8"))

        jsonschema.Draft202012Validator(request_schema).validate(value)
        jsonschema.Draft202012Validator(receipt_schema).validate(receipt)
        jsonschema.Draft202012Validator(receipt_schema).validate(off_receipt)

    def test_public_schemas_reject_values_rejected_by_runtime_invariants(self) -> None:
        request_schema = json.loads((SCHEMAS / "DECISION_REQUEST_SCHEMA.json").read_text(encoding="utf-8"))
        receipt_schema = json.loads((SCHEMAS / "DECISION_RECEIPT_SCHEMA.json").read_text(encoding="utf-8"))
        invalid_request = request("SHADOW")
        invalid_request["candidates"]["BAD\nNAME"] = invalid_request["candidates"].pop("DATA_FLOW_TRACER")
        invalid_receipt = load_module().decide(request("OFF"), lambda payload: self.fail("OFF called Jev"))
        invalid_receipt["kind"] = "INVENTED_KIND"
        invalid_receipt["binding"]["stage"] = "INVENTED_STAGE"

        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(request_schema).validate(invalid_request)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(receipt_schema).validate(invalid_receipt)


if __name__ == "__main__":
    unittest.main()
