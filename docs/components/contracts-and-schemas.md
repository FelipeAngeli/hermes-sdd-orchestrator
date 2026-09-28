# Contracts and schemas

[Docs index](../README.md) · Related: [Stage agents](stage-agents.md), [Sub-agents](sub-agents.md), [Action journal](action-journal.md), [Gates](gates-and-stack-detection.md)

**Files:** `contracts/EXECUTOR_CONTRACT.md`, `contracts/REVIEW_CONTRACT.md`, `schemas/EXECUTOR_RESULT_SCHEMA.json`, `schemas/REVIEW_RESULT_SCHEMA.json`, `runtime/validate_protocol.py`.

Every worker ends with **one JSON document**, written to a unique final-message file. Markdown, YAML, transcripts and JSONL events are never accepted as a substitute. Both envelopes use `schema_version: 2` and reject unknown properties. Each [stage agent](stage-agents.md) and [sub-agent](sub-agents.md) declares which schema applies in its `result_schema` frontmatter.

## Executor result

Used by `SPECIFY`, `CLARIFY`, `PLAN`, `TASKS`, `IMPLEMENT` and `TEST`, and by the sub-agents that declare `EXECUTOR_RESULT_SCHEMA.json`. The root is `executor_result`, with these required fields: `schema_version`, `stage` (`value`, `status`), `executor` (`name`: `CODEX` or `CLAUDE`; `invocation_type`: `EXTERNAL_CLI`), `consulted_paths`, `modified_paths`, `created_paths` (objects `{path}`), `validated_symbols`, `commands`, `blockers`, `stage_payload` (`summary`, `tasks`, `impact_files`, `decisions`), `tdd_slices` and `next_step` (never `DONE`).

For Codex, the controller passes the schema with `--output-schema` and reads `--output-last-message`.

**TDD evidence.** `IMPLEMENT` with status `SUCCESS` needs at least one slice with a non-zero RED exit, `red_failure_kind: EXPECTED_FUNCTIONAL`, and GREEN exit `0`. An infrastructure failure does not count as RED.

**Acceptance.** A non-zero CLI exit is a process failure. A zero exit with invalid JSON is `CONTRACT_INVALID`. Cited paths and symbols, ownership and TDD evidence are checked against the repository before a transition. A valid envelope is not proof that its commands actually ran.

## Review result

Used by `REVIEW` and the audit sub-agents. The root is `review_result`, with these fields: `status` (`APPROVED`, `CHANGES_REQUIRED`, `BLOCKED`), `reviewed_paths`, `findings` (severity, path, description, evidence), `baseline`, `ownership`, `e2e` (`files_modified`, `execution_performed` and `violation` are kept separate), `forbidden_actions`, `gate_status` and `next_step`.

`APPROVED` requires a preserved baseline, valid ownership, no unresolved findings and `focused_tests`/`format`/`analyze` at `PASS`. Gate values are defined in [Gates](gates-and-stack-detection.md#gate-order-gatesmd). The reviewer is read-only and cannot declare DONE.

## `validate_protocol.py`

Validates a final-message JSON against the selected schema plus semantic rules (envelope/action match, TDD evidence, path shapes). It never executes the commands in the payload and never reads STATE. Its public functions are `validate_payload` and `validate_json_text`, and it has no CLI flags. Its behavior is pinned by `tests/test_protocol.py` ([Testing](testing.md#installed-controller-tests)).

Changing a schema is a contract change: bump the `SKILL.md` version and update this page, the example envelopes in both contracts, and `test_protocol.py`.
