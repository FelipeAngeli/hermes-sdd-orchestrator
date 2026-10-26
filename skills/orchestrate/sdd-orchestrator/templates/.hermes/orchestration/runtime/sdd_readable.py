#!/usr/bin/env python3
"""Readable (non-JSON) rendering of ``sdd.py status`` and ``sdd.py next``.

Presentation only: the JSON payload stays the controller's contract, and nothing here
reads STATE, the journal or the repository. ``sdd.py`` re-exports ``render_text``,
``STAGE_LABELS`` and ``DETAIL_LEVELS`` so the public names do not move.
"""
from __future__ import annotations

from typing import Any

import stop_reasons as sr

STAGE_LABELS = {
    "IDLE": "Ocioso", "SPECIFY": "Especificar", "CLARIFY": "Esclarecer", "PLAN": "Planejar", "TASKS": "Tarefas",
    "IMPLEMENT": "Implementar", "TEST": "Testar", "REVIEW": "Revisar", "DONE": "Concluído",
}
TEXT_FORMAT_COMMANDS = ("status", "next")
DETAIL_LEVELS = ("compact", "normal", "verbose")


def _printable(value: Any) -> str:
    """One display line: terminal controls (C0, C1, bidi, line separators) become spaces."""
    text = "" if value is None else str(value)
    return "".join(character if character.isprintable() else " " for character in text)


def _stage_label(stage: Any) -> str:
    code = _printable(stage) or "?"
    name = STAGE_LABELS.get(code)
    return f"{name} ({code})" if name else code


def _text_row(label: str, value: Any) -> str:
    return f"│ {label:<12} {_printable(value)}".rstrip()


def _stop_lines(code: str, next_command: Any) -> list[str]:
    explanation = sr.explain(code)
    lines = [
        _text_row("O que houve", explanation["what"]),
        _text_row("Por quê", explanation["why"]),
        _text_row("O que fazer", explanation["action"]),
    ]
    if next_command:
        lines.append(_text_row("Rode", next_command))
    lines.append(_text_row("", f"(código técnico: {code})"))
    return lines


def render_text(command_name: str, result: dict[str, Any], *, detail: str = "normal") -> str:
    """Human-readable view of a `status` or `next` result; the JSON stays the controller's contract."""
    status = result.get("status")
    if status not in {"OK", "STOP"} or (command_name == "status" and status != "OK"):
        code = _printable(status) or "ERROR"
        message = result.get("message") or code
        if detail == "compact":
            command_hint = f" → rode: {_printable(result['next_command'])}" if result.get("next_command") else ""
            return f"✖ Erro: {_printable(message)}{command_hint}"
        lines = ["╭─ Hermes SDD · erro", _text_row("Erro", message)]
        if code in sr.STOP_REASONS:
            lines.extend(_stop_lines(code, result.get("next_command")))
        else:
            if result.get("next_step"):
                lines.append(_text_row("O que fazer", result["next_step"]))
            if result.get("next_command"):
                lines.append(_text_row("Rode", result["next_command"]))
        lines.append("╰────────────────────────────")
        return "\n".join(lines)
    if command_name == "status":
        return _render_status(result, detail)
    return _render_next(result, detail)


def _render_status(result: dict[str, Any], detail: str) -> str:
    stage = _stage_label(result.get("stage"))
    stop = result.get("stop") or None
    # With no demand the controller reports the ticket as IDLE; show that as "no demand", not as a ticket.
    idle = result.get("stage") == "IDLE" or result.get("ticket") in {None, "", "IDLE"}
    ticket = "nenhuma demanda ativa" if idle else _printable(result.get("ticket"))
    if detail == "compact":
        if stop:
            return f"■ {ticket} · {stage} · parado: {sr.explain(stop.get('stop_reason'))['what']}"
        return f"▶ {ticket} · {stage} · próximo: {_printable(result.get('next_action'))}"
    lines = [
        "╭─ Hermes SDD · status",
        _text_row("Ticket", ticket),
        _text_row("Fase", stage + (f" · {_printable(result.get('stage_status'))}" if result.get("stage_status") else "")),
    ]
    if result.get("profile"):
        lines.append(_text_row("Perfil", result["profile"]))
    if result.get("mode"):
        lines.append(_text_row("Modo", result["mode"]))
    if stop:
        lines.append(_text_row("Estado", "Parado" if stop.get("kind") != "PAUSED" else "Aguardando decisão"))
        lines.append("│")
        lines.extend(_stop_lines(_printable(stop.get("stop_reason")), None))
    else:
        lines.append(_text_row("Próximo", result.get("next_action")))
    lines.append(_text_row("Rode", result.get("next_command")))
    if stop:
        lines.append(_text_row("", "(o `next` mostra o comando que resolve esta parada)"))
    if detail == "verbose":
        recovery = result.get("recovery") or {}
        lines.extend(["│", _text_row("Recuperação", f"{_printable(recovery.get('decision'))} · journal {_printable(recovery.get('journal_status'))}")])
        budgets = result.get("budgets_left") or {}
        if budgets:
            lines.append("│ Orçamentos restantes")
            lines.extend(f"│   {_printable(name)}: {_printable(left)}" for name, left in sorted(budgets.items()))
        paths = result.get("paths") or {}
        if paths:
            lines.append("│ Arquivos")
            lines.extend(f"│   {_printable(name)}: {_printable(path)}" for name, path in sorted(paths.items()))
        if result.get("storage"):
            lines.append(_text_row("Storage", result["storage"]))
    lines.append("╰────────────────────────────")
    return "\n".join(lines)


def _render_next(result: dict[str, Any], detail: str) -> str:
    stage = _stage_label(result.get("stage"))
    commands = [_printable(item) for item in result.get("commands") or []]
    if result.get("status") == "STOP":
        code = _printable(result.get("stop_reason")) or "BLOCKED"
        word = "Aguardando você" if result.get("kind") == "PAUSED" else "Parado"
        if detail == "compact":
            return f"■ {word} em {stage}: {sr.explain(code)['what']}"
        lines = ["╭─ Hermes SDD · próximo passo", _text_row("Estado", f"{word} em {stage}"), "│"]
        lines.extend(_stop_lines(code, result.get("next_command")))
        if detail == "verbose" and result.get("next_step"):
            lines.append(_text_row("Detalhe", result["next_step"]))
        lines.append("╰────────────────────────────")
        return "\n".join(lines)
    step_name = _printable(result.get("step")) or "?"
    if detail == "compact":
        plural = "comando" if len(commands) == 1 else "comandos"
        return f"▶ {step_name} · {stage} · {len(commands)} {plural}"
    lines = [
        "╭─ Hermes SDD · próximo passo",
        _text_row("Passo", f"{step_name} · {stage}"),
        _text_row("O que fazer", result.get("next_step")),
    ]
    if commands:
        lines.extend(["│", "│ Rode, nesta ordem:"])
        lines.extend(f"│   {index}. {item}" for index, item in enumerate(commands, 1))
    if result.get("end_turn") is False and result.get("next_command"):
        lines.append(_text_row("Depois", result["next_command"]))
    if detail == "verbose" and result.get("action_id"):
        lines.append(_text_row("Ação", result["action_id"]))
    lines.append("╰────────────────────────────")
    return "\n".join(lines)
