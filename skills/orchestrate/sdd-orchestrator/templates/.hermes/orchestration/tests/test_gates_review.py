"""Regressions from the PR #62 review: forged proof, tampered reuse, hardened git, and rules the mutants showed untested.

Do not edit, skip or weaken these tests; the implementation must satisfy them with real logic.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate_fixtures import ANALYZE_CMD, PY, TEST_CMD, FakeReviewer, artifact, check, git, make_repo, review_artifact, write_app  # noqa: E402

import gate_engine  # noqa: E402
import gate_evidence  # noqa: E402
import gate_reviewer  # noqa: E402
import gate_stages  # noqa: E402
import gate_validator  # noqa: E402
import gate_workspace  # noqa: E402

MISSING_MARKER = "grep -q REQUIRED_MARKER README.md"  # allowlisted; README.md never contains the marker
FORGED_WAIVER = {"by": "worker", "reason": "not needed", "quote": "skip it", "recorded_at": "2026-10-21T00:00:00Z"}


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="gate-review-")
        self.root = Path(self._tmp.name)
        self.repo = make_repo(self.root)
        self.store = self.root / "store"
        self.reviewer = FakeReviewer()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def engine(self, reviewers=None, **kwargs):
        commands = {"focused_tests": TEST_CMD, "analyze": ANALYZE_CMD, "format": None}
        commands.update(kwargs.pop("commands", {}))
        reviewers = [gate_reviewer.Reviewer("claude-review", "CLAUDE", self.reviewer)] if reviewers is None else reviewers
        return gate_engine.GateEngine(self.store, self.repo, commands=commands, reviewers=reviewers, **kwargs)

    def evaluate(self, stage, value, engine=None, **context):
        context.setdefault("check_commands", {"AC-1": TEST_CMD})
        context.setdefault("implementer", "CODEX")
        return (engine or self.engine()).evaluate(stage, "T-1", value, context=context)

    def status(self, decision):
        return {item["id"]: item["status"] for item in decision["criteria"]}


class ForgedProofTests(Base):
    def test_a_waiver_the_worker_wrote_is_not_a_pass(self):
        forged = dict(check("AC-2", "WAIVED", command=MISSING_MARKER, evidence=None), waiver=FORGED_WAIVER)
        value = artifact("TEST", acceptance_checks=[check("AC-1"), forged])
        decision = self.evaluate("TEST", value, check_commands={"AC-1": TEST_CMD, "AC-2": MISSING_MARKER})
        self.assertNotEqual("PASS", decision["verdict"])
        self.assertEqual("BLOCKED", self.status(decision)["ACCEPTANCE:AC-2"])
        recorded = self.evaluate("TEST", value, check_commands={"AC-1": TEST_CMD}, waivers={"AC-2": {"by": "owner", "decision": "D-7"}})
        self.assertEqual("PASS", recorded["verdict"], recorded["failed_criteria"])

    def test_a_check_the_worker_drops_is_a_failure(self):
        bound = {"AC-1": TEST_CMD, "AC-2": MISSING_MARKER}
        for checks in ([check("AC-1")], []):
            decision = self.evaluate("TEST", artifact("TEST", acceptance_checks=checks), check_commands=bound)
            self.assertEqual("FAIL", decision["verdict"])
            self.assertEqual("FAIL", self.status(decision)["ACCEPTANCE:AC-2"])

    def test_review_reruns_the_tests_instead_of_trusting_gate_status(self):
        write_app(self.repo, correct=False)
        value = review_artifact()
        value["review_result"]["acceptance"]["checks"] = []
        decision = self.evaluate("REVIEW", value)
        self.assertEqual("FAIL", decision["verdict"])
        status = self.status(decision)
        self.assertEqual("FAIL", status["TESTS_PASS"])
        self.assertEqual("FAIL", status["ACCEPTANCE:AC-1"], "an expected check missing from the review fails")
        self.assertEqual([], self.reviewer.contexts, "a reviewer is not asked once a deterministic criterion failed")

    def test_review_rejects_self_reported_failures(self):
        cases = {"RECORDED_GATES_PASS": review_artifact(gate_status={"focused_tests": "FAIL", "format": "PASS", "analyze": "PASS", "ci": "PENDING"}),
                 "BASELINE_AND_OWNERSHIP": review_artifact(baseline={"preserved": False, "violations": [{"path": "README.md"}]}),
                 "REVIEW_APPROVED": review_artifact(findings=[{"severity": "HIGH", "path": "app.py", "description": "d", "evidence": "e"}])}
        for criterion, value in cases.items():
            with self.subTest(criterion):
                self.assertEqual("FAIL", self.status(self.evaluate("REVIEW", value))[criterion])

    def test_an_uncovered_agent_check_fails_checks_covered(self):
        value = artifact("TEST", acceptance_checks=[check("AC-1"), check("AC-3", command=MISSING_MARKER)])
        decision = self.evaluate("TEST", value, check_commands={"AC-1": TEST_CMD})
        self.assertEqual("FAIL", self.status(decision)["CHECKS_COVERED"])


class ReviewerTests(Base):
    def test_an_unknown_implementer_blocks_agent_criteria(self):
        value = review_artifact()
        decision = self.engine().evaluate("REVIEW", "T-1", value, context={"check_commands": {"AC-1": TEST_CMD}})
        self.assertEqual("BLOCKED", self.status(decision)["INDEPENDENT_REVIEWER"])
        self.assertEqual([], self.reviewer.contexts)

    def test_the_artifact_executor_counts_as_implementer_and_families_are_normalised(self):
        plan = artifact("PLAN", commands=[], acceptance_checks=[check("AC-1", "PLANNED", evidence=None)])
        same = [gate_reviewer.Reviewer("codex-r", "codex-cli", FakeReviewer())]
        # The context names another family; the artifact's executor (CODEX) still excludes the codex reviewer.
        decision = self.engine(reviewers=same).evaluate("PLAN", "T-1", plan, context={"implementer": "CLAUDE"})
        self.assertEqual("BLOCKED", self.status(decision)["INDEPENDENT_REVIEWER"])

    def test_a_cited_ref_outside_the_context_is_not_evidence(self):
        def outsider(context):
            return {"criteria": [{"id": item["id"], "verdict": "PASS", "evidence_refs": ["sha256:" + "0" * 64]}
                                 for item in context["criteria"]]}

        decision = self.evaluate("REVIEW", review_artifact(), engine=self.engine([gate_reviewer.Reviewer("c", "CLAUDE", outsider)]))
        self.assertEqual("INCONCLUSIVE", decision["verdict"])

    def test_oversized_evidence_is_trimmed_below_the_cap(self):
        original = gate_reviewer.MAX_CONTEXT_BYTES
        gate_reviewer.MAX_CONTEXT_BYTES = 4096  # below 24 full summaries, so trimming must happen
        self.addCleanup(setattr, gate_reviewer, "MAX_CONTEXT_BYTES", original)
        evidence = [(f"sha256:{index:064x}", {"kind": "command", "command": "x" * 2000, "detail": "y" * 2000}) for index in range(24)]
        gate = {"gate_id": "sdd.review", "stage": "REVIEW"}
        context = gate_reviewer.build_context(gate, "T-1", [{"id": "SECURITY", "description": "d"}], evidence)
        self.assertLessEqual(len(json.dumps(context).encode("utf-8")), gate_reviewer.MAX_CONTEXT_BYTES)
        self.assertTrue(context["evidence"])
        self.assertLess(len(context["evidence"]), len(evidence))


class StoreAndReuseTests(Base):
    def test_a_tampered_log_is_never_reused(self):
        write_app(self.repo, correct=False)
        first = self.evaluate("TEST", artifact("TEST"))
        self.assertEqual("FAIL", first["verdict"])
        path = gate_evidence.DecisionLog(self.store).path
        lines = path.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[-1])
        record.update(verdict="PASS", failed_criteria=[], blocking=False)
        path.write_text("\n".join(lines[:-1] + [json.dumps(record)]) + "\n", encoding="utf-8")
        with self.assertRaises(gate_evidence.EvidenceError) as raised:
            self.evaluate("TEST", artifact("TEST"))
        self.assertEqual("DECISION_LOG_TAMPERED", raised.exception.code)

    def test_append_verifies_the_chain(self):
        self.evaluate("TEST", artifact("TEST"))
        log = gate_evidence.DecisionLog(self.store)
        record = json.loads(log.path.read_text(encoding="utf-8"))
        record["verdict"] = "FAIL"
        log.path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        with self.assertRaises(gate_evidence.EvidenceError):
            log.append({"decision_id": "gd-x"})

    def test_a_pass_with_only_the_artifact_as_evidence_is_inconclusive(self):
        original = gate_stages.evaluate_stage
        gate_stages.evaluate_stage = lambda *args: [{"id": "SCHEMA_VALID", "status": "PASS", "refs": [], "detail": ""}]
        self.addCleanup(setattr, gate_stages, "evaluate_stage", original)
        decision = self.evaluate("TASKS", artifact("TASKS"))
        self.assertEqual("INCONCLUSIVE", decision["verdict"])
        self.assertIn("EVIDENCE_PRESENT", {item["id"] for item in decision["failed_criteria"]})

    def test_redaction_happens_before_truncation(self):
        secret = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1L2M3N4O5P6Q7R8"
        with tempfile.TemporaryFile() as handle:
            handle.write(("x" * 10 + " " + secret + " " + "y" * (gate_validator.OUTPUT_LIMIT - 20)).encode())
            tail = gate_validator._tail(handle)
        self.assertNotIn(secret[-16:], tail, "the part of a secret that survives the cut is masked")


class WorkspaceTests(Base):
    def test_repository_fsmonitor_does_not_run_and_no_host_env_reaches_git(self):
        marker = self.root / "fsmonitor-ran"
        hook = self.repo / ".git" / "fsm.sh"
        hook.write_text(f"#!/bin/sh\necho ran > {marker}\nexit 1\n", encoding="utf-8")
        os.chmod(hook, 0o700)
        git(self.repo, "config", "core.fsmonitor", str(hook))
        self.assertIsNotNone(gate_workspace.changed_files(self.repo))
        self.assertFalse(marker.exists())
        self.assertNotIn("GATE_SECRET", gate_workspace.git_env())

    def test_a_rename_reports_both_paths(self):
        (self.repo / "protected").mkdir()
        (self.repo / "protected" / "policy.md").write_text("p\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "policy")
        subprocess.run(["git", "-C", str(self.repo), "checkout", "-q", "--", "app.py"], check=True)
        (self.repo / "src").mkdir()
        git(self.repo, "mv", "protected/policy.md", "src/policy.md")
        self.assertLessEqual({"protected/policy.md", "src/policy.md"}, set(gate_workspace.changed_files(self.repo)))
        value = artifact("IMPLEMENT", modified_paths=["src/policy.md"])
        decision = self.evaluate("IMPLEMENT", value, editable_paths=["src/**", "app.py"])
        self.assertEqual("FAIL", self.status(decision)["WITHIN_EDITABLE_PATHS"])

    def test_a_git_failure_is_inconclusive_not_clean(self):
        not_a_repo = self.root / "plain"
        not_a_repo.mkdir()
        engine = gate_engine.GateEngine(self.store, not_a_repo, commands={"analyze": f"{PY} -c pass"}, reviewers=[])
        decision = engine.evaluate("IMPLEMENT", "T-1", artifact("IMPLEMENT"), context={"editable_paths": ["app.py"]})
        self.assertEqual("INCONCLUSIVE", self.status(decision)["CLAIMS_MATCH_WORKSPACE"])
        self.assertTrue(decision["retryable"])

    def test_reproduced_claims_are_capped(self):
        claims = [{"command": f"test -f file{index}.txt", "purpose": "p", "timeout_seconds": 5, "exit_code": 0, "result": "PASS"}
                  for index in range(gate_validator.MAX_REPRODUCED + 5)]
        decision = self.evaluate("TEST", artifact("TEST", commands=claims, acceptance_checks=[]), check_commands={})
        runs = [gate_evidence.EvidenceCollector(self.store).get(ref) for ref in decision["evidence_refs"]]
        reproduced = [item for item in runs if item.get("purpose") == "reproduce worker claim"]
        self.assertEqual(gate_validator.MAX_REPRODUCED, len(reproduced))


if __name__ == "__main__":
    unittest.main()
