"""Golden command lines: the Claude and Codex argv stay byte-for-byte what they were.

The literals below were captured from the launcher before the per-executor dispatch
refactor and before OpenCode existed. Any change to a Claude or Codex command line,
help probe or model probe must be a deliberate edit of this file.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))

import executor_launch as launch  # noqa: E402
import executor_sandbox  # noqa: E402

SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}}
SCHEMA_TEXT = '{"type":"object","properties":{"a":{"type":"string"}}}'
REPO, FINAL = Path("/work/repo"), Path("/vault/rt/T-1.result.json")
CLAUDE_HEAD = ["/usr/bin/claude", "-p", "--output-format", "json", "--json-schema", SCHEMA_TEXT, "--no-session-persistence",
               "--setting-sources", "user", "--strict-mcp-config", "--disable-slash-commands", "--tools"]
CODEX_HEAD = ["/usr/bin/codex", "exec", "--ephemeral", "--color", "never", "--cd", "/work/repo", "--sandbox"]
CODEX_SIDE = ["--output-schema", "/vault/rt/T-1.result.json.transport-schema.json",
              "--output-last-message", "/vault/rt/T-1.result.json.last-message.tmp"]
GOLDEN = [
    ("PLAN", "claude", {}, "opus-x", [], ["/vault/ctl"],
     [*CLAUDE_HEAD, "Read,Grep,Glob", "--max-turns", "60", "--model", "opus-x", "--add-dir", "/vault/ctl"]),
    ("REVIEW", "claude", {"tools": "Read,Grep", "permission_mode": "plan"}, None, [], ["/vault/ctl"],
     [*CLAUDE_HEAD, "Read,Grep", "--max-turns", "40", "--permission-mode", "plan", "--add-dir", "/vault/ctl"]),
    ("IMPLEMENT", "codex", {}, "gpt-x", ["/work/repo/sub"], [],
     [*CODEX_HEAD, "workspace-write", *CODEX_SIDE, "--model", "gpt-x", "--config", "sandbox_workspace_write.writable_roots=[]",
      "--add-dir", "/work/repo/sub", "-"]),
    ("TASKS", "codex", {}, None, [], [], [*CODEX_HEAD, "read-only", *CODEX_SIDE, "-"]),
    ("IMPLEMENT", "codex", {"sandbox_network": True}, None, ["/work/repo/sub"], [],
     [*CODEX_HEAD, "workspace-write", *CODEX_SIDE, "--config", "sandbox_workspace_write.writable_roots=[]",
      "--config", "sandbox_workspace_write.network_access=true", "--add-dir", "/work/repo/sub", "-"]),
]


class GoldenArgvTests(unittest.TestCase):
    def test_claude_and_codex_dispatch_argv_are_unchanged(self) -> None:
        policy = launch.load_policy(Path("/nonexistent/EXECUTORS.md"))
        for stage, executor, extra, model, add_dirs, read_dirs, expected in GOLDEN:
            with self.subTest(stage=stage, executor=executor, extra=extra):
                settings = launch.stage_settings(policy, stage, executor=executor, model=model, timeout=None)
                settings.update(extra)
                argv = launch.build_argv(stage=stage, settings=settings, schema=SCHEMA, repository=REPO, final=FINAL,
                                         add_dirs=add_dirs, binary=f"/usr/bin/{executor}", read_dirs=read_dirs)
                self.assertEqual(expected, argv)
                self.assertEqual(json.dumps(expected), json.dumps(argv))

    def test_claude_and_codex_help_and_required_flags_are_unchanged(self) -> None:
        calls: list[list[str]] = []

        def quiet(argv, timeout, cwd=None, env=None):
            calls.append(list(argv))
            text = " ".join(flag for flags in launch.REQUIRED_HELP_FLAGS.values() for flag in flags)
            return mock.Mock(returncode=0, stdout="9.9.9\n" + text, stderr="")

        with mock.patch.object(launch, "_run_quiet", quiet), mock.patch.object(launch.shutil, "which", lambda name: f"/usr/bin/{name}"):
            for executor in ("claude", "codex"):
                self.assertEqual("READY", launch.preflight(executor, None, probe=False)["status"])
        self.assertEqual([["/usr/bin/claude", "--version"], ["/usr/bin/claude", "--help"],
                          ["/usr/bin/codex", "--version"], ["/usr/bin/codex", "exec", "--help"]], calls)
        self.assertEqual(("--print", "--output-format", "--json-schema", "--no-session-persistence", "--model", "--tools", "--add-dir",
                          "--setting-sources", "--strict-mcp-config", "--disable-slash-commands"), launch.REQUIRED_HELP_FLAGS["claude"])
        self.assertEqual(("--output-schema", "--output-last-message", "--ephemeral", "--model", "--sandbox", "--cd", "--add-dir", "--config"),
                         launch.REQUIRED_HELP_FLAGS["codex"])

    def test_claude_and_codex_model_probe_argv_are_unchanged(self) -> None:
        seen: list[list[str]] = []

        def quiet(argv, timeout, cwd=None, env=None):
            seen.append(list(argv))
            return mock.Mock(returncode=0, stdout='{"is_error": false}', stderr="")

        for executor in ("claude", "codex"):
            executor_sandbox.model_probe(executor=executor, binary=f"/usr/bin/{executor}", model="m-1", isolation_args=launch.CLAUDE_ISOLATION_ARGS,
                                         probe_timeout=5, tail_characters=10, worker_env={}, run_quiet=quiet)
        claude, codex = seen
        self.assertEqual(["/usr/bin/claude", "-p", "--output-format", "json", "--no-session-persistence", "--setting-sources", "user",
                          "--strict-mcp-config", "--disable-slash-commands", "--max-turns", "1", "--tools", "", "--model", "m-1",
                          "Reply with the single word OK."], claude)
        scratch = codex[codex.index("-C") + 1]
        self.assertEqual(["/usr/bin/codex", "exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only", "--color", "never",
                          "-C", scratch, "--model", "m-1", "-o", str(Path(scratch) / "probe.txt"), "Reply with the single word OK."], codex)


if __name__ == "__main__":
    unittest.main()
