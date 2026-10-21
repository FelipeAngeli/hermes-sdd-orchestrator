"""Unit tests for loop-engine internals: backoff, mappings, event truncation, budgets and refusals."""
from __future__ import annotations

import sys
import json
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from loop_engine_fixtures import FakeClock, Recorder, definition, node

import loop_control  # noqa: E402
import loop_correction  # noqa: E402
import loop_engine  # noqa: E402
import loop_model  # noqa: E402
import loop_policy  # noqa: E402
import loop_state  # noqa: E402
from loop_store import LoopStore  # noqa: E402


class PolicyUnits(unittest.TestCase):
    def test_backoff_doubles_and_is_capped(self):
        limits = {"backoff_base_seconds": 2, "backoff_max_seconds": 10}
        self.assertEqual([2, 4, 8, 10, 10], [loop_policy.backoff_seconds(index, limits) for index in range(5)])

    def test_every_halt_reason_has_a_hermes_stop_reason_and_a_next_step(self):
        reasons = set(loop_policy.NEXT_STEPS) | set(loop_policy.FAILABLE_REASONS) | set(loop_state.BUDGET_REASONS)
        reasons |= set(loop_correction.LOOP_REASON_MAP.values())
        for reason in reasons:
            with self.subTest(reason=reason):
                self.assertIn(reason, loop_model.HERMES_STOP_REASON)
                self.assertTrue(loop_policy.next_step(reason))

    def test_a_malformed_handler_result_is_unknown_never_success(self):
        for value in (None, "ok", {"status": "DONE"}, {"output": 1}):
            success, failure = loop_policy.normalize_result(value)
            self.assertIsNone(success)
            self.assertEqual("UNKNOWN", failure["class"])

    def test_timeout_of_a_non_idempotent_node_is_ambiguous(self):
        decision = loop_policy.classify({"idempotent": False, "on_failure": None}, {"timeout_retries": 0, "transient_retries": 0},
                                        loop_policy.timeout_failure(5), dict(loop_model.DEFAULT_LIMITS))
        self.assertEqual(("HALT", "EFFECT_AMBIGUOUS"), (decision["decision"], decision["reason"]))

    def test_definition_defaults_timeouts_by_kind(self):
        spec = loop_model.validate_definition(definition([node("A", kind="run-agent"), node("B", kind="wait")],
                                                         limits={"timeouts_by_kind": {"run-agent": 42}}))
        self.assertEqual(42, spec["nodes"][0]["timeout_seconds"])
        self.assertIsNone(spec["nodes"][1]["timeout_seconds"])


class StoreUnits(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="loop-units-")
        self.store = Path(self._tmp.name) / "store"
        self.clock = FakeClock()
        self.rec = Recorder()

    def tearDown(self):
        self._tmp.cleanup()

    def engine(self, handlers):
        return loop_engine.LoopEngine(self.store, handlers=handlers, clock=self.clock.time, sleep=self.clock.sleep)

    def test_only_a_lock_holder_truncates_uncommitted_events(self):
        engine = self.engine({"A": self.rec.handler("A")})
        execution = engine.start(definition([node("A")]))
        identifier = execution["execution_id"]
        events = self.store / identifier / "events.jsonl"
        with open(events, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"seq": 2, "event": "NODE_STARTED"}) + "\n")
        store = LoopStore(self.store)
        store.load(identifier)  # a lock-free reader (status) between append and replace
        self.assertEqual([1, 2], [item["seq"] for item in store.events(identifier)])
        with store.runner_lock(identifier):
            store.load(identifier, repair=True)
        self.assertEqual([1], [item["seq"] for item in store.events(identifier)])
        with open(events, "a", encoding="utf-8") as handle:
            handle.write("{torn")
        self.assertEqual("UNREADABLE", store.events(identifier)[-1]["event"])
        with store.runner_lock(identifier):
            store.load(identifier, repair=True)
        self.assertEqual([1], [item["seq"] for item in store.events(identifier)])

    def test_unsafe_execution_ids_are_refused(self):
        for value in ("../x", "", "a/b", ".hidden"):
            with self.assertRaises(loop_model.LoopError) as caught:
                LoopStore(self.store).directory(value)
            self.assertEqual("EXECUTION_ID_INVALID", caught.exception.code)

    def test_a_raise_must_grow_and_stay_under_the_ceiling(self):
        engine = self.engine({name: self.rec.handler(name) for name in "AB"})
        execution = engine.start(definition([node("A"), node("B", depends_on=["A"])], limits={"max_node_runs": 1}))
        execution = engine.run(execution["execution_id"])
        identifier = execution["execution_id"]
        for value in ("max_node_runs=1", "max_node_runs=100000", "nonsense=3", "max_node_runs=x"):
            with self.assertRaises(loop_model.LoopError) as caught:
                loop_control.resolve(self.store, identifier, node=None, decision="raise-budget", value=value,
                                     by="requester", quote="mais", actor="human")
            self.assertEqual("BUDGET_RAISE_INVALID", caught.exception.code)

    def test_a_decision_that_does_not_fit_the_pending_halt_is_refused(self):
        engine = self.engine({"A": self.rec.handler("A")})
        execution = engine.start(definition([node("A", kind="wait")]))
        execution = engine.run(execution["execution_id"])
        with self.assertRaises(loop_model.LoopError) as caught:
            loop_control.resolve(self.store, execution["execution_id"], node="A", decision="approve",
                                 by="requester", quote="ok", actor="human")
        self.assertEqual("DECISION_NOT_ALLOWED", caught.exception.code)
        with self.assertRaises(loop_model.LoopError) as caught:
            loop_control.resolve(self.store, execution["execution_id"], node="B", decision="provide-input", value="x",
                                 by="requester", quote="x", actor="human")
        self.assertEqual("NODE_MISMATCH", caught.exception.code)

    def test_a_missing_handler_halts_instead_of_skipping(self):
        execution = self.engine({}).start(definition([node("A")]))
        execution = self.engine({}).run(execution["execution_id"])
        self.assertEqual("HANDLER_MISSING", execution["halt"]["reason"])
        self.assertEqual("EXECUTOR_UNAVAILABLE", execution["halt"]["hermes_stop_reason"])

    def test_a_machine_approver_is_refused_for_every_category(self):
        execution = self.engine({}).start(definition([node("A", kind="gate", approval={"category": "SCOPE", "reason": "wider"})]))
        execution = self.engine({}).run(execution["execution_id"])
        with self.assertRaises(loop_model.LoopError) as caught:
            loop_control.resolve(self.store, execution["execution_id"], node="A", decision="approve",
                                 by="Hermes", quote="ok", actor="human")
        self.assertEqual("HUMAN_ACTOR_REQUIRED", caught.exception.code)


class CorrectionBudgetUnits(unittest.TestCase):
    def test_the_correction_cap_is_per_task_across_test_and_review(self):
        from loop_engine_fixtures import failed, ok
        import loop_sdd

        def fail(evidence, hypothesis):
            return failed("FUNCTIONAL", "VERIFICATION_FAILED", evidence, failed_checks=["AC-1"], evidence=evidence, hypothesis=hypothesis)

        with tempfile.TemporaryDirectory(prefix="loop-cap-") as temp:
            rec, clock = Recorder(), FakeClock()
            scripts = {"IMPLEMENT": [ok({"diff": f"v{index}"}) for index in range(8)],
                       "TEST": [fail("t1", "h1"), fail("t2", "h2"), ok(), ok(), ok()],
                       "REVIEW": [fail("r1", "h3"), fail("r2", "h4")]}
            handlers = {stage: rec.handler(stage, scripts.get(stage)) for stage in loop_sdd.SDD_STAGES}
            engine = loop_engine.LoopEngine(Path(temp), handlers=handlers, clock=clock.time, sleep=clock.sleep)
            execution = engine.start(loop_sdd.build_definition(ticket="T-9", objective="o"))
            execution = engine.run(execution["execution_id"])
            self.assertEqual(4, rec.calls.count("IMPLEMENT"))
            self.assertEqual(3, execution["usage"]["correction_cycles"])
            self.assertEqual("CORRECTION_BUDGET_EXHAUSTED", execution["halt"]["reason"])
            self.assertEqual("REVIEW", execution["halt"]["node"])


if __name__ == "__main__":
    unittest.main()
