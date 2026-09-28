# Contracts and schemas

[Docs index](../README.md) · Related: [Stage agents](stage-agents.md), [Sub-agents](sub-agents.md), [Action journal](action-journal.md), [Gates](gates-and-stack-detection.md)

**Files:** `contracts/EXECUTOR_CONTRACT.md`, `contracts/REVIEW_CONTRACT.md`, `schemas/EXECUTOR_RESULT_SCHEMA.json`, `schemas/REVIEW_RESULT_SCHEMA.json`, `runtime/validate_protocol.py`.

Every worker ends with **one JSON document**, written to a unique final-message file. Markdown, YAML, transcripts and JSONL events are never accepted as a substitute. Both envelopes use `schema_version: 3` and reject unknown properties. Each [stage agent](stage-agents.md) and [sub-agent](sub-agents.md) declares which schema applies in its `result_schema` frontmatter.

## Executor result

Used by `SPECIFY`, `CLARIFY`, `PLAN`, `TASKS`, `IMPLEMENT` and `TEST`, and by the sub-agents that declare `EXECUTOR_RESULT_SCHEMA.json`. The root is `executor_result`, with these required fields: `schema_version`, `stage` (`value`, `status`), `executor` (`name`: `CODEX` or `CLAUDE`; `invocation_type`: `EXTERNAL_CLI`), `consulted_paths`, `modified_paths`, `created_paths` (objects `{path}`), `validated_symbols`, `commands`, `blockers`, `context_assessment`, `acceptance_checks`, `stage_payload` (`summary`, `tasks`, `impact_files`, `decisions`), `tdd_slices` and `next_step` (never `DONE`).

**Context.** `context_assessment` separates evidence-backed facts from assumptions and unresolved questions. SPECIFY may succeed with unresolved material context only by routing directly to CLARIFY. From CLARIFY onward, a successful result cannot retain an unvalidated material assumption or a material unresolved question; the worker must obtain evidence or return a blocker instead of guessing.

**Acceptance evidence.** Each `acceptance_checks` item has a stable unique ID, criterion, verification method, verifier, nullable slice assignment, status and evidence. The controller-owned mapping includes ID, criterion, verification method, verifier and a non-whitespace slice assignment for every check; workers cannot downgrade a HUMAN check to AGENT, replace its method or erase its slice. SPECIFY through PLAN always keep checks `PLANNED` with null evidence; TASKS success requires a non-empty set with every check assigned to a slice. For IMPLEMENT, the controller supplies exactly one current slice ID and disjoint completed-slice IDs; the completed set is mandatory even when explicitly empty, so omission cannot downgrade prior checks to future checks. The payload's TDD slice must match the controller-selected current ID exactly. Current/completed checks pass while future checks remain planned. TEST success requires every check at `PASS`. Every REVIEW status must match the same mapping. Context and acceptance text cannot be whitespace-only.

For Codex, the controller passes the schema with `--output-schema` and reads `--output-last-message`.

**Write scope and evidence citations.** SPECIFY, CLARIFY, PLAN, TASKS and TEST are analysis-only, so any reported `modified_paths`/`created_paths` is rejected. IMPLEMENT must receive controller-declared `editable_paths` for every status. Each pattern must be anchored and not match-all (`editable_pattern_is_safe`). Every written path must be repository-relative, non-traversing and matched segment by segment by one of them (`path_matches`; `*` does not cross `/`). A current-slice (IMPLEMENT) or TEST `PASS` from an `AGENT` verifier must cite, in backticks, one of the commands the controller bound to that check (`check_verifiers`), and that command must be recorded as exit `0` with `PASS` or be the GREEN command. "Done", a self-chosen command or another check's verifier is not evidence. **Read-only roles.** With `role` set to one of `READ_ONLY_ROLES` (`PROJECT_CONTEXT_GUARDIAN`, `DATA_FLOW_TRACER`) and only in that role's declared stages, an IMPLEMENT-stage result reports no writes and no TDD slices. It keeps unverified checks `PLANNED` without evidence and carries completed-slice checks forward as `PASS` with evidence. The editable-path, TDD and citation requirements do not apply to it. With `required_commands`, a successful IMPLEMENT/TEST must record each required verifier as passing.

**TDD evidence.** `IMPLEMENT` with status `SUCCESS` needs at least one slice with a non-zero RED exit, `red_failure_kind: EXPECTED_FUNCTIONAL`, and GREEN exit `0`. An infrastructure failure does not count as RED.

**Acceptance.** A non-zero CLI exit is a process failure. A zero exit with invalid JSON is `CONTRACT_INVALID`. Cited paths and symbols, ownership and TDD evidence are checked against the repository before a transition. A valid envelope is not proof that its commands actually ran.

## Review result

Used by `REVIEW` and the audit sub-agents. The root is `review_result`, with these fields: `status` (`APPROVED`, `CHANGES_REQUIRED`, `BLOCKED`), `reviewed_paths`, `findings` (severity, path, description, evidence), `baseline`, `ownership`, `acceptance` (`expected_check_ids`, independent verification and ID-matched evidence-backed checks), `e2e` (`files_modified`, `execution_performed` and `violation` are kept separate), `forbidden_actions`, `gate_status` and `next_step`.

`APPROVED` requires the controller's authoritative acceptance mapping, exact ID and criterion coverage with passing evidence, a preserved baseline with no violations, valid ownership with no violations, no unresolved findings and `focused_tests`/`format`/`analyze` at `PASS`. Baseline and ownership booleans must agree with their violation arrays for every review status. Green technical gates do not substitute for checking the accepted observable outcomes. Gate values are defined in [Gates](gates-and-stack-detection.md#gate-order-gatesmd). The reviewer is read-only and cannot declare DONE.

## `validate_protocol.py`

Validates a final-message JSON against the selected schema plus semantic rules (envelope/action match, material-context resolution, authoritative acceptance mapping, controller-selected current/completed slices, write scope, evidence citations, required verification, TDD evidence and path shapes). `validate_payload` and `validate_json_text` accept `expected_acceptance`, `current_slice_ids`, `completed_slice_ids`, `editable_paths`, `required_commands`, `check_verifiers` and `role`; `ANALYSIS_ONLY_ACTIONS` lists the stages that may not report file changes; every IMPLEMENT, TEST and REVIEW status fails closed unless both the authoritative acceptance mapping and returned check set are present and non-empty, and IMPLEMENT success additionally requires controller-owned current/completed slice inputs. The module never executes commands in the payload and never reads STATE.

```text
validate_protocol.py --action IMPLEMENT --result final-message.json --context verifier-context.json --json
```

Flags: `--action`, `--result`, `--context`, `--json`. `--context` takes the output of [`stage_context.py verifier-context`](harness.md#feeding-the-validator). Unknown keys or a value of the wrong type (for example `check_verifiers` given as a list) are rejected as structured errors. Exit `0` means valid; `2` means the result or the context is rejected and the errors are listed. Its behavior is pinned by `tests/test_protocol.py` ([Testing](testing.md#installed-controller-tests)).

Changing a schema is a contract change: bump the `SKILL.md` version and update this page, the example envelopes in both contracts, and `test_protocol.py`.
