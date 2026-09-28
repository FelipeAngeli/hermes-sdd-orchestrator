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
            "spec-consistency-guardian.md": (
                "SPEC_CONSISTENCY_GUARDIAN",
                "[TASKS, REVIEW]",
                "REVIEW_RESULT_SCHEMA.json",
            ),
            "data-flow-tracer.md": (
                "DATA_FLOW_TRACER",
                "[PLAN, IMPLEMENT]",
                "EXECUTOR_RESULT_SCHEMA.json",
            ),
            "release-readiness-auditor.md": (
                "RELEASE_READINESS_AUDITOR",
                "[REVIEW]",
                "REVIEW_RESULT_SCHEMA.json",
            ),
            "dependency-auditor.md": (
                "DEPENDENCY_AUDITOR",
                "[PLAN, REVIEW]",
                "REVIEW_RESULT_SCHEMA.json",
            ),
            "project-context-guardian.md": (
                "PROJECT_CONTEXT_GUARDIAN",
                "[SPECIFY, PLAN]",
                "EXECUTOR_RESULT_SCHEMA.json",
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

    def test_dispatch_policy_defaults_to_not_dispatching(self) -> None:
        """The gate the bundle was missing: a default of NOT dispatching.

        Twelve sub-agents exist and the only rule governing them was that the
        controller "may" select one. A permission with no refusal condition is
        not a policy: it makes every specialist available for every demand and
        pushes cost up with each brief added. The policy inverts that — a
        dispatch must name the pending decision that depends on the answer, and
        an answer that changes no decision is waste by definition.
        """
        policies = SKILL_ROOT / "templates" / ".hermes" / "orchestration" / "policies"
        policy = policies / "DISPATCH_POLICY.md"
        self.assertTrue(policy.is_file(), "DISPATCH_POLICY.md must ship with the bundle")
        content = policy.read_text(encoding="utf-8")

        for rule in (
            "DEFAULT = DO NOT DISPATCH",
            "name the pending decision the answer resolves",
            "If no decision changes, do not dispatch",
            "Deterministic tools run before any sub-agent",
            "record which deterministic tool was tried and why it was insufficient",
            "Escalating beyond the minimum path requires a recorded reason",
            "NO_FINDINGS",
            "A repeated iteration over unchanged evidence is forbidden",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, content)

        for tool in ("grep", "git diff", "linter", "schema", "package manager"):
            with self.subTest(tool=tool):
                self.assertIn(tool, content.lower())

        for level in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
            with self.subTest(level=level):
                self.assertIn(level, content)

        for path in ("FAST", "STANDARD", "DEEP"):
            with self.subTest(path=path):
                self.assertIn(path, content)

        sub_agents = SKILL_ROOT / "templates" / ".hermes" / "orchestration" / "sub-agents"
        shipped = {path.stem for path in sub_agents.glob("*.md")}
        referenced = {
            name
            for name in re.findall(r"`([a-z][a-z0-9-]+)`", content)
            if name.endswith(
                ("-reviewer", "-auditor", "-guardian", "-hunter", "-writer", "-analyst", "-tracer")
            )
        }
        self.assertEqual(
            referenced - shipped,
            set(),
            "routing table names a sub-agent that does not ship",
        )

    def test_spec_consistency_guardian_never_infers_a_requirement(self) -> None:
        """Traceability is asserted at TASKS time and never rechecked.

        The tasks agent already requires every task to trace to a requirement,
        but nothing verifies that the chain held once code and tests landed.
        This role walks SPEC → PLAN → TASKS → CODE → TESTS and reports breaks in
        both directions: a requirement with no implementation, and code with no
        requirement behind it. Its defining refusal is inventing the missing
        link — a requirement inferred from code turns unauthorized scope into
        retroactively justified scope, which is the failure it exists to catch.
        """
        sub_agents = SKILL_ROOT / "templates" / ".hermes" / "orchestration" / "sub-agents"
        content = (sub_agents / "spec-consistency-guardian.md").read_text(encoding="utf-8")

        for rule in (
            "The workspace is read-only",
            "Never repair",
            "Never infer a requirement that the specification does not state",
            "Report code with no requirement behind it as unauthorized scope",
            "Return `NO_FINDINGS` when the chain is intact",
            "Quote the requirement identifier",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, content)

        for direction in (
            "not implemented",
            "unauthorized scope",
            "incomplete task",
            "does not prove",
            "documentation",
        ):
            with self.subTest(direction=direction):
                self.assertIn(direction, content.lower())

    def test_data_flow_tracer_stays_scoped_to_one_demand(self) -> None:
        """A tracer's failure mode is scope, not accuracy.

        Following data end to end invites mapping the whole system, which is the
        expensive habit the dispatch policy exists to prevent. The brief binds
        the trace to one demand's path and makes an honest partial trace with a
        stated stopping point preferable to a complete-looking one padded with
        inference.
        """
        sub_agents = SKILL_ROOT / "templates" / ".hermes" / "orchestration" / "sub-agents"
        content = (sub_agents / "data-flow-tracer.md").read_text(encoding="utf-8")

        for rule in (
            "The workspace is read-only",
            "Trace only the path the demand touches",
            "Never audit the whole project",
            "Report the trace as partial and name where it stopped",
            "Never infer a hop that was not read in the code",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, content)

        for hop in ("ui", "state", "repository", "side effect", "risk"):
            with self.subTest(hop=hop):
                self.assertIn(hop, content.lower())

    def test_release_readiness_auditor_cannot_hedge_its_verdict(self) -> None:
        """The tri-state verdict is where this role can quietly fail.

        READY_WITH_RISK is the comfortable answer: it never blocks anyone and
        never looks careless. Left undefined it becomes the default, and the
        audit stops meaning anything. The brief makes it the narrow case —
        a known, named, accepted risk with a decision owner — and forbids using
        it for an unverified item, which is BLOCKED.
        """
        sub_agents = SKILL_ROOT / "templates" / ".hermes" / "orchestration" / "sub-agents"
        content = (sub_agents / "release-readiness-auditor.md").read_text(encoding="utf-8")

        for rule in (
            "The workspace is read-only",
            "Never repair",
            "An unverified item is `BLOCKED`, never `READY_WITH_RISK`",
            "Never infer that an unexecuted check would have passed",
            "name the human who accepted it",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, content)

        for verdict in ("READY", "BLOCKED", "READY_WITH_RISK"):
            with self.subTest(verdict=verdict):
                self.assertIn(verdict, content)

        for surface in ("acceptance criteria", "migration", "feature flag", "rollback", "configuration"):
            with self.subTest(surface=surface):
                self.assertIn(surface, content.lower())

    def test_dependency_auditor_defers_overlapping_domains(self) -> None:
        """Two roles over one domain let each assume the other checked it.

        Dependency findings touch security (a known vulnerability) and
        architecture (a dependency crossing a layer), both of which already have
        owners here. The brief must therefore name what it hands off, and must
        prefer what the project already depends on over anything new — the
        cheapest dependency is the one already there.
        """
        sub_agents = SKILL_ROOT / "templates" / ".hermes" / "orchestration" / "sub-agents"
        content = (sub_agents / "dependency-auditor.md").read_text(encoding="utf-8")

        for rule in (
            "The workspace is read-only",
            "Never repair",
            "Never propose a new dependency when the project already has an equivalent",
            "hand it to `security-reviewer`",
            "hand it to `architecture-guardian`",
            "Never add, upgrade or remove a dependency",
            "`NO_FINDINGS`",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, content)

        for surface in ("transitive", "duplicate", "unmaintained", "version", "lockfile"):
            with self.subTest(surface=surface):
                self.assertIn(surface, content.lower())

    def test_project_context_guardian_is_cache_first_and_path_agnostic(self) -> None:
        """The most expensive role, so it must not run by default.

        Reading a whole project is exactly the global-context habit the dispatch
        policy exists to prevent; it only pays for itself if the result is
        persisted and reused across demands. The brief must therefore read the
        cache before the repository, refresh only what changed, and resolve the
        vault through the repo-local binding rather than any literal path, so a
        clone on another machine still works.
        """
        sub_agents = SKILL_ROOT / "templates" / ".hermes" / "orchestration" / "sub-agents"
        content = (sub_agents / "project-context-guardian.md").read_text(encoding="utf-8")

        for rule in (
            "Read the stored context before reading the repository",
            "Refresh only what changed",
            "Never recreate documentation that already exists",
            "resolve every vault path through `.hermes/obsidian.json`",
            "Never write outside the project container",
            "Never invent a convention the project does not follow",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, content)

        self.assertNotIn("/Users/", content)
        self.assertNotIn("Obsidian Vault/", content)

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

    def test_installer_reports_detected_stack_for_any_language(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdd-orchestrator-stack-") as temp:
            target = Path(temp)
            self.execute("git", "init", "-b", "main", str(target))
            self.execute("git", "-C", str(target), "config", "user.email", "test@example.invalid")
            self.execute("git", "-C", str(target), "config", "user.name", "Test")
            (target / "go.mod").write_text("module example\n", encoding="utf-8")
            self.execute("git", "-C", str(target), "add", "go.mod")
            self.execute("git", "-C", str(target), "commit", "-m", "fixture")

            report = json.loads(self.execute("python3", str(INSTALLER), "--target", str(target), "--json").stdout)
            self.assertEqual("READY", report["status"])
            self.assertEqual(["go"], [item["ecosystem"] for item in report["stack"]["ecosystems"]])
            self.assertEqual("DETECTED_UNVERIFIED", report["stack"]["status"])
            self.assertFalse((target / ".hermes").exists(), "dry run must not write")

    def test_payload_is_not_coupled_to_one_language(self) -> None:
        """Only the detector and the GATES reference table may name a specific ecosystem."""
        orchestration = SKILL_ROOT / "templates" / ".hermes" / "orchestration"
        allowed = {
            orchestration / "runtime" / "detect_stack.py",
            orchestration / "tests" / "test_detect_stack.py",
            orchestration / "policies" / "GATES.md",
            orchestration / "policies" / "LOOP_POLICY.md",  # toolchain examples list
            orchestration / "tests" / "test_bounded_run_planner.py",  # legacy-name regression tests
            orchestration / "README.md",  # lists the ecosystems the detector covers
        }
        pattern = re.compile(r"\b(?:dart|flutter|fvm|pubspec)\b|\.dart\b", re.IGNORECASE)
        for path in sorted(orchestration.rglob("*")):
            if not path.is_file() or path in allowed or path.suffix not in {".md", ".py", ".json"}:
                continue
            text = path.read_text(encoding="utf-8").replace("changed_dart_files_available", "")
            with self.subTest(path=path.relative_to(orchestration)):
                self.assertIsNone(pattern.search(text))

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
