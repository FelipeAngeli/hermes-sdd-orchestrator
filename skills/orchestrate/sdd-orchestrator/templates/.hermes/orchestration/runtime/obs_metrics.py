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

from obs_model import add_tokens, estimate_cost, number, percentile

STAGE_NONE = "NO_STAGE"
#: Loop events that end a halt without a human decision (the wait is closed, not resolved).
WAIT_CLOSERS = ("LOOP_EXECUTION_CANCELLED", "LOOP_EXECUTION_COMPLETED", "LOOP_EXECUTION_FAILED")


def _n(value: Any) -> float:
    """A finite non-negative number for arithmetic; anything else counts as 0 (callers track unknowns separately)."""
    return number(value, 0) or 0


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
    def bucket() -> dict[str, Any]:
        return {"usd": 0.0, "priced": 0, "unpriced": 0, "reasons": Counter(), "partial": Counter()}

    by_stage: dict[str, dict[str, Any]] = defaultdict(bucket)
    by_actor: dict[str, dict[str, Any]] = defaultdict(bucket)
    for item in events:
        model = item["attrs"].get("model")
        for record in _tokens_of(item):
            actor = "jev" if item["agent_id"] == "jev" else "agents"
            cost = estimate_cost(record, model if actor == "agents" else "jev", prices)
            for target in (by_stage[item["stage"] or STAGE_NONE], by_actor[actor]):
                if cost["usd"] is None:
                    target["unpriced"] += 1
                    target["reasons"][cost["reason"]] += 1
                else:
                    target["priced"] += 1
                    target["usd"] += cost["usd"]
                    for component in cost["unknown_components"]:
                        target["partial"][component] += 1

    def finish(table: dict[str, dict[str, Any]]) -> dict[str, Any]:
        return {key: {"estimated_usd": round(value["usd"], 6), "priced_records": value["priced"],
                      "unpriced_records": value["unpriced"], "unpriced_reasons": dict(value["reasons"]),
                      "priced_without": dict(value["partial"]),
                      "is_lower_bound": bool(value["partial"]) or value["unpriced"] > 0} for key, value in table.items()}

    return {"price_table_version": prices.get("version"), "price_table_sha256": prices.get("sha256"),
            "method": "ESTIMATED list price; never a bill. priced_without counts records priced without an unknown component.",
            "by_stage": finish(by_stage), "by_actor": finish(by_actor)}


def latency_tracker(events: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[float]] = defaultdict(list)
    for item in events:
        attrs, kind = item["attrs"], item["event_type"]
        seconds = number(attrs.get("duration_s"), 0)
        if kind == "NODE_ATTEMPT" and seconds is not None:
            groups[f"stage:{item['stage'] or STAGE_NONE}"].append(seconds)
        elif kind == "ACTION" and seconds is not None:
            groups[f"action:{item['stage'] or STAGE_NONE}"].append(seconds)
        elif kind == "JEV_DECISION" and number(attrs.get("elapsed_ms"), 0) is not None and attrs.get("jev_calls"):
            groups["evaluator:jev_decision"].append(attrs["elapsed_ms"] / 1000)
        elif kind == "CONTEXT_OPTIMIZATION" and number(attrs.get("latency_ms"), 0) is not None:
            groups["evaluator:context_engine"].append(attrs["latency_ms"] / 1000)
        elif kind == "TOOL_CALL" and seconds is not None:
            groups[f"tool:{attrs.get('tool')}"].append(seconds)
        elif kind == "MODEL_CALL" and seconds is not None:
            groups[f"model:{attrs.get('model')}"].append(seconds)
    out: dict[str, Any] = {name: {"count": len(values), "total_s": round(sum(values), 3), "p50_s": percentile(values, 0.5),
                                  "p95_s": percentile(values, 0.95), "max_s": round(max(values), 3)} for name, values in groups.items()}
    evaluator = [value for name, values in groups.items() if name.startswith("evaluator:") for value in values]
    out["evaluator_added_latency_s"] = round(sum(evaluator), 3)
    return out


def _intervals(events: list[dict[str, Any]]) -> list[tuple[float, float]]:
    spans = []
    for item in events:
        duration, start = number(item["attrs"].get("duration_s"), 0), item["timestamp"]
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
    """Seconds from a loop halt to the human decision that resolved it, per execution.

    The earliest unresolved halt counts (a second halt does not restart the clock); a cancelled, completed
    or failed execution closes the wait without a decision (``closed_without_decision``).
    """
    waits: list[float] = []
    closed = 0
    halted: dict[str, float | None] = {}
    for item in events:
        run, kind = item["run_id"], item["event_type"]
        if item["source"] != "loop_store" or run is None:
            continue
        if kind == "LOOP_EXECUTION_HALTED":
            halted.setdefault(run, item["timestamp"])
        elif run in halted and item["attrs"].get("decision") and kind.startswith("LOOP_"):
            started = halted.pop(run)
            if started is not None and item["timestamp"] is not None:
                waits.append(max(0.0, item["timestamp"] - started))
        elif run in halted and kind in WAIT_CLOSERS:
            halted.pop(run)
            closed += 1
    return {"resolved": len(waits), "total_s": round(sum(waits), 3), "p50_s": percentile(waits, 0.5),
            "max_s": round(max(waits), 3) if waits else None, "still_waiting": len(halted), "closed_without_decision": closed}


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
                 if number(summary.get("outcome_at")) is not None and number(summary.get("created_at")) is not None]
    return {
        "loops": dict(statuses), "actions": dict(actions),
        "nodes": {"total": len(nodes), "first_attempt_success": first_ok,
                  "first_attempt_success_rate": round(first_ok / len(nodes), 3) if nodes else None},
        "retries_by_cause": dict(retries), "correction_loops": corrections,
        "correction_cycles_recorded": sum(_n(summary.get("correction_cycles")) for summary in loops.values()),
        "recovery_after_interruption": {"interrupted_nodes": len(interrupted), "recovered": recovered,
                                        "rate": round(recovered / len(interrupted), 3) if interrupted else None},
        "time_to_completion_s": {"p50": percentile(durations, 0.5), "max": round(max(durations), 3) if durations else None},
        "policy_blocks": dict(blocks),
    }


class _Usage:
    """Evaluator tokens summed per method, never across methods; calls with unreported usage are counted, never zeroed."""

    def __init__(self) -> None:
        self.unknown = 0
        self.by_method: dict[str, list[float]] = {}

    def add(self, record: Any) -> None:
        if not isinstance(record, dict):
            return
        if record.get("input_tokens") is None or record.get("output_tokens") is None:
            self.unknown += 1
        totals = self.by_method.setdefault(str(record.get("method")), [0, 0])
        totals[0] += _n(record.get("input_tokens"))
        totals[1] += _n(record.get("output_tokens"))

    @property
    def total(self) -> float | None:
        """Input plus output when one method is present; ``None`` when methods are mixed (they are never added)."""
        if len(self.by_method) > 1:
            return None
        return sum(sum(values) for values in self.by_method.values())

    def view(self) -> dict[str, Any]:
        single = len(self.by_method) <= 1
        (method, (inp, out)), = self.by_method.items() if len(self.by_method) == 1 else ((None, (0, 0)),)
        return {"input": inp if single else None, "output": out if single else None, "method": method if single else "MIXED",
                "by_method": {key: {"input": value[0], "output": value[1]} for key, value in self.by_method.items()},
                "calls_with_unknown_usage": self.unknown}


def jev_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    gross_applied = gross_dry = 0
    runs = applied_runs = cache_hits = cache_misses = 0
    calls = simulated = cached = review = decided = 0
    context, governance = _Usage(), _Usage()
    for item in events:
        attrs = item["attrs"]
        if item["event_type"] == "CONTEXT_OPTIMIZATION":
            runs += 1
            gross_dry += _n(attrs.get("estimated_saving"))
            if _n(attrs.get("effective_saving")):
                applied_runs += 1
                gross_applied += _n(attrs.get("effective_saving"))
            context.add(attrs.get("evaluator_tokens"))
            cache_hits += _n(attrs.get("cache_hits"))
            cache_misses += _n(attrs.get("cache_misses"))
        elif item["event_type"] == "JEV_DECISION":
            if attrs.get("jev_calls") and attrs.get("simulated"):
                simulated += 1
            elif attrs.get("jev_calls"):
                calls += 1
            elif attrs.get("provenance") == "CACHE":
                cached += 1
            governance.add(attrs.get("tokens"))
            review += 1 if attrs.get("disposition") == "REVIEW" or _n(attrs.get("review")) > 0 else 0
            decided += 1 if attrs.get("disposition") == "DECIDED" or attrs.get("status") == "DECIDED" else 0
    cost = context.total
    known = context.unknown == 0 and cost is not None
    return {
        "context_engine": {
            "runs": runs, "applied_runs": applied_runs,
            "gross_saving_tokens": {"applied": gross_applied, "dry_run_estimate": gross_dry, "method": "ESTIMATED"},
            "evaluator_tokens": context.view(),
            "net_saving_tokens": {"applied": gross_applied - cost if known else None,
                                  "dry_run_estimate": gross_dry - cost if known else None,
                                  "method": "ESTIMATED saving minus all evaluator tokens (dry runs included); null when any "
                                            "evaluator call reported no usage"},
            "cache": {"hits": cache_hits, "misses": cache_misses,
                      "hit_ratio": round(cache_hits / (cache_hits + cache_misses), 3) if cache_hits + cache_misses else None},
        },
        "governance": {"live_calls": calls, "simulated_calls": simulated, "cache_reuses": cached, "decided": decided, "review": review,
                       "evaluator_tokens": governance.view(),
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
