"""End-to-end proof that the controller cannot get lost.

A temporary Git repository and a fake Obsidian vault are installed with the real
installer; fake ``claude``/``codex`` executables on PATH return stage results
derived only from the controller data block of the prompt. The test then drives
one demand from ``sdd.py start`` to DONE by executing **only** the commands
``sdd.py next`` prints (and, at a human checkpoint, the printed ``next_command``
with the user's words filled in). Along the way one PLAN dispatch times out,
the format gate times out once and is re-run, REVIEW asks for changes once and
the reopened FIX slice reaches DONE without any budget raise, one HUMAN
acceptance check is satisfied by ``sdd.py waive``, and DONE is closed back to
IDLE so a second demand starts. A DECISION_DOC demand recovers from one failed
gate inside IMPLEMENT. The test never writes STATE or the journal and records
the controller-visible output bytes per stage.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from os import environ as process_environment
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "orchestrate" / "sdd-orchestrator"
INSTALLER = SKILL_ROOT / "scripts" / "install_project.py"
MAX_STEPS = 160
#: The user's words a human checkpoint fills into the printed command.
USER_WORDS = {"<user words>": "esta aprovado, pode seguir", "<why>": "PO approval given by the requester",
              "<failure>": "the gate or review failed", "<ticket-id>": "greet-2", "<title>": "Second", "<objective>": "Another demand"}
#: Stops the scripted user answers by running the printed next_command (never a budget raise).
ANSWERED_STOPS = {"HUMAN_DECISION_REQUIRED", "GATE_TIMEOUT", "REVIEW_CHANGES_REQUIRED", "FOCUSED_TESTS_FAILED", "FORMAT_FAILED", "ANALYZE_FAILED"}
#: Controller-visible bytes (next + printed command outputs) per executor dispatch in a stage.
MAX_OUTPUT_BYTES_PER_DISPATCH = 14 * 1024

FAKE_EXECUTOR = r'''
import json, os, re, sys, time
from pathlib import Path

name = Path(sys.argv[0]).name
argv = sys.argv[1:]
if "--version" in argv:
    print(f"{name} 99.0.0"); sys.exit(0)
prompt = sys.stdin.read()
fake_dir = Path(sys.argv[0]).resolve().parent  # the launcher's allow-listed environment drops FAKE_* variables
log = fake_dir / "calls.jsonl"
match = re.search(r"## Controller data \(authoritative\)\s*```json\n(.*?)\n```", prompt, re.S)
data = json.loads(match.group(1))
with log.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({"executor": name, "stage": data["stage"], "role": data["role"], "prompt_bytes": len(prompt.encode())}) + "\n")
marker = fake_dir / f"timeout-{data['stage']}"
if data["role"] is None and marker.exists():
    marker.unlink()
    time.sleep(60)
stage, role = data["stage"], data["role"]
changes = Path(os.environ["FAKE_EXECUTOR_DIR"]) / f"changes-{stage}"
slice_info = data.get("slice") or {}
focused = (slice_info.get("required_commands") or [None])[0]
waivers = data.get("recorded_waivers") or {}
current = set(slice_info.get("current_slice_ids") or []) | set(slice_info.get("completed_slice_ids") or [])

def checks(mode):
    if not data["acceptance"]:
        base = [
            {"id": "AC-1", "criterion": "greet() returns 'hello <name>'", "verification_method": "focused unit test", "verifier": "AGENT", "slice_id": None},
            {"id": "AC-2", "criterion": "Approved by the product owner", "verification_method": "recorded human decision", "verifier": "HUMAN", "slice_id": None},
        ]
    else:
        base = [{key: item[key] for key in ("id", "criterion", "verification_method", "verifier", "slice_id")} for item in data["acceptance"]]
    out = []
    for item in base:
        item = dict(item)
        if stage == "TASKS" and role is None:
            item["slice_id"] = "S1"
        verified = mode == "all" or (mode == "slice" and item["slice_id"] in current)
        if not verified:
            item.update(status="PLANNED", evidence=None, waiver=None)
        elif item["id"] in waivers:
            item.update(status="WAIVED", evidence=f"Recorded decision: \"{waivers[item['id']]['quote']}\"", waiver=waivers[item["id"]])
        else:
            item.update(status="PASS", evidence=f"`{focused}` passed", waiver=None)
        out.append(item)
    return out

if stage == "REVIEW":
    requested = changes.exists()
    if requested:
        changes.unlink()
    findings = [{"severity": "MAJOR", "path": "src/feature.py", "description": "greet() must strip the name", "evidence": "src/feature.py:2"}] if requested else []
    result = {"review_result": {
        "schema_version": 3, "status": "CHANGES_REQUIRED" if requested else "APPROVED", "reviewed_paths": [{"path": "src/feature.py"}], "findings": findings,
        "baseline": {"preserved": True, "violations": []}, "ownership": {"valid": True, "violations": []},
        "acceptance": {"verified": True, "expected_check_ids": [item["id"] for item in data["acceptance"]], "checks": checks("all")},
        "e2e": {"files_modified": False, "execution_performed": False, "violation": False},
        "forbidden_actions": {"violations": []},
        "gate_status": {"focused_tests": data["gates"]["focused_tests"], "format": data["gates"]["format"], "analyze": data["gates"]["analyze"], "ci": data["gates"]["ci"]},
        "next_step": {"action": "EVALUATE_DONE_WITH_CI_DISABLED"}}}
else:
    modified, created, commands, slices = [], [], [], []
    mode = "none"
    if stage == "IMPLEMENT" and role is None:
        repo = Path.cwd()
        (repo / "src").mkdir(exist_ok=True); (repo / "tests").mkdir(exist_ok=True)
        (repo / "src" / "feature.py").write_text("def greet(name):\n    return f'hello {name}'\n")
        (repo / "tests" / "__init__.py").write_text("")
        (repo / "tests" / "test_feature.py").write_text(
            "import sys, unittest\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\n"
            "from feature import greet\n\nclass T(unittest.TestCase):\n    def test_greet(self):\n        self.assertEqual('hello ana', greet('ana'))\n")
        created = [{"path": "src/feature.py"}, {"path": "tests/__init__.py"}, {"path": "tests/test_feature.py"}]
        commands = [{"command": focused, "purpose": "focused tests", "timeout_seconds": 300, "exit_code": 0, "result": "PASS"}]
        slices = [{"id": slice_info["current_slice_ids"][0], "objective": "greet", "test_file": "tests/test_feature.py", "red_command": focused, "red_exit_code": 1,
                   "expected_failure": "ImportError: greet missing", "red_failure_kind": "EXPECTED_FUNCTIONAL", "minimal_implementation": "greet",
                   "green_command": focused, "green_exit_code": 0, "green_result": "1 test OK"}]
        mode = "slice"
    if stage == "TEST":
        commands = [{"command": focused, "purpose": "focused tests", "timeout_seconds": 300, "exit_code": 0, "result": "PASS"}]
        mode = "all"
    nxt = {"SPECIFY": "PLAN", "CLARIFY": "PLAN", "PLAN": "TASKS", "TASKS": "IMPLEMENT", "IMPLEMENT": "TEST", "TEST": "REVIEW"}[stage]
    result = {"executor_result": {
        "schema_version": 3, "stage": {"value": stage, "status": "SUCCESS"},
        "executor": {"name": data["executor"], "invocation_type": "EXTERNAL_CLI"},
        "consulted_paths": [{"path": "AGENTS.md"}], "modified_paths": modified, "created_paths": created,
        "validated_symbols": [], "commands": commands, "blockers": [],
        "context_assessment": {"facts": [{"statement": "repository is small", "evidence": "AGENTS.md"}], "assumptions": [], "unresolved_questions": []},
        "acceptance_checks": checks(mode),
        "stage_payload": {"summary": f"{role or stage} done", "tasks": ["implement greet"] if stage == "TASKS" else [],
                          "impact_files": ["src/feature.py", "tests/__init__.py", "tests/test_feature.py"]
                          if stage == "TASKS" or (stage == "PLAN" and role is None and data["profile"] == "DECISION_DOC") else [],
                          "decisions": []},
        "tdd_slices": slices, "next_step": {"stage": nxt, "action": "continue"}}}
if name == "claude":
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "structured_output": result}))
else:
    out = argv[argv.index("--output-last-message") + 1]
    Path(out).write_text(json.dumps(result))
'''


#: A gate command: fails (FAIL) or sleeps past its timeout (TIMEOUT) once when its marker exists, else runs the real check.
FAKE_GATE = r'''
import os, subprocess, sys, time
from pathlib import Path

name, rest = sys.argv[1], sys.argv[2:]
directory = Path(os.environ["FAKE_EXECUTOR_DIR"])
for mode in ("fail", "hang"):
    marker = directory / f"{mode}-{name}"
    if marker.exists():
        marker.unlink()
        if mode == "fail":
            print(f"{name}: injected failure"); sys.exit(1)
        time.sleep(30)
sys.exit(subprocess.run([sys.executable, *rest]).returncode)
'''


def run(argv: list[str], *, cwd: Path, env: dict[str, str], timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout, check=False)


class ControllerEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="sdd-e2e-")
        base = Path(self.temp.name).resolve()
        self.repo, self.vault, self.fake = base / "repo", base / "vault", base / "fake"
        for path in (self.repo, self.vault / "Projects" / "Demo", self.vault / ".obsidian", self.fake):
            path.mkdir(parents=True)
        self.env = {key: value for key, value in process_environment.items() if not key.startswith(("HERMES_", "GIT_"))}
        self.env.update(PATH=f"{self.fake}{os.pathsep}{process_environment.get('PATH', '')}", FAKE_EXECUTOR_DIR=str(self.fake),
                        FAKE_EXECUTOR_LOG=str(self.fake / "calls.jsonl"), GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e",
                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")
        for name in ("claude", "codex"):
            script = self.fake / name
            script.write_text(f"#!{sys.executable}\n{FAKE_EXECUTOR}", encoding="utf-8")
            script.chmod(0o755)
        for argv in (["git", "init", "-q", "-b", "main"], ["git", "config", "user.email", "t@e"], ["git", "config", "user.name", "t"]):
            run(argv, cwd=self.repo, env=self.env)
        (self.repo / "AGENTS.md").write_text("# Demo\nUse TDD.\n", encoding="utf-8")
        run(["git", "add", "."], cwd=self.repo, env=self.env)
        run(["git", "commit", "-qm", "init"], cwd=self.repo, env=self.env)
        (self.repo / "notes.txt").write_text("user's own pending work\n", encoding="utf-8")
        installed = run([sys.executable, str(INSTALLER), "--target", str(self.repo), "--obsidian-vault", str(self.vault),
                         "--obsidian-project", "Projects/Demo", "--apply", "--json"], cwd=self.repo, env=self.env)
        self.assertEqual("APPLIED", json.loads(installed.stdout)["status"], installed.stdout + installed.stderr)
        self.container = self.vault / "Projects" / "Demo"
        orchestration = self.container / ".hermes" / "orchestration"
        self.sdd = [sys.executable, str(orchestration / "runtime" / "sdd.py")]
        # Project-owned configuration a human sets up once (GATES.md, EXECUTORS.md): legitimate, not STATE.
        python = shlex.quote(sys.executable)
        (self.fake / "gate.py").write_text(FAKE_GATE, encoding="utf-8")
        gate = f"{python} {shlex.quote(str(self.fake / 'gate.py'))}"
        gates = orchestration / "policies" / "GATES.md"
        text = gates.read_text(encoding="utf-8")
        rows = {
            "Focused tests": (f"`{python} -m unittest discover -s tests`", 120),
            "Format": (f"`{gate} format -m py_compile {{files}}`", 3),
            "Analyze": (f"`{gate} analyze -m compileall -q src`", 120),
        }
        lines = []
        for line in text.splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if line.startswith("|") and cells and cells[0] in rows:
                line = f"| {cells[0]} | {rows[cells[0]][0]} | host | {rows[cells[0]][1]} s |"
            lines.append(line)
        gates.write_text("\n".join(lines) + "\n", encoding="utf-8")
        executors = orchestration / "policies" / "EXECUTORS.md"
        executors.write_text(re.sub(r'("PLAN":\s*\{"executor": "claude", "model": null, "timeout_seconds": )900',
                                    r"\g<1>3", executors.read_text(encoding="utf-8")), encoding="utf-8")
        (self.fake / "timeout-PLAN").write_text("inject one timeout\n", encoding="utf-8")
        (self.fake / "hang-format").write_text("inject one gate timeout\n", encoding="utf-8")
        (self.fake / "changes-REVIEW").write_text("inject one CHANGES_REQUIRED\n", encoding="utf-8")
        self.paths = json.loads(run([sys.executable, str(orchestration / "runtime" / "action_journal.py"), "--json", "paths"],
                                    cwd=self.repo, env=self.env).stdout)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def sdd_json(self, *arguments: str) -> dict:
        completed = run([*self.sdd, *arguments], cwd=self.repo, env=self.env)
        return json.loads(completed.stdout)

    def drive(self, report: dict) -> tuple[list[str], list[str], list[str]]:
        """Run `next` and exactly the printed commands until DONE; answer ANSWERED_STOPS with the printed command."""
        executed: list[str] = []
        stops: list[str] = []
        failures: list[str] = []
        stage_bytes = report.setdefault("stage_output_bytes", {})
        per_command = report.setdefault("largest_command_output", {})
        for _ in range(MAX_STEPS):
            stage = self.sdd_json("status")["stage"]
            decision = run([*self.sdd, "next"], cwd=self.repo, env=self.env)
            stage_bytes[stage] = stage_bytes.get(stage, 0) + len(decision.stdout)
            result = json.loads(decision.stdout)
            self.assertIn("next_step", result)
            self.assertTrue(result["next_command"], result)
            if result["end_turn"]:
                stops.append(result["stop_reason"])
                checkpoints = report.setdefault("checkpoints_by_stage", {})
                checkpoints[stage] = checkpoints.get(stage, 0) + 1
                if result["stop_reason"] == "DONE":
                    return executed, stops, failures
                self.assertIn(result["stop_reason"], ANSWERED_STOPS, result)
                self.assertNotIn("budget --raise", result["next_command"])
                command = result["next_command"]
                for placeholder, words in USER_WORDS.items():
                    command = command.replace(placeholder, words)
                answered = run(shlex.split(command), cwd=self.repo, env=self.env)
                stage_bytes[stage] += len(answered.stdout)
                self.assertEqual(0, answered.returncode, answered.stdout)
                executed.append(shlex.split(command)[2])
                continue
            for printed in result["commands"]:
                completed = run(shlex.split(printed), cwd=self.repo, env=self.env)
                stage_bytes[stage] += len(completed.stdout) + len(completed.stderr)
                executed.append(f"{result['step']}:{Path(shlex.split(printed)[1]).name}")
                key = f"{result['step']}:{Path(shlex.split(printed)[1]).name}:{shlex.split(printed)[2]}"
                per_command[key] = max(per_command.get(key, 0), len(completed.stdout) + len(completed.stderr))
                if completed.returncode:
                    payload = json.loads(completed.stdout)
                    self.assertIn(payload["status"], {"EXECUTOR_TIMEOUT", "GATE_TIMEOUT", "FORMAT_FAILED", "ANALYZE_FAILED"}, completed.stdout)
                    failures.append(payload["status"])
                    break
        self.fail(f"did not reach DONE in {MAX_STEPS} steps: {executed[-10:]}")

    def test_a_demand_reaches_done_only_through_printed_commands(self) -> None:
        state_path, journal_path = Path(self.paths["state"]), Path(self.paths["journal"])
        status = self.sdd_json("status")
        self.assertEqual("IDLE", status["stage"])
        self.assertEqual("IDLE_NO_DEMAND", status["next_action"])
        idle = self.sdd_json("next")
        self.assertTrue(idle["end_turn"])
        start_argv = shlex.split(idle["next_command"].replace("<ticket-id>", "greet-1").replace("<title>", "Greeting")
                                 .replace("<objective>", "Add greet(name); approved by the PO"))
        started = json.loads(run(start_argv, cwd=self.repo, env=self.env).stdout)
        self.assertEqual("STARTED", started["status"])
        self.assertEqual(["notes.txt"], started["protected_preexisting"])

        report: dict = {}
        executed, stops, failures = self.drive(report)
        stage_bytes = report["stage_output_bytes"]
        final = self.sdd_json("status")
        self.assertEqual("DONE", final["stage"])
        self.assertEqual(["EXECUTOR_TIMEOUT", "GATE_TIMEOUT"], failures)
        self.assertEqual(["HUMAN_DECISION_REQUIRED", "GATE_TIMEOUT", "REVIEW_CHANGES_REQUIRED", "DONE"], stops)
        self.assertEqual(1, executed.count("waive"))
        self.assertEqual(1, executed.count("reopen"))
        self.assertEqual(1, executed.count("gate"), "the timed-out gate is re-run once through the printed command")
        self.assertIn("RECOVER:action_journal.py", executed)
        calls = [json.loads(line) for line in (self.fake / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(["SPECIFY", "PLAN", "PLAN", "PLAN", "TASKS", "IMPLEMENT", "TEST", "REVIEW", "IMPLEMENT", "TEST", "REVIEW"],
                         [call["stage"] for call in calls])
        self.assertEqual("PROJECT_CONTEXT_GUARDIAN", calls[1]["role"])
        self.assertLess(calls[3]["prompt_bytes"], 48 * 1024 // 2, "the retry after a timeout uses reduced context")
        state_text = state_path.read_text(encoding="utf-8")
        self.assertIn('"current": "DONE"', state_text)
        self.assertIn("CLARIFY", state_text)  # skipped with a recorded reason
        self.assertIn("esta aprovado, pode seguir", state_text)
        self.assertIn("Gate format PASS (exit 0); rerun confirmed by the user", state_text)
        self.assertIn("FIX1", state_text)
        self.assertNotIn('"raises"', state_text, "the REVIEW reopen reached DONE without any budget raise")
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        self.assertEqual("IDLE", journal["action"]["status"])
        history = sorted(path.name for path in (Path(self.paths["history_dir"]) / "greet-1").glob("*.json"))
        self.assertIn("greet-1-plan-01.json", history)
        self.assertIn("greet-1-implement-fix1-01.json", history)
        interrupted = json.loads((Path(self.paths["history_dir"]) / "greet-1" / "greet-1-plan-01.json").read_text(encoding="utf-8"))
        self.assertEqual(("INTERRUPTED", 124), (interrupted["action"]["status"], interrupted["process"]["exit_code"]))
        self.assertEqual(b"user's own pending work\n", (self.repo / "notes.txt").read_bytes())
        wiki = self.container / "raw" / "articles" / "greet-1"
        self.assertTrue(any(wiki.glob("*.md")), "stage records in the wiki")
        self.assertTrue(any((wiki / "gates").glob("*.md")), "gate records in the wiki")
        self.assertTrue(any((self.container / "concepts").glob("*.md")), "decision pages in the wiki")

        # DONE -> close -> IDLE -> a new demand, only through printed commands.
        done = self.sdd_json("next")
        self.assertEqual("DONE", done["stop_reason"])
        closed = run(shlex.split(done["next_command"]), cwd=self.repo, env=self.env)
        self.assertEqual(0, closed.returncode, closed.stdout)
        stage_bytes["DONE"] = stage_bytes.get("DONE", 0) + len(closed.stdout)
        idle = self.sdd_json("next")
        self.assertEqual("IDLE_NO_DEMAND", idle["stop_reason"])
        argv = idle["next_command"]
        for placeholder, words in USER_WORDS.items():
            argv = argv.replace(placeholder, words)
        second = json.loads(run(shlex.split(argv), cwd=self.repo, env=self.env).stdout)
        self.assertEqual(("STARTED", "greet-2"), (second["status"], second["ticket"]))
        self.assertEqual("PREPARE", self.sdd_json("next")["step"])
        self.assertIn('"ticket": "greet-1"', state_path.read_text(encoding="utf-8"), "the closed demand is archived in STATE")

        report.update(commands=len(executed), total_output_bytes=sum(stage_bytes.values()),
                      largest_command_output=dict(sorted(report["largest_command_output"].items(), key=lambda item: -item[1])[:8]))
        Path(process_environment.get("SDD_E2E_REPORT", os.devnull)).write_text(json.dumps(report, indent=1), encoding="utf-8")
        # Bound: one budget per executor dispatch plus one per human checkpoint answered in the stage.
        units = {stage: sum(1 for call in calls if call["stage"] == stage) + report["checkpoints_by_stage"].get(stage, 0) for stage in stage_bytes}
        for stage, size in stage_bytes.items():
            with self.subTest(stage=stage):
                self.assertLess(size, MAX_OUTPUT_BYTES_PER_DISPATCH * max(1, units[stage]))

    def test_decision_doc_gate_failure_recovers_through_an_in_stage_fix_slice(self) -> None:
        for marker in ("timeout-PLAN", "hang-format", "changes-REVIEW"):
            (self.fake / marker).unlink()
        (self.fake / "fail-analyze").write_text("inject one analyze failure\n", encoding="utf-8")
        started = self.sdd_json("start", "--ticket", "adr-2", "--title", "ADR", "--objective", "Decide the greeting policy",
                                "--deliverable-kind", "DECISION_DOC")
        self.assertEqual("STARTED", started["status"])
        report: dict = {}
        executed, stops, failures = self.drive(report)
        self.assertEqual(["ANALYZE_FAILED"], failures)
        self.assertEqual(["HUMAN_DECISION_REQUIRED", "ANALYZE_FAILED", "DONE"], stops)
        self.assertEqual(1, executed.count("reopen"))
        self.assertNotIn("transition", [item for item in executed if not item.startswith("TRANSITION:")])
        calls = [json.loads(line) for line in (self.fake / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(["SPECIFY", "PLAN", "PLAN", "IMPLEMENT", "IMPLEMENT", "REVIEW"], [call["stage"] for call in calls])
        state = Path(self.paths["state"]).read_text(encoding="utf-8")
        self.assertIn('"current": "DONE"', state)
        self.assertIn("IMPLEMENT->FIX1", state)
        self.assertNotIn('"raises"', state)

    def test_snapshot_is_generated_and_accepted_by_the_planner(self) -> None:
        idle = self.sdd_json("snapshot")
        self.assertEqual("IDLE_NO_DEMAND", idle["status"])
        self.assertIn("sdd.py", idle["next_command"])
        self.sdd_json("start", "--ticket", "snap-1", "--title", "t", "--objective", "o")
        written = self.sdd_json("snapshot")
        self.assertEqual("WRITTEN", written["status"])
        planned = run(shlex.split(written["next_command"]), cwd=self.repo, env=self.env)
        self.assertEqual(0, planned.returncode, planned.stdout)
        self.assertEqual("SPECIFY", json.loads(planned.stdout)["input"]["start_stage"])

    def test_decision_doc_profile_skips_tasks_and_test(self) -> None:
        started = self.sdd_json("start", "--ticket", "adr-1", "--title", "ADR", "--objective", "Decide the session policy",
                                "--deliverable-kind", "DECISION_DOC")
        self.assertEqual(["SPECIFY", "CLARIFY", "PLAN", "IMPLEMENT", "REVIEW", "DONE"], started["stages"])


if __name__ == "__main__":
    unittest.main()
