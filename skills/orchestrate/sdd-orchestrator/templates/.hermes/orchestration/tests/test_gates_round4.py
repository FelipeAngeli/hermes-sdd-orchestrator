"""Regressions from the PR #62 fourth review round: content compared by the gate itself, not by git.

Do not edit, skip or weaken these tests; the implementation must satisfy them with real logic.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate_fixtures import ANALYZE_CMD, TEST_CMD, FakeReviewer, artifact, git, make_repo  # noqa: E402

import gate_engine  # noqa: E402
import gate_reviewer  # noqa: E402
import gate_workspace  # noqa: E402


def commit_all(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="gate-round4-")
        self.root = Path(self._tmp.name)
        self.repo = make_repo(self.root)
        (self.repo / "tag.py").write_text('TAG = "$Id$"\n', encoding="utf-8")
        (self.repo / "keep.log").write_text("kept\n", encoding="utf-8")
        with (self.repo / ".gitignore").open("a", encoding="utf-8") as handle:
            handle.write("*.log\n!keep.log\n")
        self.base = commit_all(self.repo, "slice base")
        self.marker = self.root / "program-ran"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def changed(self, base=True):
        return gate_workspace.changed_files(self.repo, self.base if base else None)

    def both(self):
        """The change set with and without a base: every hiding trick must fail in both modes."""
        return set(self.changed(True) or []), set(self.changed(False) or [])

    def evaluate_test(self, **context):
        engine = gate_engine.GateEngine(self.root / "store", self.repo, commands={"focused_tests": TEST_CMD, "analyze": ANALYZE_CMD},
                                        reviewers=[gate_reviewer.Reviewer("c", "CLAUDE", FakeReviewer())])
        context.setdefault("check_commands", {"AC-1": TEST_CMD})
        return engine.evaluate("TEST", "T-1", artifact("TEST"), context={"implementer": "CODEX", **context})


class AttributeAndFilterTests(Base):
    def test_a_filter_named_with_an_equals_sign_neither_runs_nor_hides_an_edit(self):
        with (self.repo / ".git" / "config").open("a", encoding="utf-8") as handle:
            handle.write(f'[filter "a=b"]\n\tclean = "sh -c \'touch {self.marker}; cat README.md\'"\n')
        (self.repo / ".gitattributes").write_text("app.py filter=a=b\n", encoding="utf-8")
        (self.repo / "app.py").write_text("raise SystemExit(3)\n", encoding="utf-8")
        with_base, without = self.both()
        self.assertIn("app.py", with_base)
        self.assertIn("app.py", without)
        self.assertFalse(self.marker.exists(), "no git call may run a filter program")

    def test_ident_and_eol_attributes_cannot_hide_content(self):
        for place in (".gitattributes", ".git/info/attributes"):
            with self.subTest(place):
                target = self.repo / place
                target.parent.mkdir(exist_ok=True)
                target.write_text("*.py ident\nREADME.md text eol=crlf\n", encoding="utf-8")
                (self.repo / "tag.py").write_text('TAG = "$Id: BACKDOOR payload of any length 12345 $"\n', encoding="utf-8")
                (self.repo / "README.md").write_text("# demo\r\nTotals.\r\n", encoding="utf-8")
                with_base, without = self.both()
                self.assertLessEqual({"tag.py", "README.md"}, with_base)
                self.assertLessEqual({"tag.py", "README.md"}, without)
                target.unlink()

    def test_a_repository_attributes_file_cannot_hide_content(self):
        attributes = self.root / "attrs"
        attributes.write_text("tag.py ident\n", encoding="utf-8")
        git(self.repo, "config", "core.attributesFile", str(attributes))
        (self.repo / "tag.py").write_text('TAG = "$Id: hidden $"\n', encoding="utf-8")
        self.assertIn("tag.py", self.changed())


class CacheAndIndexTests(Base):
    def test_a_refreshed_stat_cache_cannot_hide_a_same_size_edit(self):
        time.sleep(1.2)  # past the racy-timestamp window, so git would trust its cached stat data
        git(self.repo, "status", "--porcelain")
        git(self.repo, "config", "core.checkStat", "minimal")
        git(self.repo, "config", "core.trustctime", "false")
        target = self.repo / "README.md"
        before = target.stat()
        target.write_bytes(target.read_bytes().replace(b"Totals", b"Totalz"))
        os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
        with_base, without = self.both()
        self.assertIn("README.md", with_base)
        self.assertIn("README.md", without)

    def test_index_flags_hide_nothing_with_a_base(self):
        for flag in ("--skip-worktree", "--assume-unchanged"):
            with self.subTest(flag):
                git(self.repo, "update-index", flag, "README.md")
                (self.repo / "README.md").write_text(f"edited {flag}\n", encoding="utf-8")
                self.assertIn("README.md", self.changed())

    def test_a_staged_new_file_and_a_committed_rename_are_reported(self):
        (self.repo / "new.py").write_text("x = 1\n", encoding="utf-8")
        git(self.repo, "add", "new.py")
        git(self.repo, "mv", "README.md", "docs.md")
        git(self.repo, "commit", "-q", "-m", "rename")
        self.assertLessEqual({"new.py", "README.md", "docs.md"}, set(self.changed()))


class TreeShapeTests(Base):
    def test_an_embedded_repository_is_walked_not_collapsed(self):
        nested = self.repo / "tests"
        nested.mkdir()
        git(nested, "init", "-q")
        (nested / "conftest.py").write_text("x = 1\n", encoding="utf-8")
        first = gate_workspace.fingerprint(self.repo, self.base)
        self.assertIn("tests/conftest.py", first["changed"])
        (nested / "conftest.py").write_text("import os  # payload\n", encoding="utf-8")
        second = gate_workspace.fingerprint(self.repo, self.base)
        self.assertNotEqual(first["changed"]["tests/conftest.py"], second["changed"]["tests/conftest.py"])

    def test_a_base_negation_rule_is_honoured(self):
        (self.repo / "sub").mkdir()
        (self.repo / "debug.log").write_text("noise\n", encoding="utf-8")
        (self.repo / "sub" / "keep.log").write_text("untracked, re-included by !keep.log\n", encoding="utf-8")
        changed = set(self.changed())
        self.assertIn("sub/keep.log", changed)
        self.assertNotIn("debug.log", changed)

    def test_a_tracked_file_replaced_by_a_directory_or_fifo_is_changed(self):
        (self.repo / "README.md").unlink()
        (self.repo / "README.md").mkdir()
        os.mkfifo(self.repo / "tag.py.fifo")
        (self.repo / "tag.py").unlink()
        os.rename(self.repo / "tag.py.fifo", self.repo / "tag.py")
        self.assertLessEqual({"README.md", "tag.py"}, set(self.changed()))

    def test_files_inside_a_nested_git_directory_are_reported(self):
        hidden = self.repo / "tests" / ".git"
        hidden.mkdir(parents=True)
        (hidden / "payload.py").write_text("import os\n", encoding="utf-8")
        self.assertIn("tests/.git/payload.py", self.changed())

    def test_a_check_ignore_failure_is_unreadable_not_clean(self):
        (self.repo / "new.py").write_text("x = 1\n", encoding="utf-8")
        real = gate_workspace.git
        def failing(repo, *args, **kwargs):
            return None if args[:1] == ("check-ignore",) else real(repo, *args, **kwargs)
        with mock.patch.object(gate_workspace, "git", failing):
            found = gate_workspace.scan(self.repo, self.base)
        self.assertIsNone(found["changed"])

    def test_repository_config_cannot_borrow_a_trusted_ignore_file(self):
        (self.repo / "sub").mkdir(exist_ok=True)
        (self.repo / "sub" / ".gitignore").write_text("*.py\n", encoding="utf-8")
        self.base = commit_all(self.repo, "sub ignore")
        (self.repo / "new.py").write_text("x = 1\n", encoding="utf-8")
        git(self.repo, "config", "core.excludesFile", "sub/.gitignore")
        self.assertIn("new.py", self.changed())
        git(self.repo, "config", "--unset", "core.excludesFile")
        decoy = self.root / "decoy"
        decoy.mkdir()
        (decoy / ".gitignore").write_text("*\n", encoding="utf-8")
        git(self.repo, "config", "core.worktree", str(decoy))
        self.assertIn("new.py", self.changed())

    def test_an_unreadable_new_file_makes_the_fingerprint_incomplete(self):
        secret = self.repo / "secret.py"
        secret.write_text("x = 1\n", encoding="utf-8")
        os.chmod(secret, 0)
        try:
            found = gate_workspace.fingerprint(self.repo, self.base)
        finally:
            os.chmod(secret, 0o600)
        self.assertEqual("unreadable", found["changed"]["secret.py"])
        self.assertFalse(found["complete"])

    def test_walk_and_byte_limits_make_the_scan_incomplete(self):
        with mock.patch.object(gate_workspace, "MAX_ENTRIES", 2):
            self.assertIsNone(gate_workspace.scan(self.repo, self.base)["changed"])
        with mock.patch.object(gate_workspace, "MAX_SCAN_BYTES", 10):
            self.assertFalse(gate_workspace.scan(self.repo, self.base)["complete"])
        self.assertTrue(gate_workspace.scan(self.repo, self.base)["complete"])

    def test_a_submodule_blocks_the_scope_check(self):
        library = self.root / "lib"
        library.mkdir()
        git(library, "init", "-q")
        git(library, "config", "user.email", "t@example.invalid")
        git(library, "config", "user.name", "t")
        (library / "lib.py").write_text("y = 1\n", encoding="utf-8")
        commit_all(library, "lib")
        git(self.repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(library), "vendor/lib")
        base = commit_all(self.repo, "submodule")
        found = gate_workspace.scan(self.repo, base)
        self.assertIsNone(found["changed"])
        self.assertFalse(found["complete"])
        engine = gate_engine.GateEngine(self.root / "store", self.repo, commands={"analyze": ANALYZE_CMD}, reviewers=[])
        decision = engine.evaluate("IMPLEMENT", "T-1", artifact("IMPLEMENT"), context={"base_commit": base, "editable_paths": ["app.py"]})
        status = {item["id"]: item["status"] for item in decision["criteria"]}
        self.assertEqual("BLOCKED", status["WITHIN_EDITABLE_PATHS"])

    def test_no_git_call_reads_content_or_the_index(self):
        hook = self.repo / ".git" / "hooks" / "post-index-change"
        hook.parent.mkdir(exist_ok=True)
        hook.write_text(f"#!/bin/sh\ntouch {self.marker}\n", encoding="utf-8")
        os.chmod(hook, 0o700)
        git(self.repo, "config", "core.fsmonitor", f"sh -c 'touch {self.marker}'")
        (self.repo / "app.py").write_text("x = 2\n", encoding="utf-8")
        self.both()
        gate_workspace.fingerprint(self.repo)
        self.assertFalse(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
