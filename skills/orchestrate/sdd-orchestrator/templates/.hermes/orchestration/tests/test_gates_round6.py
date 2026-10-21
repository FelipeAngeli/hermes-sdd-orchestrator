"""Regressions from the PR #62 sixth review round: ignored symlinks, caches written by gate commands, bytecode.

Do not edit, skip or weaken these tests; the implementation must satisfy them with real logic.
"""
from __future__ import annotations

import os
import py_compile
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate_fixtures import ANALYZE_CMD, FakeReviewer, artifact, check, git, make_repo  # noqa: E402

import gate_engine  # noqa: E402
import gate_reviewer  # noqa: E402
import gate_workspace  # noqa: E402

CACHE = f"app.{sys.implementation.cache_tag}.pyc"
CHECK = "import app, sys\nsys.exit(0 if app.total([1, 2, 3]) == 6 else 1)\n"


def commit_all(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


def wrong_same_size(source: bytes) -> bytes:
    wrong = source.replace(b"sum(values)", b"sum(values)-1", 1).replace(b"'total'", b"'tot'", 1)
    assert len(wrong) == len(source)
    return wrong


def compile_as(source: Path, body: bytes, cfile: Path) -> None:
    """Bytecode of ``body`` whose header matches ``source`` as it is on disk (same size, same mtime)."""
    original, info = source.read_bytes(), source.stat()
    source.write_bytes(body)
    os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns))
    cfile.parent.mkdir(parents=True, exist_ok=True)
    py_compile.compile(str(source), cfile=str(cfile), doraise=True)
    source.write_bytes(original)
    os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns))


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="gate-round6-")
        self.root = Path(self._tmp.name)
        self.repo = make_repo(self.root)
        (self.repo / "check_p.py").write_text(CHECK, encoding="utf-8")
        self.base = commit_all(self.repo, "slice base")
        self.command = f"{sys.executable} check_p.py"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def evaluate(self, engine: gate_engine.GateEngine | None = None) -> dict:
        engine = engine or self.engine
        context = {"implementer": "CODEX", "base_commit": self.base, "check_commands": {"AC-1": self.command}}
        value = artifact("TEST", acceptance_checks=[check("AC-1", command=self.command)],
                         commands=[{"command": self.command, "purpose": "focused tests", "timeout_seconds": 60, "exit_code": 0, "result": "PASS"}])
        return engine.evaluate("TEST", "T-1", value, context=context)

    @property
    def engine(self) -> gate_engine.GateEngine:
        if not hasattr(self, "_engine"):
            self._engine = gate_engine.GateEngine(self.root / "store", self.repo, commands={"focused_tests": self.command, "analyze": ANALYZE_CMD},
                                                  reviewers=[gate_reviewer.Reviewer("c", "CLAUDE", FakeReviewer())])
        return self._engine


class IgnoredSymlinkTests(Base):
    def test_an_ignored_symlink_into_git_cannot_keep_a_stale_pass(self):
        target = self.repo / ".git" / "cache.pyc"
        compile_as(self.repo / "app.py", (self.repo / "app.py").read_bytes(), target)
        (self.repo / "__pycache__").mkdir()
        os.symlink("../.git/cache.pyc", self.repo / "__pycache__" / CACHE)
        first = self.evaluate()
        compile_as(self.repo / "app.py", wrong_same_size((self.repo / "app.py").read_bytes()), target)
        second = self.evaluate()
        self.assertEqual("PASS", first["verdict"])
        self.assertFalse(second["reused"], "the symlink's target changed")

    def assert_only_the_ignored_digest_fails(self):
        """The scan itself is complete; only the ignored digest cannot vouch for what is under the ignored path."""
        self.assertTrue(gate_workspace.scan(self.repo, self.base)["complete"])
        found = gate_workspace.fingerprint(self.repo, self.base)
        self.assertEqual({}, found["changed"])
        self.assertIsNone(found["ignored"])
        self.assertFalse(found["complete"])

    def test_an_ignored_symlink_to_a_directory_makes_the_fingerprint_incomplete(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.repo / "__pycache__").mkdir()
        os.symlink(outside, self.repo / "__pycache__" / "linked")
        self.assert_only_the_ignored_digest_fails()

    def test_a_dangling_ignored_symlink_makes_the_fingerprint_incomplete(self):
        (self.repo / "__pycache__").mkdir()
        os.symlink(self.root / "missing.pyc", self.repo / "__pycache__" / CACHE)
        self.assert_only_the_ignored_digest_fails()

    def test_too_many_ignored_entries_make_the_fingerprint_incomplete(self):
        (self.repo / "__pycache__").mkdir()
        for index in range(20):
            (self.repo / "__pycache__" / f"{index}.pyc").write_bytes(b"x")
        # The walk sees 6 top-level entries (the ignored directory counts once); the digest sees 21.
        with mock.patch.object(gate_workspace, "MAX_ENTRIES", 10):
            self.assert_only_the_ignored_digest_fails()


class GitDirectoryTests(Base):
    def test_a_payload_kept_in_git_cannot_keep_a_stale_pass(self):
        payload = self.repo / ".git" / "info" / "t.py"
        payload.parent.mkdir(exist_ok=True)
        payload.write_text("def total(values):\n    return sum(values)\n", encoding="utf-8")
        (self.repo / "app.py").write_text("from pathlib import Path\nexec(Path(__file__).with_name('.git').joinpath('info', 't.py').read_text())\n",
                                          encoding="utf-8")
        first = self.evaluate()
        payload.write_text("def total(values):\n    return sum(values) - 1\n", encoding="utf-8")
        second = self.evaluate()
        self.assertEqual("PASS", first["verdict"])
        self.assertFalse(second["reused"], "the payload in .git changed")
        self.assertEqual("FAIL", second["verdict"])

    def test_an_untouched_git_directory_still_allows_reuse(self):
        first = self.evaluate()
        self.assertTrue(self.evaluate()["reused"])
        self.assertEqual(first["idempotency_key"], self.evaluate()["idempotency_key"])

    def test_a_payload_in_the_common_git_directory_of_a_linked_worktree_is_new_evidence(self):
        worktree = self.root / "linked"
        git(self.repo, "worktree", "add", "-q", str(worktree))
        before = gate_workspace.fingerprint(worktree, self.base)
        (self.repo / ".git" / "info" / "payload.py").write_text("x = 2\n", encoding="utf-8")
        after = gate_workspace.fingerprint(worktree, self.base)
        self.assertTrue(before["complete"])
        self.assertNotEqual(before["git_dir"], after["git_dir"], "the shared .git is where a linked worktree's objects and info live")

    def test_an_unwalkable_git_directory_makes_the_fingerprint_incomplete(self):
        self.assertTrue(gate_workspace.scan(self.repo, self.base)["complete"])
        with mock.patch.object(gate_workspace, "_git_dirs_digest", return_value=None):
            found = gate_workspace.fingerprint(self.repo, self.base)
        self.assertIsNotNone(found["ignored"])
        self.assertFalse(found["complete"])


class CommandCacheTests(Base):
    def test_a_cache_the_command_plants_cannot_be_served_as_reviewed(self):
        plant = ("import atexit, os, py_compile\n"
                 "def plant():\n"
                 "    src = 'app.py'; original = open(src, 'rb').read(); info = os.stat(src)\n"
                 "    wrong = original.replace(b'sum(values)', b'sum(values)-1', 1).replace(b\"'total'\", b\"'tot'\", 1)\n"
                 "    open(src, 'wb').write(wrong); os.utime(src, ns=(info.st_atime_ns, info.st_mtime_ns))\n"
                 f"    os.makedirs('__pycache__', exist_ok=True); py_compile.compile(src, cfile='__pycache__/{CACHE}')\n"
                 "    open(src, 'wb').write(original); os.utime(src, ns=(info.st_atime_ns, info.st_mtime_ns))\n"
                 "atexit.register(plant)\n")
        (self.repo / "check_p.py").write_text(plant + CHECK, encoding="utf-8")
        self.base = commit_all(self.repo, "a check that plants a cache")
        first = self.evaluate()
        second = self.evaluate()
        self.assertEqual("PASS", first["verdict"])
        self.assertFalse(second["reused"], "the command planted bytecode after it was evaluated")
        self.assertEqual("PASS", second["verdict"], "gate commands do not load bytecode from the repository")

    def test_a_planted_cache_is_not_loaded_by_a_fresh_evaluation(self):
        compile_as(self.repo / "app.py", (self.repo / "app.py").read_bytes(), self.repo / "__pycache__" / CACHE)
        source = self.repo / "app.py"
        info = source.stat()
        source.write_bytes(wrong_same_size(source.read_bytes()))
        os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns))
        self.assertEqual("FAIL", self.evaluate()["verdict"], "the broken source must be what runs, not the planted bytecode")


if __name__ == "__main__":
    unittest.main()
