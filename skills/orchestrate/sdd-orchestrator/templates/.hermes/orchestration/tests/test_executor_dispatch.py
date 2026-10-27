"""Per-executor dispatch table, the OPENCODE schema enums and the bounded-run plan label."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ORCHESTRATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ORCHESTRATION / "runtime"))

import bounded_run_planner  # noqa: E402
import executor_dispatch  # noqa: E402
import executor_launch as launch  # noqa: E402
import executor_transport  # noqa: E402


class DispatchTableTests(unittest.TestCase):
    def test_every_executor_has_each_dispatch_entry_and_nothing_falls_back(self) -> None:
        for table in (executor_dispatch.BUILDERS, executor_dispatch.HELP_ARGS, executor_dispatch.REQUIRED_HELP_FLAGS):
            self.assertEqual(set(executor_dispatch.EXECUTORS), set(table))
        self.assertEqual(set(executor_dispatch.EXECUTORS) - {"opencode"}, set(executor_transport.EXTRACTORS))
        with self.assertRaises(KeyError):
            executor_transport.extract_result("aider", b"{}", Path("/nonexistent"))
        with self.assertRaises(KeyError):
            executor_dispatch.help_argv("aider", "/bin/aider")

    def test_unknown_executor_is_policy_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            policy = Path(temp) / "EXECUTORS.md"
            policy.write_text('```json\n{"executors_version": 1, "stages": {"TEST": {"executor": "aider"}}}\n```\n', encoding="utf-8")
            with self.assertRaises(launch.LaunchError) as raised:
                launch.load_policy(policy)
        self.assertEqual("POLICY_INVALID", raised.exception.status)
        with self.assertRaises(launch.LaunchError) as raised:
            launch.stage_settings(launch.load_policy(Path("/nonexistent")), "TEST", executor="aider", model=None, timeout=None)
        self.assertEqual("POLICY_INVALID", raised.exception.status)

    def test_codex_only_stage_keys_stay_codex_only(self) -> None:
        for key, value in (("sandbox_network", True), ("codex_home", "/tmp"), ("env_from", ["X_VALUE"])):
            with self.subTest(key=key), self.assertRaises(launch.LaunchError) as raised:
                launch.stage_settings({"stages": {**launch.DEFAULT_STAGE_POLICY, "TEST": {**launch.DEFAULT_STAGE_POLICY["TEST"],
                                                                                         "executor": "claude", key: value}}},
                                      "TEST", executor=None, model=None, timeout=None)
            self.assertEqual("POLICY_INVALID", raised.exception.status)


class OpencodeEnumTests(unittest.TestCase):
    def test_every_schema_executor_enum_lists_opencode(self) -> None:
        found = 0
        for path in sorted((ORCHESTRATION / "schemas").glob("*.json")):
            def walk(node):
                nonlocal found
                if isinstance(node, dict):
                    enum = node.get("enum")
                    if isinstance(enum, list) and "CODEX" in enum and "CLAUDE" in enum:
                        found += 1
                        self.assertIn("OPENCODE", enum, path.name)
                    for value in node.values():
                        walk(value)
                elif isinstance(node, list):
                    for value in node:
                        walk(value)
            walk(json.loads(path.read_text(encoding="utf-8")))
        self.assertEqual(2, found)

    def test_planner_labels_follow_the_policy_and_keep_the_legacy_label(self) -> None:
        self.assertEqual("CODEX", bounded_run_planner.classify_action("IMPLEMENT_SLICE")["executor"])
        self.assertEqual("CODEX", bounded_run_planner.classify_action("PLAN")["executor"])
        self.assertEqual("HOST", bounded_run_planner.classify_action("CI")["executor"])
        policy = {"stages": {**launch.DEFAULT_STAGE_POLICY, "IMPLEMENT": {**launch.DEFAULT_STAGE_POLICY["IMPLEMENT"], "executor": "opencode",
                                                                           "model": "opencode-go/deepseek-v4.1-flash"}}}
        with mock.patch.object(launch, "load_policy", return_value=policy), \
                mock.patch.object(launch, "stage_settings", side_effect=lambda pol, stage, **_: dict(pol["stages"][stage])):
            self.assertEqual("OPENCODE", bounded_run_planner.classify_action("IMPLEMENT_SLICE")["executor"])
            self.assertEqual("CODEX", bounded_run_planner.classify_action("TASKS")["executor"])


if __name__ == "__main__":
    unittest.main()
