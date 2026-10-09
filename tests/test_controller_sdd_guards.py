"""sdd.py subcommands that guard human decisions and STATE, beyond the end-to-end happy path.

The flow tests below craft one reachable STATE (through ``sdd.py start`` plus a
direct STATE edit standing in for the stages already run) and assert that the
printed next command makes progress: no lateral loop, no stop without a command.
"""
from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_controller_e2e as e2e  # noqa: E402

sys.path.insert(0, str(e2e.SKILL_ROOT / "templates" / ".hermes" / "orchestration" / "runtime"))
import state_format as sf  # noqa: E402

run = e2e.run
FILL = {"<user words>": "sim, pode seguir", "<why>": "decided by the requester", "<failure>": "gate failed",
        "<review findings>": "review findings"}


def fill(command: str) -> list[str]:
    for placeholder, value in FILL.items():
        command = command.replace(placeholder, value)
    return shlex.split(command)


class SddGuardTests(e2e.ControllerEndToEndTests):
    """Reuses the installed fixture (real installer, fake vault, fake executors)."""

    test_a_demand_reaches_done_only_through_printed_commands = None  # type: ignore[assignment]
    test_snapshot_is_generated_and_accepted_by_the_planner = None  # type: ignore[assignment]
    test_decision_doc_profile_skips_tasks_and_test = None  # type: ignore[assignment]
    test_decision_doc_gate_failure_recovers_through_an_in_stage_fix_slice = None  # type: ignore[assignment]

    def call(self, *arguments: str) -> tuple[int, dict]:
        completed = run([*self.sdd, *arguments], cwd=self.repo, env=self.env)
        return completed.returncode, json.loads(completed.stdout)

    def run_printed(self, command: str) -> tuple[int, dict]:
        completed = run(fill(command), cwd=self.repo, env=self.env)
        return completed.returncode, json.loads(completed.stdout)

    def run_batch(self, result: dict) -> None:
        for printed in result["commands"]:
            completed = run(shlex.split(printed), cwd=self.repo, env=self.env)
            self.assertEqual(0, completed.returncode, completed.stdout)

    # -- crafted STATE ------------------------------------------------------------------
    def state(self) -> dict:
        return sf.parse(Path(self.paths["state"]).read_text(encoding="utf-8"))

    def edit_state(self, mutate) -> None:
        path = Path(self.paths["state"])
        text = path.read_text(encoding="utf-8")
        data = sf.parse(text)
        mutate(data)
        path.write_text(sf.dump(text, data, log="test fixture"), encoding="utf-8")

    def craft(self, *, profile: str = "CODE", stage: str = "TEST", slice_id: str = "S1", review: str | None = None) -> None:
        """A demand whose stages before ``stage`` (and ``stage`` itself) were accepted."""
        code, started = self.call("start", "--ticket", "c-1", "--title", "t", "--objective", "o", "--deliverable-kind", profile)
        self.assertEqual(0, code, started)
        head = run(["git", "rev-parse", "HEAD"], cwd=self.repo, env=self.env).stdout.strip()
        (self.repo / "src").mkdir(exist_ok=True)
        (self.repo / "src" / "feature.py").write_text("def greet(name):\n    return f'hello {name}'\n", encoding="utf-8")
        artifact = Path(self.temp.name) / "artifact.json"
        artifact.write_text("{}", encoding="utf-8")
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        accepted = {"action_id": "c-1-x-01", "artifact": str(artifact), "artifact_sha256": digest}

        def mutate(data: dict) -> None:
            path = sf.profile_path(profile)
            data["stage"] = {"current": stage, "status": "RUNNING", "completed": list(path[:path.index(stage)]),
                             "skipped": [{"stage": "CLARIFY", "reason": "none"}]}
            delivery = data["delivery"]
            delivery["acceptance"] = {
                "AC-1": {"criterion": "greet works", "verification_method": "focused unit test", "verifier": "AGENT", "slice_id": slice_id},
                "AC-2": {"criterion": "PO approves", "verification_method": "human decision", "verifier": "HUMAN", "slice_id": slice_id},
            }
            delivery["waivers"] = {"AC-2": {"by": "requester", "reason": "r", "quote": "ok", "recorded_at": "2026-01-01T00:00:00Z"}}
            delivery["slices"] = {"planned": [slice_id], "completed": [slice_id], "editable_paths": ["src/feature.py"]}
            delivery["accepted"] = {name: dict(accepted) for name in ("specify", "plan", "tasks", f"implement-{slice_id.lower()}", "test")}
            delivery["project_context"] = {"status": "CURRENT", "checked_head": head, "evidence": "e", "gaps": []}
            data["ownership"]["agent_owned"] = ["src/feature.py"]
            if review:
                delivery["accepted"]["review"] = dict(accepted)
                data["gates"]["review"] = {"status": review, "action_id": "c-1-review-01"}
            for name in ("focused_tests", "format", "analyze"):
                data["gates"][name] = {"status": "PASS", "exit_code": 0}
            set_used = data["loop"]["budgets"]
            set_used["stage_transitions"]["used"] = path.index(stage)
            set_used["executor_calls"]["used"] = 5
            if review:
                set_used["review_cycles"]["used"] = 1

        self.edit_state(mutate)

    def set_gate_row(self, label: str, command: str, timeout: int) -> None:
        """Edit GATES.md as the project owner would, then record their confirmation.

        `sdd.py start` pins the SHA-256 of GATES.md/EXECUTORS.md, so an edit after it
        stops every gate with CONTROLLER_POLICY_CHANGED_DURING_DEMAND until the user
        confirms the file. These tests change the row deliberately, standing in for the
        owner, so they re-pin it the same way the controller would ask the user to.
        """
        policy = self.container / ".hermes" / "orchestration" / "policies" / "GATES.md"
        lines = []
        for line in policy.read_text(encoding="utf-8").splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if line.startswith("|") and cells and cells[0] == label:
                line = f"| {label} | `{command}` | host | {timeout} s |"
            lines.append(line)
        policy.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.confirm_policy("gates")

    def confirm_policy(self, name: str) -> None:
        """Record the owner's confirmation of a controller policy file, if a demand is open."""
        if self.state().get("stage", {}).get("current") in {None, "IDLE"}:
            return
        completed = run([*self.sdd, "confirm-policy", "--name", name, "--by", "requester",
                         "--quote", "sim, eu mudei essa policy"], cwd=self.repo, env=self.env)
        self.assertEqual(0, completed.returncode, completed.stdout)

    # -- original guards ----------------------------------------------------------------
    def test_status_is_compact_and_always_names_the_next_command(self) -> None:
        completed = run([*self.sdd, "status"], cwd=self.repo, env=self.env)
        status = json.loads(completed.stdout)
        self.assertLess(len(completed.stdout), 2048)
        for key in ("stage", "status", "mode", "ticket", "recovery", "budgets_left", "next_action", "next_command", "next_step", "paths"):
            self.assertIn(key, status)

    def test_every_error_carries_next_step_and_next_command(self) -> None:
        code, result = self.call("transition", "--to", "PLAN", "--artifact", "/nonexistent")
        self.assertNotEqual(0, code)
        self.assertIn("next_step", result)
        self.assertIn("next_command", result)

    def test_start_refuses_a_second_demand(self) -> None:
        self.call("start", "--ticket", "a-1", "--title", "t", "--objective", "o")
        code, result = self.call("start", "--ticket", "a-2", "--title", "t", "--objective", "o")
        self.assertNotEqual(0, code)
        self.assertIn("sdd.py", result["next_command"])

    def test_waive_needs_a_known_human_check_and_an_allowed_approver(self) -> None:
        self.call("start", "--ticket", "w-1", "--title", "t", "--objective", "o")
        code, result = self.call("waive", "--check", "AC-9", "--by", "requester", "--quote", "ok", "--reason", "r")
        self.assertNotEqual(0, code)
        self.assertTrue(result["next_step"])
        code, _ = self.call("waive", "--check", "AC-9", "--by", "someone-else", "--quote", "ok", "--reason", "r")
        self.assertNotEqual(0, code)

    def test_transition_cannot_skip_stages(self) -> None:
        self.call("start", "--ticket", "s-1", "--title", "t", "--objective", "o")
        code, result = self.call("transition", "--to", "IMPLEMENT", "--artifact", str(self.repo / "AGENTS.md"))
        self.assertNotEqual(0, code)
        self.assertIn("next_command", result)

    def test_manifest_carries_hashes_and_the_prompt_ceiling(self) -> None:
        self.call("start", "--ticket", "m-1", "--title", "t", "--objective", "o")
        output = Path(self.temp.name) / "m.json"
        code, result = self.call("manifest", "--stage", "SPECIFY", "--output", str(output))
        self.assertEqual(0, code, result)
        manifest = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(49152, manifest["limits"]["max_prompt_bytes"])
        self.assertTrue(all(len(source["sha256"]) == 64 for source in manifest["sources"]))
        checked = run(shlex.split(result["next_command"]), cwd=self.repo, env=self.env)
        self.assertEqual(0, checked.returncode, checked.stdout)

    # -- F7: controller card keeps the profile/consent and sub-agent rules ---------------
    def test_controller_card_forbids_profile_edits_consent_and_stage_sub_agents(self) -> None:
        card = " ".join((e2e.SKILL_ROOT / "templates" / ".hermes.md").read_text(encoding="utf-8").split())
        self.assertIn("Never edit Hermes profile configuration or grant hook consent", card)
        self.assertIn("Stage agents never dispatch sub-agents", card)
        for command in ("sdd.py reopen", "sdd.py close", "sdd.py resume", "sdd.py rebaseline"):
            self.assertIn(command, card)

    # -- F1: no lateral IMPLEMENT -> IMPLEMENT ------------------------------------------
    def test_decision_doc_gate_failure_reopens_inside_implement(self) -> None:
        self.craft(profile="DECISION_DOC", stage="IMPLEMENT", slice_id="DOC")
        self.edit_state(lambda data: data["gates"].update(analyze={"status": "FAIL", "exit_code": 1}))
        used_before = self.state()["loop"]["budgets"]["stage_transitions"]["used"]
        stopped = self.call("next")[1]
        self.assertEqual("ANALYZE_FAILED", stopped["stop_reason"])
        self.assertNotIn("transition --to IMPLEMENT", stopped["next_command"])
        code, result = self.call("transition", "--to", "IMPLEMENT", "--reason", "r", "--quote", "q")
        self.assertNotEqual(0, code, "a lateral IMPLEMENT -> IMPLEMENT transition is refused")
        self.assertEqual("STEP_MISMATCH", result["status"])
        self.assertIn("reopen", result["next_command"])
        code, reopened = self.run_printed(stopped["next_command"])
        self.assertEqual(0, code, reopened)
        state = self.state()
        self.assertEqual("IMPLEMENT", state["stage"]["current"])
        self.assertEqual(used_before, state["loop"]["budgets"]["stage_transitions"]["used"])
        self.assertEqual(["DOC", "FIX1"], state["delivery"]["slices"]["planned"])
        self.assertEqual("PENDING", state["gates"]["analyze"]["status"])
        progress = self.call("next")[1]
        self.assertEqual("PREPARE", progress.get("step"), progress)
        self.assertIn("implement-fix1", progress["action_id"])

    # -- F2: review reopen fits the stage-transition budget ------------------------------
    def test_start_sizes_stage_transitions_for_review_reopens(self) -> None:
        code, started = self.call("start", "--ticket", "b-1", "--title", "t", "--objective", "o")
        self.assertEqual(0, code)
        # path (7) + review_cycles (2) * IMPLEMENT..REVIEW forward transitions (2)
        self.assertEqual(11, started["limits"]["stage_transitions"])
        self.assertIn("stage_transitions", started["limits_explained"])

    # -- F3a: GATE_TIMEOUT rerun --------------------------------------------------------
    def test_gate_timeout_reruns_after_timeout_change_or_explicit_confirmation(self) -> None:
        self.craft(stage="TEST")
        python = shlex.quote(sys.executable)
        command = f"{python} -m py_compile {{files}}"
        self.set_gate_row("Format", command, 120)
        self.edit_state(lambda data: data["gates"].update(format={"status": "TIMEOUT", "command": command, "exit_code": 124, "timeout": 120}))
        stopped = self.call("next")[1]
        self.assertEqual("GATE_TIMEOUT", stopped["stop_reason"])
        self.assertIn("gate --name format --rerun", stopped["next_command"])
        code, rerun = self.run_printed(stopped["next_command"])
        self.assertEqual(0, code, rerun)
        self.assertEqual("PASS", self.state()["gates"]["format"]["status"])
        # A changed timeout in GATES.md re-runs the gate without a confirmation.
        self.edit_state(lambda data: data["gates"].update(format={"status": "TIMEOUT", "command": command, "exit_code": 124, "timeout": 120}))
        self.set_gate_row("Format", command, 240)
        progress = self.call("next")[1]
        self.assertEqual("GATES", progress.get("step"), progress)
        self.assertTrue(any("gate --name format" in item for item in progress["commands"]))

    # -- F3b: executor missing from PATH ------------------------------------------------
    def test_missing_executor_stops_and_reprepares_after_the_policy_change(self) -> None:
        (self.fake / "claude").unlink()
        self.env["PATH"] = f"{self.fake}:/usr/bin:/bin"
        self.call("start", "--ticket", "x-1", "--title", "t", "--objective", "o")
        prepare = self.call("next")[1]
        self.assertEqual("PREPARE", prepare["step"])
        self.run_batch(prepare)
        stopped = self.call("next")[1]
        self.assertEqual("EXECUTOR_UNAVAILABLE", stopped.get("stop_reason"), stopped)
        self.assertTrue(stopped["end_turn"])
        self.assertIn("EXECUTORS.md", stopped["next_step"])
        executors = self.container / ".hermes" / "orchestration" / "policies" / "EXECUTORS.md"
        executors.write_text(re.sub(r'("SPECIFY":\s*\{"executor": )"claude"', r'\1"codex"', executors.read_text(encoding="utf-8")), encoding="utf-8")
        step = self.call("next")[1]
        self.assertEqual("REPREPARE", step.get("step"), step)
        self.run_batch(step)
        self.assertEqual(0, self.state()["loop"]["budgets"]["executor_calls"]["used"], "a never-dispatched action is refunded")
        prepare = self.call("next")[1]
        self.assertEqual("PREPARE", prepare["step"], prepare)
        self.assertTrue(prepare["action_id"].endswith("-02"))
        self.run_batch(prepare)
        self.assertEqual("DISPATCH", self.call("next")[1]["step"])

    # -- F3c: CI enabled after REVIEW APPROVED ------------------------------------------
    def test_ci_enabled_after_approval_runs_the_ci_gate(self) -> None:
        self.craft(stage="REVIEW", review="APPROVED")
        self.edit_state(lambda data: data["gates"].update(ci={"status": "DISABLED_BY_PROJECT_POLICY"}))
        policy = self.container / ".hermes" / "orchestration" / "policies" / "GATES.md"
        policy.write_text(policy.read_text(encoding="utf-8").replace("enabled: false", "enabled: true"), encoding="utf-8")
        self.set_gate_row("CI", f"{shlex.quote(sys.executable)} -c pass", 60)
        progress = self.call("next")[1]
        self.assertEqual("GATES", progress.get("step"), progress)
        self.assertTrue(any("gate --name ci" in item for item in progress["commands"]))
        self.run_batch(progress)
        self.assertEqual("TRANSITION", self.call("next")[1].get("step"))

    def test_ci_policy_changes_never_loop_on_done_gates(self) -> None:
        policy = self.container / ".hermes" / "orchestration" / "policies" / "GATES.md"
        # Enabled at approval (ci PENDING), then disabled: DONE records DISABLED_BY_PROJECT_POLICY.
        self.craft(stage="REVIEW", review="APPROVED")
        progress = self.call("next")[1]
        self.assertEqual("TRANSITION", progress.get("step"), progress)
        self.run_batch(progress)
        self.assertEqual("DISABLED_BY_PROJECT_POLICY", self.state()["gates"]["ci"]["status"])
        # Enabled with the CI-run budget spent: one budget stop, never a failing printed command.
        self.edit_state(lambda data: (data["stage"].update(current="REVIEW", status="RUNNING"),
                                      data["gates"].update(ci={"status": "DISABLED_BY_PROJECT_POLICY"}),
                                      data["loop"]["budgets"]["ci_runs"].update(used=1)))
        policy.write_text(policy.read_text(encoding="utf-8").replace("enabled: false", "enabled: true"), encoding="utf-8")
        self.set_gate_row("CI", f"{shlex.quote(sys.executable)} -c pass", 60)
        stopped = self.call("next")[1]
        self.assertEqual("CI_RUN_BUDGET_REACHED", stopped.get("stop_reason"), stopped)
        self.assertIn("budget --raise ci_runs", stopped["next_command"])

    # -- F3d: rebaseline ----------------------------------------------------------------
    def test_baseline_drift_and_protected_file_changes_are_resolved_by_rebaseline(self) -> None:
        self.call("start", "--ticket", "d-1", "--title", "t", "--objective", "o")
        (self.repo / "README.md").write_text("new\n", encoding="utf-8")
        run(["git", "add", "README.md"], cwd=self.repo, env=self.env)
        run(["git", "commit", "-qm", "external"], cwd=self.repo, env=self.env)
        stopped = self.call("next")[1]
        self.assertEqual("BASELINE_DRIFT_EXTERNAL", stopped["stop_reason"])
        self.assertIn("rebaseline", stopped["next_command"] or "")
        code, result = self.run_printed(stopped["next_command"])
        self.assertEqual(0, code, result)
        self.assertEqual("PREPARE", self.call("next")[1].get("step"))
        (self.repo / "notes.txt").write_text("changed by the user\n", encoding="utf-8")
        stopped = self.call("next")[1]
        self.assertEqual("PREEXISTING_FILE_MODIFIED", stopped["stop_reason"])
        code, result = self.run_printed(stopped["next_command"])
        self.assertEqual(0, code, result)
        self.assertEqual("PREPARE", self.call("next")[1].get("step"))
        self.assertEqual(2, len(self.state()["delivery"]["rebaselines"]))

    def test_ownership_violation_is_resolved_by_rebaseline(self) -> None:
        self.craft(stage="REVIEW", review="CHANGES_REQUIRED")
        self.edit_state(lambda data: data["gates"]["review"].update(ownership_violations=["docs/x.md"]))
        stopped = self.call("next")[1]
        self.assertEqual("OWNERSHIP_VIOLATION", stopped["stop_reason"])
        code, result = self.run_printed(stopped["next_command"])
        self.assertEqual(0, code, result)
        state = self.state()
        self.assertEqual("PENDING", state["gates"]["review"]["status"])
        self.assertIn("docs/x.md", state["ownership"]["human_accepted"])
        self.assertEqual("PREPARE", self.call("next")[1].get("step"))

    # -- F3e: DONE -> close -> IDLE -----------------------------------------------------
    def test_done_closes_to_idle_and_a_new_demand_starts(self) -> None:
        self.craft(stage="REVIEW", review="APPROVED")
        self.edit_state(lambda data: (data["stage"].update(current="DONE", status="DONE"), data["gates"].update(ci={"status": "DISABLED_BY_PROJECT_POLICY"})))
        done = self.call("next")[1]
        self.assertEqual("DONE", done["stop_reason"])
        self.assertIn("close", done["next_command"])
        code, closed = self.run_printed(done["next_command"])
        self.assertEqual(0, code, closed)
        self.assertEqual("IDLE", self.call("status")[1]["stage"])
        self.assertEqual("IDLE_NO_DEMAND", self.call("next")[1]["stop_reason"])
        code, started = self.call("start", "--ticket", "c-2", "--title", "t", "--objective", "o")
        self.assertEqual(0, code, started)
        self.assertEqual("c-1", self.state()["closed_demands"][-1]["ticket"])

    # -- F4: waive AGENT checks only through an answered HUMAN_DECISION stop -------------
    def test_waive_refuses_agent_checks_without_an_answered_decision(self) -> None:
        self.craft(stage="TEST")
        self.edit_state(lambda data: data["delivery"].update(waivers={}))
        code, result = self.call("waive", "--check", "AC-1", "--by", "requester", "--quote", "typed by the controller", "--reason", "r")
        self.assertNotEqual(0, code)
        self.assertEqual("WAIVE_REQUIRES_HUMAN_CHECK", result["status"])
        self.assertIn("request-decision --check AC-1", result["next_command"])
        code, result = self.call("waive", "--check", "AC-2", "--by", "requester", "--quote", "aprovado", "--reason", "r")
        self.assertEqual(0, code, result)
        code, requested = self.run_printed(f"{shlex.join(self.sdd)} request-decision --check AC-1 --reason 'cannot run'")
        self.assertEqual(0, code, requested)
        stopped = self.call("next")[1]
        self.assertEqual("HUMAN_DECISION_REQUIRED", stopped["stop_reason"])
        self.assertIn("answer --check AC-1", stopped["next_command"])
        code, answered = self.run_printed(stopped["next_command"])
        self.assertEqual(0, code, answered)
        code, refused = self.call("waive", "--check", "AC-1", "--by", "requester", "--quote", "other words", "--reason", "r")
        self.assertNotEqual(0, code, "the quote must be the recorded answer")
        code, waived = self.run_printed(answered["next_command"])
        self.assertEqual(0, code, waived)
        self.assertEqual("sim, pode seguir", self.state()["delivery"]["waivers"]["AC-1"]["quote"])

    # -- F5: PAUSED ---------------------------------------------------------------------
    def test_paused_mode_starts_nothing_until_resume(self) -> None:
        self.call("start", "--ticket", "p-1", "--title", "t", "--objective", "o")
        code, paused = self.call("pause", "--quote", "pausa")
        self.assertEqual(0, code, paused)
        stopped = self.call("next")[1]
        self.assertTrue(stopped["end_turn"])
        self.assertEqual("LOOP_PAUSED", stopped["stop_reason"])
        self.assertEqual([], stopped["commands"])
        self.assertIn("resume", stopped["next_command"])
        code, resumed = self.run_printed(stopped["next_command"])
        self.assertEqual(0, code, resumed)
        self.assertEqual("PREPARE", self.call("next")[1]["step"])


if __name__ == "__main__":
    unittest.main()
