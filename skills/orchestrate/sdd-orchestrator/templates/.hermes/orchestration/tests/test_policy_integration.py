"""Security policy wired into the loop and gate engines: opt-in, halts on DENY, resumes after a human approval."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from loop_engine_fixtures import FakeClock, definition, node, ok  # noqa: E402

import loop_control  # noqa: E402
import loop_engine  # noqa: E402
import loop_security  # noqa: E402
import policy_hooks  # noqa: E402
import policy_registry  # noqa: E402
from loop_policy import TransientFailure  # noqa: E402


class LoopPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store, self.clock = root / "store", FakeClock()
        self.policy = policy_registry.load()
        self.dispatcher = policy_hooks.PolicyDispatcher(self.policy, root / "state", root, clock=self.clock.time)
        self.calls: list[tuple[str, dict]] = []

    def tearDown(self) -> None:
        self.temp.cleanup()

    def engine(self, handlers, policy=True):
        def recorded(hook, action):
            self.calls.append((hook, action))
            return self.dispatcher.dispatch(hook, action)
        return loop_engine.LoopEngine(self.store, handlers=handlers, clock=self.clock.time, sleep=self.clock.sleep,
                                      policy=recorded if policy else None)

    def test_without_a_policy_nothing_is_consulted(self):
        engine = self.engine({"a": lambda ctx: ok()}, policy=False)
        execution = engine.start(definition([node("a", params={"permission_mode": "bypassPermissions"})]))
        self.assertEqual("COMPLETED", engine.run(execution["execution_id"])["status"])
        self.assertEqual([], self.calls)

    def test_a_denied_agent_start_halts_the_node_and_never_runs_it(self):
        ran = []
        engine = self.engine({"a": lambda ctx: ran.append(1) or ok()})
        execution = engine.start(definition([node("a", kind="run-agent", params={"tools": ["terminal"], "allowed_tools": ["read_file"]})]))
        result = engine.run(execution["execution_id"])
        self.assertEqual(([], "POLICY_DENIED", "ACTION_BLOCKED"), (ran, result["halt"]["reason"], result["halt"]["hermes_stop_reason"]))
        self.assertEqual("permission_widening", result["pending"]["prompt"])

    def test_a_retry_past_the_attempt_budget_is_denied_instead_of_retried(self):
        attempts = []

        def flaky(ctx):
            attempts.append(1)
            raise TransientFailure("NET", "reset")
        strict = policy_registry.validate({**{k: v for k, v in self.policy.items() if k != "sha256"}, "budgets": {"max_attempts_per_node": 3}})
        self.dispatcher = policy_hooks.PolicyDispatcher({**strict, "sha256": "test"}, Path(self.temp.name) / "strict", Path(self.temp.name),
                                                        clock=self.clock.time)
        engine = self.engine({"a": flaky})
        execution = engine.start(definition([node("a", idempotent=True)], limits={"max_transient_retries": 5}))
        result = engine.run(execution["execution_id"])
        self.assertEqual("POLICY_DENIED", result["halt"]["reason"])
        self.assertEqual(3, len(attempts))  # the policy (3) stops it before the loop's own limit (6)
        self.assertIn("before_loop_retry", [hook for hook, _ in self.calls])

    def test_a_human_retry_after_a_denial_is_still_checked_again(self):
        engine = self.engine({"a": lambda ctx: ok()})
        execution = engine.start(definition([node("a", params={"sandbox": "danger-full-access"})]))
        identifier = execution["execution_id"]
        self.assertEqual("POLICY_DENIED", engine.run(identifier)["halt"]["reason"])
        loop_control.resolve(self.store, identifier, node="a", decision="retry", by="requester", quote="tenta de novo", actor="human")
        self.assertEqual("POLICY_DENIED", engine.run(identifier)["halt"]["reason"])

    def test_completion_is_recorded_in_the_security_audit(self):
        engine = self.engine({"a": lambda ctx: ok({"gate": {"verdict": "PASS", "decision_id": "gd-1"}})})
        execution = engine.start(definition([node("a")]))
        engine.run(execution["execution_id"])
        records = [r for r in self.dispatcher.audit.records() if r.get("hook") == "after_task_completion"]
        self.assertEqual(("ALLOW", 1), (records[0]["decision"], len(records)))

    def test_an_unknown_verdict_counts_as_a_denial(self):
        self.assertEqual(("POLICY_DENIED", None), loop_security.halt_reason(lambda h, a: {"decision": "MAYBE"}, "x", {}))
        self.assertEqual((None, None), loop_security.halt_reason(None, "x", {}))


class GatePolicyTests(unittest.TestCase):
    def test_a_gate_below_the_policy_minimum_mode_is_refused(self):
        import gate_engine

        with tempfile.TemporaryDirectory() as temp:
            policy = policy_registry.load()
            strict = {**policy, "gates": {"min_mode": "ENFORCE"}}
            dispatcher = policy_hooks.PolicyDispatcher(strict, Path(temp) / "state", Path(temp))
            repo = Path(temp) / "repo"
            repo.mkdir()
            gates = gate_engine.GateEngine(Path(temp) / "store", repo, commands={}, policy=dispatcher.dispatch)
            with self.assertRaises(ValueError) as caught:
                gates.evaluate("SPECIFY", "T-1", {"spec": "x"})
            self.assertIn("GATE_POLICY_DENIED", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
