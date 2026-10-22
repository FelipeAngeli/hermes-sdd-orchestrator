#!/usr/bin/env python3
"""Reproducible A/B/C/D comparison of SDD configurations, measured with the observability engine.

Every scenario runs the same SDD delivery (SPECIFY → … → REVIEW through ``loop_sdd`` and the
real ``LoopEngine``) against the same scripted workload: per-stage token counts, one transient
failure in IMPLEMENT and one failed TEST verification that a correction fixes. Only the
configuration changes:

* ``A`` - current SDD: no Jev, no context optimization, default loop limits;
* ``B`` - Jev dry-run: governance decisions in ``SHADOW`` and the context engine in dry-run
  (nothing applied, Jev cost paid);
* ``C`` - Jev active: governance in ``ACTIVE`` and the context engine applying approved omissions;
* ``D`` - ``C`` plus raised loop limits (``max_transient_retries`` 4, ``max_correction_cycles`` 5).

Executor work is SIMULATED (scripted tokens and durations through a fake clock); Jev work is
either ``offline`` (deterministic stand-in, zero cost) or ``live`` (real connector calls, capped
by ``--max-live-calls``; every call's tokens and latency are MEASURED). Results carry the method
of every figure, and the quality criteria (all stages completed, requirement markers preserved,
gates passed) are checked identically in every scenario.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

RUNTIME = Path(__file__).resolve().parent
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

import decision_orchestration  # noqa: E402
import loop_engine  # noqa: E402
import loop_sdd  # noqa: E402
import obs_metrics  # noqa: E402
import obs_tracer  # noqa: E402
from context_cli import _fixture_bytes, _fixture_format, offline_evaluator  # noqa: E402
from context_config import DEFAULT_CONFIG_PATH, load_config  # noqa: E402
from context_engine import optimize  # noqa: E402
from obs_model import load_prices  # noqa: E402

SCENARIOS = ("A", "B", "C", "D")
#: Scripted per-stage work: prompt tokens (before context optimization), output tokens, seconds.
WORKLOAD = {
    "SPECIFY": (12_000, 2_500, 120), "CLARIFY": (9_000, 1_200, 60), "PLAN": (18_000, 3_500, 180),
    "TASKS": (14_000, 2_000, 90), "IMPLEMENT": (40_000, 9_000, 600), "TEST": (22_000, 1_500, 240), "REVIEW": (26_000, 2_200, 200),
}
#: Transient IMPLEMENT failures per workload: ``nominal`` stays within the default
#: ``max_transient_retries`` (2); ``stressed`` exceeds it but fits scenario D's raised limit (4).
WORKLOADS = {"nominal": 1, "stressed": 3}
FIXTURE_FOR_STAGE = {"IMPLEMENT": "anthropic_tool_session.json", "TEST": "openai_responses_session.json"}
GOVERNANCE_DECISIONS = (("RISK_ASSESSMENT", "PLAN"), ("AGENT_SELECTION", "REVIEW"))
FIXTURES = RUNTIME.parent / "tests" / "fixtures" / "context_engine"


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class LiveBudget:
    """Counts real Jev calls and refuses any beyond the cap (the refusal is recorded, never retried)."""

    def __init__(self, cap: int) -> None:
        self.cap, self.used, self.refused = cap, 0, 0

    def take(self) -> bool:
        if self.used >= self.cap:
            self.refused += 1
            return False
        self.used += 1
        return True


def _live_connector(budget: LiveBudget, env_file: Path, setup: Path, cache: Path) -> Callable[[dict[str, Any]], dict[str, Any]]:
    import semantic_governor as governor

    def evaluate(payload: dict[str, Any]) -> dict[str, Any]:
        if not budget.take():
            raise decision_orchestration.DecisionError("BENCHMARK_LIVE_BUDGET_EXHAUSTED")
        return governor.decide(payload, governor._connector_evaluator("typesafe", env_file, "120", RUNTIME),
                               provider="typesafe", cache_path=cache,
                               prepare=lambda: governor._connector_prepare("typesafe", env_file, setup))
    return evaluate


def _offline_governor(payload: dict[str, Any]) -> dict[str, Any]:
    criteria = payload["questions"]["selection"]["criteria"]
    value = sorted(criteria)[0]
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return {"status": "DECIDED", "provenance": "LIVE_JEV", "fingerprint": digest,
            "decisions": {"selection": {"value": value, "confidence": 0.9, "disposition": "DECIDED", "reason": "AT_OR_ABOVE_THRESHOLD"}},
            "usage": {"input_tokens": 0, "output_tokens": 0}}


def _governance(scenario: str, evaluator: Callable[[dict[str, Any]], dict[str, Any]]) -> list[dict[str, Any]]:
    if scenario == "A":
        return []
    mode = "SHADOW" if scenario == "B" else "ACTIVE"
    receipts = []
    for kind, stage in GOVERNANCE_DECISIONS:
        candidates = ({"LOW": "isolated tooling", "MEDIUM": "new opt-in runtime"} if kind == "RISK_ASSESSMENT"
                      else {"STAGE_AGENT": "single-module route", "DATA_FLOW_TRACER": "cross-store provenance"})
        request = {"schema_version": 1, "decision_id": f"bench:{scenario}:{kind}", "ticket": "BENCH-OBS", "kind": kind, "mode": mode,
                   "binding": {"stage": stage, "state_sha256": hashlib.sha256(f"{kind}{stage}".encode()).hexdigest()},
                   "baseline": {"value": sorted(candidates)[-1], "user_locked": False, "deterministic_ready": True},
                   "candidates": candidates,
                   "state": {"summary": "Read-only observability engine over existing stores; no default behavior change.",
                             "signals": {"stage": stage, "files": 6}, "evidence_ids": []}}
        try:
            receipts.append(decision_orchestration.decide(request, evaluator))
        except decision_orchestration.DecisionError as error:
            receipts.append({"decision_id": request["decision_id"], "ticket": "BENCH-OBS", "kind": kind, "binding": request["binding"],
                             "configured_mode": mode, "effective_mode": "FALLBACK",
                             "recommendation": {"provenance": "NOT_CALLED", "disposition": "NOT_CALLED"},
                             "outcome": {"selected": request["baseline"]["value"], "applied": False, "agreement": "NOT_EVALUATED",
                                         "requires_review": False, "reason": str(error)},
                             "metrics": {"jev_calls": 0, "elapsed_ms": 0, "input_tokens": None, "output_tokens": None, "billing": {}}})
    return receipts


def _context(scenario: str, stage: str, evaluate: Callable[[dict[str, Any]], Any] | None, state: Path, *,
             stages: tuple[str, ...], env_file: Path, setup: Path | None) -> dict[str, Any] | None:
    fixture = FIXTURE_FOR_STAGE.get(stage)
    if scenario == "A" or fixture is None or stage not in stages:
        return None
    config, error = load_config(DEFAULT_CONFIG_PATH)
    if error or not config:
        return None
    path = FIXTURES / fixture
    fmt = _fixture_format(path)
    if fmt is None:
        return None
    active = dict(config, enabled=True, trigger_estimated_tokens=0, evaluator="offline" if evaluate is offline_evaluator else "typesafe",
                  dry_run=scenario == "B", apply_enabled=scenario != "B")
    result = optimize(_fixture_bytes(path, fmt), fmt, stage, "Implement the slice and keep every acceptance check.", active,
                      evaluate=evaluate, apply=scenario != "B", audit_dir=state, env_file=env_file, project_setup=setup)
    return {"status": result["status"], "metrics": result["metrics"]}


def run_scenario(scenario: str, *, jev: str, budget: LiveBudget, env_file: Path, setup: Path, cache: Path,
                 transient_failures: int = 1, context_stages: tuple[str, ...] = tuple(FIXTURE_FOR_STAGE)) -> dict[str, Any]:
    clock = Clock()
    context_runs: dict[str, dict[str, Any]] = {}
    governor = _offline_governor if jev == "offline" else _live_connector(budget, env_file, setup, cache)
    judge: Callable[[dict[str, Any]], Any] = offline_evaluator if jev == "offline" else _budgeted_judge(budget, env_file)
    transient = {"class": "TRANSIENT", "code": "RATE_LIMITED", "message": "429", "effect_applied": False}
    failures = {"IMPLEMENT": [dict(transient) for _ in range(transient_failures)],
                "TEST": [{"class": "FUNCTIONAL", "code": "ASSERTION", "message": "acceptance check failed",
                          "hypothesis": "the total ignores the last slice; include it"}]}
    with tempfile.TemporaryDirectory(prefix=f"obs-bench-{scenario}-") as temp:
        store, state = Path(temp) / "loops", Path(temp) / "context"
        store.mkdir(mode=0o700)
        state.mkdir(mode=0o700)
        limits = {"max_transient_retries": 4, "max_correction_cycles": 5} if scenario == "D" else {}
        definition = loop_sdd.build_definition(ticket="BENCH-OBS", objective="Observability benchmark", limits=limits)

        def handler(ctx: Any) -> dict[str, Any]:
            stage = ctx.node["params"]["stage"]
            prompt, output, seconds = WORKLOAD[stage]
            clock.sleep(seconds)
            optimized = _context(scenario, stage, judge, state, stages=context_stages, env_file=env_file,
                                 setup=setup if jev == "live" else None)
            if optimized is not None:
                context_runs[f"{stage}:{ctx.attempt}"] = optimized
                metrics = optimized["metrics"]
                if optimized["status"] == "APPLIED":
                    prompt -= metrics["input_tokens_before"] - metrics["input_tokens_after"]
                clock.sleep(metrics["latency_ms"] / 1000)
            pending = failures.get(stage) or []
            if pending:
                failure = pending.pop(0)
                return {"status": "FAILED", "output": {}, "failure": failure, "tokens": prompt + output, "tokens_method": "SIMULATED"}
            return {"status": "SUCCEEDED", "output": {"stage": stage}, "tokens": prompt + output, "tokens_method": "SIMULATED"}

        engine = loop_engine.LoopEngine(store, handlers={"run-agent": handler, "gate": handler, **{n["id"]: handler for n in definition["nodes"]}},
                                        clock=clock.time, sleep=clock.sleep)
        started = engine.start(definition)
        execution_id = started["execution_id"]
        summary = engine.run(execution_id)
        wall = time.perf_counter()
        receipts = _governance(scenario, governor)
        governance_wall = time.perf_counter() - wall
        events = obs_tracer.loop_store_events(store)
        evaluator_method = "SIMULATED" if jev == "offline" else "MEASURED"  # the offline stand-in's zeros are not measurements
        events += [obs_tracer.receipt_event(receipt, timestamp=clock.now, method=evaluator_method) for receipt in receipts]
        events += [obs_tracer.context_metrics_event(run["metrics"], run_id=key, timestamp=clock.now, stage=key.split(":")[0],
                                                    method=evaluator_method,
                                                    task_id="BENCH-OBS") for key, run in context_runs.items()]
        report = obs_metrics.report(events, load_prices())
        preserved = all((run["metrics"].get("requirement_impact") or {}).get("preserved", True) for run in context_runs.values())
        return {"scenario": scenario, "loop_status": summary["status"], "report": report,
                "quality": {"all_stages_completed": summary["status"] == "COMPLETED", "requirements_preserved": preserved,
                            "governance_reviews": sum(1 for receipt in receipts if receipt["outcome"]["requires_review"])},
                "governance_wall_s": round(governance_wall, 3), "simulated_elapsed_s": round(clock.now - 1_800_000_000.0, 3)}


def _budgeted_judge(budget: LiveBudget, env_file: Path) -> Callable[[dict[str, Any]], Any]:
    from context_judge import connector_evaluate

    inner = connector_evaluate("typesafe", env_file, 60)

    def evaluate(payload: dict[str, Any]) -> Any:
        if not budget.take():
            return {"status": "ERROR", "reason": "BENCHMARK_LIVE_BUDGET_EXHAUSTED"}
        return inner(payload)
    return evaluate


def compare(results: list[dict[str, Any]]) -> dict[str, Any]:
    rows = {}
    for result in results:
        report = result["report"]
        agents = report["tokens"]["by_actor"].get("agents", {})
        jev = report["tokens"]["by_actor"].get("jev", {})
        progress, context = report["progress"], report["jev"]["context_engine"]
        rows[result["scenario"]] = {
            "loop_status": result["loop_status"], "quality": result["quality"],
            "agent_tokens_simulated": (agents.get("SIMULATED") or {}).get("total_tokens", 0),
            "jev_tokens_measured": (jev.get("MEASURED") or {}).get("total_tokens", 0),
            "jev_calls_with_unknown_usage": (context["evaluator_tokens"]["calls_with_unknown_usage"]
                                             + report["jev"]["governance"]["evaluator_tokens"]["calls_with_unknown_usage"]),
            "context_gross_saving_estimated": context["gross_saving_tokens"]["applied"],
            "context_net_saving_estimated": context["net_saving_tokens"]["applied"],
            "evaluator_latency_s": report["latency"].get("evaluator_added_latency_s"),
            "simulated_elapsed_s": result["simulated_elapsed_s"],
            "first_attempt_success_rate": progress["nodes"]["first_attempt_success_rate"],
            "retries_by_cause": progress["retries_by_cause"], "correction_loops": progress["correction_loops"],
            "policy_blocks": progress["policy_blocks"],
        }
    return rows


def _announce(value: dict[str, Any]) -> None:
    """Announce every live Jev use on stderr, as the project requires (script, calls, decisions, artefact)."""
    decided = review = tokens_in = tokens_out = 0
    for reports in value["reports"].values():
        for report in reports.values():
            governance, context = report["jev"]["governance"], report["jev"]["context_engine"]
            decided, review = decided + governance["decided"], review + governance["review"]
            for usage in (governance["evaluator_tokens"], context["evaluator_tokens"]):
                measured = usage["by_method"].get("MEASURED") or {"input": 0, "output": 0}
                tokens_in, tokens_out = tokens_in + measured["input"], tokens_out + measured["output"]
    for line in ("=== JEV USADO ===", "script: runtime/obs_benchmark.py --jev live",
                 f"chamadas: {value['live_calls_used']} (recusadas pelo limite: {value['live_calls_refused']})",
                 f"decididos vs revisar: {decided} vs {review}", f"tokens medidos: {tokens_in} entrada / {tokens_out} saída",
                 "artefato: relatório JSON do benchmark", "=================="):
        print(line, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A/B/C/D comparison of SDD configurations (simulated executor work).")
    parser.add_argument("--jev", choices=("offline", "live"), default="offline")
    parser.add_argument("--max-live-calls", type=int, default=0)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--project-setup", type=Path)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--scenarios", default="ABCD")
    parser.add_argument("--workload", choices=(*WORKLOADS, "all"), default="all")
    parser.add_argument("--context-stages", default=",".join(FIXTURE_FOR_STAGE),
                        help="Stages whose prompt goes through the context engine (comma-separated).")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.jev == "live" and (args.max_live_calls <= 0 or args.project_setup is None or args.cache is None):
        parser.error("--jev live needs --max-live-calls > 0, --project-setup and --cache")
    budget = LiveBudget(args.max_live_calls if args.jev == "live" else 0)
    workloads = list(WORKLOADS) if args.workload == "all" else [args.workload]
    results = {workload: [run_scenario(name, jev=args.jev, budget=budget, env_file=args.env_file, setup=args.project_setup or Path("-"),
                                       cache=args.cache or Path("-"), transient_failures=WORKLOADS[workload],
                                       context_stages=tuple(item for item in args.context_stages.split(",") if item))
                          for name in args.scenarios if name in SCENARIOS] for workload in workloads}
    value = {"schema_version": 1, "jev": args.jev, "live_calls_used": budget.used, "live_calls_refused": budget.refused,
             "workloads": {name: {"transient_failures": WORKLOADS[name], "stage_work": WORKLOAD} for name in workloads},
             "methods": {"agent_tokens": "SIMULATED (scripted workload)", "jev_tokens": "MEASURED" if args.jev == "live" else "offline stand-in (zero)",
                         "context_saving": "ESTIMATED (bytes / bytes_per_token)", "elapsed": "SIMULATED clock"},
             "comparison": {name: compare(rows) for name, rows in results.items()},
             "reports": {name: {result["scenario"]: result["report"] for result in rows} for name, rows in results.items()}}
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1, allow_nan=False)
    if args.jev == "live":
        _announce(value)
    if args.output:
        from obs_cli import write_private

        write_private(args.output, data.encode("utf-8") + b"\n")
        print(json.dumps({"status": "WRITTEN", "output": str(args.output), "live_calls_used": budget.used}))
    else:
        print(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
