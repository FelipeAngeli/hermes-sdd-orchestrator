"""Controller-owned acceptance tests for the context engine: protection and integrity.

These tests encode the owner's acceptance criteria (protection rules, tool-call
integrity, dry-run default, explicit activation, fail-open). They use a fake
evaluator only; no network call is made.
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))

import context_engine  # noqa: E402
import semantic_governor  # noqa: E402
from context_config import DEFAULT_CONFIG_PATH, load_config  # noqa: E402

PROSE = (
    "The weather log for station {i} lists ordinary readings of wind and humidity across the valley. "
    "Observers noted calm mornings, light breeze in the afternoon and clear skies by evening. "
) * 9
CONSENT = (
    "# Setup\n\n```yaml\nschema_version: 1\nstatus: COMPLETE\nanswers:\n"
    "  issue_tracker: none\n  obsidian: none\n"
    '  typesafe_ai: {"install": true, "automatic_semantic_governance": true}\n'
    "  project_tools: none\n```\n"
)
PROTECTED_LINES = (
    "AC-3 acceptance: response code is 201",
    "The endpoint MUST return 201 for a new vehicle",
    "ADR-0007 decision: use the outbox queue",
    "security: never log the session token",
    "Ran 12 tests in 0.4s OK",
    "blocked by the pending migration of the fleet table",
)


class FakeJev:
    """Thread-safe fake evaluator answering one probability for every question."""

    def __init__(self, probability=0.0, *, delay=0.0, raises=None, response=None):
        self.probability = probability
        self.delay = delay
        self.raises = raises
        self.response = response
        self.payloads = []
        self.lock = threading.Lock()
        self.in_flight = 0
        self.max_in_flight = 0

    def __call__(self, payload):
        with self.lock:
            self.payloads.append(copy.deepcopy(payload))
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay:
                threading.Event().wait(self.delay)
            if self.raises is not None:
                raise self.raises
            if self.response is not None:
                return self.response
            answers = {key: {"type": "noul", "noul": self.probability} for key in payload["questions"]}
            return {"status": "OK", "result": {"model": "jev-test", "answers": answers, "usage": {"input_tokens": 10, "output_tokens": 2}}}
        finally:
            with self.lock:
                self.in_flight -= 1

    def sent_text(self):
        return json.dumps(self.payloads, ensure_ascii=False)


def shipped_config():
    config, error = load_config(DEFAULT_CONFIG_PATH)
    assert error is None, error
    return config


def active_config(**overrides):
    config = shipped_config()
    config.update(enabled=True, dry_run=False, apply_enabled=True, evaluator="typesafe")
    config.update(overrides)
    return config


def large_anthropic(count=110, extra=None):
    """A completed tool session above the 50k-token trigger, plus protected content."""
    messages = [{"role": "user", "content": "Summarize the station logs and keep every acceptance criterion."}]
    for index in range(count):
        messages.append({"role": "assistant", "content": [{"type": "tool_use", "id": f"toolu_{index}", "name": "read_log", "input": {"path": f"logs/station_{index}.txt"}}]})
        messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"toolu_{index}", "content": PROSE.format(i=index)}]})
    messages.extend(extra or [])
    messages.append({"role": "assistant", "content": [{"type": "text", "text": "Reading the final log now."}]})
    messages.append({"role": "user", "content": [{"type": "text", "text": "Continue."}]})
    return {"model": "m", "messages": messages}


class EngineCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.audit = self.root / "state"
        self.setup_file = self.root / "PROJECT_SETUP.md"
        self.setup_file.write_text(CONSENT, encoding="utf-8")
        self.env_file = self.root / ".env"
        patcher = mock.patch.object(semantic_governor, "_connector_preflight", lambda provider, env_file: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.temp.cleanup()

    def run_engine(self, native, config, fake, *, apply=True, fmt="anthropic", stage="TEST", task="Summarize the logs."):
        raw = native if isinstance(native, bytes) else json.dumps(native, ensure_ascii=False).encode("utf-8")
        return context_engine.optimize(
            raw, fmt, stage, task, config, evaluate=fake, apply=apply, audit_dir=str(self.audit),
            env_file=str(self.env_file), project_setup=str(self.setup_file),
        )


class DefaultsAndActivationTests(EngineCase):
    def test_ac_shipped_policy_is_disabled_dry_run_and_never_calls(self):
        config = shipped_config()
        self.assertFalse(config["enabled"])
        self.assertTrue(config["dry_run"])
        self.assertFalse(config["apply_enabled"])
        self.assertEqual("none", config["evaluator"])
        self.assertEqual(50000, config["trigger_estimated_tokens"])
        self.assertEqual(4, config["max_concurrency"])
        native = large_anthropic()
        fake = FakeJev()
        result = self.run_engine(native, config, fake)
        self.assertEqual("DISABLED", result["status"])
        self.assertEqual(native, result["request"])
        self.assertEqual([], fake.payloads)

    def test_ac_real_omission_requires_all_three_switches(self):
        native = large_anthropic()
        for overrides, apply in (({"dry_run": True}, True), ({"apply_enabled": False}, True), ({}, False)):
            with self.subTest(overrides=overrides, apply=apply):
                result = self.run_engine(native, active_config(**overrides), FakeJev(), apply=apply)
                self.assertEqual("DRY_RUN", result["status"])
                self.assertEqual(native, result["request"])
        self.assertEqual("APPLIED", self.run_engine(native, active_config(), FakeJev())["status"])

    def test_ac_dry_run_request_equals_baseline_and_lists_would_omit(self):
        native = large_anthropic()
        result = self.run_engine(native, active_config(dry_run=True), FakeJev())
        self.assertEqual("DRY_RUN", result["status"])
        self.assertEqual(native, result["request"])
        self.assertTrue(result["would_omit"])

    def test_ac_small_context_is_skipped_without_evaluator_call(self):
        fake = FakeJev()
        result = self.run_engine(large_anthropic(count=3), active_config(), fake)
        self.assertEqual("SKIPPED_BELOW_TRIGGER", result["status"])
        self.assertEqual([], fake.payloads)

    def test_ac_configurable_thresholds_and_stage_override(self):
        native = large_anthropic()
        self.assertEqual("DRY_RUN", self.run_engine(native, active_config(), FakeJev(0.3))["status"])
        config = active_config()
        config["stages"]["TEST"]["thresholds"] = {"fact_loss_max": 0.5, "constraint_loss_max": 0.5}
        self.assertEqual("APPLIED", self.run_engine(native, config, FakeJev(0.3))["status"])


class ProtectionTests(EngineCase):
    def protected_session(self):
        extra = [
            {"role": "assistant", "content": [{"type": "tool_use", "id": f"toolu_p{index}", "name": "read_note", "input": {"n": index}}]}
            for index in range(len(PROTECTED_LINES))
        ]
        results = [
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"toolu_p{index}", "content": f"{PROSE.format(i=900 + index)}\n{line}\n"}]}
            for index, line in enumerate(PROTECTED_LINES)
        ]
        pairs = [item for pair in zip(extra, results) for item in pair]
        pairs.append({"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_err", "name": "run", "input": {}}]})
        pairs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_err", "is_error": True, "content": PROSE.format(i=950)}]})
        pairs.append({"role": "assistant", "content": [{"type": "thinking", "thinking": PROSE.format(i=960), "signature": "sig"}, {"type": "tool_use", "id": "toolu_pending", "name": "deploy", "input": {"target": "staging"}}]})
        return large_anthropic(extra=pairs)

    def test_ac_mandatory_facts_and_constraints_survive_apply(self):
        native = self.protected_session()
        fake = FakeJev(0.0)
        result = self.run_engine(native, active_config(), fake)
        self.assertEqual("APPLIED", result["status"])
        rendered = json.dumps(result["request"], ensure_ascii=False)
        for line in PROTECTED_LINES:
            with self.subTest(line=line):
                self.assertIn(line, rendered)
                self.assertNotIn(line, fake.sent_text())
        self.assertIn(PROSE.format(i=950), rendered, "is_error tool_result must stay")
        self.assertIn(PROSE.format(i=960), rendered, "opaque thinking block must stay")
        self.assertIn("toolu_pending", rendered)
        self.assertEqual(native["messages"][0], result["request"]["messages"][0], "user instruction")
        self.assertEqual(native["messages"][-2:], result["request"]["messages"][-2:], "recent turns")

    def test_ac_user_text_is_never_a_candidate_even_if_eligible(self):
        native = large_anthropic()
        native["messages"][0]["content"] = PROSE.format(i=999)
        config = active_config()
        config["stages"]["TEST"]["eligible_kinds"] = ["TOOL_RESULT", "ASSISTANT_TEXT"]
        fake = FakeJev(0.0)
        result = self.run_engine(native, config, fake)
        self.assertEqual(native["messages"][0], result["request"]["messages"][0])
        passages = [passage["text"] for payload in fake.payloads for passage in payload["state"]["passages"]]
        self.assertNotIn(PROSE.format(i=999), passages, "user text may give context to Jev but is never a candidate passage")


class IntegrityTests(EngineCase):
    def test_ac_anthropic_tool_ids_pairing_and_non_text_items_unchanged(self):
        native = large_anthropic()
        native["messages"][2]["content"][0]["content"] = [
            {"type": "text", "text": PROSE.format(i=2)},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
        ]
        result = self.run_engine(native, active_config(), FakeJev(0.0))
        self.assertEqual("APPLIED", result["status"])
        after = result["request"]["messages"]
        self.assertEqual(len(native["messages"]), len(after))
        for before_message, after_message in zip(native["messages"], after):
            self.assertEqual(before_message["role"], after_message["role"])
            if isinstance(before_message["content"], list):
                for left, right in zip(before_message["content"], after_message["content"]):
                    self.assertEqual(set(left), set(right))
                    for key in ("type", "id", "name", "input", "tool_use_id"):
                        self.assertEqual(left.get(key), right.get(key))
        self.assertEqual(native["messages"][2]["content"][0]["content"][1], after[2]["content"][0]["content"][1])
        self.assertIn("context-engine omitted segment", json.dumps(after))

    def test_ac_openai_output_field_rewritten_without_new_keys(self):
        items = [{"type": "message", "role": "user", "content": "Summarize the station logs."}, {"type": "reasoning", "id": "rs_1", "summary": []}]
        for index in range(110):
            items.append({"type": "function_call", "call_id": f"call_{index}", "name": "read_log", "arguments": json.dumps({"path": f"logs/{index}.txt"})})
            items.append({"type": "function_call_output", "call_id": f"call_{index}", "output": PROSE.format(i=index)})
        items.append({"type": "message", "role": "user", "content": "Continue."})
        native = {"model": "m", "input": items}
        result = self.run_engine(native, active_config(), FakeJev(0.0), fmt="openai")
        self.assertEqual("APPLIED", result["status"])
        after = result["request"]["input"]
        self.assertEqual(len(items), len(after))
        for left, right in zip(items, after):
            self.assertEqual(set(left), set(right), "no key may be added or removed")
            self.assertEqual(left.get("call_id"), right.get("call_id"))
            if left["type"] != "function_call_output":
                self.assertEqual(left, right)
        self.assertIn("context-engine omitted segment", after[3]["output"])


class FailOpenTests(EngineCase):
    def test_ac_evaluator_failures_preserve_everything(self):
        native = large_anthropic()
        cases = {
            "raises": FakeJev(raises=RuntimeError("connection reset")),
            "non_dict": FakeJev(response=["unexpected"]),
            "error_status": FakeJev(response={"status": "ERROR", "reason": "TYPESAFE_RATE_LIMITED"}),
            "out_of_range": FakeJev(1.5),
            "nan": FakeJev(float("nan")),
            "missing_answers": FakeJev(response={"status": "OK", "result": {"model": "x", "answers": {}, "usage": {}}}),
        }
        for name, fake in cases.items():
            with self.subTest(case=name):
                result = self.run_engine(native, active_config(), fake)
                self.assertEqual(native, result["request"])
                self.assertEqual(0, result["metrics"]["segments_omitted"])
                self.assertTrue(result["metrics"]["evaluation_errors"])
        retry = FakeJev(0.0)
        self.run_engine(native, active_config(), retry)
        self.assertTrue(retry.payloads, "failed evaluations must never be cached as verdicts")

    def test_ac_timeout_is_bounded_and_preserves(self):
        native = large_anthropic(count=60)
        config = active_config(evaluator_timeout_seconds=1, trigger_estimated_tokens=1000)
        import time

        begin = time.monotonic()
        result = self.run_engine(native, config, FakeJev(0.0, delay=5))
        elapsed = time.monotonic() - begin
        self.assertLess(elapsed, 4.0, "a hung evaluator must not block past the deadline")
        self.assertEqual(native, result["request"])
        self.assertIn("TIMEOUT", result["metrics"]["evaluation_errors"])

    def test_ac_malformed_input_fails_open_without_raising(self):
        for raw in (b"{not json", b"\xff\xfe\x00", b"[1,2]"):
            with self.subTest(raw=raw):
                result = self.run_engine(raw, active_config(trigger_estimated_tokens=0), FakeJev())
                self.assertEqual("FAIL_OPEN", result["status"])

    def test_ac_concurrency_cap_and_parallelism(self):
        fake = FakeJev(0.0, delay=0.05)
        self.run_engine(large_anthropic(count=40), active_config(trigger_estimated_tokens=1000), fake)
        self.assertLessEqual(fake.max_in_flight, 4)
        self.assertGreater(fake.max_in_flight, 1)

    def test_ac_payload_byte_cap(self):
        fake = FakeJev(0.0)
        self.run_engine(large_anthropic(count=20), active_config(trigger_estimated_tokens=1000, max_payload_bytes=1500), fake)
        for payload in fake.payloads:
            self.assertLessEqual(len(json.dumps(payload, ensure_ascii=False).encode("utf-8")), 1500)


if __name__ == "__main__":
    unittest.main()
