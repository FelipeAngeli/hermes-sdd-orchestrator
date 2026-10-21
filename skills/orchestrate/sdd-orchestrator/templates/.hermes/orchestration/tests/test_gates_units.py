"""Unit tests for gate internals: decision schema, corrective tasks, evidence store, environment, CLI."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate_fixtures import ANALYZE_CMD, PY, RUNTIME, TEST_CMD, FakeReviewer, artifact, make_repo, review_artifact, write_app  # noqa: E402

import jsonschema  # noqa: E402

import gate_cli  # noqa: E402
import gate_corrections  # noqa: E402
import gate_engine  # noqa: E402
import gate_evidence  # noqa: E402
import gate_reviewer  # noqa: E402

SCHEMA = json.loads((RUNTIME.parent / "schemas" / "GATE_DECISION_SCHEMA.json").read_text(encoding="utf-8"))


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="gate-units-")
        self.root = Path(self._tmp.name)
        self.repo = make_repo(self.root)
        self.store = self.root / "store"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def engine(self, **kwargs):
        commands = {"focused_tests": TEST_CMD, "analyze": ANALYZE_CMD, "format": None}
        commands.update(kwargs.pop("commands", {}))
        reviewers = kwargs.pop("reviewers", [gate_reviewer.Reviewer("claude-review", "CLAUDE", FakeReviewer())])
        return gate_engine.GateEngine(self.store, self.repo, commands=commands, reviewers=reviewers, **kwargs)


class DecisionUnits(Base):
    def test_every_recorded_decision_matches_the_schema(self):
        engine = self.engine()
        engine.evaluate("TEST", "T-1", artifact("TEST"), context={"check_commands": {"AC-1": TEST_CMD}})
        write_app(self.repo, correct=False)
        engine.evaluate("TEST", "T-1", artifact("TEST"), context={"check_commands": {"AC-1": TEST_CMD}})
        returned = engine.evaluate("REVIEW", "T-1", review_artifact(), context={"check_commands": {"AC-1": TEST_CMD}})
        jsonschema.validate(returned, SCHEMA)  # the returned value (with ``reused``) matches the contract too
        engine.evaluate("SPECIFY", "T-1", {"not": "an artifact"})
        records = gate_evidence.DecisionLog(self.store).records()
        self.assertEqual(4, len(records))
        for record in records:
            jsonschema.validate(record, SCHEMA)
            self.assertNotIn("reused", record)
        self.assertEqual("FAIL", records[-1]["verdict"], "an invalid artifact fails its schema criterion")

    def test_inconclusive_and_blocked_produce_no_corrective_task(self):
        self.assertIsNone(gate_corrections.corrective_task("TEST", [{"id": "TESTS_PASS", "status": "INCONCLUSIVE", "detail": ""}]))
        self.assertIsNone(gate_corrections.corrective_task("TEST", [{"id": "TESTS_CONFIGURED", "status": "BLOCKED", "detail": ""}]))
        task = gate_corrections.corrective_task("PLAN", [{"id": "IMPACT_FILES_EXIST", "status": "FAIL", "detail": "missing: x"}])
        self.assertEqual("PLAN", task["stage"])
        self.assertEqual("ARTIFACT_INCOMPLETE", task["failure_class"])

    def test_the_most_specific_failure_leads_the_corrective_task(self):
        failed = [{"id": "EVIDENCE_REPRODUCED", "status": "FAIL", "detail": ""},
                  {"id": "TESTS_PASS", "status": "FAIL", "detail": "`python check.py`: exit 1"}]
        task = gate_corrections.corrective_task("TEST", failed)
        self.assertEqual("TEST_FAILURE", task["failure_class"])
        self.assertEqual("python check.py", task["unit"])

    def test_a_reviewer_exception_is_inconclusive(self):
        def broken(context):
            raise RuntimeError("model unavailable")

        engine = self.engine(reviewers=[gate_reviewer.Reviewer("claude-review", "CLAUDE", broken)])
        context = {"check_commands": {"AC-1": TEST_CMD}, "implementer": "CODEX"}
        decision = engine.evaluate("REVIEW", "T-1", review_artifact(), context=context)
        self.assertEqual("INCONCLUSIVE", decision["verdict"])
        self.assertTrue(decision["retryable"], "a reviewer error is transient")
        again = engine.evaluate("REVIEW", "T-1", review_artifact(), context=context)
        self.assertFalse(again["reused"], "a transient INCONCLUSIVE is evaluated again, not reused")
        self.assertEqual(2, again["generation"])

    def test_a_changed_workspace_is_a_new_decision(self):
        engine = self.engine()
        first = engine.evaluate("TEST", "T-1", artifact("TEST"), context={"check_commands": {"AC-1": TEST_CMD}})
        (self.repo / "notes.txt").write_text("new evidence\n", encoding="utf-8")
        second = engine.evaluate("TEST", "T-1", artifact("TEST"), context={"check_commands": {"AC-1": TEST_CMD}})
        self.assertNotEqual(first["decision_id"], second["decision_id"])
        self.assertEqual(2, second["generation"])


class EvidenceUnits(Base):
    def test_the_store_is_private_and_evidence_is_redacted(self):
        secret = "sk-ant-" + "a" * 40
        command = f"{PY} -c \"print('{secret}')\""
        decision = self.engine(commands={"focused_tests": command}).evaluate("TEST", "T-1", artifact("TEST", acceptance_checks=[]),
                                                                            context={"check_commands": {}})
        self.assertEqual(0o700, stat.S_IMODE(os.stat(self.store).st_mode))
        blobs = list((self.store / "evidence").rglob("*.json"))
        self.assertTrue(blobs)
        self.assertTrue(all(stat.S_IMODE(os.stat(path).st_mode) == 0o600 for path in blobs))
        self.assertFalse(any(secret in path.read_text(encoding="utf-8") for path in blobs))
        self.assertNotIn(secret, (self.store / "decisions.jsonl").read_text(encoding="utf-8"))
        self.assertNotIn(secret, json.dumps(decision))
        self.assertTrue(decision["evidence_refs"])

    def test_gate_commands_get_a_minimal_environment(self):
        os.environ["GATE_TEST_SECRET"] = "leak-me"
        self.addCleanup(os.environ.pop, "GATE_TEST_SECRET", None)
        command = f"{PY} -c \"import os, sys; sys.exit(1 if 'GATE_TEST_SECRET' in os.environ else 0)\""
        decision = self.engine(commands={"focused_tests": command}).evaluate("TEST", "T-1", artifact("TEST", acceptance_checks=[]),
                                                                            context={"check_commands": {}})
        status = {item["id"]: item["status"] for item in decision["criteria"]}
        self.assertEqual("PASS", status["TESTS_PASS"])

    def test_no_shell_is_used(self):
        marker = self.root / "pwned"
        command = f"{PY} check_total.py; touch {marker}"
        self.engine(commands={"focused_tests": command}).evaluate("TEST", "T-1", artifact("TEST", acceptance_checks=[]),
                                                                  context={"check_commands": {}})
        self.assertFalse(marker.exists())


class CliUnits(Base):
    def cli(self, *args):
        completed = subprocess.run([sys.executable, str(RUNTIME / "gate_cli.py"), *args, "--json"], capture_output=True, text=True, timeout=120)
        return completed.returncode, json.loads(completed.stdout)

    def test_observe_never_blocks_and_enforce_exits_3(self):
        gates = self.root / "GATES.md"
        gates.write_text(f"| Gate | Command | Executor | Timeout |\n| --- | --- | --- | --- |\n"
                         f"| Focused tests | `{TEST_CMD}` | host | 60 s |\n| Analyze | `{ANALYZE_CMD}` | host | 60 s |\n", encoding="utf-8")
        self.assertEqual(TEST_CMD, gate_cli.gate_commands(gates)[0]["focused_tests"])
        write_app(self.repo, correct=False)
        value = self.root / "artifact.json"
        value.write_text(json.dumps(artifact("TEST")), encoding="utf-8")
        context = self.root / "context.json"
        context.write_text(json.dumps({"check_commands": {"AC-1": TEST_CMD}}), encoding="utf-8")
        common = ["evaluate", "--stage", "TEST", "--task", "T-9", "--artifact", str(value), "--store", str(self.store),
                  "--repo", str(self.repo), "--gates", str(gates), "--context", str(context)]
        code, payload = self.cli(*common)
        self.assertEqual((0, "FAIL", False), (code, payload["verdict"], payload["blocking"]))
        code, payload = self.cli(*common, "--enforce")
        self.assertEqual((3, True), (code, payload["blocking"]))
        self.assertEqual((0, "OK"), (self.cli("verify", "--store", str(self.store))[0], self.cli("verify", "--store", str(self.store))[1]["status"]))
        code, payload = self.cli("history", "--store", str(self.store), "--gate", "sdd.test", "--task", "T-9")
        self.assertEqual(["FAIL", "FAIL"], [item["verdict"] for item in payload["decisions"]])
        self.assertEqual(0, self.cli("registry")[0])


if __name__ == "__main__":
    unittest.main()
