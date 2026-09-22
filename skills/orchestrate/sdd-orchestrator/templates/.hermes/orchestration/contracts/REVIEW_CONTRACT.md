# Review Result Contract

REVIEW is read-only. The reviewer cannot edit files, correct code, execute E2E, or declare the demand `DONE`.

## Controller routing and transport

For the requested action `REVIEW`, the controller selects `../schemas/REVIEW_RESULT_SCHEMA.json` before dispatch. The only accepted final-message document is JSON with root `review_result` and `schema_version: 2`. The controller rejects `executor_result` for REVIEW and never attempts to validate `review_result` with `../schemas/EXECUTOR_RESULT_SCHEMA.json`.

The final-message file is the only validator input. A transcript, JSONL event stream, Markdown response, or YAML document does not substitute for JSON. Process exit status and diagnostic output remain distinct from the review result.

## Reviewer input

The reviewer receives structured input with `protected_preexisting`, `agent_owned`, `files_changed_by_agent`, `generated_or_ignored`, `out_of_scope`, `test_files_changed`, `integration_test_files_changed`, `e2e_execution_performed`, forbidden actions, and gates. A dirty pre-existing file is never attributed to the agent merely because it is dirty.

Antes de emitir o resultado, o revisor avalia, na medida aplicável ao diff e com evidência objetiva, segurança, complexidade, estilo, testes, documentação e dependências. A avaliação não cria gates, percentuais, correções automáticas ou exigências fora do escopo da mudança.

For tasks in scope, receive their canonical requirement/design references and verify them against the actual changes and evidence, including integration between tasks. Record uncovered or contradictory acceptance criteria as findings; checkbox completion is not execution or approval evidence.

## Envelope

```json
{
  "review_result": {
    "schema_version": 2,
    "status": "APPROVED",
    "reviewed_paths": [{"path": "lib/example.dart"}],
    "findings": [],
    "baseline": {"preserved": true, "violations": []},
    "ownership": {"valid": true, "violations": []},
    "e2e": {"files_modified": false, "execution_performed": false, "violation": false},
    "forbidden_actions": {"violations": []},
    "gate_status": {"focused_tests": "PASS", "format": "PASS", "analyze": "PASS", "ci": "DISABLED_BY_PROJECT_POLICY"},
    "next_step": {"action": "EVALUATE_DONE_WITH_CI_DISABLED"}
  }
}
```

The schema requires all properties shown above and rejects unknown properties. `status` is `APPROVED`, `CHANGES_REQUIRED`, or `BLOCKED`. Findings preserve severity, path, description, and objective evidence. Baseline and ownership preserve their booleans and violation path lists. E2E keeps separate `files_modified`, `execution_performed`, and `violation` values: modifying `integration_test` is not execution evidence.

Gate values preserve `PASS`, `FAIL`, `TIMEOUT`, `BLOCKED`, and `PENDING`; CI also permits `NOT_APPLICABLE` and `DISABLED_BY_PROJECT_POLICY`. The latter is not `PASS`.

## Approval policy and history

`APPROVED` requires preserved baseline, valid ownership, no unresolved findings or forbidden-action violations, and `focused_tests`, `format`, and `analyze` equal to `PASS`. After approval, the controller consults `../policies/GATES.md`: it may recommend `RUN_CI` when CI is enabled or `EVALUATE_DONE_WITH_CI_DISABLED` when the explicit project policy disables CI. The controller alone evaluates DONE.

Historical PRE_V2 review records remain valid history and are not rewritten or evaluated as version 2 payloads.