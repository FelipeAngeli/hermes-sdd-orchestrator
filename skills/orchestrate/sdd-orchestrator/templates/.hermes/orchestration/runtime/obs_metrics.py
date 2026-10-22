"""Aggregate observability events into the indicators of the AuditReporter.

Trackers (pure functions over the event list from ``obs_tracer.collect``):

* TokenTracker   - tokens per SDD stage, kept per method (MEASURED / ESTIMATED / SIMULATED);
* CostEstimator  - list-price USD from the versioned price table, only where model and token split are known;
* LatencyTracker - durations of node attempts, actions, gate commands and the Jev evaluator;
* ProgressTracker - completed / running / blocked work, first-attempt success, retries by cause,
  correction loops, recovery after interruption, concurrency and policy blocks;
* JEVMetrics     - gross and net context saving, evaluator cost and latency, cache reuse.

Nothing here reads files or calls a model.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from obs_model import add_tokens, estimate_cost, percentile

STAGE_NONE = "NO_STAGE"


def _tokens_of(item: dict[str, Any]) -> list[dict[str, Any]]:
    attrs = item["attrs"]
    return [value for value in (attrs.get("tokens"), attrs.get("evaluator_tokens")) if isinstance(value, dict)]


def token_tracker(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_stage: dict[str, dict[str, Any]] = defaultdict(dict)
    by_agent: dict[str, dict[str, Any]] = {"agents": {}, "jev": {}}
    for item in events:
        for record in _tokens_of(item):
            add_tokens(by_stage[item["stage"] or STAGE_NONE], record)
            add_tokens(by_agent["jev" if item["agent_id"] == "jev" else "agents"], record)
    return {"by_stage": dict(by_stage), "by_actor": by_agent,
            "note": "Methods are never summed together; unknown_fields counts values the source did not report."}


def cost_estimator(events: list[dict[str, Any]], prices: dict[str, Any]) -> dict[str, Any]:
    by_stage: dict[str, dict[str, Any]] = defaultdict(lambda: {"usd": 0.0, "priced": 0, "unpriced": 0, "reasons": Counter()})
    by_actor: dict[str, dict[str, Any]] = defaultdict(lambda: {"usd": 0.0, "priced": 0, "unpriced": 0, "reasons": Counter()})
    reported: dict[str, float] = defaultdict(float)
    for item in events:
        model = item["attrs"].get("model")
        for record in _tokens_of(item):
            actor = "jev" if item["agent_id"] == "jev" else "agents"
            cost = estimate_cost(record, record.get("model") or (model if actor == "agents" else "jev"), prices)
            for bucket in (by_stage[item["stage"] or STAGE_NONE], by_actor[actor]):
                if cost["usd"] is None:
                    bucket["unpriced"] += 1
                    bucket["reasons"][cost["reason"]] += 1
                else:
                    bucket["priced"] += 1
                    bucket["usd"] += cost["usd"]
            if isinstance(record.get("cost_usd"), (int, float)):
                reported[actor] += record["cost_usd"]

    def finish(table: dict[str, dict[str, Any]]) -> dict[str, Any]:
        return {key: {"estimated_usd": round(value["usd"], 6), "priced_records": value["priced"],
                      "unpriced_records": value["unpriced"], "unpriced_reasons": dict(value["reasons"])} for key, value in table.items()}

    return {"price_table_version": prices.get("version"), "price_table_sha256": prices.get("sha256"),
            "method": "ESTIMATED list price; never a bill", "by_stage": finish(by_stage), "by_actor": finish(by_actor),
            "reported_by_provider_usd": {key: round(value, 6) for key, value in reported.items()}}


def latency_tracker(events: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[float]] = defaultdict(list)
    for item in events:
        attrs = item["attrs"]
        if item["event_type"] == "NODE_ATTEMPT" and attrs.get("duration_s") is not None:
            groups[f"stage:{item['stage'] or STAGE_NONE}"].append(attrs["duration_s"])
        elif item["event_type"] == "ACTION" and attrs.get("duration_s") is not None:
            groups[f"action:{item['stage'] or STAGE_NONE}"].append(attrs["duration_s"])
        elif item["event_type"] == "JEV_DECISION" and attrs.get("elapsed_ms") is not None and attrs.get("jev_calls"):
            groups["evaluator:jev_decision"].append(attrs["elapsed_ms"] / 1000)
        elif item["event_type"] == "CONTEXT_OPTIMIZATION" and attrs.get("latency_ms") is not None:
            groups["evaluator:context_engine"].append(attrs["latency_ms"] / 1000)
        elif item["event_type"] == "TOOL_CALL" and attrs.get("duration_s") is not None:
            groups[f"tool:{attrs.get('tool')}"].append(attrs["duration_s"])
        elif item["event_type"] == "MODEL_CALL" and attrs.get("duration_s") is not None:
            groups[f"model:{attrs.get('model')}"].append(attrs["duration_s"])
    out: dict[str, Any] = {name: {"count": len(values), "total_s": round(sum(values), 3), "p50_s": percentile(values, 0.5),
                  "p95_s": percentile(values, 0.95), "max_s": round(max(values), 3)} for name, values in groups.items()}
    evaluator = [value for name, values in groups.items() if name.startswith("evaluator:") for value in values]
    out["evaluator_added_latency_s"] = round(sum(evaluator), 3)
    return out


def _intervals(events: list[dict[str, Any]]) -> list[tuple[float, float]]:
    spans = []
    for item in events:
        duration, start = item["attrs"].get("duration_s"), item["timestamp"]
        if item["event_type"] in ("NODE_ATTEMPT", "ACTION", "SUBAGENT") and start is not None and duration is not None:
            spans.append((start, start + duration))
    return spans


def concurrency(events: list[dict[str, Any]]) -> dict[str, Any]:
    spans = _intervals(events)
    if not spans:
        return {"max_parallel": None, "average_parallel": None, "busy_s": 0.0, "span_s": None}
    points = sorted([(start, 1) for start, _ in spans] + [(end, -1) for _, end in spans], key=lambda pair: (pair[0], pair[1]))
    current = peak = 0
    for _, delta in points:
        current += delta
        peak = max(peak, current)
    busy = sum(end - start for start, end in spans)
    span = max(end for _, end in spans) - min(start for start, _ in spans)
    return {"max_parallel": peak, "average_parallel": round(busy / span, 3) if span > 0 else None,
            "busy_s": round(busy, 3), "span_s": round(span, 3)}


def human_wait(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Seconds between a loop halt and the human decision that resolved it, per execution."""
    waits, open_waits = [], 0
    halted: dict[str, float | None] = {}
    for item in events:
        run = item["run_id"]
        if item["source"] != "loop_store" or run is None:
            continue
        if item["event_type"] == "LOOP_EXECUTION_HALTED":
            halted[run] = item["timestamp"]
        elif run in halted and item["attrs"].get("decision") and item["event_type"].startswith("LOOP_"):
            started = halted.pop(run)
            if started is not None and item["timestamp"] is not None:
                waits.append(max(0.0, item["timestamp"] - started))
    open_waits = len(halted)
    return {"resolved": len(waits), "total_s": round(sum(waits), 3), "p50_s": percentile(waits, 0.5),
            "max_s": round(max(waits), 3) if waits else None, "still_waiting": open_waits}


def progress_tracker(events: list[dict[str, Any]]) -> dict[str, Any]:
    nodes: dict[tuple[Any, Any], list[dict[str, Any]]] = defaultdict(list)
    loops, actions, blocks, retries = {}, Counter(), Counter(), Counter()
    corrections = 0
    for item in events:
        attrs, kind = item["attrs"], item["event_type"]
        if kind == "NODE_ATTEMPT":
            nodes[(item["run_id"], item["agent_id"])].append(attrs)
            if attrs.get("result") == "FAILED" or attrs.get("result") == "INTERRUPTED":
                retries[f"{attrs.get('failure_class')}:{attrs.get('failure_code')}"] += 1
        elif kind == "LOOP_SUMMARY":
            loops[item["run_id"]] = attrs
        elif kind == "LOOP_CORRECTION_APPLIED":
            corrections += 1
        elif kind == "LOOP_EXECUTION_HALTED":
            blocks[f"loop:{attrs.get('reason')}"] += 1
        elif kind == "ACTION":
            actions[attrs.get("status")] += 1
            if attrs.get("status") == "BLOCKED":
                blocks["journal:BLOCKED"] += 1
        elif kind == "GATE_DECISION" and attrs.get("verdict") == "BLOCKED":
            blocks[f"gate:{','.join(attrs.get('failed_criteria') or []) or 'BLOCKED'}"] += 1
        elif kind == "JEV_DECISION" and attrs.get("requires_review"):
            blocks["jev:REVIEW_REQUIRED"] += 1
    first_ok = sum(1 for attempts in nodes.values() if attempts and attempts[0].get("result") == "SUCCEEDED")
    interrupted = [attempts for attempts in nodes.values() if any(a.get("result") in ("INTERRUPTED", "RECONCILED") for a in attempts)]
    recovered = sum(1 for attempts in interrupted if attempts[-1].get("result") in ("SUCCEEDED", "RECONCILED"))
    statuses = Counter(summary.get("status") for summary in loops.values())
    durations = [summary["outcome_at"] - summary["created_at"] for summary in loops.values()
                 if isinstance(summary.get("outcome_at"), (int, float)) and isinstance(summary.get("created_at"), (int, float))]
    return {
        "loops": dict(statuses), "actions": dict(actions),
        "nodes": {"total": len(nodes), "first_attempt_success": first_ok,
                  "first_attempt_success_rate": round(first_ok / len(nodes), 3) if nodes else None},
        "retries_by_cause": dict(retries), "correction_loops": corrections,
        "correction_cycles_recorded": sum(summary.get("correction_cycles") or 0 for summary in loops.values()),
        "recovery_after_interruption": {"interrupted_nodes": len(interrupted), "recovered": recovered,
                                        "rate": round(recovered / len(interrupted), 3) if interrupted else None},
        "time_to_completion_s": {"p50": percentile(durations, 0.5), "max": round(max(durations), 3) if durations else None},
        "policy_blocks": dict(blocks),
    }


def jev_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    gross_applied = gross_dry = evaluator_in = evaluator_out = 0
    runs = applied_runs = cache_hits = cache_misses = 0
    calls = cached = review = decided = 0
    governance_in = governance_out = 0
    for item in events:
        attrs = item["attrs"]
        if item["event_type"] == "CONTEXT_OPTIMIZATION":
            runs += 1
            gross_dry += attrs.get("estimated_saving") or 0
            if attrs.get("effective_saving"):
                applied_runs += 1
                gross_applied += attrs["effective_saving"]
            usage = attrs.get("evaluator_tokens") or {}
            evaluator_in += usage.get("input_tokens") or 0
            evaluator_out += usage.get("output_tokens") or 0
            cache_hits += attrs.get("cache_hits") or 0
            cache_misses += attrs.get("cache_misses") or 0
        elif item["event_type"] == "JEV_DECISION":
            if attrs.get("jev_calls"):
                calls += 1
            elif attrs.get("provenance") == "CACHE":
                cached += 1
            usage = attrs.get("tokens") or {}
            governance_in += usage.get("input_tokens") or 0
            governance_out += usage.get("output_tokens") or 0
            review += 1 if attrs.get("disposition") == "REVIEW" or (attrs.get("review") or 0) > 0 else 0
            decided += 1 if attrs.get("disposition") == "DECIDED" or (attrs.get("status") == "DECIDED") else 0
    evaluator_total = evaluator_in + evaluator_out
    return {
        "context_engine": {
            "runs": runs, "applied_runs": applied_runs,
            "gross_saving_tokens": {"applied": gross_applied, "dry_run_estimate": gross_dry, "method": "ESTIMATED"},
            "evaluator_tokens": {"input": evaluator_in, "output": evaluator_out, "method": "MEASURED"},
            "net_saving_tokens": {"applied": gross_applied - evaluator_total, "dry_run_estimate": gross_dry - evaluator_total,
                                  "method": "ESTIMATED (estimated saving minus measured evaluator tokens)"},
            "cache": {"hits": cache_hits, "misses": cache_misses,
                      "hit_ratio": round(cache_hits / (cache_hits + cache_misses), 3) if cache_hits + cache_misses else None},
        },
        "governance": {"live_calls": calls, "cache_reuses": cached, "decided": decided, "review": review,
                       "evaluator_tokens": {"input": governance_in, "output": governance_out, "method": "MEASURED"},
                       "note": "Governance decisions save no prompt tokens; their tokens are pure overhead."},
    }


def report(events: list[dict[str, Any]], prices: dict[str, Any], *, overhead: dict[str, Any] | None = None) -> dict[str, Any]:
    """The AuditReporter output: one JSON object with every indicator and its provenance."""
    sources = Counter(item["source"] for item in events)
    unreadable = Counter(item["attrs"].get("error") for item in events if item["event_type"] == "SOURCE_UNREADABLE")
    return {
        "schema_version": 1, "events": len(events), "sources": dict(sources), "unreadable_sources": dict(unreadable),
        "tokens": token_tracker(events), "cost": cost_estimator(events, prices), "latency": latency_tracker(events),
        "progress": progress_tracker(events), "human_wait": human_wait(events), "concurrency": concurrency(events),
        "jev": jev_metrics(events), "overhead": overhead or {},
    }
