"""`--upgrade` is allowed while the only open action is held by a workspace-audit stop.

An IMPLEMENT/TEST result the opt-in workspace audit stopped stays ``ARTIFACT_READY`` in the
journal, with STATE ``RUNNING`` in that stage. The installer used to refuse every open action
(``UPGRADE_CONTROLLER_BUSY``), so a controller fix for the audit itself could not be installed
over the action it should re-audit (FP-006 slice S7, 15.19.1). Exactly one more case is now
idle enough: journal ``ARTIFACT_READY`` for an IMPLEMENT/TEST action that is not validated
(``PENDING``/``INVALID``) and has no STATE commit, STATE ``RUNNING`` in that stage for that
ticket, ``delivery.workspace_audit`` recording ``enforce`` for that action, and no active loop.
Everything else still blocks. The upgrade never touches STATE, the journal or the baseline;
``sdd.py next`` then re-audits the held action with the new runtime.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import test_sdd_orchestrator_skill as skill_tests
from test_sdd_orchestrator_skill import ORCHESTRATION, at

ACTION = "c-1-implement-s1-01"


class AuditHeldUpgradeTests(skill_tests.InstallerUpgradeTests):
    """Reuses the upgrade fixtures; only the tests below run for this class."""

    def held_controller(self, temp: Path) -> tuple[Path, list[str], Path, Path]:
        """An older controller whose only open action is an IMPLEMENT result held by the audit."""
        old = self.make_old_skill(temp, "15.19.1")
        target, storage, root = self.fixture(temp, "obsidian")
        report = self.install(old / "scripts/install_project.py", target, storage)
        runtime = Path(report["worktree_runtime"])
        self.hold(runtime)
        return target, storage, root, runtime

    def hold(self, runtime: Path, *, status: str = "ARTIFACT_READY", stage: str = "IMPLEMENT", record: str | None = ACTION,
             mode: str = "enforce", validation: str = "PENDING", committed: bool = False, loop_active: bool = False,
             state_stage: str | None = None, ticket: str = "c-1") -> None:
        journal_path = runtime / "ACTION_JOURNAL.json"
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        journal["action"].update(id=ACTION, ticket="c-1", stage=stage, status=status, executor="codex", attempt=1)
        journal["process"].update(started_at="2026-10-27T10:00:00Z", finished_at="2026-10-27T10:10:00Z", exit_code=0)
        journal["artifact"].update(exists=True, sha256="a" * 64, validation_status=validation)
        if committed:
            journal["state_commit"].update(expected_after_hash="b" * 64, committed_after_hash="b" * 64,
                                           committed_at="2026-10-27T10:11:00Z", verified=True)
        journal_path.write_text(json.dumps(journal), encoding="utf-8")
        state = runtime / "STATE.md"
        state_format = self.state_format_module()
        text = state_format.normalise(state.read_text(encoding="utf-8"))
        data = state_format.parse(text)
        data["ticket"] = {"id": ticket, "title": "t", "objective": "o"}
        data["stage"]["current"] = state_stage or stage
        data["stage"]["status"] = "RUNNING"
        data["loop"]["control"]["loop_active"] = loop_active
        delivery = data.setdefault("delivery", {})
        if record is None:
            delivery.pop("workspace_audit", None)
        else:
            delivery["workspace_audit"] = {"action_id": record, "key": "c-1:implement:S1", "mode": mode, "baseline_action_id": record}
        before, _, after = state_format.split_fence(text)
        state.write_text(f"{before}```yaml\n{json.dumps(data, indent=2, sort_keys=True)}\n```{after}", encoding="utf-8")

    def runtime_bytes(self, runtime: Path) -> dict[str, bytes]:
        return {name: (runtime / name).read_bytes() for name in ("STATE.md", "ACTION_JOURNAL.json")}

    def test_an_audit_held_action_allows_the_dry_run_and_the_apply_without_touching_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdd-upgrade-held-") as temp:
            target, storage, root, runtime = self.held_controller(Path(temp))
            before = self.runtime_bytes(runtime)
            code, plan = self.upgrade(target, storage)
            self.assertEqual(0, code, plan)
            self.assertEqual("UPGRADE_READY", plan["status"])
            code, applied = self.upgrade(target, storage, "--apply")
            self.assertEqual(0, code, applied)
            self.assertEqual("UPGRADED", applied["status"])
            self.assertEqual(before, self.runtime_bytes(runtime), "the upgrade must not touch STATE or the journal")
            self.assertEqual((ORCHESTRATION / "runtime/dispatch_audit.py").read_bytes(),
                             at(root, ".hermes/orchestration/runtime/dispatch_audit.py").read_bytes())

    def test_every_other_open_action_still_blocks(self) -> None:
        cases = {
            **{f"journal {status}": dict(status=status) for status in ("PREPARED", "DISPATCHED", "PROCESS_FINISHED", "VALIDATED", "STATE_COMMITTED")},
            "no audit record": dict(record=None),
            "audit record of another action": dict(record="c-1-implement-s1-00"),
            "audit mode off": dict(mode="off"),
            "a read-only stage": dict(stage="PLAN"),
            "STATE in another stage": dict(state_stage="TEST"),
            "STATE of another ticket": dict(ticket="c-2"),
            "a validated artifact": dict(validation="VALID"),
            "a STATE commit recorded": dict(committed=True),
            "an active loop": dict(loop_active=True),
        }
        with tempfile.TemporaryDirectory(prefix="sdd-upgrade-held-busy-") as temp:
            target, storage, root, runtime = self.held_controller(Path(temp))
            pristine = {name: (runtime / name).read_bytes() for name in ("STATE.md", "ACTION_JOURNAL.json")}
            for label, options in cases.items():
                with self.subTest(case=label):
                    for name, content in pristine.items():
                        (runtime / name).write_bytes(content)
                    self.hold(runtime, **options)
                    before = self.controller_snapshot(root)
                    code, blocked = self.upgrade(target, storage, "--apply")
                    self.assertEqual(2, code, blocked)
                    self.assertIn("UPGRADE_CONTROLLER_BUSY", blocked["reason"])
                    self.assertEqual(before, self.controller_snapshot(root))

    def test_the_held_exception_covers_test_and_an_invalid_classification(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdd-upgrade-held-test-") as temp:
            target, storage, root, runtime = self.held_controller(Path(temp))
            for options in (dict(stage="TEST"), dict(validation="INVALID")):
                with self.subTest(**options):
                    self.hold(runtime, **options)
                    code, plan = self.upgrade(target, storage)
                    self.assertEqual(0, code, plan)
                    self.assertEqual("UPGRADE_READY", plan["status"])

    def test_a_second_runtime_that_is_busy_still_blocks(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdd-upgrade-held-two-") as temp:
            target, storage, root, runtime = self.held_controller(Path(temp))
            other = runtime.parent / "other-worktree"
            shutil.copytree(runtime, other)
            journal = json.loads((other / "ACTION_JOURNAL.json").read_text(encoding="utf-8"))
            journal["action"]["status"] = "DISPATCHED"
            (other / "ACTION_JOURNAL.json").write_text(json.dumps(journal), encoding="utf-8")
            code, blocked = self.upgrade(target, storage)
            self.assertEqual(2, code, blocked)
            self.assertIn("action DISPATCHED", blocked["reason"])
            self.assertNotIn(str(runtime / "ACTION_JOURNAL.json") + ":", blocked["reason"])


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromNames(
        f"{__name__}.AuditHeldUpgradeTests.{name}" for name in loader.getTestCaseNames(AuditHeldUpgradeTests)
        if name in vars(AuditHeldUpgradeTests)
    )


if __name__ == "__main__":
    unittest.main()
