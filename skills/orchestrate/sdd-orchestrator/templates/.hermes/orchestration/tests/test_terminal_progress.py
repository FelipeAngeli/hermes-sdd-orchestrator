"""Terminal progress dashboard for repository-local SDD runs."""
from __future__ import annotations

import importlib.util
import tempfile
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest import mock

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
SCRIPT = RUNTIME / "terminal_progress.py"


def load_progress() -> Any:
    spec = importlib.util.spec_from_file_location("sdd_terminal_progress", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("terminal progress module is not importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


class TerminalProgressTests(unittest.TestCase):
    def test_dashboard_tracks_provider_stage_remaining_time_activity_and_jev(self) -> None:
        progress = load_progress()
        value = progress.new_progress("openai-codex", "PLAN", instant("2026-10-05T12:00:00Z"))
        value = progress.record_activity(value, "Validando caminhos e símbolos", instant("2026-10-05T12:00:05Z"))
        value = progress.start_jev(
            value,
            provider="jev-ai",
            area="classificação de risco e roteamento",
            questions=["risk", "specialist"],
            at=instant("2026-10-05T12:00:10Z"),
        )
        value = progress.finish_jev(value, "DECIDED", instant("2026-10-05T12:00:12Z"))
        value = progress.enter_stage(value, "TASKS", instant("2026-10-05T12:00:20Z"))

        dashboard = progress.render(value, now=instant("2026-10-05T12:00:25Z"), color=False)

        self.assertIn("Provider       openai-codex", dashboard)
        self.assertIn("Fase atual     TASKS (4/8)", dashboard)
        self.assertIn("Fases restantes 4", dashboard)
        self.assertIn("PLAN", dashboard)
        self.assertIn("20s", dashboard)
        self.assertIn("TASKS", dashboard)
        self.assertIn("5s", dashboard)
        self.assertIn("Jev            concluído · jev-ai · 2s", dashboard)
        self.assertIn("Atuação         classificação de risco e roteamento", dashboard)
        self.assertIn("risk, specialist", dashboard)
        self.assertIn("Validando caminhos e símbolos", dashboard)

    def test_only_clarify_may_be_skipped_between_visible_stages(self) -> None:
        progress = load_progress()
        specify = progress.new_progress("openai-codex", "SPECIFY", instant("2026-10-05T12:00:00Z"))

        plan = progress.enter_stage(specify, "PLAN", instant("2026-10-05T12:00:10Z"))

        self.assertEqual(
            [("SPECIFY", "COMPLETED"), ("CLARIFY", "SKIPPED")],
            [(entry["name"], entry["status"]) for entry in plan["stage"]["history"]],
        )
        with self.assertRaisesRegex(progress.ProgressError, "PROGRESS_TRANSITION_INVALID"):
            progress.enter_stage(plan, "TEST", instant("2026-10-05T12:00:20Z"))

    def test_blocked_run_does_not_mislabel_the_active_stage_as_skipped(self) -> None:
        progress = load_progress()
        value = progress.new_progress("openai-codex", "IMPLEMENT", instant("2026-10-05T12:00:00Z"))

        blocked = progress.finish_run(value, "BLOCKED", instant("2026-10-05T12:00:09Z"))

        self.assertEqual("BLOCKED", blocked["stage"]["history"][-1]["status"])
        dashboard = progress.render(blocked, now=instant("2026-10-05T12:00:09Z"), color=False)
        self.assertIn("IMPLEMENT", dashboard)
        self.assertIn("9s · blocked", dashboard)

    def test_done_run_finishes_at_done_with_no_remaining_stage(self) -> None:
        progress = load_progress()
        value = progress.new_progress("openai-codex", "REVIEW", instant("2026-10-05T12:00:00Z"))

        done = progress.finish_run(value, "DONE", instant("2026-10-05T12:00:09Z"))

        self.assertEqual("DONE", done["stage"]["current"])
        self.assertEqual(
            [("REVIEW", "COMPLETED", 9), ("DONE", "COMPLETED", 0)],
            [(item["name"], item["status"], item["elapsed_seconds"]) for item in done["stage"]["history"]],
        )
        dashboard = progress.render(done, now=instant("2026-10-05T12:00:09Z"), color=False)
        self.assertIn("Fase atual     DONE (8/8)", dashboard)
        self.assertIn("Fases restantes 0", dashboard)

    def test_windows_without_fchmod_can_persist_progress(self) -> None:
        progress = load_progress()
        value = progress.new_progress("openai-codex", "SPECIFY", instant("2026-10-05T12:00:00Z"))
        with tempfile.TemporaryDirectory(prefix="sdd-progress-no-fchmod-") as temp:
            path = Path(temp) / "progress.json"
            with mock.patch.object(progress.os, "fchmod", None):
                progress.save_progress(path, value)

            persisted = progress.load_progress(path)

        self.assertEqual(value, persisted)


if __name__ == "__main__":
    unittest.main()
