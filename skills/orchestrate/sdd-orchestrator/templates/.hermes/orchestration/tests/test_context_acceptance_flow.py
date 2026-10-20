"""Controller-owned acceptance tests for the context engine: egress, cache, audit, metrics, CLI.

Fake evaluator only; the connector preflight is patched, so no network call is made.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent / "runtime"))

import context_audit  # noqa: E402
import context_engine  # noqa: E402
import context_judge  # noqa: E402
import context_policy  # noqa: E402
import semantic_governor  # noqa: E402
from test_context_acceptance_safety import PROSE, EngineCase, FakeJev, active_config, large_anthropic  # noqa: E402

FIXTURES = TESTS / "fixtures" / "context_engine"


class ConsentAndEgressTests(EngineCase):
    def test_ac_real_evaluator_needs_consent_record(self):
        for content in (None, "# Setup\n\n```yaml\nanswers:\n  typesafe_ai: none\n```\n"):
            with self.subTest(content=content):
                if content is None:
                    self.setup_file.unlink(missing_ok=True)
                else:
                    self.setup_file.write_text(content, encoding="utf-8")
                fake = FakeJev(0.0)
                native = large_anthropic()
                result = self.run_engine(native, active_config(), fake)
                self.assertEqual([], fake.payloads)
                self.assertEqual(native, result["request"])
                self.assertIn("EVALUATOR_UNAVAILABLE", result["metrics"]["evaluation_errors"])

    def test_ac_real_evaluator_needs_ready_preflight(self):
        def blocked(provider, env_file):
            raise semantic_governor.GovernanceError("JEV_GOVERNANCE_PREFLIGHT_FAILED")

        with mock.patch.object(semantic_governor, "_connector_preflight", blocked):
            fake = FakeJev(0.0)
            result = self.run_engine(large_anthropic(), active_config(), fake)
        self.assertEqual([], fake.payloads)
        self.assertIn("EVALUATOR_UNAVAILABLE", result["metrics"]["evaluation_errors"])

    def test_ac_project_setup_is_explicit_not_next_to_env(self):
        fake = FakeJev(0.0)
        raw = json.dumps(large_anthropic()).encode()
        result = context_engine.optimize(raw, "anthropic", "TEST", "t", active_config(), evaluate=fake, apply=True,
                                         audit_dir=str(self.audit), env_file=str(self.env_file), project_setup=None)
        self.assertEqual([], fake.payloads, "no consent path given means no external call")
        self.assertIn("EVALUATOR_UNAVAILABLE", result["metrics"]["evaluation_errors"])

    def test_ac_code_is_not_sent_by_default(self):
        native = large_anthropic()
        code = "```\n" + "\n".join(f"    value_{index} = compute({index})" for index in range(30)) + "\n```\n"
        native["messages"][2]["content"][0]["content"] = code
        fake = FakeJev(0.0)
        self.run_engine(native, active_config(), fake)
        self.assertNotIn("compute(7)", fake.sent_text())
        fake = FakeJev(0.0)
        self.run_engine(native, active_config(send_code_to_evaluator=True), fake)
        self.assertIn("compute(7)", fake.sent_text())

    def test_ac_detected_secrets_and_missing_redaction_block_egress(self):
        native = large_anthropic()
        native["messages"][2]["content"][0]["content"] = PROSE.format(i=2) + " CANARYVALUE"
        fake_redact = lambda text: text.replace("CANARYVALUE", "[REDACTED]")
        with mock.patch.object(context_judge, "redact", fake_redact), mock.patch.object(context_policy, "redact", fake_redact):
            fake = FakeJev(0.0)
            result = self.run_engine(native, active_config(), fake)
        self.assertNotIn("CANARYVALUE", fake.sent_text())
        self.assertIn("CANARYVALUE", json.dumps(result["request"]))
        with mock.patch.object(context_judge, "redact", None), mock.patch.object(context_policy, "redact", None):
            fake = FakeJev(0.0)
            self.run_engine(large_anthropic(), active_config(), fake)
        self.assertEqual([], fake.payloads)

    def test_ac_task_is_redacted_and_bounded(self):
        fake_redact = lambda text: text.replace("CANARYVALUE", "[REDACTED]")
        with mock.patch.object(context_judge, "redact", fake_redact), mock.patch.object(context_policy, "redact", fake_redact):
            fake = FakeJev(0.0)
            self.run_engine(large_anthropic(), active_config(), fake, task="CANARYVALUE " + "x" * 200000)
        self.assertTrue(fake.payloads)
        self.assertNotIn("CANARYVALUE", fake.sent_text())
        for payload in fake.payloads:
            self.assertLessEqual(len(json.dumps(payload).encode()), active_config()["max_payload_bytes"])


class CacheTests(EngineCase):
    def test_ac_cache_hit_then_invalidation(self):
        native = large_anthropic()
        first = FakeJev(0.0)
        self.run_engine(native, active_config(dry_run=True), first)
        self.assertTrue(first.payloads)
        second = FakeJev(0.0)
        result = self.run_engine(native, active_config(dry_run=True), second)
        self.assertEqual([], second.payloads)
        self.assertGreater(result["metrics"]["cache_hits"], 0)
        self.assertEqual(1.0, result["metrics"]["cache_hit_ratio"])
        changed_task = FakeJev(0.0)
        self.run_engine(native, active_config(dry_run=True), changed_task, task="Another task")
        self.assertTrue(changed_task.payloads)
        changed_policy = FakeJev(0.0)
        config = active_config(dry_run=True)
        config["thresholds"] = {"fact_loss_max": 0.1, "constraint_loss_max": 0.1}
        self.run_engine(native, config, changed_policy)
        self.assertTrue(changed_policy.payloads)

    def test_ac_cache_stores_no_raw_text_and_survives_corruption(self):
        native = large_anthropic()
        self.run_engine(native, active_config(dry_run=True), FakeJev(0.0))
        files = [path for path in self.audit.rglob("*") if path.is_file() and "cache" in path.name]
        self.assertTrue(files)
        for path in files:
            self.assertNotIn("weather log", path.read_text(encoding="utf-8", errors="replace"))
            path.write_bytes(b"\xff{corrupt")
        result = self.run_engine(native, active_config(dry_run=True), FakeJev(0.0))
        self.assertEqual("DRY_RUN", result["status"])


class AuditAndRecoveryTests(EngineCase):
    def test_ac_byte_identical_recovery_of_applied_run(self):
        raw = json.dumps(large_anthropic(), indent=1).encode("utf-8")
        result = self.run_engine(raw, active_config(), FakeJev(0.0))
        self.assertEqual("APPLIED", result["status"])
        self.assertIn(result["run_id"], json.dumps(result["request"]))
        self.assertEqual(raw, context_audit.recover(str(self.audit), result["run_id"]))
        for bad in ("../outside", "a/b", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                context_audit.recover(str(self.audit), bad)

    def test_ac_audit_files_are_private(self):
        self.run_engine(large_anthropic(), active_config(), FakeJev(0.0))
        for path in self.audit.rglob("*"):
            mode = stat.S_IMODE(path.stat().st_mode)
            with self.subTest(path=path.name):
                self.assertEqual(0o700 if path.is_dir() else 0o600, mode)

    def test_ac_audit_dir_inside_repository_is_refused(self):
        repo = self.root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        previous = Path.cwd()
        (repo / "sub").mkdir()
        os.chdir(repo / "sub")
        self.addCleanup(os.chdir, previous)
        fake = FakeJev(0.0)
        raw = json.dumps(large_anthropic()).encode()
        result = context_engine.optimize(raw, "anthropic", "TEST", "t", active_config(), evaluate=fake, apply=True,
                                         audit_dir=str(repo / "state"), env_file=str(self.env_file), project_setup=str(self.setup_file))
        self.assertEqual("FAIL_OPEN", result["status"])
        self.assertEqual("AUDIT_DIR_INSIDE_REPOSITORY", result["reason"])
        self.assertEqual([], fake.payloads)


class MetricsTests(EngineCase):
    def test_ac_metrics_are_complete_and_consistent(self):
        from test_context_acceptance_safety import ProtectionTests

        result = self.run_engine(ProtectionTests.protected_session(self), active_config(), FakeJev(0.0))
        metrics = result["metrics"]
        self.assertLess(metrics["input_tokens_after"], metrics["input_tokens_before"])
        self.assertGreater(metrics["evaluator_input_tokens"], 0)
        self.assertGreater(metrics["evaluator_output_tokens"], 0)
        self.assertGreater(metrics["segments_omitted"], 0)
        self.assertGreater(metrics["segments_preserved"], 0)
        self.assertGreaterEqual(metrics["latency_ms"], 0)
        self.assertEqual(metrics["effective_saving"], metrics["input_tokens_before"] - metrics["input_tokens_after"])
        self.assertEqual(metrics["net_saving"], metrics["effective_saving"] - metrics["evaluator_input_tokens"])
        self.assertEqual(sorted(metrics["requirement_impact"]["before"]), sorted(metrics["requirement_impact"]["after"]))
        for key in ("cache_hits", "cache_hit_ratio", "segments_rejected", "evaluation_errors"):
            self.assertIn(key, metrics)

    def test_ac_dry_run_reports_estimated_not_effective_saving(self):
        metrics = self.run_engine(large_anthropic(), active_config(dry_run=True), FakeJev(0.0))["metrics"]
        self.assertEqual(0, metrics["effective_saving"])
        self.assertGreater(metrics["estimated_saving"], 0)


class CliAndCompareTests(EngineCase):
    def cli(self, *argv):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = context_engine.main(list(argv))
        return code, output.getvalue()

    def test_ac_cli_config_defaults_and_errors(self):
        code, text = self.cli("config", "--json")
        self.assertEqual(0, code)
        self.assertFalse(json.loads(text)["enabled"])
        code, _ = self.cli("config", "--config", str(self.root / "missing.md"), "--json")
        self.assertNotEqual(0, code)
        code, _ = self.cli("optimize", "--input", str(self.root / "missing.json"), "--format", "anthropic", "--stage", "TEST", "--task", "t", "--json")
        self.assertNotEqual(0, code)

    def test_ac_cli_optimize_with_shipped_config_writes_original(self):
        source = self.root / "in.json"
        source.write_bytes(json.dumps(large_anthropic()).encode())
        target = self.root / "out.json"
        code, text = self.cli("optimize", "--input", str(source), "--format", "anthropic", "--stage", "TEST", "--task", "t",
                              "--output", str(target), "--audit-dir", str(self.audit), "--json")
        self.assertEqual("DISABLED", json.loads(text)["status"])
        self.assertEqual(source.read_bytes(), target.read_bytes())
        self.assertIn(code, (0, 2))

    def test_ac_compare_corpus_baseline_vs_dry_run_vs_apply(self):
        fixtures = [path for path in FIXTURES.glob("*.json") if path.name != "corpus.json"]
        self.assertGreaterEqual(len(fixtures), 3)
        for path in fixtures:
            self.assertGreater(path.stat().st_size, 2000, f"{path.name} must be a realistic session")
        code, text = self.cli("compare", "--corpus", str(FIXTURES), "--audit-dir", str(self.audit), "--json")
        self.assertEqual(0, code)
        report = json.loads(text)
        self.assertTrue(report["offline"])
        self.assertEqual(len(fixtures), len(report["rows"]))
        for row in report["rows"]:
            with self.subTest(fixture=row["fixture"]):
                self.assertTrue(row["dry_run_equals_baseline"])
                self.assertTrue(row["apply_integrity_ok"])
                self.assertTrue(row["requirements_preserved"])
                self.assertLessEqual(row["apply_tokens"], row["baseline_tokens"])


class SddPromptTests(EngineCase):
    def test_ac_sdd_prompt_keeps_contract_sections(self):
        sections = ["# Objective\nShip the vehicle endpoint.\n", "## Rules\nNever change auth.\n", "## Acceptance\nAC-1 returns 201.\n"]
        notes = "".join(f"## Notes {index}\n{PROSE.format(i=index)}\n" for index in range(120))
        text = "".join(sections) + notes
        config = active_config()
        config["stages"]["TEST"]["eligible_kinds"] = ["SDD_SECTION"]
        result = self.run_engine(text.encode("utf-8"), config, FakeJev(0.0), fmt="sdd-prompt")
        self.assertEqual("APPLIED", result["status"])
        for section in sections:
            self.assertIn(section, result["request"])
        self.assertIn("context-engine omitted segment", result["request"])


if __name__ == "__main__":
    unittest.main()
