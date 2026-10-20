"""Reopen IMPLEMENT -> TASKS after slice-data manifest refusals."""
from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import sdd  # noqa: E402
import state_format  # noqa: E402


class ReopenTargetsTests(unittest.TestCase):
    def test_implement_reopens_to_tasks_with_a_recorded_reason(self) -> None:
        self.assertEqual(("TASKS",), state_format.REOPEN_TARGETS["IMPLEMENT"])
        state = {"schema_version": 2, "stage": {"current": "IMPLEMENT", "status": "RUNNING",
                                                 "completed": ["SPECIFY", "CLARIFY", "PLAN", "TASKS"], "skipped": []}}
        moved = state_format.apply_transition(state, "TASKS", profile="CODE", reopen_reason="slice paths unsafe")
        self.assertEqual("TASKS", moved["stage"]["current"])
        self.assertEqual(["SPECIFY", "CLARIFY", "PLAN"], moved["stage"]["completed"])
        self.assertEqual({"reason": "slice paths unsafe"}, moved["stage_provenance"]["IMPLEMENT->REOPEN"])

    def test_implement_reopen_needs_a_recorded_reason(self) -> None:
        state = {"schema_version": 2, "stage": {"current": "IMPLEMENT", "status": "RUNNING", "completed": ["SPECIFY", "PLAN"], "skipped": []}}
        with self.assertRaises(state_format.StateFormatError):
            state_format.apply_transition(state, "TASKS", profile="CODE")

    def test_implement_cannot_reopen_to_tasks_outside_its_profile(self) -> None:
        state = {"schema_version": 2, "stage": {"current": "IMPLEMENT", "status": "RUNNING",
                                                 "completed": ["SPECIFY", "CLARIFY", "PLAN"], "skipped": []}}
        with self.assertRaises(state_format.StateFormatError):
            state_format.apply_transition(state, "TASKS", profile="DECISION_DOC", reopen_reason="slice paths unsafe")


def slice_state(completed: list[str]) -> dict:
    return {"delivery": {"slices": {"planned": ["S1"], "completed": completed, "editable_paths": ["tasks/x/**"]}}}


class ReopenGuardTests(unittest.TestCase):
    def _ctx(self, stage: str = "IMPLEMENT") -> SimpleNamespace:
        return SimpleNamespace(stage=stage)

    def test_guard_allows_manifest_invalid_with_slice_findings(self) -> None:
        refusal = {"stop_reason": "MANIFEST_INVALID", "findings": [{"code": "SLICE_EDITABLE_PATH_UNSAFE"}]}
        with mock.patch.object(sdd, "build_manifest", return_value={}), \
             mock.patch.object(sdd, "check_manifest", return_value=refusal):
            self.assertIsNone(sdd.reopen_to_tasks_refusal(self._ctx(), slice_state([])))

    def test_guard_refuses_after_a_completed_slice(self) -> None:
        refusal = {"stop_reason": "MANIFEST_INVALID", "findings": [{"code": "SLICE_EDITABLE_PATH_UNSAFE"}]}
        with mock.patch.object(sdd, "build_manifest", return_value={}), \
             mock.patch.object(sdd, "check_manifest", return_value=refusal):
            error = sdd.reopen_to_tasks_refusal(self._ctx(), slice_state(["S1"]))
        self.assertIsNotNone(error)
        self.assertIn("completed", str(error))

    def test_guard_refuses_when_the_manifest_is_valid(self) -> None:
        with mock.patch.object(sdd, "build_manifest", return_value={}), \
             mock.patch.object(sdd, "check_manifest", return_value=None):
            error = sdd.reopen_to_tasks_refusal(self._ctx(), slice_state([]))
        self.assertIsNotNone(error)
        self.assertIn("valid", str(error))

    def test_guard_refuses_manifest_invalid_without_slice_findings(self) -> None:
        refusal = {"stop_reason": "MANIFEST_INVALID", "findings": [{"code": "SCHEMA_INVALID"}]}
        with mock.patch.object(sdd, "build_manifest", return_value={}), \
             mock.patch.object(sdd, "check_manifest", return_value=refusal):
            error = sdd.reopen_to_tasks_refusal(self._ctx(), slice_state([]))
        self.assertIsNotNone(error)
        self.assertIn("SLICE_", str(error))

    def test_guard_refuses_outside_implement(self) -> None:
        error = sdd.reopen_to_tasks_refusal(self._ctx("TASKS"), slice_state([]))
        self.assertIsNotNone(error)
        self.assertIn("IMPLEMENT", str(error))


class ResetForTasksReplanTests(unittest.TestCase):
    def test_reset_clears_slices_and_approved_hashes_but_keeps_plan(self) -> None:
        data = {"delivery": {
            "acceptance": {"AC-1": {"criterion": "c", "verification_method": "m", "verifier": "AGENT", "slice_id": "S1"}},
            "slices": {"planned": ["S1"], "completed": ["S1"], "editable_paths": ["tasks/x/**"]},
            "approved_slice_sha256s": ["a"], "check_commands": {"AC-1": "rg -n x docs/a.md"},
            "check_commands_refused": [{"id": "AC-1", "command": "true", "reason": "not allowlisted"}],
            "editable_path_normalizations": [{"from": "tasks/x/", "to": "tasks/x/**"}],
            "accepted": {"specify": {"artifact": "a"}, "plan": {"artifact": "p"}, "tasks": {"artifact": "t"}, "implement-s1": {"artifact": "i"}},
            "summaries": {"SPECIFY": {"summary": "s"}, "PLAN": {"summary": "p"}, "TASKS": {"summary": "t"}, "IMPLEMENT": {"summary": "i"}},
        }}
        sdd.reset_for_tasks_replan(data)
        self.assertEqual({"planned": [], "completed": [], "editable_paths": []}, data["delivery"]["slices"])
        self.assertEqual([], data["delivery"]["approved_slice_sha256s"])
        self.assertEqual({}, data["delivery"]["check_commands"])
        self.assertEqual(["plan", "specify"], sorted(data["delivery"]["accepted"]))
        self.assertNotIn("TASKS", data["delivery"]["summaries"])
        self.assertIn("AC-1", data["delivery"]["acceptance"])


class ManifestStopTests(unittest.TestCase):
    def test_implement_slice_findings_print_the_tasks_reopen(self) -> None:
        import stage_context

        with mock.patch.object(stage_context, "check", return_value={
            "errors": [{"code": "SLICE_EDITABLE_PATH_UNSAFE", "detail": "x"}],
        }):
            payload = sdd.check_manifest({"stage": "IMPLEMENT"}, None)
        self.assertEqual("MANIFEST_INVALID", payload["stop_reason"])
        self.assertIn("reopen --to TASKS", payload["next_command"])
        self.assertIn("<failure>", payload["next_command"])
        self.assertIn("<user words>", payload["next_command"])

    def test_non_implement_manifest_keeps_the_default_next_command(self) -> None:
        import stage_context

        with mock.patch.object(stage_context, "check", return_value={
            "errors": [{"code": "SLICE_EDITABLE_PATH_UNSAFE", "detail": "x"}],
        }):
            payload = sdd.check_manifest({"stage": "PLAN"}, None)
        self.assertEqual("MANIFEST_INVALID", payload["stop_reason"])
        self.assertNotIn("reopen --to TASKS", payload["next_command"])

    def test_decision_doc_manifest_stop_does_not_suggest_tasks_reopen(self) -> None:
        import stage_context

        with mock.patch.object(stage_context, "check", return_value={
            "errors": [{"code": "SLICE_EDITABLE_PATH_UNSAFE", "detail": "x"}],
        }):
            payload = sdd.check_manifest({"stage": "IMPLEMENT"}, None, "DECISION_DOC")
        self.assertEqual("MANIFEST_INVALID", payload["stop_reason"])
        self.assertNotIn("reopen --to TASKS", payload["next_command"])


class FakeReopenCtx:
    def __init__(self, state: dict) -> None:
        self.state = state
        self.stage = (state.get("stage") or {}).get("current") or "IMPLEMENT"
        self.delivery = state.get("delivery") or {}
        self.ticket = "APP-1"
        self.repo = Path(".")

    def require_pristine(self) -> None:
        return None

    def reload(self) -> None:
        return None


class CmdReopenTests(unittest.TestCase):
    def test_reopen_to_tasks_succeeds_in_the_allowed_state(self) -> None:
        ctx = FakeReopenCtx({"stage": {"current": "IMPLEMENT"}, "delivery": {"slices": {"planned": ["S1"], "completed": [], "editable_paths": ["tasks/x/**"]}}})
        refusal = {"stop_reason": "MANIFEST_INVALID", "findings": [{"code": "SLICE_EDITABLE_PATH_UNSAFE"}]}
        with mock.patch.object(sdd, "build_manifest", return_value={}), \
             mock.patch.object(sdd, "check_manifest", return_value=refusal), \
             mock.patch.object(sdd, "cmd_transition", return_value={"status": "TRANSITIONED", "from": "IMPLEMENT", "to": "TASKS"}):
            result = sdd.cmd_reopen(ctx, argparse.Namespace(to="TASKS", reason="unsafe paths", quote="approved", artifact=None, skip_reason=None))
        self.assertEqual("REOPENED", result["status"])
        self.assertEqual("TASKS", result["to"])

    def test_reopen_to_tasks_refused_without_a_quote(self) -> None:
        ctx = FakeReopenCtx({"stage": {"current": "IMPLEMENT"}, "delivery": {"slices": {"planned": ["S1"], "completed": [], "editable_paths": ["tasks/x/**"]}}})
        with self.assertRaises(sdd.SddError) as caught:
            sdd.cmd_reopen(ctx, argparse.Namespace(to="TASKS", reason="unsafe paths", quote="  ", artifact=None, skip_reason=None))
        self.assertEqual("STEP_MISMATCH", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
