#!/usr/bin/env python3
"""Hermes on_session_finalize observer: log the end of a session recorded in this project's wiki."""
from __future__ import annotations

import sys
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))
from wiki_journal import hook_main, record_session_end_event  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(hook_main(record_session_end_event))
