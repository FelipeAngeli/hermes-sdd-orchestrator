"""Isolated TDD coverage for action recovery journal behavior."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))

import action_journal as journal

SCRIPT = RUNTIME / "action_journal.py"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def payload(root: Path, *, status: str = "PREPARED") -> dict:
    artifact = root / "final-message.json"
    return {
        "journal_version": 1,
        "workspace": {"path": str(root), "branch": "dev", "head": "a" * 40, "git_common_dir": str(root / ".git")},
        "action": {
            "id": "APP-439-20260916T000000Z-01", "ticket": "APP-439", "stage": "PLAN", "name": "plan", "status": status,
            "executor": "CODEX", "schema_path": ".hermes/orchestration/schemas/EXECUTOR_RESULT_SCHEMA.json", "protocol_version": 2,
            "prompt_hash": sha256_text("prompt"), "final_message_path": str(artifact), "attempt": 1,
            "retry_mode": "FULL_REPLACEMENT", "parent_action_id": None, "parent_artifact_path": None,
            "parent_artifact_sha256": None, "invalid_fields": [], "allowed_corrections": [],
        },
        "fingerprints": {"baseline": sha256_text("baseline"), "ownership": sha256_text("ownership"), "state_before": sha256_text("state")},
        "process": {"started_at": "2026-09-16T00:00:00Z", "finished_at": None, "exit_code": None},
        "artifact": {"exists": False, "sha256": None, "validation_status": "PENDING"},
        "state_commit": {"state_path": None, "expected_before_hash": sha256_text("state"), "expected_after_hash": None, "committed_after_hash": None, "committed_at": None, "verified": False},
        "incidents": [],
    }


class ActionJournalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="action journal with spaces-")
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name) / "workspace with spaces"
        self.root.mkdir()
        self.path = self.root / "ACTION_JOURNAL.json"

    def persist(self, value: dict) -> None:
        journal.atomic_write(self.path, value)

    def invalid_artifact(self, *, attempt: int = 1) -> tuple[dict, Path]:
        value = payload(self.root, status="ARTIFACT_READY")
        artifact = Path(value["action"]["final_message_path"])
        artifact.write_text("invalid result", encoding="utf-8")
        value["action"]["attempt"] = attempt
        value["artifact"].update({
            "exists": True,
            "sha256": journal.sha256(artifact),
            "validation_status": "INVALID",
        })
        return value, artifact

    def test_invalid_artifact_offers_corrective_retry_before_generic_reconciliation(self) -> None:
        value, _ = self.invalid_artifact()

        self.assertEqual("CORRECTIVE_RETRY_AVAILABLE", journal.recovery_decision(value)["decision"])

        value["action"]["attempt"] = 2
        decision = journal.recovery_decision(value)
        self.assertEqual("BLOCKED", decision["decision"])
        self.assertEqual("RETRY_BUDGET_REACHED", decision["stop_reason"])

    def test_archive_invalid_blocks_parent_preserves_artifact_and_allows_parented_retry(self) -> None:
        value, artifact = self.invalid_artifact()
        self.persist(value)
        history = self.root / "action-journal-history"

        result = journal.archive_invalid_journal(self.path, history)

        archived = json.loads(Path(result["archived_path"]).read_text(encoding="utf-8"))
        self.assertEqual("BLOCKED", archived["action"]["status"])
        self.assertEqual(value["action"]["id"], result["parent_action_id"])
        self.assertEqual(str(artifact), result["parent_artifact_path"])
        self.assertEqual(journal.sha256(artifact), result["parent_artifact_sha256"])
        self.assertEqual("DISPATCH_ALLOWED", result["recovery_after_archive"])
        self.assertEqual("invalid result", artifact.read_text(encoding="utf-8"))
        self.assertTrue(journal.is_pristine_idle(journal.load_journal(self.path)))

        retry = payload(self.root, status="PREPARED")
        retry_artifact = self.root / "retry-final-message.json"
        retry["action"].update({
            "id": "APP-439-20260916T000001Z-02", "attempt": 2,
            "final_message_path": str(retry_artifact), "parent_action_id": result["parent_action_id"],
            "parent_artifact_path": result["parent_artifact_path"],
            "parent_artifact_sha256": result["parent_artifact_sha256"],
        })
        retry["process"] = {"started_at": None, "finished_at": None, "exit_code": None}
        journal.prepare_action(self.path, retry)
        self.assertEqual("DISPATCH_ALLOWED", journal.recovery_decision(journal.load_journal(self.path))["decision"])
        self.assertNotEqual(str(artifact), str(retry_artifact))

        with self.assertRaises(journal.JournalError) as repeat:
            journal.archive_invalid_journal(self.path, history)
        self.assertEqual("JOURNAL_ARCHIVE_INVALID_NOT_ALLOWED", repeat.exception.code)

    def test_archive_invalid_should_preserve_terminal_attempt_history_and_reset_pristine_idle(self) -> None:
        value, artifact = self.invalid_artifact(attempt=2)
        self.persist(value)

        result = journal.archive_invalid_journal(self.path, self.root / "action-journal-history")

        archived = json.loads(Path(result["archived_path"]).read_text(encoding="utf-8"))
        self.assertEqual("BLOCKED", archived["action"]["status"])
        self.assertEqual(2, archived["action"]["attempt"])
        self.assertEqual("invalid result", artifact.read_text(encoding="utf-8"))
        self.assertTrue(journal.is_pristine_idle(journal.load_journal(self.path)))
        self.assertEqual("DISPATCH_ALLOWED", result["recovery_after_archive"])

    def test_invalid_classification_cli_should_classify_initial_and_recover_legacy_blocked_artifacts(self) -> None:
        value = payload(self.root, status="ARTIFACT_READY")
        artifact = Path(value["action"]["final_message_path"])
        artifact.write_text("invalid artifact", encoding="utf-8")
        value["artifact"].update(exists=True, sha256=journal.sha256(artifact))
        self.persist(value)

        classified = subprocess.run(
            [sys.executable, str(SCRIPT), "--journal", str(self.path), "--json", "--invalid-field", "blockers", "classify-invalid"],
            text=True, capture_output=True, check=False, timeout=20,
        )

        self.assertEqual(0, classified.returncode, classified.stderr)
        self.assertEqual("ARTIFACT_READY", json.loads(classified.stdout)["action"]["status"])
        self.assertEqual("INVALID", journal.load_journal(self.path)["artifact"]["validation_status"])

        legacy = journal.load_journal(self.path)
        legacy["action"]["status"] = "BLOCKED"
        legacy["artifact"]["validation_status"] = "PENDING"
        legacy["action"]["invalid_fields"] = []
        legacy["process"] = {"started_at": "2026-09-16T00:00:00Z", "finished_at": "2026-09-16T00:00:01Z", "exit_code": 0}
        self.persist(legacy)
        recovered = subprocess.run(
            [sys.executable, str(SCRIPT), "--journal", str(self.path), "--json", "--invalid-field", "semantic_contract", "recover-blocked-invalid"],
            text=True, capture_output=True, check=False, timeout=20,
        )

        self.assertEqual(0, recovered.returncode, recovered.stderr)
        repaired = json.loads(recovered.stdout)
        self.assertEqual("ARTIFACT_READY", repaired["action"]["status"])
        self.assertEqual("INVALID", repaired["artifact"]["validation_status"])
        self.assertEqual(["semantic_contract"], repaired["action"]["invalid_fields"])

    def test_invalid_classification_should_reject_drift_commit_empty_fields_wrong_status_and_unknown_process(self) -> None:
        value = payload(self.root, status="ARTIFACT_READY")
        artifact = Path(value["action"]["final_message_path"])
        artifact.write_text("original", encoding="utf-8")
        value["artifact"].update(exists=True, sha256=journal.sha256(artifact))
        self.persist(value)
        artifact.write_text("drifted", encoding="utf-8")

        with self.assertRaises(journal.JournalError) as drift:
            journal.classify_invalid_artifact(self.path, ["blockers"])
        self.assertEqual("INVALID_ARTIFACT_CLASSIFICATION_NOT_ALLOWED", drift.exception.code)

        value["artifact"]["sha256"] = journal.sha256(artifact)
        value["state_commit"]["verified"] = True
        self.persist(value)
        with self.assertRaises(journal.JournalError) as committed:
            journal.classify_invalid_artifact(self.path, ["blockers"])
        self.assertEqual("INVALID_ARTIFACT_CLASSIFICATION_NOT_ALLOWED", committed.exception.code)

        value["state_commit"]["verified"] = False
        self.persist(value)
        with self.assertRaises(journal.JournalError) as fields:
            journal.classify_invalid_artifact(self.path, [])
        self.assertEqual("INVALID_ARTIFACT_CLASSIFICATION_NOT_ALLOWED", fields.exception.code)

        value["action"]["status"] = "PROCESS_FINISHED"
        self.persist(value)
        with self.assertRaises(journal.JournalError) as status:
            journal.classify_invalid_artifact(self.path, ["blockers"])
        self.assertEqual("INVALID_ARTIFACT_CLASSIFICATION_NOT_ALLOWED", status.exception.code)

        value["action"]["status"] = "BLOCKED"
        value["process"] = {"started_at": "2026-09-16T00:00:00Z", "finished_at": None, "exit_code": None}
        self.persist(value)
        with self.assertRaises(journal.JournalError) as unknown_process:
            journal.recover_blocked_pending_artifact(self.path, ["blockers"])
        self.assertEqual("INVALID_ARTIFACT_CLASSIFICATION_NOT_ALLOWED", unknown_process.exception.code)

    def test_archive_invalid_rejects_incompatible_evidence_and_history_conflict_without_wiping_active_journal(self) -> None:
        value, artifact = self.invalid_artifact()
        history = self.root / "action-journal-history"
        value["state_commit"]["verified"] = True
        self.persist(value)
        with self.assertRaises(journal.JournalError) as invalid_commit:
            journal.archive_invalid_journal(self.path, history)
        self.assertEqual("JOURNAL_ARCHIVE_INVALID_NOT_ALLOWED", invalid_commit.exception.code)
        self.assertEqual(value, journal.load_journal(self.path))

        value["state_commit"]["verified"] = False
        self.persist(value)
        archived_path = history / value["action"]["ticket"] / f"{value['action']['id']}.json"
        archived_path.parent.mkdir(parents=True, exist_ok=True)
        archived_path.write_text('{"conflict":true}\n', encoding="utf-8")
        with self.assertRaises(journal.JournalError) as conflict:
            journal.archive_invalid_journal(self.path, history)
        self.assertEqual("JOURNAL_HISTORY_CONFLICT", conflict.exception.code)
        self.assertEqual(value, journal.load_journal(self.path))
        self.assertTrue(artifact.is_file())

    def test_archive_invalid_cli_returns_parent_references(self) -> None:
        value, _ = self.invalid_artifact()
        self.persist(value)
        history = self.root / "action-journal-history"

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--journal", str(self.path), "--history-dir", str(history), "--json", "archive-invalid"],
            text=True, capture_output=True, check=False, timeout=20,
        )

        response = json.loads(result.stdout)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(value["action"]["id"], response["parent_action_id"])
        self.assertEqual(value["action"]["final_message_path"], response["parent_artifact_path"])
        self.assertEqual(value["artifact"]["sha256"], response["parent_artifact_sha256"])
        self.assertEqual("DISPATCH_ALLOWED", response["recovery_after_archive"])

    def test_pristine_idle_journal_allows_first_dispatch(self) -> None:
        value = journal.empty_journal({"path": str(self.root), "branch": "dev", "head": "a" * 40, "git_common_dir": str(self.root / ".git")})
        self.assertIsNone(value["action"]["id"])
        self.assertEqual("IDLE", value["action"]["status"])
        self.assertIsNone(value["action"]["ticket"])
        self.assertIsNone(value["action"]["stage"])
        self.assertFalse(value["artifact"]["exists"])
        self.assertIsNone(value["artifact"]["sha256"])
        self.assertEqual("PENDING", value["artifact"]["validation_status"])
        self.assertEqual({"started_at": None, "finished_at": None, "exit_code": None}, value["process"])
        self.assertEqual({"state_path": None, "expected_before_hash": None, "expected_after_hash": None, "committed_after_hash": None, "committed_at": None, "verified": False}, value["state_commit"])
        self.assertEqual([], value["incidents"])
        self.assertEqual("DISPATCH_ALLOWED", journal.recovery_decision(value)["decision"])

    def test_dirty_idle_evidence_never_allows_dispatch(self) -> None:
        pristine = journal.empty_journal({"path": str(self.root), "branch": "dev", "head": "a" * 40, "git_common_dir": str(self.root / ".git")})
        mutations = {
            "action_id": lambda value: value["action"].update(id="prior-action"),
            "artifact": lambda value: value["artifact"].update(exists=True),
            "final_message_path": lambda value: value["action"].update(final_message_path="/tmp/prior-message.json"),
            "process_started": lambda value: value["process"].update(started_at="2026-09-16T00:00:00Z"),
            "process_finished": lambda value: value["process"].update(finished_at="2026-09-16T00:00:01Z"),
            "process_exit_code": lambda value: value["process"].update(exit_code=0),
            "expected_before_hash": lambda value: value["state_commit"].update(expected_before_hash=sha256_text("before")),
            "expected_after_hash": lambda value: value["state_commit"].update(expected_after_hash=sha256_text("after")),
            "verified_commit": lambda value: value["state_commit"].update(verified=True),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                value = json.loads(json.dumps(pristine))
                mutate(value)
                decision = journal.recovery_decision(value)["decision"]
                self.assertIn(decision, {"BLOCKED", "WAIT_OR_MANUAL_REVIEW"})
                self.assertNotEqual("DISPATCH_ALLOWED", decision)

    def test_temporary_journal_cannot_silently_return_to_pristine_idle(self) -> None:
        workspace = {"path": str(self.root), "branch": "dev", "head": "a" * 40, "git_common_dir": str(self.root / ".git")}
        pristine = journal.empty_journal(workspace)
        self.assertEqual("DISPATCH_ALLOWED", journal.recovery_decision(pristine)["decision"])
        prepared = payload(self.root, status="PREPARED")
        prepared["process"] = {"started_at": None, "finished_at": None, "exit_code": None}
        self.persist(pristine)
        journal.prepare_action(self.path, prepared)
        self.assertEqual("DISPATCH_ALLOWED", journal.recovery_decision(journal.load_journal(self.path))["decision"])
        dirty_idle = journal.load_journal(self.path)
        dirty_idle["action"]["status"] = "IDLE"
        self.assertEqual("BLOCKED", journal.recovery_decision(dirty_idle)["decision"])

    def test_unclassified_artifact_requires_reconciliation_not_dispatch(self) -> None:
        value = payload(self.root, status="DISPATCHED")
        artifact = Path(value["action"]["final_message_path"])
        artifact.write_text('{"executor_result": {}}', encoding="utf-8")
        self.persist(value)
        decision = journal.recovery_decision(journal.load_journal(self.path))
        self.assertEqual("RECONCILE_ARTIFACT", decision["decision"])
        self.assertFalse(journal.dispatch_allowed(journal.load_journal(self.path)))

    def test_dispatched_without_artifact_and_unknown_process_is_not_dispatchable(self) -> None:
        value = payload(self.root, status="DISPATCHED")
        self.persist(value)
        decision = journal.recovery_decision(journal.load_journal(self.path))
        self.assertEqual("WAIT_OR_MANUAL_REVIEW", decision["decision"])

    def test_validated_uncommitted_artifact_reconciles_without_redispatch(self) -> None:
        value = payload(self.root, status="VALIDATED")
        value["artifact"].update(exists=True, sha256=sha256_text("artifact"), validation_status="VALID")
        self.persist(value)
        self.assertEqual("RECONCILE_ARTIFACT", journal.recovery_decision(journal.load_journal(self.path))["decision"])
        self.assertFalse(journal.dispatch_allowed(journal.load_journal(self.path)))

    def test_pending_artifact_blocks_second_dispatch(self) -> None:
        value = payload(self.root, status="PROCESS_FINISHED")
        artifact = Path(value["action"]["final_message_path"])
        artifact.write_text("result", encoding="utf-8")
        self.persist(value)
        with self.assertRaises(journal.JournalError) as error:
            journal.require_dispatch_allowed(journal.load_journal(self.path))
        self.assertEqual("ARTIFACT_PENDING", error.exception.code)

    def test_atomic_write_failure_preserves_previous_valid_journal(self) -> None:
        original = payload(self.root)
        self.persist(original)
        changed = payload(self.root, status="DISPATCHED")
        with mock.patch("action_journal.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaises(journal.JournalError):
                journal.atomic_write(self.path, changed)
        self.assertEqual(original, journal.load_journal(self.path))

    def test_metadata_overlay_preserves_parent_evidence(self) -> None:
        parent = {"tdd_slices": [{"id": "slice-1"}], "modified_paths": ["src/a.ext"], "created_paths": ["tests/a_test.ext"], "stage_payload": {"summary": "valid"}, "evidence_functional": True}
        retry = journal.metadata_overlay(parent, {"stage_payload": {"summary": "corrected"}}, ["stage_payload"], baseline="b", ownership="o", parent_baseline="b", parent_ownership="o", product_changed=False)
        self.assertEqual(parent["tdd_slices"], retry["tdd_slices"])
        self.assertEqual(parent["modified_paths"], retry["modified_paths"])
        self.assertEqual(parent["created_paths"], retry["created_paths"])
        self.assertEqual({"summary": "corrected"}, retry["stage_payload"])

    def test_overlay_rejects_parent_evidence_mismatch(self) -> None:
        parent = {"tdd_slices": [], "modified_paths": [], "created_paths": [], "stage_payload": {}, "evidence_functional": True}
        for values in (("different", "o", False), ("b", "different", False), ("b", "o", True)):
            with self.assertRaises(journal.JournalError) as error:
                journal.metadata_overlay(parent, {}, [], baseline=values[0], ownership=values[1], parent_baseline="b", parent_ownership="o", product_changed=values[2])
            self.assertEqual("PARENT_EVIDENCE_MISMATCH", error.exception.code)

    def test_only_one_corrective_retry_is_permitted(self) -> None:
        self.assertTrue(journal.corrective_retry_allowed(0, 1))
        self.assertFalse(journal.corrective_retry_allowed(1, 1))

    def test_contract_invalid_and_state_desync_create_durable_incidents(self) -> None:
        incidents = self.root / "INCIDENTS.md"
        first = journal.record_incident(incidents, ticket="APP-439", stage="PLAN", action_id="action-1", incident_type="CONTRACT_INVALID", summary="invalid", artifact_path="/tmp/final.json", state_reference="STATE.md", recovery="BLOCKED", currently_blocking=True, timestamp="2026-09-16T00:00:00Z")
        second = journal.record_incident(incidents, ticket="APP-439", stage="PLAN", action_id="action-1", incident_type="STATE_DESYNC", summary="desync", artifact_path="/tmp/final.json", state_reference="STATE.md", recovery="RECONCILE", currently_blocking=True, timestamp="2026-09-16T00:00:01Z")
        text = incidents.read_text(encoding="utf-8")
        self.assertIn(first, text)
        self.assertIn(second, text)
        self.assertTrue(first.startswith("INC-20260916-000000-CONTRACT_INVALID"))
        self.assertTrue(second.startswith("INC-20260916-000001-STATE_DESYNC"))

    def test_prepare_rejects_existing_unclassified_artifact(self) -> None:
        value = payload(self.root, status="ARTIFACT_READY")
        Path(value["action"]["final_message_path"]).write_text("artifact", encoding="utf-8")
        self.persist(value)
        with self.assertRaises(journal.JournalError) as error:
            journal.prepare_action(self.path, payload(self.root))
        self.assertEqual("ARTIFACT_PENDING", error.exception.code)

    def test_prepare_rejects_replacement_of_prepared_action(self) -> None:
        existing = payload(self.root, status="PREPARED")
        existing["process"] = {"started_at": None, "finished_at": None, "exit_code": None}
        self.persist(existing)
        replacement = payload(self.root, status="PREPARED")
        replacement["action"]["id"] = "APP-439-20260916T000001Z-02"

        with self.assertRaises(journal.JournalError) as error:
            journal.prepare_action(self.path, replacement)

        self.assertEqual("ACTION_RECOVERY_REQUIRED", error.exception.code)
        self.assertEqual(existing["action"]["id"], journal.load_journal(self.path)["action"]["id"])

    def validated_state_commit(self, before: str = "state A", after: str = "state B") -> tuple[Path, str, str]:
        state = self.root / "STATE.md"
        state.write_text(before, encoding="utf-8")
        value = payload(self.root, status="VALIDATED")
        value["artifact"].update(exists=True, sha256=sha256_text("artifact"), validation_status="VALID")
        self.persist(value)
        before_hash, after_hash = sha256_text(before), sha256_text(after)
        journal.prepare_state_commit(self.path, state, before_hash, after_hash)
        return state, before_hash, after_hash

    def released_payload(self, action_id: str = "APP-439-20260916T000000Z-01") -> tuple[dict, Path]:
        value = payload(self.root, status="RELEASED")
        artifact = self.root / f"{action_id}-final-message.json"
        artifact.write_text(f"artifact for {action_id}", encoding="utf-8")
        value["action"].update(id=action_id, final_message_path=str(artifact))
        value["artifact"].update(exists=True, sha256=journal.sha256(artifact), validation_status="VALID")
        value["state_commit"].update(
            state_path=str(self.root / "STATE.md"),
            expected_before_hash=sha256_text("before"),
            expected_after_hash=sha256_text("after"),
            committed_after_hash=sha256_text("after"),
            committed_at="2026-09-16T00:00:02Z",
            verified=True,
        )
        return value, artifact

    def assert_unsafe_history_is_rejected(self, history: Path) -> None:
        operations = (
            ("rollover", lambda: self.released_payload()[0], journal.rollover_journal),
            ("archive-interrupted", self.process_finished_without_artifact, journal.archive_interrupted_journal),
            ("archive-invalid", lambda: self.invalid_artifact()[0], journal.archive_invalid_journal),
        )
        for name, create_value, archive in operations:
            with self.subTest(operation=name):
                value = create_value()
                self.persist(value)

                with self.assertRaises(journal.JournalError) as error:
                    archive(self.path, history)

                self.assertEqual("HISTORY_PATH_UNSAFE", error.exception.code)
                self.assertEqual(value, journal.load_journal(self.path))

    def test_archive_operations_reject_outside_absolute_history_without_creating_it(self) -> None:
        outside_history = Path(self.tempdir.name) / "outside-history"

        self.assert_unsafe_history_is_rejected(outside_history)

        self.assertFalse(outside_history.exists())

    def test_archive_operations_reject_history_symlink_escape_without_writing_outside(self) -> None:
        outside_history = Path(self.tempdir.name) / "outside-history"
        outside_history.mkdir()
        history_link = self.root / "history-link"
        history_link.symlink_to(outside_history, target_is_directory=True)

        self.assert_unsafe_history_is_rejected(history_link)

        self.assertEqual([], list(outside_history.iterdir()))

    def test_rollover_rejects_ticket_directory_swapped_to_outside_symlink_before_final_write(self) -> None:
        value, _ = self.released_payload()
        self.persist(value)
        history = self.root / "action-journal-history"
        outside = Path(self.tempdir.name) / "outside-history"
        outside.mkdir()

        def swap_ticket_directory(ticket_path: Path) -> None:
            moved = ticket_path.with_name(f"{ticket_path.name}-moved")
            ticket_path.rename(moved)
            ticket_path.symlink_to(outside, target_is_directory=True)

        previous_hook = journal._history_before_final_write_hook
        journal._history_before_final_write_hook = swap_ticket_directory
        self.addCleanup(setattr, journal, "_history_before_final_write_hook", previous_hook)

        with self.assertRaises(journal.JournalError) as error:
            journal.rollover_journal(self.path, history)

        self.assertEqual("HISTORY_PATH_UNSAFE", error.exception.code)
        self.assertEqual(value, journal.load_journal(self.path))
        self.assertEqual([], list(outside.iterdir()))

    def test_released_rollover_archives_action_and_creates_pristine_journal(self) -> None:
        value, artifact = self.released_payload()
        self.persist(value)
        history = self.root / "history with spaces"

        result = journal.rollover_journal(self.path, history)

        archived = Path(result["archived_path"])
        self.assertEqual("ROLLED_OVER", result["decision"])
        self.assertEqual(journal.sha256(archived), result["archived_sha256"])
        self.assertEqual("IDLE", result["new_journal_status"])
        self.assertEqual("DISPATCH_ALLOWED", result["recovery_after_rollover"])
        archived_value = json.loads(archived.read_text(encoding="utf-8"))
        self.assertEqual(value["action"]["id"], archived_value["action"]["id"])
        self.assertEqual(value["action"]["ticket"], archived_value["action"]["ticket"])
        self.assertEqual(value["action"]["stage"], archived_value["action"]["stage"])
        self.assertEqual(value["action"]["executor"], archived_value["action"]["executor"])
        self.assertEqual(value["artifact"]["sha256"], archived_value["artifact"]["sha256"])
        self.assertEqual(value["state_commit"]["committed_after_hash"], archived_value["state_commit"]["committed_after_hash"])
        self.assertTrue(archived_value["state_commit"]["verified"])
        self.assertEqual("RELEASED", archived_value["action"]["status"])
        self.assertEqual(f"artifact for {value['action']['id']}", artifact.read_text(encoding="utf-8"))
        self.assertTrue(journal.is_pristine_idle(journal.load_journal(self.path)))

    def test_rollover_allows_next_action_and_rejects_repeat_on_pristine_journal(self) -> None:
        value, _ = self.released_payload()
        self.persist(value)
        history = self.root / "action-journal-history"
        journal.rollover_journal(self.path, history)

        next_action = payload(self.root, status="PREPARED")
        next_action["action"].update(id="APP-439-20260916T000001Z-02", ticket="APP-439")
        journal.prepare_action(self.path, next_action)
        self.assertEqual("PREPARED", journal.load_journal(self.path)["action"]["status"])

        self.persist(journal.empty_journal(value["workspace"]))
        with self.assertRaises(journal.JournalError) as error:
            journal.rollover_journal(self.path, history)
        self.assertEqual("JOURNAL_ROLLOVER_NOT_ALLOWED", error.exception.code)
        self.assertEqual(1, len(list(history.rglob("*.json"))))

    def test_rollover_history_conflict_preserves_released_active_journal(self) -> None:
        value, _ = self.released_payload()
        self.persist(value)
        history = self.root / "action-journal-history"
        conflict = history / value["action"]["ticket"] / f"{value['action']['id']}.json"
        conflict.parent.mkdir(parents=True)
        conflict.write_text('{"conflict":true}\n', encoding="utf-8")

        with self.assertRaises(journal.JournalError) as error:
            journal.rollover_journal(self.path, history)

        self.assertEqual("JOURNAL_HISTORY_CONFLICT", error.exception.code)
        self.assertEqual(value, journal.load_journal(self.path))
        self.assertEqual('{"conflict":true}\n', conflict.read_text(encoding="utf-8"))

    def test_rollover_rejects_non_released_statuses_and_unverified_release(self) -> None:
        history = self.root / "action-journal-history"
        for status in ("PREPARED", "DISPATCHED", "PROCESS_FINISHED", "ARTIFACT_READY", "VALIDATED", "STATE_COMMITTED", "BLOCKED", "INTERRUPTED", "IDLE"):
            with self.subTest(status=status):
                value = payload(self.root, status=status) if status != "IDLE" else journal.empty_journal({"path": str(self.root), "branch": "dev", "head": "a" * 40, "git_common_dir": str(self.root / ".git")})
                self.persist(value)
                with self.assertRaises(journal.JournalError) as error:
                    journal.rollover_journal(self.path, history)
                self.assertEqual("JOURNAL_ROLLOVER_NOT_ALLOWED", error.exception.code)
        value, _ = self.released_payload()
        value["state_commit"]["verified"] = False
        self.persist(value)
        with self.assertRaises(journal.JournalError) as error:
            journal.rollover_journal(self.path, history)
        self.assertEqual("JOURNAL_ROLLOVER_UNVERIFIED", error.exception.code)

    def test_two_released_actions_rollover_then_third_action_can_be_prepared(self) -> None:
        history = self.root / "action-journal-history"
        first, first_artifact = self.released_payload("APP-439-20260916T000000Z-01")
        self.persist(first)
        journal.rollover_journal(self.path, history)
        second, second_artifact = self.released_payload("APP-439-20260916T000001Z-02")
        self.persist(second)
        journal.rollover_journal(self.path, history)
        third = payload(self.root, status="PREPARED")
        third["action"].update(id="APP-439-20260916T000002Z-03", ticket="APP-439")
        journal.prepare_action(self.path, third)

        archived = sorted(history.rglob("*.json"))
        self.assertEqual(2, len(archived))
        self.assertEqual({first["action"]["id"], second["action"]["id"]}, {json.loads(path.read_text(encoding="utf-8"))["action"]["id"] for path in archived})
        self.assertEqual("PREPARED", journal.load_journal(self.path)["action"]["status"])
        self.assertEqual(f"artifact for {first['action']['id']}", first_artifact.read_text(encoding="utf-8"))
        self.assertEqual(f"artifact for {second['action']['id']}", second_artifact.read_text(encoding="utf-8"))

    def test_state_commit_rereads_real_hash_before_verified_transition(self) -> None:
        state, _, expected_after = self.validated_state_commit()
        state.write_text("state B", encoding="utf-8")
        committed = journal.mark_state_committed(self.path)
        self.assertEqual("STATE_COMMITTED", committed["action"]["status"])
        self.assertTrue(committed["state_commit"]["verified"])
        self.assertEqual(expected_after, committed["state_commit"]["committed_after_hash"])
        self.assertEqual(sha256_text("state B"), committed["state_commit"]["committed_after_hash"])
        self.assertIsNotNone(committed["state_commit"]["committed_at"])

    def test_wrong_caller_hash_cannot_prove_state_commit(self) -> None:
        state, _, _ = self.validated_state_commit()
        state.write_text("state B", encoding="utf-8")
        with self.assertRaises(journal.JournalError) as error:
            journal.mark_state_committed(self.path, caller_expected_after_hash=sha256_text("wrong"))
        self.assertEqual("STATE_COMMIT_HASH_MISMATCH", error.exception.code)
        persisted = journal.load_journal(self.path)
        self.assertEqual("VALIDATED", persisted["action"]["status"])
        self.assertFalse(persisted["state_commit"]["verified"])
        self.assertFalse(journal.dispatch_allowed(persisted))

    def test_missing_state_leaves_validated_journal_unchanged(self) -> None:
        state, _, _ = self.validated_state_commit()
        state.unlink()
        with self.assertRaises(journal.JournalError) as error:
            journal.mark_state_committed(self.path)
        self.assertEqual("STATE_COMMIT_FILE_MISSING", error.exception.code)
        persisted = journal.load_journal(self.path)
        self.assertEqual("VALIDATED", persisted["action"]["status"])
        self.assertFalse(persisted["state_commit"]["verified"])

    def test_unchanged_state_is_not_committed_or_redispatched(self) -> None:
        _, before_hash, _ = self.validated_state_commit()
        with self.assertRaises(journal.JournalError) as error:
            journal.mark_state_committed(self.path)
        self.assertEqual("STATE_NOT_COMMITTED", error.exception.code)
        persisted = journal.load_journal(self.path)
        self.assertEqual(before_hash, persisted["state_commit"]["expected_before_hash"])
        self.assertEqual("VALIDATED", persisted["action"]["status"])
        self.assertEqual("STATE_COMMIT_REQUIRED", journal.recovery_decision(persisted)["decision"])
        self.assertFalse(journal.dispatch_allowed(persisted))

    def test_recovery_reconciles_state_persisted_before_journal_commit(self) -> None:
        state, _, _ = self.validated_state_commit()
        state.write_text("state B", encoding="utf-8")
        decision = journal.recovery_decision(journal.load_journal(self.path))
        self.assertEqual("ALREADY_COMMITTED", decision["decision"])
        self.assertFalse(journal.dispatch_allowed(journal.load_journal(self.path)))
        committed = journal.reconcile_already_committed(self.path)
        self.assertEqual("STATE_COMMITTED", committed["action"]["status"])
        self.assertTrue(committed["state_commit"]["verified"])

    def test_divergent_state_blocks_recovery_and_requires_incident(self) -> None:
        state, _, _ = self.validated_state_commit()
        state.write_text("state C", encoding="utf-8")
        decision = journal.recovery_decision(journal.load_journal(self.path))
        self.assertEqual("BLOCKED", decision["decision"])
        self.assertEqual("STATE_DESYNC", decision["stop_reason"])
        self.assertEqual("STATE_DESYNC", decision["incident_required"])
        self.assertFalse(journal.dispatch_allowed(journal.load_journal(self.path)))

    def test_release_requires_verified_real_state_commit(self) -> None:
        state, _, _ = self.validated_state_commit()
        state.write_text("state B", encoding="utf-8")
        with self.assertRaises(journal.JournalError) as error:
            journal.release_action(self.path)
        self.assertEqual("STATE_COMMIT_UNVERIFIED", error.exception.code)
        journal.mark_state_committed(self.path)
        released = journal.release_action(self.path)
        self.assertEqual("RELEASED", released["action"]["status"])

    def test_symlinked_state_path_is_rejected(self) -> None:
        outside = Path(self.tempdir.name) / "outside-state.md"
        outside.write_text("state A", encoding="utf-8")
        state_link = self.root / "STATE.md"
        state_link.symlink_to(outside)
        value = payload(self.root, status="VALIDATED")
        value["artifact"].update(exists=True, sha256=sha256_text("artifact"), validation_status="VALID")
        self.persist(value)
        with self.assertRaises(journal.JournalError) as error:
            journal.prepare_state_commit(self.path, state_link, sha256_text("state A"), sha256_text("state B"))
        self.assertEqual("STATE_PATH_UNSAFE", error.exception.code)

    def process_finished_without_artifact(self, *, exit_code: int = -15) -> dict:
        value = payload(self.root, status="PROCESS_FINISHED")
        value["process"].update(finished_at="2026-09-16T00:00:01Z", exit_code=exit_code)
        value["artifact"].update(exists=False, sha256=None, validation_status="PENDING")
        value["state_commit"].update(
            state_path=None,
            expected_before_hash=None,
            expected_after_hash=None,
            committed_after_hash=None,
            committed_at=None,
            verified=False,
        )
        return value

    def test_dispatched_without_process_result_cannot_be_archived_interrupted(self) -> None:
        value = payload(self.root, status="DISPATCHED")
        self.persist(value)
        history = self.root / "action-journal-history"
        with self.assertRaises(journal.JournalError) as error:
            journal.archive_interrupted_journal(self.path, history)
        self.assertEqual("JOURNAL_ARCHIVE_INTERRUPTED_NOT_ALLOWED", error.exception.code)
        self.assertEqual(value, journal.load_journal(self.path))
        self.assertEqual([], list(history.rglob("*.json")))

    def test_process_finished_exit_minus_15_without_artifact_archives_as_interrupted(self) -> None:
        value = self.process_finished_without_artifact()
        self.persist(value)
        history = self.root / "action-journal-history"

        result = journal.archive_interrupted_journal(self.path, history)

        archived = Path(result["archived_path"])
        archived_value = json.loads(archived.read_text(encoding="utf-8"))
        self.assertEqual("INTERRUPTED", result["decision"])
        self.assertEqual("INTERRUPTED", archived_value["action"]["status"])
        self.assertNotEqual("RELEASED", archived_value["action"]["status"])
        self.assertNotEqual("SUCCESS", result["decision"])
        self.assertEqual(value["action"]["id"], archived_value["action"]["id"])
        self.assertEqual(-15, archived_value["process"]["exit_code"])
        self.assertFalse(archived_value["artifact"]["exists"])
        self.assertIsNone(archived_value["artifact"]["sha256"])
        self.assertEqual("PENDING", archived_value["artifact"]["validation_status"])
        self.assertFalse(archived_value["state_commit"]["verified"])
        self.assertIsNone(archived_value["state_commit"]["committed_after_hash"])
        self.assertEqual("IDLE", result["new_journal_status"])
        self.assertEqual("DISPATCH_ALLOWED", result["recovery_after_archive"])
        self.assertTrue(journal.is_pristine_idle(journal.load_journal(self.path)))
        self.assertEqual("DISPATCH_ALLOWED", journal.recovery_decision(journal.load_journal(self.path))["decision"])
        self.assertEqual(journal.canonical_json(archived_value), archived.read_bytes())
        expected_archived = journal.transition(value, "INTERRUPTED")
        self.assertEqual(journal.canonical_json(expected_archived), archived.read_bytes())

    def test_archive_interrupted_same_hash_is_idempotent_and_conflict_preserves_live_journal(self) -> None:
        value = self.process_finished_without_artifact()
        history = self.root / "action-journal-history"
        archived_path = history / value["action"]["ticket"] / f"{value['action']['id']}.json"
        expected = journal.transition(value, "INTERRUPTED")
        journal.atomic_create_history(
            str(self.root), history, value["action"]["ticket"], value["action"]["id"], journal.canonical_json(expected),
        )
        original_hash = journal.sha256(archived_path)

        self.persist(value)
        result = journal.archive_interrupted_journal(self.path, history)
        self.assertEqual("INTERRUPTED", result["decision"])
        self.assertEqual(original_hash, result["archived_sha256"])
        self.assertTrue(journal.is_pristine_idle(journal.load_journal(self.path)))

        conflict_source = self.process_finished_without_artifact()
        conflict_source["action"]["id"] = "APP-439-20260916T000000Z-99"
        self.persist(conflict_source)
        conflict_history = history / conflict_source["action"]["ticket"] / f"{conflict_source['action']['id']}.json"
        conflict_history.parent.mkdir(parents=True, exist_ok=True)
        conflict_history.write_text('{"conflict":true}\n', encoding="utf-8")
        with self.assertRaises(journal.JournalError) as error:
            journal.archive_interrupted_journal(self.path, history)
        self.assertEqual("JOURNAL_HISTORY_CONFLICT", error.exception.code)
        self.assertEqual(conflict_source, journal.load_journal(self.path))
        self.assertEqual('{"conflict":true}\n', conflict_history.read_text(encoding="utf-8"))

    def test_interrupted_original_cannot_be_redispatched_and_retry_uses_new_parented_action_id(self) -> None:
        value = self.process_finished_without_artifact()
        self.persist(value)
        history = self.root / "action-journal-history"
        with self.assertRaises(journal.JournalError) as error:
            journal.prepare_action(self.path, payload(self.root, status="PREPARED"))
        self.assertEqual("ACTION_RECOVERY_REQUIRED", error.exception.code)
        self.assertFalse(journal.dispatch_allowed(journal.load_journal(self.path)))

        result = journal.archive_interrupted_journal(self.path, history)
        archived = json.loads(Path(result["archived_path"]).read_text(encoding="utf-8"))
        self.assertNotEqual("DISPATCH_ALLOWED", journal.recovery_decision(archived)["decision"])
        self.assertEqual("INTERRUPTED", archived["action"]["status"])

        retry = payload(self.root, status="PREPARED")
        retry["action"].update(
            id="APP-439-20260916T000001Z-02",
            parent_action_id=value["action"]["id"],
            attempt=2,
            retry_mode="FULL_REPLACEMENT",
            final_message_path=str(self.root / "retry-final-message.json"),
        )
        retry["process"] = {"started_at": None, "finished_at": None, "exit_code": None}
        journal.prepare_action(self.path, retry)
        prepared = journal.load_journal(self.path)
        self.assertEqual("PREPARED", prepared["action"]["status"])
        self.assertEqual("APP-439-20260916T000001Z-02", prepared["action"]["id"])
        self.assertNotEqual(value["action"]["id"], prepared["action"]["id"])
        self.assertEqual(value["action"]["id"], prepared["action"]["parent_action_id"])
        self.assertEqual("DISPATCH_ALLOWED", journal.recovery_decision(prepared)["decision"])

        with self.assertRaises(journal.JournalError) as repeat:
            journal.archive_interrupted_journal(self.path, history)
        self.assertEqual("JOURNAL_ARCHIVE_INTERRUPTED_NOT_ALLOWED", repeat.exception.code)

    def test_archive_interrupted_rejects_released_and_blocked_without_changing_semantics(self) -> None:
        history = self.root / "action-journal-history"
        released, _ = self.released_payload()
        self.persist(released)
        with self.assertRaises(journal.JournalError) as released_error:
            journal.archive_interrupted_journal(self.path, history)
        self.assertEqual("JOURNAL_ARCHIVE_INTERRUPTED_NOT_ALLOWED", released_error.exception.code)
        self.assertEqual("RELEASED", journal.load_journal(self.path)["action"]["status"])
        rollover = journal.rollover_journal(self.path, history)
        self.assertEqual("ROLLED_OVER", rollover["decision"])
        self.assertEqual("RELEASED", json.loads(Path(rollover["archived_path"]).read_text(encoding="utf-8"))["action"]["status"])

        blocked = payload(self.root, status="BLOCKED")
        self.persist(blocked)
        with self.assertRaises(journal.JournalError) as blocked_error:
            journal.archive_interrupted_journal(self.path, history)
        self.assertEqual("JOURNAL_ARCHIVE_INTERRUPTED_NOT_ALLOWED", blocked_error.exception.code)
        persisted_blocked = journal.load_journal(self.path)
        self.assertEqual("BLOCKED", persisted_blocked["action"]["status"])
        self.assertEqual("BLOCKED", journal.recovery_decision(persisted_blocked)["decision"])
        self.assertFalse(journal.dispatch_allowed(persisted_blocked))
        with self.assertRaises(journal.JournalError) as transition_error:
            journal.transition(persisted_blocked, "RELEASED")
        self.assertEqual("INVALID_TRANSITION", transition_error.exception.code)


if __name__ == "__main__":
    unittest.main(verbosity=2)
