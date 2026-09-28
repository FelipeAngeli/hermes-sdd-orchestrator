"""Tests for vault_guard: write containment and baseline preservation."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
import sys

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))

import obsidian_binding  # noqa: E402
import vault_guard  # noqa: E402


class GuardTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

        self.repo = root / "repo"
        self.repo.mkdir()
        self.vault = root / "vault"
        self.container = self.vault / "Projects" / "Demo"
        self.container.mkdir(parents=True)
        (self.vault / "Projects" / "Other").mkdir(parents=True)
        (self.vault / "Workflow").mkdir(parents=True)

        payload = {
            "schema_version": 1,
            "vault_path": str(self.vault),
            "project_container": "Projects/Demo",
            "runtime_subpath": ".hermes-runtime",
        }
        binding_file = self.repo / ".hermes" / "obsidian.json"
        binding_file.parent.mkdir(parents=True)
        binding_file.write_text(json.dumps(payload), encoding="utf-8")
        self.binding = obsidian_binding.load(self.repo)


class WriteContainmentTests(GuardTestCase):
    def test_write_inside_own_container_is_allowed(self) -> None:
        vault_guard.assert_writable(self.binding, self.container / "sessions" / "s.md")

    def test_write_into_runtime_dir_is_allowed(self) -> None:
        target = obsidian_binding.state_path(self.binding, Path("/x/wt/app-455"))
        vault_guard.assert_writable(self.binding, target)

    def test_write_outside_project_container_is_refused(self) -> None:
        with self.assertRaises(vault_guard.VaultWriteRefused) as ctx:
            vault_guard.assert_writable(
                self.binding, self.vault / "Projects" / "Other" / "note.md"
            )
        self.assertEqual(ctx.exception.code, "VAULT_WRITE_OUTSIDE_CONTAINER")

    def test_write_to_shared_workflow_area_is_refused(self) -> None:
        """The protocol note lives outside the container: human-authorised only."""
        with self.assertRaises(vault_guard.VaultWriteRefused):
            vault_guard.assert_writable(self.binding, self.vault / "Workflow" / "p.md")

    def test_write_outside_the_vault_entirely_is_refused(self) -> None:
        with self.assertRaises(vault_guard.VaultWriteRefused):
            vault_guard.assert_writable(self.binding, Path("/etc/passwd"))

    def test_traversal_out_of_container_is_refused(self) -> None:
        target = self.container / ".." / "Other" / "note.md"
        with self.assertRaises(vault_guard.VaultWriteRefused):
            vault_guard.assert_writable(self.binding, target)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_symlink_escape_is_refused(self) -> None:
        link = self.container / "escape"
        os.symlink(self.vault / "Projects" / "Other", link)
        with self.assertRaises(vault_guard.VaultWriteRefused):
            vault_guard.assert_writable(self.binding, link / "note.md")


class BaselineTests(GuardTestCase):
    def _seed(self) -> None:
        (self.container / "sessions").mkdir()
        (self.container / "sessions" / "a.md").write_text("alpha", encoding="utf-8")
        (self.container / "README.md").write_text("readme", encoding="utf-8")

    def test_baseline_is_stable_when_nothing_changes(self) -> None:
        self._seed()
        before = vault_guard.capture_vault_baseline(self.binding)
        after = vault_guard.capture_vault_baseline(self.binding)
        self.assertEqual(vault_guard.diff_baseline(before, after), [])

    def test_modified_file_is_reported(self) -> None:
        self._seed()
        before = vault_guard.capture_vault_baseline(self.binding)
        (self.container / "README.md").write_text("changed", encoding="utf-8")
        changes = vault_guard.diff_baseline(
            before, vault_guard.capture_vault_baseline(self.binding)
        )
        self.assertTrue(any(c.kind == "MODIFIED" for c in changes), changes)

    def test_added_and_removed_files_are_reported(self) -> None:
        self._seed()
        before = vault_guard.capture_vault_baseline(self.binding)
        (self.container / "new.md").write_text("new", encoding="utf-8")
        (self.container / "README.md").unlink()
        changes = vault_guard.diff_baseline(
            before, vault_guard.capture_vault_baseline(self.binding)
        )
        kinds = {c.kind for c in changes}
        self.assertIn("ADDED", kinds)
        self.assertIn("REMOVED", kinds)

    def test_runtime_directory_is_excluded_from_baseline(self) -> None:
        """Runtime churns every transition; it must not trip preservation."""
        self._seed()
        runtime = obsidian_binding.runtime_dir(self.binding, Path("/x/wt/app-455"))
        runtime.mkdir(parents=True)
        (runtime / "STATE.md").write_text("v1", encoding="utf-8")
        before = vault_guard.capture_vault_baseline(self.binding)
        (runtime / "STATE.md").write_text("v2-much-longer", encoding="utf-8")
        after = vault_guard.capture_vault_baseline(self.binding)
        self.assertEqual(vault_guard.diff_baseline(before, after), [])

    def test_assert_preserved_raises_on_violation(self) -> None:
        self._seed()
        before = vault_guard.capture_vault_baseline(self.binding)
        (self.container / "README.md").write_text("changed", encoding="utf-8")
        after = vault_guard.capture_vault_baseline(self.binding)
        with self.assertRaises(vault_guard.VaultBaselineViolation):
            vault_guard.assert_baseline_preserved(before, after)

    def test_assert_preserved_passes_when_untouched(self) -> None:
        self._seed()
        before = vault_guard.capture_vault_baseline(self.binding)
        after = vault_guard.capture_vault_baseline(self.binding)
        vault_guard.assert_baseline_preserved(before, after)


if __name__ == "__main__":
    unittest.main()
