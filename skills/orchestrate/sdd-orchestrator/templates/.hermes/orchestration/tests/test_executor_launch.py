"""Executor launcher: transport schemas, policy, preflight, argv and journaled foreground runs.

Real CLIs are never called: fake ``claude``/``codex`` Python scripts on a temporary PATH
emulate success, invalid output, non-zero exit and a hang.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from os import environ as process_environment
from pathlib import Path
from unittest import mock

from jsonschema import Draft7Validator, Draft202012Validator

ORCHESTRATION = Path(__file__).resolve().parents[1]
RUNTIME = ORCHESTRATION / "runtime"
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import action_journal as journal_module  # noqa: E402
import executor_launch as launch  # noqa: E402
from test_protocol import executor_fixture, review_fixture  # noqa: E402

SCRIPT = RUNTIME / "executor_launch.py"
JOURNAL_SCRIPT = RUNTIME / "action_journal.py"

FAKE_CLI = r'''
import json, os, sys, time
from pathlib import Path

name = Path(sys.argv[0]).name
args = sys.argv[1:]
mode = os.environ.get("FAKE_MODE", "success")
record = os.environ.get("FAKE_RECORD")
if record:
    Path(record).write_text(json.dumps({"argv": args, "cwd": os.getcwd(), "pid": os.getpid()}))

def value(flag):
    return args[args.index(flag) + 1] if flag in args else None

if "--version" in args:
    print("9.9.9 (" + name + ")")
    raise SystemExit(0)
if "--help" in args:
    if name == "claude":
        print("--print --output-format --json-schema --no-session-persistence --model --tools --add-dir --max-turns")
    else:
        print("--output-schema --output-last-message --ephemeral --model --sandbox --cd --add-dir")
    raise SystemExit(0)
if value("--model") == "bad-model":
    print("model not found", file=sys.stderr)
    raise SystemExit(1)
prompt = sys.stdin.read()
if mode == "sleep":
    time.sleep(120)
if mode == "fail":
    print("upstream failure", file=sys.stderr)
    raise SystemExit(3)
result = json.loads(os.environ.get("FAKE_RESULT", "{}"))
if name == "claude":
    schema = value("--json-schema")
    if schema is not None and "$schema" in json.loads(schema):
        print('Error: --json-schema is not a valid JSON Schema: no schema with key or ref "https://json-schema.org/draft/2020-12/schema"', file=sys.stderr)
        raise SystemExit(1)
    envelope = {"type": "result", "subtype": "success", "is_error": False, "result": "done"}
    if mode == "success":
        envelope["structured_output"] = result
    elif mode == "invalid":
        envelope["result"] = "I could not produce JSON, sorry."
    print(json.dumps(envelope))
else:
    schema_file = value("--output-schema")
    if schema_file and "$schema" in json.loads(Path(schema_file).read_text()):
        raise SystemExit(1)
    target = value("--output-last-message") or value("-o")
    if mode == "success":
        Path(target).write_text(json.dumps(result))
    elif mode == "invalid":
        Path(target).write_text("plain text final message")
'''


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class TransportSchemaTests(unittest.TestCase):
    def test_every_shipped_schema_has_a_transport_form_without_meta_keywords(self) -> None:
        paths = sorted((ORCHESTRATION / "schemas").glob("*.json"))
        self.assertGreaterEqual(len(paths), 6)
        for path in paths:
            with self.subTest(schema=path.name):
                transport = launch.transport_schema(json.loads(path.read_text(encoding="utf-8")))
                text = json.dumps(transport)
                for keyword in ("$schema", "$id", "$ref", "$defs"):
                    self.assertNotIn(f'"{keyword}"', text)
                Draft202012Validator.check_schema(transport)
                Draft7Validator.check_schema(transport)

    def test_transport_schema_accepts_and_rejects_exactly_like_the_source(self) -> None:
        cases = [("PLAN", executor_fixture("PLAN")), ("TEST", executor_fixture("TEST")), ("REVIEW", review_fixture())]
        for stage, payload in cases:
            with self.subTest(stage=stage):
                source = json.loads(launch.schema_path_for(stage, None).read_text(encoding="utf-8"))
                transport = launch.stage_transport_schema(stage)
                for validator_class in (Draft202012Validator, Draft7Validator):
                    self.assertEqual([], list(validator_class(transport).iter_errors(payload)))
                self.assertEqual([], list(Draft202012Validator(source).iter_errors(payload)))
                broken = json.loads(json.dumps(payload))
                root = next(iter(broken))
                broken[root]["unexpected"] = True
                self.assertTrue(list(Draft202012Validator(transport).iter_errors(broken)))
                self.assertTrue(list(Draft202012Validator(source).iter_errors(broken)))

    def test_schema_command_prints_transport_schema_and_role_selects_brief_schema(self) -> None:
        completed = subprocess.run([sys.executable, str(SCRIPT), "schema", "--stage", "PLAN"], capture_output=True, text=True, check=False)
        self.assertEqual(0, completed.returncode, completed.stderr)
        printed = json.loads(completed.stdout)
        self.assertNotIn("$schema", printed)
        self.assertIn("executor_result", printed["properties"])
        reviewer = launch.stage_transport_schema("REVIEW", "CODE_REVIEWER")
        self.assertIn("review_result", reviewer["properties"])
        with self.assertRaises(launch.LaunchError) as raised:
            launch.stage_transport_schema("PLAN", "CODE_REVIEWER")
        self.assertEqual("ROLE_UNKNOWN", raised.exception.status)

    def test_external_and_recursive_references_are_refused_with_next_step(self) -> None:
        for schema in ({"$ref": "https://example.invalid/x.json"}, {"$defs": {"a": {"$ref": "#/$defs/a"}}, "$ref": "#/$defs/a"}):
            with self.subTest(schema=schema), self.assertRaises(launch.LaunchError) as raised:
                launch.transport_schema(schema)
            self.assertEqual("SCHEMA_UNSUPPORTED", raised.exception.status)
            self.assertTrue(raised.exception.next_step)


class PolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="executors-policy-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def write_policy(self, block: str) -> Path:
        path = self.root / "EXECUTORS.md"
        path.write_text(f"# Executors\n\n```json\n{block}\n```\n", encoding="utf-8")
        return path

    def test_shipped_policy_matches_the_documented_defaults(self) -> None:
        policy = launch.load_policy()
        self.assertEqual("FILE", policy["source"])
        self.assertEqual(launch.DEFAULT_STAGE_POLICY, policy["stages"])
        for stage in ("SPECIFY", "CLARIFY", "PLAN", "REVIEW"):
            self.assertEqual("claude", policy["stages"][stage]["executor"])
        for stage in ("TASKS", "IMPLEMENT", "TEST"):
            self.assertEqual("codex", policy["stages"][stage]["executor"])
        for stage, entry in policy["stages"].items():
            self.assertEqual(900 if stage in {"PLAN", "IMPLEMENT"} else 600, entry["timeout_seconds"])

    def test_missing_file_uses_defaults_and_partial_file_overrides_one_stage(self) -> None:
        self.assertEqual("DEFAULTS", launch.load_policy(self.root / "absent.md")["source"])
        policy = launch.load_policy(self.write_policy('{"executors_version": 1, "stages": {"PLAN": {"executor": "codex", "model": "gpt-x", "timeout_seconds": 1200}}}'))
        self.assertEqual({"executor": "codex", "model": "gpt-x", "timeout_seconds": 1200, "max_turns": 60}, policy["stages"]["PLAN"])
        self.assertEqual("claude", policy["stages"]["SPECIFY"]["executor"])

    def test_invalid_policies_fail_closed_with_next_step(self) -> None:
        for block in ("not json", '{"stages": {}}', '{"executors_version": 1, "stages": {"DONE": {}}}',
                      '{"executors_version": 1, "stages": {"PLAN": {"executor": "other"}}}',
                      '{"executors_version": 1, "stages": {"PLAN": {"timeout_seconds": 0}}}',
                      '{"executors_version": 1, "stages": {"PLAN": {"model": "a b; c"}}}',
                      '{"executors_version": 1, "stages": {"PLAN": {"shell": "x"}}}'):
            with self.subTest(block=block), self.assertRaises(launch.LaunchError) as raised:
                launch.load_policy(self.write_policy(block))
            self.assertEqual("POLICY_INVALID", raised.exception.status)
            self.assertIn("EXECUTORS.md", raised.exception.next_step)


class FakeCliMixin:
    def install_fakes(self, root: Path) -> Path:
        bin_dir = root / "bin"
        bin_dir.mkdir()
        for name in ("claude", "codex"):
            target = bin_dir / name
            target.write_text(f"#!{sys.executable}\n{FAKE_CLI}", encoding="utf-8")
            target.chmod(0o755)
        return bin_dir

    def environment(self, bin_dir: Path, **values: str):
        updates = {"PATH": f"{bin_dir}{os.pathsep}{process_environment.get('PATH', '')}", **values}
        return mock.patch.dict(process_environment, updates)


class PreflightTests(FakeCliMixin, unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="executor-preflight-")
        self.addCleanup(temp.cleanup)
        self.bin = self.install_fakes(Path(temp.name))
        self.empty = Path(temp.name) / "empty"
        self.empty.mkdir()

    def test_ready_for_both_executors_without_any_model_call(self) -> None:
        with self.environment(self.bin):
            for executor in ("claude", "codex"):
                with self.subTest(executor=executor):
                    result = launch.preflight(executor, "some-model", probe=False)
                    self.assertEqual("READY", result["status"], result)
                    self.assertEqual("NOT_PROBED", result["model_check"])
                    self.assertIn("9.9.9", result["version"])

    def test_probe_accepts_good_model_and_blocks_rejected_model(self) -> None:
        with self.environment(self.bin):
            self.assertEqual("ACCEPTED", launch.preflight("claude", "good-model", probe=True)["model_check"])
            blocked = launch.preflight("codex", "bad-model", probe=True)
        self.assertEqual(("BLOCKED", "MODEL_REJECTED"), (blocked["status"], blocked["reason"]))
        self.assertIn("--probe", blocked["next_command"])

    def test_missing_cli_or_flags_block_with_next_step(self) -> None:
        with mock.patch.dict(process_environment, {"PATH": str(self.empty)}):
            missing = launch.preflight("codex", None, probe=False)
        self.assertEqual(("BLOCKED", "EXECUTOR_UNAVAILABLE"), (missing["status"], missing["reason"]))
        self.assertTrue(missing["next_step"])
        old = self.empty / "claude"
        old.write_text(f"#!{sys.executable}\nprint('1.0.0')\n", encoding="utf-8")
        old.chmod(0o755)
        with mock.patch.dict(process_environment, {"PATH": str(self.empty)}):
            outdated = launch.preflight("claude", None, probe=False)
        self.assertEqual("EXECUTOR_CLI_UNSUPPORTED", outdated["reason"])
        self.assertIn("--json-schema", outdated["missing_flags"])
        with self.environment(self.bin):
            self.assertEqual("MODEL_INVALID", launch.preflight("claude", "model name; with spaces", probe=False)["reason"])


class LaunchRunTests(FakeCliMixin, unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="executor-run-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.bin = self.install_fakes(self.base)
        self.repo = self.base / "repo with spaces"
        self.repo.mkdir()
        self.runtime = self.base / "vault 💡" / ".hermes-runtime"
        self.runtime.mkdir(parents=True)
        self.journal = self.runtime / "ACTION_JOURNAL.json"
        self.final = self.runtime / "final-message.json"
        self.prompt = self.runtime / "prompt.txt"
        self.prompt.write_text("Do the stage work and answer with JSON.\n", encoding="utf-8")
        self.record = self.base / "fake-record.json"

    def prepare(self, stage: str, executor: str, *, prompt_hash: str | None = None, final: Path | None = None) -> None:
        workspace = {"path": str(self.repo), "branch": "dev", "head": "a" * 40, "git_common_dir": str(self.repo / ".git")}
        value = journal_module.empty_journal(workspace)
        value["action"].update({
            "id": f"T-1-{stage}-01", "ticket": "T-1", "stage": stage, "name": stage.lower(), "status": "PREPARED",
            "executor": executor.upper(), "schema_path": "schemas/EXECUTOR_RESULT_SCHEMA.json", "protocol_version": 3,
            "prompt_hash": prompt_hash or sha256_bytes(self.prompt.read_bytes()),
            "final_message_path": str(final or self.final), "attempt": 1, "retry_mode": None,
        })
        value["fingerprints"].update({"baseline": "b" * 64, "ownership": "o" * 64, "state_before": "s" * 64})
        journal_module.prepare_action(self.journal, value)

    def journal_value(self) -> dict:
        return json.loads(self.journal.read_text(encoding="utf-8"))

    def launch_args(self, stage: str, *extra: str) -> list[str]:
        return ["--stage", stage, "--prompt-file", str(self.prompt), "--journal", str(self.journal), "--final", str(self.final), *extra]

    def run_launcher(self, command: str, stage: str, *extra: str, mode: str = "success", result: dict | None = None) -> tuple[int, dict]:
        buffer = io.StringIO()
        values = {"FAKE_MODE": mode, "FAKE_RECORD": str(self.record), "FAKE_RESULT": json.dumps(result or executor_fixture(stage))}
        with self.environment(self.bin, **values), redirect_stdout(buffer):
            code = launch.main([command, *self.launch_args(stage, *extra)])
        return code, json.loads(buffer.getvalue())

    def test_build_claude_argv_uses_transport_schema_and_no_shell(self) -> None:
        self.prepare("PLAN", "claude")
        code, built = self.run_launcher("build", "PLAN", "--model", "opus-test", "--add-dir", str(self.runtime))
        self.assertEqual(0, code, built)
        argv = built["argv"]
        self.assertTrue(argv[0].endswith("claude"))
        for flag in ("-p", "--no-session-persistence", "--json-schema", "--tools"):
            self.assertIn(flag, argv)
        self.assertEqual("json", argv[argv.index("--output-format") + 1])
        self.assertEqual("opus-test", argv[argv.index("--model") + 1])
        self.assertEqual("60", argv[argv.index("--max-turns") + 1])
        self.assertEqual(str(self.runtime), argv[argv.index("--add-dir") + 1])
        self.assertNotIn("$schema", json.loads(argv[argv.index("--json-schema") + 1]))
        self.assertEqual((str(self.repo), 900), (built["cwd"], built["timeout_seconds"]))
        self.assertEqual("PREPARED", self.journal_value()["action"]["status"])

    def test_build_codex_argv_keeps_repository_as_working_root(self) -> None:
        self.prepare("IMPLEMENT", "codex")
        code, built = self.run_launcher("build", "IMPLEMENT", "--add-dir", str(self.runtime))
        self.assertEqual(0, code, built)
        argv = built["argv"]
        self.assertEqual(["exec", "--ephemeral"], argv[1:3])
        self.assertEqual(str(self.repo), argv[argv.index("--cd") + 1])
        self.assertEqual("workspace-write", argv[argv.index("--sandbox") + 1])
        self.assertEqual(built["transport_schema_file"], argv[argv.index("--output-schema") + 1])
        self.assertIn("--output-last-message", argv)
        self.assertEqual("-", argv[-1])
        self.assertEqual(900, built["timeout_seconds"])

    def test_claude_success_extracts_structured_output_and_records_journal(self) -> None:
        self.prepare("SPECIFY", "claude")
        code, result = self.run_launcher("run", "SPECIFY", "--add-dir", str(self.runtime))
        self.assertEqual(0, code, result)
        self.assertEqual(("ARTIFACT_READY", 0), (result["status"], result["exit_code"]))
        self.assertEqual(executor_fixture("SPECIFY"), json.loads(self.final.read_text(encoding="utf-8")))
        value = self.journal_value()
        self.assertEqual("ARTIFACT_READY", value["action"]["status"])
        self.assertEqual(0, value["process"]["exit_code"])
        self.assertEqual(sha256_bytes(self.final.read_bytes()), value["artifact"]["sha256"])
        self.assertIn("validate_protocol.py", result["next_command"])
        recorded = json.loads(self.record.read_text(encoding="utf-8"))
        self.assertEqual(str(self.repo.resolve()), str(Path(recorded["cwd"]).resolve()))
        self.assertEqual([], [path.name for path in self.runtime.iterdir() if path.name.startswith(".final")])

    def test_codex_success_copies_last_message_and_cleans_transport_files(self) -> None:
        self.prepare("TASKS", "codex")
        code, result = self.run_launcher("run", "TASKS")
        self.assertEqual(0, code, result)
        self.assertEqual("ARTIFACT_READY", result["status"])
        self.assertEqual(executor_fixture("TASKS"), json.loads(self.final.read_text(encoding="utf-8")))
        self.assertEqual("ARTIFACT_READY", self.journal_value()["action"]["status"])
        self.assertEqual(["ACTION_JOURNAL.json", "final-message.json", "prompt.txt"], sorted(path.name for path in self.runtime.iterdir()))

    def test_invalid_output_is_kept_as_evidence_and_classified_next(self) -> None:
        for stage, executor in (("PLAN", "claude"), ("TEST", "codex")):
            with self.subTest(executor=executor):
                self.final.unlink(missing_ok=True)
                self.journal.unlink(missing_ok=True)
                self.prepare(stage, executor)
                code, result = self.run_launcher("run", stage, mode="invalid")
                self.assertEqual(2, code)
                self.assertEqual("OUTPUT_INVALID", result["status"])
                self.assertIn("classify-invalid", result["next_command"])
                self.assertTrue(self.final.read_text(encoding="utf-8").strip())
                self.assertEqual("ARTIFACT_READY", self.journal_value()["action"]["status"])

    def test_non_zero_exit_records_process_and_points_to_archive(self) -> None:
        self.prepare("IMPLEMENT", "codex")
        code, result = self.run_launcher("run", "IMPLEMENT", mode="fail")
        self.assertEqual(2, code)
        self.assertEqual(("EXECUTOR_FAILED", 3), (result["status"], result["exit_code"]))
        self.assertIn("upstream failure", result["stderr_tail"])
        value = self.journal_value()
        self.assertEqual(("PROCESS_FINISHED", 3), (value["action"]["status"], value["process"]["exit_code"]))
        self.assertFalse(self.final.exists())
        self.assertIn("archive-interrupted", result["next_command"])

    def test_timeout_kills_process_group_records_124_and_archive_command_works(self) -> None:
        local = self.repo / ".hermes" / "orchestration"
        local.mkdir(parents=True)
        self.journal, self.final = local / "ACTION_JOURNAL.json", local / "final-message.json"
        self.prepare("PLAN", "claude")
        started = time.monotonic()
        with mock.patch.object(launch, "TERMINATE_GRACE_SECONDS", 2):
            code, result = self.run_launcher("run", "PLAN", "--timeout", "1", mode="sleep")
        self.assertLess(time.monotonic() - started, 30)
        self.assertEqual(2, code)
        self.assertEqual(("EXECUTOR_TIMEOUT", 124), (result["status"], result["exit_code"]))
        value = self.journal_value()
        self.assertEqual(("PROCESS_FINISHED", 124), (value["action"]["status"], value["process"]["exit_code"]))
        pid = json.loads(self.record.read_text(encoding="utf-8"))["pid"]
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assertIn("archive-interrupted", result["next_command"])
        archive = subprocess.run(shlex.split(result["next_command"]), capture_output=True, text=True, check=False)
        self.assertEqual(0, archive.returncode, archive.stdout + archive.stderr)
        self.assertEqual("INTERRUPTED", json.loads(archive.stdout)["decision"])
        self.assertTrue(journal_module.is_pristine_idle(self.journal_value()))

    def test_launcher_exception_still_records_process_finished(self) -> None:
        self.prepare("TASKS", "codex")
        with mock.patch.object(launch, "_spawn", side_effect=RuntimeError("spawn exploded")):
            code, result = self.run_launcher("run", "TASKS")
        self.assertEqual(2, code)
        self.assertEqual(("LAUNCHER_ERROR", 125), (result["status"], result["exit_code"]))
        value = self.journal_value()
        self.assertEqual(("PROCESS_FINISHED", 125), (value["action"]["status"], value["process"]["exit_code"]))
        self.assertIsNotNone(value["process"]["finished_at"])

    def test_interrupt_during_run_still_records_process_finished(self) -> None:
        self.prepare("TASKS", "codex")
        with mock.patch.object(launch, "_spawn", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.run_launcher("run", "TASKS")
        value = self.journal_value()
        self.assertEqual(("PROCESS_FINISHED", 125), (value["action"]["status"], value["process"]["exit_code"]))

    def test_dispatch_refused_unless_prepared_matching_and_allowed(self) -> None:
        code, result = self.run_launcher("run", "PLAN")
        self.assertEqual((2, "REPOSITORY_INVALID"), (code, result["status"]))
        code, result = self.run_launcher("run", "PLAN", "--repo", str(self.repo))
        self.assertEqual("DISPATCH_NOT_ALLOWED", result["status"])
        self.assertIn("recover", result["next_command"])
        self.assertFalse(self.record.exists())

        self.prepare("PLAN", "claude", prompt_hash="0" * 64)
        code, result = self.run_launcher("run", "PLAN")
        self.assertEqual("PROMPT_HASH_MISMATCH", result["status"])
        code, result = self.run_launcher("run", "SPECIFY")
        self.assertEqual("JOURNAL_MISMATCH", result["status"])
        code, result = self.run_launcher("run", "PLAN", "--executor", "codex")
        self.assertEqual("JOURNAL_MISMATCH", result["status"])
        self.assertFalse(self.record.exists())
        self.assertEqual("PREPARED", self.journal_value()["action"]["status"])

    def test_existing_final_message_blocks_redispatch(self) -> None:
        self.prepare("PLAN", "claude")
        self.final.write_text("{}", encoding="utf-8")
        code, result = self.run_launcher("run", "PLAN")
        self.assertEqual(2, code)
        self.assertIn(result["status"], {"DISPATCH_NOT_ALLOWED", "ARTIFACT_PENDING"})
        self.assertIn("recover", result["next_command"])
        self.assertFalse(self.record.exists())

    def test_started_record_passes_prompt_hash_when_journal_supports_it(self) -> None:
        self.prepare("PLAN", "claude")
        calls: list[tuple[str, ...]] = []
        real = launch._journal_cli

        def spy(journal: Path, *arguments: str):
            calls.append(arguments)
            if "--prompt-sha256" in arguments:
                arguments = arguments[: arguments.index("--prompt-sha256")]
            return real(journal, *arguments)

        with mock.patch.object(launch, "_journal_supports", return_value=True), mock.patch.object(launch, "_journal_cli", side_effect=spy):
            code, result = self.run_launcher("run", "PLAN")
        self.assertEqual(0, code, result)
        started = next(call for call in calls if "--started" in call)
        self.assertEqual(sha256_bytes(self.prompt.read_bytes()), started[started.index("--prompt-sha256") + 1])
        self.assertIn(("record-process", "--finished", "--exit-code", "0"), calls)

    def test_launcher_source_never_uses_a_shell(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("shell=True", source)
        self.assertNotIn("os.system", source)


if __name__ == "__main__":
    unittest.main()
