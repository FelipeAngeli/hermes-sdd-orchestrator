# Review Result Contract

REVIEW is read-only. The reviewer cannot edit files, correct code, execute E2E, or declare the demand `DONE`.

## Controller routing and transport

For the requested action `REVIEW`, the controller selects `../schemas/REVIEW_RESULT_SCHEMA.json` before dispatch. The only accepted final-message document is JSON with root `review_result` and `schema_version: 3`. The controller rejects `executor_result` for REVIEW and never attempts to validate `review_result` with `../schemas/EXECUTOR_RESULT_SCHEMA.json`.

The final-message file is the only validator input. A transcript, JSONL event stream, Markdown response, or YAML document does not substitute for JSON. Process exit status and diagnostic output remain distinct from the review result.

## Reviewer input

The reviewer receives structured input with `protected_preexisting`, `agent_owned`, `files_changed_by_agent`, `generated_or_ignored`, `out_of_scope`, `test_files_changed`, `integration_test_files_changed`, `e2e_execution_performed`, forbidden actions, and gates. A dirty pre-existing file is never attributed to the agent merely because it is dirty.

Antes de emitir o resultado, o revisor avalia, na medida aplicável ao diff e com evidência objetiva, segurança, complexidade, estilo, testes, documentação e dependências. A avaliação não cria gates, percentuais, correções automáticas ou exigências fora do escopo da mudança.

For tasks in scope, receive their canonical requirement/design references and the controller's authoritative acceptance ID → criterion/slice mapping. Verify every mapped criterion against the actual changes and evidence, including integration between tasks. The controller supplies `expected_acceptance` for every REVIEW result, regardless of `status`. The worker's `expected_check_ids` is only an echo: IDs and criterion text must match the authoritative mapping exactly once for `APPROVED`, `CHANGES_REQUIRED` and `BLOCKED`. Record uncovered, substituted or contradictory acceptance criteria as findings; checkbox completion is not execution or approval evidence.

## Envelope

```json
{
  "review_result": {
    "schema_version": 3,
    "status": "APPROVED",
    "reviewed_paths": [{"path": "src/example.ext"}],
    "findings": [],
    "baseline": {"preserved": true, "violations": []},
    "ownership": {"valid": true, "violations": []},
    "acceptance": {
      "verified": true,
      "expected_check_ids": ["AC-1"],
      "checks": [{
        "id": "AC-1",
        "criterion": "The observable outcome matches the request",
        "verification_method": "Run the focused behavior check",
        "verifier": "AGENT",
        "slice_id": "slice-1",
        "status": "PASS",
        "evidence": "The focused behavior check passed against the delivered diff",
        "waiver": null
      }]
    },
    "e2e": {"files_modified": false, "execution_performed": false, "violation": false},
    "forbidden_actions": {"violations": []},
    "gate_status": {"focused_tests": "PASS", "format": "PASS", "analyze": "PASS", "ci": "DISABLED_BY_PROJECT_POLICY"},
    "next_step": {"action": "EVALUATE_DONE_WITH_CI_DISABLED"}
  }
}
```

The schema requires all properties shown above and rejects unknown properties. Required text must contain a non-whitespace character. `status` is `APPROVED`, `CHANGES_REQUIRED`, or `BLOCKED`. Findings preserve severity, path, description, and objective evidence. Baseline and ownership booleans must agree with whether their violation lists are empty for every review status. `acceptance.expected_check_ids` echoes the complete stable ID set supplied independently by the controller, and every check must match that authoritative ID, criterion, verification method, verifier and slice assignment exactly once; the review may add only status and evidence. Green gates alone are not product acceptance. E2E keeps separate `files_modified`, `execution_performed`, and `violation` values: modifying `integration_test` is not execution evidence.

Gate values preserve `PASS`, `FAIL`, `TIMEOUT`, `BLOCKED`, and `PENDING`; CI also permits `NOT_APPLICABLE` and `DISABLED_BY_PROJECT_POLICY`. The latter is not `PASS`.

## Approval policy and history

`APPROVED` requires independently verified acceptance with at least one evidence-backed check, every check `PASS` or `WAIVED` (a `WAIVED` check carries `waiver: {by, reason, quote, recorded_at}` and, for an `AGENT` check, matches the controller's `recorded_waivers`; every other check has `waiver: null`), preserved baseline, valid ownership, no unresolved findings or forbidden-action violations, and `focused_tests`, `format`, and `analyze` equal to `PASS`. After approval, the controller consults `../policies/GATES.md`: it may recommend `RUN_CI` when CI is enabled or `EVALUATE_DONE_WITH_CI_DISABLED` when the explicit project policy disables CI. The controller alone evaluates DONE.

Historical pre-version-3 review records remain valid history and are not rewritten or evaluated as version 3 payloads.