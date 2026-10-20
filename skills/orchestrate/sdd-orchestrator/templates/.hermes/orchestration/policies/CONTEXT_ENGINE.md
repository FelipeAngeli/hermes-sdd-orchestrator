# Context engine configuration

Experimental and never invoked by default; see `docs/components/context-engine.md` in the skill repository. To experiment, copy this JSON block to an owner-managed file outside the repository, set `enabled` to `true` and pass the file with `--config`. A missing file, or any field of the wrong type or out of range, disables the engine.

```json
{
  "schema_version": 1,
  "enabled": false,
  "dry_run": true,
  "apply_enabled": false,
  "trigger_estimated_tokens": 50000,
  "bytes_per_token": 4,
  "max_concurrency": 4,
  "evaluator": "none",
  "evaluator_timeout_seconds": 30,
  "send_code_to_evaluator": false,
  "segment_target_chars": 1200,
  "max_segment_chars": 2400,
  "max_payload_bytes": 24000,
  "recent_turns_protected": 2,
  "thresholds": {"fact_loss_max": 0.2, "constraint_loss_max": 0.2},
  "cache": {"ttl_seconds": 604800, "max_entries": 10000},
  "state_dir": null,
  "stages": {
    "SPECIFY": {"eligible_kinds": ["TOOL_RESULT"]}, "CLARIFY": {"eligible_kinds": ["TOOL_RESULT"]},
    "PLAN": {"eligible_kinds": ["TOOL_RESULT"]}, "TASKS": {"eligible_kinds": ["TOOL_RESULT"]},
    "IMPLEMENT": {"eligible_kinds": ["TOOL_RESULT"]}, "TEST": {"eligible_kinds": ["TOOL_RESULT"]}, "REVIEW": {"eligible_kinds": []}
  }
}
```

- A request is rewritten only when `dry_run` is `false`, `apply_enabled` is `true` **and** `optimize` gets `--apply`. Otherwise the result is a dry run that lists `would_omit` and returns the original.
- `thresholds` are the maximum loss probabilities for an omission; a stage may override them with its own `thresholds`. Eligible kinds are `TOOL_RESULT`, `ASSISTANT_TEXT` and `SDD_SECTION`; tool inputs are never eligible. User text, errors, opaque blocks, the last `recent_turns_protected` messages and requirement-like content are always protected.
- `evaluator` `typesafe` or `jev-ai` additionally needs `optimize --project-setup <PROJECT_SETUP.md>` with the exact automatic Jev consent record and a READY connector preflight; otherwise nothing is sent and every candidate is kept (`EVALUATOR_UNAVAILABLE`). Jev then receives the task, the user messages (credentials redacted; code removed unless `send_code_to_evaluator` is `true`) and one candidate passage at a time; a passage that holds a detected credential is never sent. Payloads are capped by `max_payload_bytes`.
- State (originals, decisions, cache) lives in `state_dir` or `~/.hermes/context-engine/<hash>`; a location inside a Git work tree, or an existing directory that is not owner-only, is refused. Delete it to remove stored originals.
