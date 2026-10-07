#!/usr/bin/env python3
"""Hermes post_llm_call observer: append each turn of a bound workspace to the wiki's session transcript."""
from __future__ import annotations

import sys
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))
from wiki_journal import hook_main, record_turn_event  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(hook_main(record_turn_event))
