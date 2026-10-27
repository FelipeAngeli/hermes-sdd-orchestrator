"""The type-checker and linter caches at the repository root are test-run artifact trees.

``sdd.py gate`` gives ``mypy``/``ruff`` fresh per-run caches (``runtime/gate_env.py``), so a
gate never reads the repository's ``.mypy_cache/``/``.ruff_cache/``. A worker that runs those
tools rewrites both on every run (seen live: FP-006 slice S7 was stopped with 29
``UNDECLARED_CHANGE`` findings, all of them ``.mypy_cache/3.12/cache.N.db`` and
``.ruff_cache/0.17.2/<hash>``). The two trees now follow the ``.pytest_cache/`` rules: only
untracked, git-ignored by a baseline rule outside the tree, code suffixes still count, a
removed artifact needs a baseline ``!!``. An action whose baseline was stored by 15.19.1
(caches listed as ``!!``, ``artifact_roots`` computed without these trees) is re-audited
cleanly after an upgrade.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ORCHESTRATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ORCHESTRATION / "runtime"))

import audit_scope  # noqa: E402
import detect_stack  # noqa: E402
import dispatch_audit  # noqa: E402
import sdd  # noqa: E402
import validate_protocol  # noqa: E402

#: The FP-006 project's own .gitignore (Central Administrativa), unchanged across the upgrade.
FP006_IGNORE = ".hermes/\n__pycache__/\n*.pyc\n.venv/\n.env\n.coverage\ncoverage.xml\n.pytest_cache/\n.mypy_cache/\n.ruff_cache/\n"
MYPY_DB = ".mypy_cache/3.12/cache.0.db"
RUFF_ENTRY = ".ruff_cache/0.17.2/1234567890"
TREES_15_19_1 = (".pytest_cache", "htmlcov")


class GateCacheArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="sdd-audit-gate-caches-")
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name).resolve() / "repo"
        self.repo.mkdir()
        for argv in (("init", "-q", "-b", "main"), ("config", "user.email", "t@e"), ("config", "user.name", "t")):
            self.git(*argv)
        self.write("src/feature.py", "x = 1\n")

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

    def run_gate_tools(self, generation: str) -> None:
        """What one mypy + ruff run leaves behind: their own ``*`` .gitignore, a tag and cache entries."""
        self.write(".mypy_cache/.gitignore", "# Automatically created by mypy\n*\n")
        self.write(".mypy_cache/CACHEDIR.TAG", "Signature: 8a477f597d28d172789f06886806bc55\n")
        self.write(MYPY_DB, f"sqlite {generation}")
        self.write(".mypy_cache/3.12/cache.1.db", f"sqlite {generation}")
        self.write(".ruff_cache/.gitignore", "# Automatically created by ruff.\n*\n")
        self.write(".ruff_cache/CACHEDIR.TAG", "Signature: 8a477f597d28d172789f06886806bc55\n")
        self.write(RUFF_ENTRY, f"bincode {generation}")
        self.write(f".ruff_cache/0.17.2/{generation}999", f"bincode {generation}")

    def legacy_15_19_1(self) -> dict:
        """A baseline exactly as 15.19.1 stored it: status recorded, artifact_roots without the gate caches."""
        with mock.patch.object(audit_scope, "TREE_ROOTS", TREES_15_19_1), \
                mock.patch.object(audit_scope, "TEST_RUN_ARTIFACT_TREES", TREES_15_19_1):
            snapshot = dispatch_audit.snapshot(self.repo)
        snapshot.pop("artifact_trees", None)  # 15.19.1 did not record which trees it evaluated
        snapshot["test_artifacts"] = [path for path in snapshot["test_artifacts"] if not path.startswith((".mypy_cache/", ".ruff_cache/"))]
        return json.loads(json.dumps(snapshot))

    def findings(self, before: dict, *declared: str) -> list[tuple[str, str]]:
        after = dispatch_audit.snapshot(self.repo, baseline=before)
        result = {"executor_result": {"modified_paths": [{"path": path} for path in declared], "created_paths": []}}
        found = dispatch_audit.audit(before=before, after=after, result=result, editable_paths=[], path_matches=validate_protocol.path_matches)
        return sorted((item["path"], item["finding"]) for item in found)

    # -- the list -----------------------------------------------------------------------------
    def test_the_gate_cache_trees_are_artifact_trees_named_only_in_stack_detection(self) -> None:
        self.assertEqual((".mypy_cache", ".ruff_cache"), detect_stack.GATE_CACHE_TREES)
        self.assertEqual((".pytest_cache", "htmlcov", ".mypy_cache", ".ruff_cache"), detect_stack.TEST_RUN_ARTIFACT_TREES)
        for path in (MYPY_DB, RUFF_ENTRY, ".mypy_cache/3.12/pkg/mod.data.json", ".ruff_cache/CACHEDIR.TAG"):
            with self.subTest(path=path):
                self.assertTrue(audit_scope.candidate(path))
        for path in (".mypy_cache/plugin.py", ".ruff_cache/x.pth", ".mypy_cache/x.so", "sub/.mypy_cache/3.12/cache.0.db", ".mypy_cache"):
            with self.subTest(path=path):
                self.assertFalse(audit_scope.candidate(path))

    # -- the FP-006 incident: a 15.19.1 baseline re-audited after the upgrade -----------------
    def test_a_15_19_1_baseline_is_re_audited_cleanly_and_a_real_undeclared_source_is_still_reported(self) -> None:
        self.commit(FP006_IGNORE)
        self.run_gate_tools("1")
        baseline = self.legacy_15_19_1()
        self.assertEqual(["!!", "!!"], [baseline["status"][MYPY_DB], baseline["status"][RUFF_ENTRY]])
        self.assertEqual([".pytest_cache"], baseline["artifact_roots"])
        self.write("src/feature.py", "x = 2\n")
        self.run_gate_tools("2")
        (self.repo / ".mypy_cache/3.12/cache.1.db").unlink()
        self.assertEqual([], self.findings(baseline, "src/feature.py"))
        self.write("src/undeclared.py", "y = 1\n")
        self.assertEqual([("src/undeclared.py", "UNDECLARED_CHANGE")], self.findings(baseline, "src/feature.py"))

    def test_the_controller_re_audits_a_15_19_1_action_through_workspace_audit_stop(self) -> None:
        self.commit(FP006_IGNORE)
        self.run_gate_tools("1")
        run_dir = self.repo.parent / "run"
        run_dir.mkdir()
        (run_dir / "FP-IMPLEMENT-07.workspace-before.json").write_text(json.dumps(self.legacy_15_19_1()), encoding="utf-8")
        final = run_dir / "FP-IMPLEMENT-07.result.json"
        final.write_text(json.dumps({"executor_result": {"modified_paths": [{"path": "src/feature.py"}], "created_paths": []}}), encoding="utf-8")
        base = self.repo.parent
        ctx = mock.Mock(repo=self.repo, stage="IMPLEMENT", runtime_dir=base / "rt", state_path=base / "STATE.md",
                        journal_path=base / "j.json", history_dir=base / "h",
                        delivery={"slices": {"editable_paths": ["src/*"]},
                                  "workspace_audit": {"action_id": "FP-IMPLEMENT-07", "mode": "enforce", "baseline_action_id": "FP-IMPLEMENT-07"}})
        ctx.run_dir.return_value = run_dir
        action = {"id": "FP-IMPLEMENT-07", "stage": "IMPLEMENT", "final": final}
        self.write("src/feature.py", "x = 2\n")
        self.run_gate_tools("2")
        with mock.patch.object(sdd, "stopped", lambda _ctx, payload: payload):
            self.assertIsNone(sdd.workspace_audit_stop(ctx, action))
            self.write("docs/undeclared.md", "# new\n")
            payload = sdd.workspace_audit_stop(ctx, action)
        self.assertEqual("WORKSPACE_AUDIT_FAILED", payload["stop_reason"])
        self.assertEqual(["docs/undeclared.md"], payload["undeclared"])

    def test_a_current_baseline_records_the_trees_it_evaluated(self) -> None:
        self.commit(FP006_IGNORE)
        before = dispatch_audit.snapshot(self.repo)
        self.assertEqual(list(detect_stack.TEST_RUN_ARTIFACT_TREES), before["artifact_trees"])
        self.assertEqual([".pytest_cache", ".mypy_cache", ".ruff_cache"], before["artifact_roots"])
        self.run_gate_tools("1")
        self.assertEqual([], self.findings(before))

    def test_a_removed_ignored_cache_is_not_a_deletion(self) -> None:
        self.commit(FP006_IGNORE)
        self.run_gate_tools("1")
        for baseline in (dispatch_audit.snapshot(self.repo), self.legacy_15_19_1()):
            for relative in (MYPY_DB, RUFF_ENTRY):
                (self.repo / relative).unlink(missing_ok=True)
            self.assertEqual([], self.findings(baseline))
            self.run_gate_tools("1")

    # -- what still counts --------------------------------------------------------------------
    def test_unignored_caches_and_a_worker_written_cache_gitignore_still_count(self) -> None:
        self.commit(".hermes/\n")
        for before in (dispatch_audit.snapshot(self.repo), self.legacy_15_19_1()):
            with self.subTest(baseline="current" if "artifact_trees" in before else "15.19.1"):
                self.run_gate_tools("1")
                found = self.findings(before)
                self.assertIn((".mypy_cache/.gitignore", "IGNORE_RULES_CHANGED"), found)
                self.assertIn((MYPY_DB, "UNDECLARED_CHANGE"), found)
                self.assertIn((RUFF_ENTRY, "UNDECLARED_CHANGE"), found)
                for tree in (".mypy_cache", ".ruff_cache"):
                    shutil.rmtree(self.repo / tree)

    def test_a_root_rule_the_dispatch_adds_is_still_reported_for_a_15_19_1_baseline(self) -> None:
        self.commit(".hermes/\n")
        baseline = self.legacy_15_19_1()
        self.write(".gitignore", ".hermes/\n.mypy_cache/\n")
        self.write(MYPY_DB, "sqlite 1")
        self.assertIn((".gitignore", "IGNORE_RULES_CHANGED"), self.findings(baseline))

    def test_code_and_tracked_files_inside_the_caches_still_count(self) -> None:
        self.commit(FP006_IGNORE)
        self.write(".mypy_cache/3.12/tracked.json", "{}\n")
        self.git("add", "-f", ".mypy_cache/3.12/tracked.json")
        self.git("commit", "-qm", "track one cache file")
        for before in (dispatch_audit.snapshot(self.repo), self.legacy_15_19_1()):
            self.write(".mypy_cache/3.12/tracked.json", '{"v": 2}\n')
            self.write(".mypy_cache/sitecustomize.py", "import os\n")
            self.write(".ruff_cache/0.17.2/hook.pth", "import os\n")
            self.write("sub/.mypy_cache/3.12/cache.0.db", "sqlite\n")
            expected = [(".mypy_cache/3.12/tracked.json", "UNDECLARED_CHANGE"), (".mypy_cache/sitecustomize.py", "UNDECLARED_CHANGE"),
                        (".ruff_cache/0.17.2/hook.pth", "UNDECLARED_CHANGE"), ("sub/.mypy_cache/3.12/cache.0.db", "UNDECLARED_CHANGE")]
            self.assertEqual(expected, self.findings(before))
            self.git("checkout", "--", ".mypy_cache/3.12/tracked.json")
            for relative in (".mypy_cache/sitecustomize.py", ".ruff_cache/0.17.2/hook.pth", "sub/.mypy_cache/3.12/cache.0.db"):
                (self.repo / relative).unlink()


if __name__ == "__main__":
    unittest.main()
