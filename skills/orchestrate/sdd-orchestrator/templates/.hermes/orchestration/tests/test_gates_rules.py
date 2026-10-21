"""Controller-owned acceptance tests for the gate business rules, per-stage gates and loop-engine integration.

Do not edit, skip or weaken these tests; the implementation must satisfy them with real logic.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate_fixtures import ANALYZE_CMD, TEST_CMD, FakeReviewer, artifact, check, make_repo, review_artifact, write_app  # noqa: E402

import gate_engine  # noqa: E402
import gate_evidence  # noqa: E402
import gate_registry  # noqa: E402
import gate_reviewer  # noqa: E402
import loop_engine  # noqa: E402
import loop_sdd  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="gate-rules-")
        root = Path(self._tmp.name)
        self.repo = make_repo(root)
        self.store = root / "store"
        self.reviewer = FakeReviewer()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def engine(self, *, reviewers=None, mode="OBSERVE", commands=None):
        configured = {"focused_tests": TEST_CMD, "analyze": ANALYZE_CMD, "format": None}
        configured.update(commands or {})
        if reviewers is None:
            reviewers = [gate_reviewer.Reviewer("claude-review", "CLAUDE", self.reviewer)]
        return gate_engine.GateEngine(self.store, self.repo, commands=configured, reviewers=reviewers, mode=mode)

    def evaluate(self, stage, value, engine=None, **context):
        context.setdefault("check_commands", {"AC-1": TEST_CMD})
        return (engine or self.engine()).evaluate(stage, "T-1", value, context=context)

    def ids(self, decision):
        return {item["id"] for item in decision["failed_criteria"]}


class RegistryTests(Base):
    def test_every_sdd_stage_has_a_gate_with_explicit_criteria(self):
        registry = gate_registry.GateRegistry()
        for stage in ("SPECIFY", "CLARIFY", "PLAN", "TASKS", "IMPLEMENT", "TEST", "REVIEW"):
            gate = registry.for_stage(stage)
            self.assertEqual(f"sdd.{stage.lower()}", gate["gate_id"])
            self.assertTrue(gate["criteria"])
            for criterion in gate["criteria"]:
                self.assertIn(criterion["evaluator"], {"DETERMINISTIC", "AGENT"})
        test_gate = registry.for_stage("TEST")
        self.assertTrue(all(item["evaluator"] == "DETERMINISTIC" for item in test_gate["criteria"] if item["id"].startswith("TESTS")),
                        "automated tests are never replaced by model judgement")
        self.assertEqual(registry.digest(), gate_registry.GateRegistry().digest())


class RuleTests(Base):
    def test_a_textual_claim_is_not_proof(self):
        claim = artifact("TEST", acceptance_checks=[check("AC-1", evidence="All tests pass, I checked carefully.")])
        decision = self.evaluate("TEST", claim, check_commands={})
        self.assertNotEqual("PASS", decision["verdict"])
        self.assertIn("ACCEPTANCE:AC-1", self.ids(decision))

    def test_a_pass_always_points_to_stored_evidence(self):
        decision = self.evaluate("TEST", artifact("TEST"))
        self.assertEqual("PASS", decision["verdict"])
        collector = gate_evidence.EvidenceCollector(self.store)
        for ref in decision["evidence_refs"]:
            record = collector.get(ref)
            self.assertEqual(ref, gate_evidence.content_ref(record))

    def test_the_model_never_overrules_a_failing_test(self):
        write_app(self.repo, correct=False)
        decision = self.evaluate("TEST", artifact("TEST"), engine=self.engine(reviewers=[
            gate_reviewer.Reviewer("claude-review", "CLAUDE", FakeReviewer("PASS"))]))
        self.assertEqual("FAIL", decision["verdict"])

    def test_same_evidence_is_idempotent_and_not_reviewed_again(self):
        first = self.evaluate("REVIEW", review_artifact())
        second = self.evaluate("REVIEW", review_artifact())
        self.assertEqual(first["decision_id"], second["decision_id"])
        self.assertTrue(second["reused"])
        self.assertEqual(1, len(self.reviewer.contexts), "no new evidence, no new review")
        self.assertEqual(1, len(gate_evidence.DecisionLog(self.store).history("sdd.review", "T-1")))

    def test_the_implementer_alone_cannot_review(self):
        reviewers = [gate_reviewer.Reviewer("codex-impl", "CODEX", FakeReviewer("PASS"))]
        decision = self.evaluate("REVIEW", review_artifact(), engine=self.engine(reviewers=reviewers), implementer="CODEX")
        self.assertEqual("BLOCKED", decision["verdict"])
        self.assertIn("INDEPENDENT_REVIEWER", self.ids(decision))

    def test_reviewers_get_minimal_context(self):
        self.evaluate("REVIEW", review_artifact(), conversation="secret chat history", repo_dump="x" * 50_000)
        context = self.reviewer.contexts[0]
        self.assertLessEqual(set(context), {"gate_id", "stage", "task_id", "criteria", "evidence", "limits"})
        text = json.dumps(context)
        self.assertNotIn("secret chat history", text)
        self.assertLess(len(text.encode()), gate_reviewer.MAX_CONTEXT_BYTES)

    def test_a_reviewer_pass_without_cited_evidence_is_inconclusive(self):
        reviewers = [gate_reviewer.Reviewer("claude-review", "CLAUDE", FakeReviewer("PASS", cite=False))]
        decision = self.evaluate("REVIEW", review_artifact(), engine=self.engine(reviewers=reviewers))
        self.assertEqual("INCONCLUSIVE", decision["verdict"])

    def test_decisions_and_evidence_are_tamper_evident(self):
        decision = self.evaluate("TEST", artifact("TEST"))
        log = gate_evidence.DecisionLog(self.store)
        path = log.path
        lines = path.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[-1])
        record["verdict"] = "FAIL"
        path.write_text("\n".join(lines[:-1] + [json.dumps(record)]) + "\n", encoding="utf-8")
        with self.assertRaises(gate_evidence.EvidenceError):
            log.verify()
        ref = decision["evidence_refs"][0]
        blob = gate_evidence.EvidenceCollector(self.store).path_for(ref)
        blob.write_text("{}", encoding="utf-8")
        with self.assertRaises(gate_evidence.EvidenceError):
            gate_evidence.EvidenceCollector(self.store).get(ref)


class StageTests(Base):
    def test_specify_needs_scope_and_acceptance_criteria(self):
        good = artifact("SPECIFY", acceptance_checks=[check("AC-1", "PLANNED", evidence=None)], commands=[])
        self.assertEqual("PASS", self.evaluate("SPECIFY", good)["verdict"])
        bad = artifact("SPECIFY", acceptance_checks=[], summary="", commands=[])
        decision = self.evaluate("SPECIFY", bad)
        self.assertEqual("FAIL", decision["verdict"])
        self.assertLessEqual({"ACCEPTANCE_CRITERIA_DEFINED", "SCOPE_STATED"}, self.ids(decision))

    def test_clarify_blocks_on_open_material_questions(self):
        open_question = artifact("CLARIFY", commands=[], acceptance_checks=[check("AC-1", "PLANNED", evidence=None)],
                                 context_assessment={"facts": [], "assumptions": [], "unresolved_questions": [
                                     {"question": "Which currency?", "material": True, "impact": "pricing"}]})
        decision = self.evaluate("CLARIFY", open_question)
        self.assertEqual("BLOCKED", decision["verdict"])
        self.assertIn("NO_OPEN_MATERIAL_QUESTIONS", self.ids(decision))

    def test_plan_checks_that_impact_files_exist(self):
        plan = artifact("PLAN", commands=[], acceptance_checks=[check("AC-1", "PLANNED", evidence=None)], impact_files=["nope/missing.py"])
        self.assertIn("IMPACT_FILES_EXIST", self.ids(self.evaluate("PLAN", plan)))

    def test_tasks_need_traceable_slices(self):
        planned = check("AC-1", "PLANNED", evidence=None, slice_id=None)
        tasks = artifact("TASKS", commands=[], acceptance_checks=[planned], tasks=[])
        decision = self.evaluate("TASKS", tasks)
        self.assertLessEqual({"TASKS_DEFINED", "CHECKS_TRACED_TO_SLICES"}, self.ids(decision))

    def test_implement_checks_scope_and_static_quality(self):
        write_app(self.repo, correct=True)
        (self.repo / "README.md").write_text("changed outside scope\n", encoding="utf-8")
        value = artifact("IMPLEMENT", modified_paths=["app.py", "README.md"])
        decision = self.evaluate("IMPLEMENT", value, editable_paths=["app.py"])
        self.assertEqual("FAIL", decision["verdict"])
        self.assertIn("WITHIN_EDITABLE_PATHS", self.ids(decision))
        (self.repo / "README.md").write_text("# demo\nTotals.\n", encoding="utf-8")
        (self.repo / "app.py").write_text("def total(values:\n", encoding="utf-8")
        decision = self.evaluate("IMPLEMENT", artifact("IMPLEMENT", modified_paths=["app.py"]), editable_paths=["app.py"])
        self.assertIn("STATIC_ANALYSIS", self.ids(decision))

    def test_review_rejects_changes_required(self):
        decision = self.evaluate("REVIEW", review_artifact(status="CHANGES_REQUIRED", findings=[
            {"severity": "HIGH", "path": "app.py", "description": "no input check", "evidence": "app.py:2"}]))
        self.assertEqual("FAIL", decision["verdict"])
        self.assertIn("REVIEW_APPROVED", self.ids(decision))


class IntegrationTests(Base):
    def test_observe_mode_records_but_never_blocks_the_loop(self):
        write_app(self.repo, correct=False)
        gates = self.engine(mode="OBSERVE")
        calls = []

        def stage_handler(ctx):
            calls.append(ctx.node["id"])
            stage = ctx.node["id"]
            value = review_artifact() if stage == "REVIEW" else artifact(stage)
            return {"status": "SUCCEEDED", "output": {"artifact": value}}

        handlers = {stage: gate_engine.gated_handler(gates, stage_handler, task_id="T-1",
                                                     context={"check_commands": {"AC-1": TEST_CMD}, "editable_paths": ["app.py"]})
                    for stage in loop_sdd.SDD_STAGES}
        engine = loop_engine.LoopEngine(Path(self._tmp.name) / "loop", handlers=handlers)
        execution = engine.run(engine.start(loop_sdd.build_definition(ticket="T-1", objective="o"))["execution_id"])
        self.assertEqual("COMPLETED", execution["status"])
        self.assertEqual("FAIL", execution["nodes"]["TEST"]["output"]["gate"]["verdict"])
        self.assertFalse(execution["nodes"]["TEST"]["output"]["gate"]["blocking"])

    def test_enforce_mode_turns_a_failure_into_a_loop_correction(self):
        gates = self.engine(mode="ENFORCE")
        attempts = {"IMPLEMENT": 0}

        def stage_handler(ctx):
            stage = ctx.node["id"]
            if stage == "IMPLEMENT":
                attempts["IMPLEMENT"] += 1
                write_app(self.repo, correct=attempts["IMPLEMENT"] > 1)
                if ctx.correction:
                    self.assertIn("TESTS_PASS", ctx.correction["failed_checks"])
            value = review_artifact() if stage == "REVIEW" else artifact(stage, modified_paths=["app.py"] if stage == "IMPLEMENT" else [])
            return {"status": "SUCCEEDED", "output": {"artifact": value, "diff": attempts["IMPLEMENT"]}}

        handlers = {stage: gate_engine.gated_handler(gates, stage_handler, task_id="T-1",
                                                     context={"check_commands": {"AC-1": TEST_CMD}, "editable_paths": ["app.py"]})
                    for stage in loop_sdd.SDD_STAGES}
        engine = loop_engine.LoopEngine(Path(self._tmp.name) / "loop", handlers=handlers)
        execution = engine.run(engine.start(loop_sdd.build_definition(ticket="T-1", objective="o"))["execution_id"])
        self.assertEqual("COMPLETED", execution["status"], execution["halt"])
        self.assertEqual(2, attempts["IMPLEMENT"])
        history = gate_evidence.DecisionLog(self.store).history("sdd.test", "T-1")
        self.assertEqual(["FAIL", "PASS"], [item["verdict"] for item in history])


if __name__ == "__main__":
    unittest.main()
