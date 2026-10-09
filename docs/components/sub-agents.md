# Sub-agents and dispatch

[Docs index](../README.md) · Related: [Stage agents](stage-agents.md), [Contracts and schemas](contracts-and-schemas.md), [FSM and bounded loop](fsm-and-loop.md)

**Files:** `sub-agents/*.md`, `policies/DISPATCH_POLICY.md`.

Sub-agents are narrow, specialist leaf-worker briefs. The controller dispatches them directly, never another agent. They never own STATE or transitions, they count against the executor budget, and only one worker runs at a time. A successful specialist returns evidence but never completes a stage by itself.

## Dispatch policy: the default is to not dispatch

`DISPATCH_POLICY.md` inverts the usual permission. A sub-agent runs only when the controller can record three things, in the stage-context manifest's `dispatch` block (`stage_context.py check` refuses a role dispatch without it with `DISPATCH_QUESTION_REQUIRED`):

- `pending_decision`: the decision that depends on the answer.
- `deterministic_attempt`: which tool (grep, `git diff`, tests, analyzer, schema validation…) was tried first and why it was not enough.
- `if_empty`: what changes if the answer is `NO_FINDINGS`. If nothing changes, the dispatch is decorative and is skipped.

Each demand is classified by risk as `LOW`, `MEDIUM`, `HIGH` or `CRITICAL` and routed on the smallest safe path: `FAST`, `STANDARD` or `DEEP`. Only projects with explicit `--automatic-jev-governance` consent use Jev automatically; installing TypeSafe guidance is not consent. After deterministic tools, semantic classifications go through `runtime/semantic_governor.py decide` in one cached batch. Confidence below `0.70`, malformed output, provider failure or uncertainty returns `REVIEW`; failure tombstones prevent an automatic repeat charge for the unchanged fingerprint. Exact facts, permissions and FSM transitions never go to Jev. With that consent the policy is enforced: [`stage_context.py check`](harness.md#rules-enforced-by-check) refuses PLAN and IMPLEMENT unless the manifest cites a cached governor fingerprint for the ticket and a human resolution for any `REVIEW`. Iteration follows `OBSERVE → ANALYZE → ACT → VERIFY → LEARN`, bounded by the loop budgets; `correction_loop.py` stops with `NO_PROGRESS` when the evidence digest does not change.

The policy's routing table ("Routing suggestions by change shape") names, per change shape, the typical sub-agent — only shipped briefs — and the playbooks the stage worker loads; every row is still subject to the dispatch question.

## Catalogue

Four sub-agents ship. Each brief's frontmatter declares `role`, `allowed_stages`, `executor_policy: CONTROLLER_SELECTED` and `result_schema`. [`tests/test_docs.py`](testing.md#skill-suite-tests) checks that this table matches every brief.

| Brief | Role | Stages | Purpose | Result schema |
| --- | --- | --- | --- | --- |
| `sub-agents/project-context-guardian.md` | `PROJECT_CONTEXT_GUARDIAN` | SPECIFY, PLAN, IMPLEMENT | Cache-first project context: stack, rules, conventions, integrations. It is required before PLAN and IMPLEMENT, records code/doc divergences and returns verified vault updates, which the controller writes at once with [`wiki_journal.py`](obsidian-vault.md#recording-everything-in-the-wiki-wiki_journalpy) (the wiki is read and written; the role itself never writes), placed in the project's [LLM Wiki layout](obsidian-vault.md#project-container-layout-llm-wiki-wiki_layoutpy) (immutable `raw/`, pages in `entities/`, `concepts/`, `comparisons/`, `queries/`, with `index.md` and `log.md` entries). | `EXECUTOR_RESULT_SCHEMA.json` |
| `sub-agents/data-flow-tracer.md` | `DATA_FLOW_TRACER` | SPECIFY, CLARIFY, PLAN, TASKS, IMPLEMENT | One bounded code question, in one of three shapes the controller names: investigation (evidence for one pending decision), trace (one demand's path UI → state → service → repository → API and back, with contracts, side effects and risk points) or impact (blast radius of one contract or behavior change: call sites, registrations, mocks, fixtures, tests, classified required/conditional/out of scope). | `EXECUTOR_RESULT_SCHEMA.json` |
| `sub-agents/security-reviewer.md` | `SECURITY_REVIEWER` | REVIEW | Exploitable flaws plus disclosure of secrets, tokens and personal data. | `REVIEW_RESULT_SCHEMA.json` |
| `sub-agents/pr-reviewer.md` | `PR_REVIEWER` | REVIEW | One pull request reviewed as it will merge: scope, correctness, tests, checks, breaking changes, changelog and version, commits, mergeability. Audits every earlier review as confirmed, refuted or unaddressed. | `REVIEW_RESULT_SCHEMA.json` |

`project-context-guardian` and `data-flow-tracer` are the `READ_ONLY_ROLES` of `runtime/validate_protocol.py`: read-only in every allowed stage, including IMPLEMENT. The controller validates their results with `role` ([Contracts](contracts-and-schemas.md#executor-result)), building that context with `stage_context.py verifier-context --role` ([Harness](harness.md#feeding-the-validator)); `--role` accepts only those two names, and only in the role's own stages. The reviewers keep the workspace read-only, repair nothing, and report proven findings separately from suspicions. All briefs are language-neutral.

## Playbooks instead of specialist sub-agents

Specialist judgment that does not need an independent worker is a [project-local skill](project-local-skills.md) the stage worker loads itself, declared in the stage-context manifest's `playbooks`. Each [stage brief](stage-agents.md) has a `Playbooks` section naming which skills to load. This costs no dispatch, and the one-leaf-worker rule is unchanged.

| Removed brief | Now |
| --- | --- |
| `investigator`, `impact-analyst` | `data-flow-tracer` (investigation and impact shapes) |
| `spec-consistency-guardian` | `sdd-product-owner` (`references/traceability-audit.md`) |
| `architecture-guardian`, `dependency-auditor`, `performance-auditor` | `sdd-tech-lead` |
| `api-contract-auditor` | `sdd-api-contracts` |
| `migration-safety-auditor` | `sdd-database-design-migrations` (`references/rollout-safety-audit.md`) |
| `tdd-guardian` | `sdd-tdd` (`references/mutation-proof.md`) |
| `release-readiness-auditor`, `regression-hunter`, `documentation-writer` | `sdd-release-readiness` |
| `code-reviewer`, `tdd-implementer`, `test-runner` | the `review`, `implement` and `test` stage briefs, which absorbed their unique rules |

**Product owner and tech lead are knowledge, not approvers.** `sdd-product-owner` and `sdd-tech-lead` are never dispatched and never create a human checkpoint. An approval a request names is resolved through `PROJECT_SETUP.md` `approvers` (default: the requester); the requester's approval is recorded as a HUMAN check and an explicit waiver as `WAIVED` with a `waiver` record ([Contracts](contracts-and-schemas.md#executor-result)). Such a check never blocks IMPLEMENT unless the request literally says so.

## `pr-reviewer`: global use, mandatory here

`pr-reviewer` ships in every installation, so any project can dispatch it from the `Pull request` row of `DISPATCH_POLICY.md`. It works with any language and any host: the controller supplies the diff from the merge base, the commits, the description, the existing reviews and the check results.

The role guards against one failure: trusting the nearest summary. The description, the author's report, a green badge and an earlier approval are claims, and the diff decides. It never posts, approves, merges or rebases on its own. Deep findings name their owner: the `security-reviewer` or `data-flow-tracer` sub-agent, or a playbook (`sdd-tech-lead`, `sdd-tdd`, `sdd-api-contracts`, `sdd-database-design-migrations`, `sdd-release-readiness`, `sdd-product-owner`).

In **this** repository, every pull request and every review it already received must go through `pr-reviewer`, as required by `AGENTS.md` and the PR template.

**Adding a sub-agent:** first check whether a playbook would do; a sub-agent is justified only when the answer needs an independent worker. Then add the brief with complete frontmatter, add a row here and in the root [README](../../README.md#sub-agents), extend the expected set in `tests/test_sdd_orchestrator_skill.py`, and update `CHANGELOG.md`.
