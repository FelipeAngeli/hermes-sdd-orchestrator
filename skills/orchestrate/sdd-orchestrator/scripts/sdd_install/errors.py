"""Installer error type and strict JSON helpers."""
from __future__ import annotations

import json


class InstallError(RuntimeError):
    """A refusal reported as ``BLOCKED``.

    ``next_step``/``next_command`` and ``details`` (extra report keys) are
    optional so a caller is never left guessing how to proceed.
    """

    def __init__(
        self,
        reason: str,
        *,
        next_step: str | None = None,
        next_command: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(reason)
        self.next_step = next_step
        self.next_command = next_command
        self.details = details or {}


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _load_unique_json(value: str) -> object:
    return json.loads(value, object_pairs_hook=_unique_json_object)
