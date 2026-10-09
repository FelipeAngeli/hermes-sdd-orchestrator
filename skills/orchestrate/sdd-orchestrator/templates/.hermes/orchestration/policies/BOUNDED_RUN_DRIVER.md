# Bounded Run Driver

`runtime/bounded_run_driver.py` is the **only** bounded-run driver: a pure decision engine for an authorized `BOUNDED_AUTO` plan (an exactly approved schema 1 plan, or a schema 2 projection bound to the persisted LOCAL_DELIVERY authorization). It never activates a run, executes an executor, writes `STATE.md`, mutates Git, or bypasses recovery, budgets or authorization. `runtime/bounded_loop_driver.py` is **deprecated** (kept for pre-v14 automation; `DEPRECATED = True`); `sdd.py next` drives every mode, MANUAL included, and is what the controller runs.

## Per-action cycle

For each isolated action the controller (through the batches `sdd.py next` prints): reruns recovery; calls `next`; on `EXECUTE_NEXT` prepares the journal and executes exactly that action; validates the result, commits STATE through the journal, verifies `STATE_COMMITTED` and releases; then calls `next` again. `ROLLOVER_REQUIRED` is resolved by rollover to a pristine IDLE journal plus a new `DISPATCH_ALLOWED` before the next dispatch. A successful action never ends a BOUNDED_AUTO round.

## Runtime binding

`bind` adds a `runtime` object owned by the controller:

```json
{"current_plan_id": "brp-...", "approved_plan_sha256": "<hash>", "planned_action_count": 3, "current_sequence": 1,
 "actions_executed": 1, "started_at": "2026-09-16T00:00:00Z", "last_action_id": "action-1",
 "expected_predecessor_state_sha256": "<persisted STATE hash>"}
```

The controller increments `current_sequence`, `actions_executed`, budget counters and `last_action_id` exactly once after an accepted action, and sets `expected_predecessor_state_sha256` only after rereading the verified persisted STATE. Budget resets follow LOOP_POLICY §4 (`used_current_action` on rollover, `used_current_stage` on stage transition; `bounded_run_planner.reset_budgets`).

## Decisions

| Decision | Meaning | Controller action |
| --- | --- | --- |
| `EXECUTE_NEXT` | the next planned action may run now | run it in the same turn |
| `ROLLOVER_REQUIRED` | the journal is RELEASED | rollover, confirm `DISPATCH_ALLOWED`, call `next` again |
| `REPLAN_REQUIRED` | schema 2 projection exhausted under a still-valid authorization (`terminal_reason: PLAN_COMPLETE`) | `sdd.py snapshot` → plan → bind, without asking the user |
| `STOP_BUDGET` | a budget is spent | the stop reason's `next_command` (`sdd.py budget --raise ...`) |
| `STOP_HUMAN_REQUIRED` | a human decision is next | ask the user; record through the printed command |
| `STOP_BLOCKED` | a blocker, gate or contract failure | the stop reason's next step |
| `STOP_PLAN_STALE` | STATE or worktree no longer match the plan | `sdd.py snapshot` and replan |
| `STOP_RECOVERY` | the journal needs reconciliation (`RECOVERY_RECONCILIATION_REQUIRED`) | `sdd.py next` prints the recovery command |
| `COMPLETE` | the plan ended, or MANUAL / inactive loop (`MANUAL_ACTION_COMPLETE`) | report; no new action is authorized |

Every decision returns `next_step` and `next_command` (from `runtime/stop_reasons.py`) plus a structured event: sequence, action, decision, budget and recovery before/after, journal action id, state hashes and stop reason. Stop reasons are exactly those of LOOP_POLICY §9. A stop returns STATE update data that preserves the next action, clears the current action, records the `stop_reason`, pauses the loop (except DONE) and snapshots run counters into `last_run`.

When `stage_transitions.used == max` after `PLAN → TASKS`, the driver returns `STOP_BUDGET` with `STAGE_TRANSITION_BUDGET_REACHED` and does not dispatch `TASKS`; a later `next` keeps that reason. `PAUSED` is not `MANUAL`: the last reading must not rewrite the round as `COMPLETE` / `MANUAL_ACTION_COMPLETE`.

## Commands

```text
python3 .hermes/orchestration/runtime/bounded_run_driver.py inspect|validate|next --snapshot snapshot.json --plan plan.json --json
```

There are no force, ignore, execute, apply or unbounded options.
