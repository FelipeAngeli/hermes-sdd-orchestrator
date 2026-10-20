"""Protocol-preserving application of approved omissions, followed by an integrity check.

An omission replaces one offset-addressed text range with an explicit marker. Only the
text field a segment was cut from changes; ids, names, call ids, roles, non-text items
and every other block stay equal. Any doubt returns the original request and
``REBUILD_INTEGRITY_FAILED``.
"""
from __future__ import annotations

import copy
from typing import Any

from context_collector import ContextRequest, from_anthropic, from_openai, from_sdd_prompt

MARKER_TEMPLATE = "[context-engine omitted segment {id} sha256={sha}; recover with context_engine.py recover --run {run_id} --audit-dir <state dir>]"
INTEGRITY_FAILED = "REBUILD_INTEGRITY_FAILED"


def marker(segment: Any, run_id: str) -> str:
    """Return the explicit omission marker for ``segment`` in run ``run_id``."""
    return MARKER_TEMPLATE.format(id=segment.id, sha=segment.sha256, run_id=run_id)


def _reparse(native: Any, request: ContextRequest) -> ContextRequest:
    if request.fmt == "anthropic":
        return from_anthropic(native, request.raw)
    if request.fmt == "openai":
        return from_openai(native, request.raw)
    return from_sdd_prompt(native, request.raw)


def _find_block(request: ContextRequest, path: tuple[int, ...]) -> Any:
    for message in request.messages:
        for block in message.blocks:
            if block.path == path:
                return block
    return None


def _replaced(text: str, segments: list[Any], run_id: str) -> str | None:
    for segment in sorted(segments, key=lambda item: item.start, reverse=True):
        if text[segment.start:segment.end] != segment.text:
            return None
        ending = "\n" if segment.text.endswith("\n") else ""
        text = text[:segment.start] + marker(segment, run_id) + ending + text[segment.end:]
    return text


def _write_text(target: dict[str, Any], field: str, block: Any, text: str) -> bool:
    value = target.get(field)
    if isinstance(value, str):
        target[field] = text
        return True
    if isinstance(value, list) and block.text_item is not None:
        item = value[block.text_item]
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            item["text"] = text
            return True
    return False


def _apply_json(native: Any, request: ContextRequest, path: tuple[int, ...], block: Any, text: str) -> bool:
    if request.fmt == "anthropic":
        message = native["messages"][path[0]]
        if isinstance(message.get("content"), str):
            if block.type != "text":
                return False
            message["content"] = text
            return True
        target = message["content"][path[1]]
        if block.type == "tool_result":
            return _write_text(target, "content", block, text)
        if block.type == "text":
            target["text"] = text
            return True
        return False
    target = native["input"][path[0]]
    if block.type == "tool_result":
        return _write_text(target, "output", block, text)
    if block.type == "text":
        return _write_text(target, "content", block, text)
    return False


def rebuild(request: ContextRequest, omissions: list[Any], run_id: str, protected: list[Any] | tuple = ()) -> tuple[ContextRequest, list[str]]:
    """Return ``(rebuilt, [])`` or ``(request, [REBUILD_INTEGRITY_FAILED])``; never raises for bad input."""
    grouped: dict[tuple[int, ...], list[Any]] = {}
    for segment in omissions:
        grouped.setdefault(segment.path, []).append(segment)
    try:
        native = copy.deepcopy(request.native)
        if request.fmt == "sdd-prompt":
            for path in sorted(grouped, key=lambda item: item[1], reverse=True):
                block = _find_block(request, path)
                text = _replaced(block.text, grouped[path], run_id) if block is not None else None
                if text is None:
                    return request, [INTEGRITY_FAILED]
                offset = block.data["offset"]
                native = native[:offset] + text + native[offset + len(block.text):]
        else:
            for path, segments in grouped.items():
                block = _find_block(request, path)
                if block is None or block.type not in ("tool_result", "text"):
                    return request, [INTEGRITY_FAILED]
                text = _replaced(block.text, segments, run_id)
                if text is None or not _apply_json(native, request, path, block, text):
                    return request, [INTEGRITY_FAILED]
        rebuilt = _reparse(native, request)
    except (KeyError, IndexError, TypeError, AttributeError):
        return request, [INTEGRITY_FAILED]
    errors = check_integrity(request, rebuilt, protected, set(grouped))
    return (request, errors) if errors else (rebuilt, [])


def _without(value: Any, field: str) -> Any:
    if not isinstance(value, dict):
        return value
    return {key: item for key, item in value.items() if key != field}


def _same_block_except_text(left: Any, right: Any, fmt: str) -> bool:
    if not isinstance(left.data, dict) or not isinstance(right.data, dict):
        return False
    field = {"tool_result": "output" if fmt == "openai" else "content", "text": "text" if fmt == "anthropic" else "content"}[left.type]
    if _without(left.data, field) != _without(right.data, field):
        return False
    before, after = left.data.get(field), right.data.get(field)
    if isinstance(before, list):
        if not isinstance(after, list) or len(before) != len(after):
            return False
        for index, (old, new) in enumerate(zip(before, after)):
            if index == left.text_item:
                if _without(old, "text") != _without(new, "text"):
                    return False
            elif old != new:
                return False
    return True


def _check_messages(original: ContextRequest, rebuilt: ContextRequest, omitted: set) -> bool:
    if len(original.messages) != len(rebuilt.messages):
        return False
    source_messages = original.native["messages"] if original.fmt == "anthropic" else None
    target_messages = rebuilt.native["messages"] if original.fmt == "anthropic" else None
    for index, (before, after) in enumerate(zip(original.messages, rebuilt.messages)):
        if before.role != after.role or len(before.blocks) != len(after.blocks):
            return False
        if source_messages is not None and _without(source_messages[index], "content") != _without(target_messages[index], "content"):
            return False
        for left, right in zip(before.blocks, after.blocks):
            if left.type != right.type or left.path != right.path:
                return False
            if left.path in omitted:
                if left.type == "text" and source_messages is not None and isinstance(source_messages[index].get("content"), str):
                    continue
                if not _same_block_except_text(left, right, original.fmt):
                    return False
            elif left.data != right.data or left.text != right.text:
                return False
    return True


def _check_sections(original: ContextRequest, rebuilt: ContextRequest, omitted: set) -> bool:
    before, after = original.messages[0].blocks, rebuilt.messages[0].blocks
    if len(before) != len(after):
        return False
    for left, right in zip(before, after):
        if left.text.split("\n", 1)[0] != right.text.split("\n", 1)[0]:
            return False
        if left.path not in omitted and left.text != right.text:
            return False
    return True


def check_integrity(original: ContextRequest, rebuilt: ContextRequest, protected: list[Any] | tuple = (), omitted: set | None = None) -> list[str]:
    """Verify that only omitted text ranges changed and every protected segment is byte-identical."""
    omitted = omitted if omitted is not None else set()
    try:
        valid = _check_sections(original, rebuilt, omitted) if original.fmt == "sdd-prompt" else _check_messages(original, rebuilt, omitted)
    except (KeyError, IndexError, TypeError, AttributeError):
        valid = False
    if not valid:
        return [INTEGRITY_FAILED]
    for segment in protected:
        block = _find_block(rebuilt, segment.path)
        if block is None:
            return [INTEGRITY_FAILED]
        # Offsets shift after an earlier omission in the same field; the text must survive intact.
        intact = segment.text in block.text if segment.path in omitted else block.text[segment.start:segment.end] == segment.text
        if not intact:
            return [INTEGRITY_FAILED]
    return []
