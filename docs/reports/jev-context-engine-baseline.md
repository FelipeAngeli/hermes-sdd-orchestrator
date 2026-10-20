# Jev context-engine baseline (2026-10-20)

[Docs index](../README.md) · Component: [Context engine](../components/context-engine.md)

The engine is experimental and off by default. These measurements decide nothing about enabling it. They show that the shipped protections hold and what one live run costs. Token figures are bytes divided by 4, measured on the request as the engine serializes it for forwarding.

## Offline comparison (fake evaluator)

From the controller root, with the repository as working directory:

```text
python3 .hermes/orchestration/runtime/context_engine.py compare --corpus .hermes/orchestration/tests/fixtures/context_engine --json
```

`compare` uses `offline_evaluator`, a deterministic stand-in that judges **every eligible passage safe to omit**, so the apply column is a worst case for protection, not a saving estimate. It makes no network call and sets the trigger to 0 so the small fixtures are judged.

| Fixture | Format | Baseline tokens | Dry run = baseline | Would omit | Apply status | Apply tokens | Omitted | Protected | Integrity | Requirements kept |
| --- | --- | ---: | --- | ---: | --- | ---: | ---: | ---: | --- | --- |
| `anthropic_tool_session.json` | anthropic | 2113 | yes | 8 | APPLIED | 1157 | 8 | 1 | yes | yes |
| `openai_responses_session.json` | openai | 1845 | yes | 8 | APPLIED | 885 | 8 | 0 | yes | yes |
| `protected_only_session.json` | anthropic | 1129 | yes | 0 | DRY_RUN | 1129 | 0 | 7 | yes | yes |
| `sdd_prompt_session.json` | sdd-prompt | 1087 | yes | 6 | APPLIED | 405 | 6 | 4 | yes | yes |

Reading:
- Every dry run returned the baseline request unchanged.
- In the protected-only session, all seven tool results stayed protected even though the fake evaluator approved everything. They hold AC, MUST, ADR, security, test-evidence and blocker lines.
- Apply kept every id, pairing and requirement marker. The Anthropic error result and the SDD Objective, Rules, Acceptance and Contract sections stayed verbatim.
- With a fake that approves everything, apply also removes the station-7 log that holds the requested value. Protection rules cover structure and requirements, not task relevance, which is Jev's job.

## Live Jev probes

These calls went through `typesafe_connector.py evaluate --input-stdin` with synthetic text, model `jev-1.13.0`:

| Payload | Result |
| --- | --- |
| Engine shape, passage irrelevant to "final wind speed at station 7" | fact loss 0.05, constraint loss 0.12 |
| Engine shape, passage holding the final reading | fact loss 0.73, constraint loss 0.70 |
| `model: typesafe-ai/jev` with `low`/`high` criteria | HTTP 400 |

## Live Jev dry run

`optimize` ran in dry-run with `evaluator: typesafe`, with consent and preflight READY. The task was "Report the final peak wind for station 7 and quote the value exactly.", with trigger 0 and `recent_turns_protected: 1`. Only synthetic fixture text was sent.

| Fixture | Calls | Evaluator input / output tokens | Request tokens | Would omit | Estimated net saving | Latency |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `anthropic_tool_session.json` | 8 | 6,202 / 304 | 2,113 | 0 | −6,202 | 780 ms |
| `openai_responses_session.json` | 8 | 6,122 / 304 | 1,845 | 0 | −6,122 | 702 ms |

- The station-7 log, which holds the requested value, got fact loss 0.94 and 0.95.
- The seven irrelevant logs got fact loss 0.36–0.52 and constraint loss 0.30–0.43. That is above the 0.2 threshold, so Jev kept them all. It errs toward keeping, which is the intended failure direction.
- Each call costs about 770 evaluator input tokens for a 666-character passage. Judging these small passages cost 2.9 times (Anthropic) and 3.3 times (OpenAI) the request size.
- The engine can only pay off on large contexts (the 50,000-token trigger) with long, clearly irrelevant tool output, and only if Jev is confident. This run does not show that it does.
- Requirement compliance: the requirement markers were identical before and after in every run (`requirement_impact.preserved: true`).

Not measured yet: a live run on a real session above the trigger, the cache hit ratio across consecutive turns, and any effect on the main model's answers. Those need an owner-approved session; until then `enabled` stays `false`.
