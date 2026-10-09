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
# The launcher passes an allow-listed environment, so the test drives the fake through a file.
config_path = Path(sys.argv[0]).resolve().parent / "fake-config.json"
config = json.loads(config_path.read_text()) if config_path.is_file() else {}
mode = config.get("mode", "success")
record = config.get("record")
child = None
if mode == "sleep":
    import subprocess
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"]).pid
if record:
    Path(record + ".tmp").write_text(json.dumps({"argv": args, "cwd": os.getcwd(), "pid": os.getpid(), "child": child,
                                                 "env": sorted(os.environ)}))
    os.replace(record + ".tmp", record)

def value(flag):
    return args[args.index(flag) + 1] if flag in args else None

if "--version" in args:
    print("9.9.9 (" + name + ")")
    raise SystemExit(0)
if "--help" in args:
    if name == "claude":
        print("--print --output-format --json-schema --no-session-persistence --model --tools --add-dir --max-turns")
    else:
        print("--output-schema --output-last-message --ephemeral --model --sandbox --cd --add-dir --config")
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
result = config.get("result", {})
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
        reviewer = launch.stage_transport_schema("REVIEW", "SECURITY_REVIEWER")
        self.assertIn("review_result", reviewer["properties"])
        with self.assertRaises(launch.LaunchError) as raised:
            launch.stage_transport_schema("PLAN", "SECURITY_REVIEWER")
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

    def environment(self, bin_dir: Path, config: dict | None = None, **values: str):
        (bin_dir / "fake-config.json").write_text(json.dumps(config or {}), encoding="utf-8")
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

    def run_launcher(self, command: str, stage: str, *extra: str, mode: str = "success", result: dict | None = None,
                     env: dict[str, str] | None = None) -> tuple[int, dict]:
        buffer = io.StringIO()
        config = {"mode": mode, "record": str(self.record), "result": result or executor_fixture(stage)}
        with self.environment(self.bin, config, **(env or {})), redirect_stdout(buffer):
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
        code, built = self.run_launcher("build", "IMPLEMENT")
        self.assertEqual(0, code, built)
        argv = built["argv"]
        self.assertNotIn("--add-dir", argv)
        self.assertEqual("sandbox_workspace_write.writable_roots=[]", argv[argv.index("--config") + 1])
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

    # ------------------------------------------------------------------ security (PR #45 review)

    def test_writing_stage_refuses_add_dir_under_the_controller_or_outside_the_repository(self) -> None:
        self.prepare("IMPLEMENT", "codex")
        for directory in (launch.CONTROLLER_ROOT, launch.ORCHESTRATION_ROOT / "policies", self.runtime, self.base):
            with self.subTest(directory=str(directory)):
                code, result = self.run_launcher("build", "IMPLEMENT", "--add-dir", str(directory))
                self.assertEqual((2, "ADD_DIR_WRITABLE_REFUSED"), (code, result["status"]), result)
                self.assertNotIn("argv", result)
                self.assertTrue(result["next_step"])
        code, result = self.run_launcher("run", "IMPLEMENT", "--add-dir", str(launch.CONTROLLER_ROOT))
        self.assertEqual("ADD_DIR_WRITABLE_REFUSED", result["status"])
        self.assertFalse(self.record.exists(), "nothing may be dispatched")
        self.assertEqual("PREPARED", self.journal_value()["action"]["status"])
        inside = self.repo / "docs"
        inside.mkdir()
        code, built = self.run_launcher("build", "IMPLEMENT", "--add-dir", str(inside))
        self.assertEqual(0, code, built)

    def test_read_only_stage_may_read_the_controller_container(self) -> None:
        self.prepare("PLAN", "claude")
        code, built = self.run_launcher("build", "PLAN", "--add-dir", str(launch.CONTROLLER_ROOT))
        self.assertEqual(0, code, built)
        self.assertEqual("Read,Grep,Glob", built["argv"][built["argv"].index("--tools") + 1])

    def test_claude_with_write_tools_is_a_writing_worker(self) -> None:
        policy = self.base / "EXECUTORS.md"
        policy.write_text('```json\n{"executors_version": 1, "stages": {"PLAN": {"executor": "claude", "tools": "Read,Edit"}}}\n```\n', encoding="utf-8")
        self.prepare("PLAN", "claude")
        code, result = self.run_launcher("build", "PLAN", "--policy", str(policy), "--add-dir", str(launch.CONTROLLER_ROOT))
        self.assertEqual("ADD_DIR_WRITABLE_REFUSED", result["status"], result)

    def test_sdd_obsidian_dispatch_never_gives_a_writing_worker_the_container(self) -> None:
        import sdd

        self.assertEqual([], sdd.dispatch_dir_arguments("LOCAL"))
        extra = sdd.dispatch_dir_arguments("OBSIDIAN")
        self.assertEqual(["--read-dir", str(sdd.CONTROLLER_ROOT)], extra)
        for stage in ("IMPLEMENT", "TEST"):
            self.final.unlink(missing_ok=True)
            self.journal.unlink(missing_ok=True)
            self.prepare(stage, "codex")
            code, built = self.run_launcher("build", stage, *extra)
            self.assertEqual(0, code, built)
            argv = built["argv"]
            self.assertNotIn("--add-dir", argv, "Codex --add-dir is a writable root")
            self.assertIn("sandbox_workspace_write.writable_roots=[]", argv)
            self.assertNotIn(str(sdd.CONTROLLER_ROOT), argv)
        self.final.unlink(missing_ok=True)
        self.journal.unlink(missing_ok=True)
        self.prepare("PLAN", "claude")
        code, built = self.run_launcher("build", "PLAN", *extra)
        self.assertEqual(0, code, built)
        self.assertEqual(str(sdd.CONTROLLER_ROOT), built["argv"][built["argv"].index("--add-dir") + 1])
        policy = self.base / "EXECUTORS.md"
        policy.write_text('```json\n{"executors_version": 1, "stages": {"PLAN": {"executor": "claude", "tools": "default"}}}\n```\n', encoding="utf-8")
        code, result = self.run_launcher("build", "PLAN", "--policy", str(policy), *extra)
        self.assertEqual("ADD_DIR_WRITABLE_REFUSED", result["status"], result)

    def test_a_writing_worker_sharing_the_controller_filesystem_is_never_dispatched(self) -> None:
        """Prevention: the controller inside the worker's writable repository refuses the dispatch.

        Every tamper *detection* (policy pin, STATE fingerprint, journal) is written in the
        same tree the writing worker can rewrite, so the anchor is as writable as the thing
        it attests. The only sound answer is not to start the worker at all.
        """
        controller = self.repo / ".hermes"
        (controller / "orchestration" / "policies").mkdir(parents=True)
        with mock.patch.object(launch, "CONTROLLER_HERMES_ROOT", controller):
            for stage in ("IMPLEMENT", "TEST"):
                with self.subTest(stage=stage):
                    self.final.unlink(missing_ok=True)
                    self.journal.unlink(missing_ok=True)
                    self.prepare(stage, "codex")
                    code, result = self.run_launcher("build", stage)
                    self.assertEqual((2, "CONTROLLER_WRITABLE_BY_WORKER"), (code, result["status"]), result)
                    self.assertNotIn("argv", result)
                    self.assertTrue(result["next_step"])
                    code, result = self.run_launcher("run", stage)
                    self.assertEqual("CONTROLLER_WRITABLE_BY_WORKER", result["status"], result)
                    self.assertFalse(self.record.exists(), "nothing may be dispatched")
                    self.assertEqual("PREPARED", self.journal_value()["action"]["status"])

    def test_a_read_only_worker_may_share_the_controller_filesystem(self) -> None:
        """A worker that cannot write a file cannot rewrite the controller: only writers are refused."""
        controller = self.repo / ".hermes"
        controller.mkdir()
        with mock.patch.object(launch, "CONTROLLER_HERMES_ROOT", controller):
            self.prepare("PLAN", "claude")
            code, built = self.run_launcher("build", "PLAN")
            self.assertEqual(0, code, built)
            self.assertEqual("Read,Grep,Glob", built["argv"][built["argv"].index("--tools") + 1])

    def test_a_writing_worker_outside_the_controller_filesystem_is_still_dispatched(self) -> None:
        self.prepare("IMPLEMENT", "codex")
        code, built = self.run_launcher("build", "IMPLEMENT")
        self.assertEqual(0, code, built)
        self.assertIn("--cd", built["argv"])

    def test_claude_with_write_tools_sharing_the_controller_filesystem_is_refused(self) -> None:
        policy = self.base / "EXECUTORS.md"
        policy.write_text('```json\n{"executors_version": 1, "stages": {"PLAN": {"executor": "claude", "tools": "Read,Edit"}}}\n```\n',
                          encoding="utf-8")
        controller = self.repo / ".hermes"
        controller.mkdir()
        with mock.patch.object(launch, "CONTROLLER_HERMES_ROOT", controller):
            self.prepare("PLAN", "claude")
            code, result = self.run_launcher("build", "PLAN", "--policy", str(policy))
            self.assertEqual("CONTROLLER_WRITABLE_BY_WORKER", result["status"], result)

    def test_worker_environment_is_allow_listed(self) -> None:
        secret = "synthetic-" + "v" * 12
        names = {
            "TYPESAFE" + "_API_KEY": secret, "JEV_AI" + "_API_KEY": secret, "HERMES_" + "GATEWAY_TOKEN": secret,
            "OTHER_SERVICE" + "_API_KEY": secret, "GITHUB" + "_TOKEN": secret, "AWS_SECRET" + "_ACCESS_KEY": secret,
            "ANTHROPIC" + "_API_KEY": secret, "CLAUDE_CONFIG_DIR": "/tmp/claude-config", "OPENAI" + "_API_KEY": secret,
            "CODEX_HOME": "/tmp/codex-home", "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8", "HOME": "/tmp/home",
        }
        with mock.patch.dict(process_environment, names):
            codex = launch.worker_environment("codex")
            claude = launch.worker_environment("claude")
        for kept in ("PATH", "HOME", "LANG", "LC_ALL", "OPENAI" + "_API_KEY", "CODEX_HOME"):
            self.assertIn(kept, codex)
        for kept in ("ANTHROPIC" + "_API_KEY", "CLAUDE_CONFIG_DIR"):
            self.assertIn(kept, claude)
        for dropped in ("TYPESAFE" + "_API_KEY", "JEV_AI" + "_API_KEY", "HERMES_" + "GATEWAY_TOKEN", "OTHER_SERVICE" + "_API_KEY",
                        "GITHUB" + "_TOKEN", "AWS_SECRET" + "_ACCESS_KEY"):
            self.assertNotIn(dropped, codex)
            self.assertNotIn(dropped, claude)
        self.assertNotIn("ANTHROPIC" + "_API_KEY", codex)
        self.assertNotIn("OPENAI" + "_API_KEY", claude)

    def test_dispatched_worker_never_receives_unrelated_secrets(self) -> None:
        secret = "synthetic-" + "w" * 12
        self.prepare("TASKS", "codex")
        env = {"TYPESAFE" + "_API_KEY": secret, "JEV_AI" + "_API_KEY": secret, "OPENAI" + "_API_KEY": secret}
        code, result = self.run_launcher("run", "TASKS", env=env)
        self.assertEqual(0, code, result)
        seen = json.loads(self.record.read_text(encoding="utf-8"))["env"]
        self.assertIn("OPENAI" + "_API_KEY", seen)
        self.assertNotIn("TYPESAFE" + "_API_KEY", seen)
        self.assertNotIn("JEV_AI" + "_API_KEY", seen)

    def test_bypass_permissions_needs_the_explicit_owner_flag(self) -> None:
        policy = self.base / "EXECUTORS.md"
        stage = '"REVIEW": {"executor": "claude", "permission_mode": "bypassPermissions"}'
        policy.write_text(f'```json\n{{"executors_version": 1, "stages": {{{stage}}}}}\n```\n', encoding="utf-8")
        with self.assertRaises(launch.LaunchError) as raised:
            launch.load_policy(policy)
        self.assertEqual("EXECUTOR_POLICY_UNSAFE", raised.exception.status)
        self.assertIn("allow_bypass_permissions", raised.exception.next_step)
        policy.write_text(f'```json\n{{"executors_version": 1, "allow_bypass_permissions": true, "stages": {{{stage}}}}}\n```\n', encoding="utf-8")
        self.assertEqual("bypassPermissions", launch.load_policy(policy)["stages"]["REVIEW"]["permission_mode"])
        policy.write_text('```json\n{"executors_version": 1, "allow_bypass_permissions": "yes", "stages": {}}\n```\n', encoding="utf-8")
        with self.assertRaises(launch.LaunchError) as raised:
            launch.load_policy(policy)
        self.assertEqual("POLICY_INVALID", raised.exception.status)

    def test_sigterm_records_the_finish_and_kills_the_executor_group(self) -> None:
        import signal

        self.prepare("PLAN", "claude")
        (self.bin / "fake-config.json").write_text(json.dumps({"mode": "sleep", "record": str(self.record)}), encoding="utf-8")
        env = {key: value for key, value in process_environment.items()}
        env["PATH"] = f"{self.bin}{os.pathsep}{env.get('PATH', '')}"
        process = subprocess.Popen([sys.executable, str(SCRIPT), "run", *self.launch_args("PLAN")], env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 30
        while not self.record.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertTrue(self.record.exists(), "the fake executor never started")
        recorded = json.loads(self.record.read_text(encoding="utf-8"))
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=60)
        result = json.loads(stdout)
        self.assertEqual("LAUNCHER_INTERRUPTED", result["status"], stdout + stderr)
        self.assertIn("archive-interrupted", result["next_command"])
        value = self.journal_value()
        self.assertEqual(("PROCESS_FINISHED", 125), (value["action"]["status"], value["process"]["exit_code"]))
        for pid in (recorded["pid"], recorded["child"]):
            deadline = time.monotonic() + 10
            alive = True
            while alive and time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                    time.sleep(0.1)
                except ProcessLookupError:
                    alive = False
            self.assertFalse(alive, f"process {pid} of the executor group survived")
        with self.assertRaises(ProcessLookupError):
            os.killpg(recorded["pid"], 0)

    def test_launcher_source_never_uses_a_shell(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("shell=True", source)
        self.assertNotIn("os.system", source)


class DashPrefixedPathTests(unittest.TestCase):
    def test_validate_protocol_refuses_any_dash_prefixed_segment(self) -> None:
        import validate_protocol

        for path in ("--config=x", "-rf", "src/-x.py", "src/--help/a.py"):
            with self.subTest(path=path):
                self.assertFalse(validate_protocol._is_safe_relative(path))
                self.assertFalse(validate_protocol.editable_pattern_is_safe(path))
        for path in ("src/a-b.py", "src/x-/y.py", "tests/test_a.py"):
            with self.subTest(path=path):
                self.assertTrue(validate_protocol._is_safe_relative(path))


class GateCommandSecurityTests(unittest.TestCase):
    """`sdd.py gate`: dash-prefixed {files} and a controller policy changed during the demand are refused."""

    def setUp(self) -> None:
        import sdd

        self.sdd = sdd
        temp = tempfile.TemporaryDirectory(prefix="gate-security-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.gates = self.root / "GATES.md"
        self.executors = self.root / "EXECUTORS.md"
        self.marker = self.root / "ran"
        self.write_gates(f"`{shlex.quote(sys.executable)} -c \"import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(repr(sys.argv[2:]))\" {self.marker} {{files}}`")
        self.write_executors("claude")
        for patcher in (mock.patch.object(sdd, "GATES_POLICY", self.gates),
                        mock.patch.object(sdd, "EXECUTORS_POLICY", self.executors),
                        mock.patch.dict(sdd.CONTROLLER_POLICY_FILES, {"gates": self.gates, "executors": self.executors}),
                        mock.patch.object(sdd, "record_wiki")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.written: list[str] = []

    def write_gates(self, focused: str) -> None:
        self.gates.write_text("| Gate | Command | Executor | Timeout |\n| --- | --- | --- | --- |\n"
                              f"| Focused tests | {focused} | host | 30 s |\n", encoding="utf-8")

    def write_executors(self, executor: str) -> None:
        self.executors.write_text('```json\n{"executors_version": 1, "stages": {"TEST": {"executor": "%s"}}}\n```\n' % executor,
                                  encoding="utf-8")

    def pins(self, *, gates: bool = True, executors: bool = True) -> dict:
        recorded = {}
        if gates:
            recorded["gates"] = {"sha256": hashlib.sha256(self.gates.read_bytes()).hexdigest(), "by": "demand start"}
        if executors:
            recorded["executors"] = {"sha256": hashlib.sha256(self.executors.read_bytes()).hexdigest(), "by": "demand start"}
        return recorded

    def ctx(self, agent_owned: list[str], *, started_at: str = "2999-01-01T00:00:00Z",
            controller_policies: dict | None = None):
        test = self
        delivery = {"started_at": started_at}
        if controller_policies is not None:
            delivery["controller_policies"] = controller_policies

        class FakeCtx:
            repo = self.root
            stage = "TEST"
            ticket = "T-1"
            state = {"ownership": {"agent_owned": agent_owned}, "gates": {}, "delivery": delivery}

            def require_pristine(self) -> None:
                return None

            def write_state(self, data: dict, log: str) -> None:
                test.written.append(log)
                self.state = data

        return FakeCtx()

    def gate(self, ctx, *, confirm: str | None = None, quote: str | None = None) -> dict:
        args = mock.Mock(name="args", not_applicable=False, by="requester", quote=quote, confirm_policy=confirm)
        args.name = "focused_tests"
        return self.sdd.cmd_gate(ctx, args)

    def confirm(self, ctx, name: str, quote: str | None) -> dict:
        args = mock.Mock(name="args", by="requester", quote=quote)
        args.policy_name = name
        return self.sdd.cmd_confirm_policy(ctx, args)

    def test_dash_prefixed_agent_owned_path_is_refused_before_running(self) -> None:
        for path in ("--config=x", "src/-rf", "-x.py"):
            with self.subTest(path=path), self.assertRaises(self.sdd.SddError) as raised:
                self.gate(self.ctx(["src/ok.py", path], controller_policies=self.pins()))
            self.assertEqual("AGENT_OWNED_PATH_UNSAFE", raised.exception.code)
            self.assertIn(path, str(raised.exception))
            self.assertTrue(raised.exception.next_step)
            self.assertFalse(self.marker.exists())

    def test_safe_paths_are_passed_as_arguments(self) -> None:
        result = self.gate(self.ctx(["src/ok.py", "tests/test_ok.py"], controller_policies=self.pins()))
        self.assertEqual("PASS", result["status"])
        self.assertEqual("['src/ok.py', 'tests/test_ok.py']", self.marker.read_text(encoding="utf-8"))

    def test_gates_changed_after_the_recorded_hash_is_refused(self) -> None:
        ctx = self.ctx(["src/ok.py"], controller_policies=self.pins())
        self.assertEqual("PASS", self.gate(ctx)["status"])
        self.marker.unlink()
        self.write_gates(f"`{shlex.quote(sys.executable)} -c \"print(1)\"`")
        with self.assertRaises(self.sdd.SddError) as raised:
            self.gate(ctx)
        self.assertEqual("CONTROLLER_POLICY_CHANGED_DURING_DEMAND", raised.exception.code)
        self.assertEqual(["gates"], raised.exception.extra["changed"])
        self.assertIn("confirm-policy --name gates", raised.exception.next_command)
        self.assertTrue(raised.exception.next_step)
        self.assertFalse(self.marker.exists(), "no gate runs under an unconfirmed policy")

    def test_executors_changed_during_the_demand_also_blocks_every_gate(self) -> None:
        """A writing worker that rewrites EXECUTORS.md must not get a host gate run either."""
        ctx = self.ctx(["src/ok.py"], controller_policies=self.pins())
        self.assertEqual("PASS", self.gate(ctx)["status"])
        self.marker.unlink()
        self.write_executors("codex")
        with self.assertRaises(self.sdd.SddError) as raised:
            self.gate(ctx)
        self.assertEqual("CONTROLLER_POLICY_CHANGED_DURING_DEMAND", raised.exception.code)
        self.assertEqual(["executors"], raised.exception.extra["changed"])
        self.assertIn("confirm-policy --name executors", raised.exception.next_command)
        self.assertFalse(self.marker.exists())

    def test_both_policies_changed_are_reported_together(self) -> None:
        ctx = self.ctx(["src/ok.py"], controller_policies=self.pins())
        self.write_gates(f"`{shlex.quote(sys.executable)} -c \"print(1)\"`")
        self.write_executors("codex")
        with self.assertRaises(self.sdd.SddError) as raised:
            self.gate(ctx)
        self.assertEqual(["executors", "gates"], raised.exception.extra["changed"])

    def test_a_demand_without_pinned_policies_is_refused_instead_of_trusting_mtime(self) -> None:
        """No recorded hash is no proof: the controller cannot tell a worker edit from the owner's."""
        with self.assertRaises(self.sdd.SddError) as raised:
            self.gate(self.ctx(["src/ok.py"]))
        self.assertEqual("CONTROLLER_POLICY_UNPINNED", raised.exception.code)
        self.assertEqual(["executors", "gates"], raised.exception.extra["unpinned"])
        self.assertIn("confirm-policy --name", raised.exception.next_command)
        self.assertFalse(self.marker.exists())

    def test_confirm_policy_records_one_named_policy_and_never_runs_a_gate(self) -> None:
        ctx = self.ctx(["src/ok.py"], controller_policies={"gates": {"sha256": "0" * 64}, **self.pins(gates=False)})
        with self.assertRaises(self.sdd.SddError):
            self.confirm(ctx, "gates", "")
        result = self.confirm(ctx, "gates", "yes, I changed the gates")
        self.assertEqual("CONTROLLER_POLICY_CONFIRMED", result["status"])
        self.assertEqual("gates", result["policy"])
        self.assertFalse(self.marker.exists(), "confirmation never runs the gate")
        recorded = ctx.state["delivery"]["controller_policies"]["gates"]
        self.assertEqual(hashlib.sha256(self.gates.read_bytes()).hexdigest(), recorded["sha256"])
        self.assertEqual("yes, I changed the gates", recorded["quote"])
        self.assertEqual("PASS", self.gate(ctx)["status"])

    def test_confirming_one_policy_does_not_confirm_the_other(self) -> None:
        ctx = self.ctx(["src/ok.py"], controller_policies=self.pins())
        self.write_gates(f"`{shlex.quote(sys.executable)} -c \"print(1)\"`")
        self.write_executors("codex")
        self.confirm(ctx, "gates", "only the gates are mine")
        with self.assertRaises(self.sdd.SddError) as raised:
            self.gate(ctx)
        self.assertEqual(["executors"], raised.exception.extra["changed"])

    def test_gate_confirm_policy_shortcut_records_the_named_policy(self) -> None:
        ctx = self.ctx(["src/ok.py"], controller_policies=self.pins())
        self.write_gates(f"`{shlex.quote(sys.executable)} -c \"print(1)\"`")
        result = self.gate(ctx, confirm="gates", quote="I changed them")
        self.assertEqual("CONTROLLER_POLICY_CONFIRMED", result["status"])
        self.assertFalse(self.marker.exists())


class WorkerStateTamperTests(unittest.TestCase):
    """A worker that rewrites STATE or the journal in its own worktree is caught before accept.

    In ``--local-storage`` mode STATE, the journal and the controller policies live
    inside the repository, which a writing worker can edit. ``prepare`` already
    records ``fingerprints.state_before``; it is now verified, so a tampered STATE
    stops the loop instead of being folded in as if the controller had written it.
    """

    def setUp(self) -> None:
        import sdd

        self.sdd = sdd

    def journal(self, state_before: str | None) -> dict:
        return {"action": {"id": "T-1-test-01", "status": "ARTIFACT_READY"},
                "fingerprints": {"baseline": "b" * 64, "ownership": "o" * 64, "state_before": state_before}}

    def test_state_unchanged_since_prepare_is_accepted(self) -> None:
        text = "schema_version: 1\nstage: {current: TEST}\n"
        digest = self.sdd.sha256_bytes(text.encode("utf-8"))
        self.assertIsNone(self.sdd.state_tamper_error(self.journal(digest), digest))

    def test_state_rewritten_during_the_dispatch_is_refused(self) -> None:
        prepared = self.sdd.sha256_bytes(b"the STATE the controller prepared")
        tampered = self.sdd.sha256_bytes(b"the STATE a writing worker left behind")
        error = self.sdd.state_tamper_error(self.journal(prepared), tampered)
        self.assertIsNotNone(error)
        self.assertEqual("STATE_MODIFIED_DURING_ACTION", error.code)
        self.assertEqual(prepared, error.extra["expected_sha256"])
        self.assertEqual(tampered, error.extra["actual_sha256"])
        self.assertTrue(error.next_step)
        self.assertIn("sdd.py", error.next_command)

    def test_a_journal_without_the_fingerprint_is_not_silently_trusted(self) -> None:
        error = self.sdd.state_tamper_error(self.journal(None), "a" * 64)
        self.assertIsNotNone(error)
        self.assertEqual("STATE_MODIFIED_DURING_ACTION", error.code)

    def test_the_stop_reason_is_registered_with_one_next_command(self) -> None:
        import stop_reasons

        self.assertIn("STATE_MODIFIED_DURING_ACTION", stop_reasons.STOP_REASONS)
        described = stop_reasons.describe("STATE_MODIFIED_DURING_ACTION")
        self.assertEqual("BLOCKED", described["kind"])
        self.assertIn("sdd.py", described["next_command"])


class ControllerIsolationTests(unittest.TestCase):
    """`sdd.py` refuses a writing stage while the controller shares the worker's writable tree.

    The launcher refuses the dispatch (`CONTROLLER_WRITABLE_BY_WORKER`); the controller must
    end the turn with that stop instead of printing a PREPARE/DISPATCH batch that can only
    exit 2. Prevention replaces a detection whose anchors the worker could rewrite.
    """

    def setUp(self) -> None:
        import sdd

        self.sdd = sdd

    def settings(self, stage: str) -> dict:
        import executor_launch

        return executor_launch.stage_settings(executor_launch.load_policy(), stage, executor=None, model=None, timeout=None)

    def test_local_storage_refuses_every_writing_stage(self) -> None:
        for stage in ("IMPLEMENT", "TEST"):
            with self.subTest(stage=stage):
                error = self.sdd.controller_isolation_error("LOCAL", stage)
                self.assertIsNotNone(error)
                self.assertEqual("CONTROLLER_WRITABLE_BY_WORKER", error.code)
                self.assertIn("migrate_to_vault", error.next_command)
                self.assertTrue(error.next_step)

    def test_local_storage_still_allows_read_only_stages(self) -> None:
        for stage in ("SPECIFY", "CLARIFY", "PLAN", "TASKS", "REVIEW"):
            with self.subTest(stage=stage):
                self.assertIsNone(self.sdd.controller_isolation_error("LOCAL", stage))

    def test_obsidian_storage_allows_every_stage(self) -> None:
        for stage in self.sdd.STAGES:
            with self.subTest(stage=stage):
                self.assertIsNone(self.sdd.controller_isolation_error("OBSIDIAN", stage))

    def test_the_refused_stages_are_exactly_the_writing_workers(self) -> None:
        """The controller's refusal and the launcher's own writing-worker test agree."""
        import executor_launch

        for stage in self.sdd.STAGES:
            writing = executor_launch.is_writing_worker(self.settings(stage))
            refused = self.sdd.controller_isolation_error("LOCAL", stage) is not None
            with self.subTest(stage=stage):
                self.assertEqual(writing, refused)


if __name__ == "__main__":
    unittest.main()
