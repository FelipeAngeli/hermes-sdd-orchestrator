"""Behavior of the bounded execute → verify → correct decision tool."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))
import correction_loop as loop  # noqa: E402

SCRIPT = RUNTIME / "correction_loop.py"


def digest(label: str) -> str:
    return (label.encode().hex() * 64)[:64]


def attempt(number: int, *, verification: str = "FAIL", change: str | None = None, evidence: str | None = None,
            hypothesis: str | None = None, tier: str = "WORKER", cost: float | None = 1.0) -> dict:
    return {
        "attempt": number,
        "hypothesis": hypothesis or f"hypothesis {number}",
        "tier": tier,
        "escalation_reason": None,
        "change_digest": digest(change or f"c{number}"),
        "evidence_digest": digest(evidence or f"e{number}"),
        "verification": verification,
        "failed_check_ids": [] if verification == "PASS" else ["AC-1"],
        "cost_units": cost,
    }


def record(*attempts: dict, proposed: dict | None = None, max_attempts: int = 3,
           max_calls: int = 8, calls: int | None = None, max_cost: float | None = None,
           cost: float | None = None) -> dict:
    return {
        "schema_version": 1,
        "ticket": "APP-1",
        "action": "IMPLEMENT_SLICE",
        "limits": {"max_attempts": max_attempts, "max_executor_calls": max_calls, "max_cost_units": max_cost},
        "usage": {"executor_calls": len(attempts) if calls is None else calls, "cost_units": cost},
        "attempts": list(attempts),
        "proposed": proposed,
    }


def proposal(hypothesis: str = "the fixture ignores the timezone", *, tier: str = "WORKER",
             reason: str | None = None, estimate: float | None = None) -> dict:
    return {"hypothesis": hypothesis, "tier": tier, "escalation_reason": reason, "estimated_cost_units": estimate}


class CorrectionLoopDecisionTests(unittest.TestCase):
    def test_first_attempt_is_allowed_within_limits(self) -> None:
        result = loop.decide(record(proposed=proposal()))
        self.assertEqual("CORRECT", result["decision"])
        self.assertFalse(result["end_turn"])
        self.assertEqual(1, result["next_attempt"])

    def test_a_passing_verification_ends_the_loop_as_verified(self) -> None:
        result = loop.decide(record(attempt(1), attempt(2, verification="PASS")))
        self.assertEqual("VERIFIED", result["decision"])
        self.assertEqual("NONE", result["stop_reason"])

    def test_failure_with_a_new_hypothesis_permits_one_more_correction(self) -> None:
        result = loop.decide(record(attempt(1), proposed=proposal()))
        self.assertEqual("CORRECT", result["decision"])
        self.assertEqual(2, result["next_attempt"])
        self.assertEqual(["AC-1"], result["evidence"]["failed_check_ids"])

    def test_attempt_limit_pauses_auditably(self) -> None:
        result = loop.decide(record(attempt(1), attempt(2), proposed=proposal(), max_attempts=2))
        self.assertEqual("STOP", result["decision"])
        self.assertEqual("RETRY_BUDGET_REACHED", result["stop_reason"])
        self.assertTrue(result["end_turn"])
        self.assertEqual("PAUSED", result["state_updates"]["mode"])
        self.assertEqual("RETRY_BUDGET_REACHED", result["state_updates"]["stop_reason"])
        self.assertEqual(2, result["evidence"]["attempts"])
        self.assertTrue(result["next_step"].strip())

    def test_executor_call_limit_stops_before_dispatch(self) -> None:
        result = loop.decide(record(attempt(1), proposed=proposal(), max_calls=1))
        self.assertEqual("EXECUTOR_CALL_BUDGET_REACHED", result["stop_reason"])

    def test_cost_limit_counts_the_estimate_of_the_next_attempt(self) -> None:
        result = loop.decide(record(attempt(1), proposed=proposal(estimate=2.0), max_cost=2.5, cost=1.0))
        self.assertEqual("COST_BUDGET_REACHED", result["stop_reason"])
        allowed = loop.decide(record(attempt(1), proposed=proposal(estimate=1.0), max_cost=2.5, cost=1.0))
        self.assertEqual("CORRECT", allowed["decision"])

    def test_cost_limit_with_unknown_usage_fails_closed(self) -> None:
        result = loop.decide(record(attempt(1), proposed=proposal(), max_cost=5.0, cost=None))
        self.assertEqual("STOP", result["decision"])
        self.assertEqual("COST_BUDGET_REACHED", result["stop_reason"])

    def test_unconfigured_cost_limit_only_records_usage(self) -> None:
        result = loop.decide(record(attempt(1), proposed=proposal(), max_cost=None, cost=None))
        self.assertEqual("CORRECT", result["decision"])
        self.assertIsNone(result["usage"]["cost_units"])

    def test_repeating_a_previous_hypothesis_is_refused(self) -> None:
        for hypothesis in ("hypothesis 1", "  Hypothesis 1 ", ""):
            with self.subTest(hypothesis=hypothesis):
                result = loop.decide(record(attempt(1), proposed=proposal(hypothesis)))
                self.assertEqual("NO_NEW_HYPOTHESIS", result["stop_reason"])

    def test_unchanged_evidence_after_a_correction_means_no_progress(self) -> None:
        result = loop.decide(record(attempt(1, evidence="same"), attempt(2, evidence="same"), proposed=proposal()))
        self.assertEqual("NO_PROGRESS", result["stop_reason"])

    def test_repeating_the_same_change_means_no_progress(self) -> None:
        result = loop.decide(record(attempt(1, change="same"), attempt(2, change="same"), proposed=proposal()))
        self.assertEqual("NO_PROGRESS", result["stop_reason"])

    def test_failure_without_a_proposal_pauses_for_a_human_hypothesis(self) -> None:
        result = loop.decide(record(attempt(1)))
        self.assertEqual("NO_NEW_HYPOTHESIS", result["stop_reason"])

    def test_escalation_requires_a_concrete_failure_and_a_reason(self) -> None:
        with self.assertRaises(loop.LoopError):
            loop.decide(record(attempt(1), proposed=proposal(tier="ESCALATED")))
        with self.assertRaises(loop.LoopError):
            loop.decide(record(proposed=proposal(tier="ESCALATED", reason="complex")))
        result = loop.decide(record(attempt(1), proposed=proposal(tier="ESCALATED", reason="worker failed AC-1 twice")))
        self.assertEqual("CORRECT", result["decision"])
        self.assertEqual("worker failed AC-1 twice", result["escalation_reason"])

    def test_recorded_attempts_must_be_contiguous_and_escalations_justified(self) -> None:
        with self.assertRaises(loop.LoopError):
            loop.decide(record(attempt(1), attempt(3), proposed=proposal()))
        escalated = attempt(1, tier="ESCALATED")
        with self.assertRaises(loop.LoopError):
            loop.decide(record(escalated, proposed=proposal()))

    def test_usage_below_recorded_attempts_is_rejected(self) -> None:
        with self.assertRaises(loop.LoopError):
            loop.decide(record(attempt(1), attempt(2), proposed=proposal(), calls=1))

    def test_decision_is_deterministic_and_does_not_mutate_input(self) -> None:
        value = record(attempt(1), proposed=proposal())
        original = copy.deepcopy(value)
        self.assertEqual(loop.decide(value), loop.decide(value))
        self.assertEqual(original, value)


class CorrectionLoopCliTests(unittest.TestCase):
    def run_cli(self, value: dict) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "loop.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), "decide", "--record", str(path), "--json"],
                text=True, capture_output=True, timeout=30,
            )

    def test_cli_reports_a_stop_with_exit_zero(self) -> None:
        result = self.run_cli(record(attempt(1), attempt(2), proposed=proposal(), max_attempts=2))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("RETRY_BUDGET_REACHED", json.loads(result.stdout)["stop_reason"])

    def test_cli_rejects_a_schema_invalid_record(self) -> None:
        value = record(proposed=proposal())
        value["limits"]["max_attempts"] = 0
        result = self.run_cli(value)
        self.assertEqual(2, result.returncode)
        self.assertFalse(json.loads(result.stdout)["valid"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
