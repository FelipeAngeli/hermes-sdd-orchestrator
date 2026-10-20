"""Unit and regression tests for agent-manager PR 1 (controller review findings A1-A5)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent_registry  # noqa: E402
import delegation_config  # noqa: E402
import delegation_contract  # noqa: E402
from test_agent_manager_acceptance import _contract  # noqa: E402

REGISTRY = agent_registry.build_registry(ROOT)
CONFIG, _ = delegation_config.load_config(delegation_config.DEFAULT_CONFIG_PATH)


def codes(result):
    return {error["code"] for error in result["errors"]}


class ContractUnitTests(unittest.TestCase):
    def test_a1_writing_codex_child_is_valid_with_no_tool_list(self):
        contract = _contract(child_agent_id="sdd-implement", stage="IMPLEMENT", allowed_tools=[], timeout=900)
        result = delegation_contract.validate_contract(contract, REGISTRY, CONFIG)
        self.assertEqual("VALID", result["status"], result["errors"])
        self.assertEqual((True, "workspace-write"), (result["effective_permissions"]["writes"], result["effective_permissions"]["sandbox"]))

    def test_a1_narrower_claude_tools_are_allowed(self):
        result = delegation_contract.validate_contract(_contract(allowed_tools=["Read"]), REGISTRY, CONFIG)
        self.assertEqual("VALID", result["status"], result["errors"])
        self.assertEqual(["Read"], result["effective_permissions"]["tools"])

    def test_a2_role_child_keeps_the_requested_timeout(self):
        contract = _contract(child_agent_id="sdd-pr-reviewer", stage="REVIEW", allowed_tools=[], timeout=300,
                             output_schema={"path": "schemas/REVIEW_RESULT_SCHEMA.json", "sha256": "b" * 64})
        result = delegation_contract.validate_contract(contract, REGISTRY, CONFIG)
        self.assertEqual("VALID", result["status"], result["errors"])
        self.assertEqual(300, result["effective_permissions"]["timeout_seconds"])

    def test_a3_malformed_contracts_return_structured_errors(self):
        for bad in (_contract(timeout="600"), _contract(allowed_tools=None), _contract(allowed_tools="Read"),
                    _contract(dependencies="x"), _contract(output_schema="schemas/x.json"), _contract(token_budget="9"), [], "x", None):
            result = delegation_contract.validate_contract(bad, REGISTRY, CONFIG, prompt_bytes=10)
            self.assertEqual("REJECTED", result["status"], bad)
            self.assertIn("CONTRACT_SCHEMA_INVALID", codes(result))

    def test_a4_role_stages_must_be_known_stages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "orchestration"
            for sub in ("agents", "sub-agents", "policies", "schemas"):
                (root / sub).mkdir(parents=True)
            (root / "schemas" / "AGENT_REGISTRY_SCHEMA.json").write_bytes((ROOT / "schemas" / "AGENT_REGISTRY_SCHEMA.json").read_bytes())
            (root / "sub-agents" / "x.md").write_text("---\nname: sdd-x\nrole: X\nallowed_stages: [DEPLOY]\nexecutor_policy: CONTROLLER_SELECTED\n"
                                                      "result_schema: ../schemas/EXECUTOR_RESULT_SCHEMA.json\n---\n")
            with self.assertRaises(agent_registry.RegistryError) as caught:
                agent_registry.build_registry(root)
            self.assertEqual("REGISTRY_SOURCE_INVALID", caught.exception.status)

    def test_a5_cli_usage_errors_exit_one_with_a_status(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "c.json"
            path.write_text(json.dumps(_contract()))
            result = subprocess.run([sys.executable, str(RUNTIME / "delegation_contract.py"), "validate", "--contract", str(path),
                                     "--config", str(Path(directory) / "missing.md"), "--json"], capture_output=True, text=True, timeout=60)
            self.assertEqual(1, result.returncode, result.stderr)
            self.assertEqual("CONFIG_MISSING", json.loads(result.stdout)["status"])
            path.write_text("{not json")
            result = subprocess.run([sys.executable, str(RUNTIME / "delegation_contract.py"), "validate", "--contract", str(path), "--json"],
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual((1, "CONTRACT_UNREADABLE"), (result.returncode, json.loads(result.stdout)["status"]))

    def test_r1_writing_claude_child_with_read_only_tools_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "orchestration"
            for sub in ("agents", "sub-agents", "policies", "schemas"):
                (root / sub).mkdir(parents=True)
            for name in ("agents/implement.md", "schemas/AGENT_REGISTRY_SCHEMA.json"):
                (root / name).write_bytes((ROOT / name).read_bytes())
            (root / "policies" / "EXECUTORS.md").write_text('```json\n{"executors_version": 1, "stages": {"IMPLEMENT": '
                                                            '{"executor": "claude", "tools": "Read,Edit"}}}\n```\n')
            registry = agent_registry.build_registry(root)
            implement = registry["agents"][0]
            self.assertEqual(("claude", ["Read", "Edit"], True), (implement["executor"], implement["tools"], implement["writes"]))
            base = dict(child_agent_id="sdd-implement", stage="IMPLEMENT", timeout=900)
            read_only = delegation_contract.validate_contract(_contract(allowed_tools=["Read"], **base), registry, CONFIG)
            self.assertEqual("REJECTED", read_only["status"])
            self.assertEqual([("PERMISSION_WIDENING", "allowed_tools")], [(e["code"], e["field"]) for e in read_only["errors"]])
            writer = delegation_contract.validate_contract(_contract(allowed_tools=["Read", "Edit"], **base), registry, CONFIG)
            self.assertEqual("VALID", writer["status"], writer["errors"])

    def test_r5_writing_claude_default_tools_child_is_never_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "orchestration"
            for sub in ("agents", "sub-agents", "policies", "schemas"):
                (root / sub).mkdir(parents=True)
            for name in ("agents/implement.md", "schemas/AGENT_REGISTRY_SCHEMA.json"):
                (root / name).write_bytes((ROOT / name).read_bytes())
            (root / "policies" / "EXECUTORS.md").write_text('```json\n{"executors_version": 1, "stages": {"IMPLEMENT": '
                                                            '{"executor": "claude", "tools": "default"}}}\n```\n')
            registry = agent_registry.build_registry(root)
            self.assertEqual((None, True), (registry["agents"][0]["tools"], registry["agents"][0]["writes"]))
            for tools in ([], ["Read"], ["Edit"]):
                result = delegation_contract.validate_contract(
                    _contract(child_agent_id="sdd-implement", stage="IMPLEMENT", timeout=900, allowed_tools=tools), registry, CONFIG)
                self.assertEqual("REJECTED", result["status"], tools)
                self.assertIn("PERMISSION_WIDENING", codes(result))

    def test_r2_widening_reports_each_field(self):
        config = dict(CONFIG, default_token_budget=100)
        result = delegation_contract.validate_contract(_contract(timeout=901, token_budget=101), REGISTRY, config)
        self.assertEqual({"timeout", "token_budget"}, {e["field"] for e in result["errors"] if e["code"] == "PERMISSION_WIDENING"})

    def test_r3_blank_session_negative_bytes_and_invalid_contract_budget(self):
        self.assertIn("SESSION_ID_REQUIRED", codes(delegation_contract.validate_contract(_contract(session_id="   "), REGISTRY, CONFIG)))
        with self.assertRaises(ValueError):
            delegation_contract.validate_contract(_contract(), REGISTRY, CONFIG, prompt_bytes=-1)
        result = delegation_contract.validate_contract(_contract(timeout="x", token_budget=1), REGISTRY, CONFIG, prompt_bytes=4000)
        self.assertNotIn("TOKEN_BUDGET_EXCEEDED", codes(result))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "c.json"
            path.write_text(json.dumps(_contract()))
            run = subprocess.run([sys.executable, str(RUNTIME / "delegation_contract.py"), "validate", "--contract", str(path),
                                  "--prompt-bytes", "-5", "--json"], capture_output=True, text=True, timeout=60)
            self.assertEqual((1, "PROMPT_BYTES_INVALID"), (run.returncode, json.loads(run.stdout)["status"]))

    def test_r4_config_refuses_duplicate_keys_and_non_integer_version(self):
        text = delegation_config.DEFAULT_CONFIG_PATH.read_text()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "DELEGATION.md"
            path.write_text(text.replace('"enabled":false', '"enabled":false,"enabled":true'))
            self.assertEqual((None, "CONFIG_INVALID"), delegation_config.load_config(path))
            for version in ("true", "1.0"):
                path.write_text(text.replace('"schema_version":1', f'"schema_version":{version}'))
                self.assertEqual((None, "CONFIG_INVALID"), delegation_config.load_config(path), version)

    def test_r6_third_party_playbooks_never_break_the_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "orchestration"
            for sub in ("agents", "sub-agents", "policies", "schemas"):
                (root / sub).mkdir(parents=True)
            (root / "agents" / "plan.md").write_bytes((ROOT / "agents" / "plan.md").read_bytes())
            (root / "schemas" / "AGENT_REGISTRY_SCHEMA.json").write_bytes((ROOT / "schemas" / "AGENT_REGISTRY_SCHEMA.json").read_bytes())
            skills = root.parent / "skills"
            vendor = "---\nname: typesafe-ai\nlicense: MIT\ndescription: >\n  Folded text.\n  More.\n---\n"
            for name, text in (("typesafe-ai", vendor), ("broken", "no frontmatter\n"), ("ok", "---\nname: ok\nversion: 1.0.0\n---\n")):
                (skills / name).mkdir(parents=True)
                (skills / name / "SKILL.md").write_text(text)
            playbooks = {p["source"].split("/")[2]: p for p in agent_registry.build_registry(root)["playbooks"]}
            self.assertEqual(("ok", "1.0.0", "PARSED"), (playbooks["ok"]["name"], playbooks["ok"]["version"], playbooks["ok"]["status"]))
            self.assertEqual(("typesafe-ai", None, "PARSED"), (playbooks["typesafe-ai"]["name"], playbooks["typesafe-ai"]["version"], playbooks["typesafe-ai"]["status"]))
            self.assertEqual(("broken", None, "UNPARSED"), (playbooks["broken"]["name"], playbooks["broken"]["version"], playbooks["broken"]["status"]))
            self.assertRegex(playbooks["broken"]["source_sha256"], r"^[0-9a-f]{64}$")
            (skills / "dir-skill" / "SKILL.md").mkdir(parents=True)
            with self.assertRaises(agent_registry.RegistryError) as caught:
                agent_registry.build_registry(root)
            self.assertEqual("REGISTRY_SOURCE_INVALID", caught.exception.status)

    def test_a6_relative_root_finds_the_playbooks(self):
        result = subprocess.run([sys.executable, str(RUNTIME / "agent_registry.py"), "--root", ".", "--json"],
                                capture_output=True, text=True, timeout=60, cwd=ROOT)
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertEqual(REGISTRY["registry_sha256"], json.loads(result.stdout)["registry_sha256"])


if __name__ == "__main__":
    unittest.main()
