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

        self.assertIn("Hermes       openai-codex", dashboard)
        self.assertIn("Agora        Tarefas (TASKS) · fase 4 de 8 · há 5s", dashboard)
        self.assertIn("4 de 8 fases", dashboard)
        self.assertIn("PLAN", dashboard)
        self.assertIn("20s", dashboard)
        self.assertIn("TASKS", dashboard)
        self.assertIn("Jev          concluído · jev-ai · 2s", dashboard)
        self.assertIn("classificação de risco e roteamento", dashboard)
        self.assertIn("risk, specialist", dashboard)
        self.assertIn("Validando caminhos e símbolos", dashboard)

    def test_dashboard_names_the_executor_and_model_of_each_stage(self) -> None:
        progress = load_progress()
        value = progress.new_progress(
            "anthropic", "PLAN", instant("2026-10-05T12:00:00Z"), executor="claude", model="claude-opus-5-5",
        )
        value = progress.enter_stage(value, "TASKS", instant("2026-10-05T12:01:00Z"))
        value = progress.set_executor(value, "codex", "gpt-5.6-sol", instant("2026-10-05T12:01:01Z"))

        dashboard = progress.render(value, now=instant("2026-10-05T12:01:30Z"), color=False)

        self.assertIn("Executor     codex · gpt-5.6-sol", dashboard)
        self.assertIn("PLAN       1m 00s · concluída · claude · claude-opus-5-5", dashboard)
        self.assertEqual(
            {"executor": "claude", "model": "claude-opus-5-5"},
            {key: value["stage"]["history"][-1][key] for key in ("executor", "model")},
        )

    def test_unknown_executor_is_shown_as_not_informed_never_guessed(self) -> None:
        progress = load_progress()
        value = progress.new_progress("openai-codex", "SPECIFY", instant("2026-10-05T12:00:00Z"))

        dashboard = progress.render(value, now=instant("2026-10-05T12:00:01Z"), color=False)

        self.assertIn("Executor     não informado", dashboard)

    def test_schema_one_progress_files_still_load_and_render(self) -> None:
        progress = load_progress()
        legacy = progress.new_progress("openai-codex", "PLAN", instant("2026-10-05T12:00:00Z"))
        legacy = progress.enter_stage(legacy, "TASKS", instant("2026-10-05T12:00:10Z"))
        legacy["schema_version"] = 1
        legacy.pop("summary")
        legacy["jev"].pop("decisions")
        legacy["run"].pop("executor")
        legacy["run"].pop("model")
        legacy["run"].pop("reason")
        for entry in legacy["stage"]["history"]:
            entry.pop("executor")
            entry.pop("model")

        upgraded = progress.validate_progress(legacy)

        self.assertEqual(progress.SCHEMA_VERSION, upgraded["schema_version"])
        self.assertIsNone(upgraded["run"]["executor"])
        self.assertIn("Tarefas (TASKS)", progress.render(upgraded, now=instant("2026-10-05T12:00:11Z")))

    def test_a_stopped_run_explains_what_happened_why_and_what_to_do(self) -> None:
        progress = load_progress()
        value = progress.new_progress("openai-codex", "IMPLEMENT", instant("2026-10-05T12:00:00Z"))

        blocked = progress.finish_run(value, "BLOCKED", instant("2026-10-05T12:00:09Z"), reason="FOCUSED_TESTS_FAILED")
        dashboard = progress.render(blocked, now=instant("2026-10-05T12:00:09Z"), color=False)

        self.assertIn("Bloqueado em Implementar (IMPLEMENT)", dashboard)
        self.assertIn("O que houve", dashboard)
        self.assertIn("Por quê", dashboard)
        self.assertIn("O que fazer", dashboard)
        self.assertIn("código técnico: FOCUSED_TESTS_FAILED", dashboard)
        with self.assertRaisesRegex(progress.ProgressError, "PROGRESS_REASON_INVALID"):
            progress.finish_run(value, "BLOCKED", reason="NOT_A_REGISTERED_CODE")

    def test_entering_a_stage_clears_the_previous_executor(self) -> None:
        progress = load_progress()
        value = progress.new_progress("anthropic", "PLAN", instant("2026-10-05T12:00:00Z"), executor="claude")

        tasks = progress.enter_stage(value, "TASKS", instant("2026-10-05T12:00:10Z"))

        self.assertIsNone(tasks["run"]["executor"])
        self.assertIsNone(tasks["run"]["model"])
        self.assertEqual("claude", tasks["stage"]["history"][-1]["executor"])

    def test_a_hand_edited_reason_is_refused_when_unregistered_or_while_running(self) -> None:
        progress = load_progress()
        value = progress.new_progress("openai-codex", "IMPLEMENT", instant("2026-10-05T12:00:00Z"))
        running = dict(value, run=dict(value["run"], reason="FOCUSED_TESTS_FAILED"))
        with self.assertRaisesRegex(progress.ProgressError, "PROGRESS_STATE_INVALID"):
            progress.validate_progress(running)
        blocked = progress.finish_run(value, "BLOCKED", instant("2026-10-05T12:00:01Z"), reason="FOCUSED_TESTS_FAILED")
        forged = dict(blocked, run=dict(blocked["run"], reason="NOT_A_REGISTERED_CODE"))
        with self.assertRaisesRegex(progress.ProgressError, "PROGRESS_STATE_INVALID"):
            progress.validate_progress(forged)

    @unittest.skipUnless(os.name == "posix", "descriptor-anchored paths require POSIX")
    def test_cli_records_the_executor_and_explains_the_stop(self) -> None:
        import contextlib
        import io

        progress = load_progress()
        with tempfile.TemporaryDirectory(prefix="sdd-progress-cli-") as temp:
            path = str(Path(temp).resolve() / "progress.json")
            common = ["--file", path, "--no-color"]
            for argv in (
                ["start", "--provider", "anthropic", "--stage", "IMPLEMENT", *common],
                ["executor", "--name", "codex", "--model", "gpt-5.6-sol", *common],
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(0, progress.main(argv))
            refused = io.StringIO()
            with contextlib.redirect_stdout(refused):
                code = progress.main(["finish", "--status", "BLOCKED", "--reason", "NOT_A_REGISTERED_CODE", *common])
            shown = io.StringIO()
            with contextlib.redirect_stdout(shown):
                self.assertEqual(0, progress.main(["finish", "--status", "BLOCKED", "--reason", "CI_FAILED", *common]))

        self.assertEqual(2, code)
        self.assertIn("PROGRESS_REASON_INVALID", refused.getvalue())
        self.assertIn("! IMPLEMENT", shown.getvalue())
        self.assertIn("codex · gpt-5.6-sol", shown.getvalue())
        self.assertIn("O que fazer", shown.getvalue())
        self.assertIn("código técnico: CI_FAILED", shown.getvalue())

    def test_summary_shows_milestones_next_step_and_pending_human_decision(self) -> None:
        progress = load_progress()
        value = progress.new_progress("anthropic", "IMPLEMENT", instant("2026-10-05T12:00:00Z"))
        for index, text in enumerate(("Spec aprovada com 5 critérios", "Plano: 4 fatias", "Fatia 1 RED → GREEN"), 1):
            value = progress.record_milestone(value, text, instant(f"2026-10-05T12:00:0{index}Z"))
        value = progress.set_next_step(value, "Fatia 2: placa duplicada", instant("2026-10-05T12:00:05Z"))
        value = progress.set_attention(value, "Responder 1 pergunta → sdd.py answer", instant("2026-10-05T12:00:06Z"))

        dashboard = progress.render(value, now=instant("2026-10-05T12:00:07Z"), color=False)

        self.assertIn("│ Até aqui", dashboard)
        self.assertIn("• Fatia 1 RED → GREEN", dashboard)
        self.assertIn("Próximo      Fatia 2: placa duplicada", dashboard)
        self.assertIn("Precisa de você? Sim: Responder 1 pergunta → sdd.py answer", dashboard)

        cleared = progress.set_attention(value, None, instant("2026-10-05T12:00:08Z"))
        self.assertIn("Precisa de você? Não", progress.render(cleared, now=instant("2026-10-05T12:00:08Z")))

    def test_milestones_are_bounded_to_the_most_recent(self) -> None:
        progress = load_progress()
        value = progress.new_progress("anthropic", "PLAN", instant("2026-10-05T12:00:00Z"))
        for index in range(progress.MAX_MILESTONES + 3):
            value = progress.record_milestone(value, f"marco {index}", instant("2026-10-05T12:00:01Z"))

        self.assertEqual(progress.MAX_MILESTONES, len(value["summary"]["milestones"]))
        self.assertEqual(f"marco {progress.MAX_MILESTONES + 2}", value["summary"]["milestones"][-1]["message"])

    def test_readable_jev_shows_confidence_and_what_waits_for_the_user(self) -> None:
        progress = load_progress()
        value = progress.new_progress("anthropic", "PLAN", instant("2026-10-05T12:00:00Z"))
        value = progress.start_jev(value, provider="typesafe", area="classificação", questions=["risk", "specialist"],
                                   at=instant("2026-10-05T12:00:01Z"))
        value = progress.finish_jev(
            value, "REVIEW", instant("2026-10-05T12:00:02Z"), model="jev-1.13.0",
            decisions=[
                {"id": "risk", "value": "MEDIUM", "confidence": 0.91, "disposition": "DECIDED"},
                {"id": "specialist", "value": None, "confidence": 0.52, "disposition": "REVIEW"},
            ],
        )

        dashboard = progress.render(value, now=instant("2026-10-05T12:00:03Z"), color=False)

        self.assertIn("decidiu 1 de 2", dashboard)
        self.assertIn("✓ risk: MEDIUM (0,91)", dashboard)
        self.assertIn("? specialist: confiança 0,52, abaixo de 0,70 → aguardando você", dashboard)
        with self.assertRaisesRegex(progress.ProgressError, "PROGRESS_JEV_INVALID"):
            progress.finish_jev(
                progress.start_jev(progress.new_progress("x", "PLAN"), provider="t", area="a", questions=["q"]),
                "DECIDED", decisions=[{"id": "q", "value": "x", "confidence": 1.5, "disposition": "DECIDED"}],
            )

    def test_schema_two_progress_files_still_load(self) -> None:
        progress = load_progress()
        legacy = progress.new_progress("openai-codex", "PLAN", instant("2026-10-05T12:00:00Z"))
        legacy["schema_version"] = 2
        legacy.pop("summary")
        legacy["jev"].pop("decisions")

        upgraded = progress.validate_progress(legacy)

        self.assertEqual(progress.SCHEMA_VERSION, upgraded["schema_version"])
        self.assertEqual([], upgraded["summary"]["milestones"])
        self.assertEqual([], upgraded["jev"]["decisions"])

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
        self.assertIn("9s · bloqueada", dashboard)

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
        self.assertIn("Concluído", dashboard)
        self.assertIn("8 de 8 fases", dashboard)

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
