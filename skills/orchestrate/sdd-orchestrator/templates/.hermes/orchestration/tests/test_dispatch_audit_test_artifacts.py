"""The workspace audit leaves out only an explicit, narrow list of test-run artifacts.

Running a project's tests rewrites its runner cache, coverage data and bytecode caches,
all git-ignored, on every run; before this fix each of them was an ``UNDECLARED_CHANGE``
and stopped every IMPLEMENT/TEST slice that ran the tests (seen live on a real project).
The list itself lives in ``detect_stack.TEST_RUN_ARTIFACTS``; ``audit_scope`` applies it.
These tests pin what is left out, that the filter runs at compare time on both snapshots
(so a 15.19.0 baseline that still lists those files is handled), and everything that
must still be reported.
"""
from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ORCHESTRATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ORCHESTRATION / "runtime"))

import audit_scope  # noqa: E402
import dispatch_audit  # noqa: E402
import sdd  # noqa: E402
import validate_protocol  # noqa: E402

MATCH = validate_protocol.path_matches
IGNORE = ".hermes/\n.pytest_cache/\n__pycache__/\n*.pyc\ncoverage.xml\n.coverage\n.coverage.*\nhtmlcov/\nbuild/\n"
PYC = "tests/pkg/__pycache__/test_x.cpython-314-pytest-9.1.1.pyc"


def result(*paths: str) -> dict:
    return {"executor_result": {"modified_paths": [{"path": path} for path in paths], "created_paths": []}}


class TestArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="sdd-audit-artifacts-")
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name).resolve() / "repo"
        self.repo.mkdir()
        for argv in (("init", "-q", "-b", "main"), ("config", "user.email", "t@e"), ("config", "user.name", "t")):
            self.git(*argv)
        self.write(".gitignore", IGNORE)
        self.write("src/feature.py", "x = 1\n")
        self.write("tests/pkg/test_x.py", "def test_x():\n    assert True\n")
        self.git("add", ".")
        self.git("commit", "-qm", "init")

    def git(self, *arguments: str) -> None:
        subprocess.run(["git", "-C", str(self.repo), *arguments], check=True, capture_output=True)

    def write(self, relative: str, text: str | bytes) -> Path:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
        return path

    def pyc(self, relative: str, source: str | None, *, flags: int = 0, mtime_shift: int = 0, size_shift: int = 0) -> Path:
        """A bytecode file whose 16-byte header records ``source``'s mtime and size (PEP 552 layout)."""
        info = (self.repo / source).stat() if source else None
        mtime = (int(info.st_mtime) + mtime_shift) & 0xFFFFFFFF if info else 0
        size = (info.st_size + size_shift) & 0xFFFFFFFF if info else 0
        return self.write(relative, b"\x00\x00\r\n" + struct.pack("<III", flags, mtime, size) + b"code")

    def run_tests_artifacts(self, generation: str = "1") -> None:
        """What one test run leaves behind, all ignored by the project."""
        self.write(".pytest_cache/v/cache/nodeids", f"[\"{generation}\"]\n")
        self.write(".pytest_cache/.gitignore", "*\n")
        self.write(".pytest_cache/README.md", "cache\n")
        self.write("coverage.xml", f"<coverage run='{generation}'/>\n")
        self.write(".coverage", f"sqlite {generation}")
        self.write(".coverage.host.123.456", f"sqlite {generation}")
        self.write("htmlcov/index.html", f"<html>{generation}</html>\n")
        self.pyc(PYC, "tests/pkg/test_x.py")
        self.pyc("src/__pycache__/feature.cpython-312.pyc", "src/feature.py")
        self.pyc("src/__pycache__/feature.cpython-312.opt-1.pyc", "src/feature.py")

    def audit(self, before: dict, after: dict, *declared: str) -> list[dict]:
        return dispatch_audit.audit(before=before, after=after, result=result(*declared), editable_paths=[], path_matches=MATCH)

    def paths(self, findings: list[dict]) -> list[str]:
        return sorted(item["path"] for item in findings)

    # -- what is left out -------------------------------------------------------------
    def test_a_test_run_leaves_no_finding_but_a_real_undeclared_doc_is_still_reported(self) -> None:
        before = dispatch_audit.snapshot(self.repo)
        self.write("src/feature.py", "x = 2\n")
        self.pyc("src/__pycache__/feature.cpython-312.pyc", "src/feature.py")
        self.run_tests_artifacts()
        self.write("docs/undeclared.md", "# new\n")
        after = dispatch_audit.snapshot(self.repo)
        findings = self.audit(before, after, "src/feature.py")
        self.assertEqual([("docs/undeclared.md", "UNDECLARED_CHANGE")], [(item["path"], item["finding"]) for item in findings])

    def test_a_second_run_that_rewrites_every_artifact_is_still_clean(self) -> None:
        self.run_tests_artifacts("1")
        before = dispatch_audit.snapshot(self.repo)
        self.run_tests_artifacts("2")
        self.assertEqual([], self.audit(before, dispatch_audit.snapshot(self.repo)))

    def test_a_cleaned_cache_is_not_a_deletion(self) -> None:
        self.run_tests_artifacts()
        before = dispatch_audit.snapshot(self.repo)
        for relative in (".pytest_cache/v/cache/nodeids", "coverage.xml", PYC):
            (self.repo / relative).unlink()
        self.assertEqual([], self.audit(before, dispatch_audit.snapshot(self.repo, baseline=before)))

    def test_a_baseline_stored_by_15_19_0_still_listing_the_caches_is_filtered_at_compare_time(self) -> None:
        self.run_tests_artifacts("1")
        legacy = dispatch_audit.snapshot(self.repo)
        legacy.pop("test_artifacts", None)  # 15.19.0 wrote neither the key nor a filtered list
        cached = sorted(path for path in legacy["files"] if ".pytest_cache" in path or "__pycache__" in path)
        self.assertIn(".pytest_cache/v/cache/nodeids", cached)
        self.assertIn(PYC, cached)
        stored = json.loads(json.dumps(legacy))
        with self.subTest(case="removed afterwards"):
            for relative in (".pytest_cache/v/cache/nodeids", ".pytest_cache/.gitignore", ".pytest_cache/README.md", PYC):
                (self.repo / relative).unlink()
            self.assertEqual([], self.audit(stored, dispatch_audit.snapshot(self.repo, baseline=stored)))
        with self.subTest(case="rewritten with different hashes"):
            self.run_tests_artifacts("2")
            self.assertEqual([], self.audit(stored, dispatch_audit.snapshot(self.repo, baseline=stored)))
        with self.subTest(case="changed_paths agrees"):
            self.assertEqual([], dispatch_audit.changed_paths(stored, dispatch_audit.snapshot(self.repo, baseline=stored)))

    def test_a_legacy_baseline_does_not_launder_a_source_file_planted_in_the_cache_directory(self) -> None:
        legacy = dispatch_audit.snapshot(self.repo)
        legacy.pop("test_artifacts", None)
        self.write(".pytest_cache/plugin.py", "import os\n")
        self.write("tests/pkg/__pycache__/notes.txt", "x\n")
        after = dispatch_audit.snapshot(self.repo)
        self.assertEqual([".pytest_cache/plugin.py", "tests/pkg/__pycache__/notes.txt"], self.paths(self.audit(legacy, after)))

    def test_the_cache_directory_own_gitignore_is_not_an_ignore_rule_change(self) -> None:
        before = dispatch_audit.snapshot(self.repo)
        self.write(".pytest_cache/.gitignore", "*\n")
        self.assertEqual([], self.audit(before, dispatch_audit.snapshot(self.repo)))

    # -- what still counts ------------------------------------------------------------
    def test_everything_outside_the_narrow_list_is_still_reported(self) -> None:
        self.write("src/__pycache__/keep.txt", "x\n")
        before = dispatch_audit.snapshot(self.repo)
        cases = {
            "src/feature.pyc": lambda path: self.pyc(path, "src/feature.py"),  # a .pyc outside __pycache__
            "src/__pycache__/evil.py": lambda path: self.write(path, "import os\n"),  # not a .pyc
            "src/__pycache__/keep.txt": lambda path: self.write(path, "y\n"),  # not a .pyc, already there
            "src/__pycache__/orphan.cpython-312.pyc": lambda path: self.pyc(path, None),  # no sibling source
            "src/__pycache__/feature.pyc": lambda path: self.pyc(path, "src/feature.py"),  # no implementation tag
            "src/__pycache__/feature.cpython-312.opt-x.pyc": lambda path: self.pyc(path, "src/feature.py"),
            "tests/pkg/__pycache__/test_x.cpython-314.pyc": lambda path: self.pyc(path, "tests/pkg/test_x.py", mtime_shift=7),
            "tests/pkg/__pycache__/test_x.cpython-313.pyc": lambda path: self.pyc(path, "tests/pkg/test_x.py", size_shift=1),
            "tests/pkg/__pycache__/test_x.cpython-312.pyc": lambda path: self.pyc(path, "tests/pkg/test_x.py", flags=1),
            "tests/pkg/__pycache__/test_x.cpython-311.pyc": lambda path: self.pyc(path, "tests/pkg/test_x.py", flags=3),
            "tests/pkg/__pycache__/test_x.cpython-310.pyc": lambda path: self.write(path, b"short"),
            "conftest.py": lambda path: self.write(path, "import os\n"),
            "tests/conftest.py": lambda path: self.write(path, "import os\n"),
            ".pytest_cache/conftest.py": lambda path: self.write(path, "import os\n"),
            "sub/coverage.xml": lambda path: self.write(path, "<coverage/>\n"),
            "sub/.pytest_cache/v/cache/nodeids": lambda path: self.write(path, "[]\n"),
            "sub/htmlcov/index.html": lambda path: self.write(path, "<html/>\n"),
            ".coveragerc": lambda path: self.write(path, "[run]\n"),
            ".coverage.pth": lambda path: self.write(path, "import os\n"),  # a code suffix never qualifies
            "htmlcov/plugin.py": lambda path: self.write(path, "import os\n"),
            "build/__pycache__/feature.cpython-312.pyc": lambda path: self.pyc(path, None),
        }
        for relative, make in cases.items():
            make(relative)
        link = "src/__pycache__/feature.cpython-39.pyc"  # a valid name and sibling, but a link, not a file
        os.symlink("../feature.py", self.repo / link)
        after = dispatch_audit.snapshot(self.repo)
        expected = sorted([*cases, link])
        self.assertEqual(expected, self.paths(self.audit(before, after)))

    def test_a_tracked_coverage_file_and_an_unignored_cache_still_count(self) -> None:
        self.write(".gitignore", ".hermes/\n")
        self.write("coverage.xml", "<coverage/>\n")
        self.git("add", ".")
        self.git("commit", "-qm", "track coverage")
        before = dispatch_audit.snapshot(self.repo)
        self.write("coverage.xml", "<coverage run='2'/>\n")
        self.write(".pytest_cache/v/cache/nodeids", "[]\n")
        self.pyc("src/__pycache__/feature.cpython-312.pyc", "src/feature.py")
        found = self.paths(self.audit(before, dispatch_audit.snapshot(self.repo)))
        self.assertEqual([".pytest_cache/v/cache/nodeids", "coverage.xml", "src/__pycache__/feature.cpython-312.pyc"], found)

    def test_deleting_a_tracked_coverage_file_is_still_reported(self) -> None:
        self.write("coverage.xml", "<coverage/>\n")
        self.git("add", "-f", "coverage.xml")
        self.git("commit", "-qm", "track coverage")
        before = dispatch_audit.snapshot(self.repo)
        (self.repo / "coverage.xml").unlink()
        self.assertEqual(["coverage.xml"], self.paths(self.audit(before, dispatch_audit.snapshot(self.repo, baseline=before))))

    def test_the_list_is_exactly_the_documented_one(self) -> None:
        import detect_stack

        self.assertEqual((".pytest_cache", "htmlcov"), detect_stack.TEST_RUN_ARTIFACT_TREES)
        self.assertEqual(("coverage.xml", ".coverage"), detect_stack.TEST_RUN_ARTIFACT_FILES)
        self.assertEqual((".coverage.",), detect_stack.TEST_RUN_ARTIFACT_PREFIXES)
        self.assertEqual("__pycache__", detect_stack.BYTECODE_CACHE_DIR)
        for gate_cache in (".mypy_cache/x.json", ".ruff_cache/x", "node_modules/x.js", ".tox/x", "coverage/lcov.info"):
            with self.subTest(path=gate_cache):
                self.assertFalse(audit_scope.candidate(gate_cache))
        for name in ("a.cpython-312.pyc", "a.cpython-313t.pyc", "a.pypy310.pyc", "a.cpython-314-pytest-9.1.1.pyc",
                     "a.cpython-312.opt-2.pyc"):
            with self.subTest(name=name):
                self.assertTrue(audit_scope.candidate(f"pkg/__pycache__/{name}"))

    def test_an_ignore_rule_change_is_still_reported(self) -> None:
        before = dispatch_audit.snapshot(self.repo)
        self.write(".gitignore", IGNORE + "docs/\n")
        self.run_tests_artifacts()
        findings = self.audit(before, dispatch_audit.snapshot(self.repo), ".gitignore")
        self.assertEqual([(".gitignore", "IGNORE_RULES_CHANGED")], [(item["path"], item["finding"]) for item in findings])

    def test_a_pyc_that_stops_matching_its_source_is_reported(self) -> None:
        self.pyc(PYC, "tests/pkg/test_x.py")
        before = dispatch_audit.snapshot(self.repo)
        self.pyc(PYC, "tests/pkg/test_x.py", mtime_shift=3)
        self.assertEqual([PYC], self.paths(self.audit(before, dispatch_audit.snapshot(self.repo))))


    def test_the_controller_re_audits_an_open_action_after_an_upgrade(self) -> None:
        """A 15.19.0 baseline (caches listed, no ``test_artifacts``) through ``sdd.workspace_audit_stop``."""
        run_dir = self.repo.parent / "run"
        run_dir.mkdir()
        self.run_tests_artifacts("1")
        legacy = dispatch_audit.snapshot(self.repo)
        legacy.pop("test_artifacts")
        (run_dir / "T-IMPLEMENT-01.workspace-before.json").write_text(json.dumps(legacy), encoding="utf-8")
        final = run_dir / "T-IMPLEMENT-01.result.json"
        final.write_text(json.dumps(result("src/feature.py")), encoding="utf-8")
        base = self.repo.parent
        ctx = mock.Mock(repo=self.repo, delivery={"slices": {"editable_paths": ["src/*"]}}, stage="IMPLEMENT", runtime_dir=base / "rt",
                        state_path=base / "STATE.md", journal_path=base / "j.json", history_dir=base / "h")
        ctx.run_dir.return_value = run_dir
        action = {"id": "T-IMPLEMENT-01", "stage": "IMPLEMENT", "final": final}
        self.write("src/feature.py", "x = 2\n")
        (self.repo / ".pytest_cache" / "v" / "cache" / "nodeids").unlink()
        self.run_tests_artifacts("2")
        with mock.patch.object(sdd, "workspace_audit_enabled", return_value=True), \
                mock.patch.object(sdd, "stopped", lambda _ctx, payload: payload):
            self.assertIsNone(sdd.workspace_audit_stop(ctx, action))
            self.write("docs/undeclared.md", "# new\n")
            payload = sdd.workspace_audit_stop(ctx, action)
        self.assertEqual("WORKSPACE_AUDIT_FAILED", payload["stop_reason"])
        self.assertEqual(["docs/undeclared.md"], payload["undeclared"])


if __name__ == "__main__":
    unittest.main()
