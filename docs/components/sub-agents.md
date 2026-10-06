# Sub-agents and dispatch

[Docs index](../README.md) · Related: [Stage agents](stage-agents.md), [Contracts and schemas](contracts-and-schemas.md), [FSM and bounded loop](fsm-and-loop.md)

**Files:** `sub-agents/*.md`, `policies/DISPATCH_POLICY.md`.

Sub-agents are narrow, specialist leaf-worker briefs. The controller dispatches them directly, never another agent. They never own STATE or transitions, they count against the executor budget, and only one worker runs at a time. A successful specialist returns evidence but never completes a stage by itself.

## Dispatch policy: the default is to not dispatch

`DISPATCH_POLICY.md` inverts the usual permission. A sub-agent runs only when the controller can record three things:

- `pending_decision`: the decision that depends on the answer.
- `deterministic_attempt`: which tool (grep, `git diff`, tests, analyzer, schema validation…) was tried first and why it was not enough.
- `if_empty`: what changes if the answer is `NO_FINDINGS`. If nothing changes, the dispatch is decorative and is skipped.

Each demand is classified by risk as `LOW`, `MEDIUM`, `HIGH` or `CRITICAL` and routed on the smallest safe path: `FAST`, `STANDARD` or `DEEP`. Only projects with explicit `--automatic-jev-governance` consent use Jev automatically; installing TypeSafe guidance is not consent. After deterministic tools, semantic classifications go through `runtime/semantic_governor.py decide` in one cached batch. Confidence below `0.70`, malformed output, provider failure or uncertainty returns `REVIEW`; failure tombstones prevent an automatic repeat charge for the unchanged fingerprint. Exact facts, permissions and FSM transitions never go to Jev. With that consent the policy is enforced: [`stage_context.py check`](harness.md#rules-enforced-by-check) refuses PLAN and IMPLEMENT unless the manifest cites a cached governor fingerprint for the ticket and a human resolution for any `REVIEW`. Iteration follows `OBSERVE → ANALYZE → ACT → VERIFY → LEARN`, with a declared `max_iterations` and a stop when no new evidence appears.

## Catalogue

Each brief's frontmatter declares `role`, `allowed_stages`, `executor_policy: CONTROLLER_SELECTED` and `result_schema`. [`tests/test_docs.py`](testing.md#skill-suite-tests) checks that this table matches every brief.

| Brief | Role | Stages | Purpose | Result schema |
| --- | --- | --- | --- | --- |
| `sub-agents/project-context-guardian.md` | `PROJECT_CONTEXT_GUARDIAN` | SPECIFY, PLAN, IMPLEMENT | Cache-first project context: stack, rules, conventions, integrations. It is required before PLAN and IMPLEMENT, records code/doc divergences and only proposes vault updates, placed in the project's [LLM Wiki layout](obsidian-vault.md#project-container-layout-llm-wiki-wiki_layoutpy) (immutable `raw/`, pages in `entities/`, `concepts/`, `comparisons/`, `queries/`, with `index.md` and `log.md` entries). | `EXECUTOR_RESULT_SCHEMA.json` |
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
| `sub-agents/migration-safety-auditor.md` | `MIGRATION_SAFETY_AUDITOR` | PLAN, REVIEW | Database rollout order, mixed-version compatibility, data conversion, locks, restartability and recovery for a concrete migration. | `REVIEW_RESULT_SCHEMA.json` |
| `sub-agents/spec-consistency-guardian.md` | `SPEC_CONSISTENCY_GUARDIAN` | TASKS, REVIEW | Breaks in SPEC → PLAN → TASKS → CODE → TESTS. It never infers a requirement. | `REVIEW_RESULT_SCHEMA.json` |
| `sub-agents/dependency-auditor.md` | `DEPENDENCY_AUDITOR` | PLAN, REVIEW | Versions, duplication, maintenance. It prefers what the project already has. | `REVIEW_RESULT_SCHEMA.json` |
| `sub-agents/pr-reviewer.md` | `PR_REVIEWER` | REVIEW | One pull request reviewed as it will merge: scope, correctness, tests, checks, breaking changes, changelog and version, commits, mergeability. Audits every earlier review as confirmed, refuted or unaddressed. | `REVIEW_RESULT_SCHEMA.json` |
| `sub-agents/release-readiness-auditor.md` | `RELEASE_READINESS_AUDITOR` | REVIEW | `READY`, `BLOCKED` or `READY_WITH_RISK` (the last requires a named human). | `REVIEW_RESULT_SCHEMA.json` |

`project-context-guardian` and `data-flow-tracer` are read-only even when dispatched during IMPLEMENT; the controller validates their results with `role` ([Contracts](contracts-and-schemas.md#executor-result)), building that context with `stage_context.py verifier-context --role` ([Harness](harness.md#feeding-the-validator)). Audit roles keep the workspace read-only, revert every temporary step, repair nothing, and report proven findings separately from suspicions. All briefs are language-neutral.

Database work loads `sdd-database-design-migrations` for reusable planning/implementation procedure. The controller dispatches `migration-safety-auditor` only when a concrete rollout, mixed-version, data-preservation, lock or recovery judgment remains pending after deterministic schema/SQL/test inspection. Query cost routes to `performance-auditor`, layer violations to `architecture-guardian`, wire models to `api-contract-auditor` and disclosure/auth concerns to `security-reviewer`; the migration role does not duplicate them.

## `pr-reviewer`: global use, mandatory here

`pr-reviewer` ships in every installation, so any project can dispatch it from the `Pull request` row of `DISPATCH_POLICY.md`. It works with any language and any host: the controller supplies the diff from the merge base, the commits, the description, the existing reviews and the check results.

The role guards against one failure: trusting the nearest summary. The description, the author's report, a green badge and an earlier approval are claims, and the diff decides. It never posts, approves, merges or rebases on its own. Deep findings go to the owning specialist (`security-reviewer`, `dependency-auditor`, `architecture-guardian`, `regression-hunter`, `tdd-guardian`, `api-contract-auditor`).

In **this** repository, every pull request and every review it already received must go through `pr-reviewer`, as required by `AGENTS.md` and the PR template.

**Adding a sub-agent:** add the brief with complete frontmatter, add a row here and in the root [README](../../README.md#sub-agents), extend the expected set in `tests/test_sdd_orchestrator_skill.py`, and update `CHANGELOG.md`.
