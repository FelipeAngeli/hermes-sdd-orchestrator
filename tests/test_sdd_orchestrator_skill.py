from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "orchestrate" / "sdd-orchestrator"
INSTALLER = SKILL_ROOT / "scripts" / "install_project.py"


class SddOrchestratorSkillTests(unittest.TestCase):
    def test_installs_project_local_configuration_from_skill_bundle(self) -> None:
        self.assertTrue((SKILL_ROOT / "SKILL.md").is_file())
        self.assertTrue(INSTALLER.is_file())
        self.assertTrue((SKILL_ROOT / "templates" / ".hermes.md").is_file())

        with tempfile.TemporaryDirectory(prefix="sdd-orchestrator-skill-") as temp:
            target = Path(temp)
            self.execute("git", "init", "-b", "main", str(target))
            self.execute("git", "-C", str(target), "config", "user.email", "test@example.invalid")
            self.execute("git", "-C", str(target), "config", "user.name", "Test")
            (target / "README.md").write_text("fixture\n", encoding="utf-8")
            self.execute("git", "-C", str(target), "add", "README.md")
            self.execute("git", "-C", str(target), "commit", "-m", "fixture")

            dry_run = self.execute("python3", str(INSTALLER), "--target", str(target), "--json")
            self.assertEqual("READY", json.loads(dry_run.stdout)["status"])

            applied = self.execute("python3", str(INSTALLER), "--target", str(target), "--apply", "--json")
            self.assertTrue(json.loads(applied.stdout)["applied"])
            self.assertTrue((target / ".hermes" / "orchestration" / "STATE.md").is_file())

            repeated = self.execute("python3", str(INSTALLER), "--target", str(target), "--json")
            self.assertEqual("ALREADY_INITIALIZED", json.loads(repeated.stdout)["status"])
            self.assertEqual("", self.execute("git", "-C", str(target), "status", "--porcelain").stdout)

    def execute(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, text=True, capture_output=True, check=True, timeout=60)


if __name__ == "__main__":
    unittest.main()
