"""PR #61 review regressions for loop-engine integrity: tampering, rollback, roll-forward recovery."""
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


class IntegrityRegressions(unittest.TestCase):
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

    def test_restoring_an_older_execution_file_is_refused(self):
        spec = definition([node("A"), node("B", depends_on=["A"])])
        engine = self.engine({name: self.rec.handler(name) for name in "AB"})
        identifier = engine.start(spec)["execution_id"]
        path = self.store / identifier / "execution.json"
        early = path.read_text(encoding="utf-8")
        self.assertEqual("COMPLETED", engine.run(identifier)["status"])
        events_before = (self.store / identifier / "events.jsonl").read_text(encoding="utf-8")
        path.write_text(early, encoding="utf-8")
        self.assert_tampered(engine, identifier)
        with self.assertRaises(loop_model.LoopError) as caught:
            loop_control.request_cancel(self.store, identifier, by="requester", reason="x")
        self.assertEqual("EXECUTION_TAMPERED", caught.exception.code)
        self.assertEqual(events_before, (self.store / identifier / "events.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(["A", "B"], self.rec.calls)

    def test_a_crash_after_the_event_rolls_forward_instead_of_dropping_it(self):
        from unittest import mock
        import loop_store

        spec = definition([node("SEC", kind="gate", approval={"category": "SECURITY", "reason": "r"}),
                           node("PUSH", depends_on=["SEC"])])
        engine = self.engine({"SEC": self.rec.handler("SEC"), "PUSH": self.rec.handler("PUSH")})
        identifier = engine.run(engine.start(spec)["execution_id"])["execution_id"]
        real = loop_store.atomic_write_json

        def crash_on_final_replace(path, value):
            if path.name == "execution.json":
                raise KeyboardInterrupt("power loss")
            return real(path, value)

        with mock.patch.object(loop_store, "atomic_write_json", side_effect=crash_on_final_replace):
            with self.assertRaises(KeyboardInterrupt):
                self.resolve(identifier, node="SEC", decision="reject", by="requester", quote="não")
        execution = LoopStore(self.store).load(identifier)
        self.assertEqual("WAITING_APPROVAL", execution["status"], "execution.json still holds the pre-reject state")
        execution = engine.run(identifier)
        self.assertEqual("FAILED", execution["status"])
        self.assertEqual("APPROVAL_REJECTED", execution["outcome"]["reason"])
        self.assertEqual([], self.rec.calls)

    def test_restoring_the_previous_step_cannot_undo_a_reject(self):
        spec = definition([node("SEC", kind="gate", approval={"category": "SECURITY", "reason": "r"}),
                           node("PUSH", depends_on=["SEC"])])
        engine = self.engine({"SEC": self.rec.handler("SEC"), "PUSH": self.rec.handler("PUSH")})
        identifier = engine.run(engine.start(spec)["execution_id"])["execution_id"]
        path = self.store / identifier / "execution.json"
        before = path.read_text(encoding="utf-8")
        self.resolve(identifier, node="SEC", decision="reject", by="requester", quote="não")
        path.write_text(before, encoding="utf-8")
        execution = engine.run(identifier)
        self.assertEqual("FAILED", execution["status"])
        self.assertIn("reject", [event.get("decision") for event in LoopStore(self.store).events(identifier)])
        (self.store / identifier / "execution.pending.json").unlink()
        path.write_text(before, encoding="utf-8")
        self.assert_tampered(engine, identifier)
        self.assertEqual([], self.rec.calls)

    def test_a_forged_trailing_event_or_pending_state_is_refused_not_looped(self):
        engine = self.engine({"A": self.rec.handler("A"), "B": self.rec.handler("B")})
        spec = definition([node("A"), node("B", depends_on=["A"], kind="wait")])
        identifier = engine.run(engine.start(spec)["execution_id"])["execution_id"]
        directory = self.store / identifier
        pending = json.loads((directory / "execution.pending.json").read_text(encoding="utf-8"))
        committed = pending["checkpoint"]["seq"]
        events = (directory / "events.jsonl").read_bytes()
        forged = {"seq": committed + 1, "event": pending["checkpoint"]["event"], "state_sha256": loop_model.digest(pending)}
        (directory / "events.jsonl").write_bytes(events + json.dumps(forged).encode() + b"\n")
        self.assert_tampered(engine, identifier)
        broken = dict(pending, checkpoint={"seq": committed + 1, "at": 0, "event": "X"}, status="NOT_A_STATE")
        (directory / "execution.pending.json").write_text(json.dumps(broken), encoding="utf-8")
        forged = {"seq": committed + 1, "event": "X", "state_sha256": loop_model.digest(broken)}
        (directory / "events.jsonl").write_bytes(events + json.dumps(forged).encode() + b"\n")
        self.assert_tampered(engine, identifier)
        self.assertEqual(committed, LoopStore(self.store).load(identifier)["checkpoint"]["seq"], "the good checkpoint survives")

    def test_cancel_refuses_a_forged_state(self):
        spec = definition([node("SEC", kind="gate", approval={"category": "SECURITY", "reason": "r"})])
        engine = self.engine({"SEC": self.rec.handler("SEC")})
        identifier = engine.run(engine.start(spec)["execution_id"])["execution_id"]
        self._edit(identifier, lambda value: value["nodes"]["SEC"].update(status="COMPLETED"))
        with self.assertRaises(loop_model.LoopError) as caught:
            loop_control.request_cancel(self.store, identifier, by="requester", reason="x")
        self.assertEqual("EXECUTION_TAMPERED", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
