"""Controller-owned acceptance tests for loop-engine persistence, crash recovery and idempotency.

Do not edit, skip or weaken these tests; the implementation must satisfy them with real logic.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from loop_engine_fixtures import RUNTIME, FakeClock, Recorder, definition, node, ok

import loop_control  # noqa: E402
import loop_engine  # noqa: E402
import loop_model  # noqa: E402
import loop_policy  # noqa: E402


class Crash(BaseException):
    """Simulates the process dying in the middle of a node (never caught by the engine)."""


CHILD = textwrap.dedent("""
    import json, os, sys
    sys.path.insert(0, sys.argv[1])
    import loop_engine
    store, calls = sys.argv[2], sys.argv[3]

    def record(name):
        with open(calls, "a", encoding="utf-8") as handle:
            handle.write(name + "\\n")

    def a(ctx):
        record("A")
        return {"status": "SUCCEEDED", "output": {"a": 1}}

    def b(ctx):
        record("B")
        ctx.checkpoint({"done": 1})
        os._exit(137)

    spec = {"schema_version": 1, "loop_id": "crash", "objective": "o", "nodes": [
        {"id": "A", "kind": "run-tool", "idempotent": True},
        {"id": "B", "kind": "run-tool", "idempotent": True, "depends_on": ["A"]},
        {"id": "C", "kind": "run-tool", "depends_on": ["B"]}]}
    engine = loop_engine.LoopEngine(store, handlers={"A": a, "B": b, "C": a})
    execution = engine.start(spec, execution_id="crash-1")
    engine.run(execution["execution_id"])
""")


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="loop-recovery-")
        self.root = Path(self._tmp.name)
        self.store = self.root / "store"
        self.clock = FakeClock()
        self.rec = Recorder()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def engine(self, handlers, reconcilers=None):
        return loop_engine.LoopEngine(self.store, handlers=handlers, reconcilers=reconcilers or {},
                                      clock=self.clock.time, sleep=self.clock.sleep)

    def crash_then_restart(self, push_node, reconcilers=None):
        def crash(ctx):
            self.rec.calls.append("PUSH")
            raise Crash()

        spec = definition([node("A"), push_node, node("C", depends_on=["PUSH"])])
        first = self.engine({"A": self.rec.handler("A"), "PUSH": crash, "C": self.rec.handler("C")})
        execution = first.start(spec)
        with self.assertRaises(Crash):
            first.run(execution["execution_id"])
        second = self.engine({"A": self.rec.handler("A"), "PUSH": self.rec.handler("PUSH"), "C": self.rec.handler("C")}, reconcilers)
        return second, second.run(execution["execution_id"])


class ProcessCrashTests(Base):
    def test_a_killed_process_resumes_without_repeating_completed_nodes(self):
        calls = self.root / "calls.txt"
        script = self.root / "child.py"
        script.write_text(CHILD, encoding="utf-8")
        completed = subprocess.run([sys.executable, str(script), str(RUNTIME), str(self.store), str(calls)], timeout=60)
        self.assertEqual(137, completed.returncode)
        self.assertEqual(["A", "B"], calls.read_text(encoding="utf-8").split())

        on_disk = json.loads((self.store / "crash-1" / "execution.json").read_text(encoding="utf-8"))
        self.assertEqual("RUNNING", on_disk["nodes"]["B"]["status"])
        self.assertEqual({"done": 1}, on_disk["nodes"]["B"]["partial"])

        handlers = {name: self.rec.handler(name) for name in "ABC"}
        execution = self.engine(handlers).run("crash-1")
        self.assertEqual(["B", "C"], self.rec.calls)
        self.assertEqual({"done": 1}, self.rec.contexts["B"][0].partial)
        self.assertEqual("COMPLETED", execution["status"])
        self.assertEqual("INTERRUPTED", execution["nodes"]["B"]["attempts"][0]["result"])

    def test_the_checkpoint_is_on_disk_before_the_handler_runs(self):
        seen = {}

        def inspect(ctx):
            on_disk = json.loads((self.store / ctx.execution_id / "execution.json").read_text(encoding="utf-8"))
            seen["status"] = on_disk["nodes"]["A"]["status"]
            seen["key"] = on_disk["nodes"]["A"]["attempts"][-1]["idempotency_key"]
            return ok()

        engine = self.engine({"A": inspect})
        execution = engine.start(definition([node("A")]))
        engine.run(execution["execution_id"])
        self.assertEqual("RUNNING", seen["status"])
        self.assertTrue(seen["key"].startswith(execution["execution_id"] + ":A:"))

    def test_repeated_crashes_are_bounded(self):
        def crash(ctx):
            raise Crash()

        spec = definition([node("A", idempotent=True)])
        execution = self.engine({"A": crash}).start(spec)
        for _ in range(3):
            with self.assertRaises(Crash):
                self.engine({"A": crash}).run(execution["execution_id"])
        execution = self.engine({"A": crash}).run(execution["execution_id"])
        self.assertEqual("BLOCKED", execution["status"])
        self.assertEqual("TRANSIENT_RETRIES_EXHAUSTED", execution["halt"]["reason"])


class IdempotencyTests(Base):
    def test_a_crashed_non_idempotent_node_is_never_rerun_automatically(self):
        engine, execution = self.crash_then_restart(node("PUSH", depends_on=["A"], idempotent=False))
        self.assertEqual(["A", "PUSH"], self.rec.calls)
        self.assertEqual("BLOCKED", execution["status"])
        self.assertEqual("EFFECT_AMBIGUOUS", execution["halt"]["reason"])
        self.assertEqual("ACTION_RECOVERY_REQUIRED", execution["halt"]["hermes_stop_reason"])
        loop_control.resolve(self.store, execution["execution_id"], node="PUSH", decision="mark-applied",
                             value=json.dumps({"sha": "abc"}), by="requester", quote="o push foi feito", actor="human")
        execution = engine.run(execution["execution_id"])
        self.assertEqual(["A", "PUSH", "C"], self.rec.calls)
        self.assertEqual({"sha": "abc"}, execution["nodes"]["PUSH"]["output"])
        self.assertEqual("COMPLETED", execution["status"])

    def test_a_reconciler_that_proves_the_effect_avoids_a_duplicate(self):
        reconcilers = {"PUSH": lambda ctx: {"applied": True, "output": {"sha": "def"}}}
        _, execution = self.crash_then_restart(node("PUSH", depends_on=["A"]), reconcilers)
        self.assertEqual(["A", "PUSH", "C"], self.rec.calls)
        self.assertEqual({"sha": "def"}, execution["nodes"]["PUSH"]["output"])
        self.assertEqual("RECONCILED", execution["nodes"]["PUSH"]["attempts"][0]["result"])

    def test_a_reconciler_that_proves_no_effect_allows_one_rerun(self):
        reconcilers = {"PUSH": lambda ctx: {"applied": False}}
        _, execution = self.crash_then_restart(node("PUSH", depends_on=["A"]), reconcilers)
        self.assertEqual(["A", "PUSH", "PUSH", "C"], self.rec.calls)
        self.assertEqual("COMPLETED", execution["status"])

    def test_an_inconclusive_reconciler_still_blocks(self):
        reconcilers = {"PUSH": lambda ctx: {"applied": None}}
        _, execution = self.crash_then_restart(node("PUSH", depends_on=["A"]), reconcilers)
        self.assertEqual("EFFECT_AMBIGUOUS", execution["halt"]["reason"])

    def test_a_human_authorized_rerun_is_recorded(self):
        engine, execution = self.crash_then_restart(node("PUSH", depends_on=["A"]))
        loop_control.resolve(self.store, execution["execution_id"], node="PUSH", decision="retry",
                             by="requester", quote="pode repetir o push", actor="human")
        execution = engine.run(execution["execution_id"])
        self.assertEqual(["A", "PUSH", "PUSH", "C"], self.rec.calls)
        decisions = [entry for entry in execution["decisions"] if entry["decision"] == "retry"]
        self.assertEqual("pode repetir o push", decisions[0]["quote"])

    def test_partial_progress_reaches_the_retry_of_a_transient_failure(self):
        def first(ctx):
            ctx.checkpoint({"pages": 3})
            raise loop_policy.TransientFailure("NET", "reset")

        handler = self.rec.handler("A", [first, ok()])
        engine = self.engine({"A": handler})
        execution = engine.start(definition([node("A", idempotent=True)]))
        execution = engine.run(execution["execution_id"])
        self.assertEqual({"pages": 3}, self.rec.contexts["A"][1].partial)
        self.assertEqual("COMPLETED", execution["status"])


class IntegrityTests(Base):
    def test_a_changed_definition_or_corrupt_execution_is_refused(self):
        engine = self.engine({"A": self.rec.handler("A")})
        execution = engine.start(definition([node("A")]))
        directory = self.store / execution["execution_id"]
        original = (directory / "definition.json").read_text(encoding="utf-8")
        (directory / "definition.json").write_text(original.replace("Exercise", "Changed"), encoding="utf-8")
        with self.assertRaises(loop_model.LoopError) as caught:
            engine.run(execution["execution_id"])
        self.assertEqual("DEFINITION_CHANGED", caught.exception.code)
        (directory / "definition.json").write_text(original, encoding="utf-8")
        (directory / "execution.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(loop_model.LoopError) as caught:
            engine.run(execution["execution_id"])
        self.assertEqual("EXECUTION_CORRUPT", caught.exception.code)
        self.assertEqual([], self.rec.calls)

    def test_start_is_idempotent_for_the_same_definition(self):
        engine = self.engine({"A": self.rec.handler("A")})
        first = engine.start(definition([node("A")]), execution_id="same-1")
        again = engine.start(definition([node("A")]), execution_id="same-1")
        self.assertEqual(first["execution_id"], again["execution_id"])
        with self.assertRaises(loop_model.LoopError) as caught:
            engine.start(definition([node("B")]), execution_id="same-1")
        self.assertEqual("EXECUTION_EXISTS", caught.exception.code)

    def test_every_transition_is_journaled_in_order(self):
        engine = self.engine({"A": self.rec.handler("A")})
        execution = engine.start(definition([node("A")]))
        execution = engine.run(execution["execution_id"])
        lines = (self.store / execution["execution_id"] / "events.jsonl").read_text(encoding="utf-8").splitlines()
        events = [json.loads(line) for line in lines]
        self.assertEqual(list(range(1, len(events) + 1)), [event["seq"] for event in events])
        kinds = [event["event"] for event in events]
        self.assertLess(kinds.index("NODE_STARTED"), kinds.index("NODE_COMPLETED"))
        self.assertEqual("EXECUTION_COMPLETED", kinds[-1])
        self.assertEqual(execution["checkpoint"]["seq"], events[-1]["seq"])


if __name__ == "__main__":
    unittest.main()
