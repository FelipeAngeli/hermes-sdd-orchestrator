"""`--upgrade` of a v13.0.9 installation, the last release with the retired sub-agent briefs."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import test_sdd_orchestrator_skill as skill_tests
from test_sdd_orchestrator_skill import ORCHESTRATION, ROOT, TEMPLATES, UPGRADE_MANIFEST, V13_COMMIT, at


class V13UpgradeTests(skill_tests.InstallerUpgradeTests):
    """Reuses the upgrade fixtures; only the tests below run for this class."""

    def v13_installer(self, temp: Path) -> Path:
        """The v13.0.9 skill (origin/main 67cae82) extracted from git, installer included."""
        archive = subprocess.run(
            ["git", "-C", str(ROOT), "archive", V13_COMMIT, "skills/orchestrate/sdd-orchestrator"],
            capture_output=True, check=False, timeout=120,
        )
        if archive.returncode:
            self.skipTest(f"v13 commit {V13_COMMIT} is not available: {archive.stderr.decode(errors='replace')}")
        destination = temp / "v13"
        destination.mkdir()
        subprocess.run(["tar", "-x", "-C", str(destination)], input=archive.stdout, check=True, timeout=120)
        return destination / "skills/orchestrate/sdd-orchestrator/scripts/install_project.py"

    def test_retired_template_paths_list_every_controller_file_removed_since_v13(self) -> None:
        module = self.load_installer_module()
        retired = module.constants.RETIRED_TEMPLATE_PATHS
        self.assertEqual(15, len(retired))
        self.assertTrue(all(path.startswith(".hermes/orchestration/sub-agents/") for path in retired))
        for path in retired:
            self.assertFalse((TEMPLATES / path).exists(), path)
        # Deprecated, not removed: it still ships.
        self.assertTrue((ORCHESTRATION / "runtime/bounded_loop_driver.py").is_file())
        listed = subprocess.run(
            ["git", "-C", str(ROOT), "ls-tree", "-r", "--name-only", V13_COMMIT, "skills/orchestrate/sdd-orchestrator/templates/"],
            capture_output=True, text=True, check=False, timeout=60,
        )
        if listed.returncode == 0:
            prefix = "skills/orchestrate/sdd-orchestrator/templates/"
            removed = {
                line[len(prefix):] for line in listed.stdout.splitlines()
                if line.startswith(prefix + ".hermes/orchestration/") and not (TEMPLATES / line[len(prefix):]).exists()
            }
            self.assertEqual(removed, set(retired))

    def test_upgrade_of_a_v13_install_removes_retired_sub_agent_briefs_with_backup(self) -> None:
        for mode in self.MODES:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(prefix="sdd-upgrade-v13-") as temp:
                temp_path = Path(temp)
                installer = self.v13_installer(temp_path)
                target, storage, root = self.fixture(temp_path, mode)
                self.install(installer, target, storage)
                self.assertFalse(at(root, UPGRADE_MANIFEST).exists())
                retired = self.load_installer_module().constants.RETIRED_TEMPLATE_PATHS
                originals = {path: at(root, path).read_bytes() for path in retired}
                self.assertEqual(15, len(originals))

                code, blocked = self.upgrade(target, storage, "--apply")
                self.assertEqual(2, code)
                unverified = [w for w in blocked["warnings"] if w.startswith("OBSOLETE_UNVERIFIED: ")]
                self.assertEqual(15, len(unverified), blocked["warnings"])
                self.assertTrue(all("--accept-current-as-baseline" in w for w in unverified))
                self.assertTrue(all(at(root, path).is_file() for path in retired))

                code, plan = self.upgrade(target, storage, "--accept-current-as-baseline")
                self.assertEqual(0, code, plan)
                self.assertEqual(sorted(retired), sorted(set(plan["changes"]["remove"]) & set(retired)))

                code, applied = self.upgrade(target, storage, "--accept-current-as-baseline", "--apply")

                self.assertEqual(0, code, applied)
                self.assertEqual("UPGRADED", applied["status"])
                backup = Path(applied["backup"])
                backup_manifest = json.loads((backup / "BACKUP_MANIFEST.json").read_text(encoding="utf-8"))
                for path, content in originals.items():
                    self.assertFalse(at(root, path).exists(), path)
                    self.assertEqual(content, (backup / "files" / path).read_bytes())
                    self.assertEqual("REMOVE", backup_manifest["files"][path]["action"])
                remaining = sorted(p.name for p in at(root, ".hermes/orchestration/sub-agents").iterdir())
                self.assertEqual(
                    sorted(p.name for p in (ORCHESTRATION / "sub-agents").iterdir()), remaining,
                )
                self.assertEqual("ALREADY_CURRENT", self.upgrade(target, storage)[1]["status"])


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromNames(
        f"{__name__}.V13UpgradeTests.{name}" for name in loader.getTestCaseNames(V13UpgradeTests)
        if name in vars(V13UpgradeTests)
    )


if __name__ == "__main__":
    unittest.main()
