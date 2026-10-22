# Observability engine (read-only)

[Docs index](../README.md) · Related: [Loop engine](loop-engine.md), [Validation gates](validation-gates.md), [Context engine](context-engine.md), [Action journal](action-journal.md)

**Files:** `runtime/obs_model.py`, `runtime/obs_tracer.py`, `runtime/obs_metrics.py`, `runtime/obs_cli.py`, `runtime/obs_benchmark.py`, `policies/PRICES.json`.

Structured observability for SDD runs, loops, agents, sub-agents, validations, Jev, tokens, cost and time. It **reads the stores the runtime already writes and stores nothing of its own**: every event is derived on demand, so there is no second source of truth to drift from the first. Nothing in the controller calls it; it runs only through `obs_cli.py` or as a library. It never calls a model; only `obs_benchmark.py --jev live` calls Jev, capped by `--max-live-calls`.

## Components

| Component | Module | Role |
| --- | --- | --- |
| ExecutionTracer | `obs_tracer` | Turns each store into correlated events (see [Sources](#sources)). |
| TokenTracker | `obs_metrics.token_tracker` | Tokens per SDD stage and per actor (agents vs Jev), kept per method. |
| CostEstimator | `obs_metrics.cost_estimator`, `obs_model.estimate_cost` | List-price USD from the versioned `policies/PRICES.json`; never a bill. |
| LatencyTracker | `obs_metrics.latency_tracker` | Count, total, p50, p95 and max per stage, action, tool, model and evaluator. |
| ProgressTracker | `obs_metrics.progress_tracker`, `human_wait`, `concurrency` | Loop and action statuses, first-attempt success, retries by cause, correction loops, recovery after interruption, time to completion, human wait, concurrency, policy blocks. Human wait runs from the first unresolved halt to the human decision; `WAIT_CLOSERS` (`LOOP_EXECUTION_CANCELLED`, `LOOP_EXECUTION_COMPLETED`, `LOOP_EXECUTION_FAILED`) close it without one (`closed_without_decision`). |
| JEVMetrics | `obs_metrics.jev_metrics` | Gross vs net context saving, evaluator tokens and latency, cache reuse, governance calls. |
| AuditReporter | `obs_metrics.report`, `obs_cli.py report` | One JSON report with every indicator and its provenance. |

## Events

Every event (`obs_model.event`) has the identifiers `trace_id`, `run_id`, `loop_id`, `session_id`, `agent_id`, `parent_agent_id`, `task_id`, `stage` (`None` when the source has none), plus `event_type`, `timestamp` (epoch seconds), `source` and `attrs`. Any other identifier is refused (`OBS_ID_UNKNOWN`).

`trace_id` is derived, not stored, and is **per store family**: a hash of the loop execution id for loop events, of the ticket for journal, gate and Jev events, and of the session for supervisor events. Reading the same store twice gives the same ids. Events of one SDD run are joined across families through `task_id` (the ticket), not through `trace_id`. Context-engine runs read from `--context-state` carry no ticket, so they have no `trace_id` or `task_id`. `parent_agent_id` is the delegating session for supervisor `SUBAGENT` events; on `ACTION` events it is the retry's parent action id.

Privacy: keys that carry free text (`FORBIDDEN_ATTRS`: prompt, goal, objective, summary, quote, output, message, hypothesis, description, stderr tails, paths, request, evidence and similar) are dropped at any depth (`MAX_DEPTH`), whatever their case. Every string that is kept, identifiers included, must survive `redaction.redact` unchanged and look like a code (`CODE_TEXT`: no spaces, at most `MAX_TEXT` = 160 characters); anything else becomes `null`, so free text cannot ride under an allowed key. A failure is reduced to its class and code. Numbers must be finite (`obs_model.number`); NaN, infinities, booleans and strings in numeric fields become `null`, and reports are written with `allow_nan=False`.

## Token and cost rules

- Every token record has a `method` (`METHODS`): `MEASURED` (reported by a provider, the Claude CLI envelope or the Hermes log), `ESTIMATED` (bytes / `bytes_per_token`, or a handler that says so) or `SIMULATED` (scripted by the benchmark). Methods are summed separately and never added together.
- A value a source did not report is `None` and counted in `unknown_fields`; it is never treated as zero.
- Costs are `ESTIMATED` list prices: `input`, `output`, `cache_read` and `cache_write` per million tokens from `PRICES.json` (with its `version` and `sha256` in every report). A model without a price, or a record with only a total, is `UNPRICED` with a reason (`NO_PRICE`, `NO_TOKENS`, `TOKEN_SPLIT_UNKNOWN`), never zero. A record whose cache-read or cache-write count is unknown is priced without that component and counted in `priced_without`; the bucket is then marked `is_lower_bound` (the Hermes log never reports cache writes, so its cost is always a lower bound).
- Evaluator usage that the provider did not report stays unknown: `calls_with_unknown_usage` counts those calls, and the net saving is then `null` instead of pretending the evaluator was free.
- Jev has no public per-token price in the table, so Jev tokens are counted but stay unpriced. They are always reported apart from agent tokens (`by_actor.jev`).
- `PRICES.json` lists base-tier prices taken from the models.dev cache; long-context tiers are not applied.

## Sources

| Source | Reader | What it yields |
| --- | --- | --- |
| Loop store `<store>/<id>/execution.json` + `events.jsonl` | `loop_events`, `loop_store_events` | `LOOP_<event>` per transition, `NODE_ATTEMPT` per attempt (result, failure class and code, duration, executor, model, tokens), `LOOP_SUMMARY`, `HUMAN_DECISION`. |
| Action-journal history `<history>/<ticket>/<action>.json`, live `ACTION_JOURNAL.json` | `journal_history_events`, `journal_event` | `ACTION` (status, attempt, retry mode, exit code, duration, artifact validation, incident codes). |
| Gate decision log `decisions.jsonl` | `gate_events` | `GATE_DECISION`, only after `DecisionLog.verified_records` checks the hash chain; a tampered log yields one `SOURCE_UNREADABLE` event. |
| Jev decision receipts, `semantic_governor decide` reports | `receipt_event`, `governor_report_event` | `JEV_DECISION` (mode, provenance, disposition, calls, latency, measured tokens; a `CACHE` answer is not a call). |
| Context-engine runs `<state>/audit/<run>/metrics.json` | `context_run_events`, `context_metrics_event` | `CONTEXT_OPTIMIZATION` (before/after estimates, evaluator usage, cache hits, latency). |
| Local supervisor log (`~/.hermes/sdd-manager/data/events.jsonl`) | `obs_cli.hsm_events` | `MODEL_CALL` (Hermes-measured tokens; uncached input = `in` − `cache_read`), `TOOL_CALL`, `SUBAGENT` (delegation with parent session). |

A missing or unreadable source yields a `SOURCE_UNREADABLE` event and never hides the others. Each reader stops after `MAX_EVENTS_PER_SOURCE` (100 000) items; the supervisor log is read only up to `MAX_HSM_BYTES` (64 MiB).

Two additive runtime changes come with the engine. The [loop engine](loop-engine.md) records each attempt's own `tokens` and `tokens_method` (`MEASURED`, `SIMULATED` or `ESTIMATED`), failed attempts included, while the `max_tokens` budget still counts successes only; the tracer reads these. The [executor transport](contracts-and-schemas.md) adds the Claude CLI envelope's measured usage to the launcher's `run` output. That output is not persisted, so **the tracer does not read it yet**: a host that keeps launcher results can feed it in later.

Hostile input: every reader runs under `obs_tracer.guarded`, so a document of the wrong shape, deep nesting, a non-numeric value or an I/O error becomes one `SOURCE_UNREADABLE` event (bad loop lines become `LOOP_EVENT_UNREADABLE`, bad supervisor lines are counted in one event). Store files over `MAX_FILE_BYTES` (64 MiB) are not read, event logs are streamed line by line, and symlinked ticket or run directories are not followed.

## Command line

`obs_cli.py` (exit `0`, or `1` on a usage error):

- `report` prints the AuditReporter JSON, or writes it with `--output` (mode 0600, atomic, never through a symlink).
- `events` prints one event per line, filtered by `--trace`, `--run`, `--stage`, `--type`, at most `--limit`.
- `prices` prints the price table's version, hash, source and models (`--prices` selects another table).

Sources for `report` and `events` are repeatable: `--loop-store`, `--journal-history`, `--journal`, `--gate-store`, `--receipts`, `--governor-report`, `--context-state`, `--hsm-events`; `--since-days` keeps recent events and `--prices` selects the table. Every report carries its own `overhead` (wall and CPU milliseconds, peak RSS, `llm_calls: 0`). There is no dashboard: the existing supervisor (`hsm`) and these JSON reports cover the need, and no visual infrastructure exists in the repository.

Retention: the engine keeps nothing, so it adds no retention burden. The sources keep their own policies (the supervisor's `retention`, `context_cli.py purge` for context-engine runs).

## Comparison benchmark

`obs_benchmark.py` runs the same SDD delivery (`loop_sdd.build_definition`, SPECIFY → REVIEW, on the real `LoopEngine`) under four scenarios (`SCENARIOS`):

| Scenario | Configuration |
| --- | --- |
| `A` | Current SDD: no Jev, no context optimization, default loop limits. |
| `B` | Jev dry-run: governance decisions in `SHADOW`, context engine in dry-run (nothing applied, Jev cost paid). |
| `C` | Jev active: governance `ACTIVE`, context engine applying approved omissions. |
| `D` | `C` plus raised loop limits (`max_transient_retries` 4, `max_correction_cycles` 5). |

Executor work is scripted (per-stage tokens and durations on a fake clock): the handler reports `SIMULATED` tokens, and with `--jev offline` the stand-in evaluators are labelled `SIMULATED` too (`simulated_calls`, never `live_calls`); two workloads exist: `nominal` (one transient IMPLEMENT failure, one failed TEST verification that a correction fixes) and `stressed` (three transient failures, above the default retry limit). Context optimization runs on the IMPLEMENT and TEST prompts using the context-engine fixtures. Quality criteria are the same in every scenario: every stage completed, requirement markers preserved, governance reviews counted.

Options: `--jev offline` (default; deterministic stand-in, zero cost) or `--jev live` with `--max-live-calls` (required, > 0), `--project-setup` and `--cache`; calls beyond the cap are refused and counted, never retried. `--scenarios`, `--workload` (`nominal`, `stressed` or `all`), `--context-stages`, `--env-file`, `--output`. A live run prints a **JEV USADO** block on stderr. Measured results: [Observability baseline](../reports/observability-baseline.md).

## Limits, stated plainly

- Executor tokens exist only where a source records them: the Hermes log (sessions run through Hermes) and handler-reported loop tokens. The Claude CLI usage reaches the launcher output but is not stored, and Codex runs record no token usage, so their tokens are unknown, not zero.
- The supervisor log is per machine and per Hermes home; sessions are correlated with SDD runs only by session id, not by ticket.
- Cost is a list-price estimate. It is not what a subscription plan bills.
- The benchmark proves the instrumentation and the accounting, not real-world gains: agent work is simulated. Only Jev tokens and latency in `--jev live` are measured.
