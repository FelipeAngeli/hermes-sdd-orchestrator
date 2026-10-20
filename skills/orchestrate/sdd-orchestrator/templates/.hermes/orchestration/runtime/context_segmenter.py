"""Candidate segmentation for text fields the rebuilder can rewrite safely.

Only completed tool results, assistant prose and SDD prompt sections can be
segmented, and only for the kinds the stage makes eligible. Object tool inputs are
never segmented. An SDD section's heading line is never part of a span. Spans end
on paragraph or line boundaries; code-like text stays one span.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

KINDS = ("TOOL_RESULT", "ASSISTANT_TEXT", "SDD_SECTION")
_BLOCK_KINDS = {"tool_result": "TOOL_RESULT", "text": "ASSISTANT_TEXT", "sdd_section": "SDD_SECTION"}


@dataclass
class Segment:
    """One offset-addressed candidate inside a known text field."""

    id: str
    path: tuple[int, ...]
    start: int
    end: int
    sha256: str
    kind: str
    is_code: bool
    text: str


def is_code_like(text: str) -> bool:
    """Structural code detection: fences, indented blocks or many statement separators."""
    lines = text.splitlines()
    indented = sum(1 for line in lines if line.startswith(("    ", "\t")))
    return "```" in text or (len(lines) >= 4 and indented >= 3) or text.count(";") >= 5


def split_text(text: str, target: int, maximum: int) -> list[tuple[int, int]]:
    """Return ``(start, end)`` spans on paragraph or line boundaries; code stays whole.

    A span never ends inside a line: a single line longer than ``maximum`` stays one
    span (the judge then refuses it as too large), so no token is ever cut in two.
    """
    if len(text) <= maximum or is_code_like(text):
        return [(0, len(text))]
    spans = []
    start = 0
    while start < len(text):
        limit = min(len(text), start + maximum)
        if limit == len(text):
            spans.append((start, limit))
            break
        paragraph = text.rfind("\n\n", start + min(target, maximum) // 2, limit)
        if paragraph > start:
            cut = paragraph + 2
        else:
            line = text.rfind("\n", start, limit)
            if line >= start:
                cut = line + 1
            else:
                following = text.find("\n", limit)
                cut = len(text) if following < 0 else following + 1
        spans.append((start, cut))
        start = cut
    return spans


def segment(request: Any, config: dict[str, Any], stage: str) -> list[Segment]:
    """Return the eligible spans of ``request`` for ``stage``."""
    eligible = set(config.get("stages", {}).get(stage, {}).get("eligible_kinds", []))
    result = []
    for message in request.messages:
        for block in message.blocks:
            kind = _BLOCK_KINDS.get(block.type)
            if kind is None or kind not in eligible or not block.text:
                continue
            body = 0
            if block.type == "sdd_section" and block.text.startswith("#"):
                newline = block.text.find("\n")
                body = len(block.text) if newline < 0 else newline + 1
            spans = split_text(block.text[body:], config["segment_target_chars"], config["max_segment_chars"])
            for start, end in ((body + left, body + right) for left, right in spans):
                text = block.text[start:end]
                if not text.strip():
                    continue
                identifier = f"{'.'.join(map(str, block.path))}@{start}:{end}"
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                result.append(Segment(identifier, block.path, start, end, digest, kind, is_code_like(text), text))
    return result
