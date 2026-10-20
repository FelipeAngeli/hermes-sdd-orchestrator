"""Unit and regression tests for the context-engine modules.

Each ``test_e*``/``test_review_*`` name maps to a defect reproduced on the host
(``E1``-``E7``) or to a pr-reviewer / security-reviewer finding.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))

import context_audit  # noqa: E402
import context_judge  # noqa: E402
import context_policy  # noqa: E402
from context_cache import ContextCache  # noqa: E402
from context_collector import collect  # noqa: E402
from context_config import DEFAULT_CONFIG_PATH, load_config, stage_rules  # noqa: E402
from context_rebuilder import check_integrity, rebuild  # noqa: E402
from context_segmenter import Segment, segment, split_text  # noqa: E402

SEGMENTING = {"segment_target_chars": 1200, "max_segment_chars": 2400, "recent_turns_protected": 0, "stages": {"TEST": {"eligible_kinds": ["TOOL_RESULT"]}}}


def anthropic(result_content):
    return {"messages": [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "read", "input": {"p": "a"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": result_content}]},
    ]}


def first_segment(native, fmt="anthropic", config=SEGMENTING):
    request = collect(json.dumps(native).encode(), fmt)
    return request, segment(request, config, "TEST")[0]


class PolicyRegressionTests(unittest.TestCase):
    def test_e1_acceptance_criterion_and_must_are_protected(self):
        for text, rule in (
            ("AC-3 the endpoint returns 201", "acceptance-criteria"),
            ("The endpoint MUST return 201", "requirement"),
            ("Ran 12 tests in 0.4s OK", "validation-evidence"),
            ("ADR-0007 keeps the outbox", "architecture-decision"),
            ("blocked by the fleet migration", "blocker"),
        ):
            with self.subTest(text=text):
                request, item = first_segment(anthropic(text))
                self.assertEqual(rule, context_policy.protection(item, request, SEGMENTING))

    def test_review_case_sensitive_tokens_do_not_fire_on_prose(self):
        request, item = first_segment(anthropic("The red barn address must be okay, said the reader."))
        self.assertIsNone(context_policy.protection(item, request, SEGMENTING))

    def test_e2_requirement_markers_include_ac_ids(self):
        request, _ = first_segment(anthropic("AC-3 and AC-12 must hold.\nThe service MUST reply."))
        markers = context_policy.requirement_markers(request)
        self.assertIn("AC-3", markers)
        self.assertIn("AC-12", markers)
        self.assertIn("The service MUST reply.", markers)

    def test_review_structural_rules(self):
        native = anthropic("plain old output")
        native["messages"][1]["content"][0]["is_error"] = True
        request, item = first_segment(native)
        self.assertEqual("unresolved-error", context_policy.protection(item, request, SEGMENTING))
        config = dict(SEGMENTING, recent_turns_protected=1)
        request, item = first_segment(anthropic("plain old output"))
        self.assertEqual("recent-turn", context_policy.protection(item, request, config))
        orphan = {"messages": [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "gone", "content": "plain"}]}]}
        request, item = first_segment(orphan)
        self.assertEqual("orphan-tool-result", context_policy.protection(item, request, SEGMENTING))

    def test_review_user_text_and_roleless_openai_items_are_protected(self):
        config = dict(SEGMENTING, stages={"TEST": {"eligible_kinds": ["ASSISTANT_TEXT"]}})
        request, item = first_segment({"messages": [{"role": "user", "content": "plain words"}]}, config=config)
        self.assertEqual("user-instruction", context_policy.protection(item, request, config))
        request, item = first_segment({"input": [{"type": "message", "content": "plain words"}]}, "openai", config)
        self.assertEqual("user-instruction", context_policy.protection(item, request, config))

    def test_review_missing_redaction_protects_everything(self):
        request, item = first_segment(anthropic("plain old output"))
        with mock.patch.object(context_policy, "redact", None):
            self.assertEqual("redaction-unavailable", context_policy.protection(item, request, SEGMENTING))

    def test_decide_requires_two_valid_probabilities(self):
        thresholds = {"fact_loss_max": 0.2, "constraint_loss_max": 0.2}
        self.assertEqual("OMIT", context_policy.decide(None, {"fact_loss": 0.1, "constraint_loss": 0.2}, thresholds).disposition)
        for answer in ({"fact_loss": 0.1}, {"fact_loss": True, "constraint_loss": 0.0}, {"fact_loss": float("nan"), "constraint_loss": 0}, None, {"fact_loss": 0.3, "constraint_loss": 0}):
            with self.subTest(answer=answer):
                self.assertEqual("PRESERVE", context_policy.decide(None, answer, thresholds).disposition)


class RebuildRegressionTests(unittest.TestCase):
    def test_e3_openai_output_rewritten_in_place(self):
        native = {"input": [
            {"type": "function_call", "call_id": "c1", "name": "read", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "c1", "output": "old output"},
        ]}
        request, item = first_segment(native, "openai")
        rebuilt, errors = rebuild(request, [item], "65f0a1b2_abcdefghijklmnop")
        self.assertEqual([], errors)
        self.assertEqual({"type", "call_id", "output"}, set(rebuilt.native["input"][1]))
        self.assertIn("context-engine omitted segment", rebuilt.native["input"][1]["output"])
        self.assertEqual(native, request.native, "the original must not be mutated")

    def test_review_list_content_keeps_non_text_items(self):
        image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}
        request, item = first_segment(anthropic([{"type": "text", "text": "old output", "cache_control": {"type": "ephemeral"}}, image]))
        rebuilt, errors = rebuild(request, [item], "65f0a1b2_abcdefghijklmnop")
        self.assertEqual([], errors)
        content = rebuilt.native["messages"][1]["content"][0]["content"]
        self.assertEqual(image, content[1])
        self.assertEqual({"type": "ephemeral"}, content[0]["cache_control"])

    def test_review_multiple_omissions_in_one_block(self):
        text = "".join(f"paragraph {index} " + "word " * 300 + "\n\n" for index in range(4))
        request = collect(json.dumps(anthropic(text)).encode(), "anthropic")
        items = segment(request, SEGMENTING, "TEST")
        self.assertGreater(len(items), 1)
        rebuilt, errors = rebuild(request, items, "65f0a1b2_abcdefghijklmnop")
        self.assertEqual([], errors)
        self.assertEqual(len(items), rebuilt.native["messages"][1]["content"][0]["content"].count("context-engine omitted segment"))

    def test_review_integrity_rejects_changed_ids_and_stale_segments(self):
        request, item = first_segment(anthropic("old output"))
        changed = json.loads(json.dumps(request.native))
        changed["messages"][0]["content"][0]["id"] = "other"
        self.assertTrue(check_integrity(request, collect(json.dumps(changed).encode(), "anthropic"), (), {item.path}))
        stale = Segment(item.id, item.path, item.start, item.end, item.sha256, item.kind, False, "different")
        self.assertEqual((request, ["REBUILD_INTEGRITY_FAILED"]), rebuild(request, [stale], "65f0a1b2_abcdefghijklmnop"))

    def test_review_tool_use_input_is_never_rewritten(self):
        request = collect(json.dumps(anthropic("x")).encode(), "anthropic")
        fake = Segment("0.0@0:2", (0, 0), 0, 2, "s", "TOOL_RESULT", False, "{}")
        self.assertEqual(["REBUILD_INTEGRITY_FAILED"], rebuild(request, [fake], "65f0a1b2_abcdefghijklmnop")[1])


class SegmenterTests(unittest.TestCase):
    def test_code_stays_whole_and_prose_splits_on_lines(self):
        code = "```\n" + "\n".join(f"    line_{index} = {index}" for index in range(400)) + "\n```"
        self.assertEqual([(0, len(code))], split_text(code, 100, 200))
        prose = "\n".join("sentence number %d here" % index for index in range(200))
        spans = split_text(prose, 100, 200)
        self.assertEqual(prose, "".join(prose[start:end] for start, end in spans))
        self.assertTrue(all(prose[end - 1] == "\n" for _, end in spans[:-1]))

    def test_review_sdd_heading_is_never_part_of_a_span(self):
        request = collect(b"# Objective\nkeep\n## Notes\nold words\n", "sdd-prompt")
        items = segment(request, dict(SEGMENTING, stages={"TEST": {"eligible_kinds": ["SDD_SECTION"]}}), "TEST")
        self.assertTrue(items)
        self.assertTrue(all(not item.text.startswith("#") for item in items))


class JudgeTests(unittest.TestCase):
    def config(self, **overrides):
        config, _ = load_config(DEFAULT_CONFIG_PATH)
        config.update(evaluator="typesafe")
        config.update(overrides)
        return config

    def item(self, text="plain old output", code=False):
        return Segment("1.0@0:5", (1, 0), 0, 5, "h", "TOOL_RESULT", code, text)

    def test_evaluator_none_makes_no_call(self):
        calls = []
        report = context_judge.JevJudge(self.config(evaluator="none"), calls.append).judge([self.item()])
        self.assertEqual([], calls)
        self.assertEqual({"1.0@0:5": "EVALUATOR_NONE"}, report.errors)

    def test_review_payload_shape_matches_the_live_connector_contract(self):
        payload = context_judge.build_payload("task", "constraints", "text", 24000)
        self.assertEqual({"model", "state", "questions"}, set(payload))
        self.assertEqual(set(context_judge.QUESTION_IDS), set(payload["questions"]))
        for question in payload["questions"].values():
            self.assertEqual("noul", question["type"])
            self.assertEqual({"true", "false"}, set(question["criteria"]))

    def test_review_code_and_secrets_are_screened(self):
        sent = []
        report = context_judge.JevJudge(self.config(), sent.append).judge([self.item(code=True)])
        self.assertEqual("CODE_PROTECTED", report.errors["1.0@0:5"])
        with mock.patch.object(context_judge, "redact", lambda text: text.replace("plain", "[R]")):
            report = context_judge.JevJudge(self.config(), sent.append).judge([self.item()])
        self.assertEqual("SECRET_PRESENT", report.errors["1.0@0:5"])
        with mock.patch.object(context_judge, "redact", None):
            report = context_judge.JevJudge(self.config(), sent.append).judge([self.item()])
        self.assertEqual("REDACTION_UNAVAILABLE", report.errors["1.0@0:5"])
        self.assertEqual([], sent)

    def test_review_oversized_segment_is_not_sent(self):
        sent = []
        report = context_judge.JevJudge(self.config(max_payload_bytes=1024), sent.append).judge([self.item("x" * 5000)])
        self.assertEqual("SEGMENT_TOO_LARGE", report.errors["1.0@0:5"])
        self.assertEqual([], sent)

    def test_review_preflight_needs_explicit_consent_path(self):
        self.assertFalse(context_judge.preflight("typesafe", ".env", None))


class CacheAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_cache_ttl_and_malformed_entries(self):
        now = [1000.0]
        cache = ContextCache(self.root / "cache.json", 60, 10, lambda: now[0])
        cache.put("k", "OMIT", {"fact_loss": 0.0, "constraint_loss": 0.0})
        cache.save()
        self.assertEqual(0o600, stat.S_IMODE((self.root / "cache.json").stat().st_mode))
        self.assertIsNotNone(ContextCache(self.root / "cache.json", 60, 10, lambda: now[0]).get("k"))
        now[0] += 61
        self.assertIsNone(ContextCache(self.root / "cache.json", 60, 10, lambda: now[0]).get("k"))
        (self.root / "cache.json").write_text(json.dumps({"entries": {"k": {"verdict": "OMIT"}, "j": []}}))
        self.assertEqual({}, ContextCache(self.root / "cache.json", 60, 10).entries)

    def test_review_unique_run_ids_and_private_logs(self):
        audit = context_audit.ContextAudit(self.root)
        first = audit.start(b"one")
        audit.record({"segment_id": "a"})
        second = context_audit.ContextAudit(self.root).start(b"one")
        self.assertNotEqual(first, second)
        log = self.root / "audit" / first / "decisions.jsonl"
        self.assertEqual(0o600, stat.S_IMODE(log.stat().st_mode))
        self.assertEqual(b"one", context_audit.recover(self.root, first))

    def test_review_recover_detects_tampering_and_purge_respects_retention(self):
        run = context_audit.ContextAudit(self.root).start(b"original")
        self.assertEqual(0, context_audit.purge(self.root, 1))
        (self.root / "audit" / run / "original.bin").write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            context_audit.recover(self.root, run)
        old = time.time() - 3 * 86400
        os.utime(self.root / "audit" / run, (old, old))
        self.assertEqual(1, context_audit.purge(self.root, 2))
        self.assertFalse((self.root / "audit" / run).exists())


class ConfigTests(unittest.TestCase):
    def test_review_invalid_values_disable_instead_of_raising(self):
        shipped = (DEFAULT_CONFIG_PATH).read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            for old, new in (('"max_concurrency": 4', '"max_concurrency": "4"'), ('"enabled": false', '"enabled": "yes"'), ('"fact_loss_max": 0.2', '"fact_loss_max": 2')):
                with self.subTest(change=new):
                    path = Path(directory) / "config.md"
                    path.write_text(shipped.replace(old, new), encoding="utf-8")
                    self.assertEqual((None, "CONFIG_INVALID"), load_config(path))

    def test_stage_thresholds_override_defaults(self):
        config, _ = load_config(DEFAULT_CONFIG_PATH)
        config["stages"]["TEST"]["thresholds"] = {"fact_loss_max": 0.5}
        self.assertEqual({"fact_loss_max": 0.5, "constraint_loss_max": 0.2}, stage_rules(config, "TEST")["thresholds"])


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


if __name__ == "__main__":
    unittest.main()
