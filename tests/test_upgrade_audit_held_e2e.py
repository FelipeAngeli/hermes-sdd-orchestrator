"""End to end: a 15.19.1 controller holding an audit-stopped IMPLEMENT action is upgraded and re-audited.

The 15.19.1 skill is extracted from the ``v15.19.1`` tag and installed with its own installer
into a scratch repository and a temporary vault. ``workspace_audit: enforce`` is on and the
project's ``.gitignore`` ignores ``.mypy_cache/`` and ``.ruff_cache/``, which already exist
before the dispatch. The fake IMPLEMENT worker writes its declared files and rewrites both
caches, as running ``mypy``/``ruff`` does. Driving only the printed commands, the 15.19.1
runtime stops the action with ``WORKSPACE_AUDIT_FAILED`` (journal ``ARTIFACT_READY``). This
branch's installer must then accept ``--upgrade`` (dry run and ``--apply``) without touching
STATE, the journal or the baseline, and ``sdd.py next`` must re-audit the same action with
the new runtime: accepted when the caches were the only findings, still stopped when the
worker also left a real undeclared file. No network, no paid call.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from os import environ as process_environment
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_controller_e2e as e2e  # noqa: E402

sys.path.insert(0, str(e2e.SKILL_ROOT / "templates" / ".hermes" / "orchestration" / "runtime"))
import state_format as sf  # noqa: E402

TAG = "v15.19.1"
IGNORE = ".hermes/\n__pycache__/\n*.pyc\n.mypy_cache/\n.ruff_cache/\n"
#: Rewrites the gate caches in the worker's repository, then runs the shared fake executor.
CACHE_WRITER = """
from pathlib import Path as _P
import os as _os
_repo = _P.cwd()
for _rel, _text in (('.mypy_cache/3.12/cache.0.db', 'sqlite 2'), ('.ruff_cache/0.17.2/77', 'bincode 2')):
    (_repo / _rel).parent.mkdir(parents=True, exist_ok=True)
    (_repo / _rel).write_text(_text)
if (_P(__file__).resolve().parent / 'undeclared').exists():
    (_repo / 'docs').mkdir(exist_ok=True)
    (_repo / 'docs' / 'undeclared.md').write_text('# not declared\\n')
"""


def run(argv: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=300, check=False)


class AuditHeldUpgradeEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="sdd-held-e2e-")
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        archive = subprocess.run(["git", "-C", str(e2e.ROOT), "archive", TAG, "skills/orchestrate/sdd-orchestrator"],
                                 capture_output=True, check=False, timeout=120)
        if archive.returncode:
            self.skipTest(f"tag {TAG} is not available: {archive.stderr.decode(errors='replace')}")
        (base / "old").mkdir()
        subprocess.run(["tar", "-x", "-C", str(base / "old")], input=archive.stdout, check=True, timeout=120)
        old_installer = base / "old" / "skills/orchestrate/sdd-orchestrator/scripts/install_project.py"
        self.repo, self.vault, self.fake = base / "repo", base / "vault", base / "fake"
        for path in (self.repo, self.vault / "Projects" / "Demo", self.vault / ".obsidian", self.fake):
            path.mkdir(parents=True)
        self.env = {key: value for key, value in process_environment.items() if not key.startswith(("HERMES_", "GIT_"))}
        self.env.update(PATH=f"{self.fake}{os.pathsep}{process_environment.get('PATH', '')}", FAKE_EXECUTOR_DIR=str(self.fake),
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")
        for name in ("claude", "codex"):
            script = self.fake / name
            script.write_text(f"#!{sys.executable}\n{CACHE_WRITER if name == 'codex' else ''}{e2e.FAKE_EXECUTOR}", encoding="utf-8")
            script.chmod(0o755)
        for argv in (["git", "init", "-q", "-b", "main"], ["git", "config", "user.email", "t@e"], ["git", "config", "user.name", "t"]):
            run(argv, cwd=self.repo, env=self.env)
        (self.repo / "AGENTS.md").write_text("# Demo\nUse TDD.\n", encoding="utf-8")
        (self.repo / ".gitignore").write_text(IGNORE, encoding="utf-8")
        run(["git", "add", "."], cwd=self.repo, env=self.env)
        run(["git", "commit", "-qm", "init"], cwd=self.repo, env=self.env)
        for relative, text in ((".mypy_cache/3.12/cache.0.db", "sqlite 1"), (".ruff_cache/0.17.2/77", "bincode 1")):
            (self.repo / relative).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / relative).write_text(text, encoding="utf-8")
        self.storage = ["--obsidian-vault", str(self.vault), "--obsidian-project", "Projects/Demo"]
        installed = run([sys.executable, str(old_installer), "--target", str(self.repo), *self.storage, "--apply", "--json"],
                        cwd=self.repo, env=self.env)
        self.assertEqual("APPLIED", json.loads(installed.stdout)["status"], installed.stdout + installed.stderr)
        orchestration = self.vault / "Projects" / "Demo" / "Hermes" / "orchestration"
        self.assertIn("15.19.1", (orchestration / "INSTALL_MANIFEST.json").read_text(encoding="utf-8"))
        self.sdd = [sys.executable, str(orchestration / "runtime" / "sdd.py")]
        python = shlex.quote(sys.executable)
        gates = orchestration / "policies" / "GATES.md"
        rows = {"Focused tests": f"`{python} -m unittest discover -s tests`", "Format": f"`{python} -m py_compile {{files}}`",
                "Analyze": f"`{python} -m compileall -q src`"}
        lines = []
        for line in gates.read_text(encoding="utf-8").splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if line.startswith("|") and cells and cells[0] in rows:
                line = f"| {cells[0]} | {rows[cells[0]]} | host | 120 s |"
            lines.append(line)
        gates.write_text("\n".join(lines) + "\n", encoding="utf-8")
        executors = orchestration / "policies" / "EXECUTORS.md"
        executors.write_text(executors.read_text(encoding="utf-8").replace(
            '"executors_version": 1,', '"executors_version": 1,\n  "workspace_audit": "enforce",', 1), encoding="utf-8")
        self.paths = json.loads(run([sys.executable, str(orchestration / "runtime" / "action_journal.py"), "--json", "paths"],
                                    cwd=self.repo, env=self.env).stdout)

    def call(self, *arguments: str) -> dict:
        completed = run([*self.sdd, *arguments], cwd=self.repo, env=self.env)
        return json.loads(completed.stdout)

    def craft_implement(self) -> None:
        started = self.call("start", "--ticket", "c-1", "--title", "t", "--objective", "o")
        self.assertEqual("STARTED", started["status"], started)
        head = run(["git", "rev-parse", "HEAD"], cwd=self.repo, env=self.env).stdout.strip()
        artifact = self.fake / "artifact.json"
        artifact.write_text("{}", encoding="utf-8")
        import hashlib

        accepted = {"action_id": "c-1-x-01", "artifact": str(artifact), "artifact_sha256": hashlib.sha256(b"{}").hexdigest()}
        state_path = Path(self.paths["state"])
        text = state_path.read_text(encoding="utf-8")
        data = sf.parse(text)
        path = sf.profile_path("CODE")
        data["stage"] = {"current": "IMPLEMENT", "status": "RUNNING", "completed": list(path[:path.index("IMPLEMENT")]),
                         "skipped": [{"stage": "CLARIFY", "reason": "none"}]}
        delivery = data["delivery"]
        delivery["acceptance"] = {"AC-1": {"criterion": "greet works", "verification_method": "focused unit test", "verifier": "AGENT", "slice_id": "S1"}}
        delivery["slices"] = {"planned": ["S1"], "completed": [], "editable_paths": ["src/feature.py", "tests/__init__.py", "tests/test_feature.py"]}
        delivery["accepted"] = {name: dict(accepted) for name in ("specify", "plan", "tasks")}
        delivery["project_context"] = {"status": "CURRENT", "checked_head": head, "evidence": "e", "gaps": []}
        data["loop"]["budgets"]["stage_transitions"]["used"] = path.index("IMPLEMENT")
        state_path.write_text(sf.dump(text, data, log="test fixture"), encoding="utf-8")

    def drive_until_stop_or_validate(self) -> dict:
        for _ in range(12):
            decision = self.call("next")
            if decision.get("end_turn") or decision.get("step") == "VALIDATE":
                return decision
            for printed in decision["commands"]:
                completed = run(shlex.split(printed), cwd=self.repo, env=self.env)
                self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.fail("the controller neither stopped nor reached VALIDATE")

    def held_by_15_19_1(self) -> tuple[dict, dict[str, bytes]]:
        self.craft_implement()
        stopped = self.drive_until_stop_or_validate()
        self.assertEqual("WORKSPACE_AUDIT_FAILED", stopped.get("stop_reason"), stopped)
        self.assertIn(".mypy_cache/3.12/cache.0.db", stopped["undeclared"])
        self.assertIn(".ruff_cache/0.17.2/77", stopped["undeclared"])
        journal = json.loads(Path(self.paths["journal"]).read_text(encoding="utf-8"))
        self.assertEqual("ARTIFACT_READY", journal["action"]["status"])
        runtime = Path(self.paths["journal"]).parent
        kept = {path.relative_to(runtime).as_posix(): path.read_bytes() for path in sorted(runtime.rglob("*"))
                if path.is_file() and (path.name in {"STATE.md", "ACTION_JOURNAL.json"} or path.name.endswith(".workspace-before.json"))}
        self.assertTrue(any(name.endswith(".workspace-before.json") for name in kept))
        return stopped, kept

    def upgrade(self) -> None:
        for extra in ((), ("--apply",)):
            completed = run([sys.executable, str(e2e.INSTALLER), "--target", str(self.repo), *self.storage, "--upgrade", "--json", *extra],
                            cwd=self.repo, env=self.env)
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            self.assertEqual("UPGRADED" if extra else "UPGRADE_READY", json.loads(completed.stdout)["status"])

    def assert_untouched(self, kept: dict[str, bytes]) -> None:
        runtime = Path(self.paths["journal"]).parent
        self.assertEqual(kept, {name: (runtime / name).read_bytes() for name in kept}, "the upgrade touched STATE, the journal or a baseline")

    def test_an_action_stopped_only_for_gate_caches_is_accepted_after_the_upgrade(self) -> None:
        stopped, kept = self.held_by_15_19_1()
        self.assertEqual([".mypy_cache/3.12/cache.0.db", ".ruff_cache/0.17.2/77"], sorted(stopped["undeclared"]))
        self.upgrade()
        self.assert_untouched(kept)
        decision = self.call("next")
        self.assertEqual("VALIDATE", decision.get("step"), decision)
        self.assertEqual(stopped["action_id"], decision["action_id"])
        for printed in decision["commands"]:
            completed = run(shlex.split(printed), cwd=self.repo, env=self.env)
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertEqual("ACCEPTED", json.loads(completed.stdout)["status"])
        self.assertIn("implement-s1", sf.parse(Path(self.paths["state"]).read_text(encoding="utf-8"))["delivery"]["accepted"])

    def test_an_action_with_a_real_undeclared_file_is_still_stopped_after_the_upgrade(self) -> None:
        (self.fake / "undeclared").write_text("leave one undeclared doc\n", encoding="utf-8")
        stopped, kept = self.held_by_15_19_1()
        self.assertIn("docs/undeclared.md", stopped["undeclared"])
        self.upgrade()
        self.assert_untouched(kept)
        decision = self.call("next")
        self.assertEqual("WORKSPACE_AUDIT_FAILED", decision.get("stop_reason"), decision)
        self.assertEqual(stopped["action_id"], decision["action_id"])
        self.assertEqual(["docs/undeclared.md"], decision["undeclared"])
        self.assertTrue(re.search(r"abandon", decision["next_command"]))


if __name__ == "__main__":
    unittest.main()
