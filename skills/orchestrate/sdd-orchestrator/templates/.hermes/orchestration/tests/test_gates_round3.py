"""Regressions from the PR #62 third review round: hidden edits, worker-written ignore rules, claim spellings, untested rules.

Do not edit, skip or weaken these tests; the implementation must satisfy them with real logic.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate_fixtures import ANALYZE_CMD, PY, TEST_CMD, FakeReviewer, artifact, git, make_repo, review_artifact, write_app  # noqa: E402

import gate_engine  # noqa: E402
import gate_reviewer  # noqa: E402
import gate_stages  # noqa: E402
import gate_validator  # noqa: E402
import gate_workspace  # noqa: E402


def commit_all(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="gate-round3-")
        self.root = Path(self._tmp.name)
        self.repo = make_repo(self.root)
        self.base = commit_all(self.repo, "slice base")  # app.py correct, committed
        self.store = self.root / "store"
        self.reviewer = FakeReviewer()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def engine(self, reviewers=None, **kwargs):
        commands = {"focused_tests": TEST_CMD, "analyze": ANALYZE_CMD, "format": None}
        commands.update(kwargs.pop("commands", {}))
        reviewers = [gate_reviewer.Reviewer("claude-review", "CLAUDE", self.reviewer)] if reviewers is None else reviewers
        return gate_engine.GateEngine(self.store, self.repo, commands=commands, reviewers=reviewers, **kwargs)

    def evaluate(self, stage, value, engine=None, **context):
        context.setdefault("check_commands", {"AC-1": TEST_CMD})
        context.setdefault("implementer", "CODEX")
        return (engine or self.engine()).evaluate(stage, "T-1", value, context=context)

    def status(self, decision):
        return {item["id"]: item for item in decision["criteria"]}

    def changed(self):
        return set(gate_workspace.changed_files(self.repo, self.base))


class HiddenEditTests(Base):
    def test_index_flags_do_not_let_a_stale_pass_be_reused(self):
        for flag in ("--skip-worktree", "--assume-unchanged"):
            with self.subTest(flag):
                write_app(self.repo, correct=True)
                git(self.repo, "update-index", "--no-skip-worktree", "--no-assume-unchanged", "app.py")
                first = self.evaluate("TEST", artifact("TEST"))
                git(self.repo, "update-index", flag, "app.py")
                write_app(self.repo, correct=False)
                second = self.evaluate("TEST", artifact("TEST"))
                self.assertFalse(second["reused"], "an index flag must not hide an edit from the fingerprint")
                self.assertEqual("FAIL", second["verdict"])
                self.assertEqual("PASS", first["verdict"])

    def test_a_repository_core_worktree_cannot_point_the_scan_elsewhere(self):
        decoy = self.root / "decoy"
        shutil.copytree(self.repo, decoy, ignore=shutil.ignore_patterns(".git"))
        (self.repo / "README.md").write_text("out of scope\n", encoding="utf-8")
        git(self.repo, "config", "core.worktree", str(decoy))
        self.assertIn("README.md", self.changed())
        self.assertIn("README.md", gate_workspace.changed_files(self.repo))

    def test_a_filter_in_worktree_config_does_not_run(self):
        marker = self.root / "wt-filter-ran"
        git(self.repo, "config", "extensions.worktreeConfig", "true")
        git(self.repo, "config", "--worktree", "filter.wt.clean", f"sh -c 'touch {marker}; cat'")
        (self.repo / ".git" / "info").mkdir(exist_ok=True)
        (self.repo / ".git" / "info" / "attributes").write_text("README.md filter=wt\n", encoding="utf-8")
        (self.repo / "README.md").write_text("edited\n", encoding="utf-8")
        self.assertIn("README.md", self.changed())
        self.assertFalse(marker.exists())

    def test_replace_refs_cannot_swap_the_base(self):
        (self.repo / "README.md").write_text("out of scope\n", encoding="utf-8")
        commit_all(self.repo, "out of scope")
        tree = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD^{tree}"], capture_output=True, text=True, check=True).stdout.strip()
        fake = subprocess.run(["git", "-C", str(self.repo), "commit-tree", tree, "-m", "fake"], capture_output=True, text=True, check=True).stdout.strip()
        git(self.repo, "replace", self.base, fake)
        self.assertIn("README.md", self.changed())

    def test_stat_cache_settings_cannot_hide_a_same_size_edit(self):
        git(self.repo, "config", "core.checkStat", "minimal")
        git(self.repo, "config", "core.trustctime", "false")
        target = self.repo / "README.md"
        before = target.stat()
        target.write_text(target.read_text(encoding="utf-8").replace("Totals", "Totalz"), encoding="utf-8")
        os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertIn("README.md", self.changed())
        self.assertIn("README.md", gate_workspace.changed_files(self.repo), "git status without a base too")

    def test_core_filemode_false_cannot_hide_a_mode_change(self):
        git(self.repo, "config", "core.fileMode", "false")
        os.chmod(self.repo / "check_total.py", 0o755)
        self.assertIn("check_total.py", gate_workspace.changed_files(self.repo))
        self.assertIn("check_total.py", self.changed())

    def test_a_worker_written_ignore_rule_hides_nothing_but_base_rules_still_apply(self):
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / ".gitignore").write_text("*\n", encoding="utf-8")
        (self.repo / "tests" / "conftest.py").write_text("x = 1\n", encoding="utf-8")
        (self.repo / "__pycache__").mkdir(exist_ok=True)
        (self.repo / "__pycache__" / "app.pyc").write_bytes(b"\0")
        changed = self.changed()
        self.assertLessEqual({"tests/conftest.py", "tests/.gitignore"}, changed)
        self.assertNotIn("__pycache__/app.pyc", changed, "a rule of an unchanged base .gitignore still applies")
        with (self.repo / ".gitignore").open("a", encoding="utf-8") as handle:
            handle.write("sneaky.py\n")
        (self.repo / "sneaky.py").write_text("y = 2\n", encoding="utf-8")
        changed = self.changed()
        self.assertLessEqual({"sneaky.py", ".gitignore"}, changed, "an edited .gitignore is not trusted")

    def test_a_moved_ref_or_short_base_is_refused(self):
        git(self.repo, "branch", "slice-base", self.base)
        self.assertIsNone(gate_workspace.changed_files(self.repo, "slice-base"))
        self.assertIsNone(gate_workspace.changed_files(self.repo, self.base[:12]))
        git(self.repo, "switch", "-q", "-c", "side")
        (self.repo / "side.txt").write_text("side\n", encoding="utf-8")
        side = commit_all(self.repo, "side commit")
        git(self.repo, "switch", "-q", "-")
        self.assertIsNone(gate_workspace.changed_files(self.repo, side), "a commit in the repository that is not an ancestor of HEAD")
        decision = self.evaluate("IMPLEMENT", artifact("IMPLEMENT", modified_paths=[]), base_commit="slice-base", editable_paths=["app.py"])
        self.assertEqual("BLOCKED", self.status(decision)["WITHIN_EDITABLE_PATHS"]["status"])

    def test_an_unreadable_or_special_file_makes_the_fingerprint_incomplete(self):
        locked = self.repo / "locked.txt"
        locked.write_text("x\n", encoding="utf-8")
        with mock.patch.object(Path, "open", side_effect=PermissionError("denied")):
            self.assertFalse(gate_workspace.fingerprint(self.repo)["complete"])
        os.mkfifo(self.repo / "pipe")
        self.assertEqual("special", gate_workspace._hash(self.repo / "pipe"))

    def test_too_many_files_or_no_head_makes_the_fingerprint_incomplete(self):
        (self.repo / "extra.txt").write_text("x\n", encoding="utf-8")
        (self.repo / "more.txt").write_text("y\n", encoding="utf-8")
        with mock.patch.object(gate_workspace, "MAX_FILES", 1):
            self.assertFalse(gate_workspace.fingerprint(self.repo)["complete"])
        self.assertTrue(gate_workspace.fingerprint(self.repo)["complete"])
        fresh = self.root / "fresh"
        fresh.mkdir()
        git(fresh, "init", "-q")
        self.assertFalse(gate_workspace.fingerprint(fresh)["complete"])

    def test_hooks_and_external_diff_do_not_run(self):
        marker = self.root / "program-ran"
        hook = self.repo / ".git" / "hooks" / "post-index-change"
        hook.parent.mkdir(exist_ok=True)
        hook.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
        os.chmod(hook, 0o700)
        git(self.repo, "config", "diff.external", f"sh -c 'touch {marker}'")
        (self.repo / ".gitattributes").write_text("*.md diff=evil\n", encoding="utf-8")
        git(self.repo, "config", "diff.evil.command", f"sh -c 'touch {marker}'")
        (self.repo / "README.md").write_text("edited\n", encoding="utf-8")
        self.changed()
        gate_workspace.changed_files(self.repo)
        self.assertFalse(marker.exists())


class ClaimAndReviewTests(Base):
    def test_a_disallowed_spelling_cannot_mask_a_false_claim_about_a_configured_command(self):
        (self.repo / "lint.py").write_text("raise SystemExit(1)\n", encoding="utf-8")
        engine = self.engine(commands={"analyze": f"{PY} lint.py"})
        claims = [{"command": f"{PY} ./lint.py", "purpose": "variant", "timeout_seconds": 5, "exit_code": 0, "result": "PASS"},
                  {"command": f"{PY} lint.py", "purpose": "analyze", "timeout_seconds": 5, "exit_code": 0, "result": "PASS"}]
        decision = self.evaluate("TEST", artifact("TEST", commands=claims, acceptance_checks=[]), engine=engine, check_commands={})
        self.assertEqual("FAIL", self.status(decision)["EVIDENCE_REPRODUCED"]["status"])

    def test_a_claim_only_in_a_disallowed_spelling_is_rerun_as_the_configured_command(self):
        (self.repo / "lint.py").write_text("raise SystemExit(1)\n", encoding="utf-8")
        engine = self.engine(commands={"analyze": f"{PY} lint.py"})
        claim = [{"command": f"{PY} ./lint.py", "purpose": "variant", "timeout_seconds": 5, "exit_code": 0, "result": "PASS"}]
        decision = self.evaluate("TEST", artifact("TEST", commands=claim, acceptance_checks=[]), engine=engine, check_commands={})
        self.assertEqual("FAIL", self.status(decision)["EVIDENCE_REPRODUCED"]["status"])

    def test_an_unreadable_index_is_inconclusive_not_clean(self):
        with mock.patch.object(gate_workspace, "hidden_by_index", return_value=None):
            decision = self.evaluate("IMPLEMENT", artifact("IMPLEMENT", modified_paths=["app.py"]), editable_paths=["app.py"])
        self.assertEqual("INCONCLUSIVE", self.status(decision)["WITHIN_EDITABLE_PATHS"]["status"])

    def test_spellings_of_one_command_count_once_against_the_cap(self):
        for index in range(gate_validator.MAX_REPRODUCED):
            (self.repo / f"f{index}.txt").write_text("x\n", encoding="utf-8")
        claims = [{"command": spelled, "purpose": "p", "timeout_seconds": 5, "exit_code": 0, "result": "PASS"}
                  for index in range(gate_validator.MAX_REPRODUCED)
                  for spelled in (f"test -f f{index}.txt", f"test  -f  ./f{index}.txt")]
        decision = self.evaluate("TEST", artifact("TEST", commands=claims, acceptance_checks=[]), check_commands={})
        self.assertEqual("PASS", self.status(decision)["EVIDENCE_REPRODUCED"]["status"])

    def test_review_without_an_expected_set_is_blocked(self):
        decision = self.engine().evaluate("REVIEW", "T-1", review_artifact(), context={"implementer": "CODEX"})
        self.assertEqual("BLOCKED", self.status(decision)["EXPECTED_CHECKS_STATED"]["status"])

    def test_padded_non_blocking_severities_still_pass(self):
        self.assertEqual("LOW", gate_stages.severity_key("  low \n"))
        value = review_artifact(findings=[{"severity": "  low ", "path": "app.py", "description": "d", "evidence": "e"}])
        self.assertEqual("PASS", self.status(self.evaluate("REVIEW", value))["REVIEW_APPROVED"]["status"])

    def test_glued_and_lookalike_names_map_to_a_vendor_or_none(self):
        self.assertEqual("openai", gate_reviewer.family("codexcli"))
        self.assertEqual("anthropic", gate_reviewer.family("claudecode"))
        self.assertEqual("openai", gate_reviewer.family("gpt4o"))
        self.assertIsNone(gate_reviewer.family("\u0441odex"))


if __name__ == "__main__":
    unittest.main()
