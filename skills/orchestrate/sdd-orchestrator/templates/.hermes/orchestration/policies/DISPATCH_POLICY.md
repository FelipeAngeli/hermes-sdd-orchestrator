# Dispatch Policy — selective sub-agent routing

## DEFAULT = DO NOT DISPATCH

A sub-agent runs only when the controller can state which pending decision depends on its answer.

This inverts the previous rule. The bundle said the controller *may* select a specialist, which is a permission with no refusal condition: every brief added made every demand potentially more expensive, and nothing in the protocol ever said *no*. Not dispatching is now the default, and dispatching is the exception that must be justified.

## The dispatch question

Before dispatching any sub-agent, record three things in the action journal entry:

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

## Risk classification

Classify every demand before routing it:

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

| Change | Typical specialists |
| --- | --- |
| UI or copy | none beyond implement, test, review |
| API or contract | `api-contract-auditor`, then `impact-analyst` when the contract is shared |
| Architectural | `impact-analyst`, `architecture-guardian` |
| Database schema/data migration | `migration-safety-auditor` when rollout, mixed-version compatibility or recovery remains a pending decision |
| Pre-release | `regression-hunter`, `security-reviewer` when the change touches its surfaces |
| Pull request | `pr-reviewer` for the whole PR and its prior reviews; it names the specialists above when a deeper finding needs one |

A row in this table is a starting point, never an obligation. A specialist listed here and not needed for a named decision is still not dispatched. The table names only briefs that ship in `sub-agents/`; adding a row for a role that does not exist would send the controller looking for a brief it cannot load.

## Bounded iteration

The loop is `OBSERVE → ANALYZE → ACT → VERIFY → LEARN`, and the controller decides after `LEARN` whether another iteration is warranted.

Every run declares `max_iterations` before starting, alongside the budgets already defined in `BOUNDED_AUTOMATION.md` and `LOOP_POLICY.md`, which this policy does not replace.

Stop when the objective is met, when the evidence is sufficient for the pending decision, when a blocker requires a human, or when any budget is exhausted.

A repeated iteration over unchanged evidence is forbidden. Record an evidence digest for each iteration; if an iteration ends with the same digest it began with, the loop has stopped learning and must terminate rather than re-analyze. Loops that re-run analysis without new evidence burn budget to produce the conclusion they already had.

## Result discipline

A sub-agent that finds nothing returns `NO_FINDINGS` explicitly. That is a successful, informative result: it tells the controller the decision can proceed. A role that treats an empty result as failure will manufacture findings to appear useful, and manufactured findings cost more than the dispatch that produced them.

Output stays short, structured and machine-readable where the result schema allows it. Prose that restates the evidence without deciding anything is not a result.
