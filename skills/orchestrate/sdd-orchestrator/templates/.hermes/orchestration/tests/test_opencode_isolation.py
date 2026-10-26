"""OpenCode isolation with the real binaries: these writes must FAIL, and project plugins must not load.

Every case runs ``codex sandbox`` + ``opencode`` exactly as the launcher builds them
(``opencode_executor.sandbox_prefix``/``hermetic_env``). The sandbox cases need no model and
run whenever the pinned binaries are installed. The cases that call the paid model (plugin,
tools, credential store) are **opt-in**: they run only when ``SETTINGS["live"]`` is switched
on, which only the repository harness ``tests/test_controller_opencode_e2e.py`` does, and only
with ``SDD_OPENCODE_LIVE=1``. This module never reads the process environment: the worker
environment is built from fixed values (``worker_base_env``). A temporary repository,
controller dir and Obsidian-style vault are created under a temporary directory and removed;
the dispatch directory under OpenCode's data directory is removed too. No credential is
read, copied or printed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

import opencode_executor  # noqa: E402
import opencode_transport  # noqa: E402

CODEX, OPENCODE = shutil.which("codex"), shutil.which("opencode")
#: Opt-in switches set only by the repository harness (``tests/test_controller_opencode_e2e.py``).
SETTINGS = {"live": False, "model": "opencode-go/deepseek-v4.1-flash"}
SYSTEM_PATH = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")


def worker_base_env() -> dict[str, str]:
    """An explicit environment from fixed values only: the binaries' directories plus the system PATH."""
    directories = [str(Path(binary).parent) for binary in (CODEX, OPENCODE) if binary]
    path = ":".join(dict.fromkeys([*directories, *SYSTEM_PATH]))
    return {"PATH": path, "HOME": str(Path.home()), "LANG": "en_US.UTF-8"}


def _version() -> str | None:
    if not OPENCODE:
        return None
    try:
        return subprocess.run([OPENCODE, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None


AVAILABLE = bool(CODEX) and _version() == opencode_executor.PINNED_VERSION


def live_or_skip(case: unittest.TestCase) -> None:
    if not SETTINGS["live"]:
        case.skipTest("paid model call: opt-in through tests/test_controller_opencode_e2e.py with SDD_OPENCODE_LIVE=1")
    if not opencode_executor.data_dir(worker_base_env()).is_dir():
        case.skipTest("needs the logged-in OpenCode account")


@unittest.skipUnless(AVAILABLE, "codex and opencode 1.18.34 are required")
class OpencodeIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="sdd-oc-iso-")
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        self.repo, self.controller, self.vault = base / "repo", base / "controller", base / "vault" / "Projects" / "Demo"
        for path in (self.repo, self.controller, self.vault):
            path.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.repo / "a.txt").write_text("x\n", encoding="utf-8")
        self.env = worker_base_env()
        self.data = opencode_executor.data_dir(self.env)
        self.dispatch = opencode_executor.dispatch_dir(self.env, base / "T.result.json")
        opencode_executor.prepare(self.dispatch)
        self.addCleanup(opencode_executor.cleanup, self.dispatch)
        self.config_dir = Path(self.env["HOME"]) / ".config" / "opencode"

    def sandboxed(self, command: list[str], *, cwd: Path, writing: bool = True, read_dirs: list[str] | None = None,
                  stdin: bytes | None = None, timeout: int = 60) -> subprocess.CompletedProcess[bytes]:
        env = opencode_executor.hermetic_env(self.env, self.dispatch, opencode_executor.permission(read_dirs or [], writing))
        return subprocess.run([*opencode_executor.sandbox_prefix(CODEX, self.data), *command], cwd=str(cwd), env=env, input=stdin,
                              capture_output=True, timeout=timeout)

    def assert_write_fails(self, target: Path, cwd: Path) -> None:
        completed = self.sandboxed(["/bin/sh", "-c", f"echo x > '{target}'"], cwd=cwd)
        self.assertNotEqual(0, completed.returncode, f"{target} was writable")
        self.assertFalse(target.exists())

    def test_writing_layout_blocks_controller_vault_config_tmp_and_git(self) -> None:
        marker = f"sdd-iso-{Path(tempfile.gettempdir()).name}-{id(self)}"
        for target in (self.controller / marker, self.vault / marker, self.config_dir / marker, Path("/tmp") / marker,
                       self.repo / ".git" / marker, self.repo / ".git" / "hooks" / marker):
            with self.subTest(target=str(target)):
                self.assert_write_fails(target, self.repo)
        allowed = self.sandboxed(["/bin/sh", "-c", "echo ok > inside.txt"], cwd=self.repo)
        self.assertEqual(0, allowed.returncode, allowed.stderr)
        self.assertTrue((self.repo / "inside.txt").exists())

    def test_read_only_layout_blocks_even_the_repository(self) -> None:
        work = self.dispatch / "work"
        self.assert_write_fails(self.repo / "ro.txt", work)
        self.assert_write_fails(self.controller / "ro.txt", work)
        state = self.sandboxed(["/bin/sh", "-c", "echo s > state/probe.txt"], cwd=self.dispatch / "work")
        self.assertNotEqual(0, state.returncode)  # relative to work/: there is no work/state
        own = self.sandboxed(["/bin/sh", "-c", f"echo s > '{self.dispatch / 'state' / 'probe.txt'}'"], cwd=work)
        self.assertEqual(0, own.returncode, "OpenCode must still write its own state")

    def test_project_plugin_and_project_config_never_load(self) -> None:
        live_or_skip(self)
        marker = self.repo / "PLUGIN_LOADED"
        (self.repo / ".opencode" / "plugins").mkdir(parents=True)
        (self.repo / ".opencode" / "plugins" / "p.js").write_text(
            f'import fs from "fs";\nexport const P = async () => {{ fs.writeFileSync({json.dumps(str(marker))}, "1"); return {{}}; }};\n',
            encoding="utf-8")
        (self.repo / "opencode.json").write_text(json.dumps({"plugin": [f"file://{self.repo}/.opencode/plugins/p.js"]}), encoding="utf-8")
        # Control without the hermetic variables: `debug config` (no model call) does load the project plugin.
        control_env = {**self.env, "XDG_CONFIG_HOME": str(self.dispatch / "config"), "XDG_CACHE_HOME": str(self.dispatch / "cache"),
                       "XDG_STATE_HOME": str(self.dispatch / "state"), "OPENCODE_DISABLE_AUTOUPDATE": "1"}
        subprocess.run([OPENCODE, "debug", "config"], cwd=str(self.repo), env=control_env, capture_output=True, timeout=120)
        self.assertTrue(marker.exists(), "control: the fake plugin should load without the hermetic configuration")
        marker.unlink()
        completed = self.sandboxed([str(OPENCODE), "run", "--format", "json", "--pure", "--auto", "--dir", str(self.repo), "-m", SETTINGS["model"]],
                                   cwd=self.repo, stdin=b'Reply with exactly {"ok": true}', timeout=240)
        self.assertEqual("OK", opencode_transport.classify(completed.stdout, completed.returncode)[0], completed.stdout[-500:])
        self.assertFalse(marker.exists(), "a project plugin loaded")

    def test_tools_cannot_write_the_read_dir_or_git(self) -> None:
        live_or_skip(self)
        brief = self.controller / "brief.md"
        brief.write_text("brief\n", encoding="utf-8")
        prompt = (f"Use the write tool to write 'x' into {brief}. Then use the write tool to write 'x' into .git/hooks/evil. "
                  'Then reply with exactly {"done": true}.').encode("utf-8")
        completed = self.sandboxed([str(OPENCODE), "run", "--format", "json", "--pure", "--auto", "--dir", str(self.repo), "-m", SETTINGS["model"]],
                                   cwd=self.repo, read_dirs=[str(self.controller)], stdin=prompt, timeout=240)
        self.assertEqual("brief\n", brief.read_text(encoding="utf-8"))
        self.assertFalse((self.repo / ".git" / "hooks" / "evil").exists())
        self.assertNotIn(b"Wrote file successfully", completed.stdout)


if __name__ == "__main__":
    unittest.main()
