# Observability baseline (PROMPT 07)

[Docs index](../README.md) · Engine: [Observability engine](../components/observability.md)

Measured on 2026-10-22 with `obs_cli.py` and `obs_benchmark.py` from this change. Every number below comes from one of three artifacts and keeps its method label (`MEASURED`, `ESTIMATED`, `SIMULATED`). No performance gain is claimed: agent work in the A/B/C/D comparison is simulated, and the only live measurements are Jev's own tokens and latency.

| Artifact | What it is |
| --- | --- |
| [data/observability-benchmark-offline.json](data/observability-benchmark-offline.json) | `obs_benchmark.py --jev offline` (all four scenarios, both workloads, deterministic stand-in for Jev). |
| [data/observability-benchmark-live.json](data/observability-benchmark-live.json) | `obs_benchmark.py --jev live --max-live-calls 20 --scenarios ABC --workload nominal --context-stages TEST`: 20 real Jev calls, none refused. Recorded with the first 15.17.0 candidate, before the round-1 review fixes: its agent tokens are labelled `ESTIMATED` although they are simulated, and it has no `calls_with_unknown_usage` field. The Jev token and latency numbers it holds are unaffected (every call reported usage). |
| Real-store report (not committed: it describes the owner's machine) | `obs_cli.py report` over the local supervisor log (1 510 events, 2026-10-09 to 2026-10-12), one action-journal history and seven Jev governance reports; 1 518 events. |

## Real stores: what the engine sees today

- **Tokens (MEASURED, Hermes log, 754 model calls):** 5 566 093 uncached input, 368 971 output, 112 746 063 cache-read. **95.3 %** of prompt tokens were served from the provider cache. Cache-write tokens are not in the Hermes log line, so they are counted as unknown (754 unknown fields), not zero.
- **Cost (ESTIMATED, list price `PRICES.json` 2026-10-22):** at least USD 75.92 for those 754 calls. It is a **lower bound**: every call is priced, but without cache-write tokens, which the Hermes log does not report (`priced_without.cache_write_tokens` = 754). It is the list-price equivalent, not a bill: the sessions ran on subscription plans.
- **Jev (MEASURED):** 5 live governance calls, 7 728 input and 1 683 output tokens, plus 2 answers reused from cache (not counted as calls). Jev tokens stay unpriced (`NO_PRICE`): there is no public per-token price for Jev in the table.
- **Latency (MEASURED):** the biggest totals are the `terminal` tool (368 calls, 7 465 s, p95 91 s, max 420 s) and `gpt-5.6-sol` (475 calls, 3 843 s, p50 5.2 s, p95 20.6 s). `claude-opus-5` has the longest single call (1 215 s, p95 83 s). `clarify` waits (6 calls, p50 61 s, max 360 s) are human time.
- **Concurrency:** 51 delegations (47 completed, 4 errors). The log records one span per delegation batch, not per child, so children inside a batch are not visible to this source and measured parallelism stays at 1 at that granularity.
- **Engine overhead (MEASURED):** 63 ms wall, 61 ms CPU, 25 MB peak RSS, 0 model calls for the whole report.

## A/B/C/D comparison

Same SDD delivery (SPECIFY → REVIEW on the real `LoopEngine`), same scripted per-stage work, same quality criteria. Agent tokens and elapsed time are `SIMULATED`; context savings are `ESTIMATED` (bytes / 4); Jev tokens are `MEASURED` only in the live run. Elapsed time and evaluator latency include the real wall time of the context engine, so they vary by milliseconds between runs.

### Offline (deterministic stand-in that judges every eligible passage safe to omit)

| Workload | Scenario | Loop | Agent tokens | Gross saving | Net saving | First-attempt success | Retries by cause | Corrections | Blocks |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- | ---: | --- |
| nominal | A | COMPLETED | 284 400 | 0 | 0 | 0.714 | transient 1, functional 1 | 1 | — |
| nominal | B | COMPLETED | 284 400 | 0 (dry run) | 0 | 0.714 | same | 1 | — |
| nominal | C | COMPLETED | 279 612 | 4 788 | 4 788 | 0.714 | same | 1 | — |
| nominal | D | COMPLETED | 279 612 | 4 788 | 4 788 | 0.714 | same | 1 | — |
| stressed | A | BLOCKED | 209 200 | 0 | 0 | 0.8 | transient 3 | 0 | `TRANSIENT_RETRIES_EXHAUSTED` |
| stressed | B | BLOCKED | 209 200 | 0 | 0 | 0.8 | transient 3 | 0 | same |
| stressed | C | BLOCKED | 206 332 | 2 868 | 2 868 | 0.8 | transient 3 | 0 | same |
| stressed | D | COMPLETED | 375 700 | 6 700 | 6 700 | 0.714 | transient 3, functional 1 | 1 | — |

The offline stand-in costs zero, so gross equals net there by construction; its calls are reported as `simulated_calls` with `SIMULATED` zero tokens, never as live calls or measurements. It shows the accounting works; it says nothing about how much Jev would really remove.

### Live (20 real Jev calls; nominal workload, context engine on the TEST prompt)

| Scenario | Jev calls | Jev tokens (in / out, MEASURED) | Gross saving | Net saving | Added evaluator latency | Governance |
| --- | ---: | --- | ---: | ---: | ---: | --- |
| A | 0 | 0 / 0 | 0 | 0 | 0 s | — |
| B (dry run) | 10 | 7 153 / 375 | 0 | **−6 378** | 1.66 s | 1 decided, 1 review |
| C (active) | 10 | 7 151 / 375 | 0 | **−6 378** | 1.56 s | 0 decided, 2 review |

On this fixture Jev judged no TEST passage safe to omit, so the context engine saved nothing and the evaluator's 6 378 tokens are a net loss. Governance decisions save no prompt tokens at all: their 1 148–1 150 tokens per scenario are pure overhead. Scenario D was not run live; its live result would equal C's for Jev, because D changes only loop limits.

## Bottlenecks, from the measurements

1. **Retry budget vs transient failures.** In the stressed workload, A, B and C all stop at `TRANSIENT_RETRIES_EXHAUSTED` with the default `max_transient_retries` of 2. Only D, with 4, completes, at the price of 375 700 simulated tokens against 209 200 for the blocked runs. That is a policy trade-off, not a gain: a raised limit completes more work and spends more.
2. **Jev context evaluation does not pay for itself on these fixtures.** Live net saving is −6 378 tokens per run. It would need passages large and redundant enough that the estimated saving exceeds about 6 400 tokens per evaluated prompt.
3. **Real-session time is dominated by `terminal` and model latency**, with long human waits on `clarify`. The 95 % cache ratio means further prompt reduction attacks the smallest part of the token bill.
4. **Coverage gaps.** Codex runs record no token usage in the journal, sub-agents inside a delegation batch are invisible to the supervisor log, and no loop store or gate decision log exists on this machine yet, so loop and gate indicators come only from the benchmark.

## What this does not show

- Real agent token savings or speedups: agent work is scripted.
- Jev's value on other inputs: one fixture, 20 calls.
- Billed cost: list prices only.
