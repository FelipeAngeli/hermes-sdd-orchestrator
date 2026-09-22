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
    def test_project_payload_has_layered_architecture(self) -> None:
        orchestration = SKILL_ROOT / "templates" / ".hermes" / "orchestration"

        for layer in ("agents", "contracts", "policies", "runtime", "schemas", "tests"):
            self.assertTrue((orchestration / layer).is_dir(), layer)

        self.assertEqual([], list(orchestration.glob("*.py")))
        self.assertEqual([], list(orchestration.glob("*.json")))

    def test_stage_agents_are_complete_and_controller_safe(self) -> None:
        agents = SKILL_ROOT / "templates" / ".hermes" / "orchestration" / "agents"
        expected = {
            "specify.md": "SPECIFY",
            "clarify.md": "CLARIFY",
            "plan.md": "PLAN",
            "tasks.md": "TASKS",
            "implement.md": "IMPLEMENT",
            "test.md": "TEST",
            "review.md": "REVIEW",
        }

        self.assertEqual(set(expected), {path.name for path in agents.glob("*.md")})
        for filename, stage in expected.items():
            content = (agents / filename).read_text(encoding="utf-8")
            schema = "REVIEW_RESULT_SCHEMA.json" if stage == "REVIEW" else "EXECUTOR_RESULT_SCHEMA.json"
            with self.subTest(filename=filename):
                self.assertIn(f"stage: {stage}", content)
                self.assertIn("executor_policy: CONTROLLER_SELECTED", content)
                self.assertIn(f"result_schema: ../schemas/{schema}", content)
                self.assertTrue((agents / ".." / "schemas" / schema).resolve().is_file())
                self.assertIn("Never write `STATE.md`", content)
                self.assertIn("Never spawn another worker", content)
                self.assertIn("The controller alone decides transitions", content)
                self.assertIn(
                    "Never commit, push, open a PR, mutate a backend, update an external system, or run unapproved E2E.",
                    content,
                )
                if stage == "IMPLEMENT":
                    self.assertIn("Write only to paths explicitly assigned by the controller", content)
                else:
                    self.assertIn("The workspace is read-only for this stage", content)

    def test_documentation_has_no_legacy_flat_orchestration_paths(self) -> None:
        orchestration = SKILL_ROOT / "templates" / ".hermes" / "orchestration"
        documents = [
            ROOT / "README.md",
            ROOT / "docs" / "ARCHITECTURE.md",
            SKILL_ROOT / "SKILL.md",
            SKILL_ROOT / "templates" / ".hermes.md",
            *orchestration.rglob("*.md"),
        ]
        legacy_paths = (
            ".hermes/orchestration/GATES.md",
            ".hermes/orchestration/LOOP_POLICY.md",
            ".hermes/orchestration/ACTION_RECOVERY.md",
            ".hermes/orchestration/BOUNDED_AUTOMATION.md",
            ".hermes/orchestration/EXECUTOR_RESULT_SCHEMA.json",
            ".hermes/orchestration/REVIEW_RESULT_SCHEMA.json",
            ".hermes/orchestration/action_journal.py",
            ".hermes/orchestration/bounded_run_driver.py",
            ".hermes/orchestration/bounded_run_planner.py",
        )

        for document in documents:
            content = document.read_text(encoding="utf-8")
            for legacy_path in legacy_paths:
                with self.subTest(document=document, legacy_path=legacy_path):
                    self.assertNotIn(legacy_path, content)

    def test_entrypoint_is_compact_portable_and_has_dispatch_context(self) -> None:
        entrypoint = SKILL_ROOT / "templates" / ".hermes.md"
        content = entrypoint.read_text(encoding="utf-8")

        self.assertLessEqual(len(content), 8000)
        for marker in (
            "one leaf worker at a time",
            "stage-specific context",
            "STATE authority",
            "EXECUTOR_RESULT_SCHEMA.json",
            "REVIEW_RESULT_SCHEMA.json",
            "LOCAL_DELIVERY",
            "schema 2",
            "schema 1",
            "cumulative global limits",
        ):
            self.assertIn(marker, content)
        self.assertNotRegex(content, r"/(?:Users|home)/")

    def test_loop_modes_distinguish_schema1_preview_from_schema2_authorization(self) -> None:
        content = (SKILL_ROOT / "templates" / ".hermes.md").read_text(encoding="utf-8")
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        loop_policy = (
            SKILL_ROOT
            / "templates"
            / ".hermes"
            / "orchestration"
            / "policies"
            / "LOOP_POLICY.md"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "Schema 1 BOUNDED_AUTO requires a fresh deterministic preview and per-run confirmation.",
            content,
        )
        self.assertIn(
            "Schema 2 LOCAL_DELIVERY proceeds within its authorized fixed cumulative scope/workspace limits; replanning does not require reapproval.",
            content,
        )
        self.assertNotIn(
            "`BOUNDED_AUTO` is allowed only after the user approves a fresh deterministic plan",
            content,
        )
        self.assertIn("one leaf worker at a time", content)
        self.assertIn("total executor budget", content)
        self.assertIn("never recursively spawn workers", content)
        self.assertIn("BOUNDED_AUTO plans use Codex as their canonical executor", content)
        self.assertIn("Schema 1 BOUNDED_AUTO requires a fresh deterministic preview", skill)
        self.assertIn("Schema 2 LOCAL_DELIVERY uses its existing explicit authorization", skill)
        self.assertNotIn("`BOUNDED_AUTO` never starts without an approved fresh plan", skill)
        self.assertIn("Este fallback aplica-se somente a ações MANUAL", loop_policy)

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
            self.assertTrue((target / ".hermes" / "orchestration" / "agents" / "implement.md").is_file())
            self.assertLessEqual(len((target / ".hermes.md").read_text(encoding="utf-8")), 8000)

            installed_protocol = target / ".hermes" / "orchestration" / "tests" / "test_protocol.py"
            validator_run = self.execute("python3", str(installed_protocol))
            self.assertIn("Ran", validator_run.stderr)
            self.assertIn("OK", validator_run.stderr)

            repeated = self.execute("python3", str(INSTALLER), "--target", str(target), "--json")
            self.assertEqual("ALREADY_INITIALIZED", json.loads(repeated.stdout)["status"])
            self.assertEqual("", self.execute("git", "-C", str(target), "status", "--porcelain").stdout)

    def execute(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, text=True, capture_output=True, check=True, timeout=60)


if __name__ == "__main__":
    unittest.main()
