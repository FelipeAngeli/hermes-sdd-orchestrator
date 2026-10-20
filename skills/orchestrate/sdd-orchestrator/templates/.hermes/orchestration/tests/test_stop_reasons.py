"""Stop reasons are a closed, test-enforced vocabulary with one next action each.

Every literal a runtime emits as ``stop_reason`` is registered in
``runtime/stop_reasons.py``; every registered code is emitted by some runtime;
and LOOP_POLICY.md §9 lists exactly the registered codes. A controller can
therefore never meet an unknown stop reason ("UNKNOWN_BLOCKER and wait").
"""
from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

ORCHESTRATION = Path(__file__).resolve().parents[1]
RUNTIME = ORCHESTRATION / "runtime"
sys.path.insert(0, str(RUNTIME))

import stop_reasons  # noqa: E402

CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
SINK_CALLS = {"_stop", "stop", "stopped"}
#: Exception classes whose first positional argument is the ``status`` code of an
#: exit-2 payload the controller reads exactly like a ``stop_reason``.
SINK_RAISES = {"SddError"}
#: Module-level collections whose values are emitted as stop reasons.
SINK_COLLECTIONS = {"EMITTED_STOP_REASONS", "LOOP_STOP_REASONS", "BUDGET_STOP_REASONS", "GATE_STOP_REASONS", "GATE_FAILURE_STOP_REASONS"}
#: Functions whose returned constants become a driver/loop stop reason.
SINK_RETURNS = {"gate_precondition_error", "_action_precondition_failure"}


def _constants(node: ast.AST) -> set[str]:
    return {item.value for item in ast.walk(node) if isinstance(item, ast.Constant) and isinstance(item.value, str) and CODE.match(item.value)}


def emitted_literals(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "stop_reason" and isinstance(node.value, ast.Constant):
            found |= _constants(node.value)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value in {"stop_reason", "terminal_reason"} and isinstance(value, ast.Constant):
                    found |= _constants(value)
        elif isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
            if name in SINK_CALLS:
                for argument in node.args:
                    if isinstance(argument, ast.Constant):
                        found |= _constants(argument)
            elif name in SINK_RAISES and node.args and isinstance(node.args[0], ast.Constant):
                # `raise SddError("CODE", ...)` reaches the controller as the `status` of an
                # exit-2 payload: it is a stop reason under another name.
                found |= _constants(node.args[0])
        elif isinstance(node, ast.Assign):
            targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
            if any(target in SINK_COLLECTIONS for target in targets):
                values = node.value.values if isinstance(node.value, ast.Dict) else [node.value]
                for value in values:
                    found |= _constants(value)
            if "termination" in targets and isinstance(node.value, ast.Constant):
                found |= _constants(node.value)
        elif isinstance(node, ast.FunctionDef) and node.name in SINK_RETURNS:
            for inner in ast.walk(node):
                if isinstance(inner, ast.Return) and isinstance(inner.value, ast.Constant):
                    found |= _constants(inner.value)
    return found


def runtime_sources() -> list[Path]:
    # The registry and its additions module only register codes; neither counts as an emitter.
    return sorted(path for path in RUNTIME.glob("*.py") if path.name not in {"stop_reasons.py", "stop_reason_additions.py"})


def policy_table() -> str:
    text = (ORCHESTRATION / "policies" / "LOOP_POLICY.md").read_text(encoding="utf-8")
    return "| Stop reason | Kind | Next action |" + text.split("| Stop reason | Kind | Next action |", 1)[1].split("\n## ", 1)[0].rstrip()


class StopReasonRegistryTests(unittest.TestCase):
    def test_every_emitted_stop_reason_is_registered(self) -> None:
        for path in runtime_sources():
            for code in sorted(emitted_literals(path)):
                with self.subTest(runtime=path.name, code=code):
                    self.assertIn(code, stop_reasons.STOP_REASONS)

    def test_every_registered_stop_reason_is_emitted_by_a_runtime(self) -> None:
        sources = "\n".join(path.read_text(encoding="utf-8") for path in runtime_sources())
        for code in stop_reasons.STOP_REASONS:
            with self.subTest(code=code):
                self.assertIn(f'"{code}"', sources)

    def test_loop_policy_section_9_equals_the_generated_registry_table(self) -> None:
        self.assertEqual(stop_reasons.policy_table(), policy_table())

    def test_every_stop_reason_maps_to_one_kind_and_next_step(self) -> None:
        for code, (kind, step, command) in stop_reasons.STOP_REASONS.items():
            with self.subTest(code=code):
                self.assertIn(kind, {"PAUSED", "BLOCKED", "HUMAN", "DONE"})
                self.assertTrue(step.strip())
                described = stop_reasons.describe(code)
                self.assertEqual(code, described["stop_reason"])
                if command is not None:
                    self.assertIn("sdd.py", described["next_command"])

    def test_every_stop_reason_has_a_concrete_next_command(self) -> None:
        """No stop is a dead end: every code names a command, and its only placeholders are the user's words."""
        for code, (_, _, command) in stop_reasons.STOP_REASONS.items():
            with self.subTest(code=code):
                self.assertTrue(command and command.strip())
                described = stop_reasons.describe(code)["next_command"]
                self.assertIn("sdd.py", described)
                for placeholder in re.findall(r"<[^<>]+>", described):
                    self.assertIn(placeholder, stop_reasons.USER_PLACEHOLDERS | stop_reasons.RUNTIME_PLACEHOLDERS)

    def test_every_stop_reason_has_a_human_explanation(self) -> None:
        """Every code explains, in plain Portuguese, what happened, why and what to do."""
        self.assertEqual(set(stop_reasons.STOP_REASONS), set(stop_reasons.EXPLANATIONS))
        for code in stop_reasons.STOP_REASONS:
            with self.subTest(code=code):
                explanation = stop_reasons.describe(code)["explanation"]
                self.assertEqual({"what", "why", "action"}, set(explanation))
                for field, text in explanation.items():
                    self.assertTrue(text.strip(), field)
                    self.assertTrue(text.isprintable(), field)
                    self.assertLessEqual(len(text), 240, field)
                    self.assertNotIn(code, text, "the explanation is prose, not the code again")

    def test_describe_keeps_its_existing_keys_and_only_adds_the_explanation(self) -> None:
        described = stop_reasons.describe("FOCUSED_TESTS_FAILED")
        self.assertEqual({"stop_reason", "kind", "next_step", "next_command", "explanation"}, set(described))
        self.assertEqual("BLOCKED", described["kind"])

    def test_gate_failures_reopen_in_place_instead_of_a_lateral_transition(self) -> None:
        for code in ("FOCUSED_TESTS_FAILED", "FORMAT_FAILED", "ANALYZE_FAILED", "CI_FAILED", "REVIEW_BLOCKED", "REVIEW_CHANGES_REQUIRED"):
            with self.subTest(code=code):
                command = stop_reasons.describe(code)["next_command"]
                self.assertIn(" reopen ", command)
                self.assertNotIn("transition --to", command)

    def test_budget_stop_reasons_use_the_singular_policy_names(self) -> None:
        import bounded_run_driver

        self.assertEqual("EXECUTOR_CALL_BUDGET_REACHED", bounded_run_driver.BUDGET_STOP_REASONS["executor_calls"])
        self.assertNotIn("EXECUTOR_CALLS_BUDGET_REACHED", stop_reasons.STOP_REASONS)

    def test_raise_commands_name_a_real_budget(self) -> None:
        import sdd

        for code, (_, _, command) in stop_reasons.STOP_REASONS.items():
            match = re.search(r"budget --raise (\S+)", command)
            if match:
                with self.subTest(code=code):
                    self.assertIn(match.group(1), sdd.RAISABLE_BUDGETS)

    def test_sdd_error_codes_are_registered_stop_reasons(self) -> None:
        """`raise SddError("CODE")` is an exit-2 `status` the controller reads like a stop reason."""
        codes = emitted_literals(RUNTIME / "sdd.py")
        for code in ("DEMAND_ACTIVE", "TICKET_INVALID", "STEP_MISMATCH", "WAIVE_REQUIRES_HUMAN_CHECK",
                     "AGENT_OWNED_PATH_UNSAFE"):
            with self.subTest(code=code):
                self.assertIn(code, codes, "the guard must see codes raised as SddError")
                self.assertIn(code, stop_reasons.STOP_REASONS)

    def test_a_refused_human_decision_has_an_abandon_command(self) -> None:
        """HUMAN_DECISION_REQUIRED is not a dead end when the user refuses: `abandon` returns to IDLE."""
        self.assertIn("ABANDON_REQUESTED", stop_reasons.STOP_REASONS)
        described = stop_reasons.describe("ABANDON_REQUESTED")
        self.assertEqual("HUMAN", described["kind"])
        self.assertIn(" abandon ", described["next_command"])

    def test_a_tampered_state_is_abandoned_instead_of_reprinting_the_loop(self) -> None:
        """STATE_MODIFIED_DURING_ACTION names `abandon`: `next` alone would only reprint the stop."""
        described = stop_reasons.describe("STATE_MODIFIED_DURING_ACTION")
        self.assertEqual("BLOCKED", described["kind"])
        self.assertIn(" abandon ", described["next_command"])
        self.assertNotRegex(described["next_command"], r"sdd\.py next$")

    def test_an_unreadable_controller_policy_is_not_resolved_by_confirming_it(self) -> None:
        """A policy file that cannot be hashed is restored; confirming it would pin the empty digest."""
        self.assertIn("CONTROLLER_POLICY_UNREADABLE", stop_reasons.STOP_REASONS)
        described = stop_reasons.describe("CONTROLLER_POLICY_UNREADABLE")
        self.assertEqual("BLOCKED", described["kind"])
        self.assertNotIn("confirm-policy", described["next_command"])
        self.assertIn("restore", described["next_step"].lower())
        # Git cannot restore it: the container is not a repository and a local controller is excluded from Git.
        self.assertNotIn("git checkout", described["next_command"])
        self.assertIn(" restore-policy ", described["next_command"])

    def test_a_writing_worker_sharing_the_controller_filesystem_has_a_registered_stop(self) -> None:
        """Prevention, not detection: the dispatch is refused and the exit is moving the controller out."""
        self.assertIn("CONTROLLER_WRITABLE_BY_WORKER", stop_reasons.STOP_REASONS)
        described = stop_reasons.describe("CONTROLLER_WRITABLE_BY_WORKER")
        self.assertEqual("BLOCKED", described["kind"])
        # migrate_to_vault keeps runtime/*.py and policies/ in the repository: it is not the exit.
        self.assertIn(" abandon ", described["next_command"])
        self.assertIn("--obsidian-vault <vault>", described["next_step"])
        self.assertIn("<vault>", stop_reasons.USER_PLACEHOLDERS)
        self.assertIn("<installed-skill>", stop_reasons.USER_PLACEHOLDERS)


if __name__ == "__main__":
    unittest.main()
