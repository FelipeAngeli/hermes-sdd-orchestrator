"""The visible controller layout (`Hermes/`, `Hermes Runtime/`) is never wiki content.

Each runtime module that walks a project container must skip the visible
controller roots exactly as it skips the legacy `.hermes/` and
`.hermes-runtime/`, and must find a controller installed under `Hermes/`.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ORCHESTRATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ORCHESTRATION / "runtime"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import context_graph  # noqa: E402
import hook_runtime  # noqa: E402
import obsidian_binding  # noqa: E402
import obsidian_connector  # noqa: E402
import wiki_layout  # noqa: E402
from test_wiki_journal import WHEN, JournalTestCase, load_controller  # noqa: E402

CONTROLLER_NOTES = ("Hermes/orchestration/STATE.md", "Hermes Runtime/wt-1/STATE.md")


class VisibleRootsAreNotNotesTests(unittest.TestCase):
    def test_wiki_layout_never_classifies_the_visible_controller(self) -> None:
        for relative in CONTROLLER_NOTES:
            with self.subTest(relative=relative):
                self.assertIsNone(wiki_layout.classify(relative))

    def test_context_graph_skips_the_visible_controller(self) -> None:
        for relative in CONTROLLER_NOTES:
            with self.subTest(relative=relative):
                self.assertTrue(context_graph.skipped_note(relative))

    def test_connector_skips_the_visible_controller(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp).resolve() / "Knowledge"
            container = vault / "Projects" / "Demo"
            repo = Path(temp).resolve() / "repo"
            binding = repo / ".hermes" / "obsidian.json"
            binding.parent.mkdir(parents=True)
            binding.write_text(json.dumps({
                "schema_version": 1, "vault_path": str(vault),
                "project_container": "Projects/Demo", "runtime_subpath": "Hermes Runtime",
            }), encoding="utf-8")
            for relative in (*CONTROLLER_NOTES, "Hermes/private.md", "allowed.md"):
                note = container / relative
                note.parent.mkdir(parents=True, exist_ok=True)
                note.write_text("# note\n", encoding="utf-8")

            self.assertEqual(["allowed.md"], obsidian_connector.project_notes(repo))


class VisibleControllerPathTests(unittest.TestCase):
    def test_a_visible_container_resolves_its_binding_skills_and_hook_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            container = Path(temp).resolve() / "vault" / "Projects" / "App"
            (container / "Hermes" / "skills").mkdir(parents=True)
            binding = container / "Hermes" / "obsidian.json"
            binding.write_text(json.dumps({
                "schema_version": 1, "vault_path": str(container.parents[1]),
                "project_container": "Projects/App", "runtime_subpath": "Hermes Runtime",
            }), encoding="utf-8")

            self.assertEqual(binding, obsidian_binding.controller_binding_file(container))
            self.assertEqual(container / "Hermes/skills/x", obsidian_binding.controller_path(container, ".hermes/skills/x"))
            with mock.patch.object(hook_runtime, "INSTALLED_CONTAINER", container):
                state = hook_runtime.runtime_locations(Path(temp) / "repo").state
            self.assertEqual("Hermes Runtime", state.relative_to(container).parts[0])

    def test_a_repository_keeps_its_hidden_controller_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".hermes" / "skills").mkdir(parents=True)
            self.assertEqual(root / ".hermes/skills/x", obsidian_binding.controller_path(root, ".hermes/skills/x"))


class VisibleControllerJournalTests(JournalTestCase):
    def register_visible(self) -> None:
        runtime = self.container / "Hermes Runtime" / "visible"
        runtime.mkdir(parents=True)
        (runtime / "STATE.md").write_text(f'---\nworkspace:\n  path: "{self.repo}"\n---\n', encoding="utf-8")

    def test_visible_runtime_registers_its_worktree(self) -> None:
        self.register_visible()
        self.assertEqual([self.repo], self.wj.registered_workspaces(self.container, "Hermes Runtime"))

    def test_visible_controller_records_for_its_registered_worktree(self) -> None:
        binding = self.container / "Hermes" / "obsidian.json"
        binding.parent.mkdir(parents=True)
        binding.write_text(json.dumps({
            "schema_version": 1, "vault_path": str(self.vault),
            "project_container": "Projects/App", "runtime_subpath": "Hermes Runtime",
        }), encoding="utf-8")
        os.unlink(self.container / ".hermes" / "obsidian.json")
        self.register_visible()
        wj = load_controller(self.container, "Hermes")["wiki_journal"]

        result = wj.record_for_workspace(self.repo, kind="gate", title="lint", body="ok", when=WHEN)

        self.assertTrue((self.container / result["path"]).is_file())


if __name__ == "__main__":
    unittest.main()
