"""OpenCode executor: dispatch table, hermetic config, version pin, JSONL outcomes and journaled runs.

Fake ``opencode`` and ``codex`` executables on a temporary PATH emulate the CLI (the fake
``codex sandbox`` simply runs the wrapped command), and a temporary HOME holds the OpenCode
data directory, so these tests never touch the real one. The real-binary isolation
negatives live in ``test_opencode_isolation.py``.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from os import environ as process_environment
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "runtime"))
sys.path.insert(0, str(TESTS))

import action_journal as journal_module  # noqa: E402
import executor_dispatch  # noqa: E402
import executor_launch as launch  # noqa: E402
import opencode_executor  # noqa: E402
import opencode_transport  # noqa: E402
from test_protocol import executor_fixture  # noqa: E402

FIXTURE = TESTS / "fixtures" / "opencode" / "run_format_json_1.18.34.jsonl"
MODEL = "opencode-go/deepseek-v4.1-flash"
FAKE_OPENCODE = r'''
import json, os, subprocess, sys, time
from pathlib import Path
args = sys.argv[1:]
config = json.loads((Path(sys.argv[0]).resolve().parent / "fake-config.json").read_text())
if "--version" in args:
    print(config.get("version", "1.18.34")); raise SystemExit(0)
if "--help" in args:
    print("--format --model --pure --dir --auto --file"); raise SystemExit(0)
record = config.get("record")
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"]).pid if config.get("mode") == "sleep" else None
if record:
    Path(record).write_text(json.dumps({"argv": args, "cwd": os.getcwd(), "pid": os.getpid(), "child": child, "env": dict(os.environ)}))
prompt = sys.stdin.read()
if config.get("mode") == "sleep":
    time.sleep(120)
def event(kind, **part):
    print(json.dumps({"type": kind, "sessionID": "ses_x", "part": part}))
if config.get("mode") == "error":
    print(json.dumps({"type": "error", "error": {"name": "ProviderAuthError", "data": {"message": "no"}}})); raise SystemExit(1)
event("step_start")
if config.get("mode") == "partial":
    event("text", text="{}"); raise SystemExit(0)
event("text", text=config.get("text", ""))
if config.get("secret"):
    event("tool_use", state={"output": config["secret"]})
event("step_finish", reason="stop", tokens={"input": 3, "output": 2, "cache": {"read": 1, "write": 0}}, cost=0.5)
raise SystemExit(config.get("exit", 0))
'''
FAKE_CODEX = r'''
import os, sys
args = sys.argv[1:]
if args[:2] == ["sandbox", "--help"]:
    print("-c, --config <key=value>"); raise SystemExit(0)
os.execvp(args[args.index("--") + 1], args[args.index("--") + 1:])
'''


def events(*rows: dict) -> bytes:
    return ("\n".join(json.dumps(row) for row in rows) + "\n").encode("utf-8")


class OpencodeTransportTests(unittest.TestCase):
    def test_real_1_18_34_fixture_is_one_ok_result_with_measured_usage(self) -> None:
        outcome, data, diagnostics = opencode_transport.classify(FIXTURE.read_bytes(), 0)
        self.assertEqual("OK", outcome)
        self.assertEqual({"executor_result": {"note": "x"}}, json.loads(data))
        self.assertEqual(2, diagnostics["usage"]["num_turns"])
        self.assertEqual(4944, diagnostics["usage"]["input_tokens"])

    def test_every_outcome_class_maps_onto_an_existing_launcher_status(self) -> None:
        start, stop = {"type": "step_start", "part": {}}, {"type": "step_finish", "part": {"reason": "stop"}}
        text = lambda value: {"type": "text", "part": {"text": value}}  # noqa: E731
        cases = [
            ("OK", events(start, text('{"a": 1}'), stop), 0, False),
            ("INVALID_OUTPUT", events(start, text("not json"), stop), 0, False),
            ("INVALID_OUTPUT", events(start, text('{"a": 1}'), text('{"b": 2}'), stop), 0, False),
            ("INVALID_OUTPUT", b"plain text\n", 0, False),
            ("PARTIAL", events(start, text('{"a": 1}')), 0, False),
            ("PARTIAL", events(start, {"type": "step_finish", "part": {"reason": "tool-calls"}}), 0, False),
            ("PARTIAL", b"", 0, False),
            ("ERROR_EVENT", events({"type": "error", "error": {"name": "UnknownError"}}), 1, False),
            ("CRASH", events(start), 3, False),
            ("TIMEOUT", b"", 124, True),
        ]
        for expected, stdout, code, timed_out in cases:
            with self.subTest(expected=expected, stdout=stdout[:40]):
                self.assertEqual(expected, opencode_transport.classify(stdout, code, timed_out)[0])
        self.assertEqual(set(opencode_transport.OUTCOMES), set(opencode_transport.OUTCOME_STATUS))
        self.assertTrue(set(opencode_transport.OUTCOME_STATUS.values()) <= set(launch.LAUNCH_STATUSES))

    def test_only_the_last_step_counts_and_a_fenced_answer_is_normalized(self) -> None:
        earlier = [{"type": "step_start", "part": {}}, {"type": "text", "part": {"text": '{"draft": 1}'}},
                   {"type": "step_finish", "part": {"reason": "tool-calls"}}]
        final = [{"type": "step_start", "part": {}}, {"type": "text", "part": {"text": 'Done:\n```json\n{"a": 1}\n```'}},
                 {"type": "step_finish", "part": {"reason": "stop"}}]
        outcome, data, diagnostics = opencode_transport.classify(events(*earlier, *final), 0)
        self.assertEqual(("OK", {"a": 1}, "FENCED_BLOCK"), (outcome, json.loads(data), diagnostics["transport_source"]))


class OpencodePolicyTests(unittest.TestCase):
    def policy(self, block: str) -> Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "EXECUTORS.md"
        path.write_text(f"```json\n{block}\n```\n", encoding="utf-8")
        return path

    def test_opencode_is_optional_and_never_a_default(self) -> None:
        defaults = launch.load_policy(Path("/nonexistent/EXECUTORS.md"))
        self.assertNotIn("opencode", {entry["executor"] for entry in defaults["stages"].values()})
        self.assertNotIn("opencode", {entry["executor"] for entry in launch.load_policy()["stages"].values()})

    def test_opencode_stage_needs_a_provider_model_and_defaults_its_sandbox(self) -> None:
        stage = '"IMPLEMENT": {"executor": "opencode", "model": "%s"}'
        policy = launch.load_policy(self.policy('{"executors_version": 1, "stages": {%s}}' % (stage % MODEL)))
        settings = launch.stage_settings(policy, "IMPLEMENT", executor=None, model=None, timeout=None)
        self.assertEqual(("workspace-write", True), (settings["sandbox"], launch.is_writing_worker(settings)))
        review = launch.stage_settings(policy, "REVIEW", executor="opencode", model=MODEL, timeout=None)
        self.assertEqual(("read-only", False), (review["sandbox"], launch.is_writing_worker(review)))
        for block in ('{"executors_version": 1, "stages": {"IMPLEMENT": {"executor": "opencode"}}}',
                      '{"executors_version": 1, "stages": {"IMPLEMENT": {"executor": "opencode", "model": "flash"}}}',
                      '{"executors_version": 1, "stages": {"IMPLEMENT": {"executor": "aider", "model": "x/y"}}}',
                      '{"executors_version": 1, "stages": {"IMPLEMENT": {"executor": "opencode", "model": "a/b", "codex_home": "/x"}}}'):
            with self.subTest(block=block), self.assertRaises(launch.LaunchError) as raised:
                launch.load_policy(self.policy(block))
            self.assertEqual("POLICY_INVALID", raised.exception.status)

    def test_dispatch_table_is_explicit_per_executor(self) -> None:
        self.assertEqual(set(executor_dispatch.EXECUTORS), set(executor_dispatch.BUILDERS))
        self.assertEqual(set(executor_dispatch.EXECUTORS), set(executor_dispatch.HELP_ARGS))
        self.assertEqual(set(executor_dispatch.EXECUTORS), set(executor_dispatch.REQUIRED_HELP_FLAGS))
        self.assertEqual(["/bin/opencode", "run", "--help"], executor_dispatch.help_argv("opencode", "/bin/opencode"))
        with self.assertRaises(KeyError):
            executor_dispatch.launch_plan(settings={"executor": "aider"}, schema={}, repository=Path("/r"), final=Path("/f"), add_dirs=[], binary="x")

    def test_permission_denies_everything_but_the_read_dirs_and_never_asks(self) -> None:
        value = opencode_executor.permission(["/vault/Projects/Demo"], writing=True)
        flat = [rule for key in ("external_directory", "edit") for rule in value[key].values()] + [value[tool] for tool in opencode_executor.DENIED_TOOLS]
        self.assertNotIn("ask", flat)
        for tool in ("webfetch", "websearch", "codesearch", "task"):
            self.assertEqual("deny", value[tool])
        self.assertEqual("deny", value["external_directory"]["*"])
        self.assertEqual("allow", value["external_directory"]["/vault/Projects/Demo/*"])
        self.assertEqual("deny", value["edit"]["/vault/Projects/Demo/*"])
        self.assertEqual(["*", "../*", ".git", ".git/*"], list(value["edit"])[:4])
        self.assertEqual("deny", opencode_executor.permission([], writing=False)["edit"]["*"])

    def test_worker_environment_keeps_opencode_settings_but_drops_credentials(self) -> None:
        secret = "synthetic-" + "q" * 12
        names = {"OPENCODE_" + "API_KEY": secret, "OPENCODE_" + "CONSOLE_TOKEN": secret, "OPENCODE_CONFIG_CONTENT": "{}",
                 "OPENCODE_LOG_LEVEL": "WARN", "TYPESAFE_" + "API_KEY": secret, "OPENAI_" + "API_KEY": secret, "GITHUB_" + "TOKEN": secret}
        with mock.patch.dict(process_environment, names):
            env = launch.worker_environment("opencode")
        self.assertEqual("WARN", env["OPENCODE_LOG_LEVEL"])
        for dropped in names:
            if dropped != "OPENCODE_LOG_LEVEL":
                self.assertNotIn(dropped, env)
        hermetic = opencode_executor.hermetic_env(env, Path("/d"), opencode_executor.permission([], False))
        for name in opencode_executor.HERMETIC_ENV:
            self.assertEqual("1", hermetic[name])
        self.assertEqual(("/d/config", "/d/tmp"), (hermetic["XDG_CONFIG_HOME"], hermetic["TMPDIR"]))


class OpencodeLaunchTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="opencode-run-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.bin, self.home, self.repo, self.runtime = (self.base / name for name in ("bin", "home", "repo", "rt"))
        for path in (self.bin, self.home / ".local" / "share" / "opencode", self.repo, self.runtime):
            path.mkdir(parents=True)
        for name, body in (("opencode", FAKE_OPENCODE), ("codex", FAKE_CODEX)):
            (self.bin / name).write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
            (self.bin / name).chmod(0o755)
        self.journal, self.final, self.prompt = self.runtime / "ACTION_JOURNAL.json", self.runtime / "T.result.json", self.runtime / "p.md"
        self.prompt.write_text("work\n", encoding="utf-8")
        self.record = self.base / "record.json"
        self.policy = self.base / "EXECUTORS.md"
        self.policy.write_text('```json\n{"executors_version": 1, "stages": {"IMPLEMENT": {"executor": "opencode", "model": "%s"}, '
                               '"REVIEW": {"executor": "opencode", "model": "%s"}}}\n```\n' % (MODEL, MODEL), encoding="utf-8")

    def env(self, config: dict):
        (self.bin / "fake-config.json").write_text(json.dumps({"record": str(self.record), **config}), encoding="utf-8")
        return mock.patch.dict(process_environment, {"PATH": f"{self.bin}{os.pathsep}{process_environment['PATH']}", "HOME": str(self.home)})

    def prepare(self, stage: str) -> None:
        value = journal_module.empty_journal({"path": str(self.repo), "branch": "dev", "head": "a" * 40, "git_common_dir": str(self.repo / ".git")})
        value["action"].update({"id": f"T-{stage}-01", "ticket": "T", "stage": stage, "name": stage, "status": "PREPARED", "executor": "OPENCODE",
                                "schema_path": "s", "protocol_version": 3, "prompt_hash": launch._file_sha256(self.prompt),
                                "final_message_path": str(self.final), "attempt": 1, "retry_mode": None})
        value["fingerprints"].update({"baseline": "b" * 64, "ownership": "o" * 64, "state_before": "s" * 64})
        journal_module.prepare_action(self.journal, value)

    def launch(self, command: str, stage: str, config: dict, *extra: str) -> tuple[int, dict]:
        buffer = io.StringIO()
        with self.env(config), redirect_stdout(buffer):
            code = launch.main([command, "--stage", stage, "--prompt-file", str(self.prompt), "--journal", str(self.journal),
                                "--final", str(self.final), "--policy", str(self.policy), *extra])
        return code, json.loads(buffer.getvalue())

    def test_preflight_pins_the_version_and_requires_the_sandbox(self) -> None:
        with self.env({}):
            ready = launch.preflight("opencode", MODEL, probe=False)
        self.assertEqual("READY", ready["status"], ready)
        with self.env({"version": "1.18.35"}):
            blocked = launch.preflight("opencode", MODEL, probe=False)
        self.assertEqual(("BLOCKED", "EXECUTOR_CLI_UNSUPPORTED"), (blocked["status"], blocked["reason"]))
        self.assertIn("1.18.34", blocked["next_step"])
        with self.env({}):
            self.assertEqual("MODEL_INVALID", launch.preflight("opencode", "flash", probe=False)["reason"])
            (self.bin / "codex").unlink()
            with mock.patch.dict(process_environment, {"PATH": f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin"}):
                self.assertEqual("EXECUTOR_UNAVAILABLE", launch.preflight("opencode", MODEL, probe=False)["reason"])

    def test_build_wraps_opencode_in_the_seatbelt_and_records_version_model_and_config(self) -> None:
        self.prepare("IMPLEMENT")
        code, built = self.launch("build", "IMPLEMENT", {}, "--read-dir", str(self.runtime))
        self.assertEqual(0, code, built)
        argv = built["argv"]
        data = str(self.home / ".local" / "share" / "opencode")
        self.assertEqual(["sandbox", "-c", 'sandbox_mode="workspace-write"'], argv[1:4])
        self.assertIn(f"sandbox_workspace_write.writable_roots={json.dumps([data])}", argv)
        self.assertIn("sandbox_workspace_write.exclude_slash_tmp=true", argv)
        self.assertEqual(["run", "--format", "json", "--pure", "--auto", "--dir", str(self.repo), "-m", MODEL],
                         argv[argv.index("--") + 2: argv.index("--") + 11])
        self.assertEqual(("1.18.34", MODEL), (built["executor_version"], built["model"]))
        self.assertRegex(built["opencode_config_sha256"], "^[0-9a-f]{64}$")
        code, refused = self.launch("build", "IMPLEMENT", {}, "--add-dir", str(self.repo / "src"))
        self.assertEqual("ADD_DIR_WRITABLE_REFUSED", refused["status"])

    def test_run_accepts_one_result_stores_the_redacted_stream_and_cleans_up(self) -> None:
        self.prepare("IMPLEMENT")
        secret = "sk-ant-" + "a" * 40
        code, result = self.launch("run", "IMPLEMENT", {"text": json.dumps(executor_fixture("IMPLEMENT")), "secret": secret})
        self.assertEqual((0, "ARTIFACT_READY"), (code, result["status"]), result)
        self.assertEqual(("OK", "1.18.34", MODEL), (result["opencode_outcome"], result["executor_version"], result["model"]))
        self.assertEqual(executor_fixture("IMPLEMENT"), json.loads(self.final.read_text(encoding="utf-8")))
        raw = (self.runtime / "T.result.json.raw-stream.jsonl").read_text(encoding="utf-8")
        self.assertIn("step_finish", raw)
        self.assertNotIn(secret, raw)
        seen = json.loads(self.record.read_text(encoding="utf-8"))
        self.assertEqual(str(self.repo), seen["cwd"])
        for name in opencode_executor.HERMETIC_ENV:
            self.assertEqual("1", seen["env"][name])
        self.assertIn('"webfetch":"deny"', seen["env"]["OPENCODE_PERMISSION"])
        self.assertEqual([], list((self.home / ".local" / "share" / "opencode" / "sdd-runtime").iterdir()))
        self.assertFalse((self.runtime / "T.result.json.transport-schema.json").exists())

    def test_read_only_stage_runs_from_its_dispatch_directory(self) -> None:
        self.prepare("REVIEW")
        self.launch("run", "REVIEW", {"text": "{}"})
        seen = json.loads(self.record.read_text(encoding="utf-8"))
        self.assertTrue(seen["cwd"].endswith("/work") and "/sdd-runtime/" in seen["cwd"], seen["cwd"])
        self.assertIn('"edit":{"*":"deny"}', seen["env"]["OPENCODE_PERMISSION"])

    def test_failure_classes_map_onto_the_existing_statuses(self) -> None:
        for config, status in (({"mode": "error"}, "EXECUTOR_FAILED"), ({"mode": "partial"}, "EXECUTOR_NO_OUTPUT"),
                               ({"text": "prose only"}, "OUTPUT_INVALID"), ({"text": "{}", "exit": 3}, "EXECUTOR_FAILED")):
            with self.subTest(config=config):
                self.final.unlink(missing_ok=True)
                self.journal.unlink(missing_ok=True)
                self.prepare("IMPLEMENT")
                code, result = self.launch("run", "IMPLEMENT", config)
                self.assertEqual((2, status), (code, result["status"]), result)

    def test_timeout_kills_the_whole_process_group(self) -> None:
        self.prepare("IMPLEMENT")
        started = time.monotonic()
        with mock.patch.object(launch, "TERMINATE_GRACE_SECONDS", 1):
            code, result = self.launch("run", "IMPLEMENT", {"mode": "sleep"}, "--timeout", "2")
        self.assertLess(time.monotonic() - started, 30)
        self.assertEqual(("EXECUTOR_TIMEOUT", 124, "TIMEOUT"), (result["status"], result["exit_code"], result["opencode_outcome"]))
        seen = json.loads(self.record.read_text(encoding="utf-8"))
        for pid in (seen["pid"], seen["child"]):
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                    time.sleep(0.1)
                except ProcessLookupError:
                    break
            else:
                self.fail(f"process {pid} survived the timeout")


if __name__ == "__main__":
    unittest.main()
