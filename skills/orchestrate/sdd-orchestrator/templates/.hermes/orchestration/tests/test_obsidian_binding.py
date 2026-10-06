"""Tests for obsidian_binding: repo-local Obsidian binding resolver.

Covers loading/validation of `.hermes/obsidian.json` and per-worktree runtime
path derivation. No network, no vault access, no repository mutation.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from os import environ as process_environment
from pathlib import Path
import sys

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))

import obsidian_binding  # noqa: E402


def write_binding(repo: Path, **overrides) -> Path:
    payload = {
        "schema_version": 1,
        "vault_path": "/tmp/demo-vault",
        "project_container": "1 - 💡 Knowledge/Projects/Demo",
        "runtime_subpath": ".hermes-runtime",
        "protocol_path": "2 - 🗂 Workflow/AI/Hermes/HERMES_OBSIDIAN_PROTOCOL.md",
    }
    payload.update(overrides)
    for key in [k for k, v in payload.items() if v is obsidian_binding.OMIT]:
        del payload[key]
    path = repo / ".hermes" / "obsidian.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class LoadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        self.addCleanup(self._tmp.cleanup)

    def test_loads_binding_from_repo_root(self) -> None:
        write_binding(self.repo)
        binding = obsidian_binding.load(self.repo)
        self.assertEqual(binding.vault_path, Path("/tmp/demo-vault"))
        self.assertEqual(binding.project_container, "1 - 💡 Knowledge/Projects/Demo")
        self.assertEqual(binding.runtime_subpath, ".hermes-runtime")

    def test_missing_binding_raises_actionable_error(self) -> None:
        with self.assertRaises(obsidian_binding.BindingError) as ctx:
            obsidian_binding.load(self.repo)
        self.assertEqual(ctx.exception.code, "BINDING_MISSING")

    def test_runtime_installed_in_vault_uses_container_binding_when_repo_has_none(self) -> None:
        import importlib.util
        import shutil

        container = Path(self._tmp.name) / "vault" / "Projects" / "App"
        runtime = container / ".hermes" / "orchestration" / "runtime"
        runtime.mkdir(parents=True)
        shutil.copy2(RUNTIME / "obsidian_binding.py", runtime / "obsidian_binding.py")
        write_binding(container, vault_path=str(Path(self._tmp.name) / "vault"), project_container="Projects/App")
        spec = importlib.util.spec_from_file_location("vault_obsidian_binding", runtime / "obsidian_binding.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["vault_obsidian_binding"] = module
        self.addCleanup(sys.modules.pop, "vault_obsidian_binding", None)
        spec.loader.exec_module(module)

        binding = module.load(self.repo)

        self.assertEqual("Projects/App", binding.project_container)
        self.assertFalse((self.repo / ".hermes").exists())
        self.assertEqual((container / ".hermes" / "obsidian.json").resolve(), module.binding_path(self.repo))

    def test_unknown_schema_version_is_refused(self) -> None:
        write_binding(self.repo, schema_version=999)
        with self.assertRaises(obsidian_binding.BindingError) as ctx:
            obsidian_binding.load(self.repo)
        self.assertEqual(ctx.exception.code, "BINDING_SCHEMA_UNSUPPORTED")

    def test_missing_required_field_is_refused(self) -> None:
        write_binding(self.repo, project_container=obsidian_binding.OMIT)
        with self.assertRaises(obsidian_binding.BindingError) as ctx:
            obsidian_binding.load(self.repo)
        self.assertEqual(ctx.exception.code, "BINDING_INVALID")

    def test_malformed_json_is_refused_without_traceback_leak(self) -> None:
        path = self.repo / ".hermes" / "obsidian.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(obsidian_binding.BindingError) as ctx:
            obsidian_binding.load(self.repo)
        self.assertEqual(ctx.exception.code, "BINDING_INVALID")

    def test_relative_vault_path_is_refused(self) -> None:
        write_binding(self.repo, vault_path="relative/vault")
        with self.assertRaises(obsidian_binding.BindingError) as ctx:
            obsidian_binding.load(self.repo)
        self.assertEqual(ctx.exception.code, "BINDING_INVALID")

    def test_absolute_project_container_is_refused(self) -> None:
        absolute_container = str((self.repo.parent / "absolute-container").resolve())
        write_binding(self.repo, project_container=absolute_container)
        with self.assertRaises(obsidian_binding.BindingError) as ctx:
            obsidian_binding.load(self.repo)
        self.assertEqual(ctx.exception.code, "BINDING_INVALID")

    def test_project_container_escaping_the_vault_is_refused(self) -> None:
        write_binding(self.repo, project_container="../../etc")
        with self.assertRaises(obsidian_binding.BindingError) as ctx:
            obsidian_binding.load(self.repo)
        self.assertEqual(ctx.exception.code, "BINDING_INVALID")


class EnvOverrideTests(unittest.TestCase):
    """Risk R4: a versioned binding carries a machine-specific absolute path."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        self.addCleanup(self._tmp.cleanup)
        self._saved = process_environment.get(obsidian_binding.VAULT_ENV)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        if self._saved is None:
            process_environment.pop(obsidian_binding.VAULT_ENV, None)
        else:
            process_environment[obsidian_binding.VAULT_ENV] = self._saved

    def test_env_var_overrides_vault_path(self) -> None:
        write_binding(self.repo)
        process_environment[obsidian_binding.VAULT_ENV] = "/tmp/other-vault"
        binding = obsidian_binding.load(self.repo)
        self.assertEqual(binding.vault_path, Path("/tmp/other-vault"))

    def test_env_override_must_be_absolute(self) -> None:
        write_binding(self.repo)
        process_environment[obsidian_binding.VAULT_ENV] = "nope/relative"
        with self.assertRaises(obsidian_binding.BindingError) as ctx:
            obsidian_binding.load(self.repo)
        self.assertEqual(ctx.exception.code, "BINDING_INVALID")


class RuntimePathTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        self.addCleanup(self._tmp.cleanup)
        write_binding(self.repo)
        self.binding = obsidian_binding.load(self.repo)

    def test_runtime_dir_lives_under_hidden_subpath(self) -> None:
        runtime = obsidian_binding.runtime_dir(self.binding, Path("/x/wt/app-455"))
        parts = runtime.parts
        self.assertIn(".hermes-runtime", parts)
        self.assertTrue(
            runtime.name.startswith("app-455-"),
            f"expected slug prefix, got {runtime.name}",
        )

    def test_runtime_dir_is_inside_project_container(self) -> None:
        runtime = obsidian_binding.runtime_dir(self.binding, Path("/x/wt/app-455"))
        container = self.binding.vault_path / self.binding.project_container
        self.assertEqual(runtime.parts[: len(container.parts)], container.parts)

    def test_two_worktrees_with_same_basename_never_share_runtime_dir(self) -> None:
        """Risk R1: 20 live worktrees must not contend for one journal."""
        first = obsidian_binding.runtime_dir(self.binding, Path("/x/wt/app-455"))
        second = obsidian_binding.runtime_dir(self.binding, Path("/y/wt/app-455"))
        self.assertNotEqual(first, second)

    def test_runtime_dir_is_deterministic(self) -> None:
        first = obsidian_binding.runtime_dir(self.binding, Path("/x/wt/app-455"))
        second = obsidian_binding.runtime_dir(self.binding, Path("/x/wt/app-455"))
        self.assertEqual(first, second)

    def test_slug_is_stable_across_trailing_slash_and_dot_segments(self) -> None:
        first = obsidian_binding.worktree_slug(Path("/x/wt/app-455"))
        second = obsidian_binding.worktree_slug(Path("/x/wt/./app-455/"))
        self.assertEqual(first, second)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_slug_is_identical_through_a_symlinked_ancestor(self) -> None:
        """macOS spells /var and /private/var for the same directory.

        Hashing the unresolved path would give one worktree two runtime
        directories depending on the caller's spelling.
        """
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real" / "app-455"
            real.mkdir(parents=True)
            link = Path(tmp) / "link"
            os.symlink(Path(tmp) / "real", link)
            self.assertEqual(
                obsidian_binding.worktree_slug(real),
                obsidian_binding.worktree_slug(link / "app-455"),
            )

    def test_slug_sanitizes_filesystem_hostile_characters(self) -> None:
        slug = obsidian_binding.worktree_slug(Path("/x/wt/dev-apk-1.0.1+2"))
        self.assertNotIn("/", slug)
        self.assertNotIn(" ", slug)
        self.assertTrue(slug.strip(), "slug must not be empty")

    def test_state_and_journal_paths_sit_in_runtime_dir(self) -> None:
        worktree = Path("/x/wt/app-455")
        runtime = obsidian_binding.runtime_dir(self.binding, worktree)
        self.assertEqual(
            obsidian_binding.state_path(self.binding, worktree), runtime / "STATE.md"
        )
        self.assertEqual(
            obsidian_binding.journal_path(self.binding, worktree),
            runtime / "ACTION_JOURNAL.json",
        )


class ContainmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        self.addCleanup(self._tmp.cleanup)
        self.vault = Path(self._tmp.name) / "vault"
        (self.vault / "Projects" / "Demo").mkdir(parents=True)
        (self.vault / "Projects" / "Other").mkdir(parents=True)
        write_binding(
            self.repo,
            vault_path=str(self.vault),
            project_container="Projects/Demo",
        )
        self.binding = obsidian_binding.load(self.repo)

    def test_path_inside_container_is_recognised(self) -> None:
        target = self.vault / "Projects" / "Demo" / "sessions" / "s.md"
        self.assertTrue(obsidian_binding.is_inside_container(self.binding, target))

    def test_sibling_project_is_outside_container(self) -> None:
        target = self.vault / "Projects" / "Other" / "note.md"
        self.assertFalse(obsidian_binding.is_inside_container(self.binding, target))

    def test_prefix_lookalike_directory_is_outside_container(self) -> None:
        """'Demo-archive' must not pass as 'Demo'."""
        target = self.vault / "Projects" / "Demo-archive" / "note.md"
        self.assertFalse(obsidian_binding.is_inside_container(self.binding, target))

    def test_dotdot_traversal_is_outside_container(self) -> None:
        target = self.vault / "Projects" / "Demo" / ".." / "Other" / "note.md"
        self.assertFalse(obsidian_binding.is_inside_container(self.binding, target))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_symlink_escaping_the_container_is_outside(self) -> None:
        link = self.vault / "Projects" / "Demo" / "escape"
        os.symlink(self.vault / "Projects" / "Other", link)
        self.assertFalse(
            obsidian_binding.is_inside_container(self.binding, link / "note.md")
        )


if __name__ == "__main__":
    unittest.main()
