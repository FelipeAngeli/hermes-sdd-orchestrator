"""Readable `sdd.py status` / `sdd.py next` output (`--format text`), with the JSON default untouched."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))

import sdd  # noqa: E402
import stop_reasons as sr  # noqa: E402


STATUS = {
    "status": "OK", "storage": "local", "ticket": "EMOBI-142", "stage": "IMPLEMENT", "stage_status": "RUNNING",
    "profile": "CODE", "mode": "AUTO",
    "paths": {"state": "/r/.hermes/orchestration/STATE.yaml", "journal": "/r/j.json",
              "history_dir": "/r/history", "runtime_dir": "/r/runtime"},
    "recovery": {"decision": "NONE", "journal_status": "IDLE"},
    "budgets_left": {"executor_calls": 7, "ci_runs": 2},
    "next_action": "PREPARE", "stop": None,
    "next_step": "Run `sdd.py next` and execute exactly the printed commands.", "next_command": "python sdd.py next",
}

NEXT_OK = {
    "status": "OK", "step": "PREPARE", "end_turn": False, "stage": "IMPLEMENT",
    "commands": ["python sdd.py manifest --stage IMPLEMENT", "python stage_context.py check", "python sdd.py prepare"],
    "next_step": "Build and check the IMPLEMENT slice 3 manifest, then prepare the journaled action and its prompt.",
    "next_command": "python sdd.py next", "action_id": "A-7",
}


def next_stop(code: str) -> dict:
    return sdd.stopped(None, sdd.stop(code)) | {"stage": "TASKS"}


class RenderTextTests(unittest.TestCase):
    def test_status_text_names_ticket_stage_and_next_action(self) -> None:
        text = sdd.render_text("status", STATUS, detail="normal")

        self.assertIn("Ticket       EMOBI-142", text)
        self.assertIn("Fase         Implementar (IMPLEMENT)", text)
        self.assertIn("Próximo      PREPARE", text)
        self.assertIn("Rode         python sdd.py next", text)
        self.assertNotIn("executor_calls", text, "budgets only in verbose")
        self.assertIn("Perfil       CODE", text)
        self.assertIn("Modo         AUTO", text)

    def test_status_without_a_demand_does_not_show_idle_as_a_ticket(self) -> None:
        idle = dict(STATUS, ticket="IDLE", stage="IDLE", profile=None,
                    stop={"stop_reason": "IDLE_NO_DEMAND", "kind": "PAUSED", "next_step": "x"})
        text = sdd.render_text("status", idle, detail="normal")

        self.assertIn("Ticket       nenhuma demanda ativa", text)
        self.assertNotIn("Ticket       IDLE", text)
        self.assertNotIn("Perfil", text)
        self.assertTrue(sdd.render_text("status", idle, detail="compact").startswith("■ nenhuma demanda ativa · Ocioso (IDLE)"))

    def test_status_verbose_adds_budgets_recovery_and_paths(self) -> None:
        text = sdd.render_text("status", STATUS, detail="verbose")

        self.assertIn("executor_calls: 7", text)
        self.assertIn("Recuperação  NONE · journal IDLE", text)
        self.assertIn("/r/.hermes/orchestration/STATE.yaml", text)

    def test_status_with_a_stop_explains_it(self) -> None:
        stopped = dict(STATUS, next_action="FOCUSED_TESTS_FAILED",
                       stop={"stop_reason": "FOCUSED_TESTS_FAILED", "kind": "BLOCKED", "next_step": "x"})
        text = sdd.render_text("status", stopped, detail="normal")
        explanation = sr.explain("FOCUSED_TESTS_FAILED")

        self.assertIn(f"O que houve  {explanation['what']}", text)
        self.assertIn(f"O que fazer  {explanation['action']}", text)
        self.assertIn("código técnico: FOCUSED_TESTS_FAILED", text)

    def test_next_text_lists_the_commands_in_order(self) -> None:
        text = sdd.render_text("next", NEXT_OK, detail="normal")

        self.assertIn("Passo        PREPARE · Implementar (IMPLEMENT)", text)
        self.assertIn(NEXT_OK["next_step"], text)
        lines = text.splitlines()
        positions = [next(i for i, line in enumerate(lines) if command in line) for command in NEXT_OK["commands"]]
        self.assertEqual(sorted(positions), positions)
        self.assertIn("1. python sdd.py manifest", text)

    def test_next_compact_is_one_line(self) -> None:
        self.assertEqual(
            "▶ PREPARE · Implementar (IMPLEMENT) · 3 comandos",
            sdd.render_text("next", NEXT_OK, detail="compact"),
        )
        stopped = next_stop("HUMAN_DECISION_REQUIRED")
        compact = sdd.render_text("next", stopped, detail="compact")
        self.assertEqual(1, len(compact.splitlines()))
        self.assertTrue(compact.startswith("■ Parado em Tarefas (TASKS): "))
        self.assertIn(sr.explain("HUMAN_DECISION_REQUIRED")["what"], compact)

    def test_next_stop_explains_and_shows_the_resolution_command(self) -> None:
        stopped = next_stop("HUMAN_DECISION_REQUIRED")
        text = sdd.render_text("next", stopped, detail="normal")
        explanation = sr.explain("HUMAN_DECISION_REQUIRED")

        self.assertIn("Parado em Tarefas (TASKS)", text)
        self.assertIn(f"Por quê      {explanation['why']}", text)
        self.assertIn(stopped["next_command"], text)
        self.assertIn("código técnico: HUMAN_DECISION_REQUIRED", text)

    def test_every_registered_stop_renders(self) -> None:
        for code in sr.STOP_REASONS:
            with self.subTest(code=code):
                text = sdd.render_text("next", next_stop(code), detail="verbose")
                self.assertIn(code, text)

    def test_error_payload_renders_message_and_next_command(self) -> None:
        error = {"status": "ERROR", "message": "ValueError: boom", "next_step": "Run `sdd.py status`.",
                 "next_command": "python sdd.py status"}
        text = sdd.render_text("next", error, detail="normal")

        self.assertIn("Erro         ValueError: boom", text)
        self.assertIn("Rode         python sdd.py status", text)

    def test_text_never_contains_terminal_controls(self) -> None:
        hostile = dict(NEXT_OK, next_step="evil\x1b[31m red\x9b2J", commands=["ok\x07bell"])
        text = sdd.render_text("next", hostile, detail="verbose")

        self.assertNotIn("\x1b", text)
        self.assertNotIn("\x9b", text)
        self.assertNotIn("\x07", text)


class MainFormatTests(unittest.TestCase):
    def run_main(self, argv: list[str], result: dict) -> tuple[int, str]:
        output = io.StringIO()
        fake = mock.Mock(return_value=result)
        with mock.patch.object(sdd, "Ctx", mock.Mock()), \
                mock.patch.dict(sdd.COMMANDS, {"status": fake, "next": fake}), \
                contextlib.redirect_stdout(output):
            code = sdd.main(argv)
        return code, output.getvalue()

    def test_json_stays_the_default_and_is_unchanged(self) -> None:
        code, output = self.run_main(["next"], NEXT_OK)

        self.assertEqual(0, code)
        self.assertEqual(json.dumps(NEXT_OK, ensure_ascii=False, sort_keys=True) + "\n", output)

    def test_format_text_prints_the_readable_view(self) -> None:
        code, output = self.run_main(["next", "--format", "text", "--detail", "compact"], NEXT_OK)

        self.assertEqual(0, code)
        self.assertEqual("▶ PREPARE · Implementar (IMPLEMENT) · 3 comandos\n", output)

    def test_status_accepts_the_flags_too(self) -> None:
        code, output = self.run_main(["status", "--format", "text"], STATUS)

        self.assertEqual(0, code)
        self.assertIn("Ticket       EMOBI-142", output)

    def test_errors_keep_exit_code_two_in_text_mode(self) -> None:
        output = io.StringIO()
        with mock.patch.object(sdd, "Ctx", mock.Mock(side_effect=RuntimeError("boom"))), \
                contextlib.redirect_stdout(output):
            code = sdd.main(["status", "--format", "text"])

        self.assertEqual(2, code)
        self.assertIn("Erro         RuntimeError: boom", output.getvalue())

    def test_other_subcommands_do_not_accept_format(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            sdd.parser().parse_args(["close", "--format", "text"])


if __name__ == "__main__":
    unittest.main()
