"""Controller-owned acceptance tests for agent-manager PR 1 (registry, contract, config).

Pinned by SHA-256 outside the repository; the executor must not edit this file.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
sys.path.insert(0, str(RUNTIME))

import agent_registry  # noqa: E402
import delegation_config  # noqa: E402
import delegation_contract  # noqa: E402

PY = sys.executable


def _tree() -> dict[str, tuple[int, int]]:
    return {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in ROOT.rglob("*") if p.is_file() and "__pycache__" not in p.parts}


def _contract(**overrides):
    value = {
        "schema_version": 1,
        "task_id": "TICKET-1-T1",
        "parent_agent_id": "controller",
        "child_agent_id": "sdd-plan",
        "session_id": "20261020_140247_52c703",
        "workspace_id": "0123456789abcdef",
        "stage": "PLAN",
        "objective": "Plan the slice.",
        "input_artifacts": [{"path": "STAGE_CONTEXT.json", "sha256": "a" * 64}],
        "acceptance_criteria": ["AC-1"],
        "allowed_tools": ["Read", "Grep"],
        "dependencies": [],
        "timeout": 600,
        "retry_policy": {"max_attempts": 1},
        "token_budget": None,
        "output_schema": {"path": "schemas/EXECUTOR_RESULT_SCHEMA.json", "sha256": "b" * 64},
    }
    value.update(overrides)
    return value


class RegistryAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = agent_registry.build_registry(ROOT)
        cls.by_id = {agent["agent_id"]: agent for agent in cls.registry["agents"]}

    def test_registry_lists_existing_agents_only(self):
        stage_agents = {f"sdd-{s.lower()}" for s in ("SPECIFY", "CLARIFY", "PLAN", "TASKS", "IMPLEMENT", "TEST", "REVIEW")}
        roles = {"sdd-data-flow-tracer", "sdd-pr-reviewer", "sdd-project-context-guardian", "sdd-security-reviewer"}
        self.assertEqual(stage_agents | roles, set(self.by_id))
        on_disk = {p.stem for p in (ROOT / "agents").glob("*.md")} | {p.stem for p in (ROOT / "sub-agents").glob("*.md")}
        self.assertEqual(len(on_disk), len(self.by_id), "one registry agent per agent file, none invented")

    def test_registry_derives_stage_settings_from_executor_policy(self):
        import executor_launch

        policy = executor_launch.load_policy(ROOT / "policies" / "EXECUTORS.md")
        for stage in ("SPECIFY", "CLARIFY", "PLAN", "TASKS", "IMPLEMENT", "TEST", "REVIEW"):
            agent = self.by_id[f"sdd-{stage.lower()}"]
            settings = executor_launch.stage_settings(policy, stage, executor=None, model=None, timeout=None)
            self.assertEqual("STAGE", agent["kind"])
            self.assertEqual([stage], agent["stages"])
            self.assertEqual(settings["executor"], agent["executor"])
            self.assertEqual(settings["timeout_seconds"], agent["timeout_seconds"])
            self.assertEqual(executor_launch.is_writing_worker(settings), agent["writes"])
            self.assertTrue(agent["leaf_only"])
        self.assertTrue(self.by_id["sdd-implement"]["writes"])
        self.assertFalse(self.by_id["sdd-plan"]["writes"])

    def test_role_agents_are_read_only_and_stage_bounded(self):
        reviewer = self.by_id["sdd-security-reviewer"]
        self.assertEqual(("ROLE", "SECURITY_REVIEWER", ["REVIEW"], False), (reviewer["kind"], reviewer["role"], reviewer["stages"], reviewer["writes"]))
        self.assertTrue(reviewer["result_schema"].endswith("REVIEW_RESULT_SCHEMA.json"))

    def test_registry_hashes_sources_and_validates_against_schema(self):
        import hashlib

        import jsonschema

        schema = json.loads((ROOT / "schemas" / "AGENT_REGISTRY_SCHEMA.json").read_text())
        jsonschema.validate(self.registry, schema)
        agent = self.by_id["sdd-plan"]
        self.assertEqual(hashlib.sha256((ROOT / agent["source"]).read_bytes()).hexdigest(), agent["source_sha256"])
        self.assertRegex(self.registry["registry_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(self.registry["registry_sha256"], agent_registry.build_registry(ROOT)["registry_sha256"], "deterministic")

    def test_playbooks_are_capabilities_not_agents(self):
        names = {p["name"] for p in self.registry["playbooks"]}
        self.assertIn("sdd-tdd", names)
        self.assertFalse(names & set(self.by_id))

    def test_invalid_frontmatter_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "orchestration"
            for sub in ("agents", "sub-agents", "policies", "schemas"):
                (root / sub).mkdir(parents=True)
            (root / "agents" / "plan.md").write_text("no frontmatter here\n")
            with self.assertRaises(agent_registry.RegistryError) as caught:
                agent_registry.build_registry(root)
            self.assertEqual("REGISTRY_SOURCE_INVALID", caught.exception.status)

    def test_registry_cli_is_read_only(self):
        before = _tree()
        result = subprocess.run([PY, str(RUNTIME / "agent_registry.py"), "--root", str(ROOT), "--json"], capture_output=True, text=True, timeout=60)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(self.registry["registry_sha256"], json.loads(result.stdout)["registry_sha256"])
        self.assertEqual(before, _tree())


class ConfigAcceptance(unittest.TestCase):
    def test_shipped_config_is_off_and_single_worker(self):
        config, reason = delegation_config.load_config(delegation_config.DEFAULT_CONFIG_PATH)
        self.assertIsNone(reason)
        self.assertFalse(config["enabled"])
        self.assertFalse(config["concurrency_enabled"])
        self.assertEqual(1, delegation_config.effective_limits(config)["max_concurrent_worktrees"])
        self.assertEqual(("PREPARED_ONLY", "EXPLICIT", "BLOCK", False),
                         (config["worktree_management"], config["session_binding"], config["on_budget_exceeded"], config["auto_delegate_on_conflict"]))

    def _write(self, directory, value):
        path = Path(directory) / "DELEGATION.md"
        path.write_text("# Delegation\n\n```json\n" + json.dumps(value) + "\n```\n")
        return path

    def _shipped(self):
        config, _ = delegation_config.load_config(delegation_config.DEFAULT_CONFIG_PATH)
        return {k: v for k, v in config.items() if k != "sha256"}

    def test_concurrency_needs_both_flags_and_never_exceeds_three(self):
        with tempfile.TemporaryDirectory() as directory:
            only_concurrency = dict(self._shipped(), concurrency_enabled=True, max_concurrent_worktrees=3)
            config, _ = delegation_config.load_config(self._write(directory, only_concurrency))
            self.assertEqual(1, delegation_config.effective_limits(config)["max_concurrent_worktrees"])
            both = dict(only_concurrency, enabled=True)
            config, _ = delegation_config.load_config(self._write(directory, both))
            self.assertEqual(3, delegation_config.effective_limits(config)["max_concurrent_worktrees"])
            for key, value in (("max_concurrent_worktrees", 4), ("max_children_per_parent", 4), ("max_depth", 3)):
                config, reason = delegation_config.load_config(self._write(directory, dict(both, **{key: value})))
                self.assertEqual((None, "CONFIG_INVALID"), (config, reason), key)

    def test_unsafe_modes_and_unknown_keys_are_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            base = self._shipped()
            for key, value in (("worktree_management", "CREATE"), ("session_binding", "AUTO"), ("on_budget_exceeded", "WARN"),
                               ("auto_delegate_on_conflict", True), ("surprise", 1), ("bytes_per_token", 0)):
                config, reason = delegation_config.load_config(self._write(directory, dict(base, **{key: value})))
                self.assertEqual("CONFIG_INVALID", reason, key)
            self.assertEqual((None, "CONFIG_MISSING"), delegation_config.load_config(Path(directory) / "missing.md"))


class ContractAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = agent_registry.build_registry(ROOT)
        cls.config, _ = delegation_config.load_config(delegation_config.DEFAULT_CONFIG_PATH)

    def check(self, contract, **kw):
        return delegation_contract.validate_contract(contract, self.registry, self.config, **kw)

    def codes(self, result):
        return {error["code"] for error in result["errors"]}

    def test_valid_contract(self):
        result = self.check(_contract(), prompt_bytes=4000)
        self.assertEqual("VALID", result["status"], result)

    def test_missing_session_id_is_structured_and_never_guessed(self):
        for value in (None, ""):
            contract = _contract(session_id=value)
            result = self.check(contract)
            self.assertEqual("REJECTED", result["status"])
            self.assertIn("SESSION_ID_REQUIRED", self.codes(result))
            error = next(e for e in result["errors"] if e["code"] == "SESSION_ID_REQUIRED")
            self.assertIn("--session-id", error["next_step"])
            self.assertEqual(value, contract["session_id"], "the contract is never filled in")
        contract = _contract()
        del contract["session_id"]
        self.assertIn("SESSION_ID_REQUIRED", self.codes(self.check(contract)))

    def test_unknown_agents_and_disallowed_stage_are_rejected(self):
        self.assertIn("CHILD_AGENT_UNKNOWN", self.codes(self.check(_contract(child_agent_id="sdd-invented"))))
        self.assertIn("PARENT_AGENT_UNKNOWN", self.codes(self.check(_contract(parent_agent_id="nobody"))))
        self.assertIn("STAGE_NOT_ALLOWED", self.codes(self.check(_contract(child_agent_id="sdd-security-reviewer", stage="IMPLEMENT"))))

    def test_contract_cannot_widen_registry_permissions(self):
        self.assertIn("PERMISSION_WIDENING", self.codes(self.check(_contract(allowed_tools=["Read", "Bash"]))))
        self.assertIn("PERMISSION_WIDENING", self.codes(self.check(_contract(timeout=901))))
        self.assertIn("PERMISSION_WIDENING", self.codes(self.check(_contract(child_agent_id="sdd-implement", stage="IMPLEMENT", allowed_tools=["Read"]))))
        self.assertEqual("VALID", self.check(_contract(allowed_tools=[]))["status"])

    def test_dependencies_retry_and_output_schema_are_checked(self):
        self.assertIn("DEPENDENCY_INVALID", self.codes(self.check(_contract(dependencies=["TICKET-1-T1"]))))
        self.assertIn("DEPENDENCY_INVALID", self.codes(self.check(_contract(dependencies=["TICKET-1-T2", "TICKET-1-T2"]))))
        self.assertIn("CONTRACT_SCHEMA_INVALID", self.codes(self.check(_contract(retry_policy={"max_attempts": 3}))))
        mismatch = _contract(output_schema={"path": "schemas/REVIEW_RESULT_SCHEMA.json", "sha256": "b" * 64})
        self.assertIn("OUTPUT_SCHEMA_MISMATCH", self.codes(self.check(mismatch)))

    def test_contract_schema_has_all_fifteen_fields(self):
        schema = json.loads((ROOT / "schemas" / "DELEGATION_CONTRACT_SCHEMA.json").read_text())
        fields = {"task_id", "parent_agent_id", "child_agent_id", "session_id", "workspace_id", "stage", "objective", "input_artifacts",
                  "acceptance_criteria", "allowed_tools", "dependencies", "timeout", "retry_policy", "token_budget", "output_schema"}
        self.assertLessEqual(fields, set(schema["properties"]))
        self.assertFalse(schema.get("additionalProperties", True))

    def test_token_estimate_is_labelled_and_over_budget_blocks_for_review(self):
        result = self.check(_contract(token_budget=1000), prompt_bytes=4001)
        estimate = result["token_estimate"]
        self.assertEqual((1001, "BYTES_DIV_N", 4, "UNMEASURED", None, False),
                         (estimate["estimated_tokens"], estimate["method"], estimate["bytes_per_token"], estimate["usage_status"],
                          estimate["measured_tokens"], estimate["covers_provider_internal_context"]))
        self.assertEqual("REVIEW_REQUIRED", result["status"])
        self.assertIn("TOKEN_BUDGET_EXCEEDED", self.codes(result))
        self.assertEqual("VALID", self.check(_contract(token_budget=1000), prompt_bytes=4000)["status"])
        unknown = self.check(_contract(token_budget=1000))
        self.assertEqual((None, "UNMEASURED"), (unknown["token_estimate"]["estimated_tokens"], unknown["token_estimate"]["usage_status"]))

    def test_validation_never_mutates_the_contract(self):
        contract = _contract(allowed_tools=["Read", "Bash"])
        frozen = copy.deepcopy(contract)
        self.check(contract, prompt_bytes=10)
        self.assertEqual(frozen, contract)

    def test_contract_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "c.json"
            path.write_text(json.dumps(_contract(session_id=None)))
            result = subprocess.run([PY, str(RUNTIME / "delegation_contract.py"), "validate", "--contract", str(path), "--json"],
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(2, result.returncode)
            self.assertEqual("REJECTED", json.loads(result.stdout)["status"])
            path.write_text(json.dumps(_contract()))
            result = subprocess.run([PY, str(RUNTIME / "delegation_contract.py"), "validate", "--contract", str(path), "--prompt-bytes", "100", "--json"],
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
