#!/usr/bin/env python3
"""Compact prompt-profile source excerpt windows.

``prompt_rules`` re-exports ``compact_match_tokens`` and
``compact_source_excerpts`` so existing imports keep working.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,}$")
_MAX_WINDOWS = 6
_MAX_LINES = 120
_CONTEXT_LINES = 15
_FALLBACK_LINES = 40


def compact_match_tokens(context: dict[str, Any], ticket: str) -> list[str]:
    """Only plain identifiers become match tokens; globs like ``*`` are dropped."""
    tokens: set[str] = set()
    if ticket and _IDENTIFIER_RE.fullmatch(ticket):
        tokens.add(ticket)
    for path in context.get("editable_paths") or []:
        name = Path(path).name
        if _IDENTIFIER_RE.fullmatch(name):
            tokens.add(name)
    for check_id in context.get("expected_acceptance") or {}:
        if _IDENTIFIER_RE.fullmatch(check_id):
            tokens.add(check_id)
    return sorted(tokens)


def _merge_windows(windows: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not windows:
        return []
    merged = [list(sorted(windows)[0])]
    for low, high in sorted(windows)[1:]:
        if low <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], high)
        else:
            merged.append([low, high])
    return [(low, high) for low, high in merged]


def compact_source_excerpts(repo: Path, sources: list[dict[str, Any]], tokens: list[str], *, with_excerpts: bool = True) -> list[str]:
    """Windows clamped to each source's declared ``lines`` range.

    A source emits at most 6 windows and at most 120 excerpt lines; an empty
    range or file renders only ``### <path> (empty)``. With ``with_excerpts``
    false, only headers are emitted so the reduced ladder can shrink.
    """
    parts: list[str] = []
    for source in sources:
        try:
            lines = (repo / source["path"]).read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        declared_start, declared_end = source["lines"]
        low = max(0, declared_start - 1)
        high = min(len(lines), declared_end)
        if low >= high:
            parts.append(f"### {source['path']} (empty)")
            continue
        matches = [index for index in range(low, high) if any(token and token in lines[index] for token in tokens)]
        if matches:
            windows = _merge_windows([
                (max(low, index - _CONTEXT_LINES), min(high, index + _CONTEXT_LINES + 1))
                for index in matches
            ])
        else:
            windows = [(low, min(high, low + _FALLBACK_LINES))]
        emitted = 0
        for window_low, window_high in windows[:_MAX_WINDOWS]:
            if with_excerpts:
                remaining = _MAX_LINES - emitted
                if remaining <= 0:
                    break
                window_high = min(window_high, window_low + remaining)
                body = "\n".join(
                    f"{window_low + 1 + offset}: {line}"
                    for offset, line in enumerate(lines[window_low:window_high])
                )
                emitted += window_high - window_low
                parts.append(f"### {source['path']} lines {window_low + 1}-{window_high}\n{body}")
            else:
                parts.append(f"### {source['path']} lines {window_low + 1}-{window_high}")
    return parts
