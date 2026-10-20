from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import prompt_rules  # noqa: E402
import sdd  # noqa: E402
import stage_context  # noqa: E402
import validate_protocol  # noqa: E402

SHA = "a" * 64
_GOVERNANCE_ISOLATION: list = []


def setUpModule() -> None:
    temp = tempfile.TemporaryDirectory(prefix="sdd-prompt-rules-jev-")
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


class FakeCtx:
    def __init__(self, repo: Path, *, ticket: str = "APP-1", profile: str = "CODE", delivery: dict | None = None,
                 state: dict | None = None, run_dir: Path | None = None) -> None:
        self.repo = repo
        self.ticket = ticket
        self.profile = profile
        self.delivery = delivery or {}
        self.state = state or {}
        self._run_dir = run_dir or (repo / ".hermes" / "runs" / ticket)

    def run_dir(self) -> Path:
        return self._run_dir


def implement_slice() -> dict:
    return {
        "current_slice_ids": ["S1"],
        "completed_slice_ids": [],
        "editable_paths": ["src/feature/*", "tests/feature/*"],
        "required_playbooks": [],
        "acceptance": {
            "AC-1": {"criterion": "totals round half-up", "verification_method": "focused test", "verifier": "AGENT", "slice_id": "S1"},
            "AC-2": {"criterion": "negative totals stay negative", "verification_method": "focused test", "verifier": "AGENT", "slice_id": "S2"},
        },
        "required_verification": [
            {"id": "V1", "kind": "TEST", "command": "pytest tests/feature -q", "check_ids": ["AC-1"], "introduced_by_slice": True},
            {"id": "V2", "kind": "STATIC_ANALYSIS", "command": "ruff check src/feature", "check_ids": ["AC-1"], "introduced_by_slice": False},
        ],
    }


def test_slice() -> dict:
    return {
        "current_slice_ids": [],
        "completed_slice_ids": ["S1"],
        "editable_paths": [],
        "required_playbooks": [],
        "acceptance": {
            "AC-1": {"criterion": "totals round half-up", "verification_method": "focused test", "verifier": "AGENT", "slice_id": "S1"},
            "AC-2": {"criterion": "negative totals stay negative", "verification_method": "focused test", "verifier": "AGENT", "slice_id": "S1"},
        },
        "required_verification": [
            {"id": "V1", "kind": "TEST", "command": "pytest tests/feature -q", "check_ids": ["AC-1"], "introduced_by_slice": False},
            {"id": "V2", "kind": "STATIC_ANALYSIS", "command": "ruff check src/feature", "check_ids": ["AC-1", "AC-2"], "introduced_by_slice": False},
        ],
    }


def manifest(repo: Path, stage: str, slice_: dict, *, max_prompt_bytes: int = 250000) -> dict:
    sources = [
        {"kind": "RULES", "path": "AGENTS.md", "lines": [1, 40], "sha256": SHA},
        {"kind": "SPEC", "path": "docs/spec.md", "lines": [10, 60], "sha256": SHA},
    ]
    for source in sources:
        path = repo / source["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        start, end = source["lines"]
        path.write_text("".join(f"line {number}\n" for number in range(start, end + 1)), encoding="utf-8")
    return {
        "schema_version": 2,
        "project_root": str(repo),
        "ticket": "APP-1",
        "stage": stage,
        "limits": {"max_sources": 12, "max_lines_per_source": 250, "max_prompt_bytes": max_prompt_bytes},
        "project_context": {"status": "CURRENT", "checked_head": "b" * 40, "obsidian": "BOUND", "evidence": "e", "gaps": []},
        "sources": sources,
        "playbooks": [],
        "divergences": [],
        "slice": slice_,
        "approval": None,
    }


def plan(stage: str, *, role: str | None = None, parent: dict | None = None, action_id: str | None = None) -> dict:
    key = role or stage.lower()
    return {
        "status": "PREPARE", "stage": stage, "role": role, "slice_id": "S1" if stage == "IMPLEMENT" else None,
        "key": key, "action_id": action_id or f"APP-1-{key}-01", "attempt": 1, "reduced": False,
        "parent": parent, "manifest": "manifest.json", "prompt": "prompt.md", "final": "result.json",
        "verifier": "verifier.json", "head": "b" * 40,
    }


def base_ctx(repo: Path) -> FakeCtx:
    return FakeCtx(repo, delivery={
        "profile": "CODE",
        "acceptance": {"AC-1": {"criterion": "c", "verification_method": "m", "verifier": "AGENT", "slice_id": "S1"}},
        "authorization": {"quote": "q"},
        "summaries": {"PLAN": {"summary": "s", "decisions": [], "tasks": []}},
    }, state={"ticket": {"id": "APP-1", "title": "t", "objective": "o"}, "baseline": {}, "gates": {}, "ownership": {}})


class AcceptanceRulesTests(unittest.TestCase):
    def test_implement_prompt_contains_bound_commands_slice_ids_and_required_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            value = manifest(repo, "IMPLEMENT", implement_slice())
            ctx = base_ctx(repo)
            text = prompt_rules.build_prompt(ctx, plan("IMPLEMENT"), value)
            self.assertIn("## Acceptance rules (the validator applies exactly these to your result)", text)
            self.assertIn("AC-1 → evidence must contain, in backticks, one of: `pytest tests/feature -q`, `ruff check src/feature`", text)
            self.assertIn("`tdd_slices` must contain exactly the ids [S1]", text)
            self.assertIn("Every `required_commands` entry must be recorded in `commands[]` as exit 0 / PASS: `pytest tests/feature -q`, `ruff check src/feature`", text)
            self.assertIn('Write only paths matching editable_paths: ["src/feature/*", "tests/feature/*"]', text)
            self.assertIn("AC-2 stays PLANNED with evidence null", text)
            self.assertIn('If `stage.status` is SUCCESS, `blockers` must be []', text)
            self.assertIn('"id": "S1"', text)

    def test_test_prompt_lists_all_acceptance_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            value = manifest(repo, "TEST", test_slice())
            ctx = base_ctx(repo)
            text = prompt_rules.build_prompt(ctx, plan("TEST"), value)
            self.assertIn("AC-1 → evidence must contain, in backticks, one of: `pytest tests/feature -q`, `ruff check src/feature`", text)
            self.assertIn("AC-2 → evidence must contain, in backticks, one of: `ruff check src/feature`", text)
            self.assertNotIn("stays PLANNED with evidence null", text)

    def test_plan_prompt_has_no_rules_block(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            value = manifest(repo, "PLAN", None)
            ctx = base_ctx(repo)
            text = prompt_rules.build_prompt(ctx, plan("PLAN"), value)
            self.assertNotIn("## Acceptance rules", text)

    def test_recorded_waiver_is_rendered_concretely(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            value = manifest(repo, "IMPLEMENT", implement_slice())
            ctx = base_ctx(repo)
            ctx.delivery["acceptance"]["AC-2"] = {"criterion": "c2", "verification_method": "m", "verifier": "AGENT", "slice_id": "S2"}
            ctx.delivery["waivers"] = {"AC-2": {"by": "requester", "reason": "r", "quote": "ok", "recorded_at": "2026-01-01T00:00:00Z"}}
            text = prompt_rules.build_prompt(ctx, plan("IMPLEMENT"), value)
            self.assertIn("AC-2 is WAIVED with exactly this waiver object", text)
            self.assertIn('"by": "requester"', text)

    def test_rules_come_from_verifier_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            value = manifest(repo, "IMPLEMENT", implement_slice())
            value["slice"]["required_verification"][0]["command"] = "pytest tests/feature --changed -q"
            ctx = base_ctx(repo)
            text = prompt_rules.build_prompt(ctx, plan("IMPLEMENT"), value)
            self.assertIn("`pytest tests/feature --changed -q`", text)
            self.assertNotIn("`pytest tests/feature -q`", text)


class OneSliceTests(unittest.TestCase):
    def test_implement_prompt_starts_with_slice_line(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            value = manifest(repo, "IMPLEMENT", implement_slice())
            ctx = base_ctx(repo)
            text = prompt_rules.build_prompt(ctx, plan("IMPLEMENT"), value)
            lines = text.splitlines()
            self.assertEqual("# SDD worker: IMPLEMENT for ticket APP-1", lines[0])
            self.assertIn("Only slice S1", lines[2])

    def test_test_prompt_has_no_slice_line(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            value = manifest(repo, "TEST", test_slice())
            ctx = base_ctx(repo)
            text = prompt_rules.build_prompt(ctx, plan("TEST"), value)
            self.assertNotIn("Only slice S1", text.splitlines()[1] if len(text.splitlines()) > 1 else "")


class RetryPromptRenderTests(unittest.TestCase):
    def test_retry_prompt_renders_fix_section_at_the_top(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            value = manifest(repo, "IMPLEMENT", implement_slice())
            ctx = base_ctx(repo)
            errors_file = repo / "parent.errors.json"
            errors_file.write_text(json.dumps([
                {"path": "$.executor_result.blockers", "reason": "SUCCESS cannot include blockers"},
            ]), encoding="utf-8")
            parent = {"action_id": "APP-1-implement-s1-01", "exit_code": 1, "status": "BLOCKED",
                      "artifact_path": None, "artifact_sha256": "", "errors_file": str(errors_file)}
            text = prompt_rules.build_prompt(ctx, plan("IMPLEMENT", parent=parent), value)
            self.assertIn("## Fix these first (previous attempt APP-1-implement-s1-01 was rejected)", text)
            self.assertLess(text.index("## Fix these first"), text.index("## Controller data"))

    def test_first_attempt_has_no_fix_section(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            value = manifest(repo, "IMPLEMENT", implement_slice())
            ctx = base_ctx(repo)
            text = prompt_rules.build_prompt(ctx, plan("IMPLEMENT"), value)
            self.assertNotIn("## Fix these first", text)


class SelfCheckTests(unittest.TestCase):
    def test_selfcheck_context_matches_verifier_context_and_uses_only_context_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            value = manifest(repo, "IMPLEMENT", implement_slice())
            ctx = base_ctx(repo)
            expected = sdd.verifier_context(ctx, value, None)
            actual = prompt_rules.selfcheck_context(ctx, value, None)
            self.assertEqual(expected, actual)
            self.assertTrue(set(actual) <= set(validate_protocol._CONTEXT_TYPES))

    def test_selfcheck_context_has_no_host_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            value = manifest(repo, "IMPLEMENT", implement_slice())
            ctx = base_ctx(repo)
            text = json.dumps(prompt_rules.selfcheck_context(ctx, value, None), sort_keys=True)
            self.assertNotIn(str(repo), text)
            self.assertNotIn(os.environ.get("HOME", "/nonexistent-home"), text)
            self.assertNotIn("vault", text.lower())

    def test_prompt_contains_the_exact_selfcheck_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            value = manifest(repo, "IMPLEMENT", implement_slice())
            ctx = base_ctx(repo)
            action_plan = plan("IMPLEMENT")
            text = prompt_rules.build_prompt(ctx, action_plan, value)
            self.assertIn("## Self-check before you answer", text)
            context_path = ctx.run_dir() / f"{action_plan['action_id']}.selfcheck-context.json"
            result_file = f"$TMPDIR/sdd-selfcheck-{action_plan['action_id']}.json"
            validate_path = RUNTIME / "validate_protocol.py"
            expected = (f"`{sys.executable} {validate_path} --action IMPLEMENT --result {result_file} "
                        f"--context {context_path} --json`")
            self.assertIn(expected, text)
            self.assertIn('prints `"valid": true`', text)

    def test_read_only_plan_prompt_has_no_selfcheck(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            value = manifest(repo, "PLAN", None)
            ctx = base_ctx(repo)
            text = prompt_rules.build_prompt(ctx, plan("PLAN"), value)
            self.assertNotIn("## Self-check before you answer", text)


class PromptProfilePolicyTests(unittest.TestCase):
    def test_prompt_profile_is_valid_for_both_executors(self) -> None:
        import executor_launch

        with tempfile.TemporaryDirectory() as temp:
            policy_file = Path(temp) / "EXECUTORS.md"
            policy_file.write_text("```json\n{\"executors_version\": 1, \"stages\": {\"IMPLEMENT\": {\"executor\": \"codex\", \"prompt_profile\": \"compact\"}, \"PLAN\": {\"executor\": \"claude\", \"prompt_profile\": \"standard\"}}}\n```\n", encoding="utf-8")
            policy = executor_launch.load_policy(policy_file)
            self.assertEqual("compact", policy["stages"]["IMPLEMENT"]["prompt_profile"])
            self.assertEqual("standard", policy["stages"]["PLAN"]["prompt_profile"])

    def test_invalid_prompt_profile_is_policy_invalid(self) -> None:
        import executor_launch

        with tempfile.TemporaryDirectory() as temp:
            policy_file = Path(temp) / "EXECUTORS.md"
            policy_file.write_text("```json\n{\"executors_version\": 1, \"stages\": {\"IMPLEMENT\": {\"executor\": \"codex\", \"prompt_profile\": \"small\"}}}\n```\n", encoding="utf-8")
            with self.assertRaises(executor_launch.LaunchError) as caught:
                executor_launch.load_policy(policy_file)
            self.assertEqual("POLICY_INVALID", caught.exception.status)


class CompactProfileTests(unittest.TestCase):
    def make_sources(self, repo: Path, count: int = 3) -> list[dict]:
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

    def test_compact_sources_use_windows_with_real_line_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            value = manifest(repo, "IMPLEMENT", implement_slice(), max_prompt_bytes=400000)
            value["sources"] = self.make_sources(repo)
            value["limits"]["max_lines_per_source"] = 1000
            ctx = base_ctx(repo)
            context = sdd.verifier_context(ctx, value, None)
            tokens = prompt_rules.compact_match_tokens(context, ctx.ticket)
            self.assertIn("AC-1", tokens)
            excerpts = prompt_rules.compact_source_excerpts(repo, value["sources"], tokens)
            self.assertIn("### docs/module0.md lines 85-115", excerpts[0])
            self.assertIn("line 0100 AC-1", excerpts[0])

    def test_compact_prompt_uses_brief_compact_controller_data_and_rejections(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            value = manifest(repo, "IMPLEMENT", implement_slice(), max_prompt_bytes=400000)
            value["sources"] = self.make_sources(repo)
            value["limits"]["max_lines_per_source"] = 1000
            ctx = base_ctx(repo)
            with mock.patch.object(prompt_rules, "prompt_profile", return_value="compact"):
                text = prompt_rules.build_prompt(ctx, plan("IMPLEMENT"), value)
            self.assertIn("## Most common rejections (re-check now)", text)
            self.assertIn('{"ticket":', text)
            self.assertNotIn('\n "ticket":', text)
            self.assertIn("## Acceptance rules", text)
            self.assertLess(text.index("## Acceptance rules"), text.index("## Controller data"))

    def test_compact_prompt_is_under_25kb_for_40kb_plus_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            value = manifest(repo, "IMPLEMENT", implement_slice(), max_prompt_bytes=400000)
            value["sources"] = self.make_sources(repo)
            value["limits"]["max_lines_per_source"] = 1000
            ctx = base_ctx(repo)
            standard = prompt_rules.build_prompt(ctx, plan("IMPLEMENT"), value)
            self.assertGreaterEqual(len(standard.encode("utf-8")), 40 * 1024)
            with mock.patch.object(prompt_rules, "prompt_profile", return_value="compact"):
                compact = prompt_rules.build_prompt(ctx, plan("IMPLEMENT"), value)
            self.assertLess(len(compact.encode("utf-8")), 25 * 1024)


class ScriptModuleIdentityTests(unittest.TestCase):
    def test_prompt_rules_uses_the_running_script_module(self) -> None:
        """Run as a script, sdd.py must be the module prompt_rules imports, or its SddError escapes `except`."""
        import subprocess

        probe = (
            "import sys, types\n"
            f"sys.path.insert(0, {str(RUNTIME)!r})\n"
            f"path = {str(RUNTIME / 'sdd.py')!r}\n"
            "module = types.ModuleType('__main__'); module.__file__ = path; sys.modules['__main__'] = module\n"
            "sys.argv = ['sdd.py', '--help']\n"
            "try:\n    exec(compile(open(path, encoding='utf-8').read(), path, 'exec'), module.__dict__)\n"
            "except SystemExit:\n    pass\n"
            "import sdd\nprint('SAME' if sdd.SddError is module.SddError else 'DIFFERENT')\n"
        )
        completed = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(completed.stdout.strip().splitlines()[-1], "SAME", completed.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
