# Sub-agents and dispatch

[Docs index](../README.md) · Related: [Stage agents](stage-agents.md), [Contracts and schemas](contracts-and-schemas.md), [FSM and bounded loop](fsm-and-loop.md)

**Files:** `sub-agents/*.md`, `policies/DISPATCH_POLICY.md`.

Sub-agents are narrow, specialist leaf-worker briefs. The controller dispatches them directly, never another agent. They never own STATE or transitions, they count against the executor budget, and only one worker runs at a time. A successful specialist returns evidence but never completes a stage by itself.

## Dispatch policy: the default is to not dispatch

`DISPATCH_POLICY.md` inverts the usual permission. A sub-agent runs only when the controller can record three things:

- `pending_decision`: the decision that depends on the answer.
- `deterministic_attempt`: which tool (grep, `git diff`, tests, analyzer, schema validation…) was tried first and why it was not enough.
- `if_empty`: what changes if the answer is `NO_FINDINGS`. If nothing changes, the dispatch is decorative and is skipped.

Each demand is classified by risk as `LOW`, `MEDIUM`, `HIGH` or `CRITICAL` (an unclassified demand counts as `HIGH`) and routed on the smallest safe path: `FAST`, `STANDARD` or `DEEP`. Iteration follows `OBSERVE → ANALYZE → ACT → VERIFY → LEARN`, with a declared `max_iterations` and a stop as soon as an iteration brings no new evidence.

## Catalogue

Each brief's frontmatter declares `role`, `allowed_stages`, `executor_policy: CONTROLLER_SELECTED` and `result_schema`. [`tests/test_docs.py`](testing.md#skill-suite-tests) checks that this table matches every brief.

| Brief | Role | Stages | Purpose | Result schema |
| --- | --- | --- | --- | --- |
| `sub-agents/project-context-guardian.md` | `PROJECT_CONTEXT_GUARDIAN` | SPECIFY, PLAN | Cache-first project context: stack, rules, conventions, integrations. | `EXECUTOR_RESULT_SCHEMA.json` |
| `sub-agents/investigator.md` | `INVESTIGATOR` | SPECIFY, CLARIFY, PLAN | Bounded evidence for one controller-defined question. | `EXECUTOR_RESULT_SCHEMA.json` |
| `sub-agents/data-flow-tracer.md` | `DATA_FLOW_TRACER` | PLAN, IMPLEMENT | One demand's path UI → state → service → repository → API and back. | `EXECUTOR_RESULT_SCHEMA.json` |
| `sub-agents/impact-analyst.md` | `IMPACT_ANALYST` | PLAN, TASKS | Full blast radius of one contract or behavior change. | `EXECUTOR_RESULT_SCHEMA.json` |
| `sub-agents/tdd-implementer.md` | `TDD_IMPLEMENTER` | IMPLEMENT | One authorized slice under strict RED → GREEN. | `EXECUTOR_RESULT_SCHEMA.json` |
| `sub-agents/test-runner.md` | `TEST_RUNNER` | TEST | Focused test execution with exact commands and exit codes. | `EXECUTOR_RESULT_SCHEMA.json` |
| `sub-agents/documentation-writer.md` | `DOCUMENTATION_WRITER` | IMPLEMENT, REVIEW | Realign docs, ADRs, README and diagrams with the code. It is the only writing audit role. | `EXECUTOR_RESULT_SCHEMA.json` |
| `sub-agents/code-reviewer.md` | `CODE_REVIEWER` | REVIEW | Changes against requirements, ownership, tests and gates. | `REVIEW_RESULT_SCHEMA.json` |
| `sub-agents/security-reviewer.md` | `SECURITY_REVIEWER` | REVIEW | Exploitable flaws plus disclosure of secrets, tokens and personal data. | `REVIEW_RESULT_SCHEMA.json` |
| `sub-agents/tdd-guardian.md` | `TDD_GUARDIAN` | TEST, REVIEW | Proves weak tests by mutating production code. | `REVIEW_RESULT_SCHEMA.json` |
| `sub-agents/regression-hunter.md` | `REGRESSION_HUNTER` | TEST, REVIEW | Runs untouched consumers' suites to see what stopped working. | `REVIEW_RESULT_SCHEMA.json` |
| `sub-agents/api-contract-auditor.md` | `API_CONTRACT_AUDITOR` | PLAN, REVIEW | Client models against spec and deployed server, with ranked sources. | `REVIEW_RESULT_SCHEMA.json` |
| `sub-agents/performance-auditor.md` | `PERFORMANCE_AUDITOR` | PLAN, REVIEW | Costs backed by a measurement or counted operation only. | `REVIEW_RESULT_SCHEMA.json` |
| `sub-agents/architecture-guardian.md` | `ARCHITECTURE_GUARDIAN` | PLAN, REVIEW | Violations of the project's declared rules only, never taste. | `REVIEW_RESULT_SCHEMA.json` |
| `sub-agents/spec-consistency-guardian.md` | `SPEC_CONSISTENCY_GUARDIAN` | TASKS, REVIEW | Breaks in SPEC → PLAN → TASKS → CODE → TESTS. It never infers a requirement. | `REVIEW_RESULT_SCHEMA.json` |
| `sub-agents/dependency-auditor.md` | `DEPENDENCY_AUDITOR` | PLAN, REVIEW | Versions, duplication, maintenance. It prefers what the project already has. | `REVIEW_RESULT_SCHEMA.json` |
| `sub-agents/release-readiness-auditor.md` | `RELEASE_READINESS_AUDITOR` | REVIEW | `READY`, `BLOCKED` or `READY_WITH_RISK` (the last requires a named human). | `REVIEW_RESULT_SCHEMA.json` |

Audit roles keep the workspace read-only, revert every temporary step, repair nothing, and report proven findings separately from suspicions. All briefs are language-neutral.

**Adding a sub-agent:** add the brief with complete frontmatter, add a row here and in the root [README](../../README.md#sub-agents), extend the expected set in `tests/test_sdd_orchestrator_skill.py`, and update `CHANGELOG.md`.
