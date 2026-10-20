"""Codex sandbox probe and per-stage settings: policy fields, argv, worker env and preflight.

No real ``codex``/``node``/``docker`` call is made: preflight uses a fake codex and the probe
is driven through a mocked runner.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from os import environ as process_environment
from pathlib import Path
from unittest import mock

ORCHESTRATION = Path(__file__).resolve().parents[1]
RUNTIME = ORCHESTRATION / "runtime"
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import action_journal as journal_module  # noqa: E402
import executor_launch as launch  # noqa: E402
import executor_sandbox  # noqa: E402

FAKE_CODEX = r'''
import sys
args = sys.argv[1:]
if "--version" in args:
    print("9.9.9 (codex)")
    raise SystemExit(0)
if "--help" in args:
    print("--output-schema --output-last-message --ephemeral --model --sandbox --cd --add-dir --config")
    raise SystemExit(0)
raise SystemExit(0)
'''


class PolicyExtensionTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="executors-extension-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def write_policy(self, stages: str) -> Path:
        path = self.root / "EXECUTORS.md"
        path.write_text(f"# Executors\n\n```json\n{{\"executors_version\": 1, \"stages\": {{{stages}}}}}\n```\n", encoding="utf-8")
        return path

    def test_sandbox_network_valid_only_for_codex_workspace_write(self) -> None:
        policy = launch.load_policy(self.write_policy('"IMPLEMENT": {"executor": "codex", "sandbox_network": true}'))
        self.assertTrue(policy["stages"]["IMPLEMENT"]["sandbox_network"])
        for block in (
            '"PLAN": {"executor": "claude", "sandbox_network": true}',
            '"TASKS": {"executor": "codex", "sandbox_network": true}',
            '"IMPLEMENT": {"executor": "codex", "sandbox": "read-only", "sandbox_network": true}',
            '"IMPLEMENT": {"executor": "codex", "sandbox_network": "yes"}',
        ):
            with self.subTest(block=block), self.assertRaises(launch.LaunchError) as raised:
                launch.load_policy(self.write_policy(block))
            self.assertEqual("POLICY_INVALID", raised.exception.status)

    def test_codex_home_and_env_from_are_codex_only_and_well_shaped(self) -> None:
        for block in (
            '"PLAN": {"executor": "claude", "codex_home": "/tmp/codex"}',
            '"IMPLEMENT": {"executor": "codex", "codex_home": "relative/dir"}',
            '"PLAN": {"executor": "claude", "env_from": ["MY_VAR"]}',
            '"IMPLEMENT": {"executor": "codex", "env_from": ["bad name"]}',
            '"IMPLEMENT": {"executor": "codex", "env_from": ["TYPESAFE_KEY"]}',
            '"IMPLEMENT": {"executor": "codex", "env_from": ["JEV_AI_KEY"]}',
            '"IMPLEMENT": {"executor": "codex", "env_from": ["HERMES_TOKEN"]}',
            '"IMPLEMENT": {"executor": "codex", "env_from": ["A", "B", "C", "D", "E"]}',
        ):
            with self.subTest(block=block), self.assertRaises(launch.LaunchError) as raised:
                launch.load_policy(self.write_policy(block))
            self.assertEqual("POLICY_INVALID", raised.exception.status)


    def test_env_from_refuses_names_already_passed_and_loader_names(self) -> None:
        for name in ("CODEX_HOME", "OPENAI_API_KEY"):
            block = f'"IMPLEMENT": {{"executor": "codex", "env_from": ["{name}"]}}'
            with self.subTest(name=name):
                try:
                    launch.load_policy(self.write_policy(block))
                except launch.LaunchError as exc:
                    self.assertEqual("POLICY_INVALID", exc.status)
                    self.assertIn("already passed to the worker", str(exc))
                else:
                    self.fail(f"env_from {name} must be refused")
        for name in ("LD_PRELOAD", "NODE_OPTIONS"):
            block = f'"IMPLEMENT": {{"executor": "codex", "env_from": ["{name}"]}}'
            with self.subTest(name=name):
                try:
                    launch.load_policy(self.write_policy(block))
                except launch.LaunchError as exc:
                    self.assertEqual("POLICY_INVALID", exc.status)
                else:
                    self.fail(f"env_from {name} must be refused")


class BuildArgvTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="executors-argv-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()

    def build(self, settings: dict) -> list[str]:
        return launch.build_argv(stage="IMPLEMENT", settings=settings, schema={}, repository=self.repo,
                                 final=self.root / "final.json", add_dirs=[], binary="codex")

    def test_network_config_added_only_when_sandbox_network_true(self) -> None:
        base = {"executor": "codex", "sandbox": "workspace-write"}
        self.assertNotIn("sandbox_workspace_write.network_access=true", self.build(base))
        argv = self.build({**base, "sandbox_network": True})
        self.assertIn("sandbox_workspace_write.network_access=true", argv)
        self.assertIn("sandbox_workspace_write.writable_roots=[]", argv)


class WorkerEnvironmentTests(unittest.TestCase):
    def test_env_from_passes_named_var_only_to_that_worker(self) -> None:
        names = {"MY_APP_VAR": "synthetic-value-123", "OTHER_VAR": "other"}
        with mock.patch.dict(process_environment, names):
            with_settings = launch.worker_environment("codex", {"executor": "codex", "env_from": ["MY_APP_VAR"]})
            without_settings = launch.worker_environment("codex")
        self.assertEqual("synthetic-value-123", with_settings["MY_APP_VAR"])
        self.assertNotIn("MY_APP_VAR", without_settings)
        self.assertNotIn("OTHER_VAR", with_settings)

    def test_missing_env_from_variable_is_executor_env_missing(self) -> None:
        with mock.patch.dict(process_environment, {"PATH": os.environ.get("PATH", "")}):
            with self.assertRaises(launch.LaunchError) as raised:
                launch.worker_environment("codex", {"executor": "codex", "env_from": ["MY_MISSING_VAR"]})
        self.assertEqual("EXECUTOR_ENV_MISSING", raised.exception.status)
        self.assertIn("MY_MISSING_VAR", str(raised.exception))

    def test_codex_home_sets_worker_codex_home_override(self) -> None:
        with mock.patch.dict(process_environment, {"CODEX_HOME": "/tmp/inherited"}):
            env = launch.worker_environment("codex", {"executor": "codex", "codex_home": "/tmp/stage-home"})
        self.assertEqual("/tmp/stage-home", env["CODEX_HOME"])

    def test_env_from_openapi_key_is_accepted_and_passed(self) -> None:
        additions, missing = executor_sandbox.apply_codex_env(
            "codex", {"executor": "codex", "env_from": ["OPENCODE_API_KEY"]},
            {"OPENCODE_API_KEY": "k", "PATH": "/bin"})
        self.assertEqual("k", additions["OPENCODE_API_KEY"])
        self.assertEqual([], missing)

    def test_codex_home_wins_over_env_from_as_defence_in_depth(self) -> None:
        additions, missing = executor_sandbox.apply_codex_env(
            "codex", {"executor": "codex", "codex_home": "/stage", "env_from": ["CODEX_HOME"]},
            {"CODEX_HOME": "/base"})
        self.assertEqual("/stage", additions["CODEX_HOME"])
        self.assertEqual([], missing)


class CodexHomeDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="executors-codex-home-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.home = self.root / "codex-home"
        self.home.mkdir()

    def test_codex_home_inside_repository_is_refused_at_dispatch(self) -> None:
        inside = self.repo / "codex-home"
        inside.mkdir()
        error = executor_sandbox.codex_home_error({"codex_home": str(inside)}, self.repo, self.root / ".hermes")
        self.assertIsNotNone(error)
        error = executor_sandbox.codex_home_error({"codex_home": str(self.home)}, self.repo, self.root / ".hermes")
        self.assertIsNone(error)

    def test_codex_home_missing_directory_is_refused_at_dispatch(self) -> None:
        error = executor_sandbox.codex_home_error({"codex_home": str(self.root / "absent")}, self.repo, self.root / ".hermes")
        self.assertIsNotNone(error)


class PreflightSandboxTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="executors-sandbox-preflight-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        codex = self.bin / "codex"
        codex.write_text(f"#!{sys.executable}\n{FAKE_CODEX}", encoding="utf-8")
        codex.chmod(0o755)
        self.path = mock.patch.dict(process_environment, {"PATH": f"{self.bin}{os.pathsep}{process_environment.get('PATH', '')}"})

    def preflight(self, probe_result: dict, *, stage: str = "IMPLEMENT", sandbox_network: bool = False) -> dict:
        policy = {
            "source": "FILE", "path": "EXECUTORS.md", "allow_bypass_permissions": False,
            "stages": {"IMPLEMENT": {"executor": "codex", "model": None, "timeout_seconds": 900, "max_turns": None,
                                     "sandbox": "workspace-write", "sandbox_network": sandbox_network}},
        }
        with self.path, mock.patch.object(launch, "load_policy", return_value=policy), \
                mock.patch.object(executor_sandbox, "probe_sandbox", return_value=probe_result):
            return launch.preflight("codex", None, probe=False, stage=stage, probe_sandbox=True)

    def test_blocked_tcp_adds_sandbox_network_required_warning(self) -> None:
        result = self.preflight({"tcp_listen": "BLOCKED", "unix_listen": "OK", "docker": "SKIPPED", "network_access": False})
        self.assertEqual("READY", result["status"])
        self.assertEqual(["SANDBOX_NETWORK_REQUIRED"], result["warnings"])
        self.assertIn("sandbox_network", result["next_step"])

    def test_all_ok_adds_no_warning(self) -> None:
        result = self.preflight({"tcp_listen": "OK", "unix_listen": "OK", "docker": "OK", "network_access": False})
        self.assertNotIn("warnings", result)
        self.assertEqual("READY", result["status"])

    def test_unsupported_probe_adds_no_required_warning(self) -> None:
        result = self.preflight({"tcp_listen": "UNSUPPORTED", "unix_listen": "UNSUPPORTED", "docker": "UNSUPPORTED", "network_access": False})
        self.assertNotIn("warnings", result)
        self.assertEqual("READY", result["status"])

    def test_sandbox_network_stage_adds_enabled_warning_and_no_required_warning(self) -> None:
        result = self.preflight({"tcp_listen": "BLOCKED", "unix_listen": "OK", "docker": "SKIPPED", "network_access": True}, sandbox_network=True)
        self.assertEqual(["SANDBOX_NETWORK_ENABLED"], result["warnings"])
        self.assertNotIn("SANDBOX_NETWORK_REQUIRED", result["warnings"])


class ProbeSandboxArgTests(unittest.TestCase):
    def test_probe_sandbox_without_stage_is_an_argparse_error(self) -> None:
        err = io.StringIO()
        with self.assertRaises(SystemExit) as raised:
            with redirect_stderr(err):
                launch.main(["preflight", "--executor", "codex", "--probe-sandbox"])
        self.assertEqual(2, raised.exception.code)
        self.assertIn("--stage", err.getvalue())


class ProbeSandboxUnitTests(unittest.TestCase):
    def settings(self, network: bool) -> dict:
        return {"executor": "codex", "sandbox": "workspace-write", "sandbox_network": network}

    def run_probe(self, *, network: bool, returncode: int, argv_capture: list) -> dict:
        def fake_run_quiet(argv, timeout, cwd=None, env=None):
            argv_capture.append(argv)
            if "--help" in argv:
                return subprocess.CompletedProcess(argv, 0, "--config", "")
            return subprocess.CompletedProcess(argv, returncode, "", "")

        with mock.patch.object(executor_sandbox.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}"):
            return executor_sandbox.probe_sandbox(binary="codex", settings=self.settings(network), env={}, run_quiet=fake_run_quiet)

    def test_probe_argv_includes_network_config_when_enabled(self) -> None:
        captured: list = []
        result = self.run_probe(network=True, returncode=0, argv_capture=captured)
        self.assertTrue(result["network_access"])
        sandboxed = [argv for argv in captured if len(argv) > 2 and argv[1] == "sandbox" and "--help" not in argv]
        self.assertTrue(sandboxed)
        self.assertTrue(all("sandbox_workspace_write.network_access=true" in argv for argv in sandboxed))

    def test_probe_reports_blocked_and_ok(self) -> None:
        def fake_run_quiet(argv, timeout, cwd=None, env=None):
            if "--help" in argv:
                return subprocess.CompletedProcess(argv, 0, "--config", "")
            script = argv[-1]
            if "127.0.0.1" in script:
                return subprocess.CompletedProcess(argv, 1, "", "EPERM")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with mock.patch.object(executor_sandbox.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}"):
            result = executor_sandbox.probe_sandbox(binary="codex", settings=self.settings(False), env={}, run_quiet=fake_run_quiet)
        self.assertEqual("BLOCKED", result["tcp_listen"])
        self.assertEqual("OK", result["unix_listen"])

    def test_missing_node_skips_node_probes(self) -> None:
        def fake_run_quiet(argv, timeout, cwd=None, env=None):
            if "--help" in argv:
                return subprocess.CompletedProcess(argv, 0, "--config", "")
            return subprocess.CompletedProcess(argv, 0, "", "")

        def which(name):
            return None if name == "node" else f"/usr/bin/{name}"

        with mock.patch.object(executor_sandbox.shutil, "which", side_effect=which):
            result = executor_sandbox.probe_sandbox(binary="codex", settings=self.settings(False), env={}, run_quiet=fake_run_quiet)
        self.assertEqual("SKIPPED", result["tcp_listen"])
        self.assertEqual("SKIPPED", result["unix_listen"])
        self.assertEqual("OK", result["docker"])

    def test_config_unsupported_marks_every_probe_unsupported(self) -> None:
        def fake_run_quiet(argv, timeout, cwd=None, env=None):
            if "--help" in argv:
                return subprocess.CompletedProcess(argv, 0, "usage: codex sandbox", "")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with mock.patch.object(executor_sandbox.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}"):
            result = executor_sandbox.probe_sandbox(binary="codex", settings=self.settings(False), env={}, run_quiet=fake_run_quiet)
        self.assertEqual("UNSUPPORTED", result["tcp_listen"])
        self.assertEqual("UNSUPPORTED", result["unix_listen"])
        self.assertEqual("UNSUPPORTED", result["docker"])
        self.assertFalse(result["network_access"])

    def test_docker_probe_skipped_when_unsandboxed_baseline_fails(self) -> None:
        calls: list = []

        def fake_run_quiet(argv, timeout, cwd=None, env=None):
            calls.append(argv)
            if "--help" in argv:
                return subprocess.CompletedProcess(argv, 0, "--config", "")
            if argv[:2] == ["docker", "info"]:
                return subprocess.CompletedProcess(argv, 1, "", "daemon down")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with mock.patch.object(executor_sandbox.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}"):
            result = executor_sandbox.probe_sandbox(binary="codex", settings=self.settings(False), env={}, run_quiet=fake_run_quiet)
        self.assertEqual("SKIPPED", result["docker"])
        self.assertFalse(any(len(argv) > 3 and argv[1] == "sandbox" and argv[-1] == "info" for argv in calls))

    def test_docker_probe_blocked_only_when_baseline_succeeds_and_sandboxed_fails(self) -> None:
        def fake_run_quiet(argv, timeout, cwd=None, env=None):
            if "--help" in argv:
                return subprocess.CompletedProcess(argv, 0, "--config", "")
            if argv[:2] == ["docker", "info"]:
                return subprocess.CompletedProcess(argv, 0, "", "")
            if argv[1] == "sandbox" and argv[-1] == "info":
                return subprocess.CompletedProcess(argv, 1, "", "EPERM")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with mock.patch.object(executor_sandbox.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}"):
            result = executor_sandbox.probe_sandbox(binary="codex", settings=self.settings(False), env={}, run_quiet=fake_run_quiet)
        self.assertEqual("BLOCKED", result["docker"])
        self.assertEqual("OK", result["tcp_listen"])
