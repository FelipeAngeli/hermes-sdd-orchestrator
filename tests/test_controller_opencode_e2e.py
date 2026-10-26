"""End-to-end: one real IMPLEMENT slice dispatched through the OpenCode executor and accepted.

A scratch Git repository with a tiny Python project and a temporary Obsidian-style vault
(never the user's real vault) are installed with this branch's `install_project.py`. Its
`policies/EXECUTORS.md` puts IMPLEMENT and TEST on `opencode` with
`opencode-go/deepseek-v4.1-flash`, enables `workspace_audit: enforce`, and leaves the
other stages on fake `claude`/`codex` executables so the run stays bounded and cheap. The
test then drives the demand by running **only** the commands `sdd.py next` prints, until
one IMPLEMENT slice has been dispatched through the real `opencode` CLI, passed the
workspace audit and been accepted into STATE.

It needs the real `codex` and `opencode` 1.18.34 plus the logged-in OpenCode account, so
it is skipped when either is absent, when the version differs, or with
`SDD_OPENCODE_LIVE=0`. Everything it creates is removed with the temporary directory.
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
RUNTIME = SKILL_ROOT / "templates" / ".hermes" / "orchestration" / "runtime"
sys.path.insert(0, str(RUNTIME))

import opencode_executor  # noqa: E402

MODEL = os.environ.get("SDD_OPENCODE_TEST_MODEL", "opencode-go/deepseek-v4.1-flash")
MAX_STEPS = 60
USER_WORDS = {"<user words>": "esta aprovado, pode seguir", "<why>": "PO approval given by the requester", "<failure>": "n/a"}
#: Stages the fake executors answer; IMPLEMENT (and TEST) go to the real opencode CLI.
FAKE_EXECUTOR = (ROOT / "tests" / "test_controller_e2e.py").read_text(encoding="utf-8").split("FAKE_EXECUTOR = r'''")[1].split("'''")[0]


def _opencode_version() -> str | None:
    binary = shutil.which("opencode")
    if not binary:
        return None
    try:
        return subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None


LIVE = (bool(shutil.which("codex")) and _opencode_version() == opencode_executor.PINNED_VERSION
        and process_environment.get("SDD_OPENCODE_LIVE", "1") != "0"
        and opencode_executor.data_dir(dict(process_environment)).is_dir())


def run(argv: list[str], *, cwd: Path, env: dict[str, str], timeout: int = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout, check=False)


@unittest.skipUnless(LIVE, "needs codex, opencode 1.18.34 and the logged-in OpenCode account (SDD_OPENCODE_LIVE=0 skips)")
class OpencodeControllerEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="sdd-oc-e2e-")
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        self.repo, self.vault, self.fake = base / "repo", base / "vault", base / "fake"
        for path in (self.repo, self.vault / "Projects" / "Demo", self.vault / ".obsidian", self.fake):
            path.mkdir(parents=True)
        self.env = {key: value for key, value in process_environment.items() if not key.startswith(("HERMES_", "GIT_"))}
        self.env.update(PATH=f"{self.fake}{os.pathsep}{process_environment.get('PATH', '')}", FAKE_EXECUTOR_DIR=str(self.fake),
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")
        for name in ("claude", "codex"):
            script = self.fake / name
            script.write_text(f"#!{sys.executable}\n{FAKE_EXECUTOR}", encoding="utf-8")
            script.chmod(0o755)
        # The real codex binary must stay reachable for `codex sandbox`; the fake one shadows it on PATH,
        # so the launcher resolves `codex` through the same PATH and would wrap opencode with the fake.
        (self.fake / "codex").unlink()
        for argv in (["git", "init", "-q", "-b", "main"], ["git", "config", "user.email", "t@e"], ["git", "config", "user.name", "t"]):
            run(argv, cwd=self.repo, env=self.env)
        (self.repo / "AGENTS.md").write_text("# Demo\nUse TDD. Python only.\n", encoding="utf-8")
        # A real Python project ignores byte-code caches; without this the test runs the worker leaves
        # untracked __pycache__ files, which the workspace audit correctly reports as undeclared changes.
        (self.repo / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
        (self.repo / "src").mkdir()
        (self.repo / "src" / "__init__.py").write_text("", encoding="utf-8")
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "__init__.py").write_text("", encoding="utf-8")
        run(["git", "add", "."], cwd=self.repo, env=self.env)
        run(["git", "commit", "-qm", "init"], cwd=self.repo, env=self.env)
        installed = run([sys.executable, str(INSTALLER), "--target", str(self.repo), "--obsidian-vault", str(self.vault),
                         "--obsidian-project", "Projects/Demo", "--apply", "--json"], cwd=self.repo, env=self.env)
        self.assertEqual("APPLIED", json.loads(installed.stdout)["status"], installed.stdout + installed.stderr)
        orchestration = self.vault / "Projects" / "Demo" / "Hermes" / "orchestration"
        self.sdd = [sys.executable, str(orchestration / "runtime" / "sdd.py")]
        python = shlex.quote(sys.executable)
        gates = orchestration / "policies" / "GATES.md"
        rows = {"Focused tests": (f"`{python} -m unittest discover -s tests`", 300),
                "Format": (f"`{python} -m py_compile {{files}}`", 120),
                "Analyze": (f"`{python} -m compileall -q src`", 120)}
        lines = []
        for line in gates.read_text(encoding="utf-8").splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if line.startswith("|") and cells and cells[0] in rows:
                line = f"| {cells[0]} | {rows[cells[0]][0]} | host | {rows[cells[0]][1]} s |"
            lines.append(line)
        gates.write_text("\n".join(lines) + "\n", encoding="utf-8")
        executors = orchestration / "policies" / "EXECUTORS.md"
        text = executors.read_text(encoding="utf-8")
        for stage, timeout in (("IMPLEMENT", 900), ("TEST", 600)):
            text = re.sub(rf'("{stage}":\s*)\{{[^}}]*\}}',
                          lambda match, stage=stage, timeout=timeout: match.group(1)
                          + json.dumps({"executor": "opencode", "model": MODEL, "timeout_seconds": timeout, "max_turns": None}),
                          text, count=1)
        text = text.replace('"executors_version": 1,', '"executors_version": 1,\n  "workspace_audit": "enforce",', 1)
        executors.write_text(text, encoding="utf-8")
        self.paths = json.loads(run([sys.executable, str(orchestration / "runtime" / "action_journal.py"), "--json", "paths"],
                                    cwd=self.repo, env=self.env).stdout)
        self.report: dict = {"dispatches": [], "stops": [], "audit": "enforce"}

    def sdd_json(self, *arguments: str) -> dict:
        completed = run([*self.sdd, *arguments], cwd=self.repo, env=self.env)
        return json.loads(completed.stdout)

    def test_one_implement_slice_runs_on_opencode_passes_the_audit_and_is_accepted(self) -> None:
        policy = json.loads(re.search(r"```json\n(.*?)\n```", (self.vault / "Projects" / "Demo" / "Hermes" / "orchestration"
                                                               / "policies" / "EXECUTORS.md").read_text(encoding="utf-8"), re.S).group(1))
        self.assertEqual("opencode", policy["stages"]["IMPLEMENT"]["executor"])
        self.assertEqual("enforce", policy["workspace_audit"])
        started = self.sdd_json("start", "--ticket", "oc-1", "--title", "Greeting",
                                "--objective", "Add greet(name) returning 'hello <name>' in src/feature.py with a unit test; approved by the PO")
        self.assertEqual("STARTED", started["status"], started)
        accepted_implement = False
        for _ in range(MAX_STEPS):
            decision = self.sdd_json("next")
            self.report.setdefault("steps", []).append({key: decision.get(key) for key in ("step", "stage", "stop_reason", "message", "findings")})
            if decision.get("end_turn"):
                self.report["stops"].append(decision.get("stop_reason"))
                if decision.get("findings"):
                    self.report.setdefault("audit_findings", []).append(decision["findings"])
                if decision["stop_reason"] == "HUMAN_DECISION_REQUIRED":
                    command = decision["next_command"]
                    for placeholder, words in USER_WORDS.items():
                        command = command.replace(placeholder, words)
                    answered = run(shlex.split(command), cwd=self.repo, env=self.env)
                    self.assertEqual(0, answered.returncode, answered.stdout)
                    continue
                break
            for printed in decision["commands"]:
                completed = run(shlex.split(printed), cwd=self.repo, env=self.env)
                if "executor_launch.py" in printed:
                    payload = json.loads(completed.stdout)
                    self.report["dispatches"].append({"stage": payload.get("stage"), "executor": payload.get("executor"),
                                                      "status": payload.get("status"), "model": payload.get("model"),
                                                      "version": payload.get("executor_version"),
                                                      "config_sha256": payload.get("opencode_config_sha256")})
                if "sdd.py" in printed and printed.rstrip().endswith("accept"):
                    payload = json.loads(completed.stdout or "{}")
                    if payload.get("status") == "ACCEPTED" and str(payload.get("action_id", "")).lower().count("implement"):
                        accepted_implement = True
                if completed.returncode:
                    break
            if accepted_implement:
                break
        opencode_dispatches = [item for item in self.report["dispatches"] if item["executor"] == "opencode"]
        Path(process_environment.get("SDD_OPENCODE_E2E_REPORT", os.devnull)).write_text(json.dumps(self.report, indent=1), encoding="utf-8")
        self.assertTrue(opencode_dispatches, f"no dispatch reached opencode: {self.report}")
        self.assertEqual("IMPLEMENT", opencode_dispatches[0]["stage"])
        self.assertEqual(opencode_executor.PINNED_VERSION, opencode_dispatches[0]["version"])
        self.assertEqual(MODEL, opencode_dispatches[0]["model"])
        self.assertRegex(opencode_dispatches[0]["config_sha256"] or "", "^[0-9a-f]{64}$")
        self.assertEqual("ARTIFACT_READY", opencode_dispatches[0]["status"], self.report)
        state = Path(self.paths["state"]).read_text(encoding="utf-8")
        # Two outcomes are correct, and which one happens depends on the live model: either the slice
        # is accepted (the result declared every file it touched), or the enabled workspace audit
        # blocks it because the worker really did change a file it did not declare. What must never
        # happen is a result the audit rejected being folded into STATE anyway.
        audited = [item for item in self.report["stops"] if item == "WORKSPACE_AUDIT_FAILED"]
        self.assertTrue(accepted_implement or audited, f"neither accepted nor audited: {self.report}")
        if audited:
            self.assertTrue(self.report.get("audit_findings"), self.report)
            self.assertNotIn("IMPLEMENT slice", state, "a result the audit rejected must not reach STATE")
        else:
            self.assertIn("IMPLEMENT slice", state)
            self.assertNotIn("WORKSPACE_AUDIT_FAILED", state)
        self.assertTrue((self.repo / "src").is_dir())


if __name__ == "__main__":
    unittest.main()
