"""Tests for the read-only observability engine (obs_model, obs_tracer, obs_metrics, obs_cli, obs_benchmark)."""
from __future__ import annotations

import io
import json
import os
import stat
import sys
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from loop_engine_fixtures import definition, failed, node, ok  # noqa: E402

import gate_evidence  # noqa: E402
import obs_benchmark  # noqa: E402
import obs_cli  # noqa: E402
import obs_metrics  # noqa: E402
import obs_model  # noqa: E402
import obs_tracer  # noqa: E402

from obs_fixtures import SECRET, LoopFixture, snapshot  # noqa: E402


class TokenProvenanceTests(LoopFixture):
    def test_measured_and_estimated_tokens_are_never_summed_together(self):
        spec = definition([node("A", params={"stage": "IMPLEMENT"}), node("B", depends_on=["A"], params={"stage": "IMPLEMENT"})])
        handlers = {"A": self.rec.handler("A", [ok(tokens=100, tokens_method="MEASURED")]),
                    "B": self.rec.handler("B", [ok(tokens=40, tokens_method="ESTIMATED")])}
        self.run_loop(spec, handlers)
        stage = obs_metrics.token_tracker(obs_tracer.loop_store_events(self.store))["by_stage"]["IMPLEMENT"]
        self.assertEqual(100, stage["MEASURED"]["total_tokens"])
        self.assertEqual(40, stage["ESTIMATED"]["total_tokens"])

    def test_a_missing_count_is_unknown_not_zero(self):
        record = obs_model.tokens("MEASURED", "test", input_tokens=10, output_tokens="12")
        self.assertIsNone(record["output_tokens"])
        self.assertIsNone(record["total_tokens"])
        bucket: dict = {}
        obs_model.add_tokens(bucket, record)
        self.assertGreater(bucket["MEASURED"]["unknown_fields"], 0)

    def test_failed_attempts_keep_their_tokens_but_the_budget_counts_successes_only(self):
        spec = definition([node("A", params={"stage": "IMPLEMENT"})], limits={"max_transient_retries": 1})
        failure = failed("TRANSIENT", "RATE", "429", effect_applied=False)
        failure.update(tokens=70, tokens_method="MEASURED")
        identifier = self.run_loop(spec, {"A": self.rec.handler("A", [failure, ok(tokens=30, tokens_method="MEASURED")])})
        attempts = [item for item in obs_tracer.loop_events(self.store, identifier) if item["event_type"] == "NODE_ATTEMPT"]
        self.assertEqual([70, 30], [item["attrs"]["tokens"]["total_tokens"] for item in attempts])
        execution = json.loads((self.store / identifier / "execution.json").read_text())
        self.assertEqual(30, execution["usage"]["tokens"])

    def test_an_unknown_token_method_is_rejected(self):
        with self.assertRaises(ValueError):
            obs_model.tokens("GUESSED", "test", input_tokens=1)


class CostTests(unittest.TestCase):
    PRICES = {"version": "t1", "sha256": "x", "models": {"m": {"input": 2.0, "output": 10.0, "cache_read": 0.2}}}

    def test_cost_is_an_estimate_with_the_price_version(self):
        record = obs_model.tokens("MEASURED", "t", input_tokens=1_000_000, output_tokens=100_000, cache_read_tokens=500_000)
        cost = obs_model.estimate_cost(record, "m", self.PRICES)
        self.assertEqual({"usd": 3.1, "method": "ESTIMATED", "token_method": "MEASURED", "price_version": "t1",
                          "unknown_components": ["cache_write_tokens"]}, cost)

    def test_an_unknown_cache_component_makes_the_cost_a_lower_bound(self):
        event = obs_model.event("MODEL_CALL", 1.0, source="t", attrs={"model": "m", "tokens": obs_model.tokens(
            "MEASURED", "t", input_tokens=10, output_tokens=1, cache_read_tokens=5)})
        agents = obs_metrics.cost_estimator([event], self.PRICES)["by_actor"]["agents"]
        self.assertEqual(({"cache_write_tokens": 1}, True), (agents["priced_without"], agents["is_lower_bound"]))

    def test_unknown_model_or_split_stays_unpriced(self):
        self.assertEqual("NO_PRICE", obs_model.estimate_cost(obs_model.tokens("MEASURED", "t", input_tokens=1, output_tokens=1), "x", self.PRICES)["reason"])
        total_only = obs_model.tokens("ESTIMATED", "t", total_tokens=500)
        self.assertEqual("TOKEN_SPLIT_UNKNOWN", obs_model.estimate_cost(total_only, "m", self.PRICES)["reason"])

    def test_the_shipped_price_table_is_versioned_and_leaves_jev_unpriced(self):
        prices = obs_model.load_prices()
        self.assertTrue(prices["version"])
        self.assertEqual(64, len(prices["sha256"]))
        self.assertIsNone(obs_model.price_for(prices, "jev"))
        self.assertIsNotNone(obs_model.price_for(prices, "gpt-5.6-sol-900k"))

    def test_jev_cost_is_counted_separately_from_agents(self):
        events = [obs_tracer.governor_report_event({"provenance": "LIVE_JEV", "usage": {"input_tokens": 900, "output_tokens": 100},
                                                   "summary": {"decided": 1, "review": 0}, "status": "DECIDED"}, timestamp=1.0)]
        cost = obs_metrics.cost_estimator(events, self.PRICES)
        self.assertEqual(1, cost["by_actor"]["jev"]["unpriced_records"])
        self.assertNotIn("agents", cost["by_actor"])
        tokens = obs_metrics.token_tracker(events)["by_actor"]["jev"]["MEASURED"]
        self.assertEqual(1000, tokens["total_tokens"])


class JevMetricsTests(unittest.TestCase):
    def metrics_event(self, **values):
        base = {"input_tokens_before": 1000, "input_tokens_after": 1000, "estimated_saving": 0, "effective_saving": 0,
                "evaluator_calls": 2, "evaluator_input_tokens": 300, "evaluator_output_tokens": 50, "cache_hits": 1, "cache_misses": 3}
        base.update(values)
        return obs_tracer.context_metrics_event(base, run_id="r", timestamp=1.0)

    def test_net_saving_subtracts_the_evaluator_tokens_from_the_gross_saving(self):
        report = obs_metrics.jev_metrics([self.metrics_event(estimated_saving=500, effective_saving=400, input_tokens_after=600)])
        context = report["context_engine"]
        self.assertEqual(400, context["gross_saving_tokens"]["applied"])
        self.assertEqual(50, context["net_saving_tokens"]["applied"])
        self.assertEqual("MEASURED", context["evaluator_tokens"]["method"])

    def test_a_dry_run_has_no_applied_saving_but_still_pays_the_evaluator(self):
        context = obs_metrics.jev_metrics([self.metrics_event(estimated_saving=400)])["context_engine"]
        self.assertEqual(0, context["gross_saving_tokens"]["applied"])
        self.assertEqual(400, context["gross_saving_tokens"]["dry_run_estimate"])
        self.assertEqual(-350, context["net_saving_tokens"]["applied"])

    def test_a_call_without_reported_usage_makes_net_saving_unknown(self):
        report = obs_metrics.jev_metrics([obs_tracer.context_metrics_event(
            {"estimated_saving": 1000, "effective_saving": 1000, "evaluator_calls": 3}, run_id="r", timestamp=1.0)])
        context = report["context_engine"]
        self.assertIsNone(context["net_saving_tokens"]["applied"])
        self.assertEqual(1, context["evaluator_tokens"]["calls_with_unknown_usage"])
        receipt = {"metrics": {"jev_calls": 1, "input_tokens": None, "output_tokens": None}, "outcome": {}, "recommendation": {}}
        governance = obs_metrics.jev_metrics([obs_tracer.receipt_event(receipt)])["governance"]["evaluator_tokens"]
        self.assertEqual(1, governance["calls_with_unknown_usage"])

    def test_simulated_evaluator_calls_are_not_live_calls(self):
        receipt = {"metrics": {"jev_calls": 1, "input_tokens": 0, "output_tokens": 0}, "outcome": {}, "recommendation": {}}
        report = obs_metrics.jev_metrics([obs_tracer.receipt_event(receipt, method="SIMULATED")])["governance"]
        self.assertEqual((0, 1, "SIMULATED"), (report["live_calls"], report["simulated_calls"], report["evaluator_tokens"]["method"]))

    def test_cached_governance_decisions_are_not_counted_as_calls(self):
        events = [obs_tracer.governor_report_event({"provenance": "CACHE", "summary": {"decided": 1, "review": 0}, "status": "DECIDED"}),
                  obs_tracer.governor_report_event({"provenance": "LIVE_JEV", "usage": {"input_tokens": 5, "output_tokens": 1},
                                                    "summary": {"decided": 0, "review": 1}, "status": "REVIEW"})]
        governance = obs_metrics.jev_metrics(events)["governance"]
        self.assertEqual((1, 1, 1), (governance["live_calls"], governance["cache_reuses"], governance["review"]))


class SafetyTests(LoopFixture):
    def test_prompts_quotes_and_secrets_never_reach_an_event(self):
        spec = definition([node("A", kind="wait", params={"stage": "CLARIFY", "prompt": "secret " + SECRET})])
        identifier = self.run_loop(spec, {})
        import loop_control
        loop_control.resolve(self.store, identifier, node="A", decision="provide-input", by="requester", quote=SECRET,
                             actor="human", value=SECRET)
        dumped = json.dumps(obs_tracer.loop_events(self.store, identifier))
        self.assertNotIn(SECRET, dumped)
        self.assertNotIn("sk-ant-api03", dumped)

    def test_nested_failure_free_text_and_odd_key_case_never_reach_an_event(self):
        store = self.store / "x1"
        store.mkdir(parents=True)
        (store / "execution.json").write_text("{}")
        (store / "definition.json").write_text("{}")
        text = "cliente Maria CPF 123.456.789-00 pediu reembolso"
        (store / "events.jsonl").write_text(json.dumps({"event": "NODE_FAILED", "reason": text, "failure": {
            "class": "FUNCTIONAL", "code": "ASSERT", "Message": text, "details": text, "PROMPT": text}}) + "\n")
        events = obs_tracer.loop_store_events(self.store)
        dumped = json.dumps(events)
        self.assertNotIn("Maria", dumped)
        failed_event = next(item for item in events if item["event_type"] == "LOOP_NODE_FAILED")
        self.assertEqual({"failure_class": "FUNCTIONAL", "failure_code": "ASSERT"},
                         {key: value for key, value in failed_event["attrs"].items() if key.startswith("failure")})
        item = obs_model.event("X", 1.0, source="t", attrs={"Prompt": "p", "MESSAGE": "m", "code": "C"})
        self.assertEqual({"code": "C"}, item["attrs"])

    def test_redaction_applies_to_kept_strings(self):
        item = obs_model.event("X", 1.0, source="t", attrs={"failure_code": SECRET})
        self.assertNotIn(SECRET, json.dumps(item))

    def test_free_text_without_secrets_is_dropped_too(self):
        text = "Customer Maria asked for the refund of order 4411"
        spec = definition([node("A", params={"stage": "IMPLEMENT"})])
        identifier = self.run_loop(spec, {"A": self.rec.handler("A", [failed("FUNCTIONAL", "ASSERT", text)])})
        dumped = json.dumps(obs_tracer.loop_events(self.store, identifier))
        self.assertIn("ASSERT", dumped)
        self.assertNotIn("Maria", dumped)

    def test_unknown_identifiers_are_rejected(self):
        with self.assertRaises(ValueError):
            obs_model.event("X", 1.0, source="t", user_id="u")

    def test_event_drops_free_text_keys_at_any_depth(self):
        item = obs_model.event("X", 1.0, source="t", attrs={"prompt": "p", "result": "ok",
                                                            "nested": {"message": "m", "quote": "q", "code": "C"}})
        self.assertEqual({"result": "ok", "nested": {"code": "C"}}, item["attrs"])


class SingleSourceOfTruthTests(LoopFixture):
    def test_reading_writes_nothing_and_is_deterministic(self):
        spec = definition([node("A", params={"stage": "PLAN"})])
        self.run_loop(spec, {"A": self.rec.handler("A")})
        before = snapshot(self.root)
        first = obs_tracer.loop_store_events(self.store)
        second = obs_tracer.loop_store_events(self.store)
        self.assertEqual(first, second)
        self.assertEqual(before, snapshot(self.root))
        self.assertEqual(obs_model.trace_id("loop", first[0]["run_id"]), first[0]["trace_id"])

    def test_a_tampered_gate_log_yields_one_error_event_not_its_records(self):
        log = gate_evidence.DecisionLog(self.root / "gates")
        log.append({"decision_id": "d1", "gate_id": "g", "task_id": "T", "verdict": "PASS", "timestamp": "2026-10-22T10:00:00Z"})
        self.assertEqual("GATE_DECISION", obs_tracer.gate_events(self.root / "gates")[0]["event_type"])
        path = self.root / "gates" / "decisions.jsonl"
        path.write_text(path.read_text().replace("PASS", "FAIL"))
        events = obs_tracer.gate_events(self.root / "gates")
        self.assertEqual(["SOURCE_UNREADABLE"], [item["event_type"] for item in events])

    def test_an_unreadable_source_does_not_hide_the_others(self):
        (self.root / "bad.json").write_text("{not json")
        spec = definition([node("A", params={"stage": "PLAN"})])
        self.run_loop(spec, {"A": self.rec.handler("A")})
        events = obs_tracer.collect(loop_store=[self.store], governor_reports=[self.root / "bad.json"])
        kinds = {item["event_type"] for item in events}
        self.assertIn("SOURCE_UNREADABLE", kinds)
        self.assertIn("NODE_ATTEMPT", kinds)


class ProgressTests(LoopFixture):
    def test_first_attempt_success_retries_by_cause_corrections_and_waits(self):
        spec = definition([node("IMPL", params={"stage": "IMPLEMENT"}),
                           node("TEST", depends_on=["IMPL"], on_failure={"reopen": "IMPL"}, params={"stage": "TEST"})])
        verify = failed("FUNCTIONAL", "ASSERT", "wrong", hypothesis="fix the total")
        handlers = {"IMPL": self.rec.handler("IMPL", [failed("TRANSIENT", "RATE", "429", effect_applied=False), ok(), ok()]),
                    "TEST": self.rec.handler("TEST", [verify, ok()])}
        identifier = self.run_loop(spec, handlers)
        progress = obs_metrics.progress_tracker(obs_tracer.loop_events(self.store, identifier))
        self.assertEqual({"TRANSIENT:RATE": 1, "FUNCTIONAL:ASSERT": 1}, progress["retries_by_cause"])
        self.assertEqual(1, progress["correction_loops"])
        self.assertEqual(0.0, progress["nodes"]["first_attempt_success_rate"])
        self.assertEqual({"COMPLETED": 1}, progress["loops"])

    def test_human_wait_is_the_time_between_halt_and_decision(self):
        spec = definition([node("A", kind="wait", params={"stage": "CLARIFY"})])
        identifier = self.run_loop(spec, {})
        import loop_control
        original = loop_control._now
        halted_at = json.loads((self.store / identifier / "execution.json").read_text())["halt"]["at"]
        loop_control._now = lambda: halted_at + 90
        try:
            loop_control.resolve(self.store, identifier, node="A", decision="provide-input", by="requester", quote="ok",
                                 actor="human", value="yes")
        finally:
            loop_control._now = original
        wait = obs_metrics.human_wait(obs_tracer.loop_events(self.store, identifier))
        self.assertEqual((1, 90.0, 0), (wait["resolved"], wait["total_s"], wait["still_waiting"]))

    def test_concurrency_counts_overlapping_spans(self):
        events = [obs_model.event("NODE_ATTEMPT", start, source="t", attrs={"duration_s": length})
                  for start, length in ((0.0, 10.0), (5.0, 10.0), (20.0, 5.0))]
        value = obs_metrics.concurrency(events)
        self.assertEqual(2, value["max_parallel"])
        self.assertEqual(25.0, value["busy_s"])


class CliTests(LoopFixture):
    def test_report_writes_a_private_file_and_events_filter_by_stage(self):
        spec = definition([node("A", params={"stage": "PLAN"}), node("B", depends_on=["A"], params={"stage": "TEST"})])
        self.run_loop(spec, {"A": self.rec.handler("A"), "B": self.rec.handler("B")})
        output = self.root / "report.json"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, obs_cli.main(["report", "--loop-store", str(self.store), "--output", str(output)]))
        self.assertEqual(0o600, stat.S_IMODE(output.stat().st_mode))
        report = json.loads(output.read_text())
        self.assertEqual(0, report["overhead"]["llm_calls"])
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            obs_cli.main(["events", "--loop-store", str(self.store), "--stage", "TEST"])
        stages = {json.loads(line)["stage"] for line in buffer.getvalue().splitlines()}
        self.assertEqual({"TEST"}, stages)

    def test_report_refuses_to_write_through_a_symlink(self):
        target = self.root / "elsewhere.json"
        target.write_text("keep")
        link = self.root / "link.json"
        os.symlink(target, link)
        with self.assertRaises(OSError):
            obs_cli.write_private(link, b"{}")
        self.assertEqual("keep", target.read_text())

    def test_hsm_log_uncached_input_excludes_cache_reads(self):
        log = self.root / "events.jsonl"
        log.write_text(json.dumps({"k": "api_call", "t": 1.0, "sid": "s", "model": "m", "in": 1000, "out": 10, "cache_read": 800,
                                   "latency_s": 2.0, "prompt": SECRET, "ts": "2026-10-09 15:11:49,233"}) + "\n")
        (event,) = obs_cli.hsm_events(log)
        self.assertEqual((200, 800, "MEASURED"), (event["attrs"]["tokens"]["input_tokens"], event["attrs"]["tokens"]["cache_read_tokens"],
                                                  event["attrs"]["tokens"]["method"]))
        self.assertNotIn(SECRET, json.dumps(event))
        self.assertEqual((2026, 10, 9), time.localtime(event["timestamp"])[:3])  # the call's own time, not collection time t=1.0


class BenchmarkTests(unittest.TestCase):
    def test_offline_scenarios_are_equivalent_and_label_their_methods(self):
        budget = obs_benchmark.LiveBudget(0)
        results = [obs_benchmark.run_scenario(name, jev="offline", budget=budget, env_file=Path("-"), setup=Path("-"),
                                              cache=Path("-")) for name in "AC"]
        agents = results[1]["report"]["tokens"]["by_actor"]["agents"]
        self.assertEqual(["SIMULATED"], list(agents))
        self.assertEqual(0, results[1]["report"]["jev"]["governance"]["live_calls"])
        self.assertNotIn("MEASURED", results[1]["report"]["tokens"]["by_actor"]["jev"])
        rows = obs_benchmark.compare(results)
        self.assertTrue(rows["A"]["quality"]["all_stages_completed"])
        self.assertTrue(rows["C"]["quality"]["requirements_preserved"])
        self.assertEqual(rows["A"]["agent_tokens_simulated"] - rows["C"]["agent_tokens_simulated"],
                         rows["C"]["context_gross_saving_estimated"])
        self.assertEqual(0, budget.used)

    def test_the_live_budget_refuses_calls_beyond_the_cap(self):
        budget = obs_benchmark.LiveBudget(1)
        self.assertEqual((True, False), (budget.take(), budget.take()))
        self.assertEqual((1, 1), (budget.used, budget.refused))


if __name__ == "__main__":
    unittest.main()
