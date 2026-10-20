"""Per-AC acceptance verifiers: allowlist, binding, doc-slice RED hints."""
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

import sdd  # noqa: E402
import validate_protocol  # noqa: E402
from test_protocol import successful_implementation  # noqa: E402

import stage_context  # noqa: E402

_ISOLATION: list = []


def setUpModule() -> None:
    temp = tempfile.TemporaryDirectory(prefix="sdd-acceptance-verifiers-jev-")
    root = Path(temp.name).resolve()
    setup = root / "PROJECT_SETUP.md"
    setup.write_text("```yaml\nschema_version: 1\nanswers:\n  typesafe_ai: none\n```\n", encoding="utf-8")
    patchers = [
        mock.patch.object(stage_context, "JEV_PROJECT_SETUP_PATH", setup),
        mock.patch.object(stage_context, "JEV_CACHE_PATH", root / "JEV_CACHE.json"),
    ]
    for patcher in patchers:
        patcher.start()
    _ISOLATION.extend([temp, *patchers])


def tearDownModule() -> None:
    temp, *patchers = _ISOLATION
    for patcher in reversed(patchers):
        patcher.stop()
    temp.cleanup()
    _ISOLATION.clear()


def bound(acceptance: dict) -> dict[str, list[str]]:
    return {check_id: [command] for check_id, command in acceptance.items()}


class AllowlistTests(unittest.TestCase):
    def test_accepts_bound_grep_family_commands(self) -> None:
        import acceptance_verifiers as av

        for command in (
            "rg -n \"Decision\" docs/adr/0001.md",
            "grep -F -n -i \"x\" docs/a.md",
            "rg -q -e \"x\" docs/a.md docs/b.md",
        ):
            with self.subTest(command=command):
                self.assertTrue(av.allowed_check_command(command))

    def test_accepts_test_python_ruby_and_node_shapes(self) -> None:
        import acceptance_verifiers as av

        for command in (
            "test -f docs/a.md",
            "test -s docs/a.md",
            "test -d docs/a.md",
            "python3 -c \"import yaml,sys; yaml.safe_load(open('docs/a.yaml'))\"",
            "ruby -ryaml -e \"YAML.load_file('docs/a.yml')\"",
            "node -e \"JSON.parse(require('fs').readFileSync('docs/a.json','utf8'))\"",
        ):
            with self.subTest(command=command):
                self.assertTrue(av.allowed_check_command(command))

    def test_project_runner_scripts_need_an_exact_gate_command(self) -> None:
        """The controller is toolchain-neutral: a project runner is admitted only as a configured gate."""
        import acceptance_verifiers as av

        for command in ("make check", "make lint", "task test"):
            with self.subTest(command=command):
                self.assertFalse(av.allowed_check_command(command))
                self.assertTrue(av.allowed_check_command(command, gate_commands={command}))

    def test_package_runners_are_not_built_in(self) -> None:
        """No hard-coded package-runner shape: only an exact gate command admits one."""
        import acceptance_verifiers as av

        for command in ("bun run test", "deno task test", "just test"):
            with self.subTest(command=command):
                self.assertFalse(av.allowed_check_command(command))
                self.assertTrue(av.allowed_check_command(command, gate_commands={command}))

    def test_a_gate_command_is_matched_exactly(self) -> None:
        import acceptance_verifiers as av

        self.assertFalse(av.allowed_check_command("make check extra", gate_commands={"make check"}))
        self.assertFalse(av.allowed_check_command("make check; x", gate_commands={"make check"}))

    def test_refuses_worker_chosen_commands(self) -> None:
        import acceptance_verifiers as av

        for command in (
            "true",
            "echo PASS",
            "rg -n \"x\" docs/a.md | true",
            "rg -n \"x\" /abs/path.md",
            "rg -n \"x\" ../outside.md",
            "sh -c \"rg -n x docs/a.md\"",
            "cat /etc/passwd",
            "make build",
            "python3 -c \"import os; os.system('id')\"",
            "rg --regexp \"x\" docs/a.md",
            "rg -n \"x\" docs/*.md",
            "rg -n \"x\"",
            "rg --pre=sh x a.md",
            "rg --pre sh",
            "grep -f /etc/passwd",
            "rg -e x --files",
            "make test -- --watch",
            "make test; x",
            "rg --pre=sh x a.md",
        ):
            with self.subTest(command=command):
                self.assertFalse(av.allowed_check_command(command))

    def test_refuses_shell_characters_and_parent_paths_in_pinned_shapes(self) -> None:
        import acceptance_verifiers as av

        shapes = (
            ("python3 -c \"import yaml,sys; yaml.safe_load(open('{}'))\"", "docs/a.yaml"),
            ("ruby -ryaml -e \"YAML.load_file('{}')\"", "docs/a.yml"),
            ("node -e \"JSON.parse(require('fs').readFileSync('{}','utf8'))\"", "docs/a.json"),
        )
        for template, good_path in shapes:
            for unsafe_path in ("$(touch /tmp/pwned)", "`id`", 'bad"path', "bad\npath", "../outside.yml"):
                with self.subTest(template=template, unsafe_path=unsafe_path):
                    self.assertFalse(av.allowed_check_command(template.format(unsafe_path)))
            with self.subTest(template=template, good_path=good_path):
                self.assertTrue(av.allowed_check_command(template.format(good_path)))


class ExtractCommandTests(unittest.TestCase):
    def test_extracts_exactly_one_backtick_command(self) -> None:
        import acceptance_verifiers as av

        self.assertEqual("rg -n 'x' docs/a.md", av.extract_check_command("run `rg -n 'x' docs/a.md` to verify"))
        self.assertEqual("true", av.extract_check_command("`true`"))
        self.assertIsNone(av.extract_check_command("no command here"))
        self.assertIsNone(av.extract_check_command("two `a` and `b`"))


class BindingTests(unittest.TestCase):
    def test_binds_allowlisted_commands_and_records_refusals(self) -> None:
        import acceptance_verifiers as av

        acceptance = {
            "AC-01": {"verification_method": "run `rg -n \"Decision\" docs/adr/0001.md` to check", "verifier": "AGENT"},
            "AC-02": {"verification_method": "run `true`", "verifier": "AGENT"},
            "AC-03": {"verification_method": "run `rg -n \"x\" docs/a.md | true`", "verifier": "AGENT"},
            "AC-04": {"verification_method": "run `rg -n \"x\" /abs/path.md`", "verifier": "AGENT"},
        }
        check_commands, refused = av.bind_check_commands(acceptance)
        self.assertEqual({"AC-01": "rg -n \"Decision\" docs/adr/0001.md"}, check_commands)
        self.assertEqual({"AC-02", "AC-03", "AC-04"}, {item["id"] for item in refused})
        self.assertEqual(["true", "rg -n \"x\" docs/a.md | true", "rg -n \"x\" /abs/path.md"],
                         [item["command"] for item in refused])


class SliceBlockBindingTests(unittest.TestCase):
    def test_slice_block_adds_one_verification_item_per_bound_ac(self) -> None:
        data = {"delivery": {
            "profile": "CODE",
            "acceptance": {"AC-01": {"criterion": "c", "verification_method": "m", "verifier": "AGENT", "slice_id": "S1"}},
            "slices": {"planned": ["S1"], "completed": [], "editable_paths": ["src/*"]},
            "check_commands": {"AC-01": "rg -n 'x' docs/a.md"},
        }}
        with mock.patch.object(sdd, "gate_table", return_value={"focused_tests": {"command": "make test"}}):
            block = sdd.slice_block(None, data, "IMPLEMENT", "S1")
        per_ac = [item for item in block["required_verification"] if item["id"] == "check-ac-01"]
        self.assertEqual(1, len(per_ac))
        self.assertEqual("rg -n 'x' docs/a.md", per_ac[0]["command"])
        self.assertEqual(["AC-01"], per_ac[0]["check_ids"])
        self.assertFalse(per_ac[0]["introduced_by_slice"])


class PerAcValidationTests(unittest.TestCase):
    def test_implement_result_citing_the_bound_command_validates(self) -> None:
        fixture = successful_implementation()
        result = fixture["executor_result"]
        result["commands"] = [{"command": "rg -n \"Decision\" docs/adr/0001.md", "purpose": "doc check", "timeout_seconds": 300, "exit_code": 0, "result": "PASS"}]
        result["acceptance_checks"][0]["evidence"] = "`rg -n \"Decision\" docs/adr/0001.md` exited 0"
        expected = {"AC-1": {"criterion": result["acceptance_checks"][0]["criterion"], "verification_method": result["acceptance_checks"][0]["verification_method"], "verifier": "AGENT", "slice_id": "synthetic-slice-1"}}
        errors = validate_protocol.validate_payload(
            "IMPLEMENT", fixture,
            expected_acceptance=expected,
            current_slice_ids={"synthetic-slice-1"}, completed_slice_ids=set(),
            editable_paths={"src/*", "tests/*"},
            required_commands=["rg -n \"Decision\" docs/adr/0001.md"],
            check_verifiers={"AC-1": ["rg -n \"Decision\" docs/adr/0001.md"]},
        )
        self.assertEqual([], errors, msg=errors)

    def test_implement_result_citing_true_fails(self) -> None:
        fixture = successful_implementation()
        result = fixture["executor_result"]
        result["commands"] = [{"command": "true", "purpose": "fake", "timeout_seconds": 300, "exit_code": 0, "result": "PASS"}]
        result["acceptance_checks"][0]["evidence"] = "`true` exited 0"
        expected = {"AC-1": {"criterion": result["acceptance_checks"][0]["criterion"], "verification_method": result["acceptance_checks"][0]["verification_method"], "verifier": "AGENT", "slice_id": "synthetic-slice-1"}}
        errors = validate_protocol.validate_payload(
            "IMPLEMENT", fixture,
            expected_acceptance=expected,
            current_slice_ids={"synthetic-slice-1"}, completed_slice_ids=set(),
            editable_paths={"src/*", "tests/*"},
            required_commands=["rg -n \"Decision\" docs/adr/0001.md"],
            check_verifiers={"AC-1": ["rg -n \"Decision\" docs/adr/0001.md"]},
        )
        self.assertTrue(errors, msg="citing true must be refused")


class DocRedTests(unittest.TestCase):
    def test_doc_red_command_pipes_head_into_the_grep_family(self) -> None:
        import acceptance_verifiers as av

        self.assertEqual("git show HEAD:docs/adr/0001.md | rg -n Decision",
                         av.doc_red_command('rg -n "Decision" docs/adr/0001.md'))
        self.assertEqual("git show HEAD:docs/a.md | grep -n Decision",
                         av.doc_red_command('grep -n "Decision" docs/a.md'))
        self.assertIsNone(av.doc_red_command("test -f docs/a.md"))
        self.assertIsNone(av.doc_red_command("make test"))
        self.assertIsNone(av.doc_red_command('rg -n "Decision"'))

    def test_doc_red_commands_only_for_doc_slices_or_decision_doc(self) -> None:
        import acceptance_verifiers as av

        check_commands = {"AC-01": 'rg -n "Decision" docs/adr/0001.md'}
        self.assertEqual({"AC-01": "git show HEAD:docs/adr/0001.md | rg -n Decision"},
                         av.doc_red_commands(check_commands, ["docs/adr/0001.md"], "CODE"))
        self.assertEqual({"AC-01": "git show HEAD:docs/adr/0001.md | rg -n Decision"},
                         av.doc_red_commands(check_commands, ["docs/a.md"], "DECISION_DOC"))
        self.assertEqual({}, av.doc_red_commands(check_commands, ["src/*.py"], "CODE"))

    def test_is_doc_only_slice(self) -> None:
        import acceptance_verifiers as av

        self.assertTrue(av.is_doc_only_slice(["docs/*.md"]))
        self.assertTrue(av.is_doc_only_slice(["*.md"]))
        self.assertFalse(av.is_doc_only_slice(["docs/**"]))
        self.assertFalse(av.is_doc_only_slice(["docs/*.md", "src/*.py"]))
        self.assertFalse(av.is_doc_only_slice([]))


class DocRedValidationTests(unittest.TestCase):
    def _fixture(self, red_kind: str = "EXPECTED_FUNCTIONAL") -> dict:
        from test_protocol import executor_fixture

        fixture = executor_fixture("IMPLEMENT")
        result = fixture["executor_result"]
        result["commands"] = [{"command": "rg -n Decision docs/a.md", "purpose": "doc check", "timeout_seconds": 300, "exit_code": 0, "result": "PASS"}]
        result["acceptance_checks"][0].update({"slice_id": "DOC", "evidence": "`rg -n Decision docs/a.md` exited 0"})
        result["tdd_slices"] = [{
            "id": "DOC", "objective": "record the decision", "test_file": "docs/a.md",
            "red_command": "git show HEAD:docs/a.md | rg -n Decision", "red_exit_code": 1,
            "expected_failure": "the decision is absent at HEAD", "red_failure_kind": red_kind,
            "minimal_implementation": "write the decision", "green_command": "rg -n Decision docs/a.md",
            "green_exit_code": 0, "green_result": "decision found",
        }]
        return fixture

    def _context(self, fixture: dict) -> dict:
        check = fixture["executor_result"]["acceptance_checks"][0]
        return {
            "expected_acceptance": {"AC-1": {"criterion": check["criterion"], "verification_method": check["verification_method"], "verifier": "AGENT", "slice_id": "DOC"}},
            "current_slice_ids": {"DOC"}, "completed_slice_ids": set(),
            "editable_paths": {"docs/a.md"}, "required_commands": ["rg -n Decision docs/a.md"],
            "check_verifiers": {"AC-1": ["rg -n Decision docs/a.md"]},
        }

    def test_doc_slice_red_against_head_validates(self) -> None:
        fixture = self._fixture()
        errors = validate_protocol.validate_payload("IMPLEMENT", fixture, **self._context(fixture))
        self.assertEqual([], errors, msg=errors)

    def test_doc_slice_with_empty_tdd_slices_is_refused(self) -> None:
        fixture = self._fixture()
        fixture["executor_result"]["tdd_slices"] = []
        errors = validate_protocol.validate_payload("IMPLEMENT", fixture, **self._context(fixture))
        self.assertTrue(errors, msg="empty tdd_slices must be refused")

    def test_code_profile_with_infrastructure_red_is_refused(self) -> None:
        fixture = self._fixture(red_kind="INFRASTRUCTURE")
        errors = validate_protocol.validate_payload("IMPLEMENT", fixture, **self._context(fixture))
        self.assertTrue(any("infrastructure failure is not valid RED evidence" in item["reason"] for item in errors), errors)


class DocRedPromptTests(unittest.TestCase):
    def test_prompt_lists_red_hint_only_for_doc_slices(self) -> None:
        from test_prompt_rules import FakeCtx, manifest, plan

        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            slice_ = {
                "current_slice_ids": ["DOC"], "completed_slice_ids": [], "editable_paths": ["docs/a.md"],
                "required_playbooks": [],
                "acceptance": {"AC-01": {"criterion": "the decision is recorded", "verification_method": "run `rg -n \"Decision\" docs/a.md`", "verifier": "AGENT", "slice_id": "DOC"}},
                "required_verification": [{"id": "check-ac-01", "kind": "STATIC_ANALYSIS", "command": "rg -n Decision docs/a.md", "check_ids": ["AC-01"], "introduced_by_slice": False}],
            }
            value = manifest(repo, "IMPLEMENT", slice_)
            ctx = FakeCtx(repo, profile="DECISION_DOC", delivery={
                "profile": "DECISION_DOC",
                "check_commands": {"AC-01": "rg -n Decision docs/a.md"},
                "acceptance": slice_["acceptance"],
                "authorization": {"quote": "q"},
            }, state={"ticket": {"id": "APP-1", "title": "t", "objective": "o"}, "baseline": {}, "gates": {}, "ownership": {}})
            text = sdd.build_prompt(ctx, plan("IMPLEMENT"), value)
            self.assertIn("RED for AC-01: `git show HEAD:docs/a.md | rg -n Decision` must exit ≠ 0 against HEAD", text)

            code_ctx = FakeCtx(repo, profile="CODE", delivery={
                "profile": "CODE",
                "check_commands": {"AC-01": "rg -n Decision docs/a.md"},
                "acceptance": slice_["acceptance"],
                "authorization": {"quote": "q"},
            }, state=ctx.state)
            code_value = manifest(repo, "IMPLEMENT", {**slice_, "editable_paths": ["src/*.py"]})
            code_text = sdd.build_prompt(code_ctx, plan("IMPLEMENT"), code_value)
            self.assertNotIn("RED for AC-01", code_text)


if __name__ == "__main__":
    unittest.main()
