#!/usr/bin/env python3
"""Closed registry of every ``stop_reason`` a runtime emits, with exactly one next action each.

LOOP_POLICY.md §9 renders this table and ``tests/test_stop_reasons.py`` keeps the
three in sync: every literal a runtime emits is registered here, every registered
code is emitted by some runtime, and §9 lists exactly these codes. A controller
that meets a stop reason therefore never has to invent a step: it runs the
``next_command`` (placeholders in ``<...>`` are filled from the user's own
words) and then ``sdd.py next``.

``{sdd}`` in a command template is rendered as the exact interpreter + path of
this controller's ``runtime/sdd.py``.
"""
from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Any

SDD_SCRIPT = Path(__file__).resolve().with_name("sdd.py")

#: kind: PAUSED (healthy, waits for a decision), BLOCKED (technical/safety impediment),
#: HUMAN (needs a user decision recorded through the command) or DONE.
PAUSED, BLOCKED, HUMAN, DONE = "PAUSED", "BLOCKED", "HUMAN", "DONE"

_RAISE = "{sdd} budget --raise %s --by 1 --quote '<user words authorizing more>'"
_REOPEN = "{sdd} reopen --reason '<failure>' --quote '<user words>'"
_REBASELINE = "{sdd} rebaseline --quote '<user words>'"
_CONFIRM_POLICY = "{sdd} confirm-policy --name <policy> --by requester --quote '<user words>'"
_NEXT = "{sdd} next"
_STATUS = "{sdd} status"

#: Placeholders a registered command may carry: each is filled with the user's own
#: words (or, for ``<reason>``, the controller's one-line reason). Anything the
#: runtime knows (gate, check id, question index, budget name) is filled by
#: ``sdd.py`` itself, so it never appears here.
USER_PLACEHOLDERS = frozenset({
    "<user words>", "<user words authorizing more>", "<failure>", "<reason>",
    "<ticket-id>", "<title>", "<objective>", "<policy>",
})

STOP_REASONS: dict[str, tuple[str, str, str]] = {
    "NONE": (PAUSED, "Nothing stops the loop; continue with the next step.", _NEXT),
    # --- budgets (bounded_run_driver, correction_loop, sdd.py) ---
    "STAGE_TRANSITION_BUDGET_REACHED": (HUMAN, "The stage-transition budget is spent; ask the user whether to authorize more transitions.", _RAISE % "stage_transitions"),
    "EXECUTOR_CALL_BUDGET_REACHED": (HUMAN, "The executor-call budget is spent; ask the user whether to authorize more calls.", _RAISE % "executor_calls"),
    "RETRY_BUDGET_REACHED": (HUMAN, "The corrective-retry budget for this action is spent; ask the user to authorize one more retry or to reopen the stage.", _RAISE % "corrective_retries"),
    "TDD_SLICE_BUDGET_REACHED": (HUMAN, "The TDD-slice budget is spent; IMPLEMENT stays open. Ask the user to authorize more slices.", _RAISE % "tdd_slices"),
    "INVESTIGATION_BUDGET_REACHED": (HUMAN, "The investigation-expansion budget for this stage is spent; ask for one explicit expansion.", _RAISE % "investigation_expansions"),
    "REVIEW_CYCLE_BUDGET_REACHED": (HUMAN, "The review-cycle budget is spent; ask the user whether to authorize another REVIEW.", _RAISE % "review_cycles"),
    "CI_RUN_BUDGET_REACHED": (HUMAN, "The CI-run budget is spent; ask the user whether to authorize another CI run.", _RAISE % "ci_runs"),
    "EXTERNAL_MUTATION_REQUIRED": (HUMAN, "The next action mutates an external system (commit, push, tracker, backend); it needs separate explicit authorization outside the loop. `sdd.py next` continues the local delivery without it.", _NEXT),
    "BUDGET_REACHED": (HUMAN, "A LOCAL_DELIVERY total limit is spent; `sdd.py next` stops with the named budget reason and its exact `budget --raise <name>` command.", _NEXT),
    "COST_BUDGET_REACHED": (HUMAN, "The correction cost budget is spent; ask the user to authorize more cost or a cheaper tier, then `sdd.py next` (the stage's single corrective retry or its stop).", _NEXT),
    "CORRECTIVE_RETRY_EXHAUSTED": (HUMAN, "The corrective retry of this action was already used; ask the user to authorize one more retry.", _RAISE % "corrective_retries"),
    # --- executor / journal (action_journal, executor_launch via sdd.py) ---
    "EXECUTOR_TIMEOUT": (BLOCKED, "The worker timed out twice (the first timeout already got one reduced-context retry). Ask the user to raise the stage timeout in policies/EXECUTORS.md or authorize one more retry.", _RAISE % "corrective_retries"),
    "EXECUTOR_FAILED": (BLOCKED, "The worker process failed again after its retry; check `executor_launch.py preflight` for that executor, then authorize one more retry.", _RAISE % "corrective_retries"),
    "EXECUTOR_PROCESS_ENDED_WITHOUT_ARTIFACT": (PAUSED, "The worker ended without a final message; archive it as interrupted (the printed command), then `sdd.py next` prepares the single retry.", _NEXT),
    "ACTION_RECOVERY_REQUIRED": (BLOCKED, "The journal holds an open action; `sdd.py next` prints the recovery command for it.", _NEXT),
    "ACTION_BLOCKED": (BLOCKED, "The action was blocked; archive it with a reason (printed command), then `sdd.py next`.", _NEXT),
    "DIRTY_OR_INCONSISTENT_IDLE": (BLOCKED, "The idle journal carries stray evidence; archive it with a reason (printed command), then `sdd.py next`.", _NEXT),
    "JOURNAL_INCONSISTENT": (BLOCKED, "The journal combines evidence no command produces; block and archive it (printed command), then `sdd.py next`.", _NEXT),
    "ARTIFACT_PENDING": (BLOCKED, "A final-message file already exists for an undispatched action; block and archive the action, then `sdd.py next`.", _NEXT),
    "STATE_DESYNC": (BLOCKED, "STATE differs from both prepared hashes; `sdd.py next` prints the journal command that blocks and archives the action. Never hand-edit STATE.", _NEXT),
    "STATE_PATH_REQUIRED": (BLOCKED, "The journal's state commit has no STATE path; archive the action with archive-blocked and run `sdd.py next`.", _NEXT),
    "STATE_PATH_UNSAFE": (BLOCKED, "The journal's STATE path is not this worktree's canonical STATE (see `action_journal.py paths`); archive the action and run `sdd.py next`.", _NEXT),
    "STATE_COMMIT_FILE_MISSING": (BLOCKED, "STATE.md is missing or not a regular file; restore it, then run `sdd.py next`.", _NEXT),
    "STATE_INCONSISTENT": (BLOCKED, "STATE.md cannot be parsed, lacks required keys, or an accepted artifact changed; restore the last committed STATE (history), never repair it by hand, then check it with `sdd.py status`.", _STATUS),
    # --- contract / validation (sdd.py accept) ---
    "CONTRACT_INVALID": (PAUSED, "The worker result failed validation; it is classified invalid. `sdd.py next` archives it and prepares the single corrective retry with the errors.", _NEXT),
    "WORKER_BLOCKED": (HUMAN, "The worker reported blockers; ask the user and record the resolution: the stage is redispatched with it.", "{sdd} unblock --quote '<user words>'"),
    "CLARIFICATION_REQUIRED": (HUMAN, "Material questions are open; ask the user exactly the listed questions and record each answer with the printed `sdd.py answer --index` command.", _NEXT),
    "HUMAN_DECISION_REQUIRED": (HUMAN, "An acceptance check needs the user's decision; record it verbatim with the printed command (`waive` for a HUMAN check, `answer --check` for a requested decision).", _NEXT),
    "SCOPE_CHANGE_REQUIRED": (HUMAN, "The slice contract differs from the approved one; it is new scope. Record the user's approval of the current slice contracts.", "{sdd} approve-scope --quote '<user words>'"),
    "INVESTIGATION_BUDGET_EXCEEDED": (HUMAN, "The context budget of the manifest is exceeded; ask for an explicit expansion or narrow the sources.", _RAISE % "investigation_expansions"),
    "PROMPT_BUDGET_EXCEEDED": (HUMAN, "The worker prompt exceeds max_prompt_bytes even after reduction; ask the user to authorize a larger prompt.", "{sdd} budget --raise prompt_bytes --by 16384 --quote '<user words authorizing more>'"),
    "MANIFEST_INVALID": (BLOCKED, "The stage-context manifest was refused; the findings name the field. Fix the source data, then `sdd.py next`.", _NEXT),
    "JEV_GOVERNANCE_REQUIRED": (BLOCKED, "Automatic Jev consent is recorded but no governance fingerprint is: run `semantic_governor.py decide` for this ticket (or the user withdraws the consent in PROJECT_SETUP.md), then `sdd.py next` rebuilds the manifest.", _NEXT),
    # --- baseline (sdd.py next) ---
    "BASELINE_DRIFT_EXTERNAL": (HUMAN, "HEAD or branch changed outside the agent; ask the user whether to continue on the new baseline and record the answer (it captures the new baseline).", _REBASELINE),
    "PREEXISTING_FILE_MODIFIED": (HUMAN, "A protected pre-existing file changed during the demand; ask the user. Never restore it with Git reset/checkout. Accepting the change records the answer and captures the new hashes.", _REBASELINE),
    # --- gates (bounded_run_driver, sdd.py gate) ---
    "FOCUSED_TESTS_FAILED": (BLOCKED, "Focused tests failed; reopen IMPLEMENT for a corrective slice with the user's agreement.", _REOPEN),
    "FORMAT_FAILED": (BLOCKED, "The formatter failed; reopen IMPLEMENT for a corrective slice with the user's agreement.", _REOPEN),
    "ANALYZE_FAILED": (BLOCKED, "Static analysis failed; reopen IMPLEMENT for a corrective slice with the user's agreement.", _REOPEN),
    "CI_FAILED": (BLOCKED, "CI failed; CI is never retried automatically. Reopen IMPLEMENT with the user's agreement.", _REOPEN),
    "CI_TIMEOUT": (HUMAN, "CI timed out; it blocks advancement. Ask the user whether to run it once more (raise its timeout in GATES.md first if needed).", "{sdd} gate --name ci --rerun --quote '<user words>'"),
    "GATE_TIMEOUT": (HUMAN, "A gate command timed out; it blocks advancement. Raising its timeout in GATES.md re-runs it on the next `sdd.py next`; otherwise ask the user to confirm one more run with the printed `gate --rerun` command.", _NEXT),
    "REVIEW_BLOCKED": (BLOCKED, "REVIEW reported a blocker; reopen IMPLEMENT with the user's agreement.", _REOPEN),
    "REVIEW_CHANGES_REQUIRED": (HUMAN, "REVIEW requires changes; reopen IMPLEMENT for a corrective slice with the user's agreement.", _REOPEN),
    "OWNERSHIP_VIOLATION": (HUMAN, "REVIEW found writes outside the agent-owned paths; ask the user. Accepting them records the answer and redispatches REVIEW; otherwise reopen IMPLEMENT with `sdd.py reopen`.", _REBASELINE),
    "GATE_COMMAND_UNCONFIGURED": (BLOCKED, "A required gate has no verified command in policies/GATES.md; configure it (`detect_stack.py` suggests one; run it once), then `sdd.py next` runs it.", _NEXT),
    "GATE_CONFIRMATION_REQUIRED": (HUMAN, "GATES.md marks this gate NOT_APPLICABLE; record the user's explicit confirmation with the printed `gate --not-applicable` command.", _NEXT),
    "CONTROLLER_POLICY_CHANGED_DURING_DEMAND": (HUMAN, "policies/GATES.md or EXECUTORS.md no longer matches the hash pinned at `sdd.py start`: its gate commands run on the host and it chooses the worker binary, so a worker with write access could have changed it. Show the user the diff; only their confirmation re-pins it.", _CONFIRM_POLICY),
    "CONTROLLER_POLICY_UNPINNED": (HUMAN, "A controller policy file has no pinned SHA-256 for this demand (started before pinning existed), so an edit cannot be attributed to the owner. Review each file with the user and record their confirmation; no gate runs until then.", _CONFIRM_POLICY),
    "CONTROLLER_POLICY_CONFIRMATION_REQUIRED": (HUMAN, "Confirming a controller policy change needs the user's literal words; ask them and record the answer with the printed command.", _CONFIRM_POLICY),
    "STATE_MODIFIED_DURING_ACTION": (BLOCKED, "STATE changed while an action was open, outside the journal's state commit: in `--local-storage` mode a writing worker can reach STATE. Restore it from the journal history (never by hand), then `sdd.py next` archives the action.", _NEXT),
    "DONE_GATES_NOT_PASSED": (BLOCKED, "DONE needs focused tests, format, analysis, review and CI (or DISABLED_BY_PROJECT_POLICY) to pass; `sdd.py next` prints the missing gate.", _NEXT),
    "FOCUSED_TESTS_REQUIRED": (BLOCKED, "Format runs only after focused tests pass; run the focused-tests gate first.", _NEXT),
    "TEST_AND_FORMAT_REQUIRED": (BLOCKED, "Analysis runs only after focused tests and format pass.", _NEXT),
    "TEST_FORMAT_AND_ANALYZE_REQUIRED": (BLOCKED, "REVIEW runs only after focused tests, format and analysis pass.", _NEXT),
    "REVIEW_PREREQUISITES_REQUIRED": (BLOCKED, "CI runs only after the local gates pass and REVIEW is APPROVED.", _NEXT),
    "CI_DISABLED_BY_PROJECT_POLICY": (PAUSED, "CI is disabled by project policy; record ci as DISABLED_BY_PROJECT_POLICY (never PASS) and evaluate DONE.", _NEXT),
    "IMPLEMENTATION_SLICES_NOT_GREEN": (BLOCKED, "Gates need every planned slice GREEN; finish IMPLEMENT first.", _NEXT),
    "IMPLEMENTATION_CURSOR_INVALID": (BLOCKED, "Completed slices do not advance in planned order; regenerate the snapshot with `sdd.py snapshot` and replan.", "{sdd} snapshot"),
    # --- bounded run driver / planner ---
    "PLAN_STALE": (PAUSED, "The plan no longer matches STATE or the worktree; regenerate the snapshot and plan (schema 2 replans without a new approval).", "{sdd} snapshot"),
    "BLOCKED": (BLOCKED, "STATE has blockers or recovery is BLOCKED; `sdd.py status` names them and `sdd.py next` prints the recovery.", _NEXT),
    "HUMAN_REQUIRED": (HUMAN, "The next planned action needs a human decision (protected file, scope, architecture or external mutation); `sdd.py next` stops at it with the exact command that records the answer.", _NEXT),
    "MANUAL_ACTION_COMPLETE": (PAUSED, "MANUAL mode: the authorized progress is done. A 'continue' from the user authorizes progress up to the next human checkpoint.", _NEXT),
    "LOOP_PAUSED": (PAUSED, "The loop is PAUSED: nothing new starts. Resume only on the user's explicit request.", "{sdd} resume --quote '<user words>'"),
    "EXECUTOR_UNAVAILABLE": (BLOCKED, "The prepared action's executor is not on PATH; nothing was dispatched. Ask the user to install it or to select another executor for the stage in policies/EXECUTORS.md; then `sdd.py next` dispatches, or prints `sdd.py reprepare` (archives the undispatched action and refunds its call).", _NEXT),
    "DONE": (DONE, "DONE: engineering validated. Present a commit proposal (commit and push need separate authorization), then close the demand to return STATE to IDLE.", "{sdd} close"),
    "PLAN_COMPLETE": (PAUSED, "Every planned action ran; regenerate the snapshot and plan for the next checkpoint.", "{sdd} snapshot"),
    "PLAN_EMPTY": (PAUSED, "The plan has no action to run; `sdd.py next` shows why (usually a human checkpoint).", _NEXT),
    "SEQUENCE_SKIPPED": (BLOCKED, "STATE's next action is not the plan's next action; regenerate the snapshot and plan.", "{sdd} snapshot"),
    "LOOP_INACTIVE": (PAUSED, "The bounded loop is not active (MANUAL or PAUSED); `sdd.py next` drives MANUAL progress.", _NEXT),
    "NEXT_HUMAN_CHECKPOINT": (PAUSED, "The projection reached the next human checkpoint; `sdd.py next` names it.", _NEXT),
    "RECOVERY_RECONCILIATION_REQUIRED": (PAUSED, "A pending action must be reconciled first; `sdd.py next` prints the recovery command.", _NEXT),
    "IDLE_NO_DEMAND": (PAUSED, "No demand is active; start one from the user's request.", "{sdd} start --ticket <ticket-id> --title '<title>' --objective '<objective>'"),
    # --- correction loop ---
    "NO_NEW_HYPOTHESIS": (HUMAN, "The proposed correction repeats a tried hypothesis; ask the user for a new, specific one and record it (the stage is redispatched with it).", "{sdd} unblock --quote '<user words>'"),
    "NO_PROGRESS": (HUMAN, "The last correction changed nothing observable; investigate the failure with the user and record the new direction (the stage is redispatched with it).", "{sdd} unblock --quote '<user words>'"),
    # --- legacy bounded_loop_driver (deprecated: use sdd.py next) ---
    "PLAN_CURSOR_MISMATCH": (PAUSED, "Legacy driver: the plan cursor does not match STATE; use `sdd.py next`.", _NEXT),
    "MODE_NOT_BOUNDED_AUTO": (PAUSED, "Legacy driver: the loop is not BOUNDED_AUTO; use `sdd.py next`.", _NEXT),
    "HUMAN_APPROVAL_REQUIRED": (HUMAN, "STATE records a pending human approval; ask the user, then `sdd.py next`.", _NEXT),
    "NO_NEXT_ACTION": (PAUSED, "Legacy driver: nothing left in the plan; use `sdd.py next`.", _NEXT),
}

#: Codes a runtime emits through a computed value rather than a literal (journal
#: error codes surfaced by ``recover`` when the STATE commit cannot be hashed).
DYNAMIC_STOP_REASONS = frozenset({"STATE_PATH_REQUIRED", "STATE_PATH_UNSAFE", "STATE_COMMIT_FILE_MISSING"})


def sdd_command() -> str:
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(SDD_SCRIPT))}"


def describe(code: str) -> dict[str, Any]:
    """Return {stop_reason, kind, next_step, next_command} for a registered code."""
    kind, step, command = STOP_REASONS.get(code, STOP_REASONS["BLOCKED"])
    return {
        "stop_reason": code,
        "kind": kind,
        "next_step": step,
        "next_command": command.format(sdd=sdd_command()),
    }


def policy_table() -> str:
    """Markdown table rendered in LOOP_POLICY.md §9 (the test compares the code column)."""
    rows = ["| Stop reason | Kind | Next action |", "| --- | --- | --- |"]
    for code, (kind, step, command) in STOP_REASONS.items():
        action = f"{step} → `{command.replace('{sdd} ', 'sdd.py ')}`"
        rows.append(f"| `{code}` | {kind} | {action} |")
    return "\n".join(rows)


if __name__ == "__main__":  # pragma: no cover - documentation helper
    print(policy_table())
