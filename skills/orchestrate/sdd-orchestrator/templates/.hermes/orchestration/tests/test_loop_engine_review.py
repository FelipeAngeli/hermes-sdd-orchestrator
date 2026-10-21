"""PR #61 review regressions for the loop engine: locking, budgets, approvers, tampering, storage, timeouts."""
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

    def _edit(self, identifier, mutate):
        path = self.store / identifier / "execution.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        mutate(value)
        path.write_text(json.dumps(value), encoding="utf-8")

    def assert_tampered(self, engine, identifier):
        with self.assertRaises(loop_model.LoopError) as caught:
            engine.run(identifier)
        self.assertEqual("EXECUTION_TAMPERED", caught.exception.code)

    def test_concurrent_starts_of_one_id_return_execution_exists(self):
        import threading

        results = []

        def start():
            try:
                results.append(self.engine({}).start(definition([node("A")]), execution_id="race-1")["execution_id"])
            except loop_model.LoopError as error:
                results.append(error.code)
            except Exception as error:  # noqa: BLE001
                results.append(type(error).__name__)

        threads = [threading.Thread(target=start) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertLessEqual(set(results), {"race-1", "EXECUTION_EXISTS"})
        self.assertEqual([], [path.name for path in self.store.iterdir() if path.name.startswith(".creating-")])

    def test_a_non_json_failure_is_a_malformed_result(self):
        bad = {"status": "FAILED", "output": {}, "failure": {"class": "FUNCTIONAL", "code": "X", "message": "m", 1: "x"}}
        engine = self.engine({"A": lambda ctx: bad})
        execution = engine.run(engine.start(definition([node("A", idempotent=True)]))["execution_id"])
        self.assertEqual("UNKNOWN_FAILURE", execution["halt"]["reason"])

    def test_lone_surrogates_are_a_malformed_result(self):
        for result in ({"status": "SUCCEEDED", "output": {"x": "\ud800"}},
                       {"status": "FAILED", "output": {}, "failure": {"class": "FUNCTIONAL", "code": "X", "message": "\ud800"}},
                       {"status": "SUCCEEDED", "output": {}, "needs_input": {"prompt": "\ud800"}}):
            with self.subTest(result=result):
                store = Path(self._tmp.name) / f"s{len(self.rec.calls)}-{id(result)}"
                engine = loop_engine.LoopEngine(store, handlers={"A": lambda ctx, value=result: value},
                                                clock=self.clock.time, sleep=self.clock.sleep)
                execution = engine.run(engine.start(definition([node("A", idempotent=True)]))["execution_id"])
                self.assertEqual("UNKNOWN_FAILURE", execution["halt"]["reason"])

    def test_a_non_json_output_is_a_malformed_result_not_a_crash(self):
        engine = self.engine({"A": lambda ctx: {"status": "SUCCEEDED", "output": {1: "a", "b": 2}}})
        execution = engine.run(engine.start(definition([node("A", idempotent=True)]))["execution_id"])
        self.assertEqual("UNKNOWN_FAILURE", execution["halt"]["reason"])
        self.assertEqual("RESULT_INVALID", execution["nodes"]["A"]["attempts"][-1]["failure"]["code"])

    def test_a_crash_during_start_leaves_no_half_execution(self):
        from unittest import mock

        engine = self.engine({"A": self.rec.handler("A")})
        with mock.patch.object(LoopStore, "_commit_in", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                engine.start(definition([node("A")]), execution_id="half-1")
        self.assertFalse((self.store / "half-1").exists())
        self.assertEqual([], [path.name for path in self.store.iterdir() if path.name.startswith(".creating-")])
        self.assertEqual("COMPLETED", engine.run(engine.start(definition([node("A")]), execution_id="half-1")["execution_id"])["status"])

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
