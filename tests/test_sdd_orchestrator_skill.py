"""Behavioral contracts for the distributable SDD Orchestrator skill.

Installer behavior is exercised through the public CLI in real temporary Git
repositories. Static Markdown checks are limited to published bundle contracts:
frontmatter, safety boundaries, catalogues, and controller policy.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "orchestrate" / "sdd-orchestrator"
TEMPLATES = SKILL_ROOT / "templates"
ORCHESTRATION = TEMPLATES / ".hermes" / "orchestration"
INSTALLER = SKILL_ROOT / "scripts" / "install_project.py"

STAGE_AGENTS = {
    "specify.md": "SPECIFY",
    "clarify.md": "CLARIFY",
    "plan.md": "PLAN",
    "tasks.md": "TASKS",
    "implement.md": "IMPLEMENT",
    "test.md": "TEST",
    "review.md": "REVIEW",
}
SUB_AGENT_CONTRACTS = {
    "investigator.md": ("INVESTIGATOR", "[SPECIFY, CLARIFY, PLAN]", "EXECUTOR_RESULT_SCHEMA.json"),
    "impact-analyst.md": ("IMPACT_ANALYST", "[PLAN, TASKS]", "EXECUTOR_RESULT_SCHEMA.json"),
    "tdd-implementer.md": ("TDD_IMPLEMENTER", "[IMPLEMENT]", "EXECUTOR_RESULT_SCHEMA.json"),
    "test-runner.md": ("TEST_RUNNER", "[TEST]", "EXECUTOR_RESULT_SCHEMA.json"),
    "security-reviewer.md": ("SECURITY_REVIEWER", "[REVIEW]", "REVIEW_RESULT_SCHEMA.json"),
    "code-reviewer.md": ("CODE_REVIEWER", "[REVIEW]", "REVIEW_RESULT_SCHEMA.json"),
    "tdd-guardian.md": ("TDD_GUARDIAN", "[TEST, REVIEW]", "REVIEW_RESULT_SCHEMA.json"),
    "regression-hunter.md": ("REGRESSION_HUNTER", "[TEST, REVIEW]", "REVIEW_RESULT_SCHEMA.json"),
    "api-contract-auditor.md": ("API_CONTRACT_AUDITOR", "[PLAN, REVIEW]", "REVIEW_RESULT_SCHEMA.json"),
    "performance-auditor.md": ("PERFORMANCE_AUDITOR", "[PLAN, REVIEW]", "REVIEW_RESULT_SCHEMA.json"),
    "documentation-writer.md": ("DOCUMENTATION_WRITER", "[IMPLEMENT, REVIEW]", "EXECUTOR_RESULT_SCHEMA.json"),
    "architecture-guardian.md": ("ARCHITECTURE_GUARDIAN", "[PLAN, REVIEW]", "REVIEW_RESULT_SCHEMA.json"),
    "spec-consistency-guardian.md": ("SPEC_CONSISTENCY_GUARDIAN", "[TASKS, REVIEW]", "REVIEW_RESULT_SCHEMA.json"),
    "data-flow-tracer.md": ("DATA_FLOW_TRACER", "[PLAN, IMPLEMENT]", "EXECUTOR_RESULT_SCHEMA.json"),
    "release-readiness-auditor.md": ("RELEASE_READINESS_AUDITOR", "[REVIEW]", "REVIEW_RESULT_SCHEMA.json"),
    "dependency-auditor.md": ("DEPENDENCY_AUDITOR", "[PLAN, REVIEW]", "REVIEW_RESULT_SCHEMA.json"),
    "project-context-guardian.md": ("PROJECT_CONTEXT_GUARDIAN", "[SPECIFY, PLAN, IMPLEMENT]", "EXECUTOR_RESULT_SCHEMA.json"),
    "pr-reviewer.md": ("PR_REVIEWER", "[REVIEW]", "REVIEW_RESULT_SCHEMA.json"),
}
FORBIDDEN_ACTIONS = (
    "commit",
    "push",
    "open a pr",
    "mutate a backend",
    "update an external system",
    "run unapproved e2e",
)


def frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    self_contained = re.match(r"\A---\n(?P<body>.*?)\n---\n", text, re.S)
    if self_contained is None:
        raise AssertionError(f"{path} has no leading frontmatter")
    pairs = [line.split(": ", 1) for line in self_contained.group("body").splitlines() if ": " in line]
    if len({key for key, _ in pairs}) != len(pairs):
        raise AssertionError(f"{path} repeats a frontmatter key")
    return dict(pairs)


def normalized(text: str) -> str:
    return " ".join(text.lower().split())


def markdown_first_column(text: str, heading: str) -> set[str]:
    section = text.split(heading, 1)[1].split("\n## ", 1)[0]
    return {
        match.group(1)
        for line in section.splitlines()
        if (match := re.match(r"^\| `([a-z][a-z0-9-]+)` \|", line))
    }


class BundleContractTests(unittest.TestCase):
    def test_payload_has_layered_architecture_without_flat_runtime_files(self) -> None:
        for layer in ("agents", "contracts", "hooks", "policies", "runtime", "schemas", "sub-agents", "tests"):
            with self.subTest(layer=layer):
                self.assertTrue((ORCHESTRATION / layer).is_dir())
        self.assertEqual([], list(ORCHESTRATION.glob("*.py")))
        self.assertEqual([], list(ORCHESTRATION.glob("*.json")))

    def test_stage_agent_metadata_and_safety_boundaries_are_complete(self) -> None:
        agents = ORCHESTRATION / "agents"
        self.assertEqual(set(STAGE_AGENTS), {path.name for path in agents.glob("*.md")})
        for filename, stage in STAGE_AGENTS.items():
            path = agents / filename
            meta = frontmatter(path)
            schema = "REVIEW_RESULT_SCHEMA.json" if stage == "REVIEW" else "EXECUTOR_RESULT_SCHEMA.json"
            policy = normalized(path.read_text(encoding="utf-8"))
            with self.subTest(agent=filename):
                self.assertEqual(stage, meta["stage"])
                self.assertEqual("CONTROLLER_SELECTED", meta["executor_policy"])
                self.assertEqual(f"../schemas/{schema}", meta["result_schema"])
                self.assertTrue((ORCHESTRATION / "schemas" / schema).is_file())
                for concept in ("never write `state.md`", "never spawn another worker", "controller alone decides transitions"):
                    self.assertIn(concept, policy)
                for action in FORBIDDEN_ACTIONS:
                    self.assertRegex(policy, rf"never [^.]*\b{re.escape(action)}\b")
                boundary = "write only to paths explicitly assigned" if stage == "IMPLEMENT" else "workspace is read-only"
                self.assertIn(boundary, policy)

    def test_stage_agents_enforce_context_and_acceptance_evidence(self) -> None:
        agents = ORCHESTRATION / "agents"
        for filename, stage in STAGE_AGENTS.items():
            policy = normalized((agents / filename).read_text(encoding="utf-8"))
            with self.subTest(agent=filename):
                self.assertIn("material", policy)
                self.assertIn("acceptance", policy)
                self.assertIn("evidence", policy)
                if stage != "REVIEW":
                    self.assertIn("context_assessment", policy)
                    self.assertIn("acceptance_checks", policy)
                if stage in {"IMPLEMENT", "TEST", "REVIEW"}:
                    for concept in (
                        "controller-owned acceptance mapping",
                        "criterion",
                        "verification method",
                        "verifier",
                        "slice assignment",
                    ):
                        self.assertIn(concept, policy)
                if stage in {"IMPLEMENT", "TEST"}:
                    self.assertIn("every acceptance check", policy)
                    self.assertIn("pass", policy)
                if stage == "REVIEW":
                    self.assertIn("green gates alone", policy)
                    self.assertIn("independently", policy)

    def test_sub_agent_metadata_and_controller_boundaries_are_complete(self) -> None:
        directory = ORCHESTRATION / "sub-agents"
        self.assertEqual(set(SUB_AGENT_CONTRACTS), {path.name for path in directory.glob("*.md")})
        for filename, (role, stages, schema) in SUB_AGENT_CONTRACTS.items():
            path = directory / filename
            meta = frontmatter(path)
            policy = normalized(path.read_text(encoding="utf-8"))
            with self.subTest(sub_agent=filename):
                self.assertEqual(role, meta["role"])
                self.assertEqual(stages, meta["allowed_stages"])
                self.assertEqual("CONTROLLER_SELECTED", meta["executor_policy"])
                self.assertEqual(f"../schemas/{schema}", meta["result_schema"])
                self.assertTrue((ORCHESTRATION / "schemas" / schema).is_file())
                for concept in (
                    "dispatched only by the controller", "never spawn another worker",
                    "never write `state.md`", "controller alone decides transitions",
                ):
                    self.assertIn(concept, policy)
                for action in FORBIDDEN_ACTIONS:
                    self.assertRegex(policy, rf"never [^.]*\b{re.escape(action)}\b")
                if filename == "pr-reviewer.md":
                    for boundary in (
                        "workspace is read-only",
                        "do not modify any file or fix findings",
                        "never post a review",
                    ):
                        self.assertIn(boundary, policy)

    def test_readme_catalogue_exactly_matches_shipped_sub_agents(self) -> None:
        shipped = {path.stem for path in (ORCHESTRATION / "sub-agents").glob("*.md")}
        documented = markdown_first_column((ROOT / "README.md").read_text(encoding="utf-8"), "## Sub-agents")
        self.assertEqual(shipped, documented)

    def test_dispatch_policy_names_only_shipped_specialists_and_defaults_to_no_dispatch(self) -> None:
        text = (ORCHESTRATION / "policies" / "DISPATCH_POLICY.md").read_text(encoding="utf-8")
        policy = normalized(text)
        for concept in (
            "default = do not dispatch", "pending decision", "if no decision changes, do not dispatch",
            "deterministic tools run before any sub-agent", "record which deterministic tool was tried",
            "escalating beyond the minimum path requires a recorded reason", "no_findings",
            "repeated iteration over unchanged evidence is forbidden",
        ):
            with self.subTest(concept=concept):
                self.assertIn(concept, policy)
        for term in ("grep", "git diff", "linter", "schema", "package manager", "low", "medium", "high", "critical", "fast", "standard", "deep"):
            self.assertIn(term, policy)

        shipped = {path.stem for path in (ORCHESTRATION / "sub-agents").glob("*.md")}
        routing = text.split("Routing suggestions by change shape", 1)[1].split("## Bounded iteration", 1)[0]
        referenced = set(re.findall(r"`([a-z][a-z0-9-]+)`", routing))
        self.assertEqual(set(), referenced - shipped)

    def test_entrypoint_is_compact_portable_and_explains_both_authorization_modes(self) -> None:
        entrypoint = (TEMPLATES / ".hermes.md").read_text(encoding="utf-8")
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        loop_policy = (ORCHESTRATION / "policies" / "LOOP_POLICY.md").read_text(encoding="utf-8")

        self.assertLessEqual(len(entrypoint), 8000)
        self.assertNotRegex(entrypoint, r"/(?:Users|home)/")
        for concept in (
            "one leaf worker at a time", "stage-specific context", "STATE authority",
            "EXECUTOR_RESULT_SCHEMA.json", "REVIEW_RESULT_SCHEMA.json", "cumulative global limits",
            "Schema 1 BOUNDED_AUTO requires a fresh deterministic preview and per-run confirmation",
            "Schema 2 LOCAL_DELIVERY proceeds within its authorized fixed cumulative scope/workspace limits",
            "total executor budget", "never recursively spawn workers",
            "BOUNDED_AUTO plans use Codex as their canonical executor",
        ):
            self.assertIn(concept, entrypoint)
        self.assertIn("Schema 1 BOUNDED_AUTO requires a fresh deterministic preview", skill)
        self.assertIn("Schema 2 LOCAL_DELIVERY uses its existing explicit authorization", skill)
        self.assertIn("Este fallback aplica-se somente a ações MANUAL", loop_policy)

    def test_controller_requires_scoped_project_onboarding_before_first_demand(self) -> None:
        entrypoint = normalized((TEMPLATES / ".hermes.md").read_text(encoding="utf-8"))
        skill = normalized((SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8"))
        for policy in (entrypoint, skill):
            self.assertIn("project_setup.md", policy)
            self.assertIn("ask only unresolved", policy)
            self.assertIn("issue tracker", policy)
            self.assertIn("obsidian", policy)
            self.assertIn("project-specific tools", policy)
            self.assertIn("never ask for credentials", policy)
            self.assertIn("before the first demand", policy)

    def test_documentation_uses_only_layered_orchestration_paths(self) -> None:
        documents = [
            ROOT / "README.md", ROOT / "docs" / "ARCHITECTURE.md", SKILL_ROOT / "SKILL.md",
            TEMPLATES / ".hermes.md", *ORCHESTRATION.rglob("*.md"),
        ]
        legacy_paths = (
            ".hermes/orchestration/GATES.md", ".hermes/orchestration/LOOP_POLICY.md",
            ".hermes/orchestration/ACTION_RECOVERY.md", ".hermes/orchestration/BOUNDED_AUTOMATION.md",
            ".hermes/orchestration/EXECUTOR_RESULT_SCHEMA.json", ".hermes/orchestration/REVIEW_RESULT_SCHEMA.json",
            ".hermes/orchestration/action_journal.py", ".hermes/orchestration/bounded_run_driver.py",
            ".hermes/orchestration/bounded_run_planner.py",
        )
        for document in documents:
            content = document.read_text(encoding="utf-8")
            for legacy_path in legacy_paths:
                with self.subTest(document=document, legacy_path=legacy_path):
                    self.assertNotIn(legacy_path, content)

    def test_controller_contracts_do_not_assume_a_specific_toolchain(self) -> None:
        allowed = {
            ORCHESTRATION / "runtime" / "detect_stack.py",
            ORCHESTRATION / "tests" / "test_detect_stack.py",
            ORCHESTRATION / "policies" / "GATES.md",
            ORCHESTRATION / "policies" / "LOOP_POLICY.md",
            ORCHESTRATION / "tests" / "test_bounded_run_planner.py",
            ORCHESTRATION / "README.md",
        }
        toolchain_terms = re.compile(
            r"\b(?:dart|flutter|fvm|pubspec|npm|pnpm|yarn|gradle|maven|cargo|bundler|composer|mix)\b|\.dart\b",
            re.IGNORECASE,
        )
        for path in sorted(ORCHESTRATION.rglob("*")):
            if not path.is_file() or path in allowed or path.suffix not in {".md", ".py", ".json"}:
                continue
            text = path.read_text(encoding="utf-8").replace("changed_dart_files_available", "")
            with self.subTest(path=path.relative_to(ORCHESTRATION)):
                self.assertIsNone(toolchain_terms.search(text))

    def test_repository_requires_pr_reviewer_for_every_pull_request(self) -> None:
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        template = (ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
        dispatch = (ORCHESTRATION / "policies" / "DISPATCH_POLICY.md").read_text(encoding="utf-8")
        self.assertIn("## Pull request review", agents)
        self.assertIn("sub-agents/pr-reviewer.md", agents)
        self.assertIn("baseRefName", agents)
        self.assertNotIn("git merge-base origin/main HEAD", agents)
        self.assertIn("pr-reviewer", template)
        self.assertIn("| Pull request | `pr-reviewer`", dispatch)


class PromptPolicyContractTests(unittest.TestCase):
    """Agent briefs are executable policy inputs, so their concepts are contracts."""

    ROLE_CONCEPTS = {
        "tdd-implementer.md": (
            "derive tests from business rules", "state the bug each test detects",
            "cover the happy path, boundaries, and failures", "do not mirror the implementation",
            "do not use mocks that make the outcome inevitable", "green tests alone are not sufficient evidence",
            "propose three simple production-code mutations", "write the focused test before production code",
        ),
        "test-runner.md": (
            "derive tests from business rules", "state the bug each test detects",
            "cover the happy path, boundaries, and failures", "do not mirror the implementation",
            "do not use mocks that make the outcome inevitable", "green tests alone are not sufficient evidence",
            "propose three simple production-code mutations", "workspace is read-only",
        ),
        "tdd-guardian.md": (
            "prove every finding by mutation", "revert each mutation before interpreting the result",
            "byte-identical to the captured baseline", "stays green against broken production code",
            "report proven findings separately from unproven suspicions",
        ),
        "regression-hunter.md": (
            "run the consumer's own suite", "separate a pre-existing failure",
            "green consumer suite proves nothing", "report proven findings separately from unproven suspicions",
        ),
        "api-contract-auditor.md": (
            "cite the exact field, path and source", "never invent a field, endpoint, status code",
            "unresolvable divergence as a gap", "never call a live api without explicit authorization",
            "nullability", "pagination", "error envelope",
        ),
        "security-reviewer.md": (
            "never reproduce a discovered secret value", "committed secret as compromised",
            "rotation", "hardcoded", "insecure storage", "log", "redact", "token", "session",
            "authorization", "personal data",
        ),
        "performance-auditor.md": (
            "measurement or a counted operation", "input size at which the cost becomes material",
            "no material finding is a valid", "never weaken a correctness guarantee", "n+1", "allocation",
        ),
        "documentation-writer.md": (
            "write only to paths explicitly assigned", "never document behavior that was not verified",
            "never invent a rationale", "remove documentation describing code that no longer exists",
            "never weaken or delete a warning, constraint or security note",
            "open question", "adr", "readme", "diagram", "changelog",
        ),
        "architecture-guardian.md": (
            "cite the declared rule", "never enforce a convention the project has not declared",
            "undeclared but consistent convention as a question", "violation this change introduced",
            "never invent an architectural rule", "circular",
        ),
        "spec-consistency-guardian.md": (
            "never infer a requirement", "unauthorized scope", "return `no_findings`",
            "quote the requirement identifier", "not implemented", "incomplete task", "does not prove",
        ),
        "data-flow-tracer.md": (
            "workspace is read-only", "trace only the path the demand touches", "never audit the whole project",
            "partial and name where it stopped", "never infer a hop", "side effect", "risk",
        ),
        "release-readiness-auditor.md": (
            "unverified item is `blocked`", "never infer that an unexecuted check would have passed",
            "name the human who accepted it", "ready_with_risk", "rollback", "feature flag",
        ),
        "dependency-auditor.md": (
            "never propose a new dependency when the project already has an equivalent",
            "hand it to `security-reviewer`", "hand it to `architecture-guardian`",
            "never add, upgrade or remove", "transitive", "lockfile",
        ),
        "project-context-guardian.md": (
            "read the stored context before reading the repository", "refresh only what changed",
            "never recreate documentation", ".hermes/obsidian.json", "never write outside the project container",
            "the code is the source for implemented behavior", "never invent the decision",
            "obsidian write proposal", "never copy secrets",
        ),
        "pr-reviewer.md": (
            "full diff from the merge base", "description, the author's summary or an existing approval",
            "confirmed, refuted or unaddressed", "check that did not run is not a passing check",
            "one improvement per pull request", "never post a review", "`no_findings`",
            "never enforce a preference the project has not declared",
            "scope", "tests", "changelog", "version", "breaking", "commits", "secrets", "ci",
            "security-reviewer", "dependency-auditor", "architecture-guardian", "regression-hunter",
        ),
    }

    def test_each_specialist_brief_preserves_its_decision_contract(self) -> None:
        for filename, concepts in self.ROLE_CONCEPTS.items():
            policy = normalized((ORCHESTRATION / "sub-agents" / filename).read_text(encoding="utf-8"))
            for concept in concepts:
                with self.subTest(sub_agent=filename, concept=concept):
                    self.assertIn(normalized(concept), policy)

    def test_read_only_auditors_forbid_delivery_edits_and_identify_unproven_findings(self) -> None:
        writing_roles = {"documentation-writer.md"}
        executor_roles = {
            "investigator.md", "impact-analyst.md", "tdd-implementer.md", "test-runner.md",
            "data-flow-tracer.md", "project-context-guardian.md", "code-reviewer.md",
        }
        proof_roles = {
            "tdd-guardian.md", "regression-hunter.md", "api-contract-auditor.md",
            "security-reviewer.md", "performance-auditor.md", "architecture-guardian.md",
        }
        for path in sorted((ORCHESTRATION / "sub-agents").glob("*.md")):
            if path.name in writing_roles or path.name in executor_roles:
                continue
            policy = normalized(path.read_text(encoding="utf-8"))
            with self.subTest(sub_agent=path.name):
                self.assertIn("workspace is read-only", policy)
                self.assertRegex(policy, r"never repair|do not modify any file or fix findings")
                if path.name in proof_roles:
                    self.assertIn("unproven suspicions", policy)

    def test_project_context_brief_contains_no_machine_specific_vault_path(self) -> None:
        content = (ORCHESTRATION / "sub-agents" / "project-context-guardian.md").read_text(encoding="utf-8")
        self.assertNotRegex(content, r"/(?:Users|home)/")
        self.assertNotIn("Obsidian Vault/", content)


class InstallerBehaviorTests(unittest.TestCase):
    def execute(self, *args: str, check: bool = True, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(args, text=True, capture_output=True, timeout=120, cwd=cwd)
        if check and result.returncode:
            self.fail(result.stdout + result.stderr)
        return result

    def initialize_repository(self, root: Path, *, marker: str = "fixture\n") -> None:
        self.execute("git", "init", "-q", "-b", "main", str(root))
        self.execute("git", "-C", str(root), "config", "user.email", "test@example.invalid")
        self.execute("git", "-C", str(root), "config", "user.name", "Test")
        (root / "README.md").write_text(marker, encoding="utf-8")
        self.execute("git", "-C", str(root), "add", "README.md")
        self.execute("git", "-C", str(root), "commit", "-qm", "fixture")

    def run_installer(self, target: Path, *, installer: Path = INSTALLER, apply: bool = False) -> subprocess.CompletedProcess[str]:
        arguments = [sys.executable, str(installer), "--target", str(target)]
        if apply:
            arguments.append("--apply")
        arguments.append("--json")
        return self.execute(*arguments, check=False)

    def filesystem_snapshot(self, root: Path) -> dict[str, tuple[str, int, bytes | str | None]]:
        snapshot: dict[str, tuple[str, int, bytes | str | None]] = {}
        for path in (root, *sorted(root.rglob("*"))):
            relative = "." if path == root else path.relative_to(root).as_posix()
            metadata = path.lstat()
            mode = stat.S_IMODE(metadata.st_mode)
            if stat.S_ISLNK(metadata.st_mode):
                snapshot[relative] = ("symlink", mode, os.readlink(path))
            elif stat.S_ISREG(metadata.st_mode):
                snapshot[relative] = ("file", mode, path.read_bytes())
            elif stat.S_ISDIR(metadata.st_mode):
                snapshot[relative] = ("directory", mode, None)
            else:
                snapshot[relative] = ("other", mode, None)
        return snapshot

    def repository_snapshot(self, root: Path) -> tuple[dict[str, str], dict[str, tuple[str, int, bytes | str | None]]]:
        git_state = {
            "head": self.execute("git", "-C", str(root), "rev-parse", "HEAD").stdout,
            "refs": self.execute("git", "-C", str(root), "show-ref", "--head").stdout,
            "index": self.execute("git", "-C", str(root), "ls-files", "--stage").stdout,
            "status": self.execute(
                "git", "-C", str(root), "status", "--porcelain=v2", "--branch", "--untracked-files=all"
            ).stdout,
            "worktree_diff": self.execute("git", "-C", str(root), "diff", "--binary").stdout,
            "index_diff": self.execute("git", "-C", str(root), "diff", "--cached", "--binary").stdout,
        }
        return git_state, self.filesystem_snapshot(root)

    def assert_blocked(self, result: subprocess.CompletedProcess[str], reason: str) -> None:
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        report = json.loads(result.stderr)
        self.assertEqual("BLOCKED", report["status"])
        self.assertIn(reason, report["reason"])

    def test_dry_run_detects_stack_without_writing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdd-dry-run-") as temp:
            target = Path(temp)
            self.initialize_repository(target)
            (target / "go.mod").write_text("module example\n", encoding="utf-8")
            self.execute("git", "-C", str(target), "add", "go.mod")
            self.execute("git", "-C", str(target), "commit", "-qm", "add go")
            before = self.execute("git", "-C", str(target), "status", "--porcelain").stdout

            result = self.run_installer(target)

            self.assertEqual(0, result.returncode, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual("READY", report["status"])
            self.assertEqual(["go"], [item["ecosystem"] for item in report["stack"]["ecosystems"]])
            self.assertEqual("DETECTED_UNVERIFIED", report["stack"]["status"])
            self.assertFalse((target / ".hermes").exists())
            self.assertEqual(before, self.execute("git", "-C", str(target), "status", "--porcelain").stdout)

    def test_dry_run_requests_only_project_orchestrator_onboarding(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdd-onboarding-") as temp:
            target = Path(temp)
            self.initialize_repository(target)

            result = self.run_installer(target)

            self.assertEqual(0, result.returncode, result.stderr)
            onboarding = json.loads(result.stdout)["onboarding"]
            self.assertEqual("REQUIRED", onboarding["status"])
            self.assertEqual("ORCHESTRATOR_ONLY", onboarding["scope"])
            self.assertTrue(onboarding["ask_only_unresolved"])
            self.assertEqual(
                ["issue_tracker", "obsidian", "project_tools"],
                [question["id"] for question in onboarding["questions"]],
            )
            self.assertTrue(all("none" in question["accepted_answers"] for question in onboarding["questions"]))

    def test_apply_creates_pending_project_onboarding_record(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdd-onboarding-record-") as temp:
            target = Path(temp)
            self.initialize_repository(target)

            result = self.run_installer(target, apply=True)

            self.assertEqual(0, result.returncode, result.stderr)
            setup = (target / ".hermes/orchestration/PROJECT_SETUP.md").read_text(encoding="utf-8")
            self.assertIn("status: PENDING", setup)
            self.assertIn("issue_tracker: UNRESOLVED", setup)
            self.assertIn("obsidian: UNRESOLVED", setup)
            self.assertIn("project_tools: UNRESOLVED", setup)
            self.assertIn("Ask only about orchestrator connectivity", setup)

    def test_repeated_run_asks_only_unresolved_onboarding_questions(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdd-onboarding-resume-") as temp:
            target = Path(temp)
            self.initialize_repository(target)
            self.assertEqual(0, self.run_installer(target, apply=True).returncode)
            setup_path = target / ".hermes/orchestration/PROJECT_SETUP.md"
            setup = setup_path.read_text(encoding="utf-8")
            setup = setup.replace("issue_tracker: UNRESOLVED", "issue_tracker: none")
            setup = setup.replace("project_tools: UNRESOLVED", "project_tools: none")
            setup_path.write_text(setup, encoding="utf-8")

            result = self.run_installer(target)

            self.assertEqual(0, result.returncode, result.stderr)
            onboarding = json.loads(result.stdout)["onboarding"]
            self.assertEqual("REQUIRED", onboarding["status"])
            self.assertEqual(["obsidian"], [question["id"] for question in onboarding["questions"]])

    def test_missing_onboarding_answer_fails_closed_as_unresolved(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdd-onboarding-invalid-") as temp:
            target = Path(temp)
            self.initialize_repository(target)
            self.assertEqual(0, self.run_installer(target, apply=True).returncode)
            setup_path = target / ".hermes/orchestration/PROJECT_SETUP.md"
            setup = setup_path.read_text(encoding="utf-8").replace("  project_tools: UNRESOLVED\n", "")
            setup_path.write_text(setup, encoding="utf-8")

            result = self.run_installer(target)

            self.assertEqual(0, result.returncode, result.stderr)
            question_ids = [question["id"] for question in json.loads(result.stdout)["onboarding"]["questions"]]
            self.assertIn("project_tools", question_ids)

    def test_invalid_onboarding_answer_markers_remain_unresolved(self) -> None:
        invalid_values = ("", "null", "~", "unresolved", "\"UNRESOLVED\"", "# missing", "\"", "'none'")
        with tempfile.TemporaryDirectory(prefix="sdd-onboarding-markers-") as temp:
            target = Path(temp)
            self.initialize_repository(target)
            self.assertEqual(0, self.run_installer(target, apply=True).returncode)
            setup_path = target / ".hermes/orchestration/PROJECT_SETUP.md"
            original = setup_path.read_text(encoding="utf-8")
            for value in invalid_values:
                with self.subTest(value=value):
                    setup_path.write_text(
                        original.replace("project_tools: UNRESOLVED", f"project_tools: {value}"),
                        encoding="utf-8",
                    )
                    result = self.run_installer(target)
                    self.assertEqual(0, result.returncode, result.stderr)
                    onboarding = json.loads(result.stdout)["onboarding"]
                    self.assertIn("project_tools", [question["id"] for question in onboarding["questions"]])

    def test_semantically_incomplete_answers_remain_unresolved(self) -> None:
        incomplete = {
            "issue_tracker": "\"GitHub\"",
            "obsidian": "\"yes\"",
            "project_tools": "\"Jira\"",
        }
        with tempfile.TemporaryDirectory(prefix="sdd-onboarding-semantic-") as temp:
            target = Path(temp)
            self.initialize_repository(target)
            self.assertEqual(0, self.run_installer(target, apply=True).returncode)
            setup_path = target / ".hermes/orchestration/PROJECT_SETUP.md"
            original = setup_path.read_text(encoding="utf-8")
            for question_id, value in incomplete.items():
                with self.subTest(question_id=question_id):
                    setup = original.replace("status: PENDING", "status: COMPLETE")
                    for key in incomplete:
                        setup = setup.replace(f"{key}: UNRESOLVED", f"{key}: none")
                    setup = setup.replace(f"{question_id}: none", f"{question_id}: {value}")
                    setup_path.write_text(setup, encoding="utf-8")
                    onboarding = json.loads(self.run_installer(target).stdout)["onboarding"]
                    self.assertFalse(onboarding["record_valid"])
                    self.assertIn("ANSWER_VALUE_INVALID", onboarding["record_issues"])
                    self.assertEqual([question_id], [question["id"] for question in onboarding["questions"]])

    def test_structured_answers_can_complete_onboarding(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdd-onboarding-structured-") as temp:
            target = Path(temp)
            self.initialize_repository(target)
            self.assertEqual(0, self.run_installer(target, apply=True).returncode)
            setup_path = target / ".hermes/orchestration/PROJECT_SETUP.md"
            setup = setup_path.read_text(encoding="utf-8").replace("status: PENDING", "status: COMPLETE")
            setup = setup.replace(
                "issue_tracker: UNRESOLVED",
                'issue_tracker: {"provider":"GitHub","project":"owner/repo","read":true,"write":false}',
            )
            setup = setup.replace(
                "obsidian: UNRESOLVED",
                'obsidian: {"vault":"/vault","project_container":"Projects/App"}',
            )
            setup = setup.replace(
                "project_tools: UNRESOLVED",
                'project_tools: [{"tool":"Jira","purpose":"read demands","read":true,"write":false}]',
            )
            setup_path.write_text(setup, encoding="utf-8")

            onboarding = json.loads(self.run_installer(target).stdout)["onboarding"]

            self.assertEqual("COMPLETE", onboarding["status"])
            self.assertTrue(onboarding["record_valid"])
            self.assertEqual([], onboarding["questions"])

    def test_invalid_onboarding_encoding_returns_fail_closed_json_report(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdd-onboarding-encoding-") as temp:
            target = Path(temp)
            self.initialize_repository(target)
            self.assertEqual(0, self.run_installer(target, apply=True).returncode)
            (target / ".hermes/orchestration/PROJECT_SETUP.md").write_bytes(b"\xff\xfe")

            result = self.run_installer(target)

            self.assertEqual(0, result.returncode, result.stderr)
            onboarding = json.loads(result.stdout)["onboarding"]
            self.assertEqual("REQUIRED", onboarding["status"])
            self.assertFalse(onboarding["record_valid"])
            self.assertEqual(
                ["issue_tracker", "obsidian", "project_tools"],
                [question["id"] for question in onboarding["questions"]],
            )

    def test_invalid_onboarding_schema_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdd-onboarding-schema-") as temp:
            target = Path(temp)
            self.initialize_repository(target)
            self.assertEqual(0, self.run_installer(target, apply=True).returncode)
            setup_path = target / ".hermes/orchestration/PROJECT_SETUP.md"
            setup = setup_path.read_text(encoding="utf-8")
            setup = setup.replace("schema_version: 1", "schema_version: 99")
            setup = setup.replace("issue_tracker: UNRESOLVED", "issue_tracker: none")
            setup = setup.replace("obsidian: UNRESOLVED", "obsidian: none")
            setup = setup.replace("project_tools: UNRESOLVED", "project_tools: none")
            setup_path.write_text(setup, encoding="utf-8")

            result = self.run_installer(target)

            self.assertEqual(0, result.returncode, result.stderr)
            onboarding = json.loads(result.stdout)["onboarding"]
            self.assertFalse(onboarding["record_valid"])
            self.assertEqual(
                ["issue_tracker", "obsidian", "project_tools"],
                [question["id"] for question in onboarding["questions"]],
            )

    def test_onboarding_status_must_match_answer_completion(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdd-onboarding-status-") as temp:
            target = Path(temp)
            self.initialize_repository(target)
            self.assertEqual(0, self.run_installer(target, apply=True).returncode)
            setup_path = target / ".hermes/orchestration/PROJECT_SETUP.md"
            pending = setup_path.read_text(encoding="utf-8")
            pending = pending.replace("issue_tracker: UNRESOLVED", "issue_tracker: none")
            pending = pending.replace("obsidian: UNRESOLVED", "obsidian: none")
            pending = pending.replace("project_tools: UNRESOLVED", "project_tools: none")
            setup_path.write_text(pending, encoding="utf-8")

            pending_result = json.loads(self.run_installer(target).stdout)["onboarding"]

            self.assertEqual("REQUIRED", pending_result["status"])
            self.assertFalse(pending_result["record_valid"])
            self.assertIn("STATUS_ANSWER_MISMATCH", pending_result["record_issues"])
            self.assertEqual([], pending_result["questions"])

            complete = pending.replace("status: PENDING", "status: COMPLETE")
            setup_path.write_text(complete, encoding="utf-8")
            resolved_result = json.loads(self.run_installer(target).stdout)["onboarding"]
            self.assertEqual("COMPLETE", resolved_result["status"])
            self.assertTrue(resolved_result["record_valid"])
            self.assertEqual([], resolved_result["questions"])

            complete = complete.replace("obsidian: none", "obsidian: UNRESOLVED")
            setup_path.write_text(complete, encoding="utf-8")
            complete_result = json.loads(self.run_installer(target).stdout)["onboarding"]

            self.assertEqual("REQUIRED", complete_result["status"])
            self.assertFalse(complete_result["record_valid"])
            self.assertIn("STATUS_ANSWER_MISMATCH", complete_result["record_issues"])
            self.assertEqual(["obsidian"], [question["id"] for question in complete_result["questions"]])

    def test_apply_report_reloads_the_created_onboarding_record(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdd-onboarding-apply-report-") as temp:
            target = Path(temp)
            self.initialize_repository(target)

            result = self.run_installer(target, apply=True)

            self.assertEqual(0, result.returncode, result.stderr)
            onboarding = json.loads(result.stdout)["onboarding"]
            self.assertTrue(onboarding["record_valid"])
            self.assertEqual("PENDING", onboarding["record_status"])
            self.assertEqual([], onboarding["record_issues"])

    def test_apply_installs_complete_bundle_initial_state_and_runnable_suite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdd-install-") as temp:
            target = Path(temp)
            self.initialize_repository(target)
            gitignore_before = "build/\n"
            (target / ".gitignore").write_text(gitignore_before, encoding="utf-8")
            self.execute("git", "-C", str(target), "add", ".gitignore")
            self.execute("git", "-C", str(target), "commit", "-qm", "ignore build")
            exclude = target / ".git" / "info" / "exclude"
            exclude.write_text("custom-local-entry\n", encoding="utf-8")

            result = self.run_installer(target, apply=True)

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue(json.loads(result.stdout)["applied"])
            expected_files = {
                path.relative_to(TEMPLATES).as_posix(): path
                for path in TEMPLATES.rglob("*")
                if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}
            }
            for relative, source in expected_files.items():
                installed = target / relative
                with self.subTest(file=relative):
                    self.assertTrue(installed.is_file())
                    self.assertEqual(source.read_bytes(), installed.read_bytes())

            generated = {
                ".hermes/orchestration/STATE.md",
                ".hermes/orchestration/PROJECT_SETUP.md",
                ".hermes/orchestration/ACTION_JOURNAL.json",
                ".hermes/orchestration/INCIDENTS.md",
            }
            installed_non_generated = {
                path.relative_to(target).as_posix()
                for path in target.rglob("*")
                if path.is_file()
                and "__pycache__" not in path.parts
                and path.suffix not in {".pyc", ".pyo"}
                and (path.name == ".hermes.md" or path.is_relative_to(target / ".hermes"))
                and path.relative_to(target).as_posix() not in generated
            }
            self.assertEqual(set(expected_files) - generated, installed_non_generated)

            state = (target / ".hermes/orchestration/STATE.md").read_text(encoding="utf-8")
            incidents = (target / ".hermes/orchestration/INCIDENTS.md").read_text(encoding="utf-8")
            for marker in ("schema_version: 2", "id: IDLE", "current: IDLE", "mode: MANUAL"):
                self.assertIn(marker, state)
            self.assertEqual("# SDD Orchestration Incidents\n\nNo incidents recorded.\n", incidents)
            journal = json.loads((target / ".hermes/orchestration/ACTION_JOURNAL.json").read_text(encoding="utf-8"))
            self.assertEqual("IDLE", journal["action"]["status"])
            self.assertEqual(str(target.resolve()), journal["workspace"]["path"])
            self.assertEqual(gitignore_before, (target / ".gitignore").read_text(encoding="utf-8"))
            self.assertIn("custom-local-entry", exclude.read_text(encoding="utf-8"))
            self.assertIn(".hermes/orchestration", exclude.read_text(encoding="utf-8"))
            self.assertEqual("", self.execute("git", "-C", str(target), "status", "--porcelain").stdout)

            suite = self.execute(
                sys.executable, "-m", "unittest", "discover",
                "-s", str(target / ".hermes/orchestration/tests"), "-p", "test_*.py",
                cwd=target,
            )
            self.assertIn("OK", suite.stderr)

    def test_second_run_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdd-idempotent-") as temp:
            target = Path(temp)
            self.initialize_repository(target)
            self.assertEqual(0, self.run_installer(target, apply=True).returncode)
            snapshot = {
                path.relative_to(target).as_posix(): path.read_bytes()
                for path in target.rglob("*")
                if path.is_file() and ".git" not in path.parts
            }

            repeated = self.run_installer(target, apply=True)

            self.assertEqual(0, repeated.returncode, repeated.stderr)
            self.assertEqual("ALREADY_INITIALIZED", json.loads(repeated.stdout)["status"])
            self.assertFalse(json.loads(repeated.stdout)["applied"])
            self.assertEqual(
                snapshot,
                {
                    path.relative_to(target).as_posix(): path.read_bytes()
                    for path in target.rglob("*")
                    if path.is_file() and ".git" not in path.parts
                },
            )

    def test_installer_blocks_non_root_tracked_conflicting_symlinked_and_partial_destinations(self) -> None:
        scenarios = ("non-root", "tracked", "conflict", "symlink", "partial")
        expected = {
            "non-root": "TARGET_NOT_REPOSITORY_ROOT",
            "tracked": "TRACKED_DESTINATION_PATH",
            "conflict": "CONFIG_CONFLICT",
            "symlink": "SYMLINK_REJECTED",
            "partial": "LOCAL_STATE_REQUIRES_REVIEW",
        }
        for scenario in scenarios:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory(prefix=f"sdd-{scenario}-") as temp:
                target = Path(temp)
                self.initialize_repository(target)
                invocation_target = target
                if scenario == "non-root":
                    invocation_target = target / "nested"
                    invocation_target.mkdir()
                elif scenario == "tracked":
                    (target / ".hermes.md").write_text("tracked\n", encoding="utf-8")
                    self.execute("git", "-C", str(target), "add", ".hermes.md")
                    self.execute("git", "-C", str(target), "commit", "-qm", "track config")
                elif scenario == "conflict":
                    (target / ".hermes.md").write_text("conflict\n", encoding="utf-8")
                elif scenario == "symlink":
                    real = target / "local-config"
                    real.mkdir()
                    (target / ".hermes").symlink_to(real, target_is_directory=True)
                else:
                    state = target / ".hermes/orchestration/STATE.md"
                    state.parent.mkdir(parents=True)
                    state.write_text("partial\n", encoding="utf-8")

                before = self.repository_snapshot(target)
                result = self.run_installer(invocation_target, apply=True)

                self.assert_blocked(result, expected[scenario])
                self.assertEqual(before, self.repository_snapshot(target))

    def test_python_cache_artifacts_in_bundle_are_not_installed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdd-cache-") as temp:
            root = Path(temp)
            copied_skill = root / "sdd-orchestrator"
            shutil.copytree(SKILL_ROOT, copied_skill)
            runtime = copied_skill / "templates/.hermes/orchestration/runtime"
            cache = runtime / "__pycache__"
            cache.mkdir(exist_ok=True)
            (cache / "tool.cpython-312.pyc").write_bytes(b"cache")
            (runtime / "tool.pyo").write_bytes(b"optimized")
            target = root / "target"
            target.mkdir()
            self.initialize_repository(target)

            result = self.run_installer(target, installer=copied_skill / "scripts/install_project.py", apply=True)

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertFalse((target / ".hermes/orchestration/runtime/__pycache__/tool.cpython-312.pyc").exists())
            self.assertFalse((target / ".hermes/orchestration/runtime/tool.pyo").exists())


if __name__ == "__main__":
    unittest.main()
