# Bounded Automation Preview — Phase 2A

> **Escopo legado schema 1.** `BOUNDED_AUTO` usa preview e confirmação por rodada. `LOCAL_DELIVERY` não é um enum de modo: é o perfil `schema_version: 2` com `local_delivery` e `loop.mode: BOUNDED_AUTO`, ativado por pedido explícito de entrega local. Ele vincula autorização fixa a ticket, scope/hash, worktree e limites globais cumulativos; replan não renova uso nem pede nova confirmação.

## Scope

Phase 2A adds deterministic planning only. The planner accepts a normalized JSON snapshot and produces an immutable preview. It does not read or write `STATE.md`, run an executor, invoke tests or gates, mutate Git, activate `BOUNDED_AUTO`, or perform an external mutation.

`MANUAL` remains one explicitly requested action followed by pause. Schema 1 `BOUNDED_AUTO` requires explicit authorization for each exact plan. Schema 2 `LOCAL_DELIVERY` requires one explicit request authorization bound to ticket, scope, workspace and cumulative limits; replans consume that authorization without a new confirmation. `UNBOUNDED_AUTO` does not exist.

## Preview and authorization

Every automatic projection produces a deterministic plan targeted at `NEXT_HUMAN_CHECKPOINT`. In schema 1 it is a human-facing `BOUNDED RUN PREVIEW`; in schema 2 it is an execution projection under the persisted LOCAL_DELIVERY authorization. The plan identifies the ticket, initial stage, ordered actions, action classifications, executor or host, projected budget use, stop conditions, next checkpoint, and `plan_sha256`.

Persist the preview as the plan artifact before running validation; validation always reads that persisted artifact and the normalized snapshot. Do not validate an in-memory substitute.

The plan uses canonical UTF-8 JSON with sorted keys and deterministic separators. Its SHA-256 is calculated without the `authorization.plan_sha256` field. In schema 1, authorization remains bound to that exact hash, but the user does not need to copy it: an unambiguous affirmative response immediately after the preview, such as `sim`, `autorizo`, `pode iniciar`, or `continue`, authorizes only the most recently presented `plan_sha256` in the current conversation. The confirmation is not reusable for future plans. In schema 2, the hash remains an integrity and runtime-binding value, not a new approval request; `authorization.required` and `expires_on_state_change` are both `false`.

For schema 1, the explicit hash form remains accepted for asynchronous or otherwise ambiguous authorization:

```text
Autorizo o plano BOUNDED_AUTO:
<plan_sha256>
```

For schema 1, an affirmative response is valid only as a direct reply to the most recent preview; ambiguous or unrelated messages are not authorization. Before activating a new round, Hermes must recompute the STATE hash, worktree identity, budgets, and recovery decision. Any difference makes the preview `PLAN_STALE`; it must be recalculated and approved again. For schema 2, expected STATE and budget progress causes a replan under the same fixed authorization, while ticket, scope, workspace or cumulative-limit drift stops execution. After activation in either profile, in-round progress is evaluated by `bounded_run_driver.py` and is not by itself `PLAN_STALE`.

## Target and classifications

The default target is `NEXT_HUMAN_CHECKPOINT`: continue only while the next action is `AUTO_SAFE` or `AUTO_WITH_BUDGET`, budgets are available, no stop condition occurs, and no human authorization is required.

- `AUTO_SAFE`: local deterministic actions without model calls or external mutation, including recovery reconciliation, focused tests, changed-Dart formatting, analysis, budget and ownership checks, transactional STATE update, DONE evaluation, and run closure.
- `AUTO_WITH_BUDGET`: `SPECIFY`, `CLARIFY`, `PLAN`, `TASKS`, `IMPLEMENT_SLICE`, `REVIEW`, and policy-enabled `CI`.
- `HUMAN_REQUIRED`: retry-exhausted stage reopening, inconclusive recovery, protected-file changes, material scope or architecture choices, backend mutation, DEV E2E, commit, push, Linear, Obsidian writes, destructive actions, and unplanned external-contract changes.

The planner stops at the first human requirement, blocker, gate or contract failure, budget exhaustion, inconclusive recovery, protected file, material scope change, external mutation, or `DONE`.

## Recovery and future execution

A recovery probe is evaluated before planning dispatch. `RECONCILE_ARTIFACT` and `STATE_COMMIT_REQUIRED` make `RECOVER_PENDING_ACTION` the first planned action. `WAIT_OR_MANUAL_REVIEW` ends at a human checkpoint; `BLOCKED` permits no automatic action; `ALREADY_COMMITTED` never repeats the executor or operational commit; `RELEASED` may evaluate the state’s next action. A plan never redispatches an action with a pending artifact.

In an authorized execution—an approved schema 1 round or schema 2 LOCAL_DELIVERY—each action remains individual: reread STATE, consult `bounded_run_driver.py next`, run recovery probe, prepare ACTION_JOURNAL, execute one action, validate it, persist STATE atomically, verify `STATE_COMMITTED`, release, rollover, and then consult the driver again. `EXECUTE_NEXT` requires the next planned action in the same turn. `ROLLOVER_REQUIRED` must complete to pristine IDLE plus `DISPATCH_ALLOWED` before another dispatch. A stop decision ends the round and records its explicit stop reason; a healthy success alone never ends a BOUNDED_AUTO turn.

Expected progress after a completed action — STATE hash, budgets used, stage, and recovery `DISPATCH_ALLOWED` after rollover — is not `PLAN_STALE`. Workspace identity, ticket, or budget-limit drift is.

## Runtime driver

```text
python3 .hermes/orchestration/runtime/bounded_run_driver.py next --snapshot snapshot.json --plan plan.json --json
```

The driver never invokes an executor, writes STATE, mutates Git, or activates `BOUNDED_AUTO`. There is no `--execute`, `--apply`, `--force`, `--run`, or auto-approval option.

`bind` accepts a normalized snapshot without `runtime` plus a persisted plan which passes `planner.validate_plan`. It returns the complete snapshot with exact `RUNTIME_KEYS`, plan/hash binding, zero cursor/count and predecessor STATE hash; it does not read or write STATE. `--started-at` is explicit so bind does not invent a clock value. Schema 1 additionally requires the exact `--approved-plan-sha256`; schema 2 relies on its already-validated LOCAL_DELIVERY authorization and does not project a new approval. The controller, not this CLI, confirms human evidence, identity and real STATE.

```text
python .hermes/orchestration/runtime/bounded_run_driver.py bind --snapshot snapshot.json --plan plan.json --started-at 2026-09-17T00:00:00Z --json
```

## Planner interface

```text
python3 .hermes/orchestration/runtime/bounded_run_planner.py --help
python3 .hermes/orchestration/runtime/bounded_run_planner.py plan --snapshot snapshot.json --target NEXT_HUMAN_CHECKPOINT --json
python3 .hermes/orchestration/runtime/bounded_run_planner.py validate --plan plan.json --snapshot snapshot.json --json
python3 .hermes/orchestration/runtime/bounded_run_planner.py classify --action SPECIFY --json
```

There is deliberately no `execute`, `apply`, `force`, budget/recovery bypass, or auto-approval option.
