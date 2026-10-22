#!/usr/bin/env python3
"""Read-only observability queries over the SDD runtime stores: structured JSON reports and event streams.

    obs_cli.py report [sources] [--since-days N] [--prices PRICES.json] [--output report.json]
    obs_cli.py events [sources] [--trace ID] [--run ID] [--stage S] [--type T] [--limit N]
    obs_cli.py prices [--prices PRICES.json]

Sources (repeatable): --loop-store DIR, --journal-history DIR, --journal FILE, --gate-store DIR,
--receipts FILE, --governor-report FILE, --context-state DIR, --hsm-events FILE (the local
supervisor's ``events.jsonl``). Nothing is written except ``--output``; every command prints one
JSON document (``events`` prints one JSON object per line). Exit 0 on success, 1 on a usage error.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import resource
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

RUNTIME = Path(__file__).resolve().parent
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

import obs_metrics  # noqa: E402
import obs_tracer  # noqa: E402
from obs_model import event, load_prices, number, tokens, trace_id  # noqa: E402

MAX_HSM_BYTES = 64 * 1024 * 1024


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _log_time(value: Any, fallback: Any) -> float | None:
    """The Hermes log line's own local time (``YYYY-MM-DD HH:MM:SS,mmm``); ``t`` is only when the supervisor collected it."""
    if isinstance(value, str):
        try:
            return datetime.datetime.strptime(value, "%Y-%m-%d %H:%M:%S,%f").timestamp()
        except ValueError:
            pass
    return number(fallback)


def _hsm_line(item: dict[str, Any]) -> dict[str, Any] | None:
    kind, sid = item.get("k"), item.get("sid") or item.get("session_id")
    at = _log_time(item.get("ts"), item.get("t"))
    if kind == "api_call":
        inp, cached = _count(item.get("in")), _count(item.get("cache_read")) or 0
        uncached = inp - cached if inp is not None and inp >= cached else inp
        record = tokens("MEASURED", "hermes_log", input_tokens=uncached, output_tokens=_count(item.get("out")),
                        cache_read_tokens=cached)
        return event("MODEL_CALL", at, source="hsm", trace_id=trace_id("session", sid), session_id=sid, agent_id=sid,
                     attrs={"model": item.get("model"), "provider": item.get("provider"),
                            "duration_s": number(item.get("latency_s"), 0), "tokens": record})
    if kind == "tool_call":
        return event("TOOL_CALL", at, source="hsm", trace_id=trace_id("session", sid), session_id=sid, agent_id=sid,
                     attrs={"tool": item.get("tool"), "result": item.get("res"), "duration_s": number(item.get("dur_s"), 0)})
    if kind == "delegation":
        origin = item.get("origin_session") or item.get("parent_session_id")
        started, finished = number(item.get("dispatched_at")), number(item.get("completed_at"))
        duration = round(finished - started, 3) if started is not None and finished is not None and finished >= started else None
        return event("SUBAGENT", started, source="hsm", trace_id=trace_id("session", origin), session_id=origin,
                     agent_id=item.get("delegation_id"), parent_agent_id=origin,
                     attrs={"state": item.get("state"), "role": item.get("role"), "model": item.get("model"),
                            "is_batch": item.get("is_batch"), "duration_s": duration})
    return None


def hsm_events(path: Path | str) -> list[dict[str, Any]]:
    """Integrate the local supervisor (hsm) event log: model calls, tool calls and delegations.

    Hermes logs report ``in`` including cached prompt tokens; the uncached part is ``in - cache_read``.
    A line that is not a JSON object, or that cannot be read, is skipped and counted in one
    ``SOURCE_UNREADABLE`` event; one bad line never hides the rest.
    """
    location = Path(path)
    try:
        if location.is_symlink() or not location.is_file() or location.stat().st_size > MAX_HSM_BYTES:
            raise OSError("not a bounded regular file")
        lines = location.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        return [event("SOURCE_UNREADABLE", None, source="hsm", attrs={"error": type(error).__name__})]
    out, bad = [], 0
    for line in lines:
        try:
            item = json.loads(line)
            built = _hsm_line(item) if isinstance(item, dict) else None
        except (Exception, RecursionError):  # noqa: BLE001 - hostile line
            item, built = None, None
        if not isinstance(item, dict):
            bad += 1
        elif built is not None:
            out.append(built)
    if bad:
        out.append(event("SOURCE_UNREADABLE", None, source="hsm", attrs={"error": "LINES_UNREADABLE", "lines": bad}))
    return out


def _sources(args: argparse.Namespace) -> list[dict[str, Any]]:
    events = obs_tracer.collect(loop_store=args.loop_store, journal_history=args.journal_history, journals=args.journal,
                                gate_stores=args.gate_store, receipts=args.receipts, governor_reports=args.governor_report,
                                context_state=args.context_state)
    for path in args.hsm_events:
        events.extend(hsm_events(path))
    if args.since_days is not None:
        cutoff = time.time() - args.since_days * 86400
        events = [item for item in events if item["timestamp"] is None or item["timestamp"] >= cutoff]
    events.sort(key=lambda item: (item["timestamp"] is None, item["timestamp"] or 0.0))
    return events


def write_private(path: Path, data: bytes) -> None:
    """Write ``data`` atomically with mode 0600, never through a symlink."""
    if path.is_symlink():
        raise OSError("refusing to write through a symlink")
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".obs-")
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _report(args: argparse.Namespace) -> int:
    started, cpu = time.perf_counter(), time.process_time()
    events = _sources(args)
    prices = load_prices(args.prices)
    overhead = {"wall_ms": round((time.perf_counter() - started) * 1000, 1), "cpu_ms": round((time.process_time() - cpu) * 1000, 1),
                "max_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // (1024 if sys.platform == "darwin" else 1),
                "llm_calls": 0}
    value = obs_metrics.report(events, prices, overhead=overhead)
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1, allow_nan=False).encode("utf-8") + b"\n"
    if args.output:
        write_private(Path(args.output), data)
        print(json.dumps({"status": "WRITTEN", "output": str(args.output), "events": value["events"]}))
    else:
        sys.stdout.buffer.write(data)
    return 0


def _events(args: argparse.Namespace) -> int:
    shown = 0
    for item in _sources(args):
        if (args.trace and item["trace_id"] != args.trace) or (args.run and item["run_id"] != args.run) \
                or (args.stage and item["stage"] != args.stage) or (args.type and item["event_type"] != args.type):
            continue
        print(json.dumps(item, ensure_ascii=False, sort_keys=True, allow_nan=False))
        shown += 1
        if args.limit and shown >= args.limit:
            break
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("report", "events"):
        command = sub.add_parser(name)
        for flag in ("--loop-store", "--journal-history", "--journal", "--gate-store", "--receipts", "--governor-report",
                     "--context-state", "--hsm-events"):
            command.add_argument(flag, action="append", default=[], type=Path)
        command.add_argument("--since-days", type=float)
        command.add_argument("--prices", type=Path)
        if name == "report":
            command.add_argument("--output", type=Path)
        else:
            command.add_argument("--trace")
            command.add_argument("--run")
            command.add_argument("--stage")
            command.add_argument("--type")
            command.add_argument("--limit", type=int, default=0)
    prices = sub.add_parser("prices")
    prices.add_argument("--prices", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "prices":
        table = load_prices(args.prices)
        print(json.dumps({key: table.get(key) for key in ("version", "sha256", "source", "error")} | {"models": sorted(table["models"])}))
        return 0
    return _report(args) if args.command == "report" else _events(args)


if __name__ == "__main__":
    raise SystemExit(main())
