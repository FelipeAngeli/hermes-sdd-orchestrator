from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from jsonschema import Draft202012Validator

import prompt_rules  # noqa: E402
import sdd  # noqa: E402
import stage_context  # noqa: E402
import validate_protocol  # noqa: E402
from test_prompt_rules import SHA, base_ctx, implement_slice, manifest, plan  # noqa: E402

SCHEMA = RUNTIME.parent / "schemas" / "EXECUTOR_RESULT_SCHEMA.json"
_GOVERNANCE_ISOLATION: list = []


def setUpModule() -> None:
    temp = tempfile.TemporaryDirectory(prefix="sdd-prompt-rules-review-jev-")
    root = Path(temp.name).resolve()
    setup = root / "PROJECT_SETUP.md"
    setup.write_text("```yaml\nschema_version: 1\nanswers:\n  typesafe_ai: none\n```\n", encoding="utf-8")
    patchers = [
        mock.patch.object(stage_context, "JEV_PROJECT_SETUP_PATH", setup),
        mock.patch.object(stage_context, "JEV_CACHE_PATH", root / "JEV_CACHE.json"),
    ]
    for patcher in patchers:
        patcher.start()
    _GOVERNANCE_ISOLATION.extend([temp, *patchers])


def tearDownModule() -> None:
    temp, *patchers = _GOVERNANCE_ISOLATION
    for patcher in reversed(patchers):
        patcher.stop()
    temp.cleanup()
    _GOVERNANCE_ISOLATION.clear()


def prose_sources(repo: Path, count: int = 3) -> list[dict]:
    sources = []
    for index in range(count):
        path = repo / "docs" / f"module{index}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for number in range(1, 1001):
            marker = "AC-1" if (number == 100 and index == 0) else ("APP-1" if number == 200 else "")
            lines.append(f"line {number:04d} {marker} padding text to widen the excerpt".rstrip())
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        sources.append({"kind": "SPEC", "path": f"docs/module{index}.md", "lines": [1, 1000], "sha256": SHA})
    return sources


def implement_result(checks: list[dict]) -> dict:
    return {
        "executor_result": {
            "schema_version": 3,
            "stage": {"value": "IMPLEMENT", "status": "SUCCESS"},
            "executor": {"name": "CODEX", "invocation_type": "EXTERNAL_CLI"},
            "consulted_paths": [],
            "modified_paths": [{"path": "src/feature/total.py"}],
            "created_paths": [],
            "validated_symbols": [],
            "commands": [
                {"command": "pytest tests/feature -q", "purpose": "test", "timeout_seconds": 60, "exit_code": 0, "result": "PASS"},
                {"command": "ruff check src/feature", "purpose": "lint", "timeout_seconds": 60, "exit_code": 0, "result": "PASS"},
            ],
            "blockers": [],
            "context_assessment": {"facts": [], "assumptions": [], "unresolved_questions": []},
            "acceptance_checks": checks,
            "stage_payload": {"summary": "", "tasks": [], "impact_files": [], "decisions": []},
            "tdd_slices": [{"id": "S1", "objective": "round", "test_file": "tests/feature/test_total.py",
                            "red_command": "pytest tests/feature -q", "red_exit_code": 1,
                            "expected_failure": "assert failure", "red_failure_kind": "EXPECTED_FUNCTIONAL",
                            "minimal_implementation": "round half-up", "green_command": "pytest tests/feature -q",
                            "green_exit_code": 0, "green_result": "1 passed"}],
            "next_step": {"stage": "TEST", "action": "run focused tests"},
        }
    }


def validate_implement(repo: Path, ctx, value: dict, checks: list[dict]) -> list[dict]:
    context = sdd.verifier_context(ctx, value, None)
    kwargs = {
        "expected_acceptance": context.get("expected_acceptance"),
        "current_slice_ids": set(context.get("current_slice_ids") or []),
        "completed_slice_ids": set(context.get("completed_slice_ids") or []),
        "editable_paths": set(context.get("editable_paths") or []),
        "required_commands": context.get("required_commands") or [],
        "check_verifiers": context.get("check_verifiers") or {},
        "recorded_waivers": context.get("recorded_waivers"),
    }
    return validate_protocol.validate_payload("IMPLEMENT", implement_result(checks), **kwargs)


class ExampleSchemaValidityTests(unittest.TestCase):
    def item_validator(self, pointer: str) -> Draft202012Validator:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        items = schema["properties"]["executor_result"]["properties"][pointer]["items"]
        return Draft202012Validator({**items, "$defs": schema.get("$defs", {})})

    def test_tdd_example_is_valid_against_schema(self) -> None:
        validator = self.item_validator("tdd_slices")
        instance = json.loads(prompt_rules.tdd_example(["S1"]))
        self.assertEqual([], list(validator.iter_errors(instance)))

    def test_acceptance_example_is_never_empty_and_valid_for_agent_human_planned(self) -> None:
        validator = self.item_validator("acceptance_checks")
        acceptance = {
            "AC-A": {"criterion": "c", "verification_method": "m", "verifier": "AGENT", "slice_id": "S1"},
            "AC-H": {"criterion": "c", "verification_method": "m", "verifier": "HUMAN", "slice_id": "S1"},
        }
        agent = json.loads(prompt_rules.acceptance_example(acceptance, {"AC-A", "AC-H"}, {"AC-A": ["pytest -q"]}))
        self.assertNotEqual({}, agent)
        self.assertEqual("PASS", agent["status"])
        self.assertEqual([], list(validator.iter_errors(agent)))
        human = json.loads(prompt_rules.acceptance_example(acceptance, {"AC-H"}, {}))
        self.assertNotEqual({}, human)
        self.assertEqual("PASS", human["status"])
        self.assertIn("recorded human decision", human["evidence"])
        self.assertEqual([], list(validator.iter_errors(human)))
        planned = json.loads(prompt_rules.acceptance_example(acceptance, set(), {}))
        self.assertNotEqual({}, planned)
        self.assertEqual("PLANNED", planned["status"])
        self.assertEqual([], list(validator.iter_errors(planned)))


class WaiverRuleTests(unittest.TestCase):
    def test_future_slice_waiver_keeps_only_planned_line(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            value = manifest(repo, "IMPLEMENT", implement_slice())
            ctx = base_ctx(repo)
            ctx.delivery["acceptance"]["AC-2"] = {"criterion": "negative totals stay negative",
                                                    "verification_method": "focused test",
                                                    "verifier": "AGENT", "slice_id": "S2"}
            ctx.delivery["waivers"] = {"AC-2": {"by": "requester", "reason": "r", "quote": "ok",
                                                 "recorded_at": "2026-01-01T00:00:00Z"}}
            text = prompt_rules.build_prompt(ctx, plan("IMPLEMENT"), value)
            self.assertNotIn("AC-2 is WAIVED", text)
            self.assertIn("AC-2 stays PLANNED with evidence null", text)
            checks = [
                {"id": "AC-1", "criterion": "totals round half-up", "verification_method": "focused test",
                 "verifier": "AGENT", "slice_id": "S1", "status": "PASS",
                 "evidence": "`pytest tests/feature -q` exited 0", "waiver": None},
                {"id": "AC-2", "criterion": "negative totals stay negative", "verification_method": "focused test",
                 "verifier": "AGENT", "slice_id": "S2", "status": "PLANNED", "evidence": None, "waiver": None},
            ]
            self.assertEqual([], validate_implement(repo, ctx, value, checks))


class HumanCheckRuleTests(unittest.TestCase):
    def test_human_check_on_current_slice_gets_human_verified_line(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            value = manifest(repo, "IMPLEMENT", implement_slice())
            value["slice"]["acceptance"]["AC-1"]["verifier"] = "HUMAN"
            value["slice"]["required_verification"].append(
                {"id": "VH", "kind": "HUMAN", "command": None, "check_ids": ["AC-1"], "introduced_by_slice": False},
            )
            ctx = base_ctx(repo)
            text = prompt_rules.build_prompt(ctx, plan("IMPLEMENT"), value)
            self.assertIn("- AC-1 is HUMAN-verified: report PASS with the recorded human decision as evidence, "
                          "or WAIVED with its recorded waiver object; never leave it PLANNED.", text)


class RequiredCommandsRuleTests(unittest.TestCase):
    def test_required_commands_rule_mentions_waived_exception(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            value = manifest(repo, "IMPLEMENT", implement_slice())
            ctx = base_ctx(repo)
            text = prompt_rules.build_prompt(ctx, plan("IMPLEMENT"), value)
            self.assertIn("Every `required_commands` entry must be recorded in `commands[]` as exit 0 / PASS: "
                          "`pytest tests/feature -q`, `ruff check src/feature`; unless every check bound to it is WAIVED.", text)


class CompactWindowTests(unittest.TestCase):
    def test_compact_without_excerpts_emits_headers_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            value = manifest(repo, "IMPLEMENT", implement_slice(), max_prompt_bytes=400000)
            value["sources"] = prose_sources(repo)
            value["limits"]["max_lines_per_source"] = 1000
            ctx = base_ctx(repo)
            context = sdd.verifier_context(ctx, value, None)
            tokens = prompt_rules.compact_match_tokens(context, ctx.ticket)
            excerpts = prompt_rules.compact_source_excerpts(repo, value["sources"], tokens, with_excerpts=False)
            self.assertTrue(excerpts)
            for part in excerpts:
                self.assertEqual(1, len(part.splitlines()), part)
                self.assertTrue(part.startswith("### "), part)

    def test_compact_declared_range_never_emits_lines_outside_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            path = repo / "docs" / "spec.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            lines = [f"line {number:04d} AC-1 padding text to widen the excerpt".rstrip() for number in range(1, 201)]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            sources = [{"kind": "SPEC", "path": "docs/spec.md", "lines": [100, 110], "sha256": SHA}]
            tokens = ["AC-1"]
            excerpts = prompt_rules.compact_source_excerpts(repo, sources, tokens)
            for part in excerpts:
                for line in part.splitlines()[1:]:
                    number = int(line.split(":", 1)[0])
                    self.assertGreaterEqual(number, 100)
                    self.assertLessEqual(number, 110)

    def test_compact_bytes_less_than_standard_for_reviewer_case(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            value = manifest(repo, "IMPLEMENT", implement_slice(), max_prompt_bytes=400000)
            value["sources"] = prose_sources(repo)
            value["limits"]["max_lines_per_source"] = 1000
            ctx = base_ctx(repo)
            standard = prompt_rules.build_prompt(ctx, plan("IMPLEMENT"), value)
            with mock.patch.object(prompt_rules, "prompt_profile", return_value="compact"):
                compact = prompt_rules.build_prompt(ctx, plan("IMPLEMENT"), value)
            self.assertLess(len(compact.encode("utf-8")), len(standard.encode("utf-8")))

    def test_reduced_compact_does_not_raise_budget_exceeded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            value = manifest(repo, "IMPLEMENT", implement_slice(), max_prompt_bytes=400000)
            value["sources"] = prose_sources(repo)
            value["limits"]["max_lines_per_source"] = 1000
            ctx = base_ctx(repo)
            reduced = plan("IMPLEMENT")
            reduced["reduced"] = True
            standard = prompt_rules.build_prompt(ctx, reduced, value)
            standard_size = len(standard.encode("utf-8"))
            value["limits"]["max_prompt_bytes"] = 2 * (standard_size + 2048)
            with mock.patch.object(prompt_rules, "prompt_profile", return_value="compact"):
                compact = prompt_rules.build_prompt(ctx, reduced, value)
            self.assertLessEqual(len(compact.encode("utf-8")), standard_size + 2048)


class SelfCheckFallbackTests(unittest.TestCase):
    def test_selfcheck_mentions_tmpdir_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            value = manifest(repo, "IMPLEMENT", implement_slice())
            ctx = base_ctx(repo)
            action_plan = plan("IMPLEMENT")
            text = prompt_rules.build_prompt(ctx, action_plan, value)
            self.assertIn(f"If `$TMPDIR` is unset or not writable, use `/tmp/sdd-selfcheck-{action_plan['action_id']}.json` instead.", text)


if __name__ == "__main__":
    unittest.main()
