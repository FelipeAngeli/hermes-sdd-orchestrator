#!/usr/bin/env python3
"""Hermes pre_tool_call adapter for the security policy engine (fail closed)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))


def main() -> int:
    try:
        from policy_cli import run_policy_hook

        result = run_policy_hook(json.load(sys.stdin))
    except Exception as error:  # an unreadable payload or a broken install still blocks
        result = {"action": "block", "message": f"Security policy failed closed: {error.__class__.__name__}."}
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
