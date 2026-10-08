# Action recovery protocol

`ACTION_JOURNAL.json` is the write-ahead journal of **one** executor action; `STATE.md` stays the demand's source of truth. Never hand-edit the journal or write STATE outside the commit sequence below: every state has a command.

Below, `J` is `python3 <controller>/runtime/action_journal.py --journal <journal> --json`. Get `<journal>`, `<history-dir>` and `<state>` from `python3 <controller>/runtime/action_journal.py --json paths` (run with the repository as working directory). Obsidian storage puts them in `<vault>/<project>/.hermes-runtime/<worktree-slug>/`; legacy `--local-storage` puts them in `.hermes/orchestration/`. Never guess `--history-dir`.

## Before every dispatch

Run `J recover`. It is read-only and returns `decision`, `reason`, `next_step` and `next_command` (an exact command; fill in `<...>` placeholders). Run `next_command`, then `recover` again. Only `DISPATCH_ALLOWED` on a `PREPARED` action allows launching an executor.

> Never redispatch while an artifact for the current action exists and has not been classified.

| Journal | `recover` decision | Next command |
| --- | --- | --- |
| pristine `IDLE` | `DISPATCH_ALLOWED` | `J prepare --payload '<action-json>'` |
| `PREPARED`, not started | `DISPATCH_ALLOWED` | `J record-process --started --prompt-sha256 <hash>`, then launch the executor in the foreground |
| `DISPATCHED`, no result | `WAIT_OR_MANUAL_REVIEW` | wait in the foreground; once it ended: `J record-process --finished --exit-code <exit-code>` |
| `DISPATCHED`/`PROCESS_FINISHED`/`ARTIFACT_READY`/`VALIDATED` with a final message | `RECONCILE_ARTIFACT` | the step it names: `record-process --finished`, `record-artifact`, `validate_protocol.py` then `mark-validated` or `classify-invalid`, `prepare-state-commit` |
| `PREPARED` with `ADOPT_PARENT_ARTIFACT` | `RECONCILE_ARTIFACT` | `J record-artifact` (no dispatch) |
| `PROCESS_FINISHED`, no final message (timeout, crash, non-zero exit) | `ARCHIVE_INTERRUPTED_REQUIRED` | `J archive-interrupted --history-dir <history-dir>` |
| artifact classified `INVALID` | `CORRECTIVE_RETRY_AVAILABLE` (or `BLOCKED` / `RETRY_BUDGET_REACHED`) | `J archive-invalid --history-dir <history-dir>` |
| `VALIDATED`, STATE = before hash | `STATE_COMMIT_REQUIRED` | write the prepared STATE, then `J mark-state-committed` |
| `VALIDATED`, STATE = after hash | `ALREADY_COMMITTED` | `J mark-state-committed` |
| `STATE_COMMITTED`, verified | `ALREADY_COMMITTED` | `J release` |
| `RELEASED` | `RELEASED` | `J rollover --history-dir <history-dir>` |
| `BLOCKED` | `BLOCKED` / `ACTION_BLOCKED` | `J archive-blocked --history-dir <history-dir> --reason '<reason>'` |
| live `INTERRUPTED` | `BLOCKED` / `ACTION_RECOVERY_REQUIRED` | `J archive-blocked ...` |
| dirty `IDLE` | `BLOCKED` / `DIRTY_OR_INCONSISTENT_IDLE` | `J archive-blocked ...` |
| STATE matches neither hash | `BLOCKED` / `STATE_DESYNC` (incident required) | restore STATE and `recover`, or `J block` then `archive-blocked` |

Every `BLOCKED` carries a `stop_reason`. Every archive (`rollover`, `archive-interrupted`, `archive-invalid`, `archive-blocked`) writes immutable history to `<history-dir>/<ticket>/<action_id>.json`, opens a pristine `IDLE` journal, mirrors the action to the wiki and returns `recovery_after_*: DISPATCH_ALLOWED`. History is append-only: same bytes are idempotent; different bytes fail with `JOURNAL_HISTORY_CONFLICT` and leave the live journal unchanged.

## Successful action

1. `J prepare --payload '<action-json>'`: new `action_id`, prompt SHA-256, distinct final-message path, fingerprints, attempt, `retry_mode`, `parent_action_id` when retrying.
2. `J record-process --started --prompt-sha256 <sha256-of-prompt>`. Refused with `DISPATCH_NOT_PREPARED` unless the journal is a dispatchable `PREPARED`, with `PROMPT_HASH_MISMATCH` when the prompt is not the prepared one, and with `ARTIFACT_PENDING` when the final-message path already holds a file.
3. Launch the executor in the foreground. In a `finally` block: `J record-process --finished --exit-code <exit-code>` (124 for a timeout). Repeating the same exit code is a no-op; a different one is `PROCESS_RESULT_CONFLICT`.
4. `J record-artifact`. A missing, directory or symlinked final message returns `ARTIFACT_MISSING` and keeps `PROCESS_FINISHED`.
5. Validate with `runtime/validate_protocol.py`; then `J mark-validated` (refused with `ARTIFACT_CHANGED` if the file changed since step 4) or `J classify-invalid --invalid-field <field>`.
6. `J prepare-state-commit --state-path <state> --expected-before-hash <sha256> --expected-after-hash <sha256>`. `<state>` must be exactly the path from `paths`: the bound runtime `STATE.md` in Obsidian storage, or an in-worktree path in local storage; symlinks and `..` are refused (`STATE_PATH_UNSAFE`).
7. Write STATE atomically, then `J mark-state-committed` (it rereads STATE and computes the hash itself), `J release`, `J rollover --history-dir <history-dir>`, `J recover`.

## Timeout or crash without a final message

`DISPATCHED` → `J record-process --finished --exit-code 124` → `recover` = `ARCHIVE_INTERRUPTED_REQUIRED` → `J archive-interrupted --history-dir <history-dir>` → `recover` = `DISPATCH_ALLOWED` → `J prepare` a retry with a new `action_id`, a distinct final-message path and `parent_action_id` = the archived id (`retry_mode: FULL_REPLACEMENT`). The original `action_id` is never redispatched.

## Adopting a parent's final message (no redispatch)

When an interrupted action's final message appears after `archive-interrupted` and is complete, adopt it instead of hand-editing STATE or the journal: `J prepare --payload '<action-json>' [--history-dir <history-dir>]` with `retry_mode: ADOPT_PARENT_ARTIFACT`, `parent_action_id` = the archived id, `final_message_path` = `parent_artifact_path` = the parent's own final-message path, `parent_artifact_sha256` = its SHA-256, and no process evidence. `prepare` refuses a parent that is not archived as `INTERRUPTED` in this worktree's history, of another ticket or stage, or another file (`ADOPTION_PARENT_NOT_FOUND`, `ADOPTION_NOT_ALLOWED`, `ADOPTION_ARTIFACT_MISMATCH`). Then `J record-artifact` re-verifies the hash and moves to `ARTIFACT_READY`; continue from step 5. `record-process --started` is refused (`ADOPTION_DISPATCH_FORBIDDEN`).

## Corrective retry

At most one per action, with a new action id and final-message path and `parent_action_id`, `parent_artifact_path`, `parent_artifact_sha256` from `archive-invalid`. `FULL_REPLACEMENT` produces a complete new result. `METADATA_OVERLAY` is allowed only when the parent's functional and TDD evidence passed, baseline and ownership fingerprints are unchanged and no product file changed; it changes only `allowed_corrections`. A functional blocker never becomes SUCCESS automatically.

## Errors

Every error exits 2 with `status`, `message`, `next_step` and, when a command applies, `next_command`. There is no force, overwrite, history-reset or validation-bypass option.
