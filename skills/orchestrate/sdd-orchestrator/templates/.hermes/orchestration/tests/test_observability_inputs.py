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


class ReviewRoundTwoTests(LoopFixture):
    """Round-2 review: huge integers, dict keys, unreported context-engine usage, mixed methods."""

    def test_huge_integers_never_crash_the_report(self):
        receipt = self.root / "r.json"
        receipt.write_text('{"metrics": {"jev_calls": 1, "elapsed_ms": 1' + "0" * 400 + '}, "outcome": {}, "recommendation": {}}')
        log = self.root / "hsm.jsonl"
        log.write_text('{"k": "delegation", "dispatched_at": 1, "completed_at": 1' + "0" * 400 + '}\n')
        events = obs_tracer.collect(receipts=[receipt]) + obs_cli.hsm_events(log)
        json.dumps(obs_metrics.report(events, obs_model.load_prices()), allow_nan=False)
        self.assertIsNone(obs_model.number(10 ** 400))
        self.assertIsNone(obs_model.number(10 ** 16))  # finite as a float, but not a believable count
        self.assertEqual((5, 10 ** 15), (obs_model.number(5), obs_model.number(10 ** 15)))

    def test_dict_keys_must_be_code_shaped(self):
        token = "ghp_" + "a" * 36
        item = obs_model.event("X", 1.0, source="t", attrs={"nested": {f"Maria Silva {token}": 1, "\ud800": 2, "ok_key": 3}})
        self.assertEqual({"nested": {"ok_key": 3}}, item["attrs"])
        store = self.store / "x1"
        store.mkdir(parents=True)
        (store / "definition.json").write_text("{}")
        (store / "execution.json").write_text(json.dumps({"nodes": {"A": {"attempts": [
            {"attempt": 1, "result": "FAILED", "failure": {"class": {f"senha {token}": 1}, "code": "C"}}]}}}))
        events = obs_tracer.loop_store_events(self.store)
        report = json.dumps(obs_metrics.report(events, obs_model.load_prices()), ensure_ascii=True)
        self.assertNotIn(token, report)
        self.assertIn("None:C", report)

    def test_context_engine_calls_without_usage_are_unknown_not_free(self):
        import context_judge

        class Segment:
            def __init__(self, ident):
                self.id, self.text, self.is_code = ident, "plain passage " + ident, False

        answer = {"type": "noul", "noul": 0.0}
        answers = {name: answer for name in context_judge.QUESTION_IDS}
        config = {"evaluator": "typesafe", "max_concurrency": 1, "evaluator_timeout_seconds": 5}
        for usage, reported in ((None, 0), ({"input_tokens": 7, "output_tokens": 1}, 2), ({"input_tokens": True, "output_tokens": 1}, 0)):
            sender = lambda payload, usage=usage: {"status": "OK", "result": {"answers": answers, **({"usage": usage} if usage else {})}}
            report = context_judge.JevJudge(config, sender).judge([Segment("a"), Segment("b")], "task", "")
            self.assertEqual((2, reported), (report.calls, report.usage_reported))
        event = obs_tracer.context_metrics_event({"evaluator_calls": 2, "evaluator_calls_without_usage": 1, "estimated_saving": 10,
                                                   "effective_saving": 10, "evaluator_input_tokens": 7,
                                                   "evaluator_output_tokens": 1}, run_id="r", timestamp=1.0)
        context = obs_metrics.jev_metrics([event])["context_engine"]
        self.assertIsNone(context["net_saving_tokens"]["applied"])
        self.assertEqual(1, context["evaluator_tokens"]["calls_with_unknown_usage"])

    def test_mixed_methods_are_never_added_together(self):
        def receipt(method, tokens):
            return obs_tracer.receipt_event({"metrics": {"jev_calls": 1, "input_tokens": tokens, "output_tokens": 1},
                                             "outcome": {}, "recommendation": {}}, method=method)
        usage = obs_metrics.jev_metrics([receipt("MEASURED", 10), receipt("SIMULATED", 5)])["governance"]["evaluator_tokens"]
        self.assertEqual((None, "MIXED"), (usage["input"], usage["method"]))
        self.assertEqual({"MEASURED": {"input": 10, "output": 1}, "SIMULATED": {"input": 5, "output": 1}}, usage["by_method"])
        runs = [obs_tracer.context_metrics_event({"evaluator_calls": 1, "effective_saving": 100, "estimated_saving": 100,
                                                  "evaluator_input_tokens": 10, "evaluator_output_tokens": 1}, run_id=f"r{m}",
                                                 timestamp=1.0, method=m) for m in ("MEASURED", "SIMULATED")]
        context = obs_metrics.jev_metrics(runs)["context_engine"]
        self.assertEqual(0, context["evaluator_tokens"]["calls_with_unknown_usage"])
        self.assertIsNone(context["net_saving_tokens"]["applied"])

    def test_reads_are_bounded_by_the_bytes_actually_read(self):
        path = self.root / "ten.json"
        path.write_bytes(b"0123456789")
        self.assertEqual(b"0123456789", obs_tracer.read_bounded(path, 10))
        with self.assertRaises(OSError):  # one byte over: refused, never silently truncated
            obs_tracer.read_bounded(path, 9)

    def test_loop_events_respect_the_byte_budget(self):
        store = self.store / "g1"
        store.mkdir(parents=True)
        (store / "definition.json").write_text("{}")
        (store / "execution.json").write_text("{}")
        (store / "events.jsonl").write_text('{"event": "A", "seq": 1}\n' * 3)  # 3 lines of 25 bytes

        def names():
            return [item["event_type"] for item in obs_tracer.loop_store_events(self.store)]

        with mock.patch.object(obs_tracer, "MAX_FILE_BYTES", 30):  # oversized when checked: not read at all
            self.assertNotIn("LOOP_A", names())
        with mock.patch.object(obs_tracer, "_regular"), mock.patch.object(obs_tracer, "MAX_FILE_BYTES", 30):
            grown = names()  # grew after the check: whole lines within the budget only, the cut line is not parsed
        self.assertEqual((1, 0), (grown.count("LOOP_A"), grown.count("LOOP_EVENT_UNREADABLE")))
        self.assertEqual(3, names().count("LOOP_A"))

    def test_bracketed_secret_assignments_are_dropped(self):
        value = "Zq8vN3kT1pR7wX2m"
        for text in (f"user[password]={value}", f"[api_key]={value}", f"api_key=,{value}", f"password:]{value}", "a[b][c]"):
            self.assertIsNone(obs_model.code(text), text)
        self.assertEqual("claude-opus-4-6[1m]", obs_model.code("claude-opus-4-6[1m]"))
        log = self.root / "h.jsonl"
        log.write_text(json.dumps({"k": "tool_call", "t": 1, "tool": "terminal", "res": f"user[password]={value}"}) + "\n")
        self.assertNotIn(value, json.dumps(obs_cli.hsm_events(log)))


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
