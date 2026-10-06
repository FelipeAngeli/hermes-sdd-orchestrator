"""Terminal progress dashboard for repository-local SDD runs."""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
    def test_all_rendered_text_rejects_non_printable_unicode_controls(self) -> None:
        progress = load_progress()
        unsafe_values = ("csi\x9b31m", "osc\x9dtitle", "line\u2028break", "bidi\u202eforged")
        for unsafe in unsafe_values:
            with self.subTest(unsafe=repr(unsafe)):
                with self.assertRaisesRegex(progress.ProgressError, "PROGRESS_INPUT_INVALID"):
                    progress.new_progress(unsafe, "PLAN")
                value = progress.new_progress("openai-codex", "PLAN")
                with self.assertRaisesRegex(progress.ProgressError, "PROGRESS_INPUT_INVALID"):
                    progress.record_activity(value, unsafe)
                with self.assertRaisesRegex(progress.ProgressError, "PROGRESS_INPUT_INVALID"):
                    progress.start_jev(
                        value,
                        provider=unsafe,
                        area="classification",
                        questions=["risk"],
                    )
                active = progress.start_jev(
                    value,
                    provider="typesafe",
                    area="classification",
                    questions=["risk"],
                )
                with self.assertRaisesRegex(progress.ProgressError, "PROGRESS_INPUT_INVALID"):
                    progress.finish_jev(active, "DECIDED", model=unsafe)

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

    @unittest.skipUnless(os.name == "posix", "descriptor-anchored paths require POSIX")
    def test_progress_refuses_symlinked_ancestors_without_writing_outside(self) -> None:
        progress = load_progress()
        with tempfile.TemporaryDirectory(prefix="sdd-progress-path-") as temp:
            root = Path(temp).resolve()
            outside = root / "outside"
            outside.mkdir()
            linked = root / "linked"
            linked.symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(progress.ProgressError, "PROGRESS_FILE_INVALID"):
                progress.save_progress(
                    linked / "progress.json",
                    progress.new_progress("openai-codex", "SPECIFY"),
                )

            self.assertFalse((outside / "progress.json").exists())

    @unittest.skipUnless(os.name == "posix", "descriptor locks require POSIX")
    def test_atomic_updates_serialize_read_modify_write_without_lost_activity(self) -> None:
        progress = load_progress()
        with tempfile.TemporaryDirectory(prefix="sdd-progress-lock-") as temp:
            path = Path(temp).resolve() / "progress.json"
            progress.save_progress(path, progress.new_progress("openai-codex", "SPECIFY"))
            first_inside = threading.Event()
            release_first = threading.Event()
            errors: list[BaseException] = []

            def first(value: dict[str, Any]) -> dict[str, Any]:
                first_inside.set()
                release_first.wait(timeout=5)
                return progress.record_activity(value, "first")

            def invoke(transform: Any) -> None:
                try:
                    progress.update_progress(path, transform)
                except BaseException as error:
                    errors.append(error)

            first_thread = threading.Thread(target=invoke, args=(first,))
            second_thread = threading.Thread(
                target=invoke,
                args=(lambda value: progress.record_activity(value, "second"),),
            )
            first_thread.start()
            self.assertTrue(first_inside.wait(timeout=5))
            second_thread.start()
            release_first.set()
            first_thread.join(timeout=5)
            second_thread.join(timeout=5)
            saved = progress.load_progress(path)

        self.assertFalse(errors, [repr(error) for error in errors])
        self.assertFalse(first_thread.is_alive())
        self.assertFalse(second_thread.is_alive())
        self.assertEqual(["first", "second"], [item["message"] for item in saved["activities"]])


if __name__ == "__main__":
    unittest.main()
