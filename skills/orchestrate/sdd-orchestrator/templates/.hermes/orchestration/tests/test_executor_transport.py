"""Tolerant Codex final-message transport: normalization, sidecar and dispatch statuses.

No real CLI is called: the normalization units run directly, and the dispatch tests use a
fake ``codex`` that writes a config-driven last message.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
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
import executor_transport as transport  # noqa: E402
from test_protocol import executor_fixture  # noqa: E402

FAKE_CODEX = r'''
import json, sys
from pathlib import Path

args = sys.argv[1:]
config_path = Path(sys.argv[0]).resolve().parent / "fake-codex-config.json"
config = json.loads(config_path.read_text()) if config_path.is_file() else {}


def value(flag):
    return args[args.index(flag) + 1] if flag in args else None

if "--version" in args:
    print("9.9.9 (codex)")
    raise SystemExit(0)
if "--help" in args:
    print("--output-schema --output-last-message --ephemeral --model --sandbox --cd --add-dir --config")
    raise SystemExit(0)
target = value("--output-last-message")
if target:
    Path(target).write_text(config.get("last_message", ""))
'''


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class NormalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="codex-normalize-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def normalize(self, text: str) -> tuple[bytes | None, bool, dict]:
        return transport.normalize_codex_last_message(text.encode("utf-8"))

    def test_one_fenced_valid_block_normalizes(self) -> None:
        data, is_object, diagnostics = self.normalize('Composing the final executor_result.\n\n```json\n{"status": "ok", "n": 1}\n```\n')
        self.assertTrue(is_object)
        self.assertEqual({"transport_normalized": True, "transport_source": "FENCED_BLOCK"}, diagnostics)
        self.assertEqual({"status": "ok", "n": 1}, json.loads(data))
        self.assertTrue(data.endswith(b"\n"))

    def test_bare_fence_without_language_tag_also_normalizes(self) -> None:
        data, is_object, diagnostics = self.normalize('```\n{"a": 1}\n```\n')
        self.assertTrue(is_object)
        self.assertEqual("FENCED_BLOCK", diagnostics["transport_source"])

    def test_single_bare_object_with_prose_normalizes(self) -> None:
        data, is_object, diagnostics = self.normalize('Before.\n{"status": "done"}\nAfter.')
        self.assertTrue(is_object)
        self.assertEqual({"transport_normalized": True, "transport_source": "SINGLE_OBJECT"}, diagnostics)
        self.assertEqual({"status": "done"}, json.loads(data))

    def test_two_fenced_blocks_stay_invalid(self) -> None:
        data, is_object, diagnostics = self.normalize('```json\n{"a": 1}\n```\n```json\n{"b": 2}\n```\n')
        self.assertFalse(is_object)
        self.assertEqual({}, diagnostics)
        self.assertIn(b"```", data)

    def test_prose_without_block_stays_invalid(self) -> None:
        data, is_object, diagnostics = self.normalize("Just some prose, no JSON object anywhere.")
        self.assertFalse(is_object)
        self.assertEqual({}, diagnostics)

    def test_fenced_block_containing_an_array_stays_invalid(self) -> None:
        data, is_object, diagnostics = self.normalize('```json\n[1, 2, 3]\n```\n')
        self.assertFalse(is_object)
        self.assertEqual({}, diagnostics)

    def test_already_valid_json_message_is_unchanged_without_normalization_key(self) -> None:
        raw = b'{"a": 1}\n'
        data, is_object, diagnostics = self.normalize(raw.decode("utf-8"))
        self.assertTrue(is_object)
        self.assertEqual(raw, data)
        self.assertNotIn("transport_normalized", diagnostics)

    def test_two_top_level_bare_objects_stay_invalid(self) -> None:
        data, is_object, diagnostics = self.normalize('{"a": 1} {"b": 2}')
        self.assertFalse(is_object)
        self.assertEqual({}, diagnostics)

    def test_extract_result_codex_path_delegates_to_normalizer(self) -> None:
        last = self.root / "last.txt"
        last.write_text('```json\n{"ok": true}\n```\n', encoding="utf-8")
        data, is_object, diagnostics = transport.extract_result("codex", b"", last)
        self.assertTrue(is_object)
        self.assertEqual("FENCED_BLOCK", diagnostics["transport_source"])
        self.assertEqual({"ok": True}, json.loads(data))


class CodexTransportRunTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="codex-transport-run-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        codex = self.bin / "codex"
        codex.write_text(f"#!{sys.executable}\n{FAKE_CODEX}", encoding="utf-8")
        codex.chmod(0o755)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.runtime = self.base / "runtime"
        self.runtime.mkdir()
        self.journal = self.runtime / "ACTION_JOURNAL.json"
        self.final = self.runtime / "final-message.json"
        self.prompt = self.runtime / "prompt.txt"
        self.prompt.write_text("Do the stage work.\n", encoding="utf-8")

    def prepare(self) -> None:
        workspace = {"path": str(self.repo), "branch": "dev", "head": "a" * 40, "git_common_dir": str(self.repo / ".git")}
        value = journal_module.empty_journal(workspace)
        value["action"].update({
            "id": "T-1-TEST-01", "ticket": "T-1", "stage": "TEST", "name": "test", "status": "PREPARED",
            "executor": "CODEX", "schema_path": "schemas/EXECUTOR_RESULT_SCHEMA.json", "protocol_version": 3,
            "prompt_hash": sha256_bytes(self.prompt.read_bytes()), "final_message_path": str(self.final),
            "attempt": 1, "retry_mode": None,
        })
        value["fingerprints"].update({"baseline": "b" * 64, "ownership": "o" * 64, "state_before": "s" * 64})
        journal_module.prepare_action(self.journal, value)

    def run_codex(self, last_message: str) -> tuple[int, dict]:
        (self.bin / "fake-codex-config.json").write_text(json.dumps({"last_message": last_message}), encoding="utf-8")
        buffer = io.StringIO()
        with mock.patch.dict(process_environment, {"PATH": f"{self.bin}{os.pathsep}{process_environment.get('PATH', '')}"}), redirect_stdout(buffer):
            code = launch.main(["run", "--stage", "TEST", "--prompt-file", str(self.prompt),
                                "--journal", str(self.journal), "--final", str(self.final)])
        return code, json.loads(buffer.getvalue())

    def test_fenced_block_reaches_artifact_ready_and_writes_sidecar(self) -> None:
        raw = 'Composing the final executor_result.\n\n```json\n{"status": "done", "stage": "TEST"}\n```\n'
        self.prepare()
        code, result = self.run_codex(raw)
        self.assertEqual(0, code, result)
        self.assertEqual("ARTIFACT_READY", result["status"])
        self.assertTrue(result["transport_normalized"])
        self.assertEqual("FENCED_BLOCK", result["transport_source"])
        self.assertEqual({"status": "done", "stage": "TEST"}, json.loads(self.final.read_text(encoding="utf-8")))
        self.assertEqual(raw, self.final.with_name(self.final.name + ".raw-message.txt").read_text(encoding="utf-8"))

    def test_two_fenced_blocks_stay_output_invalid(self) -> None:
        raw = '```json\n{"a": 1}\n```\n```json\n{"b": 2}\n```\n'
        self.prepare()
        code, result = self.run_codex(raw)
        self.assertEqual(2, code)
        self.assertEqual("OUTPUT_INVALID", result["status"])
        self.assertNotIn("transport_normalized", result)
        self.assertTrue(self.final.read_text(encoding="utf-8").strip())
        self.assertFalse(self.final.with_name(self.final.name + ".raw-message.txt").exists())

    def test_already_valid_json_message_has_no_sidecar(self) -> None:
        self.prepare()
        code, result = self.run_codex(json.dumps(executor_fixture("TEST")))
        self.assertEqual(0, code, result)
        self.assertEqual("ARTIFACT_READY", result["status"])
        self.assertNotIn("transport_normalized", result)
        self.assertFalse(self.final.with_name(self.final.name + ".raw-message.txt").exists())
        self.assertEqual(executor_fixture("TEST"), json.loads(self.final.read_text(encoding="utf-8")))

    def test_stale_raw_sidecar_is_removed_before_a_new_dispatch(self) -> None:
        self.prepare()
        sidecar = self.final.with_name(self.final.name + ".raw-message.txt")
        sidecar.write_text("stale previous dispatch", encoding="utf-8")
        code, result = self.run_codex(json.dumps(executor_fixture("TEST")))
        self.assertEqual(0, code, result)
        self.assertEqual("ARTIFACT_READY", result["status"])
        self.assertFalse(sidecar.exists())
        self.assertEqual(executor_fixture("TEST"), json.loads(self.final.read_text(encoding="utf-8")))

class PreflightMissingEnvTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="executors-missing-env-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        codex = self.bin / "codex"
        codex.write_text(f"#!{sys.executable}\n{FAKE_CODEX}", encoding="utf-8")
        codex.chmod(0o755)

    def test_missing_env_from_blocks_preflight_with_reason(self) -> None:
        self.assertIn("EXECUTOR_ENV_MISSING", launch.LAUNCH_STATUSES)
        self.assertIn("EXECUTOR_ENV_MISSING", launch.PREFLIGHT_REASONS)
        policy = {
            "source": "FILE", "path": "EXECUTORS.md", "allow_bypass_permissions": False,
            "stages": {"IMPLEMENT": {"executor": "codex", "model": None, "timeout_seconds": 900, "max_turns": None,
                                     "sandbox": "workspace-write", "env_from": ["MY_MISSING_VAR"]}},
        }
        with mock.patch.dict(process_environment, {"PATH": f"{self.bin}{os.pathsep}{process_environment.get('PATH', '')}"}):
            with mock.patch.object(launch, "load_policy", return_value=policy):
                result = launch.preflight("codex", None, probe=False, stage="IMPLEMENT", probe_sandbox=False)
        self.assertEqual(("BLOCKED", "EXECUTOR_ENV_MISSING"), (result["status"], result["reason"]))
        self.assertIn("MY_MISSING_VAR", result["detail"])



class RunMissingEnvTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="executors-run-missing-env-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.runtime = self.base / "runtime"
        self.runtime.mkdir()
        self.journal = self.runtime / "ACTION_JOURNAL.json"
        self.final = self.runtime / "final-message.json"
        self.prompt = self.runtime / "prompt.txt"
        self.prompt.write_text("Do the stage work.\n", encoding="utf-8")

    def test_missing_env_from_var_leaves_journal_prepared(self) -> None:
        policy = self.base / "EXECUTORS.md"
        policy.write_text('```json\n{"executors_version": 1, "stages": {"IMPLEMENT": {"executor": "codex", "env_from": ["MY_MISSING_VAR"]}}}\n```\n',
                          encoding="utf-8")
        workspace = {"path": str(self.repo), "branch": "dev", "head": "a" * 40, "git_common_dir": str(self.repo / ".git")}
        value = journal_module.empty_journal(workspace)
        value["action"].update({
            "id": "T-1-IMPLEMENT-01", "ticket": "T-1", "stage": "IMPLEMENT", "name": "implement", "status": "PREPARED",
            "executor": "CODEX", "schema_path": "schemas/EXECUTOR_RESULT_SCHEMA.json", "protocol_version": 3,
            "prompt_hash": hashlib.sha256(self.prompt.read_bytes()).hexdigest(), "final_message_path": str(self.final),
            "attempt": 1, "retry_mode": None,
        })
        value["fingerprints"].update({"baseline": "b" * 64, "ownership": "o" * 64, "state_before": "s" * 64})
        journal_module.prepare_action(self.journal, value)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = launch.main(["run", "--stage", "IMPLEMENT", "--prompt-file", str(self.prompt),
                                "--journal", str(self.journal), "--final", str(self.final),
                                "--policy", str(policy)])
        self.assertEqual(2, code)
        result = json.loads(buffer.getvalue())
        self.assertEqual("EXECUTOR_ENV_MISSING", result["status"])
        self.assertEqual("PREPARED", json.loads(self.journal.read_text(encoding="utf-8"))["action"]["status"])



class PreflightStageProbeEnvTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="executors-stage-probe-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.home = self.base / "codex-home"
        self.home.mkdir()
        self.bin = self.base / "bin"
        self.bin.mkdir()
        codex = self.bin / "codex"
        codex.write_text(f"#!{sys.executable}\n{FAKE_CODEX}", encoding="utf-8")
        codex.chmod(0o755)

    def test_stage_probe_receives_codex_home_and_env_from(self) -> None:
        policy = {
            "source": "FILE", "path": "EXECUTORS.md", "allow_bypass_permissions": False,
            "stages": {"IMPLEMENT": {"executor": "codex", "model": None, "timeout_seconds": 900, "max_turns": None,
                                     "sandbox": "workspace-write", "codex_home": str(self.home),
                                     "env_from": ["OPENCODE_API_KEY"]}},
        }
        captured: dict = {}

        def fake_model_probe(**kwargs):
            captured.update(kwargs)
            return {"model_check": "ACCEPTED", "probe_exit_code": 0, "probe_stderr_tail": ""}

        with mock.patch.dict(process_environment, {"PATH": f"{self.bin}{os.pathsep}{process_environment.get('PATH', '')}",
                                                   "OPENCODE_API_KEY": "secret"}):
            with mock.patch.object(launch, "load_policy", return_value=policy), \
                    mock.patch.object(executor_sandbox, "model_probe", side_effect=fake_model_probe):
                result = launch.preflight("codex", None, probe=True, stage="IMPLEMENT", probe_sandbox=False)
        self.assertEqual("ACCEPTED", result["model_check"])
        self.assertEqual(str(self.home), captured["worker_env"]["CODEX_HOME"])
        self.assertEqual("secret", captured["worker_env"]["OPENCODE_API_KEY"])
