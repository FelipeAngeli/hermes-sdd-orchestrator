"""The visible Obsidian layout: fresh installs use it and `--upgrade` moves legacy containers to it.

Containers installed before the visible layout keep `.hermes/` and
`.hermes-runtime/`. The fixture builds one from a current install (rename both
roots back and drop `runtime_subpath` from the binding, as old releases wrote
it), then upgrades it.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_sdd_orchestrator_skill as skill_tests

INSTALLER = skill_tests.INSTALLER


class FreshInstallLayoutTests(unittest.TestCase):
    make_obsidian_fixture = skill_tests.InstallerBehaviorTests.make_obsidian_fixture
    run_installer = skill_tests.InstallerBehaviorTests.run_installer
    execute = skill_tests.InstallerBehaviorTests.execute
    initialize_repository = skill_tests.InstallerBehaviorTests.initialize_repository

    def test_obsidian_fresh_install_uses_the_visible_layout(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdd-obsidian-visible-") as temp:
            target, vault, container = self.make_obsidian_fixture(temp)

            result = self.run_installer(
                target, apply=True, typesafe_ai="install",
                obsidian_vault=vault, obsidian_project="Projects/App",
            )

            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            report = json.loads(result.stdout)
            self.assertTrue((container / "Hermes/orchestration/runtime/action_journal.py").is_file())
            self.assertTrue((container / "Hermes/orchestration/PROJECT_SETUP.md").is_file())
            self.assertTrue((container / "Hermes/obsidian.json").is_file())
            self.assertTrue((container / "Hermes/skills/typesafe-ai/SKILL.md").is_file())
            self.assertTrue((container / "Hermes/skills-lock.json").is_file())
            self.assertEqual(container.resolve() / "Hermes/orchestration", Path(report["controller_location"]))
            runtime = Path(report["worktree_runtime"])
            self.assertEqual("Hermes Runtime", runtime.relative_to(container.resolve()).parts[0])
            self.assertTrue((runtime / "STATE.md").is_file())
            self.assertFalse((container / ".hermes").exists())
            self.assertFalse((container / ".hermes-runtime").exists())
            self.assertEqual(
                "Hermes Runtime",
                json.loads((container / "Hermes/obsidian.json").read_text(encoding="utf-8"))["runtime_subpath"],
            )

    def test_local_install_still_uses_the_hidden_layout(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdd-local-hidden-") as temp:
            target = Path(temp)
            self.initialize_repository(target)

            result = self.run_installer(target, apply=True)

            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertTrue((target / ".hermes/orchestration/runtime/action_journal.py").is_file())
            self.assertFalse((target / "Hermes").exists())
            self.assertFalse((target / "Hermes Runtime").exists())


class LegacyLayoutUpgradeTests(unittest.TestCase):
    execute = skill_tests.InstallerUpgradeTests.execute
    initialize_repository = skill_tests.InstallerUpgradeTests.initialize_repository
    filesystem_snapshot = skill_tests.InstallerUpgradeTests.filesystem_snapshot
    repository_snapshot = skill_tests.InstallerUpgradeTests.repository_snapshot
    controller_snapshot = skill_tests.InstallerUpgradeTests.controller_snapshot
    load_installer_module = skill_tests.InstallerUpgradeTests.load_installer_module
    fixture = skill_tests.InstallerUpgradeTests.fixture
    install = skill_tests.InstallerUpgradeTests.install
    upgrade = skill_tests.InstallerUpgradeTests.upgrade
    run_upgrade_in_process = skill_tests.InstallerUpgradeTests.run_upgrade_in_process

    def legacy_container(self, temp: Path) -> tuple[Path, list[str], Path]:
        target, storage, root = self.fixture(temp, "obsidian")
        self.install(INSTALLER, target, storage)
        (root / "Hermes").rename(root / ".hermes")
        (root / "Hermes Runtime").rename(root / ".hermes-runtime")
        binding = root / ".hermes/obsidian.json"
        payload = json.loads(binding.read_text(encoding="utf-8"))
        del payload["runtime_subpath"]
        binding.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return target, storage, root

    def test_upgrade_moves_a_legacy_container_to_the_visible_layout(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdd-upgrade-layout-") as temp:
            target, storage, root = self.legacy_container(Path(temp))
            state = next((root / ".hermes-runtime").glob("*/STATE.md"))
            state_bytes, slug = state.read_bytes(), state.parent.name
            setup = (root / ".hermes/orchestration/PROJECT_SETUP.md").read_bytes()
            repository_before = self.repository_snapshot(target)
            before = self.controller_snapshot(root)

            code, plan = self.upgrade(target, storage)

            self.assertEqual(0, code, plan)
            self.assertEqual("UPGRADE_READY", plan["status"])
            self.assertEqual(
                [{"from": ".hermes-runtime", "to": "Hermes Runtime"}, {"from": ".hermes", "to": "Hermes"}],
                plan["layout_migration"]["renames"],
            )
            self.assertTrue(plan["layout_migration"]["binding_rewrite"])
            self.assertEqual(str(root / "Hermes/orchestration"), plan["controller_location"])
            self.assertEqual(before, self.controller_snapshot(root))

            code, applied = self.upgrade(target, storage, "--apply")

            self.assertEqual(0, code, applied)
            self.assertEqual("UPGRADED", applied["status"])
            for hidden in (".hermes", ".hermes-runtime"):
                self.assertFalse((root / hidden).exists(), hidden)
            self.assertEqual(state_bytes, (root / "Hermes Runtime" / slug / "STATE.md").read_bytes())
            self.assertEqual(setup, (root / "Hermes/orchestration/PROJECT_SETUP.md").read_bytes())
            binding = json.loads((root / "Hermes/obsidian.json").read_text(encoding="utf-8"))
            self.assertEqual("Hermes Runtime", binding["runtime_subpath"])
            self.assertTrue((root / "Hermes/orchestration/INSTALL_MANIFEST.json").is_file())
            self.assertTrue(Path(applied["backup"]).is_relative_to(root / "Hermes/orchestration/upgrade-backups"))
            self.assertEqual(repository_before, self.repository_snapshot(target))
            self.assertEqual("ALREADY_CURRENT", self.upgrade(target, storage)[1]["status"])
            self.assertEqual("ALREADY_INITIALIZED", self.install(INSTALLER, target, storage)["status"])

    def test_a_busy_legacy_runtime_blocks_the_migration(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdd-upgrade-layout-busy-") as temp:
            target, storage, root = self.legacy_container(Path(temp))
            journal = next((root / ".hermes-runtime").glob("*/ACTION_JOURNAL.json"))
            value = json.loads(journal.read_text(encoding="utf-8"))
            value["action"]["status"] = "DISPATCHED"
            journal.write_text(json.dumps(value), encoding="utf-8")
            before = self.controller_snapshot(root)

            code, blocked = self.upgrade(target, storage, "--apply")

            self.assertEqual(2, code)
            self.assertIn("UPGRADE_CONTROLLER_BUSY", blocked["reason"])
            self.assertEqual(before, self.controller_snapshot(root))

    def test_two_controller_roots_block_before_any_write(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdd-upgrade-layout-both-") as temp:
            target, storage, root = self.legacy_container(Path(temp))
            (root / "Hermes").mkdir()
            before = self.controller_snapshot(root)

            code, blocked = self.upgrade(target, storage, "--apply")

            self.assertEqual(2, code)
            self.assertIn("UPGRADE_OBSIDIAN_LAYOUT_CONFLICT", blocked["reason"])
            self.assertEqual(before, self.controller_snapshot(root))

    def test_a_visible_runtime_next_to_the_legacy_one_blocks(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdd-upgrade-layout-runtimes-") as temp:
            target, storage, root = self.legacy_container(Path(temp))
            (root / "Hermes Runtime").mkdir()
            before = self.controller_snapshot(root)

            code, blocked = self.upgrade(target, storage, "--apply")

            self.assertEqual(2, code)
            self.assertIn("UPGRADE_OBSIDIAN_LAYOUT_CONFLICT", blocked["reason"])
            self.assertEqual(before, self.controller_snapshot(root))

    def test_a_failed_commit_puts_both_hidden_roots_back(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdd-upgrade-layout-rollback-") as temp:
            target, storage, root = self.legacy_container(Path(temp))
            before = self.controller_snapshot(root)
            module = self.load_installer_module()
            # Both renames have happened when the commit point runs; its failure must undo them.
            failure = module.InstallError("injected commit failure")
            with mock.patch.object(module.upgrade, "_commit_manifest", side_effect=failure):
                with self.assertRaisesRegex(module.InstallError, "injected commit failure") as raised:
                    self.run_upgrade_in_process(module, target, storage)

            self.assertNotIn("ROLLBACK_FAILED", str(raised.exception))
            after = self.controller_snapshot(root)
            for snapshot in (before, after):
                snapshot.pop(".hermes/orchestration/.upgrade.lock", None)
            self.assertEqual(before, after)
            self.assertFalse((root / "Hermes").exists())
            self.assertFalse((root / "Hermes Runtime").exists())
            self.assertEqual("UPGRADE_READY", self.upgrade(target, storage)[1]["status"])

    def test_an_upgrade_killed_between_the_renames_completes_on_rerun(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdd-upgrade-layout-resume-") as temp:
            target, storage, root = self.legacy_container(Path(temp))
            module = self.load_installer_module()
            real_rename = module.layout_migration.rename_directory

            def kill_before_controller(root_path, source, destination):
                if source == ".hermes":
                    raise KeyboardInterrupt("simulated kill")
                return real_rename(root_path, source, destination)

            # A killed process runs no rollback: the runtime is visible, the controller still hidden.
            with mock.patch.object(module.layout_migration, "rename_directory", side_effect=kill_before_controller), \
                    mock.patch.object(module.upgrade, "_rollback", return_value=None):
                with self.assertRaises(KeyboardInterrupt):
                    self.run_upgrade_in_process(module, target, storage)
            self.assertTrue((root / "Hermes Runtime").is_dir())
            self.assertTrue((root / ".hermes").is_dir())

            code, applied = self.upgrade(target, storage, "--apply")

            self.assertEqual(0, code, applied)
            self.assertEqual("UPGRADED", applied["status"])
            self.assertEqual([{"from": ".hermes", "to": "Hermes"}], applied["layout_migration"]["renames"])
            self.assertFalse((root / ".hermes").exists())
            self.assertFalse((root / ".hermes-runtime").exists())
            self.assertEqual("ALREADY_CURRENT", self.upgrade(target, storage)[1]["status"])


if __name__ == "__main__":
    unittest.main()
