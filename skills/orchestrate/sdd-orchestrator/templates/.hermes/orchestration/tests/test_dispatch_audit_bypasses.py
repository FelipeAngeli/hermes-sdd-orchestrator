"""Workspace audit regressions: every bypass found in review, and the audit never fails open.

With ``workspace_audit: enforce`` a worker must not be able to hide a change from the audit by
ignoring it (a new ``.gitignore``, ``info/exclude`` or an already ignored folder), by committing
it (``git commit -am`` moves HEAD and leaves a clean worktree), by staging tricks (index flags),
or by leaving it behind in a failed attempt so the retry's baseline already contains it. An
absent or corrupt snapshot, an unreadable result or a failing ``git`` call is a stop, never a pass.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ORCHESTRATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ORCHESTRATION / "runtime"))

import dispatch_audit  # noqa: E402
import sdd  # noqa: E402
import validate_protocol  # noqa: E402

MATCH = validate_protocol.path_matches
CONTROLLER = (".hermes/orchestration",)


def result(*paths: str, created: tuple[str, ...] = ()) -> dict:
    return {"executor_result": {"modified_paths": [{"path": path} for path in paths],
                                "created_paths": [{"path": path} for path in created]}}


class Repository(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="sdd-audit-bypass-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@e")
        self.git("config", "user.name", "t")
        (self.repo / "src").mkdir()
        (self.repo / "src" / "feature.py").write_text("x = 1\n", encoding="utf-8")
        (self.repo / ".gitignore").write_text(".hermes/\nbuild/\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "init")

    def git(self, *arguments: str) -> None:
        subprocess.run(["git", "-C", str(self.repo), *arguments], check=True, capture_output=True)

    def snap(self) -> dict:
        return dispatch_audit.snapshot(self.repo, exclude=CONTROLLER)

    def findings(self, before: dict, declared: dict, editable: list[str] | None = None) -> dict[str, str]:
        found = dispatch_audit.audit(before=before, after=self.snap(), result=declared, editable_paths=editable or [], path_matches=MATCH)
        return {item["path"]: item["finding"] for item in found}


class WorkspaceAuditBypassTests(Repository):
    def test_a_new_gitignore_cannot_hide_a_dropped_file(self) -> None:
        before = self.snap()
        (self.repo / "src" / "hidden").mkdir()
        (self.repo / "src" / "hidden" / ".gitignore").write_text("*\n", encoding="utf-8")
        (self.repo / "src" / "hidden" / "conftest.py").write_text("import os\n", encoding="utf-8")
        found = self.findings(before, result())
        self.assertEqual("UNDECLARED_CHANGE", found.get("src/hidden/conftest.py"), found)
        self.assertIn("src/hidden/.gitignore", found)

    def test_a_declared_ignore_rule_change_still_fails(self) -> None:
        before = self.snap()
        (self.repo / ".gitignore").write_text(".hermes/\nbuild/\nsrc/\n", encoding="utf-8")
        found = self.findings(before, result(".gitignore"))
        self.assertEqual("IGNORE_RULES_CHANGED", found.get(".gitignore"), found)

    def test_info_exclude_changes_are_reported(self) -> None:
        before = self.snap()
        exclude = self.repo / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text(exclude.read_text(encoding="utf-8") + "src/\n" if exclude.exists() else "src/\n", encoding="utf-8")
        self.assertEqual("IGNORE_RULES_CHANGED", self.findings(before, result()).get(".git/info/exclude"))

    def test_a_file_in_an_already_ignored_folder_is_seen(self) -> None:
        before = self.snap()
        (self.repo / "build").mkdir()
        (self.repo / "build" / "conftest.py").write_text("x\n", encoding="utf-8")
        self.assertEqual("UNDECLARED_CHANGE", self.findings(before, result()).get("build/conftest.py"))

    def test_a_commit_by_the_worker_is_reported_even_when_declared(self) -> None:
        before = self.snap()
        (self.repo / "src" / "feature.py").write_text("x = 2\n", encoding="utf-8")
        self.git("commit", "-qam", "worker commit")
        found = self.findings(before, result("src/feature.py"), ["src/*"])
        self.assertEqual("GIT_STATE_MOVED", found.get("HEAD"), found)
        self.assertEqual("GIT_STATE_MOVED", found.get("index"), found)

    def test_a_new_ref_is_reported(self) -> None:
        before = self.snap()
        self.git("branch", "smuggled")
        self.assertEqual("GIT_STATE_MOVED", self.findings(before, result()).get("refs"))

    def test_an_index_flag_that_hides_a_tracked_edit_is_reported(self) -> None:
        before = self.snap()
        self.git("update-index", "--skip-worktree", "src/feature.py")
        (self.repo / "src" / "feature.py").write_text("x = 9\n", encoding="utf-8")
        self.assertEqual("GIT_STATE_MOVED", self.findings(before, result()).get("index"))

    def test_a_mode_change_of_an_already_dirty_file_is_seen(self) -> None:
        (self.repo / "src" / "feature.py").write_text("x = 2\n", encoding="utf-8")
        before = self.snap()
        (self.repo / "src" / "feature.py").chmod(0o755)
        self.assertEqual("UNDECLARED_CHANGE", self.findings(before, result()).get("src/feature.py"))

    def test_the_snapshot_records_head_refs_index_and_the_full_diff_hash(self) -> None:
        value = self.snap()
        for key in ("head", "refs_sha256", "index_sha256", "diff_sha256", "ignore_rules"):
            self.assertIn(key, value)
        (self.repo / "src" / "feature.py").write_text("x = 3\n", encoding="utf-8")
        self.git("add", "src/feature.py")
        staged = self.snap()
        self.assertNotEqual(value["diff_sha256"], staged["diff_sha256"])

    def test_a_diff_that_moved_without_any_listed_file_is_reported(self) -> None:
        before = self.snap()
        after = {**before, "diff_sha256": "0" * 64}
        found = dispatch_audit.audit(before=before, after=after, result=result(), editable_paths=[], path_matches=MATCH)
        self.assertEqual([("diff", "GIT_STATE_MOVED")], [(item["path"], item["finding"]) for item in found])

    def test_only_the_named_controller_runtime_is_excluded(self) -> None:
        before = self.snap()
        (self.repo / ".hermes" / "orchestration").mkdir(parents=True)
        (self.repo / ".hermes" / "orchestration" / "STATE.md").write_text("stage: IMPLEMENT\n", encoding="utf-8")
        (self.repo / ".hermes" / "other.py").write_text("x\n", encoding="utf-8")
        self.assertEqual({".hermes/other.py": "UNDECLARED_CHANGE"}, self.findings(before, result()))

    def test_controller_exclusions_are_only_paths_inside_the_repository(self) -> None:
        inside = self.repo / ".hermes" / "orchestration"
        self.assertEqual([".hermes/orchestration"], dispatch_audit.exclusions(self.repo, [inside, self.base / "vault", self.repo]))

    def test_a_failing_git_command_raises_instead_of_returning_an_empty_snapshot(self) -> None:
        with self.assertRaises(dispatch_audit.AuditError):
            dispatch_audit.snapshot(self.base)
        failing = subprocess.CompletedProcess([], 128, b"", b"fatal")
        with mock.patch.object(dispatch_audit.subprocess, "run", return_value=failing), self.assertRaises(dispatch_audit.AuditError):
            dispatch_audit.snapshot(self.repo)
        with mock.patch.object(dispatch_audit.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 1)), \
                self.assertRaises(dispatch_audit.AuditError):
            dispatch_audit.snapshot(self.repo)


class WorkspaceAuditControllerFailClosedTests(Repository):
    """``sdd.workspace_audit_prepare``/``workspace_audit_stop`` with a fake context."""

    def setUp(self) -> None:
        super().setUp()
        self.run_dir = self.base / "run"
        self.run_dir.mkdir()
        self.ctx = mock.Mock(repo=self.repo, delivery={"slices": {"editable_paths": ["src/*"]}}, stage="IMPLEMENT",
                             runtime_dir=self.base / "runtime", state_path=self.base / "STATE.md", journal_path=self.base / "j.json",
                             history_dir=self.base / "history")
        self.ctx.run_dir.return_value = self.run_dir
        self.data = {"delivery": dict(self.ctx.delivery)}

    def action(self, number: int) -> dict:
        return {"id": f"T-IMPLEMENT-{number:02d}", "stage": "IMPLEMENT", "final": self.run_dir / f"T-IMPLEMENT-{number:02d}.result.json"}

    def plan(self, number: int, parent: int | None = None) -> dict:
        return {"stage": "IMPLEMENT", "action_id": f"T-IMPLEMENT-{number:02d}", "key": "implement-s1",
                "parent": None if parent is None else {"action_id": f"T-IMPLEMENT-{parent:02d}"}}

    def prepare(self, number: int, parent: int | None = None, enabled: bool = True) -> None:
        with mock.patch.object(sdd, "workspace_audit_enabled", return_value=enabled):
            sdd.workspace_audit_prepare(self.ctx, self.plan(number, parent), self.data)
        self.ctx.delivery = self.data["delivery"]

    def stop(self, number: int, enabled: bool = True) -> dict | None:
        with mock.patch.object(sdd, "workspace_audit_enabled", return_value=enabled), \
                mock.patch.object(sdd, "stopped", lambda _ctx, payload: payload):
            return sdd.workspace_audit_stop(self.ctx, self.action(number))

    def test_prepare_records_the_mode_and_the_snapshot(self) -> None:
        self.prepare(1)
        self.assertEqual({"action_id": "T-IMPLEMENT-01", "key": "implement-s1", "mode": "enforce", "baseline_action_id": "T-IMPLEMENT-01"}, self.data["delivery"]["workspace_audit"])
        self.assertTrue((self.run_dir / "T-IMPLEMENT-01.workspace-before.json").is_file())

    def test_off_records_nothing_and_writes_nothing(self) -> None:
        self.prepare(1, enabled=False)
        self.assertNotIn("workspace_audit", self.data["delivery"])
        self.assertEqual([], list(self.run_dir.iterdir()))

    def test_a_retry_keeps_the_first_attempt_baseline(self) -> None:
        self.prepare(1)
        (self.repo / "sneaky.py").write_text("x\n", encoding="utf-8")  # left behind by the failed attempt
        self.prepare(2, parent=1)
        (self.repo / "src" / "feature.py").write_text("x = 2\n", encoding="utf-8")
        self.action(2)["final"].write_text(json.dumps(result("src/feature.py")), encoding="utf-8")
        payload = self.stop(2)
        self.assertIsNotNone(payload)
        self.assertEqual(["sneaky.py"], payload["undeclared"])

    def test_an_archived_undispatched_retry_does_not_reset_the_baseline(self) -> None:
        self.prepare(1)
        (self.repo / "sneaky.py").write_text("x\n", encoding="utf-8")
        self.prepare(2, parent=1)  # archived by `sdd.py reprepare` before it ever ran
        self.prepare(3, parent=1)
        self.assertEqual("T-IMPLEMENT-01", self.data["delivery"]["workspace_audit"]["baseline_action_id"])

    def test_a_new_series_takes_a_fresh_baseline(self) -> None:
        self.prepare(1)
        (self.repo / "accepted.py").write_text("x\n", encoding="utf-8")
        self.prepare(2)
        self.assertEqual("T-IMPLEMENT-02", self.data["delivery"]["workspace_audit"]["baseline_action_id"])

    def test_enforce_with_a_missing_snapshot_stops(self) -> None:
        self.prepare(1)
        (self.run_dir / "T-IMPLEMENT-01.workspace-before.json").unlink()
        self.action(1)["final"].write_text(json.dumps(result()), encoding="utf-8")
        payload = self.stop(1)
        self.assertEqual("WORKSPACE_AUDIT_FAILED", payload["stop_reason"])
        self.assertEqual("AUDIT_UNVERIFIABLE", payload["findings"][0]["finding"])

    def test_the_recorded_mode_wins_over_a_policy_switched_off_mid_dispatch(self) -> None:
        self.prepare(1)
        (self.run_dir / "T-IMPLEMENT-01.workspace-before.json").write_text("{not json", encoding="utf-8")
        self.action(1)["final"].write_text(json.dumps(result()), encoding="utf-8")
        self.assertEqual("AUDIT_UNVERIFIABLE", self.stop(1, enabled=False)["findings"][0]["finding"])

    def test_enforce_with_an_unreadable_result_stops(self) -> None:
        self.prepare(1)
        self.action(1)["final"].write_text("{broken", encoding="utf-8")
        self.assertEqual("AUDIT_UNVERIFIABLE", self.stop(1)["findings"][0]["finding"])

    def test_enforce_with_a_failing_git_command_stops(self) -> None:
        self.prepare(1)
        self.action(1)["final"].write_text(json.dumps(result()), encoding="utf-8")
        failing = subprocess.CompletedProcess([], 128, b"", b"fatal")
        with mock.patch.object(dispatch_audit.subprocess, "run", return_value=failing):
            payload = self.stop(1)
        self.assertEqual("AUDIT_UNVERIFIABLE", payload["findings"][0]["finding"])

    def test_a_failing_snapshot_at_prepare_refuses_the_action(self) -> None:
        failing = subprocess.CompletedProcess([], 128, b"", b"fatal")
        with mock.patch.object(dispatch_audit.subprocess, "run", return_value=failing), self.assertRaises(sdd.SddError) as raised:
            self.prepare(1)
        self.assertEqual("WORKSPACE_AUDIT_FAILED", raised.exception.code)


if __name__ == "__main__":
    unittest.main()
