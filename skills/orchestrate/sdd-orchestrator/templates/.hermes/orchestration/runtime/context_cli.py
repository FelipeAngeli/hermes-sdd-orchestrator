"""Command line for the opt-in context engine: optimize, recover, compare, config, purge.

Every subcommand prints one JSON object. Exit status: 0 success, 2 the request was
left unchanged because of an error or a disabled engine, 1 a usage or input error.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from context_audit import purge, recover
from context_collector import collect
from context_config import DEFAULT_CONFIG_PATH, load_config
from context_engine import optimize, serialized
from context_policy import requirement_markers
from context_rebuilder import check_integrity

FORMATS = ("anthropic", "openai", "sdd-prompt")


def _emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False))


def offline_evaluator(payload: dict[str, Any]) -> dict[str, Any]:
    """Deterministic stand-in for Jev used only by ``compare``: every eligible passage may be omitted."""
    answers = {name: {"type": "noul", "noul": 0.0} for name in payload["questions"]}
    return {"status": "OK", "result": {"model": "offline-fake", "answers": answers, "usage": {"input_tokens": 0, "output_tokens": 0}}}


def _fixture_format(path: Path) -> str | None:
    """Detect a fixture's protocol from its top-level keys; unknown shapes are skipped."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("prompt"), str):
        return "sdd-prompt"
    if isinstance(value.get("input"), list) and value["input"]:
        return "openai"
    if isinstance(value.get("messages"), list) and value["messages"]:
        return "anthropic"
    return None


def _fixture_bytes(path: Path, fmt: str) -> bytes:
    raw = path.read_bytes()
    if fmt == "sdd-prompt":
        return json.loads(raw.decode("utf-8"))["prompt"].encode("utf-8")
    return raw


def compare(corpus_dir: str | Path, config: dict[str, Any], audit_dir: str | Path | None = None) -> dict[str, Any]:
    """Run each fixture through baseline (disabled), dry-run and apply with the offline evaluator."""
    rows = []
    with tempfile.TemporaryDirectory(prefix="context-compare-") as scratch:
        state = Path(audit_dir) if audit_dir else Path(scratch)
        for path in sorted(Path(corpus_dir).glob("*.json")):
            fmt = _fixture_format(path)
            if fmt is None:
                continue
            raw = _fixture_bytes(path, fmt)
            active = dict(config, enabled=True, evaluator="offline", trigger_estimated_tokens=0)
            active["stages"] = {"COMPARE": {"eligible_kinds": ["TOOL_RESULT", "SDD_SECTION"]}}
            options = {"evaluate": offline_evaluator, "audit_dir": state}
            baseline = optimize(raw, fmt, "COMPARE", "compare", dict(config, enabled=False), **options)
            dry = optimize(raw, fmt, "COMPARE", "compare", dict(active, dry_run=True, apply_enabled=False), **options)
            applied = optimize(raw, fmt, "COMPARE", "compare", dict(active, dry_run=False, apply_enabled=True), apply=True, **options)
            original = collect(raw, fmt)
            applied_bytes = serialized(applied["request"]) if applied["status"] == "APPLIED" else raw
            rebuilt = collect(applied_bytes, fmt)
            omitted = {tuple(int(part) for part in item.split("@", 1)[0].split(".")) for item in dry.get("would_omit", [])}
            rows.append({
                "fixture": path.name,
                "format": fmt,
                "baseline_status": baseline["status"],
                "baseline_tokens": baseline["metrics"]["input_tokens_before"] or applied["metrics"]["input_tokens_before"],
                "dry_run_status": dry["status"],
                "dry_run_equals_baseline": dry["request"] == baseline["request"],
                "would_omit": len(dry.get("would_omit", [])),
                "apply_status": applied["status"],
                "apply_tokens": applied["metrics"]["input_tokens_after"],
                "segments_omitted": applied["metrics"]["segments_omitted"],
                "segments_protected": applied["metrics"]["segments_protected"],
                "apply_integrity_ok": applied["status"] in ("APPLIED", "DRY_RUN") and check_integrity(original, rebuilt, (), omitted) == [],
                "requirements_preserved": requirement_markers(original) == requirement_markers(rebuilt),
            })
    return {"offline": True, "evaluator": "offline-fake (every eligible passage judged safe to omit)", "rows": rows}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="context_engine.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    optimize_cmd = sub.add_parser("optimize")
    optimize_cmd.add_argument("--input", required=True)
    optimize_cmd.add_argument("--format", required=True, choices=FORMATS)
    optimize_cmd.add_argument("--stage", required=True)
    optimize_cmd.add_argument("--task", required=True, help="text, or @file to read it")
    optimize_cmd.add_argument("--config")
    optimize_cmd.add_argument("--evaluator", choices=("none", "typesafe", "jev-ai"))
    optimize_cmd.add_argument("--env-file", default=".env")
    optimize_cmd.add_argument("--project-setup")
    optimize_cmd.add_argument("--audit-dir")
    optimize_cmd.add_argument("--output")
    optimize_cmd.add_argument("--apply", action="store_true")
    optimize_cmd.add_argument("--json", action="store_true")
    recover_cmd = sub.add_parser("recover")
    recover_cmd.add_argument("--run", required=True)
    recover_cmd.add_argument("--audit-dir", required=True)
    recover_cmd.add_argument("--output")
    recover_cmd.add_argument("--json", action="store_true")
    compare_cmd = sub.add_parser("compare")
    compare_cmd.add_argument("--corpus", required=True)
    compare_cmd.add_argument("--config")
    compare_cmd.add_argument("--audit-dir")
    compare_cmd.add_argument("--json", action="store_true")
    config_cmd = sub.add_parser("config")
    config_cmd.add_argument("--config")
    config_cmd.add_argument("--json", action="store_true")
    purge_cmd = sub.add_parser("purge")
    purge_cmd.add_argument("--audit-dir", required=True)
    purge_cmd.add_argument("--older-than-days", required=True, type=int)
    purge_cmd.add_argument("--json", action="store_true")
    return parser


def _optimize(args: argparse.Namespace, config: dict[str, Any] | None, error: str | None) -> int:
    try:
        raw = Path(args.input).read_bytes()
        task = Path(args.task[1:]).read_text(encoding="utf-8") if args.task.startswith("@") else args.task
    except (OSError, UnicodeDecodeError) as error:
        _emit({"status": "ERROR", "reason": "INPUT_UNREADABLE", "detail": type(error).__name__})
        return 1
    result = optimize(raw, args.format, args.stage, task, config, apply=args.apply, audit_dir=args.audit_dir,
                      env_file=args.env_file, evaluator=args.evaluator, project_setup=args.project_setup)
    forwarded = serialized(result["request"]) if result["status"] == "APPLIED" else raw
    if args.output:
        Path(args.output).write_bytes(forwarded)
    report = dict(result)
    if config is None:
        report["config_error"] = error
    if args.output:
        report.pop("request")
    _emit(report)
    return 0 if result["status"] in ("APPLIED", "DRY_RUN", "SKIPPED_BELOW_TRIGGER") else 2


def main(argv: list[str] | None = None) -> int:
    """Run one subcommand and return its exit status."""
    args = _parser().parse_args(argv)
    config, error = load_config(args.config or DEFAULT_CONFIG_PATH) if hasattr(args, "config") else (None, None)
    try:
        if args.command == "config":
            _emit(config if config else {"status": "ERROR", "reason": error})
            return 0 if config else 1
        if args.command == "optimize":
            return _optimize(args, config, error)
        if args.command == "recover":
            raw = recover(args.audit_dir, args.run)
            if args.output:
                Path(args.output).write_bytes(raw)
                _emit({"status": "RECOVERED", "run_id": args.run, "bytes": len(raw)})
            else:
                sys.stdout.buffer.write(raw)
            return 0
        if args.command == "purge":
            _emit({"status": "PURGED", "removed": purge(args.audit_dir, args.older_than_days)})
            return 0
        if not config:
            _emit({"status": "ERROR", "reason": error})
            return 1
        _emit(compare(args.corpus, config, args.audit_dir))
        return 0
    except (OSError, ValueError) as failure:
        _emit({"status": "ERROR", "reason": str(failure) if isinstance(failure, ValueError) else type(failure).__name__})
        return 1
