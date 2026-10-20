"""Controller-owned acceptance tests for the loop engine: model, scheduling, failures, limits.

Do not edit, skip or weaken these tests; the implementation must satisfy them with real logic.
"""
from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from loop_engine_fixtures import FakeClock, Recorder, definition, failed, node, ok

import action_journal  # noqa: E402
import loop_control  # noqa: E402
import loop_engine  # noqa: E402
import loop_model  # noqa: E402
import loop_policy  # noqa: E402
import stop_reasons  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="loop-engine-")
        self.store = Path(self._tmp.name) / "store"
        self.clock = FakeClock()
        self.rec = Recorder()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def engine(self, handlers, reconcilers=None):
        return loop_engine.LoopEngine(self.store, handlers=handlers, reconcilers=reconcilers or {},
                                      clock=self.clock.time, sleep=self.clock.sleep)

    def start_and_run(self, spec, handlers, **run):
        engine = self.engine(handlers)
        execution = engine.start(spec)
        return engine, engine.run(execution["execution_id"], **run)


class DefinitionTests(Base):
    def assert_code(self, spec, code):
        with self.assertRaises(loop_model.LoopError) as caught:
            loop_model.validate_definition(spec)
        self.assertEqual(code, caught.exception.code)
        return caught.exception

    def test_circular_dependencies_are_rejected_with_the_cycle(self):
        error = self.assert_code(definition([node("A", depends_on=["C"]), node("B", depends_on=["A"]), node("C", depends_on=["B"])]),
                                 "DEPENDENCY_CYCLE")
        for name in ("A", "B", "C"):
            self.assertIn(name, str(error))

    def test_self_unknown_and_duplicate_dependencies_are_rejected(self):
        self.assert_code(definition([node("A", depends_on=["A"])]), "DEPENDENCY_CYCLE")
        self.assert_code(definition([node("A", depends_on=["missing"])]), "DEPENDENCY_UNKNOWN")
        self.assert_code(definition([node("A"), node("A")]), "DUPLICATE_NODE_ID")

    def test_only_the_kinds_the_sdd_needs_are_supported(self):
        self.assertEqual(("run-agent", "run-tool", "gate", "wait"), loop_model.NODE_KINDS)
        for kind in ("run-loop", "fan-out", "collect", "branch"):
            self.assertIn(kind, loop_model.PLANNED_NODE_KINDS)
            self.assert_code(definition([node("A", kind=kind)]), "UNSUPPORTED_NODE_KIND")

    def test_limits_get_experimental_defaults_and_hard_ceilings(self):
        normalized = loop_model.validate_definition({"schema_version": 1, "loop_id": "x", "objective": "o", "nodes": [node("A")]})
        self.assertEqual(3, normalized["limits"]["max_correction_cycles"])
        self.assertEqual(2, normalized["limits"]["max_transient_retries"])
        self.assertFalse(normalized["nodes"][0]["idempotent"], "unknown idempotency must default to not idempotent")
        self.assert_code(definition([node("A")], limits={"max_correction_cycles": 10_000}), "LIMIT_ABOVE_CEILING")

    def test_a_correction_target_must_be_an_upstream_node(self):
        self.assert_code(definition([node("A"), node("B", on_failure={"reopen": "C"}), node("C", depends_on=["B"])]), "CORRECTION_TARGET_INVALID")

    def test_states_map_onto_real_hermes_states_and_stop_reasons(self):
        self.assertEqual(("PENDING", "RUNNING", "WAITING_INPUT", "WAITING_APPROVAL", "RETRYING", "BLOCKED", "COMPLETED", "FAILED", "CANCELLED"),
                         loop_model.EXECUTION_STATES)
        self.assertEqual(set(loop_model.EXECUTION_STATES), set(loop_model.HERMES_STATE_MAP))
        for state, mapping in loop_model.HERMES_STATE_MAP.items():
            self.assertLessEqual(set(mapping["journal"]), action_journal.STATUSES, state)
            self.assertIn(mapping["stage_status"], {"RUNNING", "PAUSED", "BLOCKED", "DONE"}, state)
        for reason, hermes in loop_model.HERMES_STOP_REASON.items():
            self.assertIn(hermes, stop_reasons.STOP_REASONS, reason)


class SchedulingTests(Base):
    def test_dependencies_run_in_order_and_receive_upstream_outputs(self):
        spec = definition([node("C", depends_on=["B"]), node("A"), node("B", depends_on=["A"])])
        handlers = {name: self.rec.handler(name, [ok({"value": name})]) for name in "ABC"}
        _, execution = self.start_and_run(spec, handlers)
        self.assertEqual(["A", "B", "C"], self.rec.calls)
        self.assertEqual({"A": {"value": "A"}}, self.rec.contexts["B"][0].inputs)
        self.assertEqual("COMPLETED", execution["status"])
        self.assertEqual("COMPLETED", execution["outcome"]["status"])
        self.assertEqual("DONE", execution["outcome"]["hermes_stop_reason"])

    def test_completed_work_is_never_repeated_by_a_second_run(self):
        spec = definition([node("A"), node("B", depends_on=["A"])])
        handlers = {name: self.rec.handler(name) for name in "AB"}
        engine, execution = self.start_and_run(spec, handlers)
        engine.run(execution["execution_id"])
        self.assertEqual(["A", "B"], self.rec.calls)

    def test_a_second_runner_is_refused_while_the_lock_is_held(self):
        engine = self.engine({"A": self.rec.handler("A")})
        execution = engine.start(definition([node("A")]))
        with loop_engine.LoopStore(self.store).runner_lock(execution["execution_id"]):
            with self.assertRaises(loop_model.LoopError) as caught:
                engine.run(execution["execution_id"])
        self.assertEqual("LOOP_ALREADY_RUNNING", caught.exception.code)
        self.assertEqual([], self.rec.calls)


class FailureTests(Base):
    def test_transient_failures_retry_with_exponential_backoff_and_new_keys(self):
        handler = self.rec.handler("A", [loop_policy.TransientFailure("NET", "reset"), loop_policy.TransientFailure("NET", "reset"), ok()])
        _, execution = self.start_and_run(definition([node("A", idempotent=True)]), {"A": handler})
        self.assertEqual(["A", "A", "A"], self.rec.calls)
        self.assertEqual([1, 2], self.clock.sleeps)
        keys = [ctx.idempotency_key for ctx in self.rec.contexts["A"]]
        self.assertEqual(3, len(set(keys)))
        self.assertEqual("COMPLETED", execution["status"])

    def test_transient_retries_are_bounded_then_escalated(self):
        handler = self.rec.handler("A", [loop_policy.TransientFailure("NET", "reset")] * 5)
        _, execution = self.start_and_run(definition([node("A", idempotent=True)]), {"A": handler})
        self.assertEqual(3, len(self.rec.calls))
        self.assertEqual("BLOCKED", execution["status"])
        self.assertEqual("TRANSIENT_RETRIES_EXHAUSTED", execution["halt"]["reason"])
        self.assertEqual("EXECUTOR_FAILED", execution["halt"]["hermes_stop_reason"])

    def test_functional_failure_is_never_retried_and_escalates_or_fails_by_policy(self):
        handler = self.rec.handler("A", [failed("FUNCTIONAL", "ASSERT", "wrong total")])
        _, execution = self.start_and_run(definition([node("A", idempotent=True)]), {"A": handler})
        self.assertEqual(1, len(self.rec.calls))
        self.assertEqual("BLOCKED", execution["status"])
        self.assertEqual("FUNCTIONAL_FAILURE", execution["halt"]["reason"])
        self.store = Path(self._tmp.name) / "store-2"
        handler = self.rec.handler("B", [loop_policy.FunctionalFailure("ASSERT", "wrong total")])
        _, execution = self.start_and_run(definition([node("B", idempotent=True)], loop_id="d2",
                                                     policy={"on_unrecoverable_failure": "FAIL"}), {"B": handler})
        self.assertEqual("FAILED", execution["status"])
        self.assertEqual("FUNCTIONAL_FAILURE", execution["outcome"]["reason"])

    def test_unknown_errors_are_not_retried(self):
        handler = self.rec.handler("A", [RuntimeError("boom"), ok()])
        _, execution = self.start_and_run(definition([node("A", idempotent=True)]), {"A": handler})
        self.assertEqual(1, len(self.rec.calls))
        self.assertEqual("UNKNOWN_FAILURE", execution["halt"]["reason"])

    def test_timeout_is_enforced_retried_once_then_escalated(self):
        release = threading.Event()
        self.addCleanup(release.set)

        def hang(ctx):
            self.rec.calls.append("A")
            release.wait(30)
            return ok()

        spec = definition([node("A", idempotent=True, timeout_seconds=1)], limits={"max_timeout_retries": 1})
        _, execution = self.start_and_run(spec, {"A": hang})
        self.assertEqual(2, len(self.rec.calls))
        self.assertEqual("TIMEOUT_RETRIES_EXHAUSTED", execution["halt"]["reason"])
        self.assertEqual("EXECUTOR_TIMEOUT", execution["halt"]["hermes_stop_reason"])

    def test_a_non_idempotent_operation_with_uncertain_effect_is_never_retried_automatically(self):
        handler = self.rec.handler("PUSH", [loop_policy.TransientFailure("NET", "reset after send")])
        spec = definition([node("PUSH", idempotent=False)])
        _, execution = self.start_and_run(spec, {"PUSH": handler})
        self.assertEqual(1, len(self.rec.calls))
        self.assertEqual("EFFECT_AMBIGUOUS", execution["halt"]["reason"])
        self.assertEqual("DECISION", execution["pending"]["kind"])

    def test_a_non_idempotent_operation_that_proves_no_effect_may_retry(self):
        handler = self.rec.handler("PUSH", [loop_policy.TransientFailure("NET", "refused", effect_applied=False), ok()])
        _, execution = self.start_and_run(definition([node("PUSH", idempotent=False)]), {"PUSH": handler})
        self.assertEqual(2, len(self.rec.calls))
        self.assertEqual("COMPLETED", execution["status"])


class LimitTests(Base):
    def test_run_budget_escalates_and_a_human_raise_resumes(self):
        handlers = {name: self.rec.handler(name) for name in "ABC"}
        spec = definition([node("A"), node("B", depends_on=["A"]), node("C", depends_on=["B"])], limits={"max_node_runs": 2})
        engine, execution = self.start_and_run(spec, handlers)
        self.assertEqual("RUN_BUDGET_EXHAUSTED", execution["halt"]["reason"])
        self.assertEqual(["A", "B"], self.rec.calls)
        loop_control.resolve(self.store, execution["execution_id"], node=None, decision="raise-budget",
                             value="max_node_runs=3", by="requester", quote="pode seguir com mais uma", actor="human")
        execution = engine.run(execution["execution_id"])
        self.assertEqual(["A", "B", "C"], self.rec.calls)
        self.assertEqual("COMPLETED", execution["status"])

    def test_token_budget_counts_reported_usage(self):
        handlers = {"A": self.rec.handler("A", [ok(tokens=8, tokens_method="MEASURED")]),
                    "B": self.rec.handler("B", [ok(tokens=5, tokens_method="ESTIMATED")]), "C": self.rec.handler("C")}
        spec = definition([node("A"), node("B", depends_on=["A"]), node("C", depends_on=["B"])], limits={"max_tokens": 10})
        _, execution = self.start_and_run(spec, handlers)
        self.assertEqual("TOKEN_BUDGET_EXHAUSTED", execution["halt"]["reason"])
        self.assertEqual(13, execution["usage"]["tokens"])
        self.assertEqual("ESTIMATED", execution["usage"]["tokens_method"])
        self.assertEqual(["A", "B"], self.rec.calls)

    def test_wall_clock_budget_is_respected(self):
        def slow(ctx):
            self.clock.now += 50
            return ok()

        spec = definition([node("A"), node("B", depends_on=["A"])], limits={"max_wall_clock_seconds": 40})
        _, execution = self.start_and_run(spec, {"A": slow, "B": self.rec.handler("B")})
        self.assertEqual("TIME_BUDGET_EXHAUSTED", execution["halt"]["reason"])
        self.assertEqual([], self.rec.calls)

    def test_max_steps_returns_without_losing_progress(self):
        handlers = {name: self.rec.handler(name) for name in "AB"}
        engine, execution = self.start_and_run(definition([node("A"), node("B", depends_on=["A"])]), handlers, max_steps=1)
        self.assertEqual("COMPLETED", execution["nodes"]["A"]["status"])
        self.assertEqual("PENDING", execution["nodes"]["B"]["status"])


class ControlTests(Base):
    def test_cancellation_during_a_node_finishes_it_and_cancels_the_rest(self):
        def cancel(ctx):
            loop_control.request_cancel(self.store, ctx.execution_id, by="requester", reason="stop")
            self.assertTrue(ctx.cancel_requested())
            return ok()

        handlers = {"A": self.rec.handler("A", [cancel]), "B": self.rec.handler("B")}
        _, execution = self.start_and_run(definition([node("A"), node("B", depends_on=["A"])]), handlers)
        self.assertEqual("CANCELLED", execution["status"])
        self.assertEqual("COMPLETED", execution["nodes"]["A"]["status"])
        self.assertEqual("CANCELLED", execution["nodes"]["B"]["status"])
        self.assertEqual(["A"], self.rec.calls)
        self.assertEqual("ABANDON_REQUESTED", execution["outcome"]["hermes_stop_reason"])

    def test_cancelling_an_idle_execution_applies_immediately(self):
        engine = self.engine({"A": self.rec.handler("A")})
        execution = engine.start(definition([node("A")]))
        result = loop_control.request_cancel(self.store, execution["execution_id"], by="requester", reason="drop")
        self.assertEqual("CANCELLED", result["status"])
        self.assertEqual("CANCELLED", engine.run(execution["execution_id"])["status"])
        self.assertEqual([], self.rec.calls)

    def test_security_approval_is_never_automatic(self):
        spec = definition([node("SEC", kind="gate", approval={"category": "SECURITY", "reason": "new secret scope"})])
        engine, execution = self.start_and_run(spec, {"SEC": self.rec.handler("SEC")})
        self.assertEqual("WAITING_APPROVAL", execution["status"])
        self.assertEqual("HUMAN_APPROVAL_REQUIRED", execution["halt"]["hermes_stop_reason"])
        identifier = execution["execution_id"]
        for actor, by, quote in (("agent", "requester", "ok"), ("human", "engine", "ok"), ("human", "requester", " ")):
            with self.assertRaises(loop_model.LoopError) as caught:
                loop_control.resolve(self.store, identifier, node="SEC", decision="approve", by=by, quote=quote, actor=actor)
            self.assertIn(caught.exception.code, {"HUMAN_ACTOR_REQUIRED", "SECURITY_APPROVAL_HUMAN_ONLY", "QUOTE_REQUIRED"})
        loop_control.resolve(self.store, identifier, node="SEC", decision="approve", by="requester", quote="aprovo", actor="human")
        self.assertEqual("COMPLETED", engine.run(identifier)["status"])

    def test_external_effects_wait_for_approval_and_a_rejection_fails_the_node(self):
        spec = definition([node("PR", effect="EXTERNAL")])
        engine, execution = self.start_and_run(spec, {"PR": self.rec.handler("PR")})
        self.assertEqual("WAITING_APPROVAL", execution["status"])
        self.assertEqual("EXTERNAL_MUTATION", execution["pending"]["category"])
        loop_control.resolve(self.store, execution["execution_id"], node="PR", decision="reject", by="requester", quote="não", actor="human")
        execution = engine.run(execution["execution_id"])
        self.assertEqual([], self.rec.calls)
        self.assertIn(execution["status"], {"FAILED", "BLOCKED"})

    def test_wait_nodes_ask_for_input(self):
        spec = definition([node("ASK", kind="wait", params={"prompt": "Which region?"}), node("USE", depends_on=["ASK"])])
        engine, execution = self.start_and_run(spec, {"USE": self.rec.handler("USE")})
        self.assertEqual("WAITING_INPUT", execution["status"])
        self.assertEqual("CLARIFICATION_REQUIRED", execution["halt"]["hermes_stop_reason"])
        loop_control.resolve(self.store, execution["execution_id"], node="ASK", decision="provide-input", value="sa-east-1",
                             by="requester", quote="sa-east-1", actor="human")
        execution = engine.run(execution["execution_id"])
        self.assertEqual("COMPLETED", execution["status"])
        self.assertEqual({"input": "sa-east-1"}, self.rec.contexts["USE"][0].inputs["ASK"])


if __name__ == "__main__":
    unittest.main()
