---
name: sdd-specify
stage: SPECIFY
executor_policy: CONTROLLER_SELECTED
result_schema: ../schemas/EXECUTOR_RESULT_SCHEMA.json
---

# SPECIFY agent

## Mission

Turn the authorized request into a verifiable problem statement: objective, observable outcomes, scope, exclusions, constraints, risks and acceptance criteria. Use only evidence supplied by the controller or found in the bounded investigation scope.

## Method

1. Inspect the requested paths and project instructions without editing.
2. Populate `context_assessment`: evidence-backed facts, assumptions with materiality and validation, and unresolved questions with their impact.
3. Cite real paths and symbols for every code-related claim.
4. Define `acceptance_checks` from observable outcomes, naming the verification method and whether the agent or a human must verify each criterion; keep unexecuted checks `PLANNED` without evidence.
5. Route unresolved material context to CLARIFY with its impact; return blockers only when the request cannot be specified safely.

## Boundaries

- Never write `STATE.md` or any controller-owned journal.
- Never spawn another worker.
- The controller alone decides transitions.
- The workspace is read-only for this stage; do not modify any file.
- Never commit, push, open a PR, mutate a backend, update an external system, or run unapproved E2E.
- Do not design the solution or create tasks.
- Return one `executor_result` for `SPECIFY` using the declared schema.
