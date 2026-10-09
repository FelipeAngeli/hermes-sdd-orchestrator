# Dispatch Policy — selective sub-agent routing

## DEFAULT = DO NOT DISPATCH

A sub-agent runs only when the controller can state which pending decision depends on its answer.

This inverts the previous rule. The bundle said the controller *may* select a specialist, which is a permission with no refusal condition: every brief added made every demand potentially more expensive, and nothing in the protocol ever said *no*. Not dispatching is now the default, and dispatching is the exception that must be justified.

## The dispatch question

Before dispatching any sub-agent, record three things in the stage-context manifest's `dispatch` block (`schemas/STAGE_CONTEXT_SCHEMA.json`; `stage_context.py check` refuses a role dispatch without it with `DISPATCH_QUESTION_REQUIRED`, and `sdd.py manifest` fills it for the guardian):

- `pending_decision` — name the pending decision the answer resolves. Not a topic, a decision: "whether slice S3 may proceed without a migration", not "check the database".
- `deterministic_attempt` — record which deterministic tool was tried and why it was insufficient.
- `if_empty` — what changes if the sub-agent returns `NO_FINDINGS`.

If no decision changes, do not dispatch. An answer that leaves every choice identical is waste, however interesting it reads.

If `if_empty` and the finding case lead to the same next action, the dispatch is decorative: the controller has already decided and is buying reassurance with budget.

## Deterministic precedence

Deterministic tools run before any sub-agent. An LLM worker is authorized only for a question these cannot answer:

`grep` and structural search, `git diff` and `git log`, the test suite, the analyzer, the linter, schema validation, AST queries, the package manager, and any project script already in the repository.

These are exact, cheap and reproducible. A sub-agent that re-derives what `grep` already proves spends budget to produce a less reliable answer. When a deterministic tool answers the question, the dispatch does not happen and the tool output is the evidence.

Escalate to a sub-agent when the question requires judgment the tools cannot supply: whether a test proves a rule, whether a consumer's green suite actually exercises the changed path, whether a divergence is intentional.

## Automatic Jev semantic governance

Only explicit automatic Jev consent recorded by `--automatic-jev-governance` (`automatic_semantic_governance: true`) is standing authorization; installing TypeSafe guidance is not consent, and legacy install-only answers never authorize calls. After that one-time consent, do not ask again for each call. Deterministic facts, calculations, exact lookups, Git state, schema checks, commands, permissions and final FSM transitions remain in code and never go to Jev.

After deterministic precedence, send **every non-deterministic semantic classification** needed for the unchanged demand through `runtime/semantic_governor.py decide`. Put risk, optional-context relevance and eligible-specialist questions into one batch so an unchanged demand costs at most one paid call. Consult its cache first; call Jev again only when the canonical semantic fingerprint changes because material state, evidence, candidates, provider, model or policy changed.

The governor accepts a judgment only at confidence `0.70` or higher. A lower-confidence, malformed, unavailable or uncertain result is `REVIEW`: never fabricate a route, silently substitute a main-model classification, or automatically retry a possibly billed request. Jev selects only options supplied by the controller; it cannot invent paths, roles, commands or permissions. Every live use prints the `JEV USADO` terminal block with evaluated-file count, decided-versus-review totals and the fingerprint artifact; cache hits make no paid call.

This is enforced, not advisory: with automatic consent, `runtime/stage_context.py check` refuses a PLAN or IMPLEMENT manifest that lacks `semantic_governance`, whose fingerprint is not a cached governor report for the same ticket, or whose `REVIEW` outcome has neither a recorded human `review_resolution` nor a validated `SHADOW` request whose complete control binding recomputes to that exact cached fingerprint. A standalone caller-controlled `mode: SHADOW` never bypasses review.

## Risk classification

Classify every demand before routing it; when the classification is not fixed by the deterministic definitions below, use the semantic governor above:

- `LOW` — isolated change, no contract, no shared state, no external effect. Copy, styling, a constant, a message.
- `MEDIUM` — behavior inside one module, existing contracts unchanged.
- `HIGH` — shared contract, cross-module behavior, persistence, authorization, money, or anything with irreversible effect.
- `CRITICAL` — production data, credentials, migrations, or a change whose failure cannot be rolled back by redeploying.

Classification is recorded with the reason. An unclassified demand is treated as `HIGH` until classified — unknown risk is not low risk.

## Execution paths

- `FAST` — `LOW` risk. Implement and verify. No audit sub-agent unless the change touches something the classification missed.
- `STANDARD` — `MEDIUM` risk. Implement, verify, review. Specialists only where a named decision requires one.
- `DEEP` — `HIGH` or `CRITICAL`. Impact, contract, architecture and regression work as the change actually demands.

The controller chooses the smallest safe path. Escalating beyond the minimum path requires a recorded reason naming the specific risk that the smaller path leaves unexamined; de-escalating below the classified path is not permitted.

Routing suggestions by change shape, each still subject to the dispatch question:

| Change | Typical specialists | Playbooks the stage worker loads |
| --- | --- | --- |
| UI or copy | none beyond implement, test, review | `sdd-frontend-engineering` |
| API or contract | `data-flow-tracer` (impact) when the contract is shared | `sdd-api-contracts`, `sdd-tech-lead` |
| Architectural | `data-flow-tracer` (impact) | `sdd-tech-lead`, `sdd-architecture-decisions` |
| Database schema/data migration | none; the rollout-safety audit is a playbook reference | `sdd-database-design-migrations` |
| Pre-release | `security-reviewer` when the change touches its surfaces | `sdd-release-readiness`, `sdd-tdd` |
| Pull request | `pr-reviewer` for the whole PR and its prior reviews; it names the owner when a deeper finding needs one | `sdd-product-owner`, `sdd-tech-lead` |

A row in this table is a starting point, never an obligation. A specialist listed here and not needed for a named decision is still not dispatched. The specialist column names only briefs that ship in `sub-agents/`, and the playbook column only skills that ship in `.hermes/skills/` (`project-context-guardian`, `data-flow-tracer`, `pr-reviewer`, `security-reviewer`); adding a row for a role that does not exist would send the controller looking for a brief it cannot load.

Playbooks are project-local skills under `.hermes/skills/` that the stage worker loads itself, declared in the stage-context manifest's playbooks list. They cost no dispatch and are never a separate worker. Product owner (`sdd-product-owner`) and tech lead (`sdd-tech-lead`) are playbooks, not sub-agents and not approver gates: an approval a request names is resolved through the approvers record in `PROJECT_SETUP.md` (default: the requester) and recorded as a HUMAN check or `WAIVED` evidence, and it never blocks IMPLEMENT unless the request literally says so.

## Bounded iteration

The loop is `OBSERVE → ANALYZE → ACT → VERIFY → LEARN`, and the controller decides after `LEARN` whether another iteration is warranted. Its bounds are the budgets of `LOOP_POLICY.md` §4 and the LOCAL_DELIVERY limits of `BOUNDED_AUTOMATION.md`; this policy adds no separate iteration counter.

Stop when the objective is met, when the evidence is sufficient for the pending decision, when a blocker requires a human, or when any budget is exhausted.

A repeated iteration over unchanged evidence is forbidden. `runtime/correction_loop.py decide` enforces it: it compares each attempt's evidence digest with the previous one and stops with `NO_PROGRESS` (unchanged evidence) or `NO_NEW_HYPOTHESIS` (a hypothesis already tried) instead of re-analyzing. Loops that re-run analysis without new evidence burn budget to produce the conclusion they already had.

## Result discipline

A sub-agent that finds nothing returns `NO_FINDINGS` explicitly. That is a successful, informative result: it tells the controller the decision can proceed. A role that treats an empty result as failure will manufacture findings to appear useful, and manufactured findings cost more than the dispatch that produced them.

Output stays short, structured and machine-readable where the result schema allows it. Prose that restates the evidence without deciding anything is not a result.
