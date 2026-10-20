"""Controller-owned acceptance tests for the SDD adapter of the loop engine and its CLI.

Do not edit, skip or weaken these tests; the implementation must satisfy them with real logic.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from loop_engine_fixtures import RUNTIME, FakeClock, Recorder, failed, ok, write_json

import loop_control  # noqa: E402
import loop_engine  # noqa: E402
import loop_policy  # noqa: E402
import loop_sdd  # noqa: E402

CODE = ["SPECIFY", "CLARIFY", "PLAN", "TASKS", "IMPLEMENT", "TEST", "REVIEW"]
CLI = RUNTIME / "loop_cli.py"


def verdict_fail(evidence: str, hypothesis: str | None, checks=("AC-1",)):
    extra = {"failed_checks": list(checks), "evidence": evidence}
    if hypothesis is not None:
        extra["hypothesis"] = hypothesis
        extra["cause"] = f"cause of {evidence}"
    return failed("FUNCTIONAL", "VERIFICATION_FAILED", evidence, **extra)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="loop-sdd-")
        self.store = Path(self._tmp.name) / "store"
        self.clock = FakeClock()
        self.rec = Recorder()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_sdd(self, scripts, profile="CODE", **limits):
        spec = loop_sdd.build_definition(ticket="T-1", objective="Ship it.", profile=profile, limits=limits or None)
        handlers = {stage: self.rec.handler(stage, scripts.get(stage)) for stage in CODE}
        engine = loop_engine.LoopEngine(self.store, handlers=handlers, clock=self.clock.time, sleep=self.clock.sleep)
        execution = engine.start(spec)
        return engine, engine.run(execution["execution_id"])


class DefinitionTests(Base):
    def test_the_code_profile_is_a_chain_with_corrections_from_test_and_review(self):
        spec = loop_sdd.build_definition(ticket="T-1", objective="o", profile="CODE")
        self.assertEqual(CODE, [item["id"] for item in spec["nodes"]])
        by_id = {item["id"]: item for item in spec["nodes"]}
        for previous, current in zip(CODE, CODE[1:]):
            self.assertEqual([previous], by_id[current]["depends_on"])
        self.assertFalse(by_id["IMPLEMENT"]["idempotent"])
        self.assertTrue(by_id["PLAN"]["idempotent"])
        self.assertEqual({"reopen": "IMPLEMENT"}, by_id["TEST"]["on_failure"])
        self.assertEqual({"reopen": "IMPLEMENT"}, by_id["REVIEW"]["on_failure"])
        self.assertEqual("gate", by_id["TEST"]["kind"])
        self.assertEqual("run-agent", by_id["PLAN"]["kind"])
        self.assertEqual(3, spec["limits"]["max_correction_cycles"])
        self.assertEqual(2, spec["limits"]["max_transient_retries"])
        self.assertGreater(by_id["PLAN"]["timeout_seconds"], 0)

    def test_the_decision_doc_profile_skips_tasks_and_test(self):
        spec = loop_sdd.build_definition(ticket="T-1", objective="o", profile="DECISION_DOC")
        self.assertEqual(["SPECIFY", "CLARIFY", "PLAN", "IMPLEMENT", "REVIEW"], [item["id"] for item in spec["nodes"]])


class CorrectionTests(Base):
    def test_a_test_failure_reruns_only_implement_and_test(self):
        scripts = {"IMPLEMENT": [ok({"diff": "v1"}), ok({"diff": "v2"})],
                   "TEST": [verdict_fail("total is 9, expected 10", "off-by-one in sum"), ok({"verdict": "PASS"})]}
        _, execution = self.run_sdd(scripts)
        self.assertEqual(CODE[:6] + ["IMPLEMENT", "TEST", "REVIEW"], self.rec.calls)
        correction = self.rec.contexts["IMPLEMENT"][1].correction
        self.assertEqual(1, correction["cycle"])
        self.assertEqual("TEST", correction["verifier"])
        self.assertEqual("off-by-one in sum", correction["hypothesis"])
        self.assertEqual(["AC-1"], correction["failed_checks"])
        self.assertIsNone(self.rec.contexts["IMPLEMENT"][0].correction)
        self.assertEqual("COMPLETED", execution["status"])
        self.assertEqual(1, execution["usage"]["correction_cycles"])

    def test_a_review_failure_reruns_implement_test_and_review_only(self):
        scripts = {"IMPLEMENT": [ok({"diff": "v1"}), ok({"diff": "v2"})],
                   "REVIEW": [verdict_fail("missing null check", "guard the empty input"), ok({"verdict": "APPROVED"})]}
        _, execution = self.run_sdd(scripts)
        self.assertEqual(CODE + ["IMPLEMENT", "TEST", "REVIEW"], self.rec.calls)
        self.assertEqual("COMPLETED", execution["status"])

    def test_correction_cycles_are_capped_then_escalated(self):
        scripts = {"IMPLEMENT": [ok({"diff": f"v{index}"}) for index in range(10)],
                   "TEST": [verdict_fail(f"failure {index}", f"hypothesis {index}") for index in range(10)]}
        _, execution = self.run_sdd(scripts)
        self.assertEqual(4, self.rec.calls.count("IMPLEMENT"))
        self.assertEqual("BLOCKED", execution["status"])
        self.assertEqual("CORRECTION_BUDGET_EXHAUSTED", execution["halt"]["reason"])
        self.assertEqual("RETRY_BUDGET_REACHED", execution["halt"]["hermes_stop_reason"])

    def test_an_equivalent_failure_after_a_real_change_is_no_progress(self):
        scripts = {"IMPLEMENT": [ok({"diff": "v1"}), ok({"diff": "v2"})],
                   "TEST": [verdict_fail("Same   failure", "h1"), verdict_fail("same failure", "h2")]}
        _, execution = self.run_sdd(scripts)
        self.assertEqual(2, self.rec.calls.count("IMPLEMENT"))
        self.assertEqual("NO_PROGRESS", execution["halt"]["reason"])
        self.assertEqual("NO_PROGRESS", execution["halt"]["hermes_stop_reason"])

    def test_a_correction_that_changes_nothing_is_no_progress(self):
        scripts = {"IMPLEMENT": [ok({"diff": "v1"}), ok({"diff": "v1"})],
                   "TEST": [verdict_fail("fails", "h1"), verdict_fail("fails again", "h2")]}
        _, execution = self.run_sdd(scripts)
        self.assertEqual(2, self.rec.calls.count("IMPLEMENT"))
        self.assertEqual("NO_PROGRESS", execution["halt"]["reason"])

    def test_a_repeated_or_missing_hypothesis_escalates(self):
        scripts = {"IMPLEMENT": [ok({"diff": "v1"}), ok({"diff": "v2"})],
                   "TEST": [verdict_fail("e1", "same idea"), verdict_fail("e2", "Same  idea")]}
        _, execution = self.run_sdd(scripts)
        self.assertEqual("NO_NEW_HYPOTHESIS", execution["halt"]["reason"])
        self.store = Path(self._tmp.name) / "store-2"
        self.rec = Recorder()
        _, execution = self.run_sdd({"TEST": [verdict_fail("e1", None)]})
        self.assertEqual("NO_NEW_HYPOTHESIS", execution["halt"]["reason"])
        self.assertEqual(1, self.rec.calls.count("IMPLEMENT"))

    def test_a_human_hypothesis_resumes_a_stalled_correction(self):
        scripts = {"IMPLEMENT": [ok({"diff": "v1"}), ok({"diff": "v2"})],
                   "TEST": [verdict_fail("e1", None), ok({"verdict": "PASS"})]}
        engine, execution = self.run_sdd(scripts)
        loop_control.resolve(self.store, execution["execution_id"], node="TEST", decision="correct",
                             value="the fixture uses UTC", by="requester", quote="tente com UTC", actor="human")
        execution = engine.run(execution["execution_id"])
        self.assertEqual("the fixture uses UTC", self.rec.contexts["IMPLEMENT"][1].correction["hypothesis"])
        self.assertEqual("COMPLETED", execution["status"])

    def test_clarify_questions_wait_for_input(self):
        scripts = {"CLARIFY": [ok({"questions": ["Which currency?"]}, needs_input={"prompt": "Which currency?"}), ok()]}
        engine, execution = self.run_sdd(scripts)
        self.assertEqual("WAITING_INPUT", execution["status"])
        loop_control.resolve(self.store, execution["execution_id"], node="CLARIFY", decision="provide-input",
                             value="BRL", by="requester", quote="BRL", actor="human")
        execution = engine.run(execution["execution_id"])
        self.assertEqual("BRL", self.rec.contexts["CLARIFY"][1].human_input)
        self.assertEqual("COMPLETED", execution["status"])

    def test_transient_failures_retry_read_only_stages_but_not_implement(self):
        scripts = {"PLAN": [loop_policy.TransientFailure("RATE_LIMIT", "429"), ok()],
                   "IMPLEMENT": [loop_policy.TransientFailure("RATE_LIMIT", "429")]}
        _, execution = self.run_sdd(scripts)
        self.assertEqual(2, self.rec.calls.count("PLAN"))
        self.assertEqual(1, self.rec.calls.count("IMPLEMENT"))
        self.assertEqual("EFFECT_AMBIGUOUS", execution["halt"]["reason"])


class CliTests(Base):
    def cli(self, *args):
        completed = subprocess.run([sys.executable, str(CLI), *args, "--json"], capture_output=True, text=True, timeout=60)
        return completed.returncode, json.loads(completed.stdout)

    def test_validate_status_cancel_and_resolve_never_run_a_node(self):
        good = write_json(Path(self._tmp.name) / "good.json", loop_sdd.build_definition(ticket="T-2", objective="o", profile="CODE"))
        bad = write_json(Path(self._tmp.name) / "bad.json", {"schema_version": 1, "loop_id": "x", "objective": "o",
                                                             "nodes": [{"id": "A", "kind": "run-tool", "depends_on": ["A"]}]})
        self.assertEqual(0, self.cli("validate", "--definition", str(good))[0])
        code, payload = self.cli("validate", "--definition", str(bad))
        self.assertEqual((2, "DEPENDENCY_CYCLE"), (code, payload["code"]))

        engine = loop_engine.LoopEngine(self.store, handlers={})
        execution = engine.start(json.loads(good.read_text(encoding="utf-8")), execution_id="cli-1")
        code, payload = self.cli("status", "--store", str(self.store), "--execution", "cli-1")
        self.assertEqual(0, code)
        self.assertEqual("PENDING", payload["status"])
        self.assertEqual("PENDING", payload["nodes"]["SPECIFY"])
        code, payload = self.cli("resolve", "--store", str(self.store), "--execution", "cli-1", "--node", "SPECIFY",
                                 "--decision", "approve", "--by", "requester", "--quote", "ok")
        self.assertEqual((2, "NOTHING_PENDING"), (code, payload["code"]))
        code, payload = self.cli("cancel", "--store", str(self.store), "--execution", "cli-1", "--by", "requester", "--reason", "drop")
        self.assertEqual((0, "CANCELLED"), (code, payload["status"]))
        self.assertEqual(execution["execution_id"], payload["execution_id"])
        self.assertEqual(1, self.cli("status", "--store", str(self.store))[0])


if __name__ == "__main__":
    unittest.main()
