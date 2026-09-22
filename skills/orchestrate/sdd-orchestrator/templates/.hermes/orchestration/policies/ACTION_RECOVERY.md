# Action recovery protocol

`ACTION_JOURNAL.json` is a write-ahead journal for one executor action. `STATE.md` remains the demand source of truth. The journal records the in-flight transaction; it never replaces `STATE.md`, and exists to recover an interruption between executor completion and STATE persistence.

## Lifecycle

`IDLE → PREPARED → DISPATCHED → PROCESS_FINISHED → ARTIFACT_READY → VALIDATED → STATE_COMMITTED → RELEASED`

`BLOCKED` is terminal for that action and keeps its previous semantics. `INTERRUPTED` is a distinct terminal outcome for a process that finished without a valid artifact and without `STATE_COMMITTED`. Every status change is validated against its immediate predecessor and written atomically. A completed action requires: validated artifact, updated STATE, reread STATE, persisted evidence confirmation, `STATE_COMMITTED`, then `RELEASED` lock release.

Do not reuse `RELEASED` to hide an interrupted execution. Success remains `SUCCESS → STATE_COMMITTED → RELEASED`. Failure-to-produce-artifact remains `DISPATCHED → PROCESS_FINISHED → INTERRUPTED → archive-interrupted → pristine IDLE`.

## Rollover between consecutive actions

`ACTION_JOURNAL.json` represents only the active transaction. After `RELEASED`, `recover` deliberately remains `RELEASED`: it proves the preceding action is complete, not that a new action may be prepared directly.

Before a valid next STATE action, the controller must run the explicit `rollover` operation. It accepts only a `RELEASED` journal with `state_commit.verified: true`, non-null ticket/action id, classified artifact evidence, and completed STATE-commit evidence. Rollover atomically creates immutable history at `action-journal-history/<ticket>/<action_id>.json`, then atomically replaces the active journal with `empty_journal(workspace)`. The artifact is never moved or deleted.

History is append-only. An existing file with the same canonical SHA-256 is idempotent, allowing recovery after a crash between archival and active-journal replacement. A different hash fails closed with `JOURNAL_HISTORY_CONFLICT`; the active `RELEASED` journal remains unchanged. Repeating rollover after a completed replacement deterministically fails with `JOURNAL_ROLLOVER_NOT_ALLOWED` because the active journal is pristine `IDLE`.

After rollover, reread the active journal and run recovery again. Only `DISPATCH_ALLOWED` permits `prepare` for the next action. `RELEASED → prepare` is prohibited.

## Interrupted action archive

`archive-interrupted` is the first-class recovery for an action that was `DISPATCHED`, whose process has a recorded result, and that produced no valid artifact and no `STATE_COMMITTED`. It never fabricates an artifact, never fabricates `STATE_COMMITTED`, and never uses `RELEASED`.

It accepts only `PROCESS_FINISHED` with `process.started_at`, `process.finished_at`, and `process.exit_code` present, no on-disk or journal-valid artifact, and unverified empty STATE-commit evidence. `DISPATCHED` without a recorded process result is refused: the action may still be running or the result is unknown. A present unclassified artifact must be reconciled, not archived as interrupted. `BLOCKED` and `RELEASED` keep their existing terminals.

The operation transitions a copy to `INTERRUPTED`, archives that snapshot byte-for-byte at `action-journal-history/<ticket>/<action_id>.json` using the same append-only history primitive as rollover, then replaces the active journal with `empty_journal(workspace)`. History with the same canonical SHA-256 is idempotent. A different hash fails closed with `JOURNAL_HISTORY_CONFLICT` and leaves the live `PROCESS_FINISHED` journal unchanged. Repeating the operation after a completed replacement fails with `JOURNAL_ARCHIVE_INTERRUPTED_NOT_ALLOWED` because the active journal is pristine `IDLE`.

The archived action cannot be redispatched. Recovery of the new journal is `DISPATCH_ALLOWED`. A later retry must `prepare` a new `action_id` with a distinct final-message path and `parent_action_id` equal to the archived action. Redispatch of the original `action_id` remains prohibited.

## Pristine IDLE

`PRISTINE_IDLE` means no action has been started and no transaction exists to recover. It is strictly an `IDLE` journal with no action identity, ticket, stage, name, executor, final-message path, retries or parent references; attempt `0`; no artifact, process, prepared/confirmed state-commit data, fingerprints, or incidents; and the canonical pending artifact and unverified state-commit defaults.

`PRISTINE_IDLE → DISPATCH_ALLOWED` because there is no prior executor transaction to recover.

`DIRTY_OR_INCONSISTENT_IDLE → BLOCKED`. Any evidence incompatible with a pristine journal is fail-closed and must not be treated as permission to dispatch.

Before dispatch, persist a unique `action_id`, ticket, stage, action, executor, selected schema, protocol version, final-message path, prompt SHA-256, baseline and ownership fingerprints, expected STATE hash, attempt, retry mode, parent action when present, and `started_at`. After execution persist exit code, `finished_at`, artifact existence and SHA-256, validation result, and incident reference when applicable.

## STATE commit verification

After artifact validation and **before** the controller writes STATE, run `prepare-state-commit` with the absolute in-worktree non-symlink `state_path`, `expected_before_hash`, and `expected_after_hash`. The latter is the SHA-256 of the complete serialized snapshot the controller intends to persist. The executor never writes STATE and its payload is never used to write STATE.

The controller persists STATE atomically. `mark-state-committed` then rereads the recorded STATE path, calculates SHA-256 internally, rejects an unchanged STATE (`STATE_NOT_COMMITTED`) or a mismatched hash (`STATE_COMMIT_HASH_MISMATCH`), and only then records `committed_after_hash`, `committed_at`, `verified: true`, and `STATE_COMMITTED`. `--committed-after-hash` is compatibility input only; it is compared and never copied as proof. `release` reloads the journal and requires `STATE_COMMITTED` with `verified: true`.

For `VALIDATED`, recovery returns `ALREADY_COMMITTED` when real STATE equals `expected_after_hash`; reconcile the journal without an executor. When it equals `expected_before_hash`, recovery returns `STATE_COMMIT_REQUIRED`; the controller must reconcile STATE and must not redispatch. Any other hash is `BLOCKED` with required `STATE_DESYNC` incident. For `STATE_COMMITTED`, recovery accepts only a reread equal to `committed_after_hash`; divergence is `BLOCKED` with `STATE_DESYNC`.

## Recovery probe and redispatch invariant

Before **every** Claude/Codex dispatch, inspect the journal, the pending STATE action, and its expected final-message path.

> Never redispatch while an artifact for the current action exists and has not been classified.

If an artifact exists, parse it; validate schema, contract, paths, symbols, ownership, and baseline; then reconcile STATE, reread it, mark `STATE_COMMITTED`, and release. Invalid artifacts are evaluated for one corrective retry or BLOCKED. `PREPARED` with no started process and no artifact is dispatchable. `DISPATCHED` or `PROCESS_FINISHED` with no artifact and unknown process result is `RECOVERY_REQUIRED`, never an automatic redispatch.

`recover` is diagnostic only and returns one of `DISPATCH_ALLOWED`, `RECONCILE_ARTIFACT`, `STATE_COMMIT_REQUIRED`, `WAIT_OR_MANUAL_REVIEW`, `CORRECTIVE_RETRY_AVAILABLE`, `BLOCKED`, `ALREADY_COMMITTED`, or `RELEASED`. It never invokes an executor.

## Corrective retry

There is at most one corrective retry. It has a new action id and distinct final-message path, never deletes its parent, and records `parent_action_id`, `parent_artifact_path`, `parent_artifact_sha256`, `retry_mode`, `invalid_fields`, `allowed_corrections`, and attempt.

`FULL_REPLACEMENT` creates a complete new result. `METADATA_OVERLAY` is allowed only if the parent passed functional evidence, baseline and ownership fingerprints remain identical, product files did not change, and parent TDD evidence is valid. The overlay preserves `tdd_slices`, `modified_paths`, `created_paths`, `stage_payload`, and valid evidence; it changes only fields listed by `allowed_corrections`. Parent reuse is rejected on product, test, baseline, or ownership drift. A functional blocker never becomes SUCCESS automatically.

## Tool

`python3 .hermes/orchestration/runtime/action_journal.py --help` documents the local interface. It provides `init`, `prepare`, `record-process`, `record-artifact`, `mark-validated`, `prepare-state-commit`, `mark-state-committed`, `release`, `rollover`, `archive-interrupted`, `block`, `inspect`, and diagnostic `recover`, all with `--journal`; `rollover` and `archive-interrupted` additionally require `--history-dir`; `--json` emits structured output. It has no force, overwrite, history-reset, or validation-bypass option.

Writes create a same-directory temporary file, write and flush full JSON, `fsync`, atomically `os.replace`, and attempt directory `fsync`. A write failure leaves the preceding valid journal in place and returns nonzero.
