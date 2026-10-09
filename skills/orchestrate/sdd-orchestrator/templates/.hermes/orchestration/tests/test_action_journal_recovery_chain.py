"""Recovery must always make progress: following ``next_command`` never cycles.

Each test crafts a journal state, then runs ``recover`` and the command it
prints, again and again, the way an LLM controller does. The chain must reach
DISPATCH_ALLOWED (or hand over to a step outside the journal, such as the
validator) within a few steps; a repeated (decision, status) pair means the
controller would loop forever.
"""
from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))

import action_journal as journal

SCRIPT = RUNTIME / "action_journal.py"
MAX_STEPS = 6
#: Placeholders the chain can fill without a human; any other ``<...>`` ends the chain as an external step.
FILLABLE = {"<exit-code>": "1", "<reason>": "recovery chain test"}


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class RecoveryChainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="action journal chain-")
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name).resolve() / "workspace"
        self.root.mkdir()
        self.path = self.root / "ACTION_JOURNAL.json"
        self.history = self.root / journal.HISTORY_DIRECTORY_NAME
        self.workspace = {"path": str(self.root), "branch": "dev", "head": "a" * 40, "git_common_dir": str(self.root / ".git")}
        self.final = self.root / "APP-439-plan-01.json"
        self.outside = Path(self.tempdir.name).resolve() / "outside-final.json"
        self.outside.write_text('{"looks": "valid"}', encoding="utf-8")

    # --- crafted states -------------------------------------------------------------

    def action(self, status: str, *, started: bool, exit_code: int | None = None) -> dict:
        value = journal.empty_journal(self.workspace)
        value["action"].update(
            id="APP-439-plan-01", ticket="APP-439", stage="PLAN", name="PLAN", status=status, executor="CODEX",
            schema_path=".hermes/orchestration/schemas/EXECUTOR_RESULT_SCHEMA.json", protocol_version=2,
            prompt_hash=sha256_text("prompt"), final_message_path=str(self.final), attempt=1, retry_mode="FULL_REPLACEMENT",
        )
        value["fingerprints"].update(baseline=sha256_text("b"), ownership=sha256_text("o"), state_before=sha256_text("s"))
        if started:
            value["process"]["started_at"] = "2026-10-09T00:00:00Z"
        if exit_code is not None:
            value["process"].update(finished_at="2026-10-09T00:00:01Z", exit_code=exit_code)
        return value

    def symlinked_final(self) -> None:
        self.final.symlink_to(self.outside)

    # --- chain runner -------------------------------------------------------------------

    def cli(self, *argv: str) -> tuple[int, dict]:
        result = subprocess.run([sys.executable, "-B", *argv], text=True, capture_output=True, check=False, timeout=60)
        return result.returncode, json.loads(result.stdout)

    def recover(self) -> dict:
        code, decision = self.cli(str(SCRIPT), "--journal", str(self.path), "--json", "recover")
        self.assertEqual(0, code, decision)
        return decision

    def follow(self, value: dict, *, max_steps: int = MAX_STEPS) -> list[tuple[str, str]]:
        """Persist ``value``, then follow recover's next_command until a terminal decision.

        Terminal: DISPATCH_ALLOWED, or a next_command that is not an action-journal
        command / needs a value only a controller can supply. Fails on a repeated
        (decision, status) pair or when ``max_steps`` pass without a terminal.
        """
        journal.atomic_write(self.path, value)
        seen: list[tuple[str, str]] = []
        for _ in range(max_steps):
            decision = self.recover()
            status = journal.load_journal(self.path)["action"]["status"]
            pair = (decision["decision"], status)
            self.assertNotIn(pair, seen, f"recovery cycle: {seen + [pair]}")
            seen.append(pair)
            self.assertTrue(decision["next_step"], decision)
            self.assertTrue(decision["next_command"], decision)
            if decision["decision"] == "DISPATCH_ALLOWED":
                return seen
            argv = shlex.split(decision["next_command"])
            if Path(argv[1]).name != SCRIPT.name:
                return seen
            argv = [FILLABLE.get(token, token) for token in argv]
            if any(token.startswith("<") and token.endswith(">") for token in argv):
                return seen
            code, outcome = self.cli(*argv[1:])
            self.assertEqual(0, code, f"{pair}: {decision['next_command']} -> {outcome}")
        self.fail(f"no terminal decision within {max_steps} steps: {seen}")

    # --- the reproduced loop ----------------------------------------------------------------

    def test_symlinked_final_message_after_exit_requires_archive_interrupted(self) -> None:
        self.symlinked_final()
        value = self.action("PROCESS_FINISHED", started=True, exit_code=0)
        journal.atomic_write(self.path, value)

        decision = self.recover()

        self.assertEqual("ARCHIVE_INTERRUPTED_REQUIRED", decision["decision"])
        self.assertIn("archive-interrupted", decision["next_command"])
        self.assertFalse(journal.artifact_present(value))

    def test_symlinked_final_message_chain_reaches_dispatch_allowed(self) -> None:
        self.symlinked_final()

        chain = self.follow(self.action("PROCESS_FINISHED", started=True, exit_code=0))

        self.assertEqual(
            [("ARCHIVE_INTERRUPTED_REQUIRED", "PROCESS_FINISHED"), ("DISPATCH_ALLOWED", "IDLE")], chain,
        )
        archived = journal.load_journal(self.history / "APP-439" / "APP-439-plan-01.json")
        self.assertEqual("INTERRUPTED", archived["action"]["status"])
        # The link and its target are left alone: evidence, never followed.
        self.assertTrue(self.final.is_symlink())
        self.assertEqual('{"looks": "valid"}', self.outside.read_text(encoding="utf-8"))

    def test_every_presence_check_treats_a_symlink_as_missing(self) -> None:
        self.symlinked_final()
        value = self.action("PROCESS_FINISHED", started=True, exit_code=0)
        journal.atomic_write(self.path, value)
        self.assertFalse(journal.artifact_present(value))
        journal.archive_interrupted_allowed(value)  # does not raise
        with self.assertRaises(journal.JournalError) as missing:
            journal.record_artifact(self.path)
        self.assertEqual("ARTIFACT_MISSING", missing.exception.code)

    # --- generic: no crafted state cycles -----------------------------------------------------

    def test_no_crafted_journal_state_cycles(self) -> None:
        def symlink(value: dict) -> dict:
            self.symlinked_final(); return value

        def directory(value: dict) -> dict:
            self.final.mkdir(); return value

        def regular(value: dict) -> dict:
            self.final.write_text("{}", encoding="utf-8"); return value

        def interrupted() -> dict:
            return self.action("INTERRUPTED", started=True, exit_code=1)

        def dirty_idle() -> dict:
            value = journal.empty_journal(self.workspace)
            value["process"]["started_at"] = "2026-10-09T00:00:00Z"
            return value

        cases = {
            "finished, symlinked final": lambda: symlink(self.action("PROCESS_FINISHED", started=True, exit_code=0)),
            "finished, final is a directory": lambda: directory(self.action("PROCESS_FINISHED", started=True, exit_code=0)),
            "finished, no final": lambda: self.action("PROCESS_FINISHED", started=True, exit_code=124),
            "finished, regular final": lambda: regular(self.action("PROCESS_FINISHED", started=True, exit_code=0)),
            "dispatched, symlinked final": lambda: symlink(self.action("DISPATCHED", started=True)),
            "dispatched, regular final": lambda: regular(self.action("DISPATCHED", started=True)),
            "dispatched, no final": lambda: self.action("DISPATCHED", started=True),
            "prepared, symlink occupies final": lambda: symlink(self.action("PREPARED", started=False)),
            "prepared, regular file occupies final": lambda: regular(self.action("PREPARED", started=False)),
            "prepared, clean": lambda: self.action("PREPARED", started=False),
            "blocked": lambda: self.action("BLOCKED", started=True, exit_code=1),
            "live interrupted": interrupted,
            "dirty idle": dirty_idle,
            "pristine idle": lambda: journal.empty_journal(self.workspace),
        }
        for label, build in cases.items():
            with self.subTest(state=label):
                self.reset()
                chain = self.follow(build())
                self.assertTrue(chain)

    def reset(self) -> None:
        for path in (self.path, self.final):
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        if self.history.exists():
            for item in sorted(self.history.rglob("*"), reverse=True):
                item.unlink() if item.is_file() else item.rmdir()
            self.history.rmdir()

    def test_a_symlink_or_directory_at_an_undispatched_final_path_still_blocks_dispatch(self) -> None:
        for occupy in (self.symlinked_final, self.final.mkdir):
            with self.subTest(occupant=occupy.__name__):
                self.reset()
                occupy()
                value = self.action("PREPARED", started=False)
                journal.atomic_write(self.path, value)
                self.assertEqual("BLOCKED", self.recover()["decision"])
                with self.assertRaises(journal.JournalError) as refused:
                    journal.record_process_started(self.path)
                self.assertEqual("ARTIFACT_PENDING", refused.exception.code)


class HistoryComponentTests(unittest.TestCase):
    """Ticket and action ids become history path components: only TICKET_PATTERN-safe names."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="action journal ids-")
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name).resolve() / "workspace"
        self.root.mkdir()
        self.history = self.root / journal.HISTORY_DIRECTORY_NAME
        self.path = self.root / "ACTION_JOURNAL.json"
        self.workspace = {"path": str(self.root), "branch": "dev", "head": "a" * 40, "git_common_dir": str(self.root / ".git")}

    UNSAFE = ("..", ".", "", "-rf", "a/b", "a\\b", "x" * 65, "tab\there", "..hidden", ".hidden")

    def test_unsafe_ticket_or_action_id_never_becomes_a_history_path(self) -> None:
        for unsafe in self.UNSAFE:
            pairs = [(unsafe, "APP-1-plan-01")]
            pairs.append(("APP-1", "y" * 161 if unsafe == "x" * 65 else unsafe))
            for ticket, action_id in pairs:
                with self.subTest(ticket=ticket, action_id=action_id):
                    with self.assertRaises(journal.JournalError) as error:
                        journal.atomic_create_history(str(self.root), self.history, ticket, action_id, b"{}")
                    self.assertEqual("HISTORY_PATH_UNSAFE", error.exception.code)
        self.assertEqual([], [p.name for p in self.root.iterdir() if p.name != journal.HISTORY_DIRECTORY_NAME])
        self.assertFalse(any(self.history.rglob("*.json")) if self.history.exists() else False)

    def test_dotdot_ticket_does_not_write_above_history(self) -> None:
        with self.assertRaises(journal.JournalError):
            journal.atomic_create_history(str(self.root), self.history, "..", "APP-1-plan-01", b"{}")
        self.assertFalse((self.root / "APP-1-plan-01.json").exists())

    def test_safe_ids_follow_the_controller_ticket_pattern(self) -> None:
        for ticket in ("APP-439", "a", "A1.b_c-d", "x" * 64):
            self.assertTrue(journal.safe_path_component(ticket), ticket)
        self.assertTrue(journal.safe_path_component("APP-439-implement-slice-1-01", action=True))
        self.assertTrue(journal.safe_path_component("x" * 64 + "-implement-" + "y" * 40 + "-01", action=True))
        for unsafe in self.UNSAFE:
            self.assertFalse(journal.safe_path_component(unsafe), unsafe)
            if unsafe != "x" * 65:
                self.assertFalse(journal.safe_path_component(unsafe, action=True), unsafe)
        self.assertFalse(journal.safe_path_component("y" * 161, action=True))
        self.assertFalse(journal.safe_path_component(None))

    def payload(self, ticket: str, action_id: str) -> dict:
        value = journal.empty_journal(self.workspace)
        value["action"].update(
            id=action_id, ticket=ticket, stage="PLAN", name="PLAN", status="PREPARED", executor="CODEX",
            prompt_hash=sha256_text("p"), final_message_path=str(self.root / "final.json"), attempt=1,
            retry_mode="FULL_REPLACEMENT",
        )
        return value

    def test_prepare_refuses_unsafe_ids_with_guidance(self) -> None:
        journal.atomic_write(self.path, journal.empty_journal(self.workspace))
        for ticket, action_id in (("..", "APP-1-plan-01"), ("APP-1", "../escape")):
            with self.subTest(ticket=ticket, action_id=action_id):
                with self.assertRaises(journal.JournalError) as error:
                    journal.prepare_action(self.path, self.payload(ticket, action_id))
                self.assertEqual("ACTION_ID_UNSAFE", error.exception.code)
                self.assertTrue(error.exception.next_step)
                self.assertEqual("IDLE", journal.load_journal(self.path)["action"]["status"])

    def test_a_journal_with_unsafe_ids_is_still_archivable_as_blocked(self) -> None:
        value = self.payload("..", "../escape")
        value["action"]["status"] = "BLOCKED"
        journal.atomic_write(self.path, value)
        decision = journal.recovery_decision(value, journal_path=self.path)
        self.assertIn("archive-blocked", decision["next_command"])

        archived = journal.archive_blocked_journal(self.path, self.history, "unsafe ids")

        archived_path = Path(archived["archived_path"])
        self.assertEqual(self.history / "NO-TICKET", archived_path.parent)
        self.assertTrue(archived_path.name.startswith("NO-ACTION-"))
        self.assertIn("..", journal.load_journal(archived_path)["action"]["ticket"])
        self.assertEqual("DISPATCH_ALLOWED", archived["recovery_after_archive"])

    def test_archive_interrupted_with_unsafe_ids_names_block_then_archive_blocked(self) -> None:
        value = self.payload("..", "APP-1-plan-01")
        value["action"]["status"] = "PROCESS_FINISHED"
        value["process"].update(started_at="t0", finished_at="t1", exit_code=1)
        journal.atomic_write(self.path, value)
        with self.assertRaises(journal.JournalError) as error:
            journal.archive_interrupted_journal(self.path, self.history)
        self.assertEqual("JOURNAL_ARCHIVE_INTERRUPTED_INCOMPLETE", error.exception.code)
        self.assertIn(" block", error.exception.next_command)
        self.assertFalse(self.history.exists() and any(self.history.rglob("*.json")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
