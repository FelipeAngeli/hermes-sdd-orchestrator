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


class ReviewRegressions(unittest.TestCase):
    """PR #61 review findings (pr-reviewer 1-7, security-reviewer 1-7)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="loop-review-")
        self.store = Path(self._tmp.name) / "store"
        self.clock = FakeClock()
        self.rec = Recorder()

    def tearDown(self):
        self._tmp.cleanup()

    def engine(self, handlers, reconcilers=None):
        return loop_engine.LoopEngine(self.store, handlers=handlers, reconcilers=reconcilers or {},
                                      clock=self.clock.time, sleep=self.clock.sleep)

    def resolve(self, identifier, **kwargs):
        kwargs.setdefault("by", "requester")
        kwargs.setdefault("quote", "ok")
        kwargs.setdefault("actor", "human")
        kwargs.setdefault("node", None)
        return loop_control.resolve(self.store, identifier, **kwargs)

    def test_a_verifier_failing_on_a_spent_run_budget_resumes_after_a_raise(self):
        from loop_engine_fixtures import failed, ok
        import loop_sdd

        scripts = {"IMPLEMENT": [ok({"diff": "v1"}), ok({"diff": "v2"})],
                   "TEST": [failed("FUNCTIONAL", "VERIFY", "e1", failed_checks=["AC-1"], evidence="e1", hypothesis="h1"), ok()]}
        handlers = {stage: self.rec.handler(stage, scripts.get(stage)) for stage in loop_sdd.SDD_STAGES}
        engine = self.engine(handlers)
        execution = engine.start(loop_sdd.build_definition(ticket="T-B", objective="o", limits={"max_node_runs": 6}))
        execution = engine.run(execution["execution_id"])
        self.assertEqual("RUN_BUDGET_EXHAUSTED", execution["halt"]["reason"])
        self.assertEqual("BLOCKED", execution["nodes"]["TEST"]["status"])
        self.resolve(execution["execution_id"], node="TEST", decision="raise-budget", value="max_node_runs=20")
        execution = engine.run(execution["execution_id"])
        self.assertEqual("COMPLETED", execution["status"])
        self.assertEqual("h1", self.rec.contexts["IMPLEMENT"][1].correction["hypothesis"])

    def test_control_commands_never_create_an_unknown_execution(self):
        self.engine({}).start(definition([node("A")]), execution_id="known-1")
        for call in (lambda: loop_control.request_cancel(self.store, "ghost-1", by="requester", reason="x"),
                     lambda: self.resolve("ghost-1", decision="approve")):
            with self.assertRaises(loop_model.LoopError) as caught:
                call()
            self.assertEqual("EXECUTION_NOT_FOUND", caught.exception.code)
        self.assertFalse((self.store / "ghost-1").exists())
        self.engine({}).start(definition([node("A")]), execution_id="ghost-1")

    def test_a_raise_must_name_the_exhausted_budget(self):
        engine = self.engine({name: self.rec.handler(name) for name in "AB"})
        execution = engine.start(definition([node("A"), node("B", depends_on=["A"])], limits={"max_node_runs": 1}))
        execution = engine.run(execution["execution_id"])
        with self.assertRaises(loop_model.LoopError) as caught:
            self.resolve(execution["execution_id"], decision="raise-budget", value="max_correction_cycles=5")
        self.assertEqual("BUDGET_RAISE_INVALID", caught.exception.code)

    def test_program_names_never_approve_security(self):
        spec = definition([node("SEC", kind="gate", approval={"category": "SECURITY", "reason": "r"})])
        engine = self.engine({"SEC": self.rec.handler("SEC")})
        execution = engine.run(engine.start(spec)["execution_id"])
        for name in ("claude", "Claude", "codex", "hermes-agent", "sdd-bot", "AutoApprover", "Hermes Agent", "gpt-5", "felipe"):
            with self.subTest(name=name), self.assertRaises(loop_model.LoopError) as caught:
                self.resolve(execution["execution_id"], node="SEC", decision="approve", by=name)
            self.assertIn(caught.exception.code, {"SECURITY_APPROVAL_HUMAN_ONLY", "HUMAN_ACTOR_REQUIRED"})
        self.assertTrue(loop_control.is_machine_name("hermes-agent"))
        self.assertFalse(loop_control.is_machine_name("felipe"))

    def test_a_hand_edited_approval_or_limit_is_refused(self):
        spec = definition([node("SEC", kind="gate", approval={"category": "SECURITY", "reason": "r"})])
        engine = self.engine({"SEC": self.rec.handler("SEC")})
        identifier = engine.run(engine.start(spec)["execution_id"])["execution_id"]
        path = self.store / identifier / "execution.json"
        original = json.loads(path.read_text(encoding="utf-8"))
        for mutate in (lambda value: value["nodes"]["SEC"].update(approval={"decision": "approve", "category": "SECURITY"}),
                       lambda value: value["limits"].update(max_node_runs=999)):
            value = json.loads(json.dumps(original))
            mutate(value)
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(loop_model.LoopError) as caught:
                engine.run(identifier)
            self.assertEqual("EXECUTION_TAMPERED", caught.exception.code)
        self.assertEqual([], self.rec.calls)

    def _edit(self, identifier, mutate):
        path = self.store / identifier / "execution.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        mutate(value)
        path.write_text(json.dumps(value), encoding="utf-8")

    def assert_tampered(self, engine, identifier):
        with self.assertRaises(loop_model.LoopError) as caught:
            engine.run(identifier)
        self.assertEqual("EXECUTION_TAMPERED", caught.exception.code)

    def test_a_hand_completed_gate_is_refused(self):
        spec = definition([node("SEC", kind="gate", approval={"category": "SECURITY", "reason": "r"}),
                           node("PUSH", depends_on=["SEC"], idempotent=True)])
        engine = self.engine({"SEC": self.rec.handler("SEC"), "PUSH": self.rec.handler("PUSH")})
        identifier = engine.run(engine.start(spec)["execution_id"])["execution_id"]

        def complete(value):
            value["nodes"]["SEC"]["status"] = "COMPLETED"
            value.update(status="RUNNING", pending=None, halt=None)

        self._edit(identifier, complete)
        self.assert_tampered(engine, identifier)
        self.assertEqual([], self.rec.calls)

    def test_a_reset_usage_is_refused(self):
        engine = self.engine({name: self.rec.handler(name) for name in "ABC"})
        spec = definition([node("A"), node("B", depends_on=["A"]), node("C", depends_on=["B"])], limits={"max_node_runs": 1})
        identifier = engine.run(engine.start(spec)["execution_id"])["execution_id"]

        def reset(value):
            value["usage"]["node_runs"] = 0
            value["nodes"]["B"]["status"] = "PENDING"
            value.update(status="RUNNING", pending=None, halt=None)

        self._edit(identifier, reset)
        self.assert_tampered(engine, identifier)
        self.assertEqual(["A"], self.rec.calls)

    def test_a_rewritten_raise_value_is_refused_and_whitespace_is_normalized(self):
        engine = self.engine({name: self.rec.handler(name) for name in "ABC"})
        spec = definition([node("A"), node("B", depends_on=["A"]), node("C", depends_on=["B"])], limits={"max_node_runs": 1})
        identifier = engine.run(engine.start(spec)["execution_id"])["execution_id"]
        self.resolve(identifier, decision="raise-budget", value=" max_node_runs = 2 ")
        execution = engine.run(identifier)
        self.assertEqual("max_node_runs=2", execution["decisions"][-1]["value"])
        self.assertEqual("RUN_BUDGET_EXHAUSTED", execution["halt"]["reason"])

        def rewrite(value):
            value["decisions"][-1]["value"] = "max_node_runs=1000"
            value["limits"]["max_node_runs"] = 1000

        self._edit(identifier, rewrite)
        self.assert_tampered(engine, identifier)
        self.assertEqual(["A", "B"], self.rec.calls)

    def test_store_directories_are_private_and_links_are_refused(self):
        import os
        import stat as stat_module

        identifier = self.engine({}).start(definition([node("A")]))["execution_id"]
        self.assertEqual(0o700, stat_module.S_IMODE(os.stat(self.store).st_mode))
        self.assertEqual(0o700, stat_module.S_IMODE(os.stat(self.store / identifier).st_mode))
        os.chmod(self.store / identifier, 0o777)
        with self.assertRaises(loop_model.LoopError) as caught:
            self.engine({}).run(identifier)
        self.assertEqual("STORE_PATH_UNSAFE", caught.exception.code)
        os.chmod(self.store / identifier, 0o700)
        events = self.store / identifier / "events.jsonl"
        target = Path(self._tmp.name) / "victim.txt"
        target.write_text("", encoding="utf-8")
        events.unlink()
        events.symlink_to(target)
        with self.assertRaises(loop_model.LoopError):
            self.engine({"A": self.rec.handler("A")}).run(identifier)
        self.assertEqual("", target.read_text(encoding="utf-8"))
        with self.assertRaises(loop_model.LoopError):
            LoopStore(self.store).directory("abc\n")

    def test_non_wait_nodes_need_a_finite_timeout(self):
        with self.assertRaises(loop_model.LoopError) as caught:
            loop_model.validate_definition(definition([node("A", timeout_seconds=None)]))
        self.assertEqual("TIMEOUT_REQUIRED", caught.exception.code)

    def test_a_cancel_during_the_last_node_still_completes(self):
        def last(ctx):
            loop_control.request_cancel(self.store, ctx.execution_id, by="requester", reason="late")
            return {"status": "SUCCEEDED", "output": {}}

        engine = self.engine({"A": last})
        execution = engine.run(engine.start(definition([node("A")]))["execution_id"])
        self.assertEqual("COMPLETED", execution["status"])

    def test_handlers_get_copies_and_a_stale_non_idempotent_thread_blocks_a_retry(self):
        import threading

        release = threading.Event()
        self.addCleanup(release.set)

        def hang(ctx):
            ctx.inputs["X"] = "mutated"
            release.wait(30)
            return {"status": "SUCCEEDED", "output": {}}

        spec = definition([node("X"), node("PUSH", depends_on=["X"], timeout_seconds=1)])
        engine = loop_engine.LoopEngine(self.store, handlers={"X": self.rec.handler("X"), "PUSH": hang})
        execution = engine.run(engine.start(spec)["execution_id"])
        self.assertEqual("EFFECT_AMBIGUOUS", execution["halt"]["reason"])
        self.assertEqual({"node": "X"}, execution["nodes"]["X"]["output"])
        self.resolve(execution["execution_id"], node="PUSH", decision="retry")
        execution = engine.run(execution["execution_id"])
        self.assertEqual("NODE_STILL_RUNNING", execution["halt"]["reason"])


if __name__ == "__main__":
    unittest.main()
