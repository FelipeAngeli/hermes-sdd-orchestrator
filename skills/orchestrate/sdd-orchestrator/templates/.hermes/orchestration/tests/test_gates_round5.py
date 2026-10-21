"""Regressions from the PR #62 fifth review round: verified objects, symlinks, ignored caches, scan limits.

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
from gate_fixtures import ANALYZE_CMD, TEST_CMD, FakeReviewer, artifact, git, make_repo, write_app  # noqa: E402

import gate_engine  # noqa: E402
import gate_reviewer  # noqa: E402
import gate_validator  # noqa: E402
import gate_workspace  # noqa: E402


def run(repo: Path, *args: str, data: bytes | None = None) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], input=data, capture_output=True, check=True).stdout.decode().strip()


def commit_all(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return run(repo, "rev-parse", "HEAD")


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="gate-round5-")
        self.root = Path(self._tmp.name)
        self.repo = make_repo(self.root)
        self.base = commit_all(self.repo, "slice base")
        self.marker = self.root / "program-ran"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def engine(self) -> gate_engine.GateEngine:
        return gate_engine.GateEngine(self.root / "store", self.repo, commands={"focused_tests": TEST_CMD, "analyze": ANALYZE_CMD},
                                      reviewers=[gate_reviewer.Reviewer("c", "CLAUDE", FakeReviewer())])

    def evaluate_test(self, engine: gate_engine.GateEngine) -> dict:
        context = {"implementer": "CODEX", "base_commit": self.base, "check_commands": {"AC-1": TEST_CMD}}
        return engine.evaluate("TEST", "T-1", artifact("TEST"), context=context)


class ObjectStoreTests(Base):
    def test_a_rewritten_tree_object_is_refused_not_trusted(self):
        (self.repo / "src").mkdir()
        (self.repo / "src" / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        self.base = commit_all(self.repo, "src")
        (self.repo / "src" / "app.py").write_text("def f():\n    return 2  # backdoor\n", encoding="utf-8")
        blob = run(self.repo, "hash-object", "-w", "src/app.py")
        old_tree = run(self.repo, "rev-parse", f"{self.base}:src")
        new_tree = run(self.repo, "mktree", data=f"100644 blob {blob}\tapp.py\n".encode())
        objects = self.repo / ".git" / "objects"
        target = objects / old_tree[:2] / old_tree[2:]
        os.chmod(target, 0o644)
        target.write_bytes((objects / new_tree[:2] / new_tree[2:]).read_bytes())
        self.assertEqual(blob, run(self.repo, "rev-parse", f"{self.base}:src/app.py"), "the swap must fool plain git")
        for base in (self.base, None):
            with self.subTest(base=base):
                found = gate_workspace.scan(self.repo, base)
                self.assertIsNone(found["changed"])
                self.assertFalse(found["complete"])

    def test_a_promisor_lazy_fetch_does_not_run_the_workers_ssh_command(self):
        for key, value in (("extensions.partialClone", "origin"), ("remote.origin.promisor", "true"),
                           ("remote.origin.url", "ssh://example.invalid/x"), ("core.sshCommand", f"touch {self.marker}; false")):
            git(self.repo, "config", key, value)
        tree = run(self.repo, "rev-parse", f"{self.base}^{{tree}}")
        (self.repo / ".git" / "objects" / tree[:2] / tree[2:]).unlink()
        for base in (self.base, None):
            self.assertIsNone(gate_workspace.scan(self.repo, base)["changed"])
        self.assertFalse(self.marker.exists())


class SymlinkTests(Base):
    def test_a_changed_symlink_never_lets_a_pass_be_reused(self):
        outside = self.root / "outside"
        outside.mkdir()
        os.replace(self.repo / "app.py", outside / "app.py")
        os.symlink(outside / "app.py", self.repo / "app.py")
        engine = self.engine()
        first = self.evaluate_test(engine)
        write_app(outside, correct=False)
        second = self.evaluate_test(engine)
        self.assertEqual("PASS", first["verdict"])
        self.assertFalse(second["reused"], "the symlink's target changed")
        self.assertEqual("FAIL", second["verdict"])

    def test_a_tracked_directory_replaced_by_a_symlink_is_reported(self):
        (self.repo / "vendor").mkdir()
        (self.repo / "vendor" / "lib.py").write_text("x = 1\n", encoding="utf-8")
        with (self.repo / ".gitignore").open("a", encoding="utf-8") as handle:
            handle.write("vendor\n")
        git(self.repo, "add", "-f", "vendor/lib.py", ".gitignore")
        git(self.repo, "commit", "-q", "-m", "vendored")
        self.base = run(self.repo, "rev-parse", "HEAD")
        decoy = self.root / "decoy"
        decoy.mkdir()
        (decoy / "lib.py").write_text("x = 1\n", encoding="utf-8")
        (decoy / "injected.py").write_text("import os\n", encoding="utf-8")
        (self.repo / "vendor" / "lib.py").unlink()
        (self.repo / "vendor").rmdir()
        os.symlink(decoy, self.repo / "vendor")
        found = gate_workspace.scan(self.repo, self.base)
        self.assertIn("vendor", found["changed"])
        self.assertIn("vendor/lib.py", found["changed"])

    def test_an_ignored_directory_that_holds_tracked_files_hides_nothing(self):
        (self.repo / "vendor").mkdir()
        (self.repo / "vendor" / "lib.py").write_text("x = 1\n", encoding="utf-8")
        with (self.repo / ".gitignore").open("a", encoding="utf-8") as handle:
            handle.write("vendor\n")
        git(self.repo, "add", "-f", "vendor/lib.py", ".gitignore")
        git(self.repo, "commit", "-q", "-m", "vendored")
        self.base = run(self.repo, "rev-parse", "HEAD")
        (self.repo / "vendor" / "injected.py").write_text("import os\n", encoding="utf-8")
        self.assertIn("vendor/injected.py", gate_workspace.scan(self.repo, self.base)["changed"])


class IgnoredCacheTests(Base):
    def test_a_planted_bytecode_cache_is_new_evidence(self):
        engine = self.engine()
        first = self.evaluate_test(engine)
        source = self.repo / "app.py"
        original, stat = source.read_bytes(), source.stat()
        wrong = original.replace(b"sum(values)", b"sum(values)-1", 1).replace(b"'total'", b"'tot'", 1)
        self.assertEqual(len(original), len(wrong), "the planted source must have the same size")
        source.write_bytes(wrong)
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        py_compile.compile(str(source), cfile=str(self.repo / "__pycache__" / f"app.{sys.implementation.cache_tag}.pyc"), doraise=True)
        source.write_bytes(original)
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        second = self.evaluate_test(engine)
        self.assertEqual("PASS", first["verdict"])
        self.assertFalse(second["reused"], "a planted cache in an ignored path must be new evidence")

    def test_the_gates_own_caches_do_not_prevent_reuse(self):
        engine = self.engine()
        first = self.evaluate_test(engine)
        second = self.evaluate_test(engine)
        self.assertEqual("PASS", first["verdict"])
        self.assertTrue(second["reused"])


class LimitTests(Base):
    def test_running_out_of_scan_budget_is_inconclusive_not_a_partial_pass(self):
        (self.repo / "a_big.bin").write_bytes(b"\0" * 64)
        (self.repo / "z_secret.py").write_text("x = 1\n", encoding="utf-8")
        self.base = commit_all(self.repo, "big")
        (self.repo / "z_secret.py").write_text("x = 2\n", encoding="utf-8")
        with mock.patch.object(gate_workspace, "MAX_SCAN_BYTES", 32):
            found = gate_workspace.scan(self.repo, self.base)
            self.assertIsNone(found["changed"])
            engine = gate_engine.GateEngine(self.root / "store", self.repo, commands={"analyze": ANALYZE_CMD}, reviewers=[])
            decision = engine.evaluate("IMPLEMENT", "T-1", artifact("IMPLEMENT"),
                                       context={"base_commit": self.base, "editable_paths": ["docs/**"]})
        status = {item["id"]: item["status"] for item in decision["criteria"]}
        self.assertEqual("INCONCLUSIVE", status["WITHIN_EDITABLE_PATHS"])
        self.assertTrue(decision["retryable"])
        self.assertIs(gate_validator.gate_workspace, gate_workspace)


if __name__ == "__main__":
    unittest.main()
