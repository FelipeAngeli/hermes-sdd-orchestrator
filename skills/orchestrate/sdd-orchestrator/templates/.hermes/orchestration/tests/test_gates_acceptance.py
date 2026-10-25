"""Controller-owned acceptance tests for SDD validation gates: the eight required scenarios.

Do not edit, skip or weaken these tests; the implementation must satisfy them with real logic.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate_fixtures import ANALYZE_CMD, PY, TEST_CMD, FakeReviewer, artifact, check, make_repo, review_artifact, write_app  # noqa: E402

import gate_engine  # noqa: E402
import gate_evidence  # noqa: E402
import gate_reviewer  # noqa: E402

CONTRACT_KEYS = {"gate_id", "task_id", "verdict", "evidence_refs", "failed_criteria", "suggested_actions", "evaluator", "timestamp"}


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="gates-")
        root = Path(self._tmp.name)
        self.repo = make_repo(root)
        self.store = root / "store"
        self.reviewer = FakeReviewer()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def engine(self, *, commands=None, reviewers=None, mode="OBSERVE", timeouts=None):
        configured = {"focused_tests": TEST_CMD, "analyze": ANALYZE_CMD, "format": None}
        configured.update(commands or {})
        if reviewers is None:
            reviewers = [gate_reviewer.Reviewer("claude-review", "CLAUDE", self.reviewer)]
        return gate_engine.GateEngine(self.store, self.repo, commands=configured, reviewers=reviewers, mode=mode, timeouts=timeouts)

    def evaluate(self, stage="TEST", value=None, engine=None, **context):
        engine = engine or self.engine()
        context.setdefault("check_commands", {"AC-1": TEST_CMD})
        context.setdefault("implementer", "CODEX")
        return engine.evaluate(stage, "T-1", value if value is not None else artifact(stage), context=context)

    def assert_contract(self, decision):
        self.assertLessEqual(CONTRACT_KEYS, set(decision))
        self.assertIn(decision["verdict"], {"PASS", "FAIL", "BLOCKED", "INCONCLUSIVE"})
        collector = gate_evidence.EvidenceCollector(self.store)
        for ref in decision["evidence_refs"]:
            self.assertIsNotNone(collector.get(ref), ref)

    def failed_ids(self, decision):
        return {item["id"] for item in decision["failed_criteria"]}


class ScenarioTests(Base):
    def test_correct_code_passes_with_reproduced_evidence(self):
        decision = self.evaluate()
        self.assert_contract(decision)
        self.assertEqual("PASS", decision["verdict"], decision["failed_criteria"])
        self.assertTrue(decision["evidence_refs"], "every PASS points to evidence")
        self.assertEqual("sdd.test", decision["gate_id"])
        self.assertEqual("T-1", decision["task_id"])
        self.assertIn("deterministic", decision["evaluator"])
        runs = [gate_evidence.EvidenceCollector(self.store).get(ref) for ref in decision["evidence_refs"]]
        self.assertTrue(any(item["kind"] == "command" and item["exit_code"] == 0 for item in runs))
        self.assertFalse(decision["blocking"], "observation mode never blocks")

    def test_a_failing_test_fails_with_a_bounded_corrective_task(self):
        write_app(self.repo, correct=False)
        honest = artifact("TEST", commands=[{"command": TEST_CMD, "purpose": "focused tests", "timeout_seconds": 60,
                                             "exit_code": 1, "result": "FAIL"}],
                          acceptance_checks=[check("AC-1", "FAIL")])
        decision = self.evaluate(value=honest)
        self.assertEqual("FAIL", decision["verdict"])
        self.assertNotIn("EVIDENCE_REPRODUCED", self.failed_ids(decision), "an honest failure report is not false evidence")
        self.assertIn("TESTS_PASS", self.failed_ids(decision))
        task = decision["corrective_task"]
        self.assertEqual("IMPLEMENT", task["stage"])
        self.assertEqual("TEST_FAILURE", task["failure_class"])
        self.assertIn("TESTS_PASS", task["failed_criteria"])
        self.assertTrue(task["unit"])
        self.assertTrue(decision["suggested_actions"])

    def test_a_missing_test_is_not_a_pass(self):
        decision = self.evaluate(engine=self.engine(commands={"focused_tests": f"{PY} tests/test_missing.py"}),
                                 check_commands={"AC-1": f"{PY} tests/test_missing.py"})
        self.assertEqual("FAIL", decision["verdict"])
        self.assertIn("TESTS_EXIST", self.failed_ids(decision))
        no_checks = artifact("TEST", acceptance_checks=[])
        decision = self.evaluate(value=no_checks, engine=self.engine(commands={"focused_tests": None}), check_commands={})
        self.assertEqual("BLOCKED", decision["verdict"])
        self.assertIn("TESTS_CONFIGURED", self.failed_ids(decision))

    def test_false_evidence_is_contradicted_by_the_real_run(self):
        write_app(self.repo, correct=False)  # the worker claims exit 0, the real run fails
        decision = self.evaluate()
        self.assertEqual("FAIL", decision["verdict"])
        self.assertIn("EVIDENCE_REPRODUCED", self.failed_ids(decision))
        claimed = artifact("IMPLEMENT", modified_paths=["app.py", "ghost.py"], tdd_slices=[])
        decision = self.evaluate("IMPLEMENT", claimed, editable_paths=["app.py", "ghost.py"])
        self.assertEqual("FAIL", decision["verdict"])
        self.assertIn("CLAIMS_MATCH_WORKSPACE", self.failed_ids(decision))

    def test_contradictory_reviewers_are_inconclusive_not_success(self):
        reviewers = [gate_reviewer.Reviewer("claude-review", "CLAUDE", FakeReviewer("PASS")),
                     gate_reviewer.Reviewer("gemini-review", "GEMINI", FakeReviewer("PASS", fail_on={"FUNCTIONAL_ADHERENCE"}))]
        decision = self.evaluate("REVIEW", review_artifact(), engine=self.engine(reviewers=reviewers))
        self.assertEqual("INCONCLUSIVE", decision["verdict"])
        self.assertIn("FUNCTIONAL_ADHERENCE", self.failed_ids(decision))
        self.assertNotEqual("PASS", decision["verdict"])

    def test_an_inconclusive_result_is_never_success(self):
        slow = f"{PY} -c \"import time; time.sleep(5)\""
        no_checks = artifact("TEST", acceptance_checks=[])
        decision = self.evaluate(value=no_checks, engine=self.engine(commands={"focused_tests": slow}, timeouts={"focused_tests": 1}),
                                 check_commands={})
        self.assertEqual("INCONCLUSIVE", decision["verdict"])
        self.assertIn("TESTS_PASS", self.failed_ids(decision))
        self.assertIsNone(decision["corrective_task"], "an inconclusive run is not a defect to correct")
        enforced = self.evaluate(value=no_checks, engine=self.engine(commands={"focused_tests": slow}, timeouts={"focused_tests": 1},
                                                                     mode="ENFORCE"),
                                 check_commands={})
        self.assertTrue(enforced["blocking"])
        self.assertFalse(gate_engine.is_success(enforced))

    def test_an_unmet_acceptance_criterion_fails_its_own_unit(self):
        failing = "test -f docs/AC-2-evidence.md"  # an allowlisted verifier that exits 1: the file does not exist
        value = artifact("TEST", acceptance_checks=[check("AC-1"), check("AC-2", command=failing)])
        decision = self.evaluate(value=value, check_commands={"AC-1": TEST_CMD, "AC-2": failing})
        self.assertEqual("FAIL", decision["verdict"])
        failed = {item["id"]: item for item in decision["failed_criteria"]}
        self.assertIn("ACCEPTANCE:AC-2", failed)
        self.assertNotIn("ACCEPTANCE:AC-1", failed)
        self.assertEqual("AC-2", decision["corrective_task"]["unit"])
        self.assertEqual("ACCEPTANCE_UNMET", decision["corrective_task"]["failure_class"])

    def test_a_failure_fixed_in_a_new_generation_passes_and_history_is_kept(self):
        write_app(self.repo, correct=False)
        first = self.evaluate()
        self.assertEqual("FAIL", first["verdict"])
        write_app(self.repo, correct=True)
        second = self.evaluate()
        self.assertEqual("PASS", second["verdict"])
        self.assertEqual(first["generation"] + 1, second["generation"])
        self.assertEqual(first["decision_id"], second["previous_decision_id"])
        history = gate_evidence.DecisionLog(self.store).history("sdd.test", "T-1")
        self.assertEqual([first["decision_id"], second["decision_id"]], [item["decision_id"] for item in history])
        self.assertEqual(["FAIL", "PASS"], [item["verdict"] for item in history])
        gate_evidence.DecisionLog(self.store).verify()


if __name__ == "__main__":
    unittest.main()
