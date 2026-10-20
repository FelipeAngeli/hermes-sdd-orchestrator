"""Protocol adapters for the opt-in context engine."""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any


# Anthropic uses ``text`` parts; OpenAI Responses uses ``input_text`` and ``output_text``.
TEXT_PART_TYPES = ("text", "input_text", "output_text")


@dataclass
class Block:
    """A normalized native content block."""
    type: str
    data: Any
    path: tuple[int, ...]
    text: str = ""
    text_item: int | None = None


@dataclass
class Message:
    """A normalized message or Responses input item."""
    role: str
    blocks: list[Block]
    path: tuple[int, ...]


@dataclass
class ContextRequest:
    """A lossless wrapper around one supported input format."""
    fmt: str
    native: Any
    messages: list[Message]
    raw: bytes


def canonical_json(value: Any) -> bytes:
    """Return deterministic UTF-8 JSON for hashing and comparisons."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def estimate_tokens(value: int | bytes | bytearray, bytes_per_token: int) -> int:
    """Estimate tokens from UTF-8 bytes using the configured divisor."""
    size = len(value) if isinstance(value, (bytes, bytearray)) else value
    return math.ceil(size / bytes_per_token)


def _text(value: Any) -> tuple[str, int | None]:
    if isinstance(value, str):
        return value, None
    if isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, dict) and item.get("type") in TEXT_PART_TYPES and isinstance(item.get("text"), str):
                return item["text"], index
    return "", None


def from_anthropic(native: dict[str, Any], raw: bytes = b"") -> ContextRequest:
    """Normalize an Anthropic Messages request without mutating it."""
    messages = []
    for index, message in enumerate(native.get("messages", [])):
        content = message.get("content", []) if isinstance(message, dict) else []
        items = [{"type": "text", "text": content}] if isinstance(content, str) else content
        blocks = []
        for block_index, block in enumerate(items if isinstance(items, list) else []):
            path = (index, block_index)
            if not isinstance(block, dict):
                blocks.append(Block("opaque", block, path))
            elif block.get("type") == "text" and isinstance(block.get("text"), str):
                blocks.append(Block("text", block, path, block["text"]))
            elif block.get("type") == "tool_use" and isinstance(block.get("id"), str) and isinstance(block.get("name"), str):
                blocks.append(Block("tool_use", block, path))
            elif block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str):
                text, item = _text(block.get("content"))
                blocks.append(Block("tool_result", block, path, text, item))
            else:
                blocks.append(Block("opaque", block, path))
        messages.append(Message(message.get("role", ""), blocks, (index,)))
    return ContextRequest("anthropic", native, messages, raw)


def from_openai(native: dict[str, Any], raw: bytes = b"") -> ContextRequest:
    """Normalize an OpenAI Responses request without guessing missing roles."""
    messages = []
    for index, item in enumerate(native.get("input", [])):
        role = item.get("role", "") if isinstance(item, dict) else ""
        block = Block("opaque", item, (index,))
        if isinstance(item, dict) and item.get("type") == "message":
            text, text_item = _text(item.get("content"))
            block = Block("text", item, (index,), text, text_item)
        elif isinstance(item, dict) and item.get("type") == "function_call" and isinstance(item.get("call_id"), str):
            block = Block("tool_use", item, (index,))
        elif isinstance(item, dict) and item.get("type") == "function_call_output" and isinstance(item.get("call_id"), str):
            text, text_item = _text(item.get("output"))
            block = Block("tool_result", item, (index,), text, text_item)
        messages.append(Message(role, [block], (index,)))
    return ContextRequest("openai", native, messages, raw)


def from_sdd_prompt(native: str, raw: bytes = b"") -> ContextRequest:
    """Normalize a Markdown prompt while retaining every heading verbatim."""
    starts = [match.start() for match in re.finditer(r"(?m)^#{1,6}\s+.*$", native)]
    if not starts or starts[0] != 0:
        starts.insert(0, 0)
    blocks = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(native)
        blocks.append(Block("sdd_section", {"offset": start}, (0, index), native[start:end]))
    return ContextRequest("sdd-prompt", native, [Message("user", blocks, (0,))], raw)


def collect(data: bytes, fmt: str) -> ContextRequest:
    """Parse raw bytes into a context request, raising only for invalid input."""
    if fmt == "sdd-prompt":
        return from_sdd_prompt(data.decode("utf-8"), data)
    native = json.loads(data.decode("utf-8"))
    if not isinstance(native, dict):
        raise ValueError("REQUEST_NOT_OBJECT")
    if fmt == "anthropic":
        return from_anthropic(native, data)
    if fmt == "openai":
        return from_openai(native, data)
    raise ValueError("FORMAT_INVALID")


def to_native(request: ContextRequest) -> Any:
    """Return the native request object currently represented by ``request``."""
    return request.native
