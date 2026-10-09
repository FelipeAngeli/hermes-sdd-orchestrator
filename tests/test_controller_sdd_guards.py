"""sdd.py subcommands that guard human decisions and STATE, beyond the end-to-end happy path.

The flow tests below craft one reachable STATE (through ``sdd.py start`` plus a
direct STATE edit standing in for the stages already run) and assert that the
printed next command makes progress: no lateral loop, no stop without a command.
"""
from __future__ import annotations

import hashlib
import json
import re
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_controller_e2e as e2e  # noqa: E402

sys.path.insert(0, str(e2e.SKILL_ROOT / "templates" / ".hermes" / "orchestration" / "runtime"))
import state_format as sf  # noqa: E402

run = e2e.run
FILL = {"<user words>": "sim, pode seguir", "<why>": "decided by the requester", "<failure>": "gate failed",
        "<review findings>": "review findings", "<installed-skill>": str(e2e.SKILL_ROOT), "<ticket-id>": "c-2", "<title>": "again",
        "<objective>": "re-run the demand", "<reason>": "controller exit"}


TEMPLATE_GATES = e2e.SKILL_ROOT / "templates" / ".hermes" / "orchestration" / "policies" / "GATES.md"


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

    def test_next_prints_a_shadow_governance_command_instead_of_a_jev_dead_end(self) -> None:
        self.craft(stage="PLAN")
        setup = self.container / ".hermes" / "orchestration" / "PROJECT_SETUP.md"
        text = setup.read_text(encoding="utf-8")
        setup.write_text(
            text.replace(
                "typesafe_ai: UNRESOLVED",
                'typesafe_ai: {"install":true,"automatic_semantic_governance":true}',
            ),
            encoding="utf-8",
        )
        self.edit_state(lambda data: data["delivery"]["accepted"].pop("plan", None))

        code, result = self.call("next")

        self.assertEqual(0, code, result)
        self.assertEqual("GOVERN", result["step"])
        self.assertFalse(result["end_turn"])
        self.assertEqual(1, len(result["commands"]))
        self.assertIn(" govern", result["commands"][0])

    def test_implement_requires_a_fresh_stage_bound_shadow_decision(self) -> None:
        self.craft(stage="IMPLEMENT")
        setup = self.container / ".hermes" / "orchestration" / "PROJECT_SETUP.md"
        setup.write_text(
            setup.read_text(encoding="utf-8").replace(
                "typesafe_ai: UNRESOLVED",
                'typesafe_ai: {"install":true,"automatic_semantic_governance":true}',
            ),
            encoding="utf-8",
        )

        def stale_plan_receipt(data: dict) -> None:
            data["delivery"]["accepted"].pop("implement-s1", None)
            data["delivery"]["semantic_governance"] = {"fingerprint": "a" * 64, "review_resolution": None, "mode": "SHADOW"}
            data["delivery"]["semantic_decisions"] = [{"binding": {"stage": "PLAN"}}]

        self.edit_state(stale_plan_receipt)

        code, result = self.call("next")

        self.assertEqual(0, code, result)
        self.assertEqual("GOVERN", result["step"])

    def test_govern_records_a_shadow_receipt_and_unblocks_dispatch(self) -> None:
        self.craft(stage="PLAN")
        setup = self.container / ".hermes" / "orchestration" / "PROJECT_SETUP.md"
        setup.write_text(
            setup.read_text(encoding="utf-8").replace(
                "typesafe_ai: UNRESOLVED",
                'typesafe_ai: {"install":true,"automatic_semantic_governance":true}',
            ),
            encoding="utf-8",
        )
        self.edit_state(lambda data: data["delivery"]["accepted"].pop("plan", None))
        connector = self.container / ".hermes" / "orchestration" / "runtime" / "typesafe_connector.py"
        connector.write_text(
            """#!/usr/bin/env python3
import json, sys
if 'preflight' in sys.argv:
    print(json.dumps({'status': 'READY'}))
else:
    payload = json.load(sys.stdin)
    options = list(payload['questions']['selection']['criteria'])
    choice = options[1]
    provider = sys.argv[sys.argv.index('--provider') + 1]
    print(json.dumps({'status': 'OK', 'provider': provider, 'result': {
        'model': 'jev-1.13.0',
        'answers': {'selection': {'type': 'choice', 'choice': choice,
                    'probabilities': {item: (0.91 if item == choice else 0.09) for item in options},
                    'confidence': 0.91}},
        'usage': {'input_tokens': 12, 'output_tokens': 3}}}))
""",
            encoding="utf-8",
        )

        code, governed = self.call("govern")
        self.assertEqual(0, code, governed)
        self.assertEqual("GOVERNED", governed["status"])
        self.assertEqual("SHADOW", governed["receipt"]["configured_mode"])
        self.assertFalse(governed["receipt"]["outcome"]["applied"])
        self.assertEqual("DECIDED", governed["receipt"]["recommendation"]["disposition"], governed)

        code, following = self.call("next")
        self.assertEqual(0, code, following)
        self.assertEqual("PREPARE", following.get("step"), following)
        governance = self.state()["delivery"]["semantic_governance"]
        self.assertIn("request", governance)
        self.assertEqual("SHADOW", governance["request"]["mode"])

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
        # Enabling CI edits GATES.md during the demand, so the host-run gate commands need the
        # user's confirmation once (CONTROLLER_POLICY_CHANGED_DURING_DEMAND); record it as the stop instructs.
        code, confirmed = self.call("gate", "--name", "ci", "--confirm-policy", "gates", "--quote", "sim, habilitei o CI")
        self.assertEqual(0, code, confirmed)
        self.assertEqual("CONTROLLER_POLICY_CONFIRMED", confirmed["status"])
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
                                      data["loop"]["budgets"]["ci_runs"].update(used=data["loop"]["budgets"]["ci_runs"]["max"])))
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

    # -- R2-F01: a refused human decision has an exit --------------------------------------
    def test_a_refused_human_check_is_abandoned_back_to_idle(self) -> None:
        """The user says no to the pending HUMAN check: `abandon` returns STATE to IDLE."""
        self.craft(stage="REVIEW")
        self.set_gate_row("Focused tests", f"{shlex.quote(sys.executable)} -c pass", 60)
        self.call("gate", "--name", "focused_tests", "--confirm-policy", "gates", "--quote", "sim, configurei o gate")
        self.edit_state(lambda data: data["delivery"].update(waivers={}))
        stopped = self.call("next")[1]
        self.assertEqual("HUMAN_DECISION_REQUIRED", stopped.get("stop_reason"), stopped)
        self.assertIn("waive --check AC-2", stopped["next_command"])
        # Refusal: the printed alternative is the abandon command, not another waive.
        self.assertIn("abandon", stopped["refusal_command"])
        code, refused = self.call("abandon", "--reason", "o usuario recusou a aprovacao")
        self.assertNotEqual(0, code, "abandon needs the user's literal words")
        self.assertEqual("ABANDON_REQUESTED", refused["status"])
        code, abandoned = self.run_printed(stopped["refusal_command"])
        self.assertEqual(0, code, abandoned)
        self.assertEqual("ABANDONED", abandoned["status"])
        self.assertEqual("IDLE", self.call("status")[1]["stage"])
        self.assertEqual("IDLE_NO_DEMAND", self.call("next")[1]["stop_reason"])
        closed = self.state()["closed_demands"][-1]
        self.assertEqual("c-1", closed["ticket"])
        self.assertEqual("ABANDONED", closed["outcome"])
        self.assertEqual("REVIEW", closed["stage_reached"])
        # The user's literal words are recorded verbatim, with the stop they refused.
        self.assertEqual(FILL["<user words>"], closed["abandon"]["quote"])
        self.assertIn("AC-2", closed["abandon"]["pending_human_checks"])
        # A new demand starts right away: nothing of the abandoned one blocks it.
        code, started = self.call("start", "--ticket", "c-9", "--title", "t", "--objective", "o")
        self.assertEqual(0, code, started)

    def test_abandon_refuses_a_done_demand_and_an_idle_controller(self) -> None:
        code, result = self.call("abandon", "--reason", "r", "--quote", "q")
        self.assertNotEqual(0, code)
        self.assertEqual("IDLE_NO_DEMAND", result["status"])
        self.craft(stage="REVIEW", review="APPROVED")
        self.edit_state(lambda data: data["stage"].update(current="DONE", status="DONE"))
        code, result = self.call("abandon", "--reason", "r", "--quote", "q")
        self.assertNotEqual(0, code, "a DONE demand is closed, never abandoned")
        self.assertEqual("STEP_MISMATCH", result["status"])
        self.assertIn("close", result["next_command"])

    # -- R2-F02: ci_runs is sized for the authorized review cycles -------------------------
    def test_start_sizes_ci_runs_for_every_authorized_review_cycle(self) -> None:
        code, started = self.call("start", "--ticket", "b-1", "--title", "t", "--objective", "o")
        self.assertEqual(0, code, started)
        limits = started["limits"]
        self.assertEqual(limits["review_cycles"], limits["ci_runs"],
                         "a pre-authorized REVIEW reopen must still be able to run CI")

    # -- R2-F03: ownership.human_accepted never crosses a demand ---------------------------
    def test_closing_a_demand_clears_the_accepted_ownership_paths(self) -> None:
        self.craft(stage="REVIEW", review="APPROVED")
        self.edit_state(lambda data: (data["ownership"].update(human_accepted=["docs/x.md"]),
                                      data["stage"].update(current="DONE", status="DONE"),
                                      data["gates"].update(ci={"status": "DISABLED_BY_PROJECT_POLICY"})))
        code, closed = self.call("close")
        self.assertEqual(0, code, closed)
        self.assertEqual([], self.state()["ownership"]["human_accepted"])
        code, started = self.call("start", "--ticket", "c-3", "--title", "t", "--objective", "o")
        self.assertEqual(0, code, started)
        self.assertEqual([], self.state()["ownership"]["human_accepted"])

    # -- R2-F04: AGENT_OWNED_PATH_UNSAFE names a command that progresses -------------------
    def test_unsafe_agent_owned_path_is_dropped_by_a_named_command(self) -> None:
        self.craft(stage="TEST")
        self.edit_state(lambda data: data["ownership"].update(agent_owned=["src/feature.py", "src/-weird.py"]))
        self.set_gate_row("Focused tests", "/usr/bin/true {files}", 30)
        self.call("gate", "--name", "focused_tests", "--confirm-policy", "gates", "--quote", "sim, configurei o gate")
        code, refused = self.call("gate", "--name", "focused_tests")
        self.assertNotEqual(0, code)
        self.assertEqual("AGENT_OWNED_PATH_UNSAFE", refused["status"])
        self.assertIn("disown", refused["next_command"])
        code, disowned = self.run_printed(refused["next_command"])
        self.assertEqual(0, code, disowned)
        self.assertEqual(["src/feature.py"], self.state()["ownership"]["agent_owned"])
        self.assertIn("src/-weird.py", self.state()["ownership"]["disowned_unsafe"])

    # -- R3-F01: STATE_MODIFIED_DURING_ACTION is not a dead end ----------------------------
    def tamper_with_state_during_an_action(self) -> dict:
        """Drive one demand to a dispatched action, then rewrite STATE as a writing worker would."""
        self.call("start", "--ticket", "t-1", "--title", "t", "--objective", "o")
        for _ in range(2):  # PREPARE, then DISPATCH
            self.run_batch(self.call("next")[1])
        self.edit_state(lambda data: data.setdefault("delivery", {}).update(worker_blockers=[]))
        return self.call("next")[1]

    def test_state_modified_during_an_action_ends_the_turn_with_an_abandon_exit(self) -> None:
        """`next` never prints VALIDATE/ACCEPT over a tampered STATE: it stops, and `abandon` resolves it."""
        stopped = self.tamper_with_state_during_an_action()
        self.assertTrue(stopped["end_turn"], stopped)
        self.assertEqual("STATE_MODIFIED_DURING_ACTION", stopped["stop_reason"])
        self.assertEqual([], stopped["commands"], "a stop never prints a command batch that cannot succeed")
        self.assertEqual("BLOCKED", stopped["kind"])
        self.assertIn(" abandon ", stopped["next_command"])
        self.assertNotEqual(stopped["expected_sha256"], stopped["actual_sha256"])
        # The printed command is the exit: STATE returns to IDLE and the action is archived as evidence.
        code, abandoned = self.run_printed(stopped["next_command"])
        self.assertEqual(0, code, abandoned)
        self.assertEqual("ABANDONED", abandoned["status"])
        self.assertEqual(stopped["action_id"], abandoned["discarded_action"]["action_id"])
        self.assertTrue(Path(abandoned["discarded_action"]["archived_path"]).is_file())
        self.assertEqual("IDLE", self.call("status")[1]["stage"])
        self.assertEqual("DISPATCH_ALLOWED", self.call("status")[1]["recovery"]["decision"])
        self.assertEqual("IDLE_NO_DEMAND", self.call("next")[1]["stop_reason"])
        closed = self.state()["closed_demands"][-1]
        self.assertEqual("ABANDONED", closed["outcome"])
        self.assertEqual("STATE_MODIFIED_DURING_ACTION", closed["abandon"]["stop_reason"])
        self.assertEqual(FILL["<user words>"], closed["abandon"]["quote"])
        # A new demand starts right away; nothing of the discarded one blocks it.
        code, started = self.call("start", "--ticket", "t-2", "--title", "t", "--objective", "o")
        self.assertEqual(0, code, started)

    def test_accept_over_a_tampered_state_names_the_same_abandon_exit(self) -> None:
        """Running `accept` directly refuses with the stop code and the same resolution command."""
        self.tamper_with_state_during_an_action()
        code, refused = self.call("accept")
        self.assertNotEqual(0, code)
        self.assertEqual("STATE_MODIFIED_DURING_ACTION", refused["status"])
        self.assertIn(" abandon ", refused["next_command"])

    # -- R3-F02 / R4: an unreadable controller policy is restored, never confirmed in a loop --
    def test_an_unreadable_controller_policy_prints_a_restore_command_instead_of_looping(self) -> None:
        self.craft(stage="TEST")
        self.set_gate_row("Focused tests", f"{shlex.quote(sys.executable)} -c pass", 60)
        self.edit_state(lambda data: data["gates"].update(focused_tests={"status": "PENDING"}))
        policy = self.container / ".hermes" / "orchestration" / "policies" / "GATES.md"
        policy.unlink()
        code, refused = self.call("gate", "--name", "focused_tests")
        self.assertNotEqual(0, code)
        self.assertEqual("CONTROLLER_POLICY_UNREADABLE", refused["status"])
        self.assertEqual(["gates"], refused["unreadable"])
        self.assertNotIn("confirm-policy", refused["next_command"], "confirming an unreadable file pins nothing")
        self.assertNotIn("git checkout", " ".join(refused["restore_commands"]), "the container is not a Git repository")
        self.assertEqual(refused["restore_commands"][0], refused["next_command"])
        # Confirming it is refused with the same stop, so the controller cannot loop on it.
        code, confirmed = self.call("confirm-policy", "--name", "gates", "--by", "requester", "--quote", "nao foi erro, pode aceitar")
        self.assertNotEqual(0, code, "an unreadable policy is never pinned")
        self.assertEqual("CONTROLLER_POLICY_UNREADABLE", confirmed["status"])
        pinned = self.state()["delivery"]["controller_policies"]["gates"]
        self.assertEqual(64, len(pinned["sha256"]), "the previous pin survives; the empty digest is never recorded")
        self.assertNotEqual("nao foi erro, pode aceitar", pinned.get("quote"))
        # `next` surfaces the same stop rather than a gate batch.
        stopped = self.call("next")[1]
        self.assertTrue(stopped["end_turn"], stopped)
        self.assertEqual("CONTROLLER_POLICY_UNREADABLE", stopped["stop_reason"])
        self.assertEqual([], stopped["commands"])
        # The printed exit, executed as the controller would: every command succeeds, the demand stays open.
        for printed in stopped["restore_commands"]:
            code, result = self.run_printed(printed)
            self.assertEqual(0, code, (printed, result))
        self.assertEqual(TEMPLATE_GATES.read_bytes(), policy.read_bytes(), "recreated from the installed skill's template")
        self.assertEqual("TEST", self.state()["stage"]["current"], "the demand was not abandoned")
        # The template rows are unconfigured: the owner configures them again, then the gate runs.
        self.set_gate_row("Focused tests", f"{shlex.quote(sys.executable)} -c pass", 60)
        progress = self.call("next")[1]
        self.assertEqual("GATES", progress.get("step"), progress)
        self.run_batch(progress)
        self.assertEqual("PASS", self.state()["gates"]["focused_tests"]["status"])

    def test_an_unreadable_policy_with_a_changed_skill_template_falls_back_to_a_working_reinstall(self) -> None:
        """restore-policy never adopts a template the install did not record; its fallback sequence works when executed."""
        self.craft(stage="TEST")
        policy = self.container / ".hermes" / "orchestration" / "policies" / "GATES.md"
        policy.unlink()
        stopped = self.call("next")[1]
        self.assertEqual("CONTROLLER_POLICY_UNREADABLE", stopped["stop_reason"], stopped)
        other = Path(self.temp.name) / "other-skill"
        (other / "templates" / ".hermes" / "orchestration" / "policies").mkdir(parents=True)
        (other / "templates" / ".hermes" / "orchestration" / "policies" / "GATES.md").write_text("# not this install's template\n", encoding="utf-8")
        code, refused = self.run_printed(stopped["restore_commands"][0].replace("<installed-skill>", str(other)))
        self.assertNotEqual(0, code)
        self.assertEqual("CONTROLLER_POLICY_UNREADABLE", refused["status"])
        self.assertFalse(policy.exists(), "a foreign template is never written")
        self.assertEqual(refused["reinstall_commands"][0], refused["next_command"])
        for printed in refused["reinstall_commands"]:
            code, result = self.run_printed(printed)
            self.assertEqual(0, code, (printed, result))
        self.assertTrue(policy.is_file())
        self.assertEqual("SPECIFY", self.state()["stage"]["current"], "the demand was re-run with the restored policy")
        self.assertEqual("PREPARE", self.call("next")[1].get("step"))

    # -- R3-F03: `next` never prints a gate batch a policy check will refuse ---------------
    def test_a_changed_policy_stops_next_instead_of_printing_a_failing_gate(self) -> None:
        self.craft(stage="TEST")
        self.set_gate_row("Focused tests", f"{shlex.quote(sys.executable)} -c pass", 60)
        self.edit_state(lambda data: data["gates"].update(focused_tests={"status": "PENDING"}))
        policy = self.container / ".hermes" / "orchestration" / "policies" / "GATES.md"
        policy.write_text(policy.read_text(encoding="utf-8") + "\n<!-- edited mid-demand -->\n", encoding="utf-8")
        stopped = self.call("next")[1]
        self.assertTrue(stopped["end_turn"], stopped)
        self.assertEqual("CONTROLLER_POLICY_CHANGED_DURING_DEMAND", stopped["stop_reason"])
        self.assertEqual([], stopped["commands"], "the gate would only refuse with this same code at exit 2")
        self.assertEqual(["gates"], stopped["changed"])
        self.assertIn("confirm-policy --name gates", stopped["next_command"])
        code, confirmed = self.run_printed(stopped["next_command"].replace("<user words confirming the policy change>", FILL["<user words>"]))
        self.assertEqual(0, code, confirmed)
        progress = self.call("next")[1]
        self.assertEqual("GATES", progress.get("step"), progress)
        self.run_batch(progress)
        self.assertEqual("PASS", self.state()["gates"]["focused_tests"]["status"])


    # -- R3-F04: `start` never adopts a changed controller policy as a silent new baseline -
    def test_start_refuses_a_controller_policy_changed_since_the_last_demand(self) -> None:
        """A policy edited between demands needs the user's confirmation, not a silent re-pin."""
        self.craft(stage="REVIEW", review="APPROVED")
        self.edit_state(lambda data: (data["stage"].update(current="DONE", status="DONE"),
                                      data["gates"].update(ci={"status": "DISABLED_BY_PROJECT_POLICY"})))
        code, closed = self.call("close")
        self.assertEqual(0, code, closed)
        policy = self.container / ".hermes" / "orchestration" / "policies" / "GATES.md"
        policy.write_text(policy.read_text(encoding="utf-8") + "\n<!-- edited while IDLE -->\n", encoding="utf-8")
        code, refused = self.call("start", "--ticket", "c-4", "--title", "t", "--objective", "o")
        self.assertNotEqual(0, code, refused)
        self.assertEqual("CONTROLLER_POLICY_CHANGED_DURING_DEMAND", refused["status"])
        self.assertEqual(["gates"], refused["changed"])
        self.assertIn("confirm-policy --name gates", refused["next_command"])
        self.assertEqual("IDLE", self.call("status")[1]["stage"], "the demand never started")
        # The user's confirmation is the exit; the next start is accepted and pins the confirmed file.
        code, confirmed = self.run_printed(refused["next_command"].replace("<user words confirming the policy change>", FILL["<user words>"]))
        self.assertEqual(0, code, confirmed)
        code, started = self.call("start", "--ticket", "c-4", "--title", "t", "--objective", "o")
        self.assertEqual(0, code, started)
        self.assertEqual(confirmed["sha256"], self.state()["delivery"]["controller_policies"]["gates"]["sha256"])

    def test_start_is_accepted_when_the_controller_policies_did_not_change(self) -> None:
        self.craft(stage="REVIEW", review="APPROVED")
        self.edit_state(lambda data: (data["stage"].update(current="DONE", status="DONE"),
                                      data["gates"].update(ci={"status": "DISABLED_BY_PROJECT_POLICY"})))
        self.assertEqual(0, self.call("close")[0])
        code, started = self.call("start", "--ticket", "c-5", "--title", "t", "--objective", "o")
        self.assertEqual(0, code, started)


class LocalControllerExitTests(unittest.TestCase):
    """CONTROLLER_WRITABLE_BY_WORKER: the printed exit, executed, moves the controller out and the demand re-runs.

    The controller is physically inside the repository (``--local-storage``), with or
    without an Obsidian binding that relabels the storage. ``next`` must stop before any
    writing stage — also for an action prepared earlier — and every printed exit command
    must succeed when the controller runs it with the user's answers filled in.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="sdd-local-exit-")
        base = Path(self.temp.name).resolve()
        self.repo, self.vault, self.fake = base / "repo", base / "vault", base / "fake"
        for path in (self.repo, self.vault / ".obsidian", self.vault / "Projects" / "Demo", self.fake):
            path.mkdir(parents=True)
        self.env = {key: value for key, value in e2e.process_environment.items() if not key.startswith(("HERMES_", "GIT_"))}
        self.env.update(PATH=f"{self.fake}{os.pathsep}{e2e.process_environment.get('PATH', '')}", GIT_AUTHOR_NAME="t",
                        GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")
        for name in ("claude", "codex"):
            script = self.fake / name
            script.write_text(f"#!{sys.executable}\n{e2e.FAKE_EXECUTOR}", encoding="utf-8")
            script.chmod(0o755)
        for argv in (["git", "init", "-q", "-b", "main"], ["git", "config", "user.email", "t@e"], ["git", "config", "user.name", "t"]):
            run(argv, cwd=self.repo, env=self.env)
        (self.repo / "AGENTS.md").write_text("# Demo\nUse TDD.\n", encoding="utf-8")
        run(["git", "add", "."], cwd=self.repo, env=self.env)
        run(["git", "commit", "-qm", "init"], cwd=self.repo, env=self.env)
        installed = run([sys.executable, str(e2e.INSTALLER), "--target", str(self.repo), "--local-storage", "--apply", "--json"],
                        cwd=self.repo, env=self.env)
        self.assertEqual("APPLIED", json.loads(installed.stdout)["status"], installed.stdout)
        self.orchestration = self.repo / ".hermes" / "orchestration"
        self.sdd = [sys.executable, str(self.orchestration / "runtime" / "sdd.py")]
        gates = self.orchestration / "policies" / "GATES.md"
        python = shlex.quote(sys.executable)
        lines = []
        for line in gates.read_text(encoding="utf-8").splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if line.startswith("|") and cells and cells[0] in ("Focused tests", "Format", "Analyze"):
                line = f"| {cells[0]} | `{python} -c pass` | host | 60 s |"
            lines.append(line)
        gates.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.fill = {**FILL, "<vault>": str(self.vault), "<project>": "Projects/Demo"}

    def tearDown(self) -> None:
        self.temp.cleanup()

    def call(self, *arguments: str, sdd: list[str] | None = None) -> tuple[int, dict]:
        completed = run([*(sdd or self.sdd), *arguments], cwd=self.repo, env=self.env)
        return completed.returncode, json.loads(completed.stdout)

    def paths(self) -> dict:
        return json.loads(run([sys.executable, str(self.orchestration / "runtime" / "action_journal.py"), "--json", "paths"],
                              cwd=self.repo, env=self.env).stdout)

    def bind_to_vault(self) -> None:
        """A --local-storage install that also carries an Obsidian binding: storage reads OBSIDIAN, the controller stays."""
        sys.path.insert(0, str(self.orchestration / "runtime"))
        import obsidian_binding  # noqa: PLC0415

        binding = {"schema_version": obsidian_binding.SCHEMA_VERSION, "vault_path": str(self.vault), "project_container": "Projects/Demo"}
        (self.repo / ".hermes" / "obsidian.json").write_text(json.dumps(binding), encoding="utf-8")
        paths = self.paths()
        self.assertEqual("OBSIDIAN", paths["storage"])
        runtime = Path(paths["runtime_dir"])
        runtime.mkdir(parents=True, exist_ok=True)
        for name in ("STATE.md", "ACTION_JOURNAL.json", "INCIDENTS.md"):
            if (self.orchestration / name).exists():
                shutil.move(str(self.orchestration / name), str(runtime / name))

    def craft_implement(self) -> None:
        code, started = self.call("start", "--ticket", "c-1", "--title", "t", "--objective", "o")
        self.assertEqual(0, code, started)
        head = run(["git", "rev-parse", "HEAD"], cwd=self.repo, env=self.env).stdout.strip()
        artifact = Path(self.temp.name) / "artifact.json"
        artifact.write_text("{}", encoding="utf-8")
        accepted = {"action_id": "c-1-x-01", "artifact": str(artifact), "artifact_sha256": hashlib.sha256(b"{}").hexdigest()}
        state_path = Path(self.paths()["state"])
        text = state_path.read_text(encoding="utf-8")
        data = sf.parse(text)
        path = sf.profile_path("CODE")
        data["stage"] = {"current": "IMPLEMENT", "status": "RUNNING", "completed": list(path[:path.index("IMPLEMENT")]),
                         "skipped": [{"stage": "CLARIFY", "reason": "none"}]}
        delivery = data["delivery"]
        delivery["acceptance"] = {"AC-1": {"criterion": "greet works", "verification_method": "focused unit test", "verifier": "AGENT", "slice_id": "S1"}}
        delivery["slices"] = {"planned": ["S1"], "completed": [], "editable_paths": ["src/feature.py"]}
        delivery["accepted"] = {name: dict(accepted) for name in ("specify", "plan", "tasks")}
        delivery["project_context"] = {"status": "CURRENT", "checked_head": head, "evidence": "e", "gaps": []}
        data["loop"]["budgets"]["stage_transitions"]["used"] = path.index("IMPLEMENT")
        state_path.write_text(sf.dump(text, data, log="test fixture"), encoding="utf-8")

    def run_exit(self, commands: list[str]) -> list[dict]:
        results = []
        for printed in commands:
            for placeholder, value in self.fill.items():
                printed = printed.replace(placeholder, value)
            self.assertNotRegex(printed, r"<[a-z-]+>", "every placeholder is the user's answer or the skill path")
            completed = run(shlex.split(printed), cwd=self.repo, env=self.env)
            self.assertEqual(0, completed.returncode, (printed, completed.stdout[-1500:], completed.stderr[-800:]))
            results.append(json.loads(completed.stdout) if completed.stdout.strip().startswith("{") else {})
        return results

    def assert_moved_out_and_progressing(self, stopped: dict) -> None:
        self.assertEqual("CONTROLLER_WRITABLE_BY_WORKER", stopped.get("stop_reason"), stopped)
        self.assertEqual([], stopped["commands"])
        self.assertNotIn("migrate_to_vault", " ".join(stopped["exit_commands"]), "migrate_to_vault keeps runtime/ and policies/ in the repo")
        self.assertEqual(stopped["exit_commands"][0], stopped["next_command"])
        results = self.run_exit(stopped["exit_commands"])
        self.assertEqual("ABANDONED", results[0]["status"])
        self.assertEqual("READY", results[1]["status"], "the dry run is READY before --apply")
        self.assertEqual("APPLIED", results[2]["status"])
        self.assertEqual("STARTED", results[-1]["status"])
        self.assertFalse(self.orchestration.exists(), "no controller is left inside the repository")
        container = self.vault / "Projects" / "Demo"
        self.assertTrue((container / ".hermes-local-controller-backup" / "orchestration" / "runtime" / "sdd.py").is_file())
        new = [sys.executable, str(container / ".hermes" / "orchestration" / "runtime" / "sdd.py")]
        code, progress = self.call("next", sdd=new)
        self.assertEqual(0, code, progress)
        self.assertEqual("PREPARE", progress.get("step"), progress)

    def test_local_storage_exit_moves_the_controller_out_when_executed(self) -> None:
        self.craft_implement()
        code, stopped = self.call("next")
        self.assertEqual(0, code, stopped)
        self.assert_moved_out_and_progressing(stopped)

    def test_an_obsidian_label_does_not_hide_a_controller_inside_the_repository(self) -> None:
        """Finding 2: the criterion is the physical location (the launcher's), not the storage label."""
        self.bind_to_vault()
        self.craft_implement()
        code, stopped = self.call("next")
        self.assertEqual(0, code, stopped)
        self.assertNotIn(stopped.get("step"), {"PREPARE", "DISPATCH"}, "the launcher would refuse that batch every time")
        self.assert_moved_out_and_progressing(stopped)

    def test_an_action_prepared_before_the_check_is_refused_and_its_exit_archives_it(self) -> None:
        """Finding 3: a PREPARED writing action takes the recovery DISPATCH path, which checks isolation too."""
        executors = self.orchestration / "policies" / "EXECUTORS.md"
        writing = executors.read_text(encoding="utf-8")
        executors.write_text(writing.replace('"IMPLEMENT": {"executor": "codex",  "model": null,',
                                             '"IMPLEMENT": {"executor": "codex",  "model": null, "sandbox": "read-only",'), encoding="utf-8")
        self.craft_implement()
        code, prepare = self.call("next")
        self.assertEqual("PREPARE", prepare.get("step"), prepare)
        for printed in prepare["commands"]:
            completed = run(shlex.split(printed), cwd=self.repo, env=self.env)
            self.assertEqual(0, completed.returncode, completed.stdout)
        executors.write_text(writing, encoding="utf-8")  # the upgrade: IMPLEMENT writes again
        journal = json.loads(Path(self.paths()["journal"]).read_text(encoding="utf-8"))
        self.assertEqual("PREPARED", journal["action"]["status"])
        code, stopped = self.call("next")
        self.assertNotEqual("DISPATCH", stopped.get("step"), "the launcher would refuse that dispatch every time")
        self.assertEqual(journal["action"]["id"], stopped.get("action_id"))
        self.assert_moved_out_and_progressing(stopped)
        history = Path(self.temp.name) / "vault" / "Projects" / "Demo" / ".hermes-local-controller-backup" / "orchestration"
        archived = list((history / "action-journal-history").rglob(f"{journal['action']['id']}*.json"))
        self.assertTrue(archived, "abandon archived the undispatched action as evidence")

    def test_the_location_check_matches_a_case_different_spelling(self) -> None:
        sys.path.insert(0, str(self.orchestration / "runtime"))
        probe = Path(str(self.repo).swapcase())
        if not probe.exists():
            self.skipTest("case-sensitive filesystem")
        completed = run([sys.executable, "-c", "import sys, sdd; from pathlib import Path; print(sdd.controller_inside_repository(Path(sys.argv[1])))",
                         str(probe)], cwd=self.orchestration / "runtime", env=self.env)
        self.assertEqual("True", completed.stdout.strip(), completed.stderr)
        completed = run([sys.executable, "-c", "import sys, sdd; from pathlib import Path; print(sdd.controller_inside_repository(Path(sys.argv[1])))",
                         str(self.vault)], cwd=self.orchestration / "runtime", env=self.env)
        self.assertEqual("False", completed.stdout.strip(), completed.stderr)


if __name__ == "__main__":
    unittest.main()
