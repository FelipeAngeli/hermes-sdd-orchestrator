# Executor Result Contract

## Scope and transport

This contract applies only to external execution actions: `SPECIFY`, `CLARIFY`, `PLAN`, `TASKS`, `IMPLEMENT`, and `TEST`. `REVIEW` is exclusively governed by `REVIEW_CONTRACT.md` and `../schemas/REVIEW_RESULT_SCHEMA.json`.

The final executor message is one JSON document with root `executor_result` and `schema_version: 3`. Markdown, literal YAML, JSONL events, transcripts, tool logs, and free text are not substitutes for that JSON document.

For Codex, the controller selects `../schemas/EXECUTOR_RESULT_SCHEMA.json` before dispatch and uses both `--output-schema` and `--output-last-message`. Only the unique final-message file is supplied to the validator. Process exit code and optional transcript diagnostics remain separate from the result document.

The controller supplies the stage-specific contract in the prompt, validates schema, semantics, paths, symbols, and ownership, then decides the state transition. `next_step` is only a recommendation; it never starts work automatically and cannot be `DONE`.

## Envelope

```json
{
  "executor_result": {
    "schema_version": 3,
    "stage": {"value": "SPECIFY", "status": "SUCCESS"},
    "executor": {"name": "CODEX", "invocation_type": "EXTERNAL_CLI"},
    "consulted_paths": [{"path": "src/example.ext"}],
    "modified_paths": [],
    "created_paths": [],
    "validated_symbols": [{"symbol": "example", "path": "src/example.ext", "exists": true}],
    "commands": [],
    "blockers": [],
    "context_assessment": {
      "facts": [{"statement": "The behavior is declared here", "evidence": "src/example.ext:10"}],
      "assumptions": [],
      "unresolved_questions": []
    },
    "acceptance_checks": [{
      "id": "AC-1",
      "criterion": "The observable outcome matches the request",
      "verification_method": "Run the focused behavior check",
      "verifier": "AGENT",
      "slice_id": null,
      "status": "PLANNED",
      "evidence": null
    }],
    "stage_payload": {"summary": "", "tasks": [], "impact_files": [], "decisions": []},
    "tdd_slices": [],
    "next_step": {"stage": "CLARIFY", "action": "Describe the next action"}
  }
}
```

All listed fields are required, including empty arrays. `consulted_paths`, `modified_paths`, and `created_paths` are arrays of objects containing exactly `path: string`; standalone strings are invalid. Context and acceptance text must contain a non-whitespace character. `context_assessment` keeps evidence-backed facts separate from assumptions and unresolved questions. Every assumption and question declares whether it is material. SPECIFY may succeed with unresolved material context only when `next_step.stage` is `CLARIFY`; from CLARIFY onward, a material assumption needs explicit validation and a material unresolved question prevents `SUCCESS`. Each `acceptance_checks` item has a stable unique `id`, criterion, verification method, `AGENT` or `HUMAN` verifier, nullable `slice_id`, current status and evidence. `stage_payload.summary` is a string and `tasks`, `impact_files`, and `decisions` are arrays of strings.

`stage.value` is one of `SPECIFY`, `CLARIFY`, `PLAN`, `TASKS`, `IMPLEMENT`, or `TEST`. `next_step.stage` may recommend one of those stages or `REVIEW`; it must not be `DONE`.

## Task traceability

For new or revised TASKS, use the existing `stage_payload.tasks` strings to reference the canonical requirement (document/section or authorized request), applicable design decision, observable outcome, and planned focused test/evidence. Preserve original identifiers; do not create aliases, parallel matrices, or new schema fields. Implementation returns actual evidence through the existing result fields; never present planned validation as executed.

## Acceptance verification

SPECIFY, CLARIFY and PLAN keep every check `PLANNED` with `evidence: null` for every result status; they define how an outcome will be checked without pretending it already passed. TASKS success requires a non-empty check set and assigns every criterion to the `slice_id` that will verify it. For every IMPLEMENT and TEST result status, the controller supplies the same non-empty authoritative ID → criterion/verification method/verifier/slice mapping, every authoritative and returned check has a non-whitespace slice assignment, and the worker returns the complete non-empty check set; omission or replacement with empty data makes the result contract-invalid. For IMPLEMENT success it also supplies exactly one `current_slice_ids` entry and the disjoint set of already completed slice IDs; the latter must be passed explicitly even when empty, and omission fails closed. The reported `tdd_slices` must exactly equal that current set. TEST success uses the same authoritative mapping. An IMPLEMENT result carries the complete check set: checks assigned to current or completed slices must be `PASS` with evidence, while checks for later slices remain `PLANNED`. TEST with `SUCCESS` requires every check to be `PASS` with non-whitespace evidence. A passing command is relevant evidence only when it exercises the named criterion. Human-verifiable criteria use `verifier: HUMAN` and may pass only with an explicit human decision recorded as evidence.

## TDD slices

`tdd_slices` is always present. Each item declares `id`, `objective`, `test_file`, `red_command`, `red_exit_code`, `expected_failure`, `red_failure_kind`, `minimal_implementation`, `green_command`, `green_exit_code`, and `green_result` with the exact names and types in `../schemas/EXECUTOR_RESULT_SCHEMA.json`.

Use `null` only for unavailable execution data in `BLOCKED` or `TIMEOUT` results. Outside `IMPLEMENT`, `tdd_slices` may be `[]`. `IMPLEMENT` with `SUCCESS` requires at least one slice with a non-zero RED exit code, a non-empty expected failure, `red_failure_kind: EXPECTED_FUNCTIONAL`, GREEN exit code `0`, and a non-empty GREEN result. `INFRASTRUCTURE` is not valid RED evidence. A blocked implementation may retain partial slice evidence but cannot be reported as successful.

A syntactically valid payload is not proof that commands ran. The controller confirms actual execution from independently recorded evidence and never retroactively applies version 3 requirements to earlier history.

## Acceptance rules

- The controller rejects the wrong root envelope, wrong action, unsupported version, unknown property, or schema-invalid result.
- A non-zero CLI exit is an executor/process failure. A zero exit with invalid final-message JSON or schema is `CONTRACT_INVALID`. A valid final message with `stage.status: BLOCKED` is a valid blocked result.
- The controller rejects invalid cited paths or symbols for PLAN/TASKS, ownership violations, unresolved material context after SPECIFY, missing acceptance evidence, missing required TDD evidence, and non-empty blocking blockers before advancement.
- One corrective retry is the maximum when the applicable loop policy permits it. The retry has a new action id, a new final-message file, `parent_action_id`, `parent_artifact_path`, `parent_artifact_sha256`, `retry_mode`, `invalid_fields`, `allowed_corrections`, and attempt; it counts as a new executor call and never deletes the parent artifact.
- `METADATA_OVERLAY` is only for a parent whose functional, acceptance and TDD evidence passed, with unchanged baseline and ownership fingerprints and no product-file change. It preserves `context_assessment`, `acceptance_checks`, `tdd_slices`, `modified_paths`, `created_paths`, `stage_payload`, and valid evidence, altering only explicit `allowed_corrections`. Otherwise use `FULL_REPLACEMENT` or block with `PARENT_EVIDENCE_MISMATCH`; functional blockers never become SUCCESS automatically.