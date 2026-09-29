#!/usr/bin/env python3
"""Hermes subagent_stop adapter for non-sensitive audit metadata."""
from __future__ import annotations

import sys
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))
from hook_runtime import run_subagent_hook, shell_main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(shell_main(run_subagent_hook))
