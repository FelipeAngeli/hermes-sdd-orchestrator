"""Lexical helpers of the STATE.md subset parser in ``state_format``.

They type scalars, unquote strings and read inline-collection tokens. None of them
raises: structural errors stay in ``state_format``, which re-exports these names.
"""

from __future__ import annotations

from typing import Any, Tuple


def _read_flow_token(text: str, index: int, stop: str) -> Tuple[str, int]:
    start = index
    in_single = in_double = False
    while index < len(text):
        char = text[index]
        if char == '"' and not in_single:
            in_double = not in_double
        elif char == "'" and not in_double:
            in_single = not in_single
        elif char in stop and not in_single and not in_double:
            break
        index += 1
    return text[start:index], index


def _skip_space(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t":
        index += 1
    return index


def _parse_scalar(token: str) -> Any:
    token = token.strip()
    if not token:
        return None
    if (token.startswith('"') and token.endswith('"') and len(token) >= 2) or (
        token.startswith("'") and token.endswith("'") and len(token) >= 2
    ):
        return _unquote(token)
    lowered = token.lower()
    if lowered in {"null", "~"}:
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        pass
    return token


def _unquote(token: str) -> str:
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        inner = token[1:-1]
        if token[0] == '"':
            return inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner.replace("''", "'")
    return token
