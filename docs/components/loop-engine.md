# Loop engine (experimental, opt-in)

[Docs index](../README.md) · Related: [Harness: bounded correction](harness.md), [Action journal and recovery](action-journal.md), [FSM and bounded loop](fsm-and-loop.md)

**Files:** `runtime/loop_model.py`, `runtime/loop_policy.py`, `runtime/loop_store.py`, `runtime/loop_state.py`, `runtime/loop_correction.py`, `runtime/loop_engine.py`, `runtime/loop_control.py`, `runtime/loop_sdd.py`, `runtime/loop_cli.py`, `schemas/LOOP_DEFINITION_SCHEMA.json`, `schemas/LOOP_EXECUTION_SCHEMA.json`.

A durable, bounded executor for a graph of nodes, inspired by the Goal / Verification / Stop / Outcome contracts of CompozyOS. It is **not wired into the controller**: `sdd.py`, `bounded_run_driver.py` and `action_journal.py` neither import nor call it, no policy cites it, and it writes only under a store directory its caller passes. The production SDD flow is unchanged. The engine runs only when a host program constructs `loop_engine.LoopEngine(store, handlers=...)`; the CLI never runs a node.

## What it reuses

| Existing mechanism | How the engine uses it |
| --- | --- |
| `correction_loop.decide` and `CORRECTION_LOOP_SCHEMA.json` | Every TEST/REVIEW correction is decided by it: attempt cap, repeated hypothesis (`NO_NEW_HYPOTHESIS`), unchanged change or equivalent failure (`NO_PROGRESS`). |
| `state_format.PROFILES` | `loop_sdd.build_definition` takes the stage order of the CODE and DECISION_DOC profiles from it. |
| `executor_launch.load_policy`, `stage_settings`, `is_writing_worker` | Per-stage timeouts and which stages write (not idempotent) come from `policies/EXECUTORS.md` or its defaults. |
| `action_journal` write-ahead discipline | Same order: record the transition first, then replace the state file atomically (`fsync` file and directory). |
| `stop_reasons.STOP_REASONS` | Every halt and outcome reason maps to a registered Hermes stop reason (`hermes_stop_reason`). |

The engine does not replace the action journal: a handler that launches a real executor should still go through `executor_launch.py` and the journal for that action.

## Model

- **LoopDefinition** (`LOOP_DEFINITION_SCHEMA.json`, v1, closed): `loop_id`, `objective`, `loop_type` (`GENERIC` | `SDD_DELIVERY`), `ticket`, `nodes`, `limits`, `policy` (`on_unrecoverable_failure`, `security_approvers`, default `DEFAULT_SECURITY_APPROVERS` = `requester`). `loop_model.validate_definition` fills defaults and refuses, with a stable code: `DEFINITION_INVALID`, `DUPLICATE_NODE_ID`, `DEPENDENCY_UNKNOWN`, `DEPENDENCY_INVALID` (listed twice), `DEPENDENCY_CYCLE` (the message names the cycle), `UNSUPPORTED_NODE_KIND`, `TIMEOUT_REQUIRED` (a non-`wait` node without a finite timeout), `CORRECTION_TARGET_INVALID` (a `reopen` target that is not upstream), `LIMIT_ABOVE_CEILING`.
- **LoopNode**: `id`, `kind`, `handler` (defaults to `id`), `depends_on`, `idempotent` (default **false**: unknown means not safe to repeat), `effect` (`NONE` | `LOCAL` | `EXTERNAL`), `timeout_seconds` (default from `limits.timeouts_by_kind`), `params`, `approval` (`{category, reason}` with category `SECURITY`, `EXTERNAL_MUTATION`, `PROTECTED_FILE`, `SCOPE` or `HUMAN`), `on_failure` (`{reopen: <upstream node>}` marks a verifier).
- **LoopExecution** (`LOOP_EXECUTION_SCHEMA.json`): status, per-node record (status, generation, attempts with idempotency keys, results and the attempt's own handler-reported `tokens`/`tokens_method` from `loop_state.record_attempt_tokens` (`MEASURED` or `SIMULATED` when the handler says so, otherwise `ESTIMATED`; an invalid count is `null` with `UNMEASURED`), recorded for failed attempts too, retry counters, output, partial progress, correction task, human input, approval), `usage`, `corrections`, `pending`, `halt`, `decisions`, `outcome`, `checkpoint`.
- **LoopCheckpoint**: `checkpoint {seq, at, event}` inside `execution.json`, matching the last line of `events.jsonl`.
- **LoopOutcome**: `outcome {status, reason, hermes_stop_reason, outputs, node_statuses, at}`, written once when the execution becomes terminal.

### Node kinds

Implemented (`NODE_KINDS`): `run-agent`, `run-tool`, `gate`, `wait`. They differ in default timeout; a `gate` is a verifier when it has `on_failure`; a `wait` node never calls a handler and waits for human input. Planned (`PLANNED_NODE_KINDS`): `run-loop`, `fan-out`, `collect`, `branch`; the schema knows them and `validate_definition` refuses them with `UNSUPPORTED_NODE_KIND`, because the current SDD flow is a sequential chain and needs none of them.

### States and their Hermes mapping

`loop_model.HERMES_STATE_MAP` expresses each state in states Hermes already persists (journal statuses from `action_journal.STATUSES`, STATE stage status):

| Loop state | Journal status of the node's action | STATE stage status | Typical `hermes_stop_reason` |
| --- | --- | --- | --- |
| `PENDING` | `IDLE` | `RUNNING` | — |
| `RUNNING` | `PREPARED` … `STATE_COMMITTED` | `RUNNING` | — |
| `WAITING_INPUT` | `IDLE` | `PAUSED` | `CLARIFICATION_REQUIRED` |
| `WAITING_APPROVAL` | `IDLE` | `PAUSED` | `HUMAN_APPROVAL_REQUIRED`, `EXTERNAL_MUTATION_REQUIRED` |
| `RETRYING` | `INTERRUPTED`, `IDLE` | `RUNNING` | — |
| `BLOCKED` | `BLOCKED` | `BLOCKED` | per halt reason below |
| `COMPLETED` | `RELEASED`, `IDLE` | `DONE` | `DONE` |
| `FAILED` | `BLOCKED` | `BLOCKED` | per reason |
| `CANCELLED` | `IDLE` | `PAUSED` | `ABANDON_REQUESTED` |

Halt and outcome reasons (`loop_model.HERMES_STOP_REASON`): `TRANSIENT_RETRIES_EXHAUSTED` and `UNKNOWN_FAILURE` → `EXECUTOR_FAILED`; `TIMEOUT_RETRIES_EXHAUSTED` → `EXECUTOR_TIMEOUT`; `HANDLER_MISSING` → `EXECUTOR_UNAVAILABLE`; `FUNCTIONAL_FAILURE` → `WORKER_BLOCKED`; `EFFECT_AMBIGUOUS` → `ACTION_RECOVERY_REQUIRED`; `RUN_BUDGET_EXHAUSTED`, `TOKEN_BUDGET_EXHAUSTED`, `TIME_BUDGET_EXHAUSTED` → `BUDGET_REACHED`; `CORRECTION_BUDGET_EXHAUSTED` → `RETRY_BUDGET_REACHED`; `NO_PROGRESS` and `NO_NEW_HYPOTHESIS` keep their names; `APPROVAL_REQUIRED` → `HUMAN_APPROVAL_REQUIRED`; `EXTERNAL_APPROVAL_REQUIRED` → `EXTERNAL_MUTATION_REQUIRED`; `APPROVAL_REJECTED` → `HUMAN_DECISION_REQUIRED`; `INPUT_REQUIRED` → `CLARIFICATION_REQUIRED`; `NO_RUNNABLE_NODE` → `BLOCKED`; `NODE_STILL_RUNNING` → `EXECUTOR_TIMEOUT`; `COMPLETED` → `DONE`; `CANCELLED` → `ABANDON_REQUESTED`. Every halt carries a `next_step` (`loop_policy.NEXT_STEPS`).

## Execution rules

`LoopEngine.run(execution_id, max_steps=None)` takes the execution's exclusive, non-blocking runner lock (`LOOP_ALREADY_RUNNING` for a second runner), refuses a definition whose hash changed since `start` (`DEFINITION_CHANGED`), an unreadable or invalid execution file (`EXECUTION_CORRUPT`) and a hand-edited one (`EXECUTION_TAMPERED`, see [Integrity](#integrity)), and repeats one step at a time:

1. Finish with `COMPLETED` when every node is COMPLETED (a cancellation that arrives while the last node runs does not undo finished work).
2. Honour a cancellation request; stop at a pending halt.
3. Recover attempts a dead runner left `RUNNING` (see below).
4. Pick the first `PENDING`/`RETRYING` node, in stable topological order, whose dependencies are all COMPLETED. COMPLETED nodes are never run again.
5. Check budgets: `max_node_runs`, `max_tokens` (handler-reported `tokens`; `usage.tokens_method` is `UNMEASURED`, `MEASURED` or `ESTIMATED`, and an estimate is never labelled measured), `max_wall_clock_seconds` (time spent inside handlers).
6. Ask for approval when the node has `approval` or `effect: EXTERNAL`; ask for input for a `wait` node. Refuse to start a non-idempotent node while an earlier timed-out attempt of it is still running in this process (`NODE_STILL_RUNNING`).
7. Checkpoint the attempt (`RUNNING`, attempt number, `attempt_id` `<execution>:<node>:g<generation>:a<attempt>` and idempotency key `<execution>:<node>:g<generation>`) **before** calling the handler; then call it with the node timeout; then checkpoint the result.

The idempotency key is the same for every retry and crash replay of one node generation, so an external system that stores it can recognise a replay; a correction opens a new generation and a new key. Handlers receive deep copies of their inputs, partial progress and correction task, and `ctx.abandoned()` turns true once the engine stops waiting (timeout).

Results and failures (`loop_policy`): a handler returns `{status: SUCCEEDED|FAILED, output, failure, tokens, tokens_method, needs_input}` or raises `TransientFailure` / `FunctionalFailure`. A malformed result (including an output, failure, token count or input request that is not JSON with string keys or valid UTF-8, `RESULT_INVALID`) or any other exception is `UNKNOWN` and never a success; `ctx.checkpoint` refuses partial progress that is not JSON. A state that cannot be saved raises `STATE_NOT_SERIALIZABLE` without advancing the checkpoint. Failure classes (`FAILURE_CLASSES`): `TRANSIENT`, `TIMEOUT`, `FUNCTIONAL`, `UNKNOWN`. `loop_policy.classify` returns `RETRY`, `CORRECT` or `HALT`:

| Failure | Decision |
| --- | --- |
| `TRANSIENT` on an idempotent node, or on a non-idempotent node whose failure says `effect_applied: false` | `RETRY` after `backoff_base_seconds × 2^n` (capped at `backoff_max_seconds`, no jitter), up to `max_transient_retries`; then halt `TRANSIENT_RETRIES_EXHAUSTED`. |
| `TIMEOUT` | same, with `max_timeout_retries`; then `TIMEOUT_RETRIES_EXHAUSTED`. |
| `TRANSIENT` or `TIMEOUT` on a non-idempotent node without proof of no effect | halt `EFFECT_AMBIGUOUS`; never retried automatically. |
| `FUNCTIONAL` on a verifier (`on_failure.reopen`) | `CORRECT` (bounded correction, next section). |
| `FUNCTIONAL` elsewhere | halt `FUNCTIONAL_FAILURE`; never retried. |
| `UNKNOWN` | halt `UNKNOWN_FAILURE`; never retried. |

A halt waits for a human decision (`BLOCKED`, `WAITING_APPROVAL` or `WAITING_INPUT`). With `policy.on_unrecoverable_failure: FAIL`, the reasons in `loop_policy.FAILABLE_REASONS` end the execution as `FAILED` instead.

**Defaults** (`loop_model.DEFAULT_LIMITS`): `max_correction_cycles` 3, `max_transient_retries` 2, `max_timeout_retries` 1, `max_node_runs` 100, `max_tokens` and `max_wall_clock_seconds` off, backoff 2 s → 60 s, `timeouts_by_kind` run-agent 900, run-tool 300, gate 600, wait none. Hard ceilings (`LIMIT_CEILINGS`, node timeout 7200 s) apply to definitions and to every raise.

**Cancellation.** `loop_control.request_cancel` cancels at once when no runner holds the lock; otherwise it writes `cancel-request.json` and the runner, after the current node finishes and is checkpointed, marks every non-terminal node `CANCELLED` and writes the outcome. A handler can poll `ctx.cancel_requested()`. A running handler is never killed by the engine.

**Human decisions.** `loop_control.resolve` records one decision for the pending halt with `by`, the user's literal `quote`, `actor` and an optional `value`, appended to `decisions`. Names are attribution labels, not authentication: the engine cannot prove who calls it (any process can pass `actor="human"` and `by="requester"`), so the name filter is fail-closed and advisory; it also refuses some real names (for example ones containing `claude` or the word `ci`). A SECURITY approval additionally needs an exact match in `policy.security_approvers`. The decision event logs `value`, `by` and `quote`; `raise-budget` values are stored without whitespace. Refusals: `HUMAN_ACTOR_REQUIRED` (the actor is not human, or `by` is empty or contains a program or AI-executor token from `MACHINE_ACTORS`, matched per word, camel-case part or substring: `claude`, `hermes-agent`, `AutoApprover`, `sdd-bot` are all refused; `loop_control.is_machine_name`), `SECURITY_APPROVAL_HUMAN_ONLY` (a SECURITY approval from anyone not listed in `policy.security_approvers`), `QUOTE_REQUIRED`, `NOTHING_PENDING`, `NODE_MISMATCH`, `EXECUTION_TAMPERED`, `DECISION_NOT_ALLOWED`, `DECISION_UNKNOWN`, `BUDGET_RAISE_INVALID`, `VALUE_INVALID`, `NO_NEW_HYPOTHESIS`. Decisions (`DECISIONS`): `approve`, `reject` (the node and execution end `FAILED`, reason `APPROVAL_REJECTED`), `provide-input`, `retry` (only for `RETRYABLE_REASONS`: `EFFECT_AMBIGUOUS`, `TRANSIENT_RETRIES_EXHAUSTED`, `TIMEOUT_RETRIES_EXHAUSTED`, `UNKNOWN_FAILURE`, `FUNCTIONAL_FAILURE`, `HANDLER_MISSING`, `NODE_STILL_RUNNING`), `mark-applied` (JSON of the observed output; completes an `EFFECT_AMBIGUOUS` or `NODE_STILL_RUNNING` node without re-running it), `correct` (a new hypothesis for a stalled correction), `raise-budget` (`<limit>=<n>` naming exactly the exhausted budget, above the current value and within the ceiling; when the halted node is a verifier with an open failed correction, the correction is decided again so the loop resumes instead of sticking). Each decision records the node's `generation`. Nothing is ever approved automatically.

## Bounded correction for TEST and REVIEW

`loop_correction` keeps one `CORRECTION_LOOP_SCHEMA` record per verifier. Attempt 1 is the original change; each later attempt carries the hypothesis that produced its change. On a functional failure of the verifier it appends the attempt (change digest = canonical hash of the target's output; evidence digest = normalized evidence text plus sorted failed checks) and calls `correction_loop.decide` with `max_attempts = max_correction_cycles + 1` and the hypothesis the failure proposes. `max_correction_cycles` is also a cap **per task**: the cycles spent on one target (IMPLEMENT) are counted across every verifier that reopens it (`corrections._cycles_by_target`), so TEST and REVIEW together never exceed 3 by default.

- `CORRECT` → reopen **only** the target (IMPLEMENT) and its downstream nodes in a new generation, with a delimited corrective task in `ctx.correction` (`cycle`, `verifier`, `hypothesis`, `failed_checks`, `cause`, `evidence`, `scope`). Upstream nodes (SPECIFY … TASKS) are not re-run.
- `STOP` → halt `CORRECTION_BUDGET_EXHAUSTED`, `NO_PROGRESS` (equivalent failure or unchanged change) or `NO_NEW_HYPOTHESIS` (missing or repeated hypothesis). The user resumes with `correct --value '<hypothesis>'` or `raise-budget`.

A passing verification closes the record.

## SDD adapter

`loop_sdd.build_definition(ticket=..., objective=..., profile="CODE"|"DECISION_DOC")` builds `loop_id: sdd-<ticket>`: the stages of the profile (`SDD_STAGES`: SPECIFY, CLARIFY, PLAN, TASKS, IMPLEMENT, TEST, REVIEW, filtered by the profile) as a chain; TEST and REVIEW are `gate` verifiers with `on_failure: {reopen: IMPLEMENT}` (`VERIFIER_STAGES`, `CORRECTION_TARGET`); the other stages are `run-agent`. IMPLEMENT is the only stage marked not idempotent (`effect: LOCAL`), because it is the only writing stage that is not a verifier; TEST writes in its sandbox but is treated as a re-runnable verifier. Timeouts come from EXECUTORS.md. The adapter builds the definition only; real handlers belong to a future integration.

## CLI

`python3 runtime/loop_cli.py <command> ... [--json]`. It never runs a node. Exit 0 on success, 2 on a refusal (`code`, `message`, `next_step`), 1 for a usage error (including `EXECUTION_REQUIRED`).

| Command | Options and result |
| --- | --- |
| `validate` | `--definition <file>`: normalized order, limits and `definition_sha256`. |
| `sdd-definition` | `--ticket <id> --objective <text> [--profile CODE\|DECISION_DOC] [--executors <EXECUTORS.md>]`: prints the SDD definition. |
| `status` | `--store <dir> --execution <id>`: status, node statuses, pending decision, halt, outcome, usage, checkpoint. |
| `cancel` | `--store <dir> --execution <id> --by <person> --reason <text>`. |
| `resolve` | `--store <dir> --execution <id> [--node <id>] --decision <decision> [--value <v>] --by <person> --quote <words>`: records a human decision. |

## Storage

Under `<store>/<execution_id>/` (`EXECUTION_ID_INVALID` for an unsafe id, including a trailing newline; `STORE_PATH_UNSAFE` for a symlink, a non-regular file, or a store or execution directory that is not owner-private 0700 of the current user; `EXECUTION_NOT_FOUND`; `EXECUTION_EXISTS` when the id holds another definition; `start` with the same definition returns the existing execution; concurrent `start`s of one id yield one execution and `EXECUTION_EXISTS` for the others): `definition.json` (immutable), `execution.json` (atomic replace, 0600), `execution.pending.json` (the state about to be committed), `events.jsonl` (append-only, `fsync` per line, opened without following links), `runner.lock`, `cancel-request.json`. Only `start` creates directories, and it builds the execution in a private `.creating-*` staging directory renamed into place in one step, so a crash during `start` leaves no half-created execution; `cancel`, `resolve` and `status` on an unknown id create nothing. Every commit writes the new state to `execution.pending.json`, appends the event, then replaces `execution.json`. A crash leaves at most one trailing line after the committed checkpoint. A caller holding the runner lock (`run`, `resolve`, an immediate `cancel`) trims it only when it is torn (unparseable, including a UTF-8 character cut in half); a complete event is never dropped: recovery rolls forward to the pending state only when it is schema-valid, belongs to the same execution and definition, sits exactly at `checkpoint + 1` with the event's name, and hashes to the event's `state_sha256`; otherwise it refuses with `EXECUTION_TAMPERED` and leaves `execution.json` untouched. A missing checkpoint event, a non-contiguous sequence or more than one event after the checkpoint is also `EXECUTION_TAMPERED`. Restoring an older `execution.json`, even by one step, therefore cannot drop a recorded event (a `reject`, a completion); `status` and other lock-free reads never write, so they cannot delete an event a runner is committing. `LoopStore.events` reports an unreadable line as `UNREADABLE` instead of failing.

### Integrity

`execution.json` is trusted only as far as the definition and the event log explain it (`loop_state.verify_integrity`, run by `run`, `resolve` and an immediate `cancel`): every event carries `state_sha256`, the canonical hash of the execution it committed, and `execution.json` must hash to the value in the event of its checkpoint `seq`, so any edit of that file alone (a hand-completed gate, a reset `usage`, a rewritten decision or limit) is refused; every recorded decision must also be in `events.jsonl`; `limits` must equal the definition limits plus the recorded `raise-budget` decisions and stay within the ceilings (the execution schema also bounds them); and a node approval must match a recorded human `approve`/`reject` of the same node generation. Anything else is `EXECUTION_TAMPERED`. This detects edits that leave the two files inconsistent; a process that can rewrite both the execution file and the event log consistently is not detected, which is why the store must be owner-private.

## Recovery guarantees (verified by the tests)

What the tests prove, and only that:

- **Crash after a node completes**: its result is in `execution.json` before the next node starts; a restarted runner never repeats it (`test_a_killed_process_resumes_without_repeating_completed_nodes` kills a real process with `os._exit`).
- **Crash during an idempotent node**: the attempt is marked `INTERRUPTED`, counted as a transient retry and re-run with the same idempotency key, a new `attempt_id` and the last `ctx.checkpoint` partial progress. Repeated crashes stop at `max_transient_retries` (`TRANSIENT_RETRIES_EXHAUSTED`).
- **Crash during a non-idempotent node**: never re-run automatically. A registered reconciler may prove `applied: true` (the node completes with the observed output, `RECONCILED`) or `applied: false` (one re-run); any other answer, or no reconciler, halts `EFFECT_AMBIGUOUS` until a human chooses `mark-applied` or `retry`.
- **Torn write**: `execution.json` is never partially written (temporary file, `fsync`, `os.replace`, directory `fsync`); the event log is trimmed to the committed checkpoint.
- **Concurrent runners**: refused by the lock. `resolve` and an immediate `cancel` take the same lock; `status` reads without it and writes nothing.

Limits, stated plainly:

- **At-least-once for idempotent nodes.** A crash between the handler's side effect and the result checkpoint repeats the attempt. The idempotency key lets an external system deduplicate; the engine cannot do it for it.
- **No exactly-once for non-idempotent nodes without a reconciler**: the engine guarantees *no automatic duplicate*, not completion; a human decides.
- **Timeout does not kill.** A timed-out handler thread keeps running in the background (Python cannot kill a thread); its later checkpoints are ignored and it works on copies of its inputs. An idempotent node may be retried while the old thread still runs; a non-idempotent one is not (`NODE_STILL_RUNNING`) within the same process. Handlers should stop when `ctx.abandoned()` is true, and handlers that launch processes must enforce their own deadline, as `executor_launch.py` already does by killing the process group.
- **Cancellation is cooperative**: it applies between nodes, or inside a handler that polls `cancel_requested()`.
- **Wall-clock budget** counts time inside handlers in the runner process, not time between runs or backoff sleeps.
- **Token usage** is whatever handlers report; with no report it stays `UNMEASURED`.
- **The CLI cannot prove who is typing.** `loop_cli.py resolve` records `actor: human`; it refuses machine names and empty quotes, but an agent that runs it is bound by the same rule as `sdd.py waive`: record only the user's own words.
- Locking uses `fcntl.flock` on POSIX (`msvcrt` on Windows, untested here); the store must be on a local filesystem.

## Rollback

Nothing reads the store and nothing in the controller imports these modules: reverting the change removes the engine. Delete a store directory to discard its executions.

## CompozyOS ideas borrowed (ideas, not code)

From [compozy/compozy](https://github.com/compozy/compozy) at commit `2e63bba66c32ee210ac54902b07b5fca61536c5c`: the Goal/Verification/StopWhen/IterationCap/NoProgress/Budget contract and the closed terminal vocabulary (`internal/loop/dsl/contract.go`); action and control node kinds (`internal/loop/dsl/types_nodes.go`); per-attempt idempotency keys (`internal/loop/coordinator_terminal_helpers.go`, `NodeCellAttemptIdempotencyKey`); the "ambiguous" outcome for an effect whose truth cannot be proven (`internal/loop/action_goal_contract.go`, `internal/loop/goal/recovery.go`); exponential backoff bounded by a maximum (`internal/retry/backoff.go`).
