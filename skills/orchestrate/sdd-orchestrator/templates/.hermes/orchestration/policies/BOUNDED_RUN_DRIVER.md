# Bounded Run Runtime Driver — Phase 2B

## Scope

`bounded_run_driver.py` is the pure, local runtime decision engine for an already approved `BOUNDED_AUTO` plan. It does not activate a run, execute an executor, write `STATE.md`, mutate Git, or bypass recovery, budgets, or authorization.

The controller owns all effects. For each isolated action it must:

1. read and normalize STATE plus the recovery result;
2. call `next` with the approved plan;
3. when `EXECUTE_NEXT`, prepare the journal and execute exactly that action;
4. validate its result, persist STATE, verify `STATE_COMMITTED`, and release;
5. when another plan action remains, call `next` again; `ROLLOVER_REQUIRED` must be resolved by rollover before the next dispatch;
6. after rollover requires `DISPATCH_ALLOWED`, call `next` again in the same bounded round.

A successful action does not end a `BOUNDED_AUTO` round. `MANUAL` remains one requested action followed by pause.

## Runtime snapshot

The driver accepts the Phase 2A snapshot plus a `runtime` object owned by the controller:

```json
{
  "current_plan_id": "brp-...",
  "approved_plan_sha256": "<exact approved hash>",
  "planned_action_count": 3,
  "current_sequence": 1,
  "actions_executed": 1,
  "started_at": "2026-09-16T00:00:00Z",
  "last_action_id": "action-1",
  "expected_predecessor_state_sha256": "<persisted STATE hash>"
}
```

The controller increments `current_sequence`, `actions_executed`, budget counters, and `last_action_id` exactly once after an accepted action. It must set `expected_predecessor_state_sha256` only after it rereads the verified persisted STATE.

## Commands

```text
python3 .hermes/orchestration/runtime/bounded_run_driver.py inspect --snapshot snapshot.json --plan plan.json --json
python3 .hermes/orchestration/runtime/bounded_run_driver.py validate --snapshot snapshot.json --plan plan.json --json
python3 .hermes/orchestration/runtime/bounded_run_driver.py next --snapshot snapshot.json --plan plan.json --json
```

The possible continuation decisions are `EXECUTE_NEXT`, `ROLLOVER_REQUIRED`, `STOP_BUDGET`, `STOP_HUMAN_REQUIRED`, `STOP_BLOCKED`, `STOP_PLAN_STALE`, `STOP_RECOVERY`, and `COMPLETE`. `COMPLETE` also records a manual or already-inactive loop pause without authorizing a new action. There are no force, ignore, execute, apply, or unbounded options.

## Stop and observability

Every decision returns a structured event containing sequence, action, decision, budget before/after, recovery before/after, journal action id, state hashes, and stop reason. The driver is pure, so the before and after values are the same immutable decision snapshot; the controller records a distinct post-action event after it performs a journal, STATE, or recovery mutation. A stop returns STATE update data that preserves the next action, clears the current action, records the explicit `stop_reason`, pauses the loop (except DONE), and snapshots coherent run counters for `last_run`.

When `stage_transitions.used == max` after `PLAN → TASKS`, the driver returns `STOP_BUDGET` with `STAGE_TRANSITION_BUDGET_REACHED`; it does not dispatch `TASKS`. A later `next` after that pause must keep `STAGE_TRANSITION_BUDGET_REACHED`. `PAUSED` is not `MANUAL`, so the last reading must not rewrite the round as `COMPLETE` / `MANUAL_ACTION_COMPLETE`.
