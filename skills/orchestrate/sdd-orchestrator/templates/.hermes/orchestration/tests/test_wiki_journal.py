"""Behavior of the wiki journal: everything the orchestrator runs lands in the project wiki."""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))

import action_journal  # noqa: E402
import wiki_journal  # noqa: E402
import wiki_layout  # noqa: E402

SCRIPT = RUNTIME / "wiki_journal.py"
WHEN = dt.datetime(2026, 10, 7, 12, 30, 5, tzinfo=dt.timezone.utc)


def setUpModule() -> None:
    """Behave like a source checkout even when the suite runs from an installed container."""
    patcher = mock.patch.object(wiki_journal, "_installed_container", return_value=None)
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


class JournalTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="sdd wiki journal-")
        self.addCleanup(temp.cleanup)
        self.base = Path(os.path.realpath(temp.name))
        self.vault = self.base / "vault"
        (self.vault / ".obsidian").mkdir(parents=True)
        self.container = self.vault / "Projects" / "App"
        self.container.mkdir(parents=True)
        wiki_layout.init(self.container, project="App", today="2026-10-07")
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.bind(self.repo)

    def bind(self, repo: Path) -> None:
        binding = repo / ".hermes" / "obsidian.json"
        binding.parent.mkdir(parents=True, exist_ok=True)
        binding.write_text(json.dumps({
            "schema_version": 1,
            "vault_path": str(self.vault),
            "project_container": "Projects/App",
        }), encoding="utf-8")

    def record(self, **kwargs) -> dict:
        kwargs.setdefault("when", WHEN)
        return wiki_journal.record(self.container, **kwargs)

    def log(self) -> str:
        return (self.container / "log.md").read_text(encoding="utf-8")


class RecordTests(JournalTestCase):
    def test_stage_artifact_lands_in_raw_articles_of_the_ticket_and_is_logged(self) -> None:
        result = self.record(kind="stage", title="Login spec", body="# Spec\n\nUsers sign in.", ticket="APP-12", stage="SPECIFY")
        self.assertEqual("WRITTEN", result["status"])
        self.assertEqual("raw/articles/app-12/20261007-123005-specify-login-spec.md", result["path"])
        text = (self.container / result["path"]).read_text(encoding="utf-8")
        self.assertIn('kind: "stage"', text)
        self.assertIn('ticket: "APP-12"', text)
        self.assertIn('stage: "SPECIFY"', text)
        self.assertIn("Users sign in.", text)
        self.assertIn("ingest | stage — Login spec", self.log())
        self.assertIn("[[raw/articles/app-12/20261007-123005-specify-login-spec]]", self.log())

    def test_raw_records_are_never_overwritten(self) -> None:
        first = self.record(kind="gate", title="unit tests", body="PASS", ticket="APP-12")
        second = self.record(kind="gate", title="unit tests", body="FAIL", ticket="APP-12")
        self.assertNotEqual(first["path"], second["path"])
        self.assertTrue(second["path"].endswith("-unit-tests-2.md"))
        self.assertIn("PASS", (self.container / first["path"]).read_text(encoding="utf-8"))
        self.assertIn("FAIL", (self.container / second["path"]).read_text(encoding="utf-8"))

    def test_record_without_ticket_goes_to_no_ticket(self) -> None:
        result = self.record(kind="action", title="run", body="done")
        self.assertTrue(result["path"].startswith("raw/articles/no-ticket/actions/"))

    def test_decision_creates_a_concept_page_indexes_it_and_appends_later_records(self) -> None:
        first = self.record(kind="decision", title="Use JWT refresh", body="Short-lived access tokens.", ticket="APP-12", stage="PLAN")
        self.assertEqual("concepts/use-jwt-refresh.md", first["path"])
        self.assertTrue(first["created"])
        index = (self.container / "index.md").read_text(encoding="utf-8")
        self.assertIn("[[concepts/use-jwt-refresh|use-jwt-refresh]] — Short-lived access tokens.", index)
        self.assertIn("Total pages: 1", index)
        second = self.record(kind="decision", title="Use JWT refresh", body="Rotation every 15 min.", ticket="APP-12", stage="REVIEW")
        self.assertFalse(second["created"])
        page = (self.container / first["path"]).read_text(encoding="utf-8")
        self.assertIn("type: \"decision\"", page)
        self.assertIn("Short-lived access tokens.", page)
        self.assertIn("Rotation every 15 min.", page)
        self.assertEqual(1, (self.container / "index.md").read_text(encoding="utf-8").count("[[concepts/use-jwt-refresh"))
        self.assertIn("create | decision — Use JWT refresh", self.log())
        self.assertIn("update | decision — Use JWT refresh", self.log())

    def test_layer_two_kinds_go_to_their_folders(self) -> None:
        for kind, folder in (("entity", "entities"), ("concept", "concepts"), ("comparison", "comparisons"), ("query", "queries")):
            with self.subTest(kind=kind):
                result = self.record(kind=kind, title=f"{kind} page", body="text")
                self.assertTrue(result["path"].startswith(folder + "/"))

    def test_turns_of_one_session_append_to_one_transcript(self) -> None:
        first = self.record(kind="turn", title="turn 1", body="hello", session="s-1")
        second = self.record(kind="turn", title="turn 2", body="world", session="s-1")
        self.assertEqual("raw/transcripts/sessions/2026-10-07-s-1.md", first["path"])
        self.assertEqual(first["path"], second["path"])
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        text = (self.container / first["path"]).read_text(encoding="utf-8")
        self.assertLess(text.index("hello"), text.index("world"))
        self.assertEqual(1, self.log().count("session s-1"))

    def test_secrets_are_redacted_before_writing(self) -> None:
        result = self.record(
            kind="stage", title="setup", stage="PLAN",
            body="key sk-abcdefghijklmnopqrstuvwxyz123456 and TYPESAFE_API_KEY=supersecretvalue1 and Bearer abcdefghijklmnopqrstuvwx",
        )
        text = (self.container / result["path"]).read_text(encoding="utf-8")
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz123456", text)
        self.assertNotIn("supersecretvalue1", text)
        self.assertNotIn("abcdefghijklmnopqrstuvwx", text)
        self.assertIn("[REDACTED]", text)

    def test_oversized_body_is_truncated(self) -> None:
        result = self.record(kind="action", title="big", body="x" * (wiki_journal.MAX_RECORD_BYTES + 10))
        text = (self.container / result["path"]).read_text(encoding="utf-8")
        self.assertIn("> Truncated", text)
        self.assertLess(len(text.encode("utf-8")), wiki_journal.MAX_RECORD_BYTES + 2000)

    def test_unknown_kind_and_empty_title_are_rejected(self) -> None:
        with self.assertRaises(wiki_journal.WikiJournalError) as raised:
            self.record(kind="note", title="x", body="y")
        self.assertEqual("WIKI_RECORD_KIND_INVALID", raised.exception.code)
        with self.assertRaises(wiki_journal.WikiJournalError) as raised:
            self.record(kind="stage", title="  ", body="y")
        self.assertEqual("WIKI_RECORD_INVALID", raised.exception.code)

    def test_symlinked_folder_never_redirects_a_write(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        (self.container / "raw" / "articles" / "app-9").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(wiki_journal.WikiJournalError):
            self.record(kind="stage", title="spec", body="x", ticket="APP-9", stage="SPECIFY")
        self.assertEqual([], list(outside.iterdir()))

    def test_symlinked_transcript_is_refused(self) -> None:
        outside = self.base / "outside.md"
        outside.write_text("keep\n", encoding="utf-8")
        folder = self.container / "raw" / "transcripts" / "sessions"
        folder.mkdir(parents=True)
        (folder / "2026-10-07-s-1.md").symlink_to(outside)
        with self.assertRaises(wiki_journal.WikiJournalError):
            self.record(kind="turn", title="t", body="x", session="s-1")
        self.assertEqual("keep\n", outside.read_text(encoding="utf-8"))

    def test_hard_linked_concept_page_is_refused(self) -> None:
        outside = self.base / "outside.md"
        outside.write_text("keep\n", encoding="utf-8")
        os.link(outside, self.container / "concepts" / "shared.md")
        with self.assertRaises(wiki_journal.WikiJournalError) as raised:
            self.record(kind="concept", title="shared", body="x")
        self.assertEqual("WIKI_PATH_UNSAFE", raised.exception.code)
        self.assertEqual("keep\n", outside.read_text(encoding="utf-8"))

    def test_uninitialized_container_gets_the_skeleton_before_the_first_record(self) -> None:
        fresh = self.vault / "Projects" / "Fresh"
        fresh.mkdir()
        container = wiki_journal.prepare_container(fresh)
        result = wiki_journal.record(container, kind="stage", title="spec", body="x", stage="SPECIFY", when=WHEN)
        self.assertTrue((fresh / "SCHEMA.md").is_file())
        self.assertTrue((fresh / result["path"]).is_file())

    def test_vault_root_is_never_a_container(self) -> None:
        with self.assertRaises(wiki_journal.WikiJournalError) as raised:
            wiki_journal.prepare_container(self.vault)
        self.assertEqual("WIKI_CONTAINER_IS_VAULT", raised.exception.code)


class WorkspaceTests(JournalTestCase):
    def test_record_for_workspace_resolves_the_repository_binding(self) -> None:
        result = wiki_journal.record_for_workspace(self.repo, kind="gate", title="lint", body="ok", when=WHEN)
        self.assertTrue((self.container / result["path"]).is_file())

    def test_unbound_workspace_is_refused(self) -> None:
        stray = self.base / "stray"
        stray.mkdir()
        with self.assertRaises(wiki_journal.WikiJournalError) as raised:
            wiki_journal.record_for_workspace(stray, kind="gate", title="lint", body="ok")
        self.assertEqual("WIKI_BINDING_MISSING", raised.exception.code)

    def test_installed_controller_only_writes_for_registered_workspaces(self) -> None:
        runtime = self.container / ".hermes-runtime" / "repo-1"
        runtime.mkdir(parents=True)
        (runtime / "STATE.md").write_text(f'workspace:\n  path: "{self.repo}"\n', encoding="utf-8")
        (self.container / ".hermes").mkdir(exist_ok=True)
        (self.container / ".hermes" / "obsidian.json").write_text(
            (self.repo / ".hermes" / "obsidian.json").read_text(encoding="utf-8"), encoding="utf-8")
        (self.repo / ".hermes" / "obsidian.json").unlink()
        stray = self.base / "stray"
        stray.mkdir()
        with mock.patch.object(wiki_journal, "_installed_container", return_value=self.container):
            result = wiki_journal.record_for_workspace(self.repo, kind="gate", title="lint", body="ok", when=WHEN)
            self.assertTrue((self.container / result["path"]).is_file())
            with self.assertRaises(wiki_journal.WikiJournalError) as raised:
                wiki_journal.record_for_workspace(stray, kind="gate", title="lint", body="ok")
        self.assertEqual("WIKI_WORKSPACE_NOT_REGISTERED", raised.exception.code)


class ActionJournalMirrorTests(JournalTestCase):
    def journal(self, status: str = "RELEASED") -> dict:
        artifact = self.base / "final-message.json"
        artifact.write_text('{"summary": "plan ready"}', encoding="utf-8")
        return {
            "journal_version": 1,
            "workspace": {"path": str(self.repo), "branch": "dev", "head": "a" * 40, "git_common_dir": str(self.repo / ".git")},
            "action": {
                "id": "APP-12-20261007T000000Z-01", "ticket": "APP-12", "stage": "PLAN", "name": "plan", "status": status,
                "executor": "CODEX", "final_message_path": str(artifact), "attempt": 1, "invalid_fields": [],
            },
            "process": {"started_at": "2026-10-07T00:00:00Z", "finished_at": "2026-10-07T00:01:00Z", "exit_code": 0},
            "artifact": {"exists": True, "sha256": "b" * 64, "validation_status": "VALID"},
        }

    def test_mirror_action_writes_the_action_and_the_executor_result(self) -> None:
        result = wiki_journal.mirror_action(self.journal(), outcome="RELEASED")
        self.assertEqual("WRITTEN", result["status"], result)
        self.assertTrue(result["path"].startswith("raw/articles/app-12/actions/"))
        text = (self.container / result["path"]).read_text(encoding="utf-8")
        self.assertIn("outcome: RELEASED", text)
        self.assertIn('{"summary": "plan ready"}', text)

    def test_mirror_action_never_raises(self) -> None:
        stray = self.journal()
        stray["workspace"]["path"] = str(self.base / "nowhere")
        self.assertEqual("SKIPPED", wiki_journal.mirror_action(stray, outcome="RELEASED")["status"])
        self.assertEqual("SKIPPED", wiki_journal.mirror_action({}, outcome="RELEASED")["status"])

    def test_record_incident_is_mirrored_into_the_wiki(self) -> None:
        runtime = self.base / "runtime"
        runtime.mkdir()
        (runtime / "ACTION_JOURNAL.json").write_text(json.dumps({"workspace": {"path": str(self.repo)}}), encoding="utf-8")
        identifier = action_journal.record_incident(
            runtime / "INCIDENTS.md", ticket="APP-12", stage="PLAN", action_id="a-1", incident_type="CONTRACT_INVALID",
            summary="invalid", artifact_path="/tmp/final.json", state_reference="STATE.md", recovery="BLOCKED",
            currently_blocking=True, timestamp="2026-10-07T00:00:00Z",
        )
        incidents = list((self.container / "raw" / "articles" / "app-12" / "incidents").iterdir())
        self.assertEqual(1, len(incidents))
        self.assertIn(identifier, incidents[0].read_text(encoding="utf-8"))

    def test_incident_still_recorded_locally_when_the_wiki_is_unavailable(self) -> None:
        runtime = self.base / "runtime"
        runtime.mkdir()
        identifier = action_journal.record_incident(
            runtime / "INCIDENTS.md", ticket="APP-12", stage="PLAN", action_id="a-1", incident_type="STATE_DESYNC",
            summary="desync", artifact_path="/tmp/final.json", state_reference="STATE.md", recovery="RECONCILE",
            currently_blocking=True, timestamp="2026-10-07T00:00:01Z",
        )
        self.assertIn(identifier, (runtime / "INCIDENTS.md").read_text(encoding="utf-8"))


class HookTests(JournalTestCase):
    def turn_payload(self, cwd: Path, **extra) -> dict:
        return {
            "hook_event_name": "post_llm_call", "session_id": "20261007_120000_abc", "cwd": str(cwd),
            "extra": {"user_message": "fix login", "assistant_response": "done", "turn_id": "t1", **extra},
        }

    def test_turn_inside_a_bound_repository_is_recorded(self) -> None:
        with mock.patch.object(wiki_journal, "_installed_container", return_value=None), \
                mock.patch.object(wiki_journal, "__file__", str(self.repo / ".hermes/orchestration/runtime/wiki_journal.py")):
            result = wiki_journal.record_turn_event(self.turn_payload(self.repo / "src"))
        self.assertEqual("WRITTEN", result["status"], result)
        text = (self.container / result["path"]).read_text(encoding="utf-8")
        self.assertIn("fix login", text)
        self.assertIn("done", text)

    def test_turn_in_an_unrelated_directory_is_skipped(self) -> None:
        stray = self.base / "stray"
        stray.mkdir()
        with mock.patch.object(wiki_journal, "__file__", str(self.repo / ".hermes/orchestration/runtime/wiki_journal.py")):
            result = wiki_journal.record_turn_event(self.turn_payload(stray))
        self.assertEqual("SKIPPED", result["status"])
        self.assertFalse((self.container / "raw" / "transcripts" / "sessions").exists())

    def test_installed_controller_records_turns_from_registered_workspace_and_parent(self) -> None:
        runtime = self.container / ".hermes-runtime" / "repo-1"
        runtime.mkdir(parents=True)
        (runtime / "STATE.md").write_text(f'workspace:\n  path: "{self.repo}"\n', encoding="utf-8")
        (self.container / ".hermes").mkdir(exist_ok=True)
        (self.container / ".hermes" / "obsidian.json").write_text(
            (self.repo / ".hermes" / "obsidian.json").read_text(encoding="utf-8"), encoding="utf-8")
        with mock.patch.object(wiki_journal, "_installed_container", return_value=self.container):
            inside = wiki_journal.record_turn_event(self.turn_payload(self.repo / "src"))
            parent = wiki_journal.record_turn_event(self.turn_payload(self.base))
            unrelated = wiki_journal.record_turn_event(self.turn_payload(self.vault))
        self.assertEqual("WRITTEN", inside["status"], inside)
        self.assertEqual("WRITTEN", parent["status"], parent)
        self.assertEqual("SKIPPED", unrelated["status"])

    def test_empty_turn_is_skipped(self) -> None:
        with mock.patch.object(wiki_journal, "__file__", str(self.repo / ".hermes/orchestration/runtime/wiki_journal.py")):
            result = wiki_journal.record_turn_event(self.turn_payload(self.repo, user_message="", assistant_response=" "))
        self.assertEqual("SKIPPED", result["status"])

    def test_session_end_is_logged(self) -> None:
        with mock.patch.object(wiki_journal, "__file__", str(self.repo / ".hermes/orchestration/runtime/wiki_journal.py")):
            result = wiki_journal.record_session_end_event({
                "hook_event_name": "on_session_end", "session_id": "s-9", "cwd": str(self.repo),
                "extra": {"completed": True, "interrupted": False, "turn_exit_reason": "done"},
            })
        self.assertEqual("WRITTEN", result["status"])
        self.assertIn("session s-9 ended", self.log())

    def test_hook_main_never_fails_and_prints_an_empty_object(self) -> None:
        def boom(_payload):
            raise RuntimeError("wiki down")

        output = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO("{\"cwd\": \"/\"}")), redirect_stdout(output):
            self.assertEqual(0, wiki_journal.hook_main(boom))
        self.assertEqual("{}", output.getvalue().strip())
        output = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO("not json")), redirect_stdout(output):
            self.assertEqual(0, wiki_journal.hook_main(boom))

    def test_hook_scripts_run_and_never_block(self) -> None:
        hooks = Path(__file__).resolve().parents[1] / "hooks"
        for name in ("record-turn.py", "record-session-end.py"):
            with self.subTest(name=name):
                completed = subprocess.run(
                    [sys.executable, "-B", str(hooks / name)], input=json.dumps({"cwd": str(self.base)}),
                    capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(0, completed.returncode, completed.stderr)
                self.assertEqual("{}", completed.stdout.strip())


class CliTests(JournalTestCase):
    def run_cli(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), *args], input=stdin, capture_output=True, text=True, timeout=60,
        )

    def test_cli_records_from_the_repository_binding_and_stdin(self) -> None:
        completed = self.run_cli(
            "record", "--repo", str(self.repo), "--kind", "stage", "--stage", "PLAN", "--ticket", "APP-12",
            "--title", "Plan", "--body-file", "-", "--json", stdin="the plan\n",
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual("WRITTEN", report["status"])
        self.assertIn("the plan", (self.container / report["path"]).read_text(encoding="utf-8"))

    def test_cli_blocks_an_unbound_repository(self) -> None:
        stray = self.base / "stray"
        stray.mkdir()
        completed = self.run_cli("record", "--repo", str(stray), "--kind", "gate", "--title", "x", "--body", "y", "--json")
        self.assertEqual(2, completed.returncode)
        # A source checkout has no installed binding; a vault-resident copy refuses the unregistered worktree.
        self.assertIn(json.loads(completed.stdout)["reason"], {"WIKI_BINDING_MISSING", "WIKI_WORKSPACE_NOT_REGISTERED"})
        self.assertFalse((self.container / "raw" / "articles" / "no-ticket").exists())


class PolicyTests(unittest.TestCase):
    def test_policies_describe_the_wiki_as_read_and_write(self) -> None:
        orchestration = Path(__file__).resolve().parents[1]
        loop = (orchestration / "policies" / "LOOP_POLICY.md").read_text(encoding="utf-8")
        self.assertIn("Obsidian é leitura **e escrita**", loop)
        self.assertNotIn("OBSIDIAN WRITE PROPOSAL", loop)
        import bounded_run_planner

        self.assertIn("OBSIDIAN_WRITE", bounded_run_planner.AUTO_SAFE)
        self.assertNotIn("OBSIDIAN_WRITE", bounded_run_planner.HUMAN_REQUIRED)
        self.assertNotIn("OBSIDIAN_WRITE", bounded_run_planner.EXTERNAL_MUTATION_ACTIONS)


if __name__ == "__main__":
    unittest.main()
