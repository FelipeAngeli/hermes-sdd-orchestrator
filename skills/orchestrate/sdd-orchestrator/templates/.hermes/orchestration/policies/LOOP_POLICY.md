# SDD Orchestrator — Loop Policy

Authoritative rules for modes, budgets, transitions and stops. The controller does not read this file at session start: `runtime/sdd.py next` applies it and prints the next command. Read a section only when a stop names it.

## 1. Principle

Autonomy is never unbounded. Every round is bounded by the FSM, invariants, contracts, ownership, budgets, timeouts, gates, explicit stop conditions and human approval. There is no UNBOUNDED_AUTO. Goal: run as much **safe and verifiable** work as possible before the next human checkpoint.

## 2. Modes

`LOCAL_DELIVERY` is not a `mode` enum value: it is the schema 2 profile (`schema_version: 2`, `local_delivery`, `loop.mode: BOUNDED_AUTO`).

| Mode / profile | Entered by | Advances | Stops at | Resume |
| --- | --- | --- | --- | --- |
| `MANUAL` (default) | install, or the user leaving a bounded run | through `sdd.py next` when the user asks; "continue"/"pode seguir" authorizes progress **up to the next HUMAN_REQUIRED stop**, not a single action | every HUMAN/BLOCKED stop, and `MANUAL_ACTION_COMPLETE` | the user's next request, then `sdd.py next` |
| `LOCAL_DELIVERY` (schema 2) | an explicit delivery request ("orquestre/implemente <demand>"): `sdd.py start` shows ONE preview with the fixed cumulative limits; the request is the authorization, bound to ticket, scope hash and worktree | automatically through `sdd.py next` without new questions; `REPLAN_REQUIRED` regenerates snapshot → plan → bind, preserving the ledger and cumulative totals | next HUMAN checkpoint, a BLOCKED stop, a spent global limit, scope/worktree drift | the user's answer through the printed command |
| `BOUNDED_AUTO` legacy (schema 1) | a fresh deterministic `BOUNDED RUN PREVIEW` (policies/BOUNDED_AUTOMATION.md) and an unambiguous yes right after it (`sim`, `autorizo`, `pode iniciar`, `continue`), valid only for that `plan_sha256` | `bounded_run_driver.py next` until `end_turn: true`; `ROLLOVER_REQUIRED` needs rollover and a new `DISPATCH_ALLOWED` before the next dispatch (never `RELEASED → prepare`) | per-round budgets (§4), `PLAN_STALE` before activation | a new preview and confirmation |
| `PAUSED` | the user ("pause"), or a healthy stop (budget, checkpoint, approval) | nothing new starts; reading and explaining only | — | an explicit user command, then `sdd.py next` |

`PAUSED` is a healthy halt waiting for a decision; `BLOCKED` is a technical or safety impediment (§8). After any `STOP_*`, resume by running the stop's `next_command` (§9) and then `sdd.py next`.

Forbidden: UNBOUNDED_AUTO, infinite loops, recursion between agents, executors starting orchestrators, reusable generic authorization for future plans. External effects (commit, push, PR, issue tracker, backend, DEV E2E) need explicit authorization; writing to the Obsidian wiki does not (§18).

## 3. Unit of iteration

Each iteration runs ONE logical action: one stage dispatch, its validation, one TDD slice, or one gate. "Implement the whole demand" is never one iteration.

## 4. Budgets

| Budget | Default | Counter | Resets on |
| --- | --- | --- | --- |
| `stage_transitions` | 3 per schema 1 round / demand limit in LOCAL_DELIVERY | `used` | never within a demand |
| `executor_calls` | 8 per round | `used` | never within a demand |
| `corrective_retries` | 1 per action | `used_current_action` | journal rollover to a new action |
| `tdd_slices` | 3 per round | `used` | never within a demand |
| `investigation_expansions` | 1 per stage | `used_current_stage` | stage transition |
| `review_cycles` | 2 per round | `used` | never within a demand |
| `ci_runs` | 1 per round | `used` | never within a demand |
| `external_mutations` | 0 | `used` | never |

Resets are implemented by `bounded_run_planner.reset_budgets(budgets, "ROLLOVER" | "STAGE_TRANSITION")`. Schema 1 limits are per round and a new round needs a new preview; never apply that reset to LOCAL_DELIVERY totals. When a budget is spent: finish the current action safely, commit STATE, start nothing new, stop with the budget's stop reason. More budget is authorized only with the user's words: `sdd.py budget --raise <budget> --by <n> --quote "..."`.

## 5. FSM and delivery profiles

`CODE` / `BOTH`: SPECIFY → CLARIFY → PLAN → TASKS → IMPLEMENT → TEST → REVIEW → DONE.
`DECISION_DOC`: SPECIFY → CLARIFY → PLAN → IMPLEMENT (the document) → REVIEW → DONE; TASKS and TEST are recorded as SKIPPED with the reason "not part of the DECISION_DOC delivery profile".

CLARIFY may be SKIPPED only when no material question is open and the reason is recorded in STATE. No other stage is skipped. Reopening IMPLEMENT from TEST/REVIEW needs a recorded reason (`sdd.py transition --to IMPLEMENT --reason ... --quote ...`). `state_format.apply_transition` enforces this.

## 6. Transition conditions

| Transition | Requires |
| --- | --- |
| SPECIFY → CLARIFY/PLAN | valid EXECUTOR_CONTRACT result, verifiable scope, real paths, no blockers, ownership preserved, budget available; CLARIFY when material questions are open |
| CLARIFY → PLAN | questions answered (`sdd.py answer`) or CLARIFY skipped with a reason |
| PLAN → TASKS | valid result, validated paths and symbols, impact analysis (definitions, call sites, registrations, mocks, tests) when public contracts change, no blockers |
| TASKS → IMPLEMENT | real paths/symbols, known `impact_files`, ownership computed, every acceptance check assigned to a slice, no write to protected pre-existing files needed |
| IMPLEMENT → TEST | every planned slice GREEN with RED/GREEN evidence; if only the slice budget ran out, IMPLEMENT stays IMPLEMENT and the loop stops with `TDD_SLICE_BUDGET_REACHED` |
| TEST → REVIEW | `focused_tests`, `format`, `analyze` PASS (order and commands in policies/GATES.md) |
| REVIEW → DONE | review APPROVED; CI PASS, or `DISABLED_BY_PROJECT_POLICY` when CI is disabled (never run it, never turn it into PASS); every acceptance check PASS or WAIVED; no blocker |

## 7. Mandatory stops

Stop immediately on: safety (protected file modified, ownership violation, destructive action), contract (invalid result after the allowed retry), investigation budget exceeded or conflicting sources of truth, executor timeout/failure after the single retry (§10), TDD (invalid RED, failed GREEN), failed gates, environment (missing toolchain, sandbox permission), budget, and before any action that needs human approval: commit, push, issue-tracker update, backend mutation, DEV E2E, protected-file change, material scope expansion, destructive action, external contract change, or a material architectural choice between equivalent options.

## 8. PAUSED versus BLOCKED

`PAUSED`: healthy, stopped by budget, checkpoint, human approval or voluntary end (e.g. stage IMPLEMENT, `TDD_SLICE_BUDGET_REACHED`). `BLOCKED`: technical or safety impediment (e.g. stage TEST, `ANALYZE_FAILED`). `HUMAN` stops are PAUSED until the user's decision is recorded through the printed command.

## 9. Stop reasons

This table is the closed vocabulary: it is generated from `runtime/stop_reasons.py`, and `tests/test_stop_reasons.py` fails when a runtime emits a code that is not listed here or a listed code is no longer emitted. Every reason has exactly one next action; `sdd.py next` prints it as `next_step`/`next_command` (fill `<...>` with the user's own words). Never invent a stop reason.

| Stop reason | Kind | Next action |
| --- | --- | --- |
| `NONE` | PAUSED | Nothing stops the loop; continue with the next step. → `sdd.py next` |
| `STAGE_TRANSITION_BUDGET_REACHED` | HUMAN | The stage-transition budget is spent; ask the user whether to authorize more transitions. → `sdd.py budget --raise stage_transitions --by 1 --quote '<user words authorizing more>'` |
| `EXECUTOR_CALL_BUDGET_REACHED` | HUMAN | The executor-call budget is spent; ask the user whether to authorize more calls. → `sdd.py budget --raise executor_calls --by 1 --quote '<user words authorizing more>'` |
| `RETRY_BUDGET_REACHED` | HUMAN | The corrective-retry budget for this action is spent; ask the user to authorize one more retry or to reopen the stage. → `sdd.py budget --raise corrective_retries --by 1 --quote '<user words authorizing more>'` |
| `TDD_SLICE_BUDGET_REACHED` | HUMAN | The TDD-slice budget is spent; IMPLEMENT stays open. Ask the user to authorize more slices. → `sdd.py budget --raise tdd_slices --by 1 --quote '<user words authorizing more>'` |
| `INVESTIGATION_BUDGET_REACHED` | HUMAN | The investigation-expansion budget for this stage is spent; ask for one explicit expansion. → `sdd.py budget --raise investigation_expansions --by 1 --quote '<user words authorizing more>'` |
| `REVIEW_CYCLE_BUDGET_REACHED` | HUMAN | The review-cycle budget is spent; ask the user whether to authorize another REVIEW. → `sdd.py budget --raise review_cycles --by 1 --quote '<user words authorizing more>'` |
| `CI_RUN_BUDGET_REACHED` | HUMAN | The CI-run budget is spent; ask the user whether to authorize another CI run. → `sdd.py budget --raise ci_runs --by 1 --quote '<user words authorizing more>'` |
| `EXTERNAL_MUTATION_REQUIRED` | HUMAN | The next action mutates an external system (commit, push, tracker, backend); it needs separate explicit authorization outside the loop. |
| `BUDGET_REACHED` | HUMAN | A LOCAL_DELIVERY total limit is spent; `sdd.py status` names which. Ask the user to raise it. → `sdd.py budget --raise <budget> --by 1 --quote '<user words authorizing more>'` |
| `COST_BUDGET_REACHED` | HUMAN | The correction cost budget is spent; ask the user to authorize more cost or a cheaper tier. |
| `CORRECTIVE_RETRY_EXHAUSTED` | HUMAN | The corrective retry of this action was already used; ask the user to authorize one more retry. → `sdd.py budget --raise corrective_retries --by 1 --quote '<user words authorizing more>'` |
| `EXECUTOR_TIMEOUT` | BLOCKED | The worker timed out twice (the first timeout already got one reduced-context retry). Ask the user to raise the stage timeout in policies/EXECUTORS.md or authorize one more retry. → `sdd.py budget --raise corrective_retries --by 1 --quote '<user words authorizing more>'` |
| `EXECUTOR_FAILED` | BLOCKED | The worker process failed again after its retry; check `executor_launch.py preflight` for that executor, then authorize one more retry. → `sdd.py budget --raise corrective_retries --by 1 --quote '<user words authorizing more>'` |
| `EXECUTOR_PROCESS_ENDED_WITHOUT_ARTIFACT` | PAUSED | The worker ended without a final message; archive it as interrupted (the printed command), then `sdd.py next` prepares the single retry. → `sdd.py next` |
| `ACTION_RECOVERY_REQUIRED` | BLOCKED | The journal holds an open action; `sdd.py next` prints the recovery command for it. → `sdd.py next` |
| `ACTION_BLOCKED` | BLOCKED | The action was blocked; archive it with a reason (printed command), then `sdd.py next`. → `sdd.py next` |
| `DIRTY_OR_INCONSISTENT_IDLE` | BLOCKED | The idle journal carries stray evidence; archive it with a reason (printed command), then `sdd.py next`. → `sdd.py next` |
| `JOURNAL_INCONSISTENT` | BLOCKED | The journal combines evidence no command produces; block and archive it (printed command), then `sdd.py next`. → `sdd.py next` |
| `ARTIFACT_PENDING` | BLOCKED | A final-message file already exists for an undispatched action; block and archive the action, then `sdd.py next`. → `sdd.py next` |
| `STATE_DESYNC` | BLOCKED | STATE differs from both prepared hashes; restore STATE from the journal's snapshot or archive the action with archive-blocked. Never hand-edit STATE. |
| `STATE_PATH_REQUIRED` | BLOCKED | The journal's state commit has no STATE path; archive the action with archive-blocked and run `sdd.py next`. → `sdd.py next` |
| `STATE_PATH_UNSAFE` | BLOCKED | The journal's STATE path is not this worktree's canonical STATE (see `action_journal.py paths`); archive the action and run `sdd.py next`. → `sdd.py next` |
| `STATE_COMMIT_FILE_MISSING` | BLOCKED | STATE.md is missing or not a regular file; restore it, then run `sdd.py next`. → `sdd.py next` |
| `STATE_INCONSISTENT` | BLOCKED | STATE.md cannot be parsed or lacks required keys; restore the last committed STATE (history), never repair it by hand. |
| `CONTRACT_INVALID` | PAUSED | The worker result failed validation; it is classified invalid. `sdd.py next` archives it and prepares the single corrective retry with the errors. → `sdd.py next` |
| `WORKER_BLOCKED` | HUMAN | The worker reported blockers; read them in `sdd.py status` and resolve them with the user (answer, scope or reopen). → `sdd.py status` |
| `CLARIFICATION_REQUIRED` | HUMAN | Material questions are open; ask the user exactly the listed questions and record each answer. → `sdd.py answer --index <n> --quote '<user words>'` |
| `HUMAN_DECISION_REQUIRED` | HUMAN | A HUMAN acceptance check needs the user's decision; record it verbatim (approval or waiver). → `sdd.py waive --check <check-id> --by requester --quote '<user words>' --reason '<why>'` |
| `SCOPE_CHANGE_REQUIRED` | HUMAN | The slice contract differs from the approved one; it is new scope and needs its own approval. |
| `INVESTIGATION_BUDGET_EXCEEDED` | HUMAN | The context budget of the manifest is exceeded; ask for an explicit expansion or narrow the sources. → `sdd.py budget --raise investigation_expansions --by 1 --quote '<user words authorizing more>'` |
| `PROMPT_BUDGET_EXCEEDED` | BLOCKED | The worker prompt exceeds max_prompt_bytes even after reduction; narrow the manifest sources or raise max_prompt_bytes in the manifest. |
| `MANIFEST_INVALID` | BLOCKED | The stage-context manifest was refused; the findings name the field. Fix the source data, then `sdd.py next`. → `sdd.py next` |
| `JEV_GOVERNANCE_REQUIRED` | BLOCKED | Automatic Jev consent is recorded: run `semantic_governor.py decide` for this ticket and record its fingerprint before PLAN/IMPLEMENT. |
| `BASELINE_DRIFT_EXTERNAL` | HUMAN | HEAD or branch changed outside the agent; ask the user whether to continue on the new baseline. |
| `PREEXISTING_FILE_MODIFIED` | BLOCKED | A protected pre-existing file changed during the demand; stop and ask the user. Never restore it with Git reset/checkout. |
| `FOCUSED_TESTS_FAILED` | BLOCKED | Focused tests failed; reopen IMPLEMENT for a corrective slice with the user's agreement. → `sdd.py transition --to IMPLEMENT --reason '<failure>' --quote '<user words>'` |
| `FORMAT_FAILED` | BLOCKED | The formatter failed; reopen IMPLEMENT for a corrective slice with the user's agreement. → `sdd.py transition --to IMPLEMENT --reason '<failure>' --quote '<user words>'` |
| `ANALYZE_FAILED` | BLOCKED | Static analysis failed; reopen IMPLEMENT for a corrective slice with the user's agreement. → `sdd.py transition --to IMPLEMENT --reason '<failure>' --quote '<user words>'` |
| `CI_FAILED` | BLOCKED | CI failed; CI is never retried automatically. Reopen IMPLEMENT with the user's agreement. → `sdd.py transition --to IMPLEMENT --reason '<failure>' --quote '<user words>'` |
| `CI_TIMEOUT` | BLOCKED | CI timed out; it blocks advancement. Ask the user how to proceed. |
| `GATE_TIMEOUT` | BLOCKED | A gate command timed out; it blocks advancement. Ask the user to raise its timeout in GATES.md. |
| `REVIEW_BLOCKED` | BLOCKED | REVIEW reported a blocker; reopen IMPLEMENT with the user's agreement. → `sdd.py transition --to IMPLEMENT --reason '<failure>' --quote '<user words>'` |
| `REVIEW_CHANGES_REQUIRED` | HUMAN | REVIEW requires changes; reopen IMPLEMENT for a corrective slice with the user's agreement. → `sdd.py transition --to IMPLEMENT --reason '<failure>' --quote '<user words>'` |
| `OWNERSHIP_VIOLATION` | BLOCKED | REVIEW found writes outside the agent-owned paths; stop and ask the user. |
| `GATE_COMMAND_UNCONFIGURED` | BLOCKED | A required gate has no verified command in policies/GATES.md; configure it (run it once) before this stage. |
| `GATE_CONFIRMATION_REQUIRED` | HUMAN | GATES.md marks this gate NOT_APPLICABLE; record the user's explicit confirmation. → `sdd.py gate --name <gate> --not-applicable --by requester --quote '<user words>'` |
| `DONE_GATES_NOT_PASSED` | BLOCKED | DONE needs focused tests, format, analysis, review and CI (or DISABLED_BY_PROJECT_POLICY) to pass; `sdd.py next` prints the missing gate. → `sdd.py next` |
| `FOCUSED_TESTS_REQUIRED` | BLOCKED | Format runs only after focused tests pass; run the focused-tests gate first. → `sdd.py next` |
| `TEST_AND_FORMAT_REQUIRED` | BLOCKED | Analysis runs only after focused tests and format pass. → `sdd.py next` |
| `TEST_FORMAT_AND_ANALYZE_REQUIRED` | BLOCKED | REVIEW runs only after focused tests, format and analysis pass. → `sdd.py next` |
| `REVIEW_PREREQUISITES_REQUIRED` | BLOCKED | CI runs only after the local gates pass and REVIEW is APPROVED. → `sdd.py next` |
| `CI_DISABLED_BY_PROJECT_POLICY` | PAUSED | CI is disabled by project policy; record ci as DISABLED_BY_PROJECT_POLICY (never PASS) and evaluate DONE. → `sdd.py next` |
| `IMPLEMENTATION_SLICES_NOT_GREEN` | BLOCKED | Gates need every planned slice GREEN; finish IMPLEMENT first. → `sdd.py next` |
| `IMPLEMENTATION_CURSOR_INVALID` | BLOCKED | Completed slices do not advance in planned order; regenerate the snapshot with `sdd.py snapshot` and replan. → `sdd.py snapshot` |
| `PLAN_STALE` | PAUSED | The plan no longer matches STATE or the worktree; regenerate the snapshot and plan (schema 2 replans without a new approval). → `sdd.py snapshot` |
| `BLOCKED` | BLOCKED | STATE has blockers or recovery is BLOCKED; `sdd.py status` names them and `sdd.py next` prints the recovery. → `sdd.py next` |
| `HUMAN_REQUIRED` | HUMAN | The next planned action needs a human decision (protected file, scope, architecture or external mutation); ask the user. |
| `MANUAL_ACTION_COMPLETE` | PAUSED | MANUAL mode: the authorized progress is done. A 'continue' from the user authorizes progress up to the next human checkpoint. → `sdd.py next` |
| `DONE` | DONE | DONE: engineering validated. Present a commit proposal; commit and push need separate authorization. |
| `PLAN_COMPLETE` | PAUSED | Every planned action ran; regenerate the snapshot and plan for the next checkpoint. → `sdd.py snapshot` |
| `PLAN_EMPTY` | PAUSED | The plan has no action to run; `sdd.py next` shows why (usually a human checkpoint). → `sdd.py next` |
| `SEQUENCE_SKIPPED` | BLOCKED | STATE's next action is not the plan's next action; regenerate the snapshot and plan. → `sdd.py snapshot` |
| `LOOP_INACTIVE` | PAUSED | The bounded loop is not active (MANUAL or PAUSED); `sdd.py next` drives MANUAL progress. → `sdd.py next` |
| `NEXT_HUMAN_CHECKPOINT` | PAUSED | The projection reached the next human checkpoint; `sdd.py next` names it. → `sdd.py next` |
| `RECOVERY_RECONCILIATION_REQUIRED` | PAUSED | A pending action must be reconciled first; `sdd.py next` prints the recovery command. → `sdd.py next` |
| `IDLE_NO_DEMAND` | PAUSED | No demand is active; start one from the user's request. → `sdd.py start --ticket <ticket-id> --title '<title>' --objective '<objective>'` |
| `NO_NEW_HYPOTHESIS` | HUMAN | The proposed correction repeats a tried hypothesis; supply a new, specific one or stop. |
| `NO_PROGRESS` | HUMAN | The last correction changed nothing observable; investigate the failure with the user before another attempt. |
| `PLAN_CURSOR_MISMATCH` | PAUSED | Legacy driver: the plan cursor does not match STATE; use `sdd.py next`. → `sdd.py next` |
| `MODE_NOT_BOUNDED_AUTO` | PAUSED | Legacy driver: the loop is not BOUNDED_AUTO; use `sdd.py next`. → `sdd.py next` |
| `HUMAN_APPROVAL_REQUIRED` | HUMAN | STATE records a pending human approval; ask the user, then `sdd.py next`. → `sdd.py next` |
| `NO_NEXT_ACTION` | PAUSED | Legacy driver: nothing left in the plan; use `sdd.py next`. → `sdd.py next` |

## 10. Retry and timeouts

At most one corrective retry per action (`max_corrective_retries_per_action: 1`), as a new journal action with `parent_action_id` and `retry_mode: FULL_REPLACEMENT`. A retry is allowed only for a known cause with a deterministic correction (e.g. a cited path does not exist), with ownership intact and no external mutation. Never retry automatically: CI failure, ownership violation, baseline drift, unknown environment failure, destructive action.

Executor timeout: the first `EXECUTOR_TIMEOUT` → `action_journal.py archive-interrupted` → one FULL_REPLACEMENT retry with reduced context (brief and manifest pointers, no inline excerpts) or the cross-executor fallback of §11. A second timeout → BLOCKED with `EXECUTOR_TIMEOUT` and its next step. `sdd.py next` prints exactly this sequence.

Prompt ceiling: the stage-context manifest carries `limits.max_prompt_bytes` (default 48 KB). `stage_context.py check --prompt-file` refuses a larger prompt with `PROMPT_TOO_LARGE`; `sdd.py prepare` first retries with reduced context and stops with `PROMPT_BUDGET_EXCEEDED` only if that is still too large.

### Action recovery precondition

Before every dispatch the printed batch runs `action_journal.py recover`; its decision → command table is `policies/ACTION_RECOVERY.md` (the single authoritative place). `RELEASED → prepare` directly is prohibited; an unclassified artifact is reconciled before any redispatch; `ARCHIVE_INTERRUPTED_REQUIRED` archives and then prepares the single retry.

## 11. Executor fallback

This fallback applies only to MANUAL actions. BOUNDED_AUTO plans use the CODEX executor declared by the planner and never switch executor during a round. In MANUAL, Claude → Codex is allowed once (policies/EXECUTORS.md). The fallback counts as an executor call, resets neither the retry nor the transition budget, and is recorded in STATE. Claude → Codex → Claude → Codex is forbidden; if the fallback also fails: BLOCKED.

## 12. TDD slices

Each IMPLEMENT slice: small task, hypothesis, RED with the expected failure, minimal change, GREEN, STATE update, executor ends. A new slice needs the previous GREEN, budget, valid ownership and no blocker. Never mark IMPLEMENT complete because the slice budget ran out.

## 13. Investigation budget

Initial scope: at most 12 files, 250 relevant lines per file, 60 scoped search matches. One expansion per stage, recording reason, extra paths and new limit (`sdd.py budget --raise investigation_expansions`). Never turn an expansion into an unrestricted search.

## 14. Gates

`policies/GATES.md` is the single source for gate commands, order, timeouts, CI policy and blocking conditions; `sdd.py gate --name <gate>` runs exactly the configured command. A gate marked `NOT_APPLICABLE` needs the user's explicit confirmation, recorded as `gates.<name>.not_applicable` {by, quote, reason, recorded_at} by `sdd.py gate --name <gate> --not-applicable --by ... --quote ...`; the DONE check accepts it.

## 15. REVIEW

At most `max_review_cycles_per_run: 2`. APPROVED → CI per project policy. CHANGES_REQUIRED → automatic corrections only when findings carry verifiable evidence, paths are agent-owned, scope is unchanged and the slice and review budgets allow; otherwise stop. REVIEW verifies accepted outcomes independently; green gates alone do not establish product acceptance.

## 16. CI

CI is an expensive gate: `max_ci_runs_per_run: 1`, never retried automatically. CI PASS updates STATE immediately. When CI is `DISABLED_BY_PROJECT_POLICY` it is not run and never becomes PASS.

## 17. Human in the loop

A HUMAN stop reports `stage`, `reason`, `requested_action`, `affected_paths`, `risk` and `recommended_option` in a few lines, then waits. Human decisions are recorded verbatim (`sdd.py answer`, `sdd.py waive`, `sdd.py budget --raise`, `sdd.py gate --not-applicable`); a waiver is `{by, reason, quote, recorded_at}` and reaches `validate_protocol` as `recorded_waivers`. The controller asks at most one material question at a time.

## 18. Obsidian

Obsidian is read **and write**: the project wiki (`SCHEMA.md`, `index.md`, `log.md`, `raw/`, `entities/`, `concepts/`, `comparisons/`, `queries/`) is the project memory and receives everything that runs in it, without approval.

`sdd.py` records through `runtime/wiki_journal.py` (`record --repo . --kind <kind> --title <t> --body-file <file>`): each completed stage artifact → `--kind stage` (`raw/articles/<ticket>/`); each gate result → `--kind gate` (`raw/articles/<ticket>/gates/`); each decision with its reason → `--kind decision` (`concepts/`, listed in `index.md`); modules/services → `--kind entity`; concepts/rules → `--kind concept`; comparisons → `--kind comparison`; answers worth keeping → `--kind query`. The runtime also records every journaled action and finished worker (`raw/articles/<ticket>/actions/`) and every incident. With the `post_llm_call`/`on_session_finalize` hooks active, conversation turns inside a served worktree go to `raw/transcripts/sessions/`.

`raw/` is immutable; layer-2 pages receive dated sections; every record enters `log.md`. Known credential formats are masked before writing, but that is a safety net: never paste or record credentials. A wiki write failure never blocks the loop or DONE: the record is `SKIPPED` and the loop continues.

## 19. Commit and push

DONE means engineering validated, not committed or pushed. After DONE the controller may present a COMMIT PROPOSAL; push needs separate approval.

## 20. Transactional STATE

action → result → validation → STATE commit (through the journal) → next action. Never run several critical actions and update STATE only at the end. STATE and the journal are written only by runtimes (`sdd.py`, `action_journal.py`, `state_format.py`); never by hand.

## 21. STATE integrity

`sdd.py status` parses STATE with `state_format.py` (JSON or legacy YAML dialect, duplicate keys refused). An unparseable or incomplete STATE stops with `STATE_INCONSISTENT`; never repair a structural inconsistency by hand and continue in the same iteration.

## 22. Baseline drift

A changed Git state does not imply an agent write. Check the run's write set, HEAD/branch, external commits and resets before classifying. Without evidence of an agent write use `BASELINE_DRIFT_EXTERNAL` and pause; never claim `PREEXISTING_FILE_MODIFIED` without objective evidence.

## 23. Progress reporting

Progress messages are short: stage, action, executor, budgets left and `stop_reason` (`sdd.py status` prints them). Never paste long logs.

## 24. End of round

Each round ends with a short summary: start/end stage, result (COMPLETED | PAUSED | BLOCKED), executor calls, slices, gates, `stop_reason`, whether human approval is required, `next_step`, STATE updated, repository integrity, commit/push NOT PERFORMED, wiki RECORDED | SKIPPED.

## 25. Harness: stage context and bounded correction

Before each dispatch the printed batch writes the stage-context manifest (`sdd.py manifest`, hashes computed) and runs `stage_context.py check`; any finding prevents dispatch and is never auto-corrected:

- context budget exceeded or whole document → `INVESTIGATION_BUDGET_EXCEEDED` (expansion per §13); prompt above `max_prompt_bytes` → `PROMPT_TOO_LARGE`;
- PLAN/IMPLEMENT without a `project-context-guardian` result → dispatch the guardian first (cache-first);
- slice without `editable_paths`, without an observable verifier, or only self-created verifiers → `CONTRACT_INVALID`;
- slice hash different from the approved one → `SCOPE_CHANGE_REQUIRED`.

When PLAN/TASKS are approved the controller stores each planned slice hash in `approved_slice_sha256s`; a matching hash (`APPROVAL_REUSED`) is already approved. TEST and REVIEW do not authorize writes (`APPROVAL_NOT_APPLICABLE`). Approval never covers commit, push, issue tracker, backend or DEV E2E. Code/doc divergence is recorded with `authority: CODE`; never invent the decision that would explain it.

The worker result is validated with `validate_protocol.py --context` using `stage_context.py verifier-context --state` (which also carries `recorded_waivers`). An AGENT PASS must cite, in backticks, a command bound to that check, recorded with exit 0 and PASS; read-only roles (`project-context-guardian`, `data-flow-tracer`) are validated with `role`; SPECIFY, CLARIFY, PLAN, TASKS and TEST report no changed files; IMPLEMENT changes only `editable_paths`.

When a valid result fails verification, consult `runtime/correction_loop.py decide` before any correction: `VERIFIED` → commit and continue; `CORRECT` → one correction with the proposed hypothesis and tier, as a new journal action with `parent_action_id`; `STOP` → stop with its `stop_reason` and `next_step`. Limits: `max_attempts = 1 + max_corrective_retries_per_action`; `max_executor_calls` = calls left in the round or LOCAL_DELIVERY authorization; `max_cost_units: null` (record only) unless configured. Never repeat a tried hypothesis or an unchanged correction, never escalate to a costlier tier without a recorded concrete failure and reason; deterministic local checks come first.

## 26. Supreme rule

When speed conflicts with safety/verifiability, choose safety/verifiability: run the most safe and verifiable work, not the most work.
