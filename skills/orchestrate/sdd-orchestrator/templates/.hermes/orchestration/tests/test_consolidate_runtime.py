"""Tests for consolidate_runtime: de-duplicating shared project history."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
import sys

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))

import consolidate_runtime  # noqa: E402
import obsidian_binding  # noqa: E402


class ConsolidateTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)

        self.repo = root / "repo"
        (self.repo / ".hermes").mkdir(parents=True)
        self.vault = root / "vault"
        self.container = self.vault / "Projects" / "Demo"
        self.container.mkdir(parents=True)
        (self.repo / ".hermes" / "obsidian.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "vault_path": str(self.vault),
                    "project_container": "Projects/Demo",
                    "runtime_subpath": ".hermes-runtime",
                }
            ),
            encoding="utf-8",
        )
        self.binding = obsidian_binding.load(self.repo)
        self.runtime_root = self.binding.runtime_root
        self.runtime_root.mkdir(parents=True)

    def seed(self, worktree: str, relative: str, body: str) -> Path:
        path = self.runtime_root / worktree / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def plan(self):
        return consolidate_runtime.build_plan(self.binding)


class SharingTests(ConsolidateTestCase):
    def test_identical_history_across_worktrees_is_shared(self) -> None:
        a = self.seed("wt-a", "history/APP-1.md", "same")
        b = self.seed("wt-b", "history/APP-1.md", "same")
        consolidate_runtime.apply(self.plan())
        shared = self.runtime_root / consolidate_runtime.SHARED_DIR / "history/APP-1.md"
        self.assertTrue(shared.exists())
        self.assertFalse(a.exists())
        self.assertFalse(b.exists())
        self.assertEqual("same", shared.read_text(encoding="utf-8"))

    def test_file_present_in_only_one_worktree_stays_put(self) -> None:
        only = self.seed("wt-a", "history/UNIQUE.md", "unique")
        consolidate_runtime.apply(self.plan())
        self.assertTrue(only.exists())

    def test_same_path_with_different_content_is_never_shared(self) -> None:
        """Divergent files are distinct evidence; collapsing them loses one."""
        a = self.seed("wt-a", "runs/log.txt", "alpha")
        b = self.seed("wt-b", "runs/log.txt", "beta")
        consolidate_runtime.apply(self.plan())
        self.assertTrue(a.exists())
        self.assertTrue(b.exists())
        self.assertEqual("alpha", a.read_text(encoding="utf-8"))
        self.assertEqual("beta", b.read_text(encoding="utf-8"))

    def test_runtime_state_is_never_shared(self) -> None:
        """STATE and the journal are per-worktree by definition."""
        a = self.seed("wt-a", "STATE.md", "identical")
        b = self.seed("wt-b", "STATE.md", "identical")
        ja = self.seed("wt-a", "ACTION_JOURNAL.json", "{}")
        jb = self.seed("wt-b", "ACTION_JOURNAL.json", "{}")
        consolidate_runtime.apply(self.plan())
        for path in (a, b, ja, jb):
            self.assertTrue(path.exists(), f"{path} must not be shared")

    def test_incidents_stay_per_worktree(self) -> None:
        a = self.seed("wt-a", "INCIDENTS.md", "none")
        b = self.seed("wt-b", "INCIDENTS.md", "none")
        consolidate_runtime.apply(self.plan())
        self.assertTrue(a.exists())
        self.assertTrue(b.exists())


class SafetyTests(ConsolidateTestCase):
    def test_dry_run_changes_nothing(self) -> None:
        a = self.seed("wt-a", "audits/x.log", "same")
        b = self.seed("wt-b", "audits/x.log", "same")
        plan = self.plan()
        self.assertTrue(plan.shared)
        self.assertTrue(a.exists() and b.exists())
        self.assertFalse((self.runtime_root / consolidate_runtime.SHARED_DIR).exists())

    def test_no_content_is_lost(self) -> None:
        before = {}
        for worktree in ("wt-a", "wt-b", "wt-c"):
            for name in ("history/h.md", "runs/r.log"):
                path = self.seed(worktree, name, f"body-{name}")
                before[path.read_text(encoding="utf-8")] = True
        consolidate_runtime.apply(self.plan())
        after = {
            p.read_text(encoding="utf-8")
            for p in self.runtime_root.rglob("*")
            if p.is_file()
        }
        self.assertEqual(set(before), after)

    def test_apply_is_idempotent(self) -> None:
        self.seed("wt-a", "history/h.md", "same")
        self.seed("wt-b", "history/h.md", "same")
        consolidate_runtime.apply(self.plan())
        second = self.plan()
        self.assertEqual([], second.shared)

    def test_shared_dir_is_hidden_from_obsidian(self) -> None:
        self.assertTrue(consolidate_runtime.SHARED_DIR.startswith("_"))
        self.seed("wt-a", "history/h.md", "s")
        self.seed("wt-b", "history/h.md", "s")
        consolidate_runtime.apply(self.plan())
        shared = self.runtime_root / consolidate_runtime.SHARED_DIR
        self.assertTrue(
            obsidian_binding.is_inside_container(self.binding, shared)
        )

    def test_empty_directories_are_pruned(self) -> None:
        self.seed("wt-a", "history/h.md", "same")
        self.seed("wt-b", "history/h.md", "same")
        consolidate_runtime.apply(self.plan())
        self.assertFalse((self.runtime_root / "wt-a" / "history").exists())

    def test_report_counts_reclaimed_copies(self) -> None:
        self.seed("wt-a", "history/h.md", "same")
        self.seed("wt-b", "history/h.md", "same")
        self.seed("wt-c", "history/h.md", "same")
        result = consolidate_runtime.apply(self.plan())
        self.assertEqual(1, result.shared_files)
        self.assertEqual(3, result.removed_copies)


if __name__ == "__main__":
    unittest.main()
