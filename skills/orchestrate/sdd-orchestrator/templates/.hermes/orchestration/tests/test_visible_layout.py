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

import bootstrap_worktree  # noqa: E402
import context_graph  # noqa: E402
import hook_runtime  # noqa: E402
import obsidian_binding  # noqa: E402
import obsidian_connector  # noqa: E402
import stage_context  # noqa: E402
import test_stage_context as stage_tests  # noqa: E402
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

            self.assertEqual(binding, obsidian_binding.controller_binding_file(container, "Hermes"))
            self.assertEqual(container / "Hermes/skills/x",
                             obsidian_binding.controller_path(container, ".hermes/skills/x", "Hermes"))
            with mock.patch.object(hook_runtime, "INSTALLED_CONTAINER", container), \
                    mock.patch.object(obsidian_binding, "CONTROLLER_FOLDER", "Hermes"):
                state = hook_runtime.runtime_locations(Path(temp) / "repo").state
            self.assertEqual("Hermes Runtime", state.relative_to(container).parts[0])

    def test_a_repository_keeps_its_hidden_controller_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".hermes" / "skills").mkdir(parents=True)
            self.assertEqual(root / ".hermes/skills/x", obsidian_binding.controller_path(root, ".hermes/skills/x", ".hermes"))


class LayoutReviewRegressionTests(unittest.TestCase):
    def test_runtime_subpath_is_confined_to_its_own_folder(self) -> None:
        self.assertEqual(".hermes-runtime", obsidian_binding._validate_runtime_subpath(".hermes-runtime/"))
        self.assertEqual("Hermes Runtime", obsidian_binding._validate_runtime_subpath("Hermes Runtime"))
        for value in (".", "./x", "Hermes", "hermes/x", ".hermes", "raw/articles", "concepts", ".obsidian", "a/./b"):
            with self.subTest(value=value), self.assertRaises(obsidian_binding.BindingError):
                obsidian_binding._validate_runtime_subpath(value)

    def test_bootstrap_keeps_the_runtime_folder_the_container_already_uses(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            container = Path(temp)
            self.assertEqual("Hermes Runtime", obsidian_binding.container_runtime_subpath(container))
            (container / ".hermes").mkdir()
            self.assertEqual(".hermes-runtime", obsidian_binding.container_runtime_subpath(container))
            (container / ".hermes").rmdir()
            (container / ".hermes-runtime").mkdir()
            self.assertEqual(".hermes-runtime", obsidian_binding.container_runtime_subpath(container))
            (container / "Hermes Runtime").mkdir()
            self.assertEqual("Hermes Runtime", obsidian_binding.container_runtime_subpath(container))

    def test_bootstrap_binding_names_the_runtime_it_resolved(self) -> None:
        target = bootstrap_worktree.ObsidianTarget(Path("/v"), "P", Path("/v/P/.hermes-runtime/wt"))
        self.assertEqual(".hermes-runtime", json.loads(bootstrap_worktree.render_binding(target))["runtime_subpath"])

    def test_migrate_puts_a_root_lock_into_the_visible_controller(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp).resolve() / ".obsidian").mkdir()
            container = Path(temp).resolve() / "Projects" / "App"
            (container / "Hermes").mkdir(parents=True)
            (container / "Hermes" / "obsidian.json").write_text("{}", encoding="utf-8")
            (container / "skills-lock.json").write_text("{}", encoding="utf-8")
            moves = wiki_layout.plan(container)["moves"]
            self.assertIn({"source": "skills-lock.json", "destination": "Hermes/skills-lock.json"},
                          [{k: m[k] for k in ("source", "destination")} for m in moves])

    def test_a_planted_folder_never_replaces_the_controllers_own(self) -> None:
        """A `.hermes/` controller ignores a planted `Hermes/` and the reverse (security review SEC-1/SEC-2)."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for folder in (".hermes", "Hermes"):
                (root / folder / "skills").mkdir(parents=True)
                (root / folder / "obsidian.json").write_text("{}", encoding="utf-8")
            for own in (".hermes", "Hermes"):
                with self.subTest(own=own), mock.patch.object(obsidian_binding, "CONTROLLER_FOLDER", own):
                    self.assertEqual(root / own / "obsidian.json", obsidian_binding.controller_binding_file(root))
                    self.assertEqual(root / own / "skills", obsidian_binding.controller_path(root, ".hermes/skills"))
                    with mock.patch.object(hook_runtime, "INSTALLED_CONTAINER", root):
                        self.assertEqual(root / own / "obsidian.json", hook_runtime._container_binding_path())
            (root / ".hermes" / "obsidian.json").unlink()
            with mock.patch.object(obsidian_binding, "CONTROLLER_FOLDER", ".hermes"):
                self.assertIsNone(obsidian_binding.controller_binding_file(root))

    def test_a_controller_trusts_the_folder_it_is_installed_in(self) -> None:
        self.assertEqual(ORCHESTRATION.parent.name, obsidian_binding.CONTROLLER_FOLDER)
        self.assertIn(obsidian_binding.CONTROLLER_FOLDER, {".hermes", "Hermes"})


class VaultResidentPlaybookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        stage_tests.setUpModule()  # isolate the Jev governance gate, as test_stage_context does
        cls.addClassCleanup(stage_tests.tearDownModule)

    def test_required_playbook_is_verified_from_vault_resident_controller(self) -> None:
        value = stage_tests.context()
        value["slice"]["required_playbooks"] = [
            {"name": "sdd-database-design-migrations", "slice_ids": ["S1"]}
        ]
        with tempfile.TemporaryDirectory() as repo_temp, tempfile.TemporaryDirectory() as vault_temp:
            repo = Path(repo_temp).resolve()
            container = Path(vault_temp).resolve()
            stage_tests.materialize_playbook(value, container)
            with mock.patch.object(stage_context, "_live_project_root", return_value=repo), \
                    mock.patch.object(stage_context, "_installed_playbook_root", return_value=container), \
                    mock.patch.object(obsidian_binding, "CONTROLLER_FOLDER", ".hermes"):
                value["project_root"] = str(repo)
                result = stage_context.check(value)
            self.assertTrue(result["valid"], result["errors"])
            self.assertFalse((repo / ".hermes").exists())


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
