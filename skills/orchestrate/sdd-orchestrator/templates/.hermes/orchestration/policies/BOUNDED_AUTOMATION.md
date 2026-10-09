# Bounded Automation — preview, authorization and planner

The controller normally never runs these commands by hand: `sdd.py start` shows the authorization preview and `sdd.py snapshot` generates the planner snapshot. This file is the authoritative reference for **what authorizes automatic progress**. Mode semantics (MANUAL, LOCAL_DELIVERY, legacy BOUNDED_AUTO, PAUSED) live in `LOOP_POLICY.md` §2; journal recovery decisions in `ACTION_RECOVERY.md`.

## Authorization

- **Schema 2 LOCAL_DELIVERY** (`schema_version: 2`, `local_delivery`, `loop.mode: BOUNDED_AUTO`): an explicit local-delivery request ("orquestre/implemente <demand>") is the authorization. It is bound to ticket, scope/hash, worktree and fixed cumulative limits; `sdd.py start` shows it once. Replans consume the same authorization without a new confirmation: `authorization.required` and `expires_on_state_change` are `false`, and the plan hash is an integrity and runtime-binding value, not an approval request. Expected STATE and budget progress causes a replan; ticket, scope, workspace or cumulative-limit drift stops execution.
- **Schema 1 BOUNDED_AUTO (legacy)**: every round needs a fresh `BOUNDED RUN PREVIEW` targeted at `NEXT_HUMAN_CHECKPOINT`. An unambiguous affirmative reply immediately after the preview (`sim`, `autorizo`, `pode iniciar`, `continue`) authorizes only the most recently presented `plan_sha256` in the current conversation; it is not reusable. The explicit form remains accepted for asynchronous approval:

  ```text
  Autorizo o plano BOUNDED_AUTO:
  <plan_sha256>
  ```

  Before activation, Hermes recomputes the STATE hash, worktree identity, budgets and recovery decision; any difference makes the preview `PLAN_STALE` and it must be approved again. After activation, in-round progress is judged by `bounded_run_driver.py` and is not by itself `PLAN_STALE`.
- `MANUAL` never runs unauthorized actions; `UNBOUNDED_AUTO` does not exist.

## Plan

The plan is canonical UTF-8 JSON (sorted keys, deterministic separators); its SHA-256 excludes `authorization.plan_sha256`. It names ticket, initial stage, ordered actions with classifications, executor or host, projected budget use, stop conditions, next checkpoint and `plan_sha256`. Persist the plan before validating it; validation always reads the persisted artifact and the normalized snapshot, never an in-memory substitute.

## Classifications

The default target is `NEXT_HUMAN_CHECKPOINT`: continue only while the next action is `AUTO_SAFE` or `AUTO_WITH_BUDGET`, budgets are available, no stop condition occurs and no human authorization is required.

- `AUTO_SAFE`: local deterministic actions without model calls or external mutation — recovery reconciliation, focused tests, formatting restricted to changed files, analysis, budget and ownership checks, transactional STATE update, DONE evaluation, run closure, and writes to the project's Obsidian wiki (`OBSIDIAN_WRITE`, LOOP_POLICY §18).
- `AUTO_WITH_BUDGET`: `SPECIFY`, `CLARIFY`, `PLAN`, `TASKS`, `IMPLEMENT_SLICE`, `REVIEW`, and policy-enabled `CI`.
- `HUMAN_REQUIRED`: retry-exhausted stage reopening, inconclusive recovery, protected-file changes, material scope or architecture choices, backend mutation, DEV E2E, commit, push, issue-tracker updates (e.g. Linear), destructive actions, unplanned external-contract changes.

The planner stops at the first human requirement, blocker, gate or contract failure, budget exhaustion, inconclusive recovery, protected file, material scope change, external mutation, or `DONE`.

## Recovery in the plan

`RECONCILE_ARTIFACT`, `STATE_COMMIT_REQUIRED` and `ARCHIVE_INTERRUPTED_REQUIRED` make `RECOVER_PENDING_ACTION` the first planned action; `WAIT_OR_MANUAL_REVIEW` ends at a human checkpoint; `BLOCKED` permits no automatic action; `ALREADY_COMMITTED` never repeats the executor or the commit; `RELEASED` may evaluate the next STATE action. A plan never redispatches an action with a pending artifact. A STATE at `IDLE` is refused with `IDLE_NO_DEMAND` and the `sdd.py start` command.

## Commands

```text
python3 .hermes/orchestration/runtime/sdd.py snapshot          # writes the normalized snapshot and prints the plan command
python3 .hermes/orchestration/runtime/bounded_run_planner.py plan --snapshot snapshot.json --target NEXT_HUMAN_CHECKPOINT --json
python3 .hermes/orchestration/runtime/bounded_run_planner.py validate --plan plan.json --snapshot snapshot.json --json
python3 .hermes/orchestration/runtime/bounded_run_planner.py classify --action SPECIFY --json
python3 .hermes/orchestration/runtime/bounded_run_driver.py bind --snapshot snapshot.json --plan plan.json --started-at <utc> --json
python3 .hermes/orchestration/runtime/bounded_run_driver.py next --snapshot snapshot.json --plan plan.json --json
```

The planner reads JSON only; it never reads or writes `STATE.md`, runs an executor, a test or a gate, mutates Git or activates a mode. `bind` returns the snapshot with the exact runtime binding and does not read or write STATE; schema 1 also needs `--approved-plan-sha256`. Every error carries `next_step` and `next_command`. There is deliberately no `execute`, `apply`, `force`, budget/recovery bypass or auto-approval option. The driver's decisions are in `BOUNDED_RUN_DRIVER.md`.
