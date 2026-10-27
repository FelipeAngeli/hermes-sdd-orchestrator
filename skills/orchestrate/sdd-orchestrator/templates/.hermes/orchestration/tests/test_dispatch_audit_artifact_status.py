"""The workspace audit leaves out a test-run artifact only when the baseline proves it was ignored.

Round-1 review of the test-run artifact list (``runtime/audit_scope.py``):

* a deleted path is left out only when it was git-ignored in the **baseline**; an untracked
  but unignored ``coverage.xml``, ``htmlcov/x.html`` or ``.pytest_cache/x`` that disappears is
  reported. A baseline stored before the snapshot recorded per-path status (15.19.0, or the
  first head of this change) is resolved with ``git check-ignore --no-index``;
* an artifact tree (``.pytest_cache/``, ``htmlcov/``) qualifies only when the directory itself
  was ignored by the baseline rules from outside the tree, so a worker-written
  ``.pytest_cache/.gitignore`` with ``*`` neither hides the tree nor is skipped itself.

The FP-006 scenario (the project's own ``.gitignore`` ignores the caches it produces) must
still report only the real undeclared documents, fresh and after an upgrade.
"""
from __future__ import annotations

import py_compile
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ORCHESTRATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ORCHESTRATION / "runtime"))

import dispatch_audit  # noqa: E402
import validate_protocol  # noqa: E402

#: The project's own .gitignore in the FP-006 incident, unchanged across the upgrade.
FP006_IGNORE = ".hermes/\n__pycache__/\n*.pyc\n.coverage\ncoverage.xml\n.pytest_cache/\n"
LEGACY_KEYS = ("status", "artifact_roots", "test_artifacts")


class ArtifactStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="sdd-audit-status-")
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name).resolve() / "repo"
        self.repo.mkdir()
        for argv in (("init", "-q", "-b", "main"), ("config", "user.email", "t@e"), ("config", "user.name", "t")):
            self.git(*argv)
        self.write("src/feature.py", "x = 1\n")
        self.write("tests/test_feature.py", "def test_x():\n    assert True\n")

    def git(self, *arguments: str) -> None:
        subprocess.run(["git", "-C", str(self.repo), *arguments], check=True, capture_output=True)

    def commit(self, ignore: str) -> None:
        self.write(".gitignore", ignore)
        self.git("add", ".")
        self.git("commit", "-qm", "init")

    def write(self, relative: str, text: str) -> Path:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def findings(self, before: dict, *declared: str) -> list[tuple[str, str]]:
        after = dispatch_audit.snapshot(self.repo, baseline=before)
        result = {"executor_result": {"modified_paths": [{"path": path} for path in declared], "created_paths": []}}
        found = dispatch_audit.audit(before=before, after=after, result=result, editable_paths=[], path_matches=validate_protocol.path_matches)
        return sorted((item["path"], item["finding"]) for item in found)

    def legacy(self) -> dict:
        """A baseline as 15.19.0 (or this change's first head) stored it: no per-path status, no roots."""
        snapshot = dispatch_audit.snapshot(self.repo)
        for key in LEGACY_KEYS:
            snapshot.pop(key, None)
        return snapshot

    def simulate_test_run(self, generation: str) -> None:
        """What one test run with coverage leaves behind, with real interpreter bytecode."""
        self.write(".pytest_cache/.gitignore", "# Created by pytest automatically.\n*\n")
        self.write(".pytest_cache/CACHEDIR.TAG", "Signature: 8a477f597d28d172789f06886806bc55\n")
        self.write(".pytest_cache/v/cache/nodeids", f"[\"{generation}\"]\n")
        self.write(".coverage", f"sqlite {generation}")
        self.write("coverage.xml", f"<coverage run='{generation}'/>\n")
        tag = sys.implementation.cache_tag
        for source, name in (("src/feature.py", f"feature.{tag}.pyc"), ("tests/test_feature.py", f"test_feature.{tag}-pytest-9.1.1.pyc")):
            py_compile.compile(str(self.repo / source), cfile=str(self.repo / Path(source).parent / "__pycache__" / name), doraise=True)

    # -- M1: a deleted artifact counts unless the baseline had it ignored ---------------------
    def test_an_untracked_unignored_artifact_that_is_deleted_is_reported(self) -> None:
        self.commit(".hermes/\n")
        for relative in ("coverage.xml", ".coverage.host.1", "htmlcov/x.html", ".pytest_cache/x"):
            with self.subTest(path=relative):
                self.write(relative, "data\n")
                for baseline in (dispatch_audit.snapshot(self.repo), self.legacy()):
                    (self.repo / relative).unlink(missing_ok=True)
                    self.assertEqual([(relative, "UNDECLARED_CHANGE")], self.findings(baseline))
                    self.write(relative, "data\n")
                (self.repo / relative).unlink()

    def test_an_ignored_artifact_that_is_deleted_is_not_reported(self) -> None:
        self.commit(".hermes/\ncoverage.xml\n.coverage.*\nhtmlcov/\n.pytest_cache/\n")
        files = ("coverage.xml", ".coverage.host.1", "htmlcov/x.html", ".pytest_cache/x")
        for baseline_kind in ("current", "legacy"):
            with self.subTest(baseline=baseline_kind):
                for relative in files:
                    self.write(relative, "data\n")
                baseline = dispatch_audit.snapshot(self.repo) if baseline_kind == "current" else self.legacy()
                for relative in files:
                    (self.repo / relative).unlink()
                self.assertEqual([], self.findings(baseline))

    def test_the_snapshot_records_each_path_status(self) -> None:
        self.commit(".hermes/\ncoverage.xml\n")
        self.write("coverage.xml", "<coverage/>\n")
        self.write("notes.md", "draft\n")
        self.write("src/feature.py", "x = 2\n")
        status = dispatch_audit.snapshot(self.repo)["status"]
        self.assertEqual({"coverage.xml": "!!", "notes.md": "??", "src/feature.py": " M"}, status)

    # -- L1: an artifact tree must be ignored from outside it in the baseline -------------------
    def test_a_worker_written_cache_gitignore_does_not_make_an_unignored_tree_eligible(self) -> None:
        self.commit(".hermes/\n")
        for tree in ("htmlcov", ".pytest_cache"):
            with self.subTest(tree=tree):
                before = dispatch_audit.snapshot(self.repo)
                self.write(f"{tree}/.gitignore", "*\n")
                self.write(f"{tree}/v/data", "x\n")
                expected = [(f"{tree}/.gitignore", "IGNORE_RULES_CHANGED"), (f"{tree}/v/data", "UNDECLARED_CHANGE")]
                self.assertEqual(expected, self.findings(before))
                self.assertEqual(expected, self.findings(self.legacy_from(before)))
                for relative in (f"{tree}/.gitignore", f"{tree}/v/data"):
                    (self.repo / relative).unlink()

    def legacy_from(self, snapshot: dict) -> dict:
        return {key: value for key, value in snapshot.items() if key not in LEGACY_KEYS}

    def test_the_cache_gitignore_is_skipped_only_when_the_tree_was_already_ignored(self) -> None:
        self.commit(".hermes/\n.pytest_cache/\n")
        before = dispatch_audit.snapshot(self.repo)
        self.assertEqual([".pytest_cache"], before["artifact_roots"])
        self.write(".pytest_cache/.gitignore", "*\n")
        self.write(".pytest_cache/v/cache/nodeids", "[]\n")
        self.assertEqual([], self.findings(before))

    # -- FP-006 end to end: only the real undeclared documents --------------------------------------
    def test_fp006_reports_only_the_undeclared_docs_fresh_and_after_an_upgrade(self) -> None:
        self.commit(FP006_IGNORE)
        self.simulate_test_run("1")
        for baseline_kind in ("current", "legacy"):
            with self.subTest(baseline=baseline_kind):
                baseline = dispatch_audit.snapshot(self.repo) if baseline_kind == "current" else self.legacy()
                self.write("src/feature.py", f"x = '{baseline_kind}'\n")
                (self.repo / ".pytest_cache" / "v" / "cache" / "nodeids").unlink()
                (self.repo / "coverage.xml").unlink()
                self.simulate_test_run("2")
                self.write(f"docs/{baseline_kind}-a.md", "# a\n")
                self.write(f"docs/{baseline_kind}-b.md", "# b\n")
                expected = [(f"docs/{baseline_kind}-a.md", "UNDECLARED_CHANGE"), (f"docs/{baseline_kind}-b.md", "UNDECLARED_CHANGE")]
                self.assertEqual(expected, self.findings(baseline, "src/feature.py"))
                for name in ("a", "b"):
                    (self.repo / "docs" / f"{baseline_kind}-{name}.md").unlink()


if __name__ == "__main__":
    unittest.main()
