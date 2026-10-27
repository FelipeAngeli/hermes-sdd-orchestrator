"""Opt-in workspace audit: OFF changes nothing, ON blocks undeclared or out-of-scope changes.

The audit compares the real worktree before and after a writing dispatch with the paths
the result declares, for every executor. These tests drive the snapshot/audit primitives
directly on a temporary Git repository and the controller wiring through ``sdd.py``'s own
functions with a fake context.
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
import executor_launch as launch  # noqa: E402
import sdd  # noqa: E402
import stop_reasons  # noqa: E402
import validate_protocol  # noqa: E402

MATCH = validate_protocol.path_matches


def result(*paths: str, created: tuple[str, ...] = ()) -> dict:
    return {"executor_result": {"modified_paths": [{"path": path} for path in paths],
                                "created_paths": [{"path": path} for path in created]}}


class WorkspaceAuditPolicyTests(unittest.TestCase):
    def policy(self, block: str) -> Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "EXECUTORS.md"
        path.write_text(f"```json\n{block}\n```\n", encoding="utf-8")
        return path

    def test_the_audit_is_off_by_default_everywhere(self) -> None:
        for policy in (launch.load_policy(Path("/nonexistent/EXECUTORS.md")), launch.load_policy(),
                       launch.load_policy(self.policy('{"executors_version": 1, "stages": {}}'))):
            self.assertEqual("off", policy["workspace_audit"])
            for stage in sdd.STAGES:
                self.assertFalse(dispatch_audit.enabled(policy, stage))

    def test_enforce_applies_only_to_the_writing_stages_and_other_values_are_refused(self) -> None:
        policy = launch.load_policy(self.policy('{"executors_version": 1, "workspace_audit": "enforce", "stages": {}}'))
        self.assertEqual("enforce", policy["workspace_audit"])
        for stage in sdd.STAGES:
            self.assertEqual(stage in ("IMPLEMENT", "TEST"), dispatch_audit.enabled(policy, stage))
        for block in ('{"executors_version": 1, "workspace_audit": "warn", "stages": {}}',
                      '{"executors_version": 1, "workspace_audit": true, "stages": {}}'):
            with self.subTest(block=block), self.assertRaises(launch.LaunchError) as raised:
                launch.load_policy(self.policy(block))
            self.assertEqual("POLICY_INVALID", raised.exception.status)

    def test_the_stop_reason_is_registered_with_one_next_command(self) -> None:
        described = stop_reasons.describe(dispatch_audit.STOP_REASON)
        self.assertEqual(("WORKSPACE_AUDIT_FAILED", "BLOCKED"), (described["stop_reason"], described["kind"]))
        self.assertIn("abandon", described["next_command"])
        # A controller fix for the audit can be installed over the held action, which `next` then re-audits.
        self.assertIn("install_project.py --upgrade", described["next_step"])
        self.assertIn("re-audits", described["next_step"])


class WorkspaceAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="sdd-audit-")
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name).resolve() / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@e")
        self.git("config", "user.name", "t")
        (self.repo / "src").mkdir()
        (self.repo / "src" / "feature.py").write_text("x = 1\n", encoding="utf-8")
        (self.repo / ".gitignore").write_text(".hermes/\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "init")

    def git(self, *arguments: str) -> None:
        subprocess.run(["git", "-C", str(self.repo), *arguments], check=True, capture_output=True)

    def test_a_clean_declared_change_passes(self) -> None:
        before = dispatch_audit.snapshot(self.repo)
        (self.repo / "src" / "feature.py").write_text("x = 2\n", encoding="utf-8")
        (self.repo / "src" / "new.py").write_text("y = 1\n", encoding="utf-8")
        after = dispatch_audit.snapshot(self.repo)
        self.assertEqual(["src/feature.py", "src/new.py"], dispatch_audit.changed_paths(before, after))
        findings = dispatch_audit.audit(before=before, after=after, result=result("src/feature.py", created=("src/new.py",)),
                                        editable_paths=["src/*"], path_matches=MATCH)
        self.assertEqual([], findings)

    def test_an_undeclared_change_is_reported_for_every_executor(self) -> None:
        before = dispatch_audit.snapshot(self.repo)
        (self.repo / "src" / "feature.py").write_text("x = 2\n", encoding="utf-8")
        (self.repo / "sneaky.py").write_text("import os\n", encoding="utf-8")
        after = dispatch_audit.snapshot(self.repo)
        for executor in ("claude", "codex", "opencode"):
            with self.subTest(executor=executor):
                findings = dispatch_audit.audit(before=before, after=after, result=result("src/feature.py"),
                                                editable_paths=["src/*"], path_matches=MATCH)
                self.assertEqual([{"path": "sneaky.py", "finding": "UNDECLARED_CHANGE",
                                   "detail": "the file changed during the dispatch but the result does not declare it"}], findings)
        self.assertIn("sneaky.py", dispatch_audit.stop_message("IMPLEMENT", findings))

    def test_a_declared_change_outside_the_editable_paths_is_reported(self) -> None:
        before = dispatch_audit.snapshot(self.repo)
        (self.repo / "setup.cfg").write_text("[x]\n", encoding="utf-8")
        after = dispatch_audit.snapshot(self.repo)
        findings = dispatch_audit.audit(before=before, after=after, result=result(created=("setup.cfg",)),
                                        editable_paths=["src/*"], path_matches=MATCH)
        self.assertEqual("OUTSIDE_EDITABLE_PATHS", findings[0]["finding"])

    def test_a_silent_content_rewrite_and_a_revert_are_both_seen(self) -> None:
        (self.repo / "src" / "feature.py").write_text("x = 2\n", encoding="utf-8")
        before = dispatch_audit.snapshot(self.repo)
        (self.repo / "src" / "feature.py").write_text("x = 3\n", encoding="utf-8")
        rewritten = dispatch_audit.snapshot(self.repo)
        self.assertEqual(["src/feature.py"], dispatch_audit.changed_paths(before, rewritten))
        (self.repo / "src" / "feature.py").write_text("x = 1\n", encoding="utf-8")
        reverted = dispatch_audit.snapshot(self.repo)
        self.assertEqual(["src/feature.py"], dispatch_audit.changed_paths(before, reverted))

    def test_staged_and_committed_work_is_still_compared(self) -> None:
        before = dispatch_audit.snapshot(self.repo)
        (self.repo / "src" / "feature.py").write_text("x = 9\n", encoding="utf-8")
        self.git("add", "src/feature.py")
        self.assertEqual(["src/feature.py"], dispatch_audit.changed_paths(before, dispatch_audit.snapshot(self.repo)))

    def test_the_controller_runtime_never_counts_as_a_worker_change(self) -> None:
        before = dispatch_audit.snapshot(self.repo, [".hermes/orchestration"])
        controller = self.repo / ".hermes" / "orchestration"
        controller.mkdir(parents=True)
        (controller / "STATE.md").write_text("stage: IMPLEMENT\n", encoding="utf-8")
        after = dispatch_audit.snapshot(self.repo, [".hermes/orchestration"])
        self.assertEqual([], dispatch_audit.changed_paths(before, after))
        self.assertEqual([], dispatch_audit.audit(before=before, after=after, result=result(), editable_paths=["src/*"], path_matches=MATCH))


class WorkspaceAuditControllerTests(unittest.TestCase):
    """``sdd.workspace_audit_stop``: the controller's own wiring, with the policy mocked."""

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="sdd-audit-ctl-")
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        self.repo, self.run_dir = base / "repo", base / "run"
        for path in (self.repo, self.run_dir):
            path.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)], check=True, capture_output=True)
        for argv in (("config", "user.email", "t@e"), ("config", "user.name", "t")):
            subprocess.run(["git", "-C", str(self.repo), *argv], check=True, capture_output=True)
        (self.repo / "src").mkdir()
        (self.repo / "src" / "feature.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "init"], check=True, capture_output=True)
        self.final = self.run_dir / "T-IMPLEMENT-01.result.json"
        self.action = {"id": "T-IMPLEMENT-01", "stage": "IMPLEMENT", "final": self.final}
        self.ctx = mock.Mock(repo=self.repo, delivery={"slices": {"editable_paths": ["src/*"]}}, stage="IMPLEMENT",
                             runtime_dir=base / "runtime", state_path=base / "STATE.md", journal_path=base / "j.json", history_dir=base / "h")
        self.ctx.run_dir.return_value = self.run_dir

    def write_before(self) -> None:
        (self.run_dir / "T-IMPLEMENT-01.workspace-before.json").write_text(
            json.dumps(dispatch_audit.snapshot(self.repo)), encoding="utf-8")

    def test_off_returns_none_even_with_an_undeclared_change(self) -> None:
        self.write_before()
        (self.repo / "sneaky.py").write_text("x\n", encoding="utf-8")
        self.final.write_text(json.dumps(result("src/feature.py")), encoding="utf-8")
        with mock.patch.object(sdd, "workspace_audit_enabled", return_value=False):
            self.assertIsNone(sdd.workspace_audit_stop(self.ctx, self.action))

    def test_on_stops_with_the_registered_reason_and_lists_the_paths(self) -> None:
        self.write_before()
        (self.repo / "sneaky.py").write_text("x\n", encoding="utf-8")
        (self.repo / "src" / "feature.py").write_text("x = 2\n", encoding="utf-8")
        self.final.write_text(json.dumps(result("src/feature.py")), encoding="utf-8")
        with mock.patch.object(sdd, "workspace_audit_enabled", return_value=True), mock.patch.object(sdd, "stopped", lambda _ctx, payload: payload):
            payload = sdd.workspace_audit_stop(self.ctx, self.action)
        self.assertEqual("WORKSPACE_AUDIT_FAILED", payload["stop_reason"])
        self.assertEqual(["sneaky.py"], payload["undeclared"])
        self.assertIn("sneaky.py", payload["message"])

    def test_on_passes_a_clean_dispatch_and_fails_closed_without_a_snapshot(self) -> None:
        self.write_before()
        (self.repo / "src" / "feature.py").write_text("x = 2\n", encoding="utf-8")
        self.final.write_text(json.dumps(result("src/feature.py")), encoding="utf-8")
        with mock.patch.object(sdd, "workspace_audit_enabled", return_value=True), mock.patch.object(sdd, "stopped", lambda _ctx, payload: payload):
            self.assertIsNone(sdd.workspace_audit_stop(self.ctx, self.action))
            (self.run_dir / "T-IMPLEMENT-01.workspace-before.json").unlink()
            payload = sdd.workspace_audit_stop(self.ctx, self.action)
        self.assertEqual("AUDIT_UNVERIFIABLE", payload["findings"][0]["finding"])

    def test_a_read_only_stage_is_never_audited(self) -> None:
        with mock.patch.object(sdd, "executor_policy", return_value={"workspace_audit": "enforce"}):
            self.assertTrue(sdd.workspace_audit_enabled("IMPLEMENT"))
            self.assertTrue(sdd.workspace_audit_enabled("TEST"))
            for stage in ("SPECIFY", "CLARIFY", "PLAN", "TASKS", "REVIEW"):
                self.assertFalse(sdd.workspace_audit_enabled(stage))


if __name__ == "__main__":
    unittest.main()
