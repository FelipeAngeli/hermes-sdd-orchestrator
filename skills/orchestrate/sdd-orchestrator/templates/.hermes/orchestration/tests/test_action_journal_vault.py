"""Action journal in Obsidian storage: the runtime lives in the vault, outside the worktree.

The end-to-end cycles run a real controller copy installed in a fake vault
container (``<vault>/Projects/App/.hermes/orchestration``) bound by the
container's own ``.hermes/obsidian.json``, exactly as the installer lays it out,
and drive the CLI as a subprocess with the repository as working directory.
"""
from __future__ import annotations

import hashlib
import json
import os
from os import environ as process_environment
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ORCHESTRATION = Path(__file__).resolve().parents[1]
RUNTIME = ORCHESTRATION / "runtime"
sys.path.insert(0, str(RUNTIME))

import action_journal as journal  # noqa: E402
import obsidian_binding  # noqa: E402
import wiki_layout  # noqa: E402


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def child_environment() -> dict[str, str]:
    environment = dict(process_environment)
    environment.pop(obsidian_binding.VAULT_ENV, None)
    environment.pop("TERMINAL_CWD", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


class VaultControllerCycleTests(unittest.TestCase):
    """Full journal cycles through a vault-resident controller and its external runtime."""

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="sdd journal vault-")
        self.addCleanup(temp.cleanup)
        self.base = Path(os.path.realpath(temp.name))
        self.vault = self.base / "vault"
        (self.vault / ".obsidian").mkdir(parents=True)
        self.container = self.vault / "Projects" / "App"
        self.container.mkdir(parents=True)
        wiki_layout.init(self.container, project="App", today="2026-10-08")
        binding_file = self.container / ".hermes" / "obsidian.json"
        binding_file.parent.mkdir(parents=True)
        binding_file.write_text(json.dumps({"schema_version": 1, "vault_path": str(self.vault), "project_container": "Projects/App"}), encoding="utf-8")
        runtime_copy = self.container / ".hermes" / "orchestration" / "runtime"
        shutil.copytree(RUNTIME, runtime_copy, ignore=shutil.ignore_patterns("__pycache__"))
        self.script = runtime_copy / "action_journal.py"
        self.repo = self.base / "code" / "repo"
        self.repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True, capture_output=True)
        binding = obsidian_binding.load_path(binding_file)
        self.runtime = obsidian_binding.runtime_dir(binding, self.repo)
        self.runtime.mkdir(parents=True)
        self.state = self.runtime / "STATE.md"
        self.state.write_text(self.state_text("SPECIFY"), encoding="utf-8")
        self.journal = self.runtime / "ACTION_JOURNAL.json"
        self.history = self.runtime / "action-journal-history"
        self.workspace = {"path": str(self.repo), "branch": "main", "head": "a" * 40, "git_common_dir": str(self.repo / ".git")}
        journal.atomic_write(self.journal, journal.empty_journal(self.workspace))

    def state_text(self, stage: str) -> str:
        return f'---\nworkspace:\n  path: "{self.repo}"\nstage: {stage}\n---\n'

    def cli(self, *arguments: str, expect: int = 0) -> dict:
        result = subprocess.run(
            [sys.executable, "-B", str(self.script), *arguments],
            cwd=self.repo, env=child_environment(), text=True, capture_output=True, check=False, timeout=60,
        )
        self.assertEqual(expect, result.returncode, f"{arguments}: {result.stdout}{result.stderr}")
        return json.loads(result.stdout)

    def j(self, command: str, *extra: str, expect: int = 0) -> dict:
        return self.cli("--journal", str(self.journal), "--json", *extra, command, expect=expect)

    def run_next_command(self, decision: dict, **placeholders: str) -> dict:
        command = decision["next_command"]
        for key, value in placeholders.items():
            command = command.replace(f"<{key}>", value)
        self.assertNotIn("<", command, command)
        arguments = shlex.split(command)
        self.assertEqual(Path(arguments[1]).resolve(), self.script.resolve())
        return self.cli(*arguments[2:])

    def action(self, action_id: str, *, parent: str | None = None, retry_mode: str = "FULL_REPLACEMENT",
               final: Path | None = None, parent_path: str | None = None, parent_sha: str | None = None) -> dict:
        value = journal.empty_journal(self.workspace)
        value["action"].update({
            "id": action_id, "ticket": "APP-1", "stage": "SPECIFY", "name": "specify", "status": "PREPARED",
            "executor": "CLAUDE", "schema_path": "schemas/EXECUTOR_RESULT_SCHEMA.json", "protocol_version": 2,
            "prompt_hash": digest("prompt"), "final_message_path": str(final or self.runtime / f"{action_id}.json"),
            "attempt": 1 if parent is None else 2, "retry_mode": retry_mode, "parent_action_id": parent,
            "parent_artifact_path": parent_path, "parent_artifact_sha256": parent_sha,
        })
        value["fingerprints"].update(baseline=digest("baseline"), ownership=digest("ownership"), state_before=journal.sha256(self.state))
        return value

    def commit_state_and_release(self) -> dict:
        before = journal.sha256(self.state)
        after_text = self.state_text("CLARIFY")
        self.j("prepare-state-commit", "--state-path", str(self.state), "--expected-before-hash", before, "--expected-after-hash", digest(after_text))
        self.state.write_text(after_text, encoding="utf-8")
        self.assertEqual("STATE_COMMITTED", self.j("mark-state-committed")["action"]["status"])
        self.assertEqual("RELEASED", self.j("release")["action"]["status"])
        released = self.j("recover")
        self.assertEqual("RELEASED", released["decision"])
        return self.run_next_command(released)

    def test_paths_reports_the_bound_vault_runtime(self) -> None:
        paths = self.cli("--json", "--repo", str(self.repo), "paths")
        self.assertEqual("OBSIDIAN", paths["storage"])
        self.assertEqual(str(self.journal), paths["journal"])
        self.assertEqual(str(self.history), paths["history_dir"])
        self.assertEqual(str(self.state), paths["state"])
        self.assertEqual(str(self.runtime / "INCIDENTS.md"), paths["incidents"])

    def test_full_success_cycle_commits_vault_state_and_rolls_over_into_vault_history(self) -> None:
        self.j("prepare", "--payload", json.dumps(self.action("APP-1-specify-001")))
        dispatch = self.j("recover")
        self.assertEqual("DISPATCH_ALLOWED", dispatch["decision"])
        self.run_next_command(dispatch)
        final = self.runtime / "APP-1-specify-001.json"
        final.write_text('{"executor_result": {}}', encoding="utf-8")
        self.assertEqual("PROCESS_FINISHED", self.j("record-process", "--finished", "--exit-code", "0")["action"]["status"])
        self.assertEqual("ARTIFACT_READY", self.j("record-artifact")["action"]["status"])
        self.assertEqual("VALIDATED", self.j("mark-validated")["action"]["status"])

        rollover = self.commit_state_and_release()

        self.assertEqual("ROLLED_OVER", rollover["decision"])
        archived = Path(rollover["archived_path"])
        self.assertEqual(self.history / "APP-1" / "APP-1-specify-001.json", archived)
        self.assertEqual("RELEASED", json.loads(archived.read_text(encoding="utf-8"))["action"]["status"])
        self.assertEqual("DISPATCH_ALLOWED", self.j("recover")["decision"])
        self.assertEqual([], [path for path in self.repo.rglob("*") if ".git" not in path.parts])

    def test_archive_interrupted_into_vault_history_with_existing_commands(self) -> None:
        self.j("prepare", "--payload", json.dumps(self.action("APP-1-specify-001")))
        self.j("record-process", "--started")
        self.j("record-process", "--finished", "--exit-code", "-15")

        archived = self.j("archive-interrupted", "--history-dir", str(self.history))

        self.assertEqual(str(self.history / "APP-1" / "APP-1-specify-001.json"), archived["archived_path"])
        self.assertEqual("DISPATCH_ALLOWED", archived["recovery_after_archive"])

    def test_timeout_archive_interrupted_then_parented_retry_in_vault(self) -> None:
        self.j("prepare", "--payload", json.dumps(self.action("APP-1-specify-001")))
        self.j("record-process", "--started", "--prompt-sha256", digest("prompt"))
        self.assertEqual("PROCESS_FINISHED", self.j("record-process", "--finished", "--exit-code", "124")["action"]["status"])
        self.assertEqual("ARTIFACT_MISSING", self.j("record-artifact", expect=2)["status"])

        decision = self.j("recover")
        self.assertEqual("ARCHIVE_INTERRUPTED_REQUIRED", decision["decision"])
        self.assertIn("archive-interrupted", decision["next_command"])
        self.assertIn(str(self.history), decision["next_command"])
        archived = self.run_next_command(decision)

        self.assertEqual("INTERRUPTED", archived["decision"])
        self.assertTrue(Path(archived["archived_path"]).is_file())
        self.assertTrue(str(archived["archived_path"]).startswith(str(self.history)))
        self.assertEqual("WRITTEN", archived["wiki"]["status"])
        self.assertEqual("DISPATCH_ALLOWED", self.j("recover")["decision"])
        retry = self.j("prepare", "--payload", json.dumps(self.action("APP-1-specify-002", parent="APP-1-specify-001")))
        self.assertEqual("APP-1-specify-001", retry["action"]["parent_action_id"])

    def test_interrupted_parent_artifact_is_adopted_without_redispatch(self) -> None:
        self.j("prepare", "--payload", json.dumps(self.action("APP-1-specify-001")))
        self.j("record-process", "--started", "--prompt-sha256", digest("prompt"))
        self.j("record-process", "--finished", "--exit-code", "124")
        self.j("archive-interrupted", "--history-dir", str(self.history))
        late = self.runtime / "APP-1-specify-001.json"
        late.write_text('{"executor_result": {"late": true}}', encoding="utf-8")

        adopt = self.action(
            "APP-1-specify-002", parent="APP-1-specify-001", retry_mode="ADOPT_PARENT_ARTIFACT", final=late,
            parent_path=str(late), parent_sha=journal.sha256(late),
        )
        self.j("prepare", "--payload", json.dumps(adopt), "--history-dir", str(self.history))
        reconcile = self.j("recover")
        self.assertEqual("RECONCILE_ARTIFACT", reconcile["decision"])
        self.assertIn("record-artifact", reconcile["next_command"])
        self.assertEqual("ADOPTION_DISPATCH_FORBIDDEN", self.j("record-process", "--started", expect=2)["status"])
        self.assertEqual("ARTIFACT_READY", self.run_next_command(reconcile)["action"]["status"])
        self.assertEqual("VALIDATED", self.j("mark-validated")["action"]["status"])

        rollover = self.commit_state_and_release()

        archived = json.loads(Path(rollover["archived_path"]).read_text(encoding="utf-8"))
        self.assertEqual("ADOPT_PARENT_ARTIFACT", archived["action"]["retry_mode"])
        self.assertIsNone(archived["process"]["started_at"])
        self.assertEqual(journal.sha256(late), archived["artifact"]["sha256"])

    def test_blocked_action_is_archived_with_reason_and_reopens_dispatch(self) -> None:
        self.j("prepare", "--payload", json.dumps(self.action("APP-1-specify-001")))
        self.j("block")
        decision = self.j("recover")
        self.assertEqual("BLOCKED", decision["decision"])
        self.assertEqual("ACTION_BLOCKED", decision["stop_reason"])
        self.assertIn("archive-blocked", decision["next_command"])

        archived = self.run_next_command(decision, reason="operator abandoned the action")

        self.assertEqual("ARCHIVED_BLOCKED", archived["decision"])
        snapshot = json.loads(Path(archived["archived_path"]).read_text(encoding="utf-8"))
        self.assertEqual("BLOCKED", snapshot["action"]["status"])
        self.assertIn("ARCHIVED_BLOCKED: operator abandoned the action", snapshot["incidents"])
        self.assertEqual("WRITTEN", archived["wiki"]["status"])
        self.assertEqual("DISPATCH_ALLOWED", self.j("recover")["decision"])

    def test_state_commit_refuses_another_file_in_the_vault_runtime(self) -> None:
        self.j("prepare", "--payload", json.dumps(self.action("APP-1-specify-001")))
        self.j("record-process", "--started")
        (self.runtime / "APP-1-specify-001.json").write_text("{}", encoding="utf-8")
        self.j("record-process", "--finished", "--exit-code", "0")
        self.j("record-artifact")
        self.j("mark-validated")
        other = self.runtime / "OTHER.md"
        other.write_text("x", encoding="utf-8")
        refused = self.j("prepare-state-commit", "--state-path", str(other), "--expected-before-hash", digest("x"), "--expected-after-hash", digest("y"), expect=2)
        self.assertEqual("STATE_PATH_UNSAFE", refused["status"])
        self.assertTrue(refused["next_step"])


class BoundRuntimeSecurityTests(unittest.TestCase):
    """Trust-boundary checks on the external runtime (ported from the reviewed live fix)."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="bound-runtime-")
        self.addCleanup(self.tempdir.cleanup)
        self.base = Path(self.tempdir.name).resolve()
        self.workspace = self.base / "worktrees" / "backend"
        self.workspace.mkdir(parents=True)
        self.vault = self.base / "vault"
        self.container = self.vault / "Projects" / "Fazenda"
        self.binding_file = self.container / ".hermes" / "obsidian.json"
        self.binding_file.parent.mkdir(parents=True)
        self.binding_file.write_text(json.dumps({
            "schema_version": 1, "vault_path": str(self.vault),
            "project_container": "Projects/Fazenda", "runtime_subpath": ".hermes-runtime",
        }), encoding="utf-8")
        self.binding = obsidian_binding.load_path(self.binding_file)
        self.runtime = obsidian_binding.runtime_dir(self.binding, self.workspace)
        self.journal_path = self.runtime / "ACTION_JOURNAL.json"
        self.runtime.mkdir(parents=True)
        patcher = mock.patch.object(obsidian_binding, "_installed_container_binding", lambda: self.binding_file)
        patcher.start()
        self.addCleanup(patcher.stop)
        environment = mock.patch.dict(process_environment, {})
        environment.start()
        self.addCleanup(environment.stop)
        process_environment.pop(obsidian_binding.VAULT_ENV, None)

    def journal_value(self) -> dict:
        value = journal.empty_journal({"path": str(self.workspace), "branch": "dev", "head": "a" * 40, "git_common_dir": str(self.workspace / ".git")})
        value["action"].update({
            "id": "APP-439-20260916T000000Z-01", "ticket": "APP-439", "stage": "PLAN", "name": "plan", "status": "PROCESS_FINISHED",
            "executor": "CODEX", "schema_path": ".hermes/schema.json", "protocol_version": 2, "prompt_hash": digest("prompt"),
            "final_message_path": str(self.workspace / "final.json"), "attempt": 1, "retry_mode": "FULL_REPLACEMENT",
        })
        value["fingerprints"].update(baseline=digest("baseline"), ownership=digest("ownership"), state_before=digest("state"))
        value["process"].update(started_at="2026-09-16T00:00:00Z", finished_at="2026-09-16T00:00:01Z", exit_code=-15)
        return value

    def validated_value(self) -> dict:
        value = self.journal_value()
        value["action"]["status"] = "VALIDATED"
        value["artifact"] = {"exists": True, "sha256": digest("artifact"), "validation_status": "VALID"}
        return value

    def test_state_commit_accepts_exact_bound_runtime_state_and_rereads_it(self) -> None:
        state = obsidian_binding.state_path(self.binding, self.workspace)
        state.write_text("before", encoding="utf-8")
        journal.atomic_write(self.journal_path, self.validated_value())

        journal.prepare_state_commit(self.journal_path, state, digest("before"), digest("after"))
        state.write_text("after", encoding="utf-8")
        committed = journal.mark_state_committed(self.journal_path)

        self.assertEqual("STATE_COMMITTED", committed["action"]["status"])
        self.assertTrue(committed["state_commit"]["verified"])

    def test_external_history_rejects_another_worktree_runtime(self) -> None:
        value = self.journal_value()
        journal.atomic_write(self.journal_path, value)
        other = self.base / "worktrees" / "other"
        other.mkdir()
        wrong_history = obsidian_binding.runtime_dir(self.binding, other) / "action-journal-history"

        with self.assertRaises(journal.JournalError) as error:
            journal.archive_interrupted_journal(self.journal_path, wrong_history)

        self.assertEqual("HISTORY_PATH_UNSAFE", error.exception.code)
        self.assertEqual(value, journal.load_journal(self.journal_path))

    def test_external_history_rejects_other_vault_despite_environment_override(self) -> None:
        value = self.journal_value()
        journal.atomic_write(self.journal_path, value)
        attacker_vault = self.base / "attacker-vault"
        (attacker_vault / "Projects" / "Fazenda").mkdir(parents=True)
        wrong_history = attacker_vault / "Projects" / "Fazenda" / ".hermes-runtime" / self.runtime.name / "history"

        with mock.patch.dict(process_environment, {obsidian_binding.VAULT_ENV: str(attacker_vault)}):
            with self.assertRaises(journal.JournalError) as error:
                journal.archive_interrupted_journal(self.journal_path, wrong_history)

        self.assertEqual("HISTORY_PATH_UNSAFE", error.exception.code)
        self.assertEqual(value, journal.load_journal(self.journal_path))
        self.assertFalse(wrong_history.exists())

    def test_external_runtime_symlink_is_rejected_without_following_it(self) -> None:
        self.runtime.rmdir()
        outside = self.base / "outside-runtime"
        outside.mkdir()
        self.runtime.symlink_to(outside, target_is_directory=True)

        with self.assertRaises(journal.JournalError) as error:
            journal.atomic_create_history(str(self.workspace), self.runtime / "history", "APP-439", "action-1", b"{}\n")

        self.assertEqual("HISTORY_PATH_UNSAFE", error.exception.code)
        self.assertFalse((outside / "history").exists())

    def test_external_container_ancestor_symlink_is_rejected(self) -> None:
        moved = self.base / "real-runtime-root"
        runtime_root = self.container / ".hermes-runtime"
        runtime_root.rename(moved)
        runtime_root.symlink_to(moved, target_is_directory=True)

        with self.assertRaises(journal.JournalError) as error:
            journal.atomic_create_history(str(self.workspace), self.runtime / "history", "APP-439", "action-1", b"{}\n")

        self.assertEqual("HISTORY_PATH_UNSAFE", error.exception.code)
        self.assertFalse((moved / self.runtime.name / "history").exists())

    def test_external_history_root_swap_before_persist_is_rejected(self) -> None:
        value = self.journal_value()
        journal.atomic_write(self.journal_path, value)
        outside = self.base / "outside-history"
        outside.mkdir()
        moved_runtime = self.runtime.with_name(f"{self.runtime.name}-moved")

        def swap_runtime(_: Path) -> None:
            self.runtime.rename(moved_runtime)
            self.runtime.symlink_to(outside, target_is_directory=True)

        previous_hook = journal._history_before_final_write_hook
        journal._history_before_final_write_hook = swap_runtime
        self.addCleanup(setattr, journal, "_history_before_final_write_hook", previous_hook)
        with self.assertRaises(journal.JournalError) as error:
            journal.archive_interrupted_journal(self.journal_path, self.runtime / "history")

        self.assertEqual("HISTORY_PATH_UNSAFE", error.exception.code)
        self.assertEqual(value, journal.load_journal(moved_runtime / "ACTION_JOURNAL.json"))
        self.assertEqual([], list(outside.iterdir()))

    def test_symlink_above_the_vault_is_accepted_in_either_spelling(self) -> None:
        alias = self.base / "alias"
        alias.symlink_to(self.base, target_is_directory=True)
        self.binding_file.write_text(json.dumps({
            "schema_version": 1, "vault_path": str(alias / "vault"),
            "project_container": "Projects/Fazenda", "runtime_subpath": ".hermes-runtime",
        }), encoding="utf-8")
        aliased_runtime = alias / "vault" / "Projects" / "Fazenda" / ".hermes-runtime" / self.runtime.name
        value = self.validated_value()
        journal.atomic_write(self.journal_path, value)
        state = self.runtime / "STATE.md"
        state.write_text("before", encoding="utf-8")

        for spelling in (aliased_runtime, self.runtime):
            with self.subTest(spelling=str(spelling)):
                journal.atomic_write(self.journal_path, value)
                prepared = journal.prepare_state_commit(self.journal_path, spelling / "STATE.md", digest("before"), digest("after"))
                self.assertEqual(state, journal.safe_state_path(prepared))
                archived, _ = journal.atomic_create_history(str(self.workspace), spelling / "history", "APP-439", "a-1", b"{}\n")
                self.assertTrue(archived.is_file())
        self.assertTrue((self.runtime / "history" / "APP-439" / "a-1.json").is_file())

    def test_external_state_rejects_wrong_file_symlink_and_history_traversal(self) -> None:
        journal.atomic_write(self.journal_path, self.validated_value())
        wrong_state = self.runtime / "OTHER.md"
        wrong_state.write_text("before", encoding="utf-8")
        outside_state = self.base / "outside-STATE.md"
        outside_state.write_text("before", encoding="utf-8")
        obsidian_binding.state_path(self.binding, self.workspace).symlink_to(outside_state)

        for candidate in (wrong_state, obsidian_binding.state_path(self.binding, self.workspace)):
            with self.subTest(state=candidate.name):
                with self.assertRaises(journal.JournalError) as error:
                    journal.prepare_state_commit(self.journal_path, candidate, digest("before"), digest("after"))
                self.assertEqual("STATE_PATH_UNSAFE", error.exception.code)
        with self.assertRaises(journal.JournalError) as traversal:
            journal.atomic_create_history(str(self.workspace), self.runtime / "history" / ".." / "other", "APP-439", "action-1", b"{}\n")
        self.assertEqual("HISTORY_PATH_UNSAFE", traversal.exception.code)

    def test_invalid_installed_binding_fails_closed_for_external_history(self) -> None:
        self.binding_file.write_text("{invalid", encoding="utf-8")
        with self.assertRaises(journal.JournalError) as error:
            journal.atomic_create_history(str(self.workspace), self.runtime / "history", "APP-439", "action-1", b"{}\n")
        self.assertEqual("HISTORY_PATH_UNSAFE", error.exception.code)

    def test_legacy_state_rejects_workspace_ancestor_symlink_escape(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "STATE.md").write_text("outside", encoding="utf-8")
        (self.workspace / "link").symlink_to(outside, target_is_directory=True)
        value = journal.empty_journal({"path": str(self.workspace), "branch": "dev", "head": "a" * 40, "git_common_dir": str(self.workspace / ".git")})
        value["state_commit"]["state_path"] = str(self.workspace / "link" / "STATE.md")

        with self.assertRaises(journal.JournalError) as error:
            journal.safe_state_path(value)

        self.assertEqual("STATE_PATH_UNSAFE", error.exception.code)

    def test_legacy_history_rejects_internal_symlink_without_resolving_it(self) -> None:
        outside = self.base / "outside-history"
        outside.mkdir()
        (self.workspace / "action-journal-history").symlink_to(outside, target_is_directory=True)

        with self.assertRaises(journal.JournalError) as error:
            journal.atomic_create_history(str(self.workspace), Path("action-journal-history/subagent-events"), "APP-439", "action-1", b"{}\n")

        self.assertEqual("HISTORY_PATH_UNSAFE", error.exception.code)
        self.assertEqual([], list(outside.iterdir()))

    @unittest.skipUnless(os.path.isdir("/private/var") and os.path.islink("/var"), "requires the macOS /var alias")
    def test_legacy_history_preserves_var_alias_in_returned_path(self) -> None:
        private_prefix = "/private/var/"
        if not str(self.workspace).startswith(private_prefix):
            self.skipTest("temporary directory is not under /private/var")
        alias_workspace = Path("/var/" + str(self.workspace)[len(private_prefix):])

        archived, _ = journal.atomic_create_history(str(alias_workspace), Path("action-journal-history"), "APP-439", "action-1", b"{}\n")

        self.assertTrue(archived.is_relative_to(alias_workspace))
        self.assertTrue(archived.is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
