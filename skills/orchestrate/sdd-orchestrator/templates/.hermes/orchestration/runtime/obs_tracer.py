"""ExecutionTracer: derive correlated observability events from the stores the runtime already writes.

Sources (all read-only; nothing here writes):

* loop store ``<store>/<execution_id>/execution.json`` + ``events.jsonl`` (``loop_store``);
* action-journal history ``<history>/<ticket>/<action_id>.json`` and a live ``ACTION_JOURNAL.json``;
* gate decision log ``<gate-store>/decisions.jsonl`` (hash chain verified before use);
* Jev decision receipts (``DECISION_RECEIPT_SCHEMA``) and ``semantic_governor`` reports;
* context-engine run metrics ``<state>/audit/<run>/metrics.json``.

Each reader returns events built by ``obs_model.event``; prompts, goals, quotes and paths are
dropped and remaining strings are redacted. A source that is missing or unreadable yields a
``SOURCE_UNREADABLE`` event instead of raising, so one bad store never hides the others.
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any, Iterable

RUNTIME = Path(__file__).resolve().parent
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

import obs_model  # noqa: E402
from obs_model import event, tokens, trace_id  # noqa: E402

MAX_EVENTS_PER_SOURCE = 100_000


def _epoch(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str) and value:
        try:
            return _dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _read_json(path: Path) -> Any:
    if path.is_symlink() or not path.is_file():
        raise OSError(f"not a regular file: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _unreadable(source: str, error: Exception) -> dict[str, Any]:
    return event("SOURCE_UNREADABLE", None, source=source, attrs={"error": type(error).__name__})


# --------------------------------------------------------------------------- loop store

def loop_events(store: Path | str, execution_id: str) -> list[dict[str, Any]]:
    """Events of one loop execution: transitions from events.jsonl plus per-attempt spans."""
    directory = Path(store) / execution_id
    try:
        execution = _read_json(directory / "execution.json")
        definition = _read_json(directory / "definition.json")
    except (OSError, ValueError) as error:
        return [_unreadable("loop_store", error)]
    loop_id, ticket = execution.get("loop_id"), definition.get("ticket")
    trace = trace_id("loop", execution_id)
    ids = {"trace_id": trace, "run_id": execution_id, "loop_id": loop_id, "task_id": ticket}
    nodes = {node.get("id"): node for node in definition.get("nodes") or [] if isinstance(node, dict)}
    out: list[dict[str, Any]] = []
    raw_events = directory / "events.jsonl"
    if raw_events.is_file() and not raw_events.is_symlink():
        for index, line in enumerate(raw_events.read_bytes().split(b"\n")):
            if index >= MAX_EVENTS_PER_SOURCE or not line.strip():
                continue
            try:
                item = json.loads(line)
            except ValueError:
                out.append(event("LOOP_EVENT_UNREADABLE", None, source="loop_store", attrs={"line": index + 1}, **ids))
                continue
            node_id = item.get("node")
            stage = (nodes.get(node_id) or {}).get("params", {}).get("stage") if node_id else None
            attrs = {key: item.get(key) for key in ("seq", "reason", "failure", "decision", "limit", "attempt", "delay", "cycle", "by")
                     if item.get(key) is not None}
            out.append(event(f"LOOP_{item.get('event', 'UNKNOWN')}", _epoch(item.get("at")), source="loop_store",
                             agent_id=node_id, stage=stage, attrs=attrs, **ids))
    for node_id, record in (execution.get("nodes") or {}).items():
        spec = nodes.get(node_id) or {}
        params = spec.get("params") or {}
        for attempt in record.get("attempts") or []:
            started, finished = _epoch(attempt.get("started_at")), _epoch(attempt.get("finished_at"))
            method = attempt.get("tokens_method")
            token_record = (tokens(method, "loop_handler", total_tokens=attempt.get("tokens"))
                            if method in ("MEASURED", "ESTIMATED") else None)
            failure = attempt.get("failure") or {}
            out.append(event("NODE_ATTEMPT", started, source="loop_store", agent_id=node_id, stage=params.get("stage"),
                             attrs={"attempt": attempt.get("attempt"), "generation": attempt.get("generation"),
                                    "result": attempt.get("result"), "failure_class": failure.get("class"),
                                    "failure_code": failure.get("code"), "human_authorized": attempt.get("human_authorized"),
                                    "duration_s": round(finished - started, 3) if started is not None and finished is not None else None,
                                    "executor": params.get("executor"), "model": params.get("model"), "kind": spec.get("kind"),
                                    "tokens": token_record}, **ids))
    usage = execution.get("usage") or {}
    out.append(event("LOOP_SUMMARY", _epoch(execution.get("updated_at")), source="loop_store",
                     attrs={"status": execution.get("status"), "created_at": _epoch(execution.get("created_at")),
                            "node_runs": usage.get("node_runs"), "active_seconds": usage.get("active_seconds"),
                            "correction_cycles": usage.get("correction_cycles"), "tokens_total": usage.get("tokens"),
                            "tokens_method": usage.get("tokens_method"),
                            "outcome": (execution.get("outcome") or {}).get("reason"),
                            "outcome_at": _epoch((execution.get("outcome") or {}).get("at"))}, **ids))
    for decision in execution.get("decisions") or []:
        out.append(event("HUMAN_DECISION", _epoch(decision.get("at")), source="loop_store", agent_id=decision.get("node"),
                         attrs={"decision": decision.get("decision"), "generation": decision.get("generation")}, **ids))
    return out


def loop_store_events(store: Path | str) -> list[dict[str, Any]]:
    root = Path(store)
    if not root.is_dir() or root.is_symlink():
        return []
    out: list[dict[str, Any]] = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and not child.is_symlink() and (child / "execution.json").is_file():
            out.extend(loop_events(root, child.name))
    return out


# --------------------------------------------------------------------------- action journal

def journal_event(journal: dict[str, Any], source: str = "action_journal") -> dict[str, Any]:
    action, process = journal.get("action") or {}, journal.get("process") or {}
    artifact, commit = journal.get("artifact") or {}, journal.get("state_commit") or {}
    started, finished = _epoch(process.get("started_at")), _epoch(process.get("finished_at"))
    ticket, action_id = action.get("ticket"), action.get("id")
    incidents = [str(item).split(":", 1)[0] for item in journal.get("incidents") or []]
    return event("ACTION", started, source=source, trace_id=trace_id("ticket", ticket), run_id=action_id, task_id=ticket,
                 stage=action.get("stage"), agent_id=action.get("executor"), parent_agent_id=action.get("parent_action_id"),
                 attrs={"status": action.get("status"), "attempt": action.get("attempt"), "retry_mode": action.get("retry_mode"),
                        "exit_code": process.get("exit_code"),
                        "duration_s": round(finished - started, 3) if started is not None and finished is not None else None,
                        "finished_at": finished, "committed_at": _epoch(commit.get("committed_at")),
                        "artifact_validation": artifact.get("validation_status"), "incidents": incidents,
                        "invalid_fields": len(action.get("invalid_fields") or [])})


def journal_history_events(history: Path | str) -> list[dict[str, Any]]:
    root = Path(history)
    if not root.is_dir() or root.is_symlink():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(root.glob("*/*.json"))[:MAX_EVENTS_PER_SOURCE]:
        try:
            out.append(journal_event(_read_json(path), "action_journal_history"))
        except (OSError, ValueError) as error:
            out.append(_unreadable("action_journal_history", error))
    return out


# --------------------------------------------------------------------------- gates, Jev, context engine

def gate_events(gate_store: Path | str) -> list[dict[str, Any]]:
    """Gate decisions, only after the hash chain verifies (a tampered log yields one error event)."""
    import gate_evidence

    log = gate_evidence.DecisionLog(gate_store)
    try:
        records = log.verified_records()
    except (gate_evidence.EvidenceError, OSError, ValueError) as error:
        return [_unreadable("gate_decisions", error)]
    out = []
    for record in records[:MAX_EVENTS_PER_SOURCE]:
        task = record.get("task_id")
        out.append(event("GATE_DECISION", _epoch(record.get("timestamp")), source="gate_decisions", trace_id=trace_id("ticket", task),
                         run_id=record.get("decision_id"), task_id=task, stage=record.get("stage"), agent_id=record.get("evaluator"),
                         attrs={"gate_id": record.get("gate_id"), "verdict": record.get("verdict"), "mode": record.get("mode"),
                                "blocking": record.get("blocking"), "reused": record.get("reused"), "generation": record.get("generation"),
                                "retryable": record.get("retryable"),
                                "failed_criteria": [item.get("id") for item in record.get("failed_criteria") or [] if isinstance(item, dict)]}))
    return out


def receipt_event(receipt: dict[str, Any], *, timestamp: float | None = None) -> dict[str, Any]:
    """One Jev decision receipt; tokens are MEASURED only when the provider reported them."""
    metrics, recommendation, outcome = receipt.get("metrics") or {}, receipt.get("recommendation") or {}, receipt.get("outcome") or {}
    called = metrics.get("jev_calls") == 1
    token_record = (tokens("MEASURED", "jev_usage", input_tokens=metrics.get("input_tokens"), output_tokens=metrics.get("output_tokens"))
                    if called and metrics.get("input_tokens") is not None else None)
    ticket = receipt.get("ticket")
    return event("JEV_DECISION", timestamp, source="jev_receipts", trace_id=trace_id("ticket", ticket), run_id=receipt.get("decision_id"),
                 task_id=ticket, stage=(receipt.get("binding") or {}).get("stage"), agent_id="jev",
                 attrs={"kind": receipt.get("kind"), "configured_mode": receipt.get("configured_mode"),
                        "effective_mode": receipt.get("effective_mode"), "provenance": recommendation.get("provenance"),
                        "disposition": recommendation.get("disposition"), "agreement": outcome.get("agreement"),
                        "applied": outcome.get("applied"), "requires_review": outcome.get("requires_review"),
                        "reason": outcome.get("reason"), "jev_calls": metrics.get("jev_calls"), "elapsed_ms": metrics.get("elapsed_ms"),
                        "request_bytes": metrics.get("request_bytes"), "tokens": token_record,
                        "billing_reported": bool(metrics.get("billing"))})


def governor_report_event(report: dict[str, Any], *, timestamp: float | None = None) -> dict[str, Any]:
    """A ``semantic_governor decide`` report (LIVE_JEV or CACHE)."""
    live = report.get("provenance") == "LIVE_JEV"
    usage = report.get("usage") if isinstance(report.get("usage"), dict) else {}
    token_record = tokens("MEASURED", "jev_usage", **usage) if live and usage else None
    summary = report.get("summary") or {}
    ticket = report.get("ticket")
    return event("JEV_DECISION", timestamp, source="jev_reports", trace_id=trace_id("ticket", ticket), run_id=report.get("fingerprint"),
                 task_id=ticket, agent_id="jev",
                 attrs={"kind": "GOVERNANCE_BATCH", "provenance": report.get("provenance"), "status": report.get("status"),
                        "decided": summary.get("decided"), "review": summary.get("review"), "jev_calls": 1 if live else 0,
                        "tokens": token_record, "billing_reported": bool(report.get("billing"))})


def context_run_events(state_dir: Path | str) -> list[dict[str, Any]]:
    """Context-engine runs: before/after estimates (ESTIMATED) and the evaluator's usage (MEASURED)."""
    root = Path(state_dir) / "audit"
    if not root.is_dir() or root.is_symlink():
        return []
    out = []
    for path in sorted(root.glob("*/metrics.json"))[:MAX_EVENTS_PER_SOURCE]:
        try:
            metrics = _read_json(path)
        except (OSError, ValueError) as error:
            out.append(_unreadable("context_engine", error))
            continue
        run = path.parent.name
        try:
            started = float(int(run.split("_", 1)[0], 16))
        except ValueError:
            started = None
        out.append(context_metrics_event(metrics, run_id=run, timestamp=started))
    return out


def context_metrics_event(metrics: dict[str, Any], *, run_id: str | None, timestamp: float | None, stage: str | None = None,
                          task_id: str | None = None) -> dict[str, Any]:
    evaluator = (tokens("MEASURED", "jev_usage", input_tokens=metrics.get("evaluator_input_tokens"),
                        output_tokens=metrics.get("evaluator_output_tokens")) if metrics.get("evaluator_calls") else None)
    keys = ("input_tokens_before", "input_tokens_after", "estimated_saving", "effective_saving", "evaluator_calls",
            "cache_hits", "cache_misses", "segments_total", "segments_omitted", "segments_protected", "latency_ms")
    attrs = {key: metrics.get(key) for key in keys}
    attrs["evaluator_tokens"] = evaluator
    attrs["requirements_preserved"] = (metrics.get("requirement_impact") or {}).get("preserved")
    return event("CONTEXT_OPTIMIZATION", timestamp, source="context_engine", trace_id=trace_id("ticket", task_id) if task_id else None,
                 run_id=run_id, task_id=task_id, stage=stage, agent_id="jev", attrs=attrs)


def collect(*, loop_store: Iterable[Path | str] = (), journal_history: Iterable[Path | str] = (), journals: Iterable[Path | str] = (),
            gate_stores: Iterable[Path | str] = (), receipts: Iterable[Path | str] = (), governor_reports: Iterable[Path | str] = (),
            context_state: Iterable[Path | str] = ()) -> list[dict[str, Any]]:
    """All events from the given sources, sorted by timestamp (events without one last)."""
    out: list[dict[str, Any]] = []
    for store in loop_store:
        out.extend(loop_store_events(store))
    for history in journal_history:
        out.extend(journal_history_events(history))
    for path in journals:
        try:
            out.append(journal_event(_read_json(Path(path))))
        except (OSError, ValueError) as error:
            out.append(_unreadable("action_journal", error))
    for store in gate_stores:
        out.extend(gate_events(store))
    for path in receipts:
        try:
            value = _read_json(Path(path))
            for receipt in value if isinstance(value, list) else [value]:
                out.append(receipt_event(receipt, timestamp=Path(path).stat().st_mtime))
        except (OSError, ValueError, AttributeError) as error:
            out.append(_unreadable("jev_receipts", error))
    for path in governor_reports:
        try:
            out.append(governor_report_event(_read_json(Path(path)), timestamp=Path(path).stat().st_mtime))
        except (OSError, ValueError, AttributeError) as error:
            out.append(_unreadable("jev_reports", error))
    for state in context_state:
        out.extend(context_run_events(state))
    out.sort(key=lambda item: (item["timestamp"] is None, item["timestamp"] or 0.0))
    return out


__all__ = ["collect", "loop_events", "journal_event", "gate_events", "receipt_event", "governor_report_event",
           "context_metrics_event", "obs_model"]
