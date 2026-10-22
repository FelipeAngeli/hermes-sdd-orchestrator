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
from typing import Any, Callable, Iterable

RUNTIME = Path(__file__).resolve().parent
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

import obs_model  # noqa: E402
from obs_model import event, tokens, trace_id  # noqa: E402

MAX_EVENTS_PER_SOURCE = 100_000
#: A JSON store file or event log larger than this is not read (SOURCE_UNREADABLE).
MAX_FILE_BYTES = 64 * 1024 * 1024


def _epoch(value: Any) -> float | None:
    stamp = obs_model.number(value)
    if stamp is not None:
        return float(stamp)
    if isinstance(value, str) and value:
        try:
            return _dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _span(started: float | None, finished: float | None) -> float | None:
    return round(finished - started, 3) if started is not None and finished is not None and finished >= started else None


def _d(value: Any) -> dict[str, Any]:
    """``value`` if it is a mapping, else an empty one: store content is untrusted and may have any shape."""
    return value if isinstance(value, dict) else {}


def _l(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _regular(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise OSError(f"not a regular file: {path.name}")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise OSError(f"larger than {MAX_FILE_BYTES} bytes: {path.name}")


def read_bounded(path: Path, limit: int) -> bytes:
    """At most ``limit`` bytes of a regular file, read through one descriptor (a file that grows past it is refused)."""
    if path.is_symlink() or not path.is_file():
        raise OSError(f"not a regular file: {path.name}")
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise OSError(f"larger than {limit} bytes: {path.name}")
    return data


def _read_json(path: Path) -> Any:
    return json.loads(read_bounded(path, MAX_FILE_BYTES).decode("utf-8"))


def _unreadable(source: str, error: BaseException) -> dict[str, Any]:
    return event("SOURCE_UNREADABLE", None, source=source, attrs={"error": type(error).__name__})


def _children(root: Path, pattern: str) -> list[Path]:
    """``root/<dir>/<pattern>`` files, never following a symlinked directory or file."""
    if not root.is_dir() or root.is_symlink():
        return []
    found = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and not child.is_symlink():
            found.extend(path for path in sorted(child.glob(pattern)) if not path.is_symlink())
    return found[:MAX_EVENTS_PER_SOURCE]


def guarded(source: str, reader: Callable[[], list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Run one source reader; any failure (hostile shape, deep nesting, I/O) becomes one SOURCE_UNREADABLE event."""
    try:
        return reader()
    except (Exception, RecursionError) as error:  # noqa: BLE001 - one bad source must never hide the others
        return [_unreadable(source, error)]


# --------------------------------------------------------------------------- loop store

def loop_events(store: Path | str, execution_id: str) -> list[dict[str, Any]]:
    """Events of one loop execution: transitions from events.jsonl plus per-attempt spans."""
    return guarded("loop_store", lambda: _loop_events(Path(store) / execution_id, execution_id))


def _loop_line(line: bytes, index: int, nodes: dict[str, Any], ids: dict[str, Any]) -> dict[str, Any]:
    try:
        item = json.loads(line)
    except (ValueError, RecursionError):
        item = None
    if not isinstance(item, dict):
        return event("LOOP_EVENT_UNREADABLE", None, source="loop_store", attrs={"line": index + 1}, **ids)
    node_id = obs_model.code(item.get("node"))
    stage = _d(_d(nodes.get(node_id)).get("params")).get("stage") if node_id else None
    failure = _d(item.get("failure"))
    attrs = {key: item.get(key) for key in ("seq", "reason", "decision", "limit", "attempt", "delay", "cycle", "by")
             if isinstance(item.get(key), (str, int, float)) and not isinstance(item.get(key), bool)}
    if failure:
        attrs["failure_class"], attrs["failure_code"] = obs_model.code(failure.get("class")), obs_model.code(failure.get("code"))
    name = obs_model.code(item.get("event")) or "UNKNOWN"
    return event(f"LOOP_{name}", _epoch(item.get("at")), source="loop_store", agent_id=node_id, stage=stage, attrs=attrs, **ids)


def _loop_events(directory: Path, execution_id: str) -> list[dict[str, Any]]:
    execution, definition = _d(_read_json(directory / "execution.json")), _d(_read_json(directory / "definition.json"))
    ids = {"trace_id": trace_id("loop", execution_id), "run_id": execution_id, "loop_id": execution.get("loop_id"),
           "task_id": definition.get("ticket")}
    nodes = {node.get("id"): node for node in _l(definition.get("nodes")) if isinstance(node, dict) and isinstance(node.get("id"), str)}
    out: list[dict[str, Any]] = []
    raw_events = directory / "events.jsonl"
    if raw_events.exists() or raw_events.is_symlink():
        _regular(raw_events)
        with raw_events.open("rb") as handle:
            budget = MAX_FILE_BYTES  # bounded even when the file grows after _regular checked it
            for index in range(MAX_EVENTS_PER_SOURCE):
                line = handle.readline(budget + 1)
                budget -= len(line)
                if not line or budget < 0:
                    break
                if line.strip():
                    out.append(_loop_line(line, index, nodes, ids))
    for node_id, record in _d(execution.get("nodes")).items():
        spec = _d(nodes.get(node_id))
        params = _d(spec.get("params"))
        for attempt in _l(_d(record).get("attempts")):
            attempt = _d(attempt)
            started, finished = _epoch(attempt.get("started_at")), _epoch(attempt.get("finished_at"))
            method = attempt.get("tokens_method")
            token_record = tokens(method, "loop_handler", total_tokens=attempt.get("tokens")) if method in obs_model.METHODS else None
            failure = _d(attempt.get("failure"))
            out.append(event("NODE_ATTEMPT", started, source="loop_store", agent_id=node_id, stage=params.get("stage"),
                             attrs={"attempt": attempt.get("attempt"), "generation": attempt.get("generation"),
                                    "result": attempt.get("result"), "failure_class": obs_model.code(failure.get("class")),
                                    "failure_code": obs_model.code(failure.get("code")), "human_authorized": attempt.get("human_authorized"),
                                    "duration_s": _span(started, finished), "executor": params.get("executor"),
                                    "model": params.get("model"), "kind": spec.get("kind"), "tokens": token_record}, **ids))
    usage, outcome = _d(execution.get("usage")), _d(execution.get("outcome"))
    out.append(event("LOOP_SUMMARY", _epoch(execution.get("updated_at")), source="loop_store",
                     attrs={"status": execution.get("status"), "created_at": _epoch(execution.get("created_at")),
                            "node_runs": usage.get("node_runs"), "active_seconds": usage.get("active_seconds"),
                            "correction_cycles": usage.get("correction_cycles"), "tokens_total": usage.get("tokens"),
                            "tokens_method": usage.get("tokens_method"), "outcome": outcome.get("reason"),
                            "outcome_at": _epoch(outcome.get("at"))}, **ids))
    for decision in _l(execution.get("decisions")):
        decision = _d(decision)
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

def journal_event(journal: Any, source: str = "action_journal") -> dict[str, Any]:
    """One action. ``parent_agent_id`` here is the retry's parent action id, not a parent agent."""
    journal = _d(journal)
    action, process = _d(journal.get("action")), _d(journal.get("process"))
    artifact, commit = _d(journal.get("artifact")), _d(journal.get("state_commit"))
    started, finished = _epoch(process.get("started_at")), _epoch(process.get("finished_at"))
    ticket = action.get("ticket")
    incidents = [str(item).split(":", 1)[0] for item in _l(journal.get("incidents")) if isinstance(item, str)]
    return event("ACTION", started, source=source, trace_id=trace_id("ticket", ticket), run_id=action.get("id"), task_id=ticket,
                 stage=action.get("stage"), agent_id=action.get("executor"), parent_agent_id=action.get("parent_action_id"),
                 attrs={"status": action.get("status"), "attempt": action.get("attempt"), "retry_mode": action.get("retry_mode"),
                        "exit_code": process.get("exit_code"), "duration_s": _span(started, finished), "finished_at": finished,
                        "committed_at": _epoch(commit.get("committed_at")), "artifact_validation": artifact.get("validation_status"),
                        "incidents": incidents, "invalid_fields": len(_l(action.get("invalid_fields")))})


def journal_history_events(history: Path | str) -> list[dict[str, Any]]:
    """``<history>/<ticket>/<action>.json``; symlinked ticket directories and files are skipped."""
    return [item for path in _children(Path(history), "*.json")
            for item in guarded("action_journal_history", lambda path=path: [journal_event(_read_json(path), "action_journal_history")])]


# --------------------------------------------------------------------------- gates, Jev, context engine

def gate_events(gate_store: Path | str) -> list[dict[str, Any]]:
    """Gate decisions, only after the hash chain verifies (a tampered log yields one error event)."""
    import gate_evidence

    def read() -> list[dict[str, Any]]:
        out = []
        for record in gate_evidence.DecisionLog(gate_store).verified_records()[:MAX_EVENTS_PER_SOURCE]:
            record = _d(record)
            task = record.get("task_id")
            failed = [item.get("id") for item in _l(record.get("failed_criteria")) if isinstance(item, dict)]
            out.append(event("GATE_DECISION", _epoch(record.get("timestamp")), source="gate_decisions", trace_id=trace_id("ticket", task),
                             run_id=record.get("decision_id"), task_id=task, stage=record.get("stage"), agent_id=record.get("evaluator"),
                             attrs={"gate_id": record.get("gate_id"), "verdict": record.get("verdict"), "mode": record.get("mode"),
                                    "blocking": record.get("blocking"), "reused": record.get("reused"),
                                    "generation": record.get("generation"), "retryable": record.get("retryable"),
                                    "failed_criteria": failed}))
        return out
    return guarded("gate_decisions", read)


def _evaluator_tokens(calls: Any, usage: dict[str, Any], method: str) -> dict[str, Any] | None:
    """Evaluator usage for ``calls`` > 0. Usage the provider did not report stays unknown (``None`` fields), never zero."""
    if not obs_model.number(calls, 1):
        return None
    return obs_model.usage_tokens(method, "jev_usage", usage)


def receipt_event(receipt: Any, *, timestamp: float | None = None, method: str = "MEASURED") -> dict[str, Any]:
    """One Jev decision receipt. ``method`` is ``SIMULATED`` for the benchmark's offline stand-in."""
    receipt = _d(receipt)
    metrics, recommendation, outcome = _d(receipt.get("metrics")), _d(receipt.get("recommendation")), _d(receipt.get("outcome"))
    calls = metrics.get("jev_calls")
    ticket = receipt.get("ticket")
    return event("JEV_DECISION", timestamp, source="jev_receipts", trace_id=trace_id("ticket", ticket), run_id=receipt.get("decision_id"),
                 task_id=ticket, stage=_d(receipt.get("binding")).get("stage"), agent_id="jev",
                 attrs={"kind": receipt.get("kind"), "configured_mode": receipt.get("configured_mode"),
                        "effective_mode": receipt.get("effective_mode"), "provenance": recommendation.get("provenance"),
                        "disposition": recommendation.get("disposition"), "agreement": outcome.get("agreement"),
                        "applied": outcome.get("applied"), "requires_review": outcome.get("requires_review"),
                        "reason": outcome.get("reason"), "jev_calls": calls, "elapsed_ms": metrics.get("elapsed_ms"),
                        "request_bytes": metrics.get("request_bytes"), "tokens": _evaluator_tokens(calls, metrics, method),
                        "simulated": method == "SIMULATED", "billing_reported": bool(metrics.get("billing"))})


def governor_report_event(report: Any, *, timestamp: float | None = None) -> dict[str, Any]:
    """A ``semantic_governor decide`` report (LIVE_JEV or CACHE)."""
    report = _d(report)
    calls = 1 if report.get("provenance") == "LIVE_JEV" else 0
    summary, ticket = _d(report.get("summary")), report.get("ticket")
    return event("JEV_DECISION", timestamp, source="jev_reports", trace_id=trace_id("ticket", ticket), run_id=report.get("fingerprint"),
                 task_id=ticket, agent_id="jev",
                 attrs={"kind": "GOVERNANCE_BATCH", "provenance": report.get("provenance"), "status": report.get("status"),
                        "decided": summary.get("decided"), "review": summary.get("review"), "jev_calls": calls,
                        "tokens": _evaluator_tokens(calls, _d(report.get("usage")), "MEASURED"),
                        "billing_reported": bool(report.get("billing"))})


def context_run_events(state_dir: Path | str) -> list[dict[str, Any]]:
    """Context-engine runs under ``<state>/audit/<run>/metrics.json``. They carry no ticket, so no trace_id."""
    out = []
    for path in _children(Path(state_dir) / "audit", "metrics.json"):
        run = path.parent.name
        try:
            started: float | None = float(int(run.split("_", 1)[0], 16))
        except ValueError:
            started = None
        out.extend(guarded("context_engine", lambda path=path, run=run, started=started:
                           [context_metrics_event(_read_json(path), run_id=run, timestamp=started)]))
    return out


def context_metrics_event(metrics: Any, *, run_id: str | None, timestamp: float | None, stage: str | None = None,
                          task_id: str | None = None, method: str = "MEASURED") -> dict[str, Any]:
    metrics = _d(metrics)
    usage = {"input_tokens": metrics.get("evaluator_input_tokens"), "output_tokens": metrics.get("evaluator_output_tokens")}
    keys = ("input_tokens_before", "input_tokens_after", "estimated_saving", "effective_saving", "evaluator_calls",
            "cache_hits", "cache_misses", "segments_total", "segments_omitted", "segments_protected", "latency_ms")
    attrs: dict[str, Any] = {key: metrics.get(key) for key in keys}
    without = obs_model.number(metrics.get("evaluator_calls_without_usage"), 0)
    if without:  # the engine summed only the calls that reported usage; the rest are unknown, not free
        usage = {"input_tokens": None, "output_tokens": None}
    attrs["evaluator_tokens"] = _evaluator_tokens(metrics.get("evaluator_calls"), usage, method)
    attrs["evaluator_calls_without_usage"] = without
    attrs["simulated"] = method == "SIMULATED"
    attrs["requirements_preserved"] = _d(metrics.get("requirement_impact")).get("preserved")
    return event("CONTEXT_OPTIMIZATION", timestamp, source="context_engine", trace_id=trace_id("ticket", task_id) if task_id else None,
                 run_id=run_id, task_id=task_id, stage=stage, agent_id="jev", attrs=attrs)


def _file_events(source: str, path: Path, build: Callable[[Any, float], list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return guarded(source, lambda: build(_read_json(path), path.stat().st_mtime))


def collect(*, loop_store: Iterable[Path | str] = (), journal_history: Iterable[Path | str] = (), journals: Iterable[Path | str] = (),
            gate_stores: Iterable[Path | str] = (), receipts: Iterable[Path | str] = (), governor_reports: Iterable[Path | str] = (),
            context_state: Iterable[Path | str] = ()) -> list[dict[str, Any]]:
    """All events from the given sources, sorted by timestamp (events without one last). One bad source never hides another."""
    out: list[dict[str, Any]] = []
    for store in loop_store:
        out.extend(guarded("loop_store", lambda store=store: loop_store_events(store)))
    for history in journal_history:
        out.extend(guarded("action_journal_history", lambda history=history: journal_history_events(history)))
    for path in journals:
        out.extend(_file_events("action_journal", Path(path), lambda value, _: [journal_event(value)]))
    for store in gate_stores:
        out.extend(gate_events(store))
    for path in receipts:
        out.extend(_file_events("jev_receipts", Path(path), lambda value, mtime: [
            receipt_event(item, timestamp=mtime) for item in (value if isinstance(value, list) else [value])]))
    for path in governor_reports:
        out.extend(_file_events("jev_reports", Path(path), lambda value, mtime: [governor_report_event(value, timestamp=mtime)]))
    for state in context_state:
        out.extend(guarded("context_engine", lambda state=state: context_run_events(state)))
    out.sort(key=lambda item: (item["timestamp"] is None, item["timestamp"] or 0.0))
    return out


__all__ = ["collect", "loop_events", "journal_event", "gate_events", "receipt_event", "governor_report_event",
           "context_metrics_event", "obs_model"]
