#!/usr/bin/env python3
"""Tolerant final-message extraction for the executor launcher.

The Codex worker is allowed to wrap its JSON object in a short prose preamble and a
single fenced block, or to leave one bare top-level JSON object in the middle of prose,
because a hard failure here would discard correct work. Everything stricter still fails:
two fenced blocks, two top-level objects, a fenced block that is not an object, or prose
with no JSON object at all stay invalid. The normalized object is what gets written to
``--final``; the raw message is kept beside it as evidence.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_FENCE_OPEN = frozenset({"```", "```json"})
_FENCE_CLOSE = "```"


def _fenced_blocks(text: str) -> list[str]:
    """Return the content of every ```/```json fenced block in ``text`` (Markdown fences)."""
    lines = text.splitlines()
    blocks: list[str] = []
    index = 0
    while index < len(lines):
        if lines[index].strip() in _FENCE_OPEN:
            end = index + 1
            while end < len(lines) and lines[end].strip() != _FENCE_CLOSE:
                end += 1
            if end < len(lines):
                blocks.append("\n".join(lines[index + 1:end]))
                index = end + 1
                continue
        index += 1
    return blocks


def _object_spans(text: str) -> list[tuple[int, int]]:
    """Top-level ``{...}`` spans, tracking strings so braces inside JSON strings do not close them."""
    spans: list[tuple[int, int]] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0:
                spans.append((start, index + 1))
    return spans


def _parses_as_json(text: str) -> bool:
    try:
        json.loads(text)
        return True
    except json.JSONDecodeError:
        return False


def _single_top_level_object(text: str) -> str | None:
    """Exactly one top-level JSON object and nothing else that parses as JSON, else ``None``."""
    candidates: list[str] = []
    for start, end in _object_spans(text):
        candidate = text[start:end]
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append((start, end, candidate))
    if len(candidates) != 1:
        return None
    start, end, candidate = candidates[0]
    remainder = (text[:start] + text[end:]).strip()
    if _parses_as_json(remainder):
        return None
    return candidate


def _normalized(value: dict[str, Any], source: str) -> tuple[bytes, bool, dict[str, Any]]:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8"), True, {
        "transport_normalized": True,
        "transport_source": source,
    }


def normalize_codex_last_message(data: bytes) -> tuple[bytes | None, bool, dict[str, Any]]:
    """Normalize one Codex last message; already-valid JSON objects pass through unchanged."""
    if not data.strip():
        return None, False, {"last_message": "EMPTY"}
    text = data.decode("utf-8", errors="replace")
    try:
        value = json.loads(text.strip())
    except json.JSONDecodeError:
        value = None
    if isinstance(value, dict):
        return data, True, {}
    blocks = _fenced_blocks(text)
    if blocks:
        if len(blocks) != 1:
            return data, False, {}
        content = blocks[0].strip()
        try:
            value = json.loads(content)
        except json.JSONDecodeError:
            return data, False, {}
        if not isinstance(value, dict):
            return data, False, {}
        return _normalized(value, "FENCED_BLOCK")
    single = _single_top_level_object(text)
    if single is None:
        return data, False, {}
    return _normalized(json.loads(single), "SINGLE_OBJECT")


def extract_result(executor: str, stdout: bytes, last_message: Path) -> tuple[bytes | None, bool, dict[str, Any]]:
    """Return (final bytes, is_json_object, diagnostics). Raw non-JSON output is kept as evidence.

    The Claude path is unchanged: the structured_output of the ``--output-format json``
    envelope, falling back to the textual ``result``. The Codex path goes through the
    tolerant normalizer above.
    """
    diagnostics: dict[str, Any] = {}
    if executor == "claude":
        text = stdout.decode("utf-8", errors="replace")
        try:
            envelope = json.loads(text)
        except json.JSONDecodeError:
            return (stdout if text.strip() else None), False, {"envelope": "NOT_JSON"}
        if not isinstance(envelope, dict):
            return stdout, False, {"envelope": "NOT_OBJECT"}
        diagnostics = {"envelope_subtype": envelope.get("subtype"), "envelope_is_error": envelope.get("is_error")}
        structured = envelope.get("structured_output")
        if isinstance(structured, dict):
            return (json.dumps(structured, indent=2, ensure_ascii=False) + "\n").encode("utf-8"), True, diagnostics
        result = envelope.get("result")
        if not isinstance(result, str) or not result.strip():
            return None, False, diagnostics
        try:
            is_object = isinstance(json.loads(result), dict)
        except json.JSONDecodeError:
            is_object = False
        return result.encode("utf-8"), is_object, diagnostics
    if not last_message.is_file():
        return None, False, {"last_message": "MISSING"}
    return normalize_codex_last_message(last_message.read_bytes())
