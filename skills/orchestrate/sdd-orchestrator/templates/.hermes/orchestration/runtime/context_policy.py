"""Deterministic, fail-closed protection rules and the omission decision.

Protection is decided here, never by Jev: a protected segment is never omitted and
never sent to the evaluator as a passage (user text is sent as context; see
``context_judge.build_payload``). Structural rules come first (role, opaque data, pending
tool calls, recent turns, errors), then content rules. Case-sensitive tokens such as
``MUST`` or ``OK`` are word-bounded so ordinary prose does not trigger them.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

try:
    from redaction import redact
except ImportError:
    redact = None

_I = re.IGNORECASE
PROTECTION_RULES = (
    ("acceptance-criteria", re.compile(r"\bAC-\d+\b|acceptance criteria|critérios de aceite", _I)),
    ("architecture-decision", re.compile(r"\bADR(?:-\d+)?\b")),
    ("architecture-decision", re.compile(r"\bdecision:|\bdecisão\b", _I)),
    ("requirement", re.compile(r"\b(?:MUST|SHALL)\b")),
    ("requirement", re.compile(r"\b(?:contract|schema|requirement|requisito)s?\b", _I)),
    ("security-constraint", re.compile(r"\b(?:security|secret|credential|permission|segurança)s?\b", _I)),
    ("unresolved-error", re.compile(r"Traceback|\bFAILED\b|\bError:|\bERROR\b")),
    ("validation-evidence", re.compile(r"\bOK\b|\bpassed\b|\bRED\b|\bGREEN\b|\bexit_code\b")),
    ("blocker", re.compile(r"\bblocked\b|\bTODO\b|\bpending\b|\bbloqueio\b", _I)),
)
RULE_IDS = (
    "user-instruction",
    "opaque",
    "orphan-tool-result",
    "recent-turn",
    "unresolved-error",
    "redaction-unavailable",
    "secret-present",
    "sdd-section",
    "acceptance-criteria",
    "architecture-decision",
    "requirement",
    "security-constraint",
    "validation-evidence",
    "blocker",
)
SECTION = re.compile(r"^#{1,6}\s*(Rules|Acceptance|Contract|Objective)\b", _I)
_AC_ID = re.compile(r"\bAC-\d+\b")
_MARKER_LINES = (
    re.compile(r"\b(?:MUST|SHALL)\b"),
    re.compile(r"\b(?:contract|schema|requirement|requisito)s?\b", _I),
)


@dataclass
class Decision:
    """A disposition (``PRESERVE`` or ``OMIT``) and its auditable reason."""

    disposition: str
    reason: str


def _locate(request: Any, path: tuple[int, ...]) -> tuple[Any, Any]:
    for message in request.messages:
        for block in message.blocks:
            if block.path == path:
                return message, block
    return None, None


def _result_ids(request: Any) -> set[str]:
    found = set()
    for message in request.messages:
        for block in message.blocks:
            if block.type == "tool_result" and isinstance(block.data, dict):
                identifier = block.data.get("tool_use_id") or block.data.get("call_id")
                if isinstance(identifier, str):
                    found.add(identifier)
    return found


def pending_tool_calls(request: Any) -> list[str]:
    """Return the ids of tool calls that have no matching result yet."""
    answered = _result_ids(request)
    pending = []
    for message in request.messages:
        for block in message.blocks:
            if block.type == "tool_use" and isinstance(block.data, dict):
                identifier = block.data.get("id") or block.data.get("call_id")
                if isinstance(identifier, str) and identifier not in answered:
                    pending.append(identifier)
    return pending


def _orphan_result(request: Any, block: Any) -> bool:
    if block.type != "tool_result" or not isinstance(block.data, dict):
        return False
    identifier = block.data.get("tool_use_id") or block.data.get("call_id")
    calls = {
        item.data.get("id") or item.data.get("call_id")
        for message in request.messages
        for item in message.blocks
        if item.type == "tool_use" and isinstance(item.data, dict)
    }
    return identifier not in calls


def _structural(segment: Any, request: Any, config: dict[str, Any]) -> str | None:
    message, block = _locate(request, segment.path)
    if message is None or block is None or block.type == "opaque":
        return "opaque"
    if block.type == "text" and message.role != "assistant":
        return "user-instruction"
    if block.type == "sdd_section" and SECTION.match(block.text):
        return "sdd-section"
    if _orphan_result(request, block):
        return "orphan-tool-result"
    recent = config.get("recent_turns_protected", 0)
    if block.type != "sdd_section" and recent and segment.path[0] >= len(request.messages) - recent:
        return "recent-turn"
    if isinstance(block.data, dict) and block.data.get("is_error"):
        return "unresolved-error"
    return None


def protection(segment: Any, request: Any, config: dict[str, Any]) -> str | None:
    """Return the protection rule id that keeps ``segment``, or ``None`` when it may be judged."""
    rule = _structural(segment, request, config)
    if rule is not None:
        return rule
    if redact is None:
        return "redaction-unavailable"
    _, block = _locate(request, segment.path)
    # Screen the whole field, not only the span: a key block or a token can begin in
    # one span and continue in the next, where the redactor would no longer see it.
    if redact(block.text) != block.text or redact(segment.text) != segment.text:
        return "secret-present"
    for rule_id, pattern in PROTECTION_RULES:
        if pattern.search(segment.text):
            return rule_id
    return None


def _valid_probability(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and 0 <= value <= 1


def decide(segment: Any, answer: Any, thresholds: dict[str, float]) -> Decision:
    """Omit only when both loss probabilities are valid and at or below their thresholds."""
    if not isinstance(answer, dict):
        return Decision("PRESERVE", "INVALID_ANSWER")
    fact, constraint = answer.get("fact_loss"), answer.get("constraint_loss")
    if not _valid_probability(fact) or not _valid_probability(constraint):
        return Decision("PRESERVE", "INVALID_ANSWER")
    assert isinstance(fact, (int, float)) and isinstance(constraint, (int, float))
    if fact <= thresholds["fact_loss_max"] and constraint <= thresholds["constraint_loss_max"]:
        return Decision("OMIT", "JEV_APPROVED")
    return Decision("PRESERVE", "JEV_RETAINED")


def requirement_markers(request: Any) -> list[str]:
    """Return the sorted requirement markers (AC ids, MUST/SHALL and contract lines) in ``request``."""
    found = set()
    for message in request.messages:
        for block in message.blocks:
            found.update(_AC_ID.findall(block.text))
            for line in block.text.splitlines():
                if any(pattern.search(line) for pattern in _MARKER_LINES):
                    found.add(line.strip())
    return sorted(found)
