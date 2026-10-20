"""Regression tests for the second and third review rounds of the context engine."""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent / "runtime"))

import context_audit  # noqa: E402
import context_judge  # noqa: E402
import context_policy  # noqa: E402
from context_collector import collect  # noqa: E402
from context_config import DEFAULT_CONFIG_PATH, load_config  # noqa: E402
from context_rebuilder import rebuild  # noqa: E402
from context_segmenter import Segment, segment, split_text  # noqa: E402
from test_context_units import SEGMENTING, anthropic, first_segment  # noqa: E402


class Round2RegressionTests(unittest.TestCase):
    """Second-round review findings (security N1, pr-reviewer N1-N13)."""

    def test_r2_secret_split_across_spans_protects_every_span(self):
        key = "-----BEGIN " + "PRIVATE KEY-----\n" + "\n".join("QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo" + str(index) for index in range(120)) + "\n-----END " + "PRIVATE KEY-----\n"
        request = collect(json.dumps(anthropic("header line\n" + key)).encode(), "anthropic")
        items = segment(request, SEGMENTING, "TEST")
        self.assertGreater(len(items), 1, "the fixture must span several segments")
        for item in items:
            with self.subTest(span=item.id):
                self.assertEqual("secret-present", context_policy.protection(item, request, SEGMENTING))

    def test_r2_long_single_line_is_never_cut(self):
        line = "x" * 6000
        self.assertEqual([(0, 6001)], split_text(line + "\n", 1200, 2400))
        text = "short\n" + line + "\nshort tail\n"
        spans = split_text(text, 1200, 2400)
        self.assertTrue(all(end == len(text) or text[end - 1] == "\n" for _, end in spans))

    def test_r2_code_in_task_and_constraints_is_not_sent_by_default(self):
        task = "Fix this:\n```\nsecret_algorithm()\n```\n"
        constraints = "keep it\n    line_one()\n    line_two()\n    line_three()\n"
        payload = context_judge.build_payload(task, constraints, "passage", 24000)
        dumped = json.dumps(payload)
        self.assertNotIn("secret_algorithm", dumped)
        self.assertNotIn("line_two", dumped)
        self.assertIn(context_judge.CODE_PLACEHOLDER, dumped)
        sent = json.dumps(context_judge.build_payload(task, constraints, "passage", 24000, send_code=True))
        self.assertIn("secret_algorithm", sent)

    def test_r2_responses_input_text_parts_are_read(self):
        native = {"input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "AC-4 keep the plate"}]}]}
        request = collect(json.dumps(native).encode(), "openai")
        self.assertEqual("AC-4 keep the plate", request.messages[0].blocks[0].text)
        self.assertIn("AC-4", context_policy.requirement_markers(request))

    def test_r2_decision_prefix_is_case_insensitive(self):
        request, item = first_segment(anthropic("Decision: use the outbox"))
        self.assertEqual("architecture-decision", context_policy.protection(item, request, SEGMENTING))

    def test_r2_protected_span_after_an_omission_in_the_same_field(self):
        text = "word " * 300 + "\n\n" + "AC-9 keep\n" + "word " * 300 + "\n"
        request = collect(json.dumps(anthropic(text)).encode(), "anthropic")
        items = segment(request, dict(SEGMENTING, max_segment_chars=1600, segment_target_chars=800), "TEST")
        protected = [item for item in items if context_policy.protection(item, request, SEGMENTING)]
        omitted = [item for item in items if item not in protected]
        self.assertTrue(protected and omitted and omitted[0].start < protected[0].start)
        rebuilt, errors = rebuild(request, omitted[:1], "65f0a1b2_abcdefghijklmnop", protected)
        self.assertEqual([], errors)

    def test_r2_marker_names_the_audit_dir(self):
        request, item = first_segment(anthropic("old output"))
        rebuilt, _ = rebuild(request, [item], "65f0a1b2_abcdefghijklmnop")
        self.assertIn("--audit-dir", rebuilt.native["messages"][1]["content"][0]["content"])

    def test_r2_state_dir_must_be_owner_private_and_purge_ignores_foreign_names(self):
        with tempfile.TemporaryDirectory() as directory:
            shared = Path(directory) / "shared"
            shared.mkdir(mode=0o755)
            os.chmod(shared, 0o755)
            with self.assertRaises(ValueError):
                context_audit.ContextAudit(shared).start(b"x")
            self.assertEqual(0o755, stat.S_IMODE(shared.stat().st_mode), "permissions of a foreign dir are not changed")
            root = Path(directory) / "state"
            context_audit.ContextAudit(root).start(b"x")
            foreign = root / "audit" / "node_modules"
            foreign.mkdir()
            old = time.time() - 9 * 86400
            os.utime(foreign, (old, old))
            context_audit.purge(root, 1)
            self.assertTrue(foreign.exists())

    def test_r2_cancelled_calls_are_not_counted(self):
        config, _ = load_config(DEFAULT_CONFIG_PATH)
        config.update(evaluator="typesafe", max_concurrency=1, evaluator_timeout_seconds=0.3)
        items = [Segment(f"1.0@{index}:5", (1, 0), 0, 5, "h", "TOOL_RESULT", False, "plain") for index in range(5)]
        report = context_judge.JevJudge(config, lambda payload: time.sleep(1)).judge(items)
        self.assertEqual(1, report.calls)
        self.assertEqual(5, list(report.errors.values()).count("TIMEOUT"))


class Round3RegressionTests(unittest.TestCase):
    """Third-round review findings (R3-L1, R3-L3/R3-2, T1, T2)."""

    def test_r3_role_only_responses_items_and_multi_part_text(self):
        native = {"input": [
            {"role": "user", "content": "AC-5 keep the fleet id"},
            {"type": "function_call", "call_id": "c1", "name": "read", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "c1", "output": [{"type": "output_text", "text": "first part"}, {"type": "output_text", "text": "second part"}]},
        ]}
        request = collect(json.dumps(native).encode(), "openai")
        self.assertEqual("text", request.messages[0].blocks[0].type)
        self.assertIn("AC-5", context_policy.requirement_markers(request))
        result = request.messages[2].blocks[0]
        self.assertEqual("first part\nsecond part", result.text)
        self.assertEqual([], segment(request, SEGMENTING, "TEST"), "a multi-part field is never segmented")

    def test_r3_span_longer_than_max_segment_chars_is_never_sent(self):
        config, _ = load_config(DEFAULT_CONFIG_PATH)
        config.update(evaluator="typesafe")
        sent = []
        long_line = Segment("1.0@0:6000", (1, 0), 0, 6000, "h", "TOOL_RESULT", False, "y" * 6000)
        report = context_judge.JevJudge(config, sent.append).judge([long_line])
        self.assertEqual("SEGMENT_TOO_LARGE", report.errors["1.0@0:6000"])
        self.assertEqual([], sent)

    def test_r3_state_dir_inside_another_repository_is_refused(self):
        import context_engine

        with tempfile.TemporaryDirectory() as directory:
            other = Path(directory).resolve() / "other-repo"
            (other / ".git").mkdir(parents=True)
            self.assertTrue(context_engine.inside_repository(other / "state"))
            self.assertFalse(context_engine.inside_repository(Path(directory).resolve() / "plain" / "state"))

    def test_r3_cli_reports_config_error_and_savings_ignore_formatting(self):
        import contextlib
        import io

        import context_cli
        import context_engine

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "in.json"
            source.write_text(json.dumps(anthropic("plain")))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = context_cli.main(["optimize", "--input", str(source), "--format", "anthropic", "--stage", "TEST", "--task", "t", "--config", str(Path(directory) / "missing.md")])
            report = json.loads(output.getvalue())
            self.assertEqual(("DISABLED", "CONFIG_MISSING", 2), (report["status"], report["config_error"], code))
        config, _ = load_config(DEFAULT_CONFIG_PATH)
        config.update(enabled=True, trigger_estimated_tokens=0)
        native = anthropic("plain")
        compact = context_engine.optimize(json.dumps(native).encode(), "anthropic", "TEST", "t", config, audit_dir=tempfile.mkdtemp())
        pretty = context_engine.optimize(json.dumps(native, indent=4).encode(), "anthropic", "TEST", "t", config, audit_dir=tempfile.mkdtemp())
        self.assertEqual(compact["metrics"]["input_tokens_before"], pretty["metrics"]["input_tokens_before"])


if __name__ == "__main__":
    unittest.main()
