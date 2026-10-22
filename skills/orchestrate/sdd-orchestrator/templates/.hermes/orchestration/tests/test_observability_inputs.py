"""Observability engine against hostile or partial inputs: store shapes, sizes, symlinks and executor envelopes."""
from __future__ import annotations

import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from obs_fixtures import LoopFixture  # noqa: E402

import obs_cli  # noqa: E402
import obs_metrics  # noqa: E402
import obs_model  # noqa: E402
import obs_tracer  # noqa: E402


class HostileStoreTests(LoopFixture):
    """Round-1 review: any hostile store content becomes SOURCE_UNREADABLE and never hides another source."""

    def journal(self) -> Path:
        path = self.root / "journal.json"
        path.write_text(json.dumps({"action": {"ticket": "T", "id": "a1", "status": "RELEASED"}}))
        return path

    def assert_isolated(self, **sources):
        events = obs_tracer.collect(journals=[self.journal()], **sources)
        kinds = [item["event_type"] for item in events]
        self.assertIn("ACTION", kinds)
        self.assertIn("SOURCE_UNREADABLE", kinds)
        json.dumps(obs_metrics.report(events, obs_model.load_prices()), allow_nan=False)

    def loop_dir(self, execution, events_lines=None, definition=None) -> Path:
        directory = self.store / "x1"
        directory.mkdir(parents=True)
        (directory / "execution.json").write_text(execution)
        (directory / "definition.json").write_text(definition or "{}")
        if events_lines is not None:
            (directory / "events.jsonl").write_text(events_lines)
        return self.store

    def test_non_dict_and_deep_documents(self):
        for execution in ("[]", "[" * 100_000 + "]" * 100_000, '{"nodes": [1]}', '{"nodes": {"A": {"attempts": [1, "x"]}}}'):
            with self.subTest(execution=execution[:20]):
                shutil.rmtree(self.store, ignore_errors=True)
                events = obs_tracer.collect(loop_store=[self.loop_dir(execution)], journals=[self.journal()])
                self.assertIn("ACTION", [item["event_type"] for item in events])

    def test_bad_event_lines_are_counted_not_fatal(self):
        store = self.loop_dir("{}", '[1,2]\n{"event": "X", "node": [1]}\n{"event": "NODE_STARTED", "node": "A"}\n')
        kinds = [item["event_type"] for item in obs_tracer.loop_store_events(store)]
        self.assertEqual(1, kinds.count("LOOP_EVENT_UNREADABLE"))
        self.assertIn("LOOP_NODE_STARTED", kinds)

    def test_hostile_journal_and_reports(self):
        bad = self.root / "bad.json"
        bad.write_text("[1]")
        report = self.root / "report.json"
        report.write_text(json.dumps({"provenance": "LIVE_JEV", "usage": {"input_tokens": 5, "output_tokens": 1, "source": "api",
                                                                           "method": "x"}}))
        events = obs_tracer.collect(journals=[self.journal(), bad], governor_reports=[report])
        jev = [item for item in events if item["event_type"] == "JEV_DECISION"]
        self.assertEqual(6, jev[0]["attrs"]["tokens"]["total_tokens"])
        self.assertIn("ACTION", [item["event_type"] for item in events])

    def test_non_numeric_and_non_finite_values_never_reach_arithmetic(self):
        log = self.root / "hsm.jsonl"
        log.write_text("[1]\n" + json.dumps({"k": "tool_call", "tool": "t", "dur_s": "slow"}) + "\n"
                       + '{"k": "api_call", "model": "m", "in": 10, "out": 1, "latency_s": 1e999}\n')
        events = obs_cli.hsm_events(log)
        self.assertEqual(1, sum(1 for item in events if item["event_type"] == "SOURCE_UNREADABLE"))
        state = self.root / "ctx" / "audit" / "1_r"
        state.mkdir(parents=True)
        (state / "metrics.json").write_text(json.dumps({"latency_ms": "x", "estimated_saving": "y", "cache_hits": True}))
        events += obs_tracer.collect(context_state=[self.root / "ctx"])
        json.dumps(obs_metrics.report(events, obs_model.load_prices()), allow_nan=False)

    def test_symlinked_ticket_directories_are_not_followed(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "a.json").write_text(json.dumps({"action": {"ticket": "OUTSIDE"}}))
        history = self.root / "history"
        history.mkdir()
        os.symlink(outside, history / "T1")
        self.assertEqual([], obs_tracer.journal_history_events(history))

    def test_oversized_store_files_are_not_read(self):
        big = self.root / "big.json"
        big.write_text(json.dumps({"provenance": "LIVE_JEV", "pad": "x" * 5000}))
        with mock.patch.object(obs_tracer, "MAX_FILE_BYTES", 1000):
            self.assert_isolated(governor_reports=[big])


class EnvelopeUsageTests(unittest.TestCase):
    def setUp(self) -> None:
        import executor_transport

        self.transport = executor_transport

    def test_claude_envelope_usage_is_measured_and_kept_beside_the_result(self):
        envelope = {"type": "result", "subtype": "success", "is_error": False, "structured_output": {"ok": True},
                    "total_cost_usd": 0.42, "duration_api_ms": 1800, "num_turns": 3,
                    "usage": {"input_tokens": 1200, "output_tokens": 300, "cache_read_input_tokens": 9000,
                              "cache_creation_input_tokens": 500}}
        data, is_object, diagnostics = self.transport.extract_result("claude", json.dumps(envelope).encode(), Path("-"))
        self.assertTrue(is_object)
        self.assertEqual({"ok": True}, json.loads(data))
        self.assertEqual({"method": "MEASURED", "input_tokens": 1200, "output_tokens": 300, "cache_read_tokens": 9000,
                          "cache_write_tokens": 500, "cost_usd": 0.42, "duration_api_ms": 1800, "num_turns": 3},
                         diagnostics["usage"])

    def test_missing_counters_stay_unknown_and_no_usage_means_no_key(self):
        usage = self.transport.envelope_usage({"usage": {"input_tokens": "many"}})
        self.assertIsNone(usage["input_tokens"])
        self.assertIsNone(usage["cost_usd"])
        envelope = {"type": "result", "result": "{}", "is_error": False}
        self.assertNotIn("usage", self.transport.extract_result("claude", json.dumps(envelope).encode(), Path("-"))[2])


if __name__ == "__main__":
    unittest.main()
