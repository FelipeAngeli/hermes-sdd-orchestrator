"""Behavior of the per-stage context and slice-contract checker."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))
import stage_context as ctx  # noqa: E402
import validate_protocol  # noqa: E402

SCRIPT = RUNTIME / "stage_context.py"
SHA = "a" * 64


def context(stage: str = "IMPLEMENT") -> dict:
    value = {
        "schema_version": 1,
        "ticket": "APP-1",
        "stage": stage,
        "limits": {"max_sources": 12, "max_lines_per_source": 250},
        "project_context": {
            "status": "CURRENT",
            "checked_head": "b" * 40,
            "obsidian": "BOUND",
            "evidence": "Architecture note matches src/ layout at HEAD",
            "gaps": [],
        },
        "sources": [
            {"kind": "RULES", "path": "AGENTS.md", "lines": [1, 40], "sha256": SHA},
            {"kind": "SPEC", "path": "docs/spec.md", "lines": [10, 60], "sha256": SHA},
        ],
        "divergences": [],
        "slice": None,
        "approval": None,
    }
    if stage in {"IMPLEMENT", "TEST", "REVIEW"}:
        value["slice"] = {
            "current_slice_ids": ["S1"] if stage == "IMPLEMENT" else [],
            "completed_slice_ids": [] if stage == "IMPLEMENT" else ["S1"],
            "editable_paths": ["src/feature/*", "tests/feature/*"] if stage == "IMPLEMENT" else [],
            "acceptance": {
                "AC-1": {"criterion": "totals round half-up", "verification_method": "focused test",
                         "verifier": "AGENT", "slice_id": "S1"},
            },
            "required_verification": [
                {"id": "V1", "kind": "TEST", "command": "pytest tests/feature -q", "check_ids": ["AC-1"],
                 "introduced_by_slice": True},
                {"id": "V2", "kind": "STATIC_ANALYSIS", "command": "ruff check src/feature", "check_ids": ["AC-1"],
                 "introduced_by_slice": False},
            ],
        }
    return value


def errors_of(value: dict) -> list[str]:
    return [error["code"] for error in ctx.check(value)["errors"]]


class StageContextBudgetTests(unittest.TestCase):
    def test_valid_implement_context_is_accepted(self) -> None:
        result = ctx.check(context())
        self.assertTrue(result["valid"], result["errors"])
        self.assertRegex(result["slice_sha256"], r"^[a-f0-9]{64}$")

    def test_whole_documents_and_oversized_excerpts_are_refused(self) -> None:
        value = context()
        value["sources"][0]["lines"] = [1, 400]
        self.assertIn("CONTEXT_EXCERPT_TOO_LARGE", errors_of(value))
        value = context()
        value["sources"][0]["lines"] = [50, 10]
        self.assertIn("CONTEXT_EXCERPT_INVALID", errors_of(value))

    def test_too_many_sources_and_duplicates_are_refused(self) -> None:
        value = context()
        value["limits"]["max_sources"] = 1
        self.assertIn("CONTEXT_BUDGET_EXCEEDED", errors_of(value))
        value = context()
        value["sources"].append(copy.deepcopy(value["sources"][0]))
        self.assertIn("CONTEXT_SOURCE_DUPLICATED", errors_of(value))

    def test_plan_and_implement_require_consulted_project_context(self) -> None:
        for stage in ("PLAN", "IMPLEMENT"):
            with self.subTest(stage=stage):
                value = context(stage)
                value["project_context"]["status"] = "MISSING"
                self.assertIn("PROJECT_CONTEXT_REQUIRED", errors_of(value))
        value = context("SPECIFY")
        value["project_context"]["status"] = "MISSING"
        self.assertNotIn("PROJECT_CONTEXT_REQUIRED", errors_of(value))

    def test_partial_project_context_must_name_its_gaps(self) -> None:
        value = context("PLAN")
        value["project_context"]["status"] = "PARTIAL"
        self.assertIn("PROJECT_CONTEXT_GAPS_REQUIRED", errors_of(value))
        value["project_context"]["gaps"] = ["Integrations note not examined"]
        self.assertTrue(ctx.check(value)["valid"])

    def test_divergences_cite_both_sides_and_keep_code_authoritative(self) -> None:
        value = context("PLAN")
        value["divergences"] = [{"code_path": "src/a.py:10", "doc_path": "docs/a.md:3",
                                 "summary": "doc says 2 retries, code does 1", "authority": "CODE"}]
        self.assertTrue(ctx.check(value)["valid"])
        value["divergences"][0]["authority"] = "DOCUMENTATION"
        self.assertFalse(ctx.check(value)["valid"])


class SliceContractTests(unittest.TestCase):
    def test_implement_requires_one_current_slice_and_editable_paths(self) -> None:
        value = context()
        value["slice"]["current_slice_ids"] = ["S1", "S2"]
        self.assertIn("SLICE_CURRENT_INVALID", errors_of(value))
        value = context()
        value["slice"]["editable_paths"] = []
        self.assertIn("SLICE_EDITABLE_PATHS_REQUIRED", errors_of(value))
        value = context()
        value["slice"]["editable_paths"] = ["../outside/*"]
        self.assertIn("SLICE_EDITABLE_PATH_UNSAFE", errors_of(value))

    def test_analysis_stages_cannot_declare_editable_paths(self) -> None:
        value = context("TEST")
        value["slice"]["editable_paths"] = ["src/*"]
        self.assertIn("ANALYSIS_STAGE_EDITABLE_PATHS", errors_of(value))

    def test_every_agent_check_in_scope_is_bound_to_an_observable_verifier(self) -> None:
        value = context()
        for verifier in value["slice"]["required_verification"]:
            verifier["check_ids"] = []
        self.assertIn("ACCEPTANCE_CHECK_UNVERIFIED", errors_of(value))

    def test_a_slice_cannot_rely_only_on_verifiers_it_introduced(self) -> None:
        value = context()
        value["slice"]["required_verification"][1]["introduced_by_slice"] = True
        self.assertIn("INDEPENDENT_VERIFIER_REQUIRED", errors_of(value))

    def test_an_unbound_preexisting_verifier_does_not_make_a_check_independent(self) -> None:
        value = context()
        value["slice"]["required_verification"][1]["check_ids"] = []
        self.assertIn("INDEPENDENT_VERIFIER_REQUIRED", errors_of(value))

    def test_match_all_and_depth_crossing_editable_patterns_are_refused(self) -> None:
        for pattern in ("*/*", "?*", "***", "**/**", "[!~]*", "*"):
            with self.subTest(pattern=pattern):
                value = context()
                value["slice"]["editable_paths"] = [pattern]
                self.assertIn("SLICE_EDITABLE_PATH_UNSAFE", errors_of(value))

    def test_controller_runtime_files_are_never_context_sources(self) -> None:
        for path in (".hermes/orchestration/STATE.md", "vault/runtime/wt/ACTION_JOURNAL.json",
                     ".hermes/orchestration/action-journal-history/APP-1/a.json"):
            with self.subTest(path=path):
                value = context()
                value["sources"].append({"kind": "EVIDENCE", "path": path, "lines": [1, 5], "sha256": SHA})
                self.assertIn("CONTEXT_SOURCE_FORBIDDEN", errors_of(value))

    def test_machine_verifiers_need_a_command_and_known_checks(self) -> None:
        value = context()
        value["slice"]["required_verification"][0]["command"] = None
        self.assertIn("VERIFIER_COMMAND_REQUIRED", errors_of(value))
        value = context()
        value["slice"]["required_verification"][0]["check_ids"] = ["AC-404"]
        self.assertIn("VERIFIER_UNKNOWN_CHECK", errors_of(value))

    def test_human_checks_may_be_verified_by_a_human_entry(self) -> None:
        value = context()
        value["slice"]["acceptance"]["AC-2"] = {"criterion": "copy approved", "verification_method": "PO review",
                                                "verifier": "HUMAN", "slice_id": "S1"}
        value["slice"]["required_verification"].append(
            {"id": "V3", "kind": "HUMAN", "command": None, "check_ids": ["AC-2"], "introduced_by_slice": False})
        self.assertTrue(ctx.check(value)["valid"], ctx.check(value)["errors"])


def two_slice_context(current: str, completed: list[str]) -> dict:
    value = context()
    contract = value["slice"]
    contract["current_slice_ids"] = [current]
    contract["completed_slice_ids"] = completed
    contract["acceptance"]["AC-2"] = {"criterion": "totals are shown", "verification_method": "focused test",
                                      "verifier": "AGENT", "slice_id": "S2"}
    contract["required_verification"].append(
        {"id": "V3", "kind": "TEST", "command": "pytest tests/view -q", "check_ids": ["AC-2"],
         "introduced_by_slice": False})
    return value


def approve(value: dict, hashes: list[str]) -> dict:
    value["approval"] = {"approved_slice_sha256s": hashes, "evidence": "user approved TASKS"}
    return value


class ApprovalReuseTests(unittest.TestCase):
    def approved_at_tasks(self) -> list[str]:
        """What the controller stores when TASKS is approved: one hash per planned slice."""
        return [ctx.slice_sha256(two_slice_context("S1", [])), ctx.slice_sha256(two_slice_context("S2", []))]

    def test_tasks_approval_is_reused_by_every_later_implement_dispatch(self) -> None:
        approved = self.approved_at_tasks()
        for current, completed in (("S1", []), ("S2", ["S1"])):
            with self.subTest(slice=current):
                result = ctx.check(approve(two_slice_context(current, completed), approved))
                self.assertEqual("APPROVAL_REUSED", result["approval"], result["errors"])
                self.assertTrue(result["valid"], result["errors"])

    def test_finishing_a_slice_does_not_change_the_hash_of_the_next(self) -> None:
        self.assertEqual(ctx.slice_sha256(two_slice_context("S2", [])),
                         ctx.slice_sha256(two_slice_context("S2", ["S1"])))

    def test_context_refresh_does_not_invalidate_the_slice_approval(self) -> None:
        value = approve(two_slice_context("S1", []), self.approved_at_tasks())
        value["sources"].append({"kind": "EVIDENCE", "path": "logs/run.txt", "lines": [1, 5], "sha256": SHA})
        value["project_context"]["status"] = "REFRESHED"
        self.assertEqual("APPROVAL_REUSED", ctx.check(value)["approval"])

    def test_read_only_stages_need_no_slice_approval(self) -> None:
        for stage in ("TEST", "REVIEW"):
            with self.subTest(stage=stage):
                value = approve(context(stage), self.approved_at_tasks())
                result = ctx.check(value)
                self.assertEqual("APPROVAL_NOT_APPLICABLE", result["approval"])
                self.assertTrue(result["valid"], result["errors"])

    def test_any_scope_change_requires_new_approval(self) -> None:
        mutations = (
            lambda s: s["editable_paths"].append("config/*"),
            lambda s: s["acceptance"]["AC-1"].update({"criterion": "different"}),
            lambda s: s["required_verification"][0].update({"command": "pytest -q"}),
        )
        for mutate in mutations:
            value = two_slice_context("S1", [])
            mutate(value["slice"])
            approve(value, self.approved_at_tasks())
            with self.subTest(mutation=mutate):
                result = ctx.check(value)
                self.assertEqual("APPROVAL_REQUIRED", result["approval"])
                self.assertIn("SCOPE_CHANGE_REQUIRED", [error["code"] for error in result["errors"]])

    def test_order_of_paths_and_checks_does_not_change_the_hash(self) -> None:
        value = context()
        reordered = copy.deepcopy(value)
        reordered["slice"]["editable_paths"].reverse()
        reordered["slice"]["required_verification"].reverse()
        self.assertEqual(ctx.slice_sha256(value), ctx.slice_sha256(reordered))


class VerifierContextIntegrationTests(unittest.TestCase):
    def test_verifier_context_feeds_the_protocol_validator(self) -> None:
        verifier = ctx.verifier_context(context())
        self.assertEqual({"S1"}, set(verifier["current_slice_ids"]))
        self.assertEqual({"AC-1": ["pytest tests/feature -q", "ruff check src/feature"]}, verifier["check_verifiers"])
        self.assertEqual(["pytest tests/feature -q", "ruff check src/feature"], verifier["required_commands"])
        result = {
            "executor_result": {
                "schema_version": 3,
                "stage": {"value": "IMPLEMENT", "status": "SUCCESS"},
                "executor": {"name": "CODEX", "invocation_type": "EXTERNAL_CLI"},
                "consulted_paths": [], "modified_paths": [{"path": "src/feature/total.py"}], "created_paths": [],
                "validated_symbols": [],
                "commands": [{"command": "ruff check src/feature", "purpose": "analysis", "timeout_seconds": 60,
                              "exit_code": 0, "result": "PASS"}],
                "blockers": [],
                "context_assessment": {"facts": [], "assumptions": [], "unresolved_questions": []},
                "acceptance_checks": [{"id": "AC-1", "criterion": "totals round half-up",
                                       "verification_method": "focused test", "verifier": "AGENT",
                                       "slice_id": "S1", "status": "PASS",
                                       "evidence": "`pytest tests/feature -q` exited 0"}],
                "stage_payload": {"summary": "", "tasks": [], "impact_files": [], "decisions": []},
                "tdd_slices": [{"id": "S1", "objective": "round", "test_file": "tests/feature/test_total.py",
                                "red_command": "pytest tests/feature -q", "red_exit_code": 1,
                                "expected_failure": "assert 1.0 == 1.01", "red_failure_kind": "EXPECTED_FUNCTIONAL",
                                "minimal_implementation": "round half-up", "green_command": "pytest tests/feature -q",
                                "green_exit_code": 0, "green_result": "1 passed"}],
                "next_step": {"stage": "TEST", "action": "run focused tests"},
            }
        }
        kwargs = {key: set(value) if isinstance(value, list) else value for key, value in verifier.items()}
        kwargs["required_commands"] = verifier["required_commands"]
        self.assertEqual([], validate_protocol.validate_payload("IMPLEMENT", result, **kwargs))
        result["executor_result"]["commands"] = []
        errors = validate_protocol.validate_payload("IMPLEMENT", result, **kwargs)
        self.assertTrue(any("ruff check src/feature" in error["reason"] for error in errors), errors)


class StageContextCliTests(unittest.TestCase):
    def run_cli(self, *args: str, value: dict) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "context.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            return subprocess.run([sys.executable, str(SCRIPT), *args, "--context", str(path), "--json"],
                                  text=True, capture_output=True, timeout=30)

    def test_check_exit_codes(self) -> None:
        self.assertEqual(0, self.run_cli("check", value=context()).returncode)
        broken = context()
        broken["slice"]["editable_paths"] = []
        result = self.run_cli("check", value=broken)
        self.assertEqual(2, result.returncode)
        self.assertFalse(json.loads(result.stdout)["valid"])

    def test_verifier_context_command(self) -> None:
        result = self.run_cli("verifier-context", value=context())
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(["src/feature/*", "tests/feature/*"], json.loads(result.stdout)["editable_paths"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
