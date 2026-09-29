"""Behavior of the repository-local Hermes shell hooks."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ORCHESTRATION = Path(__file__).resolve().parents[1]
RUNTIME = ORCHESTRATION / "runtime"
HOOKS = ORCHESTRATION / "hooks"
sys.path.insert(0, str(RUNTIME))

import hook_runtime  # noqa: E402
import action_journal  # noqa: E402

SHA = "a" * 64


def context() -> dict:
    return {
        "schema_version": 1,
        "ticket": "APP-1",
        "stage": "IMPLEMENT",
        "limits": {"max_sources": 12, "max_lines_per_source": 250},
        "project_context": {
            "status": "CURRENT", "checked_head": "b" * 40, "obsidian": "NOT_CONFIGURED",
            "evidence": "Repository rules inspected", "gaps": [],
        },
        "sources": [{"kind": "RULES", "path": "AGENTS.md", "lines": [1, 20], "sha256": SHA}],
        "divergences": [],
        "slice": {
            "current_slice_ids": ["S1"], "completed_slice_ids": [],
            "editable_paths": ["src/**", "tests/*"],
            "acceptance": {
                "AC-1": {"criterion": "works", "verification_method": "focused test", "verifier": "AGENT", "slice_id": "S1"},
            },
            "required_verification": [
                {"id": "V1", "kind": "TEST", "command": "python3 -m unittest tests.test_feature",
                 "check_ids": ["AC-1"], "introduced_by_slice": False},
            ],
        },
        "approval": None,
    }


class ScopeHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".hermes" / "orchestration").mkdir(parents=True)

    def test_allows_direct_write_inside_editable_paths_with_canonical_args(self) -> None:
        payload = {"tool_name": "write_file", "tool_input": {"path": "src/app.py", "content": "x"}, "cwd": str(self.root)}
        result = hook_runtime.scope_tool_call(payload, self.root, context())
        self.assertEqual("modify", result["action"])
        self.assertEqual(str((self.root / "src/app.py").resolve()), result["args"]["path"])
        self.assertEqual("x", result["args"]["content"])

    def test_blocks_direct_write_outside_editable_paths(self) -> None:
        payload = {"tool_name": "patch", "tool_input": {"path": "docs/architecture.md"}, "cwd": str(self.root)}
        result = hook_runtime.scope_tool_call(payload, self.root, context())
        self.assertEqual("block", result["action"])
        self.assertIn("editable_paths", result["message"])

    def test_v4a_patch_validates_every_target_including_lenient_headers(self) -> None:
        payload = {"tool_name": "patch", "tool_input": {
            "mode": "patch", "patch": "*** Begin Patch\n*** Update File: src/a.py\n@@\n-x\n+y\n***Delete File: docs/no.md\n*** End Patch"
        }, "cwd": str(self.root)}
        result = hook_runtime.scope_tool_call(payload, self.root, context())
        self.assertEqual("block", result["action"])
        self.assertIn("docs/no.md", result["message"])

    def test_vault_write_is_refused_outside_implement(self) -> None:
        vault = self.root.parent / f"{self.root.name}-vault"
        self.addCleanup(shutil.rmtree, vault, True)
        container = vault / "Project"
        container.mkdir(parents=True)
        (self.root / ".hermes" / "obsidian.json").write_text(json.dumps({
            "schema_version": 1, "vault_path": str(vault), "project_container": "Project",
        }), encoding="utf-8")
        value = context()
        value["stage"] = "TEST"
        value["slice"]["current_slice_ids"] = []
        value["slice"]["completed_slice_ids"] = ["S1"]
        value["slice"]["editable_paths"] = []
        payload = {"tool_name": "write_file", "tool_input": {"path": str(container / "note.md"), "content": "x"}, "cwd": str(self.root)}
        result = hook_runtime.scope_tool_call(payload, self.root, value)
        self.assertEqual("block", result["action"])
        self.assertIn("IMPLEMENT", result["message"])

    def test_missing_tool_name_fails_closed(self) -> None:
        payload = {"tool_input": {"path": "src/app.py"}, "cwd": str(self.root)}
        result = hook_runtime.scope_tool_call(payload, self.root, context())
        self.assertEqual("block", result["action"])
        self.assertIn("tool_name", result["message"])

    def test_missing_context_fails_closed(self) -> None:
        payload = {"tool_name": "write_file", "tool_input": {"path": "src/app.py"}, "cwd": str(self.root)}
        result = hook_runtime.run_scope_hook(payload, environ={})
        self.assertEqual("block", result["action"])
        self.assertIn("unavailable or inconsistent", result["message"])


class VerificationAndLifecycleHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.orchestration = self.root / ".hermes" / "orchestration"
        self.orchestration.mkdir(parents=True)

    def install_live_binding(self) -> tuple[dict, dict]:
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "t@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "T"], check=True)
        (self.root / "tracked.txt").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "base"], check=True)
        head = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"], check=True, text=True, capture_output=True
        ).stdout.strip()
        ctx = context()
        ctx["project_context"]["checked_head"] = head
        (self.orchestration / "STAGE_CONTEXT.json").write_text(json.dumps(ctx), encoding="utf-8")
        state = """# State\n```yaml\nticket:\n  id: APP-1\nstage:\n  current: IMPLEMENT\nloop:\n  mode: MANUAL\n  progress:\n    current_slice: S1\n  budgets:\n    executor_calls: {max: 8, used: 1}\n    stage_transitions: {max: 5, used: 1}\n```\n"""
        (self.orchestration / "STATE.md").write_text(state, encoding="utf-8")
        journal = action_journal.empty_journal({
            "path": str(self.root.resolve()), "branch": "main", "head": head,
            "git_common_dir": str(self.root / ".git"),
        })
        journal["action"].update({
            "id": "ACT-1", "ticket": "APP-1", "stage": "IMPLEMENT", "attempt": 1, "status": "DISPATCHED",
        })
        action_journal.atomic_write(self.orchestration / "ACTION_JOURNAL.json", journal)
        binding = {
            "schema_version": 1,
            "context_sha256": hashlib.sha256(json.dumps(ctx, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "workspace": str(self.root.resolve()), "head": head, "ticket": "APP-1", "stage": "IMPLEMENT",
            "current_slice_ids": ["S1"], "action_id": "ACT-1", "attempt": 1,
        }
        (self.orchestration / "HOOK_BINDING.json").write_text(json.dumps(binding), encoding="utf-8")
        return ctx, binding

    def test_pre_verify_requires_passing_evidence_for_every_check(self) -> None:
        payload = {"cwd": str(self.root), "extra": {"coding": True, "attempt": 0, "changed_paths": ["src/app.py"]}}
        incomplete = {"checks": {"AC-1": {"status": "FAIL", "evidence": "test failed"}}}
        result = hook_runtime.verify_completion(payload, context(), incomplete)
        self.assertEqual("continue", result["action"])
        unsupported = {"checks": {"AC-1": {"status": "PASS", "evidence": "claimed pass", "verifiers": []}}}
        self.assertEqual("continue", hook_runtime.verify_completion(payload, context(), unsupported)["action"])
        complete = {"checks": {"AC-1": {
            "status": "PASS", "evidence": "V1 exited 0",
            "verifiers": [{"command": "python3 -m unittest tests.test_feature", "exit_code": 0}],
        }}}
        self.assertEqual({}, hook_runtime.verify_completion(payload, context(), complete))

        human_context = context()
        human_context["slice"]["acceptance"]["AC-1"]["verifier"] = "HUMAN"
        human_context["slice"]["required_verification"] = [
            {"id": "V-H", "kind": "HUMAN", "command": None, "check_ids": ["AC-1"], "introduced_by_slice": False},
            {"id": "V-M", "kind": "TEST", "command": "python3 -m unittest tests.test_feature", "check_ids": ["AC-1"], "introduced_by_slice": False},
        ]
        human = {"checks": {"AC-1": {
            "status": "PASS", "evidence": "Approval and test recorded by controller",
            "verifiers": [{"command": "python3 -m unittest tests.test_feature", "exit_code": 0}],
        }}}
        self.assertEqual({}, hook_runtime.verify_completion(payload, human_context, human))

    def test_state_summary_is_structural_and_bounded(self) -> None:
        state = """# State\n```yaml\nticket:\n  id: APP-1\nstage:\n  current: IMPLEMENT\nloop:\n  mode: MANUAL\n  progress:\n    current_slice: S1\n  budgets:\n    executor_calls: {max: 8, used: 3}\n    stage_transitions: {max: 5, used: 2}\nnotes: \"current: WRONG\"\n```\nSECRET BODY\n"""
        summary = hook_runtime.summarize_state(state)
        self.assertIn("APP-1", summary)
        self.assertIn("IMPLEMENT", summary)
        self.assertIn("slice S1", summary)
        self.assertIn("executor remaining 5", summary)
        self.assertIn("transitions remaining 3", summary)
        self.assertNotIn("WRONG", summary)
        self.assertNotIn("SECRET BODY", summary)
        self.assertLessEqual(len(summary), hook_runtime.MAX_STATE_SUMMARY_CHARS)
        long_state = state.replace("APP-1", "A" * 1000)
        self.assertLessEqual(len(hook_runtime.summarize_state(long_state)), hook_runtime.MAX_STATE_SUMMARY_CHARS)

    def test_state_hook_never_echoes_malformed_or_wrong_shaped_content(self) -> None:
        payload = {"cwd": str(self.root), "extra": {}}
        secret = "TOP-SECRET-CREDENTIAL"
        wrong_shape = f"""# State\n```yaml\nticket:\n  id: {{secret: {secret}}}\nstage:\n  current: IMPLEMENT\nloop:\n  mode: MANUAL\n  progress:\n    current_slice: S1\n  budgets:\n    executor_calls: {{max: 8, used: 3}}\n    stage_transitions: {{max: 5, used: 2}}\n```\n"""
        (self.orchestration / "STATE.md").write_text(wrong_shape, encoding="utf-8")
        result = hook_runtime.run_context_hook(payload, environ={})
        self.assertEqual("SDD state unavailable.", result["context"])
        self.assertNotIn(secret, result["context"])
        (self.orchestration / "STATE.md").write_text(f"# State\n```yaml\nticket:\n bad-indent: {secret}\n```\n", encoding="utf-8")
        result = hook_runtime.run_context_hook(payload, environ={})
        self.assertEqual("SDD state unavailable.", result["context"])
        self.assertNotIn(secret, result["context"])
        (self.orchestration / "STATE.md").write_bytes(b"\xff")
        completed = subprocess.run(
            [sys.executable, str(HOOKS / "inject-state-summary.py")], input=json.dumps(payload),
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual({"context": "SDD state unavailable."}, json.loads(completed.stdout))

    def test_scope_and_verify_never_echo_malformed_state_details(self) -> None:
        self.install_live_binding()
        secret = "TOP-SECRET-STATE-VALUE"
        (self.orchestration / "STATE.md").write_text(
            f"# State\n```yaml\nticket:\n bad-indent: {secret}\n```\n", encoding="utf-8"
        )
        scope_payload = {"cwd": str(self.root), "tool_name": "write_file", "tool_input": {"path": "src/a.py", "content": "x"}}
        scope_result = hook_runtime.run_scope_hook(scope_payload, environ={})
        self.assertEqual("block", scope_result["action"])
        self.assertNotIn(secret, scope_result["message"])
        verify_payload = {"cwd": str(self.root), "extra": {"coding": True, "attempt": 0, "changed_paths": ["src/a.py"]}}
        verify_result = hook_runtime.run_verify_hook(verify_payload, environ={})
        self.assertEqual("continue", verify_result["action"])
        self.assertNotIn(secret, verify_result["message"])

    def test_state_budget_booleans_are_rejected(self) -> None:
        state = """# State\n```yaml\nticket:\n  id: APP-1\nstage:\n  current: IMPLEMENT\nloop:\n  mode: MANUAL\n  progress:\n    current_slice: S1\n  budgets:\n    executor_calls: {max: true, used: false}\n    stage_transitions: {max: 5, used: 2}\n```\n"""
        (self.orchestration / "STATE.md").write_text(state, encoding="utf-8")
        result = hook_runtime.run_context_hook({"cwd": str(self.root), "extra": {}}, environ={})
        self.assertEqual("SDD state unavailable.", result["context"])

    def test_subagent_event_is_immutable_and_omits_summary(self) -> None:
        journal = action_journal.empty_journal({
            "path": str(self.root.resolve()), "branch": "main", "head": "a" * 40,
            "git_common_dir": str(self.root / ".git"),
        })
        journal["action"].update({"id": "ACT-1", "ticket": "APP-1", "stage": "IMPLEMENT", "attempt": 2, "status": "DISPATCHED"})
        action_journal.atomic_write(self.orchestration / "ACTION_JOURNAL.json", journal)
        payload = {"cwd": str(self.root), "session_id": "parent-session", "extra": {
            "child_session_id": "child-1", "child_role": "TEST_RUNNER", "child_status": "completed",
            "duration_ms": 42, "child_summary": "secret output", "tool_call_history": ["sensitive"],
        }}
        path = hook_runtime.record_subagent_event(payload, self.root)
        event = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual("child-1", event["child_session_id"])
        self.assertEqual("parent-session", event["parent_session_id"])
        self.assertEqual("ACT-1", event["action_id"])
        self.assertEqual(2, event["attempt"])
        self.assertRegex(event["result_sha256"], r"^[a-f0-9]{64}$")
        self.assertNotIn("child_summary", event)
        self.assertNotIn("tool_call_history", event)

    def test_subagent_event_refuses_symlinked_history_parent(self) -> None:
        journal = action_journal.empty_journal({
            "path": str(self.root.resolve()), "branch": "main", "head": "a" * 40,
            "git_common_dir": str(self.root / ".git"),
        })
        journal["action"].update({"id": "ACT-1", "ticket": "APP-1", "stage": "IMPLEMENT", "attempt": 1, "status": "DISPATCHED"})
        action_journal.atomic_write(self.orchestration / "ACTION_JOURNAL.json", journal)
        outside = self.root / "outside"
        outside.mkdir()
        (self.orchestration / "action-journal-history").symlink_to(outside, target_is_directory=True)
        payload = {"cwd": str(self.root), "extra": {"child_session_id": "child-1", "child_summary": "x"}}
        with self.assertRaises(action_journal.JournalError):
            hook_runtime.record_subagent_event(payload, self.root)

    def test_subagent_event_refuses_terminal_journal_action(self) -> None:
        journal = action_journal.empty_journal({
            "path": str(self.root.resolve()), "branch": "main", "head": "a" * 40,
            "git_common_dir": str(self.root / ".git"),
        })
        journal["action"].update({"id": "ACT-1", "ticket": "APP-1", "attempt": 1, "status": "RELEASED"})
        action_journal.atomic_write(self.orchestration / "ACTION_JOURNAL.json", journal)
        payload = {"cwd": str(self.root), "extra": {"child_session_id": "child-1", "child_summary": "x"}}
        with self.assertRaises(hook_runtime.HookInputError):
            hook_runtime.record_subagent_event(payload, self.root)

    def test_live_binding_rejects_context_or_state_drift(self) -> None:
        ctx, binding = self.install_live_binding()
        hook_runtime.validate_live_binding(self.root, ctx, binding)
        stale = json.loads(json.dumps(ctx))
        stale["slice"]["editable_paths"].append("docs/**")
        with self.assertRaises(hook_runtime.HookInputError):
            hook_runtime.validate_live_binding(self.root, stale, binding)
        state_path = self.orchestration / "STATE.md"
        state_path.write_text(state_path.read_text(encoding="utf-8").replace("current_slice: S1", "current_slice: S2"), encoding="utf-8")
        with self.assertRaises(hook_runtime.HookInputError):
            hook_runtime.validate_live_binding(self.root, ctx, binding)

    def test_live_binding_and_events_use_vault_backed_runtime(self) -> None:
        ctx, binding = self.install_live_binding()
        vault = self.root.parent / f"{self.root.name}-runtime-vault"
        self.addCleanup(shutil.rmtree, vault, True)
        (vault / "Project").mkdir(parents=True)
        (self.root / ".hermes" / "obsidian.json").write_text(json.dumps({
            "schema_version": 1, "vault_path": str(vault), "project_container": "Project",
        }), encoding="utf-8")
        locations = hook_runtime.runtime_locations(self.root)
        locations.state.parent.mkdir(parents=True)
        shutil.move(str(self.orchestration / "STATE.md"), locations.state)
        shutil.move(str(self.orchestration / "ACTION_JOURNAL.json"), locations.journal)
        hook_runtime.validate_live_binding(self.root, ctx, binding)
        payload = {"cwd": str(self.root), "session_id": "parent", "extra": {
            "child_session_id": "child-vault", "child_status": "completed", "child_summary": "ok",
        }}
        event_path = hook_runtime.record_subagent_event(payload, self.root)
        self.assertTrue(event_path.is_relative_to(locations.history_workspace))
        self.assertTrue(event_path.is_file())

    def test_verify_hook_requires_evidence_bound_to_live_context_and_action(self) -> None:
        _, binding = self.install_live_binding()
        evidence = {"binding": binding, "checks": {"AC-1": {
            "status": "PASS", "evidence": "V1 exited 0",
            "verifiers": [{"command": "python3 -m unittest tests.test_feature", "exit_code": 0}],
        }}}
        (self.orchestration / "VERIFICATION_EVIDENCE.json").write_text(json.dumps(evidence), encoding="utf-8")
        payload = {"cwd": str(self.root), "extra": {"coding": True, "attempt": 0, "changed_paths": ["src/app.py"]}}
        self.assertEqual({}, hook_runtime.run_verify_hook(payload, environ={}))
        evidence["binding"]["attempt"] = 99
        (self.orchestration / "VERIFICATION_EVIDENCE.json").write_text(json.dumps(evidence), encoding="utf-8")
        self.assertEqual("continue", hook_runtime.run_verify_hook(payload, environ={})["action"])

    def test_scope_script_uses_json_stdin_stdout_protocol(self) -> None:
        self.install_live_binding()
        payload = {"hook_event_name": "pre_tool_call", "tool_name": "write_file",
                   "tool_input": {"path": "docs/no.md"}, "cwd": str(self.root), "extra": {}}
        completed = subprocess.run(
            [sys.executable, str(HOOKS / "enforce-slice-scope.py")], input=json.dumps(payload),
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("block", json.loads(completed.stdout)["action"])

    def test_scope_script_returns_block_json_for_invalid_utf8_context(self) -> None:
        self.install_live_binding()
        (self.orchestration / "STAGE_CONTEXT.json").write_bytes(b"\xff")
        payload = {"hook_event_name": "pre_tool_call", "tool_name": "write_file",
                   "tool_input": {"path": "src/a.py", "content": "x"}, "cwd": str(self.root), "extra": {}}
        completed = subprocess.run(
            [sys.executable, str(HOOKS / "enforce-slice-scope.py")], input=json.dumps(payload),
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("block", json.loads(completed.stdout)["action"])

    def test_each_script_returns_json_for_malformed_input(self) -> None:
        for script in sorted(HOOKS.glob("*.py")):
            with self.subTest(script=script.name):
                completed = subprocess.run(
                    [sys.executable, str(script)], input="not-json", text=True, capture_output=True, check=False,
                )
                self.assertEqual(0, completed.returncode, completed.stderr)
                self.assertIsInstance(json.loads(completed.stdout), dict)

    def test_example_uses_supported_events_and_absolute_project_placeholder(self) -> None:
        text = (HOOKS / "hooks.example.yaml").read_text(encoding="utf-8")
        events = {
            (line.strip()[2:] if line.strip().startswith("# ") else line.strip())[:-1]
            for line in text.splitlines()
            if line.startswith("  ") and not line.startswith("    ") and line.strip().endswith(":")
        }
        self.assertEqual({"pre_tool_call", "pre_verify", "subagent_stop", "pre_llm_call"}, events)
        self.assertNotIn('command: "python3 .hermes/', text)
        self.assertIn("<ABSOLUTE_PROJECT_ROOT>", text)
        for raw in (line for line in text.splitlines() if "command:" in line):
            line = raw.strip().lstrip("#").strip().lstrip("-").strip()
            self.assertRegex(line, r'''command: 'python3 "<ABSOLUTE_PROJECT_ROOT>/.+"'$''')


if __name__ == "__main__":
    unittest.main()
