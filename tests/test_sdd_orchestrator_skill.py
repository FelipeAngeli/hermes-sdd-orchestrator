from __future__ import annotations

import json
import re
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

        for layer in ("agents", "contracts", "policies", "runtime", "schemas", "sub-agents", "tests"):
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

    def test_specialized_sub_agents_are_complete_and_controller_owned(self) -> None:
        sub_agents = SKILL_ROOT / "templates" / ".hermes" / "orchestration" / "sub-agents"
        expected = {
            "investigator.md": ("INVESTIGATOR", "[SPECIFY, CLARIFY, PLAN]", "EXECUTOR_RESULT_SCHEMA.json"),
            "impact-analyst.md": ("IMPACT_ANALYST", "[PLAN, TASKS]", "EXECUTOR_RESULT_SCHEMA.json"),
            "tdd-implementer.md": ("TDD_IMPLEMENTER", "[IMPLEMENT]", "EXECUTOR_RESULT_SCHEMA.json"),
            "test-runner.md": ("TEST_RUNNER", "[TEST]", "EXECUTOR_RESULT_SCHEMA.json"),
            "security-reviewer.md": ("SECURITY_REVIEWER", "[REVIEW]", "REVIEW_RESULT_SCHEMA.json"),
            "code-reviewer.md": ("CODE_REVIEWER", "[REVIEW]", "REVIEW_RESULT_SCHEMA.json"),
            "tdd-guardian.md": ("TDD_GUARDIAN", "[TEST, REVIEW]", "REVIEW_RESULT_SCHEMA.json"),
            "regression-hunter.md": ("REGRESSION_HUNTER", "[TEST, REVIEW]", "REVIEW_RESULT_SCHEMA.json"),
            "api-contract-auditor.md": (
                "API_CONTRACT_AUDITOR",
                "[PLAN, REVIEW]",
                "REVIEW_RESULT_SCHEMA.json",
            ),
            "performance-auditor.md": (
                "PERFORMANCE_AUDITOR",
                "[PLAN, REVIEW]",
                "REVIEW_RESULT_SCHEMA.json",
            ),
            "documentation-writer.md": (
                "DOCUMENTATION_WRITER",
                "[IMPLEMENT, REVIEW]",
                "EXECUTOR_RESULT_SCHEMA.json",
            ),
            "architecture-guardian.md": (
                "ARCHITECTURE_GUARDIAN",
                "[PLAN, REVIEW]",
                "REVIEW_RESULT_SCHEMA.json",
            ),
        }

        self.assertEqual(set(expected), {path.name for path in sub_agents.glob("*.md")})
        for filename, (role, stages, schema) in expected.items():
            path = sub_agents / filename
            content = path.read_text(encoding="utf-8")
            with self.subTest(path=path):
                self.assertIn(f"role: {role}", content)
                self.assertIn(f"allowed_stages: {stages}", content)
                self.assertIn("executor_policy: CONTROLLER_SELECTED", content)
                self.assertIn(f"result_schema: ../schemas/{schema}", content)
                self.assertTrue((sub_agents / ".." / "schemas" / schema).resolve().is_file())
                self.assertIn("Dispatched only by the controller", content)
                self.assertIn("Never spawn another worker", content)
                self.assertIn("Never write `STATE.md`", content)
                self.assertIn("The controller alone decides transitions", content)
                self.assertIn("Never commit, push, open a PR, mutate a backend", content)

    def test_tdd_quality_sub_agents_require_behavioral_test_design(self) -> None:
        sub_agents = SKILL_ROOT / "templates" / ".hermes" / "orchestration" / "sub-agents"
        required_rules = (
            "Derive tests from business rules",
            "State the bug each test detects",
            "Cover the happy path, boundaries, and failures",
            "Do not mirror the implementation",
            "Do not use mocks that make the outcome inevitable",
            "Green tests alone are not sufficient evidence",
            "Propose three simple production-code mutations",
        )

        for filename in ("tdd-implementer.md", "test-runner.md"):
            content = (sub_agents / filename).read_text(encoding="utf-8")
            for rule in required_rules:
                with self.subTest(filename=filename, rule=rule):
                    self.assertIn(rule, content)

    def test_audit_sub_agents_require_empirical_proof_over_reading(self) -> None:
        """A finding these two report must be reproducible, not an impression.

        Reading a test to decide it is weak, or reading a diff to decide a
        consumer broke, produces plausible prose the controller cannot act on.
        Both briefs therefore demand execution, forbid repairs, and keep
        unproven suspicions in a separate section of the report.
        """
        sub_agents = SKILL_ROOT / "templates" / ".hermes" / "orchestration" / "sub-agents"
        shared_rules = (
            "The workspace is read-only",
            "Never repair",
            "Report proven findings separately from unproven suspicions",
        )

        guardian = (sub_agents / "tdd-guardian.md").read_text(encoding="utf-8")
        for rule in (
            *shared_rules,
            "Prove every finding by mutation",
            "Revert each mutation before interpreting the result",
            "Confirm the workspace is byte-identical to the captured baseline",
            "A test that stays green against broken production code is a proven false positive",
        ):
            with self.subTest(agent="tdd-guardian", rule=rule):
                self.assertIn(rule, guardian)

        hunter = (sub_agents / "regression-hunter.md").read_text(encoding="utf-8")
        for rule in (
            *shared_rules,
            "Run the consumer's own suite",
            "Separate a pre-existing failure from a failure this change introduced",
            "A green consumer suite proves nothing when it never exercises the affected path",
        ):
            with self.subTest(agent="regression-hunter", rule=rule):
                self.assertIn(rule, hunter)

    def test_api_contract_auditor_ranks_sources_and_refuses_to_guess(self) -> None:
        """Contract drift is decided by evidence, never by plausibility.

        Three sources disagree in practice: the client's models, the published
        specification, and the server actually deployed. An auditor that picks
        the convenient one, or invents a field to close a gap, produces a
        confident answer that breaks in production. The brief therefore fixes a
        source hierarchy, forbids inventing any contract element, and requires
        an unresolvable divergence to be reported as a gap for human decision.
        """
        sub_agents = SKILL_ROOT / "templates" / ".hermes" / "orchestration" / "sub-agents"
        content = (sub_agents / "api-contract-auditor.md").read_text(encoding="utf-8")

        for rule in (
            "The workspace is read-only",
            "Never repair",
            "Report proven findings separately from unproven suspicions",
            "Cite the exact field, path and source for every divergence",
            "Never invent a field, endpoint, status code, enum value or nullability",
            "Report an unresolvable divergence as a gap and request a decision",
            "Never call a live API without explicit authorization",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, content)

        for surface in (
            "nullability",
            "enum",
            "required",
            "obsolete",
            "pagination",
            "error envelope",
            "unit",
        ):
            with self.subTest(surface=surface):
                self.assertIn(surface, content.lower())

    def test_security_reviewer_covers_secrets_storage_and_log_exposure(self) -> None:
        """Credential exposure is the failure mode that survives code review.

        An injected token or a logged document number reads as ordinary code:
        nothing crashes, tests stay green, and the leak is only visible to
        someone looking for it. The brief therefore names the disclosure
        surfaces explicitly, and forbids the two ways an audit can make things
        worse — pasting the secret into the report, and quietly "fixing" it in
        a way that leaves the value live in history.
        """
        sub_agents = SKILL_ROOT / "templates" / ".hermes" / "orchestration" / "sub-agents"
        content = (sub_agents / "security-reviewer.md").read_text(encoding="utf-8")

        for rule in (
            "Never reproduce a discovered secret value",
            "Report a committed secret as compromised and requiring rotation",
            "Never repair",
            "Report proven findings separately from unproven suspicions",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, content)

        for surface in (
            "hardcoded",
            "rotation",
            "insecure storage",
            "log",
            "redact",
            "token",
            "session",
            "authorization",
            "personal data",
        ):
            with self.subTest(surface=surface):
                self.assertIn(surface, content.lower())

    def test_performance_auditor_requires_measurement_over_intuition(self) -> None:
        """Performance is where plausible reasoning is most often wrong.

        Any code can be described as potentially slow, so a brief that permits
        intuition produces endless unfalsifiable findings and sends the team
        optimizing whatever reads badly. This role therefore reports a cost
        only with a measurement or a counted operation behind it, states the
        input size at which it matters, and is explicitly allowed to conclude
        that nothing is worth changing.
        """
        sub_agents = SKILL_ROOT / "templates" / ".hermes" / "orchestration" / "sub-agents"
        content = (sub_agents / "performance-auditor.md").read_text(encoding="utf-8")

        for rule in (
            "The workspace is read-only",
            "Never repair",
            "Report proven findings separately from unproven suspicions",
            "Report a cost only with a measurement or a counted operation behind it",
            "State the input size at which the cost becomes material",
            "Reporting no material finding is a valid and useful result",
            "Never weaken a correctness guarantee to gain speed",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, content)

        for surface in (
            "duplicate",
            "cache",
            "n+1",
            "index",
            "pagination",
            "rebuild",
            "allocation",
            "leak",
            "main thread",
        ):
            with self.subTest(surface=surface):
                self.assertIn(surface, content.lower())

    def test_documentation_writer_is_the_only_writing_audit_role(self) -> None:
        """This role writes, so its failure mode is inverted.

        Every other audit role is read-only and risks a wrong finding. A writer
        risks something worse: fluent prose describing code that does not exist,
        which readers trust precisely because it reads well. The brief therefore
        grounds every statement in a verified symbol, confines writes to
        controller-assigned paths, and requires deleting documentation for code
        that is gone rather than leaving a plausible description of nothing.
        """
        sub_agents = SKILL_ROOT / "templates" / ".hermes" / "orchestration" / "sub-agents"
        content = (sub_agents / "documentation-writer.md").read_text(encoding="utf-8")

        for rule in (
            "Write only to paths explicitly assigned by the controller",
            "Never document behavior that was not verified in the code",
            "Never invent a rationale for a decision",
            "Remove documentation describing code that no longer exists",
            "Never weaken or delete a warning, constraint or security note",
            "Record an unexplained decision as an open question",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, content)

        for artifact in ("adr", "readme", "diagram", "changelog"):
            with self.subTest(artifact=artifact):
                self.assertIn(artifact, content.lower())

    def test_architecture_guardian_enforces_declared_rules_not_its_own_taste(self) -> None:
        """An architecture role fails by importing opinion as law.

        Every codebase violates someone's preferred architecture, so a guardian
        that reasons from general principle produces endless findings and
        rewrites a team's deliberate choices as defects. This brief binds it to
        the project's own declared rules, requires citing the rule a violation
        breaks, and forces an undeclared convention to be reported as a question
        rather than enforced. It also separates a violation the change
        introduced from one it merely inherited.
        """
        sub_agents = SKILL_ROOT / "templates" / ".hermes" / "orchestration" / "sub-agents"
        content = (sub_agents / "architecture-guardian.md").read_text(encoding="utf-8")

        for rule in (
            "The workspace is read-only",
            "Never repair",
            "Cite the declared rule each violation breaks",
            "Never enforce a convention the project has not declared",
            "Report an undeclared but consistent convention as a question",
            "Distinguish a violation this change introduced from one it inherited",
            "Never invent an architectural rule",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, content)

        for surface in (
            "presentation",
            "domain",
            "data",
            "inversion",
            "abstraction",
            "leak",
            "circular",
        ):
            with self.subTest(surface=surface):
                self.assertIn(surface, content.lower())

    def test_readme_documents_every_shipped_sub_agent(self) -> None:
        """The README must not silently fall behind the bundle.

        A reader decides whether this skill does what they need from the README
        alone. A sub-agent that ships without appearing there is invisible, and
        a sub-agent listed after being removed is worse — the reader plans
        around a role that does not exist. Binding the document to the directory
        makes both states a test failure instead of a discovery months later.
        """
        sub_agents = SKILL_ROOT / "templates" / ".hermes" / "orchestration" / "sub-agents"
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        shipped = {path.stem for path in sub_agents.glob("*.md")}
        self.assertTrue(shipped, "no sub-agent briefs found to document")

        for name in sorted(shipped):
            with self.subTest(sub_agent=name):
                self.assertIn(name, readme)

        documented = {
            name
            for name in re.findall(r"`([a-z][a-z0-9-]+)`", readme)
            if name.endswith(("-reviewer", "-auditor", "-guardian", "-hunter", "-writer", "-analyst", "-implementer", "-runner"))
        }
        self.assertEqual(
            documented - shipped,
            set(),
            "README documents a sub-agent that is no longer shipped",
        )

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
            for filename in (
                "investigator.md",
                "impact-analyst.md",
                "tdd-implementer.md",
                "test-runner.md",
                "security-reviewer.md",
                "code-reviewer.md",
            ):
                installed = target / ".hermes" / "orchestration" / "sub-agents" / filename
                bundled = (
                    SKILL_ROOT
                    / "templates"
                    / ".hermes"
                    / "orchestration"
                    / "sub-agents"
                    / filename
                )
                self.assertTrue(installed.is_file())
                self.assertEqual(bundled.read_text(encoding="utf-8"), installed.read_text(encoding="utf-8"))
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
