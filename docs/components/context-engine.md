# Context engine (experimental)

[Docs index](../README.md) · Related: [Harness](harness.md), [Skill and installer](skill-and-installer.md), [Testing](testing.md)

**Files:** `runtime/context_engine.py`, `runtime/context_cli.py`, `runtime/context_config.py`, `runtime/context_collector.py`, `runtime/context_segmenter.py`, `runtime/context_policy.py`, `runtime/context_judge.py`, `runtime/context_cache.py`, `runtime/context_rebuilder.py`, `runtime/context_audit.py`, `runtime/context_metrics.py`, `policies/CONTEXT_ENGINE.md`.

The context engine asks Jev (TypeSafe's evaluation model) which completed tool output in a large request can be replaced by an explicit omission marker, and rebuilds the request without it. The idea comes from [compozy/yoshi](https://github.com/compozy/yoshi), a context-pruning proxy for Claude Code and Codex. This is a Python port of the ideas, not of the code.

It is **experimental and off**. Nothing in `sdd.py`, the drivers, the hooks or the installer calls it. The shipped [policy](../../skills/orchestrate/sdd-orchestrator/templates/.hermes/orchestration/policies/CONTEXT_ENGINE.md) sets `enabled: false`, `dry_run: true`, `apply_enabled: false` and `evaluator: "none"`. A request is rewritten only when a configuration passed with `--config` sets `enabled: true`, `dry_run: false` and `apply_enabled: true`, **and** the command line has `--apply`.

## What was verified about Jev and Yoshi

- `typesafe-ai/jev` is not a public repository. Jev is a hosted model. Yoshi reaches it through the Vercel AI Gateway. This engine instead reuses the existing `runtime/typesafe_connector.py` (TypeSafe or `jev-ai` provider, unchanged) through `semantic_governor._connector_evaluator`. It adds no provider, gateway or authentication path.
- Live probes on 2026-10-20 through `typesafe_connector.py evaluate --input-stdin` with synthetic text (recorded in the baseline report): the shape the engine sends (`model: jev-latest`, two `noul` questions with `true`/`false` criteria) answered from model `jev-1.13.0` with fact/constraint loss 0.05/0.12 for an irrelevant passage and 0.73/0.70 for a passage holding the requested value; `model: typesafe-ai/jev` with `low`/`high` criteria was refused with HTTP 400.
- Yoshi is a Bun/TypeScript HTTP proxy for the Anthropic Messages and OpenAI Responses APIs. It is not natively compatible with Hermes, ACP or this controller. Choices kept from it: completed tool results as candidates; human instructions, errors and opaque blocks protected; line/paragraph spans; one span per Jev request; two loss questions with both probabilities at or below 0.2; keep on any failure; a metadata-only decision store; explicit omission markers; dry-run; a 50,000-token estimate as trigger.

## Pipeline

`optimize` runs these steps. Every exception returns the original request with `FAIL_OPEN`.

1. **Collect** (`context_collector.py`): normalize Anthropic Messages (`messages`), OpenAI Responses (`input`) or a Markdown SDD prompt into messages and typed blocks (`text`, `tool_use`, `tool_result`, `sdd_section`, `opaque`). Unknown shapes, reasoning and thinking blocks are `opaque`.
2. **Trigger**: below `trigger_estimated_tokens` (bytes divided by `bytes_per_token`) the status is `SKIPPED_BELOW_TRIGGER` and nothing is judged.
3. **State location**: `--audit-dir`, else `state_dir`, else `~/.hermes/context-engine/<hash>`. A location inside the current Git work tree, or inside any Git work tree, is refused with `AUDIT_DIR_INSIDE_REPOSITORY`. An existing directory must already be owner-only (0700, same user) or the run fails open with `STATE_DIR_NOT_PRIVATE`; permissions of existing directories are never changed.
4. **Segment** (`context_segmenter.py`): only the stage's `eligible_kinds`. Segment kinds (`KINDS`): `TOOL_RESULT`, `ASSISTANT_TEXT`, `SDD_SECTION`. Tool inputs are never segmented. Spans end on paragraph or line boundaries and never inside a line (a line longer than `max_segment_chars` stays whole and is then too large to send); code-like text (fences, indented blocks) stays one span; an SDD heading line is never part of a span. Text parts are `TEXT_PART_TYPES` (`text`, `input_text`, `output_text`).
5. **Protect** (`context_policy.py`): deterministic rules, never decided by Jev. A protected segment is never omitted and is never sent as a passage to judge. (User text does reach Jev as context; see step 8.) `RULE_IDS`: `user-instruction` (any non-assistant text, including role-less Responses items), `opaque`, `orphan-tool-result` (a result with no matching call), `recent-turn` (the last `recent_turns_protected` messages), `unresolved-error` (`is_error`, Traceback, `FAILED`, `Error:`), `redaction-unavailable`, `secret-present` (the redactor changes the segment or anywhere in its field, so a key or token that crosses spans protects every span), `sdd-section` (Objective, Rules, Acceptance, Contract), `acceptance-criteria`, `architecture-decision`, `requirement` (`MUST`/`SHALL`, contract, schema, requirement), `security-constraint`, `validation-evidence` (`OK`, `passed`, `RED`, `GREEN`, `exit_code`), `blocker`. Case-sensitive tokens are word-bounded. Tool calls without a result (`pending_tool_calls`) are never rewritten: tool inputs are not segmented.
6. **Evaluator gate**: `none` keeps everything (`EVALUATOR_NONE`). Real evaluators (`REAL_EVALUATORS`) are `typesafe` and `jev-ai`; for them `--project-setup` must name a `PROJECT_SETUP.md` whose consent is exactly the automatic Jev record (`semantic_governor.consent_state` is `ENABLED`), and the connector preflight must be `READY`. Otherwise no call is made and every candidate is kept with `EVALUATOR_UNAVAILABLE`. The engine does not check that the setup file belongs to the project whose transcript is judged; the operator chooses it. Library callers that pass their own `evaluate` under another evaluator name bypass this gate; the CLI cannot.
7. **Cache** (`context_cache.py`): the key is the SHA-256 of the segment hash, task hash, user-constraint hash, full policy hash, evaluator and `ENGINE_VERSION`. Entries hold the verdict, the two probabilities and a timestamp, never text. TTL and `max_entries` apply, and a corrupt file is an empty cache. Only valid answers are cached.
8. **Judge** (`context_judge.py`): one passage per request (model `JEV_MODEL`), at most `max_concurrency` in flight, one overall `evaluator_timeout_seconds` deadline (hung calls are abandoned, not awaited), no retry. **What is sent**: the task and the text of every user message (as `constraints`, at most `MAX_CONSTRAINT_BYTES`), with credentials redacted and, unless `send_code_to_evaluator`, fenced and indented code replaced by `CODE_PLACEHOLDER`; plus one candidate passage. Passages are not redacted: a passage the redactor would change is protected and never sent. Redaction covers credential shapes only, not personal data such as names or e-mail addresses. Everything is cut to fit `max_payload_bytes`. `SCREEN_REASONS` keep a segment without sending it: `EVALUATOR_NONE`, `REDACTION_UNAVAILABLE`, `CODE_PROTECTED` (unless `send_code_to_evaluator`), `SECRET_PRESENT`, `SEGMENT_TOO_LARGE`.
9. **Decide**: `OMIT` only when both questions (`QUESTION_IDS`: `fact_loss`, `constraint_loss`) have valid probabilities at or below the thresholds (`fact_loss_max`, `constraint_loss_max`; a stage may override them); otherwise `PRESERVE`. Reasons: `JEV_APPROVED`, `JEV_RETAINED`, `INVALID_ANSWER`, or the evaluator error (`EVALUATOR_FAILED`, `EVALUATOR_RESPONSE_INVALID`, `TIMEOUT`, or a connector reason).
10. **Evaluator failure**: any error other than screening returns `FAIL_OPEN` with reason `EVALUATOR_FAILED` and the original request.
11. **Rebuild** (`context_rebuilder.py`): each omission becomes `MARKER_TEMPLATE`, `[context-engine omitted segment <id> sha256=<hash>; recover with context_engine.py recover --run <run_id>]`, written into the field it came from (Anthropic `content` string or text item, Responses `output`, SDD text). `check_integrity` then requires the same messages, roles, block types, ids, names, call ids, non-text items and every protected segment still present verbatim (also when an earlier omission in the same field shifted its offsets), and `requirement_markers` (AC ids, `MUST`/`SHALL` and contract lines) must be equal before and after. Otherwise `FAIL_OPEN` with `REBUILD_INTEGRITY_FAILED` (`INTEGRITY_FAILED`) or `REQUIREMENT_MARKER_LOST`.
12. **Result**: `APPLIED` only with all activation switches; otherwise `DRY_RUN` with the original request and `would_omit`.

Statuses (`STATUSES`): `DISABLED`, `SKIPPED_BELOW_TRIGGER`, `DRY_RUN`, `APPLIED`, `FAIL_OPEN`. Configuration errors: `CONFIG_MISSING`, `CONFIG_INVALID` (any field of the wrong type or out of range disables the engine). `EVALUATORS`: `none`, `typesafe`, `jev-ai`. `FORMATS`: `anthropic`, `openai`, `sdd-prompt`.

## Audit and recovery

`context_audit.py` creates one directory per run that reaches the evaluator: `audit/<run_id>/original.bin` (the exact input bytes), `sha256`, `decisions.jsonl` (segment id, hash, kind, disposition, reason, protection rule, cached flag, probabilities; no text) and `metrics.json`. Directories are 0700 and files 0600. The run id must match `RUN_ID` (hex timestamp, `_`, 16 URL-safe characters); `purge` only removes directories with that shape; any other value is refused with `AUDIT_RUN_ID_INVALID`, and a changed original with `AUDIT_SHA256_MISMATCH`. Originals are kept, including for dry runs, until `purge` removes them (`PURGE_DAYS_INVALID` for a negative age).

## Metrics

Every report has `metrics`:
- tokens: `input_tokens_before`, `input_tokens_after`, both measured on the request as the engine serializes it for forwarding, so a saving is removed content and never a formatting difference;
- evaluator: `evaluator_calls`, `evaluator_input_tokens`, `evaluator_output_tokens`;
- savings: `estimated_saving` (would-be saving, also in dry-run), `estimated_net_saving` (minus evaluator input tokens), `effective_saving` (0 unless `APPLIED`), `net_saving`;
- `latency_ms`, `cache_hits`, `cache_misses`, `cache_hit_ratio` (`evaluator_calls` counts only calls that started);
- segments: `segments_total`, `segments_protected`, `segments_preserved`, `segments_omitted`, `segments_rejected` (approved by Jev but undone by an integrity failure);
- `evaluation_errors` and `protection_rules` (counts by reason), `requirement_impact` (`before`, `after`, `preserved`).

Token figures are estimates, not provider counts.

## Command line

`python3 .hermes/orchestration/runtime/context_engine.py <command>` (implemented in `context_cli.py`) prints one JSON object. Exit 0 on success, 2 when the request was left unchanged by an error or a disabled engine, 1 for a usage or input error (`INPUT_UNREADABLE`).

- `optimize --input <file> --format anthropic|openai|sdd-prompt --stage <STAGE> --task <text|@file> [--config <file>] [--evaluator none|typesafe|jev-ai] [--env-file <file>] [--project-setup <file>] [--audit-dir <dir>] [--output <file>] [--apply] [--json]`. `--output` receives the forwarded bytes: the input itself unless `APPLIED`. With a missing or invalid `--config` the result is `DISABLED` and the report carries `config_error`.
- `recover --run <run_id> --audit-dir <dir> [--output <file>] [--json]` returns the byte-identical original (`RECOVERED`).
- `compare --corpus <dir> [--config <file>] [--audit-dir <dir>] [--json]` runs every fixture through baseline (disabled), dry-run and apply with `offline_evaluator`, a deterministic stand-in that judges every eligible passage safe. It reports tokens, `dry_run_equals_baseline`, `apply_integrity_ok` and `requirements_preserved` per fixture.
- `config [--config <file>] [--json]` prints the effective configuration (the shipped policy by default).
- `purge --audit-dir <dir> --older-than-days <n> [--json]` deletes older runs (`PURGED`).

## Enabling and rollback

To experiment, copy the JSON block of `policies/CONTEXT_ENGINE.md` to an owner-managed file outside the repository, set `enabled: true` and pass it with `--config`. Keep `dry_run: true` until the protection tests and a dry-run review pass. A real evaluator sends redacted passages, the task and the user constraints to TypeSafe, and each call is billed.

Rollback: the engine has no call site, so not invoking it is the rollback. Remove the state directory to delete stored originals and the cache. Removing the eleven runtime files and the policy page restores the previous release exactly.

Measured offline comparison: [Jev context-engine baseline](../reports/jev-context-engine-baseline.md).
