# Changelog

All notable changes to the orchestration are recorded here. Every change under `skills/`, `tools/`, `.githooks/` or `.github/workflows/` adds an entry (enforced by `tools/check_docs_sync.py`; see [docs/maintaining-docs.md](docs/maintaining-docs.md)).

## Unreleased

### Added
- A subordinate, typed Jev decision layer: `decision_orchestration.py` and strict request/receipt schemas implement closed-candidate `OFF`/`SHADOW`/`ACTIVE`/`FALLBACK` decisions with explicit-choice and deterministic-completion precedence, redaction before evaluation, and observed latency/token/billing receipts. `sdd.py next` now emits a `GOVERN` step before PLAN and IMPLEMENT under explicit automatic consent; the initial rollout is SHADOW-only, persists stage/STATE-bound receipts, binds the operating mode into the cached governor fingerprint, never executes Jev's recommendation, and lets only fingerprint-verified low-confidence shadow observations fall back without creating a false human checkpoint. The evaluation report includes a reproducible six-workload fixture and sanitized pilot records, while explicitly withholding performance claims below the preregistered sample size.

## 15.0.1 - 2026-10-09

### Fixed
- 15.0.0 is unreleased on this branch, so the `sdd.py restore-policy` command below is recorded as part of the fixes to it.
- `CONTROLLER_WRITABLE_BY_WORKER` is no longer a dead end (pr-reviewer round 4 on #45, BLOCKING). Its exit `migrate_to_vault.py --repo <repo> --apply` failed when executed (exit 2 `BINDING_MISSING` on a `--local-storage` install, refused an open demand, and by design kept `runtime/*.py` and `policies/` in the repository). The stop now prints `exit_commands`: `abandon`, `install_project.py --obsidian-vault <vault> --obsidian-project <project>` dry run and `--apply`, `mv` of the in-repository controller into `<vault>/<project>/.hermes-local-controller-backup/`, `confirm-policy` for the new container's policies and the new controller's `start`; a test executes every printed command and reaches `PREPARE`.
- `sdd.py` and the launcher now use the same isolation criterion (round 4, MEDIUM): the controller's physical location inside the repository (device/inode ancestor walk, so case-different macOS paths match), not the storage label. A `--local-storage` install with `.hermes/obsidian.json` reported `OBSIDIAN` and printed PREPARE/DISPATCH batches the launcher refused every time.
- An IMPLEMENT/TEST action PREPARED before the isolation check applied no longer loops on a refused `DISPATCH` (round 4, MINOR): the recovery path stops with `CONTROLLER_WRITABLE_BY_WORKER`, and `abandon` now archives an undispatched PREPARED action (`undispatched_action`) instead of refusing with `ACTION_RECOVERY_REQUIRED`.
- `CONTROLLER_POLICY_UNREADABLE` no longer prints `git checkout -- <file>` (round 4, BLOCKING): it failed in both modes (the vault is not a Git repository; a `--local-storage` controller is excluded from Git). Its `restore_commands` (`restore-policy`, `confirm-policy`, `next`) and the fallback `reinstall_commands` (`abandon`, `install_project.py --upgrade` dry run and `--apply`, `confirm-policy`, `start`) are executed by tests.
- Security (R4-01, HIGH): a Claude dispatch no longer loads project-controlled agent configuration. `claude -p` ran with the repository as working directory and skips the workspace-trust dialog, so `.claude/settings.json`/`settings.local.json` hooks ran as the user outside every tool restriction (proven with SessionStart/UserPromptSubmit hooks under `--tools Read,Grep,Glob`), and `.mcp.json` servers were started; a writing Codex IMPLEMENT worker could plant them for the next Claude stage. Every Claude call (dispatch and `preflight --probe`) now passes `--setting-sources user --strict-mcp-config --disable-slash-commands`, verified against claude 2.1.294 with a planted repository: no project hook, local hook or MCP server ran, and OAuth login kept working (`--bare` was rejected because it disables OAuth/keychain auth). `preflight` returns `EXECUTOR_CLI_UNSUPPORTED` when the CLI's help lacks any of the three flags, naming the missing ones in `next_step`. Codex has no flag that ignores a project `.codex/`; the residual risk is documented in `policies/EXECUTORS.md`.
- Security (R4-03, LOW): the launcher's containment test (`--add-dir` and `CONTROLLER_WRITABLE_BY_WORKER`) compares `(st_dev, st_ino)` along the ancestors, as the installer does for `OBSIDIAN_VAULT_OVERLAPS_TARGET`. `Path.resolve()` keeps the caller's letter case on case-insensitive macOS, so a case-flipped path to the launcher returned `READY` for a writing stage that the canonical path refused.
- Security (R4-02, MEDIUM, documentation): `policies/EXECUTORS.md`, `docs/components/contracts-and-schemas.md` and `docs/components/gates-and-stack-detection.md` no longer claim that in Obsidian storage the worker "cannot reach `GATES.md`, STATE or the journal at all". The worker *process* cannot; code it writes into the repository and that a gate executes on the host (a `conftest.py` run by the focused tests, for example) can. Gates are not sandboxed: they must run only in a trusted environment, and REVIEW reads the diff of test, fixture, build and tool-configuration files.
- `CONTROLLER_WRITABLE_BY_WORKER` from `executor_launch.py` no longer points to `migrate_to_vault.py`, which keeps `runtime/*.py` and `policies/` in the repository by design and so never ended the refusal. Its `next_step` is now: abandon the demand, reinstall the controller in Obsidian mode with `install_project.py --obsidian-vault <vault>`, then start again; `next_command` is that install command.
- `sdd.py restore-policy --name gates|executors --skill <installed-skill>` (status `CONTROLLER_POLICY_RESTORED`): recreates a missing or unreadable controller policy file from the installed skill's template, only when that template's SHA-256 equals the `INSTALL_MANIFEST.json` record, without ending the demand. `stop_reasons.USER_PLACEHOLDERS` gains `<vault>`, `<project>` and `<installed-skill>`. Stops `CONTROLLER_WRITABLE_BY_WORKER` and `CONTROLLER_POLICY_UNREADABLE` carry the full exit as `exit_commands` / `restore_commands` (+ `reinstall_commands`).

## 15.0.0 - 2026-10-09

### Breaking
- Security (closes the round-3 findings R3-01, R3-02 and R3-03 on #45 by prevention): **a writing worker is no longer dispatched while the controller shares its writable filesystem.** In `--local-storage` the controller sits inside the repository an IMPLEMENT/TEST worker edits, so that worker can rewrite `policies/GATES.md` (whose commands run on the host, outside every sandbox), `policies/EXECUTORS.md`, `runtime/*.py`, the stage briefs, STATE and the journal — *and*, in the same step, every hash those are checked against, because each anchor lives in that same tree. The round-2 detections were defeated empirically with one extra write each: re-pinning `delivery.controller_policies` after editing `GATES.md` (R3-01), rewriting `fingerprints.state_before` after editing STATE (R3-02), and patching `sdd.py` itself, which no check covered at all (R3-03). Detection has no anchor out of the worker's reach there, so the dispatch is refused instead: `executor_launch.py build|run` returns the new status `CONTROLLER_WRITABLE_BY_WORKER` and `sdd.py next` stops with the same registered stop reason **before** preparing the action (no executor call is spent), printing `migrate_to_vault.py --repo <repository> --apply` as the exit. Read-only stages (SPECIFY, CLARIFY, PLAN, TASKS, REVIEW) still run under `--local-storage`; only Obsidian storage, where the controller is never a writable root for any worker, runs a writing stage. A project that drove IMPLEMENT/TEST with `--local-storage` must migrate the controller to the vault before its next writing stage.

### Fixed
- Security (R3-04): `sdd.py start` no longer adopts a `GATES.md`/`EXECUTORS.md` changed since the last confirmed pin as the new baseline without review. The confirmed digests are carried across the demand boundary in a top-level `controller_policies` block (kept by `close`/`abandon`, which clear every other per-demand block), and `start` compares against it, refusing with `CONTROLLER_POLICY_CHANGED_DURING_DEMAND` and the `confirm-policy` command before the demand begins; a first install has no pin and records the current files as the baseline. Previously the round-2 finding ("every edit before the pin is adopted as the baseline") simply reappeared one demand later.
- Security (R3-06): `policies/EXECUTORS.md`, `docs/components/fsm-and-loop.md` and `docs/components/contracts-and-schemas.md` no longer present the tamper detection as a guarantee. They state the threat model instead: the pin and the STATE fingerprint catch an accident, a careless edit or anything reaching these files outside a dispatch, and remain the full protection in Obsidian storage, but they never stood against a worker that rewrites the anchor together with the file — which is why the writing dispatch is now refused outright.
- R3-07: the controller card (`.hermes.md`) lists `confirm-policy`, `abandon` and `disown` among the commands a stop prints, so the one document read at session start names every exit.
- `STATE_MODIFIED_DURING_ACTION` is no longer an absolute dead end (pr-reviewer round 3 on #45, HIGH). Its registered resolution was `sdd.py next`, which only re-derived the same step: `next` kept printing `VALIDATE`/`ACCEPT`, every printed command refused with the same code at exit 2, and the only documented escape — restoring STATE from the journal history — is not something the history supports (it archives journals, not STATE snapshots). The stop now names `sdd.py abandon --reason '<reason>' --quote '<user words>'`, with the reason pre-filled, and `abandon` handles the one case where an action is still open: it archives it as BLOCKED evidence (never redispatched; path and SHA-256 returned as `discarded_action`), never folds its result into STATE, resets every per-demand block to its IDLE default and records `stop_reason: STATE_MODIFIED_DURING_ACTION` in `closed_demands[-1].abandon`, so `sdd.py start` re-runs the demand. Nothing in the worktree is reverted.
- `sdd.py next` raises `STATE_MODIFIED_DURING_ACTION` itself, before any step is printed (new `state_tamper_stop`), instead of printing a batch whose every command refuses. An action whose STATE commit already landed (`STATE_COMMITTED`/`RELEASED`, or a STATE matching `expected_after_hash`) is still not a tamper.
- Confirming an unreadable controller policy no longer loops forever (pr-reviewer round 3 on #45, MEDIUM). `confirm-policy` pinned `sha256: ""` for a missing or unreadable `GATES.md`/`EXECUTORS.md`; `controller_policy_check` reads an empty pin as "no pin", so the next gate stopped with `CONTROLLER_POLICY_UNPINNED` and printed the same confirmation command, indefinitely. The new stop reason `CONTROLLER_POLICY_UNREADABLE` (BLOCKED) names the real remediation — one exact `git checkout -- <file>` per unreadable policy in `restore_commands`, plus `unreadable` and `policies` — and `confirm-policy` on such a file is refused with the same code, so a recorded pin is never overwritten with the empty digest.
- `sdd.py next` no longer prints a gate batch that a controller-policy check will refuse (pr-reviewer round 3 on #45, MEDIUM). A `GATES.md`/`EXECUTORS.md` edited mid-demand left `next` printing `GATES`, whose `gate` commands exited 2 with `CONTROLLER_POLICY_CHANGED_DURING_DEMAND` — a stop disguised as a command batch, the class round 2 closed elsewhere. `gates_step` now verifies the policies before printing and ends the turn with the stop (`changed`/`unpinned` and the `confirm-policy` command) instead. An unreadable `GATES.md` is reported as `CONTROLLER_POLICY_UNREADABLE` rather than the misleading `GATE_COMMAND_UNCONFIGURED`, whose own resolution is `next` and would loop too; the same applies to the AGENT-check guard that reads the `focused_tests` row.
- `policies/EXECUTORS.md`, `LOOP_POLICY.md` §9, `docs/components/fsm-and-loop.md` and `docs/components/contracts-and-schemas.md` describe these three exits.

## 14.0.2 - 2026-10-09

### Fixed
- 14.0.0 and 14.0.1 exist only on this unmerged branch and were never tagged, so the renames below
  (`gate --confirm-gates-policy` → `confirm-policy --name <policy>`, `GATES_CHANGED_DURING_DEMAND` →
  `CONTROLLER_POLICY_CHANGED_DURING_DEMAND`, `delivery.gates_policy` → `delivery.controller_policies`)
  correct work no published version exposed; 13.0.9 consumers see only fixes.
- `sdd.py gate --confirm-gates-policy` is replaced by `sdd.py confirm-policy --name gates|executors --by <who> --quote '<user words>'` (also reachable as `gate --confirm-policy <name>`), which confirms **one named** controller policy file; the status is `CONTROLLER_POLICY_CONFIRMED` instead of `GATES_POLICY_CONFIRMED`. The stop reason `GATES_CHANGED_DURING_DEMAND` is replaced by `CONTROLLER_POLICY_CHANGED_DURING_DEMAND` (which carries `changed`, `policies`, `sha256` and `pinned_sha256` per file). STATE moves the pin from `delivery.gates_policy` to `delivery.controller_policies.{gates,executors}`; a demand started before this version has no pin and stops with `CONTROLLER_POLICY_UNPINNED` until the user confirms each file. Automation matching the old flag, status or stop reason must switch.
- Stop reasons `CONTROLLER_POLICY_CHANGED_DURING_DEMAND`, `CONTROLLER_POLICY_UNPINNED`, `CONTROLLER_POLICY_CONFIRMATION_REQUIRED` and `STATE_MODIFIED_DURING_ACTION`; `sdd.py` subcommand `confirm-policy`; `stop_reasons.USER_PLACEHOLDERS` gains `<policy>`. `sdd.py start` reports the pinned hashes as `controller_policies`.
- `sdd.py abandon --reason R --quote Q` (any stage except DONE → IDLE): the exit of a demand that will not reach DONE. A refused human decision previously had no command at all — `close` accepted only DONE, `start` refused with `DEMAND_ACTIVE` and `next` reprinted the same `HUMAN_DECISION_REQUIRED` stop forever. It reverts nothing in the worktree, records `{reason, quote, by, stop_reason, pending_human_checks}` with `outcome: ABANDONED`, `stage_reached` and `left_in_worktree` in `closed_demands`, and returns STATE to IDLE so the next `start` is accepted. Every `HUMAN_DECISION_REQUIRED` stop now also prints `refusal_command`.
- `sdd.py disown --path P --reason R`: drops one path from `ownership.agent_owned` into `ownership.disowned_unsafe` without touching the file. It is the remediation `AGENT_OWNED_PATH_UNSAFE` names (its stop prints one `disown` command per unsafe path in `disown_commands`); previously that stop's `next_step` described dropping the path "through a reopened stage", which no command did, and its `next_command` was `sdd.py status`, which makes no progress.
- Stop reasons `ABANDON_REQUESTED`, `DEMAND_ACTIVE`, `TICKET_INVALID`, `STEP_MISMATCH`, `WAIVE_REQUIRES_HUMAN_CHECK` and `AGENT_OWNED_PATH_UNSAFE` are registered in `runtime/stop_reasons.py` and listed in `LOOP_POLICY.md` §9. They were already emitted by `sdd.py` as the `status` of an exit-2 payload but were invisible to the registry guard, so a controller trusting §9 could meet an unknown code. `stop_reasons.RUNTIME_PLACEHOLDERS` holds the `<check-id>`/`<gate>`/`<path>` placeholders the runtime fills itself.
- `action_journal.block_action(path)`: the public door to `BLOCKED` (used by the `block` CLI command and by `sdd.py reprepare`, which reached into the private `_save_transition` instead).
- `sdd.py start` sizes `ci_runs` as `review_cycles` (2, was fixed at 1): one CI failure followed by the REVIEW reopen the same authorization pre-approves no longer exhausts the budget and stops with `CI_RUN_BUDGET_REACHED`, which in practice cancelled the authorized review cycle.
- `sdd.py close` and `sdd.py abandon` clear the whole `ownership` block, `blockers` and (on `start`) `human_accepted`/`disowned_unsafe`. A path the user accepted in one demand no longer survives into the next one as `human_accepted_paths` in the worker manifest, silently suppressing an ownership finding the new demand's REVIEW should raise.
- `GATE_COMMAND_UNCONFIGURED`'s registered `next_step` no longer claims that configuring `GATES.md` makes `sdd.py next` run the gate: editing it mid-demand needs the user's `confirm-policy --name gates` once, which the stop now says. `reopen_slices` documents that clearing `delivery.approved_slice_sha256s` is deliberate (the FIX slice runs under the reopen's own `--quote` and never re-raises `SCOPE_CHANGE_REQUIRED`).
- `tests/test_stop_reasons.py` treats `raise SddError("CODE", ...)` as a stop-reason sink (`SINK_RAISES`), so a new command refusal cannot be added without registering it and documenting it in §9.
- Security (HIGH, residual finding on #45): in `--local-storage` mode the controller lives inside the repository a writing IMPLEMENT/TEST worker can edit, so that worker could rewrite `policies/GATES.md` and obtain arbitrary host command execution on the next `sdd.py gate` (gate commands run on the host, outside every worker sandbox), or rewrite `policies/EXECUTORS.md` to choose the next worker's binary, tools and sandbox. Obsidian mode prevents this by never giving the container as a writable root; local storage cannot prevent it, so the controller now **detects** it: `sdd.py start` pins the SHA-256 of both owner files in `delivery.controller_policies`, and every gate re-verifies both before running anything (`CONTROLLER_POLICY_CHANGED_DURING_DEMAND`, naming each changed file). The previous guard pinned only `GATES.md`, only on the *first gate run* — every edit before it was adopted as the baseline — and otherwise fell back to comparing the file's mtime with `delivery.started_at`, which cannot distinguish the owner's edit from a worker's; a demand with no pin is now refused (`CONTROLLER_POLICY_UNPINNED`) instead of trusted. Confirming one policy never confirms the other.
- Security (same finding, STATE/journal half): `sdd.py prepare` recorded `fingerprints.state_before` but nothing ever compared it, so a worker that rewrote `STATE.md` inside its own worktree during the dispatch had that STATE folded in by `sdd.py accept` as if the controller had written it (gates, budgets, ownership and waivers included). `accept` now verifies STATE against that fingerprint before applying any result and stops with `STATE_MODIFIED_DURING_ACTION`, pointing at the append-only journal history for the restore; a journal without the fingerprint is refused rather than trusted.
- `policies/EXECUTORS.md` no longer claims a worker "never edits `GATES.md`, STATE or the journal": that holds for Obsidian storage, while `--local-storage` relies on the detection above. The file documents the difference and names Obsidian storage as the stronger configuration.

## 14.0.1 - 2026-10-09

### Fixed
- 14.0.0 is unreleased on this branch, so the follow-up changes below are recorded as fixes to it:
  the controller surface it introduced (stop reasons, `transition`, `waive`, budgets, `EXECUTORS.md`) is
  corrected here before any tag, and no published version ever exposed the behavior they replace.
- `install_project.py --upgrade` no longer applies over a running controller whose `STATE.md` carries the JSON payload `sdd.py` writes: STATE is parsed with the template's `state_format.py` (JSON or YAML) and `UPGRADE_CONTROLLER_BUSY` is raised for `loop.control.loop_active: true`, `stage.status: RUNNING` or an unparseable/symlinked STATE, with the rerun `--upgrade` as `next_command`.
- `--upgrade` of an installation without `INSTALL_MANIFEST.json` (v13 and older) now handles the 15 sub-agent briefs 14.0.0 retired (`RETIRED_TEMPLATE_PATHS`): with `--accept-current-as-baseline` they are backed up and removed; without it each is reported as an `OBSOLETE_UNVERIFIED` warning naming the exact rerun command. Previously they stayed on disk silently.
- `action_journal.py recover` no longer loops when the final-message path is a symlink to a regular file: presence is one no-follow regular-file test in `recover`, `record-artifact`, `archive-interrupted`, `archive-invalid` and `classify-invalid`, so the symlink counts as missing and `recover` returns `ARCHIVE_INTERRUPTED_REQUIRED`. Before dispatch, anything at the path (dangling symlink or directory included) still blocks with `ARTIFACT_PENDING`.
- `action_journal.py` refuses ticket and action ids that are not safe path components (`TICKET_PATTERN` of `sdd.py`; `..`, `.`, separators, leading dash): `prepare` returns `ACTION_ID_UNSAFE`, history writes `HISTORY_PATH_UNSAFE`, and `recover` on such a journal returns `BLOCKED`/`JOURNAL_INCONSISTENT` with `block`; `archive-blocked` and `rollover` file the evidence under `NO-TICKET/NO-ACTION-<hash>`. A ticket `..` previously wrote one level above the history directory.
- DECISION_DOC: a failed local gate in IMPLEMENT no longer prints a lateral IMPLEMENT → IMPLEMENT transition that spent `stage_transitions` without progress.
- A REVIEW `CHANGES_REQUIRED` reopen no longer hits `STAGE_TRANSITION_BUDGET_REACHED` before DONE.
- Dead ends: `GATE_TIMEOUT` stayed TIMEOUT forever; a missing executor re-printed DISPATCH while the journal stayed PREPARED; enabling or disabling CI after REVIEW looped on `DONE_GATES_NOT_PASSED` (a gate whose GATES.md row changed is now re-run, DONE re-reads the CI policy, and an enabled CI with its run budget spent stops with `CI_RUN_BUDGET_REACHED`); baseline drift, protected-file changes and ownership violations had no command; a DONE demand blocked every new `start`.
- PAUSED mode is honored: `sdd.py next` prints no PREPARE/DISPATCH while paused.
- Controller card (`.hermes.md`) again forbids editing Hermes profile configuration or granting hook consent and states that stage agents never dispatch sub-agents.
- Security: in Obsidian mode `sdd.py` passed the controller container as `--add-dir`, which `codex exec --sandbox workspace-write` treats as writable, so an IMPLEMENT/TEST worker could edit `policies/GATES.md` or STATE and get host command execution through `sdd.py gate`. The container is now passed as `--read-dir`; a writing worker (Codex `workspace-write`, or Claude with write tools) runs with `--config sandbox_workspace_write.writable_roots=[]` and any `--add-dir` outside the repository or under `.hermes` is refused with `ADD_DIR_WRITABLE_REFUSED`.
- Security: `sdd.py start` pins the SHA-256 of `GATES.md` and `EXECUTORS.md` in `delivery.controller_policies` and every `sdd.py gate` re-verifies both, refusing `CONTROLLER_POLICY_CHANGED_DURING_DEMAND` (or `CONTROLLER_POLICY_UNPINNED`) with `confirm-policy --name <policy>` as the next command.
- Security: the worker process gets an allow-listed environment (`PATH`, `HOME`, `LANG`, `LC_*`, `TMPDIR`, `USER`, `SHELL`, `TERM`, … plus its own `ANTHROPIC_*`/`CLAUDE_*` or `OPENAI_*`/`CODEX_*`); `TYPESAFE_*`, `JEV_*`, `HERMES_*` and other `*_API_KEY`/`*_TOKEN` no longer leak to it.
- SIGTERM/SIGHUP to `executor_launch.py run` no longer leaves the journal `DISPATCHED` and the worker alive: the process group is killed (TERM, then KILL), `record-process --finished --exit-code 125` is recorded and `LAUNCHER_INTERRUPTED` is returned with the archive command.
- Security: `{files}` expansion in `sdd.py gate` refuses an agent-owned path with a `-`-prefixed segment (`AGENT_OWNED_PATH_UNSAFE`) instead of passing it as an option, and `validate_protocol` rejects such written paths and editable patterns.
- MANUAL mode: a user "continue"/"pode seguir" now authorizes progress **up to the next HUMAN checkpoint** (the next stop `sdd.py next` ends the turn on), not a single action; `MANUAL_ACTION_COMPLETE` now means "the progress authorized in MANUAL is done; a new 'continue' authorizes the next stretch", no longer "the one requested action ran". This shipped in 14.0.0 without a Breaking note.
- `sdd.py transition --to <current stage>` is refused (`STEP_MISMATCH`; `state_format.apply_transition` raises `STATE_TRANSITION_INVALID`). Gate-failure and REVIEW stops (`FOCUSED_TESTS_FAILED`, `FORMAT_FAILED`, `ANALYZE_FAILED`, `CI_FAILED`, `REVIEW_CHANGES_REQUIRED`, `REVIEW_BLOCKED`) now print `sdd.py reopen --reason … --quote …` instead of `sdd.py transition --to IMPLEMENT …`.
- `sdd.py waive` refuses AGENT (command-verified) checks with `WAIVE_REQUIRES_HUMAN_CHECK` unless `sdd.py request-decision` opened a `HUMAN_DECISION_REQUIRED` stop for the check and `sdd.py answer --check` recorded the user's words; the waiver `--quote` must be that answer. HUMAN checks are unchanged.
- Every `stop_reasons.STOP_REASONS` entry now has a non-null `next_command`; several changed kind or command (`BASELINE_DRIFT_EXTERNAL`, `PREEXISTING_FILE_MODIFIED`, `OWNERSHIP_VIOLATION` → `sdd.py rebaseline`; `GATE_TIMEOUT`, `CI_TIMEOUT` → rerun; `DONE` → `sdd.py close`; `WORKER_BLOCKED`, `NO_NEW_HYPOTHESIS`, `NO_PROGRESS` → `sdd.py unblock`; `SCOPE_CHANGE_REQUIRED` → `sdd.py approve-scope`; `PROMPT_BUDGET_EXCEEDED` → `budget --raise prompt_bytes`; `BUDGET_REACHED` no longer prints a `<budget>` placeholder). Automation matching `next_command: null` must not.
- `sdd.py start` sizes `stage_transitions` as forward transitions + `review_cycles` × the IMPLEMENT→REVIEW re-advance (CODE 11, DECISION_DOC 7, was 7 and 5).
- `policies/EXECUTORS.md` accepts the top-level owner flag `allow_bypass_permissions` (default `false`); `permission_mode: bypassPermissions` without it is refused with `EXECUTOR_POLICY_UNSAFE`. The file documents its trust model (owner-only edits, worker isolation, `setsid` grandchildren escape the process-group kill).
- `executor_launch.py build|run` gain `--read-dir DIR` (read-only directory: `--add-dir` for a read-only Claude worker, dropped for Codex) and `sdd.py` gains `confirm-policy --name gates|executors` (records the user's confirmation of a changed controller policy file, runs no gate; also reachable as `gate --confirm-policy <name>`). `preflight` now requires `--config` in the Codex help.
- `sdd.py reopen` (FIX slice in IMPLEMENT after a failed gate or REVIEW, also inside IMPLEMENT for DECISION_DOC), `request-decision`, `answer --check`, `gate --rerun --quote`, `rebaseline`, `approve-scope`, `close` (DONE → IDLE, summary in `closed_demands`), `pause`/`resume`, `reprepare` (archive an undispatched PREPARED action and refund its executor call), `budget --raise prompt_bytes`. `start` output gains `limits_explained`.
- Stop reasons `LOOP_PAUSED` and `EXECUTOR_UNAVAILABLE`; `sdd.py next` step `REPREPARE`; `stop_reasons.USER_PLACEHOLDERS`.

## 14.0.0 - 2026-10-08

### Breaking
- `bounded_run_driver.py` stop reasons follow `LOOP_POLICY.md` §9 exactly: budget stops use singular names (`EXECUTOR_CALL_BUDGET_REACHED`, `RETRY_BUDGET_REACHED`, `TDD_SLICE_BUDGET_REACHED`, … instead of `EXECUTOR_CALLS_BUDGET_REACHED`, `CORRECTIVE_RETRIES_BUDGET_REACHED`, …), gate failures use the named reasons (`FOCUSED_TESTS_FAILED`, `FORMAT_FAILED`, `ANALYZE_FAILED`, `CI_FAILED`, `CI_TIMEOUT`, `REVIEW_BLOCKED`, `REVIEW_CHANGES_REQUIRED`) instead of `GATE_<NAME>_<VALUE>`, a recovery stop uses `RECOVERY_RECONCILIATION_REQUIRED` (the journal decision moves to `recovery`), and `REPLAN_REQUIRED` reports `terminal_reason: PLAN_COMPLETE` instead of `PROJECTION_COMPLETE`. Automation matching the old literals must switch to the §9 names.
- Sub-agent dispatches (`stage_context.py check --role`) require the manifest's new `dispatch` block (`pending_decision`, `deterministic_attempt`, `if_empty`); a role manifest without it fails with `DISPATCH_QUESTION_REQUIRED`.
- Sub-agents consolidated from 19 to 4 (`project-context-guardian`, `data-flow-tracer`, `pr-reviewer`, `security-reviewer`). Removed briefs: `investigator`, `impact-analyst`, `tdd-implementer`, `test-runner`, `code-reviewer`, `tdd-guardian`, `regression-hunter`, `api-contract-auditor`, `performance-auditor`, `documentation-writer`, `architecture-guardian`, `spec-consistency-guardian`, `release-readiness-auditor`, `dependency-auditor`, `migration-safety-auditor`. Their content moved into project-local playbooks the stage worker loads itself (see `docs/components/sub-agents.md` for the mapping); `code-reviewer`, `tdd-implementer` and `test-runner` duplicated stage briefs and their unique rules moved into `agents/review.md`, `agents/implement.md` and `agents/test.md`. Controllers that dispatched a removed role must load the playbook instead.
- `EXECUTOR_RESULT_SCHEMA.json` and `REVIEW_RESULT_SCHEMA.json`: every acceptance check now requires a `waiver` field (`null` unless `status` is `WAIVED`). Worker results without it are schema-invalid.
- `data-flow-tracer` (`DATA_FLOW_TRACER`) absorbs investigation and impact analysis; its `allowed_stages` and `READ_ONLY_ROLES` entry grow to SPECIFY, CLARIFY, PLAN, TASKS and IMPLEMENT.

### Removed
- The unused `PROJECT_SKILLS` installer constant (project skills are copied with the rest of the template tree).

### Added
- `runtime/sdd.py`, the single deterministic controller entry point: `status` (one compact JSON with storage paths, stage/status/mode/ticket, journal recovery decision, budgets left, `next_action`, `next_command`), `next` (the exact command batch for the current step — recover, manifest, `stage_context.py check`, `prepare`, `executor_launch.py run`, validation, journaled STATE commit, gates, transition — or the stop with its single next command; MANUAL included), `start` (IDLE → SPECIFY with automatic baseline capture; pre-existing dirty files become protected; `--deliverable-kind CODE|DECISION_DOC|BOTH`), `snapshot` (normalized planner snapshot from STATE + journal), `manifest` (complete stage-context manifest with hashes), `transition` (validated, with provenance), `waive` (records a HUMAN-check waiver `{by, reason, quote, recorded_at}` that reaches `validate_protocol` as `recorded_waivers`), plus `prepare`, `accept`, `reject`, `answer`, `unblock`, `budget --raise` and `gate` (including `--not-applicable` confirmation). Every output and error carries `next_step`/`next_command`.
- `runtime/stop_reasons.py`: closed registry of every emitted stop reason with one kind, next step and next command; `LOOP_POLICY.md` §9 is generated from it and `tests/test_stop_reasons.py` enforces both directions.
- `limits.max_prompt_bytes` in `STAGE_CONTEXT_SCHEMA.json` (default 48 KB): `stage_context.py check --prompt-file` refuses a larger prompt with `PROMPT_TOO_LARGE`; `sdd.py prepare` first rebuilds with reduced context.
- `DECISION_DOC` delivery profile (SPECIFY → CLARIFY → PLAN → IMPLEMENT (document) → REVIEW → DONE; TASKS/TEST recorded as skipped with a reason) in `state_format.apply_transition`, `sdd.py` and the planner.
- `state_format.dump`, `apply_transition` and `skipped_stage_names`; `bounded_run_planner.reset_budgets` (`ROLLOVER` resets `used_current_action`, `STAGE_TRANSITION` also `used_current_stage`).
- `stage_context.py check --role/--prompt-file`, `verifier-context --state/--output`; every result carries `next_step`.
- `approvers` JSON block in `PROJECT_SETUP.md` (default: the requester) consumed by `sdd.py waive`.
- `tests/test_controller_e2e.py`: installs the controller with the real installer into a fake vault, puts fake `claude`/`codex` on PATH and drives a demand from `sdd.py start` to DONE executing only printed commands, with one injected executor timeout and one waived HUMAN check; asserts no hand-edited STATE/journal, wiki records and bounded controller output per dispatch.
- `action_journal.py paths [--repo]` prints the canonical `journal`, `history_dir`, `state`, `incidents` and `runtime_dir` of the current worktree in both storage modes (`OBSIDIAN` / `LOCAL`), so the controller never guesses `--history-dir`.
- `action_journal.py archive-blocked --reason <text> --history-dir <dir>` archives a `BLOCKED`, live `INTERRUPTED` or dirty `IDLE` journal to history (reason recorded as an incident), opens a pristine journal and mirrors the action to the wiki: `BLOCKED` is no longer a dead end.
- `retry_mode: ADOPT_PARENT_ARTIFACT`: after `archive-interrupted`, a new action adopts the interrupted parent's own final message, verified by SHA-256 against history, without redispatch (`prepare` → `record-artifact` → normal validation), so nobody hand-edits STATE or the journal. New codes `ADOPTION_NOT_ALLOWED`, `ADOPTION_PARENT_NOT_FOUND`, `ADOPTION_ARTIFACT_MISMATCH`, `ADOPTION_DISPATCH_FORBIDDEN`.
- `recover` decision `ARCHIVE_INTERRUPTED_REQUIRED` (stop reason `EXECUTOR_PROCESS_ENDED_WITHOUT_ARTIFACT`) for a finished process without a final message, e.g. a timeout with exit 124.
- Every `recover` decision and every journal error now carries `next_step` and, when a command applies, an exact `next_command`; every `BLOCKED` carries a `stop_reason`.
- `record-process --started --prompt-sha256 <hash>` dispatch guard: start requires a dispatchable `PREPARED` action (`DISPATCH_NOT_PREPARED`), the prepared prompt (`PROMPT_HASH_MISMATCH`) and a free final-message path (`ARTIFACT_PENDING`).
- `runtime/executor_launch.py` (`schema`, `preflight`, `build`, `run`) is the only way to dispatch a Claude or Codex worker: it derives a transport schema without `$schema` (the Claude CLI rejected the draft 2020-12 reference), refuses unless the journal is `PREPARED` with `DISPATCH_ALLOWED` and a matching prompt hash, runs the CLI in the foreground from the repository with a hard timeout that kills the process group, always records `record-process --finished` (`124` on timeout), extracts `structured_output` (Claude) or the last-message file (Codex) atomically and returns `status`/`next_step`/`next_command`.
- `policies/EXECUTORS.md`: project-owned stage → executor/model/timeout/max-turns map (defaults: Claude for SPECIFY/CLARIFY/PLAN/REVIEW, Codex for TASKS/IMPLEMENT/TEST, 900 s for PLAN/IMPLEMENT, 600 s otherwise). `contracts/EXECUTOR_CONTRACT.md` now documents dispatch for both executors only through the launcher.
- Project-local skills `sdd-product-owner` (scope, observable acceptance, `deliverable_kind` CODE | DECISION_DOC | BOTH with request quotes, traceability, approvals resolved through PROJECT_SETUP `approvers`), `sdd-tech-lead` (declared architecture, dependencies, performance, operability, reversibility), `sdd-api-contracts`, `sdd-tdd` and `sdd-release-readiness`; `sdd-database-design-migrations` gains `references/rollout-safety-audit.md`. Product owner and tech lead are knowledge the worker applies, never approver gates, and never block IMPLEMENT unless the request literally says so.
- Acceptance status `WAIVED` with a required `waiver: {by, reason, quote, recorded_at}` record. `validate_protocol.py` accepts it wherever `PASS` is required (IMPLEMENT current/completed slices, TEST success, read-only carry-forward, REVIEW `APPROVED`); an AGENT check is waivable only with the identical controller-supplied `recorded_waivers` entry (new `--context` key and `recorded_waivers` keyword), and waiver rejections return `next_step`/`next_command`.
- Every stage brief has a `Playbooks` section naming the skills to load; `agents/specify.md` requires `deliverable_kind` and `implementation_in_scope` with request quotes and forbids inventing human approval gates the request does not literally require. `DISPATCH_POLICY.md` routing table gains a playbook column.
- `install_project.py --upgrade` (dry run, report `UPGRADE_READY`) and `--upgrade --apply` (`UPGRADED`, then `ALREADY_CURRENT`) update an existing installation in both storage modes: pristine files per the manifest are replaced, owner edits block (`UPGRADE_CONFLICT` with `MODIFIED_BY_OWNER`/`UNKNOWN_BASELINE`), obsolete pristine files are removed, edited ones are kept with an `OBSOLETE_MODIFIED` warning. Apply holds `.upgrade.lock`, re-plans (`UPGRADE_STATE_CHANGED`), backs up to `upgrade-backups/<UTC>-<from>-to-<to>/` with `BACKUP_MANIFEST.json`, replaces atomically keeping file modes, writes the manifest last and rolls back in reverse (`UPGRADE_ROLLBACK_FAILED`). Preconditions: `UPGRADE_NOT_INSTALLED`, `UPGRADE_DOWNGRADE_REFUSED`, `UPGRADE_CONTROLLER_BUSY`, `UPGRADE_MANIFEST_INVALID`. `--accept-current-as-baseline` adopts the current files of an installation without a manifest.
- A fresh install writes `.hermes/orchestration/INSTALL_MANIFEST.json` (skill version, storage, per-file SHA-256, owner files) in the same transaction; it is never created for an existing installation by a plain rerun.
- Install reports carry `controller_location`, `hidden_controller_note` and, in Obsidian mode, an `obsidian_url` and `warnings` with `CONTAINER_PATH_SHELL_UNSAFE` for non-ASCII or shell-special container paths. Onboarding questions carry `choices` with `none` first.
- `policies/EXECUTORS.md` is an owner file like `GATES.md` (kept once installed, created from the template when absent).

### Changed
- `templates/.hermes.md` rewritten as a ~4.6 KB controller card (the `sdd.py status`/`next` loop, hard rules, where details live); `SKILL.md` procedure points to `sdd.py` instead of reading seven documents up front. Session-start reading load drops from ~45 KB to ~17 KB (see the e2e report and REPORT-tokens).
- `LOOP_POLICY.md` rewritten in English: mode table (MANUAL / LOCAL_DELIVERY / legacy BOUNDED_AUTO / PAUSED with how to resume), budget table with reset rules, generated stop-reason table with one next action each, timeout rule (first `EXECUTOR_TIMEOUT` → archive → one reduced-context FULL_REPLACEMENT retry or cross-executor fallback; second → BLOCKED). Request semantics: "orquestre/implemente <demand>" is a LOCAL_DELIVERY request (one preview, then advance to the next HUMAN checkpoint); in MANUAL, "continue/pode seguir" authorizes progress to the next HUMAN_REQUIRED stop.
- `BOUNDED_AUTOMATION.md`, `BOUNDED_RUN_DRIVER.md` (decision table incl. `REPLAN_REQUIRED`), `DISPATCH_POLICY.md` (dispatch question lives in the manifest; iteration bounded by loop budgets and `correction_loop.py` `NO_PROGRESS` instead of an unimplemented `max_iterations`/evidence digest) and the installed README shortened to their authoritative content; recovery lives only in `ACTION_RECOVERY.md`.
- `bounded_loop_driver.py` is deprecated (`DEPRECATED = True`); `bounded_run_driver.py` is the only bounded-run driver and every driver/planner stop or error carries `next_step`/`next_command`.
- A human-confirmed `NOT_APPLICABLE` format/analyze gate counts as passed in the planner, driver and DONE check.
- Secret redaction moved from `runtime/wiki_journal.py` into its own module `runtime/redaction.py` (`redact` is its only public name; `wiki_journal.redact` still works) with unit tests in `tests/test_redaction.py`. Internal module split, no behavior change: the patterns are byte-identical and the unused `_TOK` pattern was dropped.
- `record-process --finished` is idempotent for the same exit code (safe in a launcher's `finally`), reports `PROCESS_RESULT_CONFLICT` / `PROCESS_NOT_STARTED`, and records a missing result on a started `BLOCKED` action. `mark-validated` re-hashes the final message (`ARTIFACT_CHANGED`, `ARTIFACT_CLASSIFIED_INVALID`). `ACTION_RECOVERY.md` is rewritten as a decision → command table.
- `scripts/install_project.py` (2959 lines) is split into the `scripts/sdd_install/` package (`constants`, `mode`, `errors`, `gitops`, `fsops`, `templates`, `onboarding`, `typesafe`, `exclude`, `local_install`, `obsidian`, `report`, `cli`). The entry point keeps argparse and the interpreter checks; CLI, exit codes and JSON keys are unchanged (verified against golden dry-run/apply reports in both storage modes). The per-run storage globals became one `MODE` object restored by a context manager.

### Fixed
- Planner accepts the journal decision `ARCHIVE_INTERRUPTED_REQUIRED` (plans `RECOVER_PENDING_ACTION`) and refuses an `IDLE` STATE with `IDLE_NO_DEMAND` and the `sdd.py start` command instead of a schema error.
- `context_graph.py` no longer reports installed skills' nested frontmatter (`metadata: hermes:`) as `GRAPH_FRONTMATTER_INVALID`: only notes with a top-level `graph_node` are parsed, and `.hermes`, `.hermes-runtime`, `.obsidian`, `.trash` are skipped.
- `wiki_journal.py` finds the registered workspace in a JSON-dialect STATE, so vault-mode demands driven by `sdd.py` record stage, gate and decision notes.
- Dead references removed: `make ci`, `IMPACT_ANALYSIS` as an action, `RECOVERY_REQUIRED`, `UNKNOWN_BLOCKER`; `test_executor_launch.py` used the removed `CODE_REVIEWER` role.
- The action journal rejected its own Obsidian runtime: `archive-interrupted`, `rollover`, `archive-invalid` and the STATE commit failed with `HISTORY_PATH_UNSAFE` / `STATE_PATH_UNSAFE` because history and STATE live in `<vault>/<project>/.hermes-runtime/<slug>/`, outside the worktree. They now accept this worktree's runtime directory of the binding installed with the controller (never a repository binding or the `HERMES_OBSIDIAN_VAULT` override), walk it with no-follow descriptors from `/`, and keep the legacy in-worktree paths with the symlink-ancestor protection.
- `record-artifact` moved to `ARTIFACT_READY` even when the final message was missing, which then made `archive-interrupted` impossible; it now returns `ARTIFACT_MISSING` and keeps `PROCESS_FINISHED` (a directory or symlink counts as missing).
- `ACTION_RECOVERY.md` named a nonexistent `RECOVERY_REQUIRED` decision for an unknown process result; the decision is `WAIT_OR_MANUAL_REVIEW`, with the exact `record-process --finished` command.
- In `--local-storage` mode, rerunning the installer after configuring `policies/GATES.md` returned `CONFIG_CONFLICT`; the file is now an owner file in both modes and listed under `preserved_owner_files`.
- The Obsidian dry run now refuses a container nested in, or holding, another project container with `WIKI_CONTAINER_NESTED` and a sibling-container `next_step`/`next_command`, instead of returning `READY` and failing later in `wiki_journal`.
- `CONFIG_CONFLICT` reports now point to `--upgrade` in their `next_step`.

## 13.0.9 - 2026-10-08

### Fixed
- `wiki_journal._redact_jwts` docstring described only the round-6 rule (header after `-`); it now states the 13.0.8 behavior (header after `-`, `_` or `=`, empty second segment for `dir` JWE and detached payloads) and the accepted over-redaction of names such as `report.eyJanuary.final.pdf` (pr-reviewer round 9, LOW). Documentation only; no behavior change.

## 13.0.8 - 2026-10-07

### Fixed
- pr-reviewer round 8 on 13.0.7: the JWT pass missed a JWE with alg `dir` (empty encrypted-key segment, `eyJ…..iv.ciphertext.tag`, as in NextAuth session cookies) and a JWS with a detached payload, leaving the IV, ciphertext, tag or signature in clear; a header after `_` (`ACCESS_TOKEN_eyJ…`) and a base64 `=`-padded token also leaked. An empty second segment followed by a real segment is now a token, a header starts at a segment start or after `-`, `_` or `=`, and `=` is part of a token run. Pre-existing gaps, not regressions; still linear.

## 13.0.7 - 2026-10-07

### Fixed
- The pinned Hermes Skills Guard scan rated the skill `DANGEROUS` and failed CI: `tests/test_wiki_journal.py` held a literal private-key header and read `os.environ` directly. The header is now assembled at runtime like the other credential fixtures, and the tests reach the environment through `from os import environ as process_environment`, as `test_hooks.py` already does. Test-only change; the scan is `SAFE` again.

## 13.0.6 - 2026-10-07

### Fixed
- Interrupted round-7 review on 13.0.5: the JWT pattern's 1024-character bound left the signature of large real tokens in clear (an `x5c` certificate header with a payload over 1024 characters; a payload over 8192 characters, already the case in 13.0.4), and a two-segment unsigned token or the last segments of a five-segment JWE were never redacted. JWTs are now found by one linear pass over dotted token runs instead of a regular expression: a header is `eyJ` at the start of a segment or after a `-`, and the header, payload and every following segment (up to five) are replaced, whatever their size.

## 13.0.5 - 2026-10-07

### Fixed
- pr-reviewer round 6 on 13.0.4: a JWT after any hyphen was left in clear (`x-auth-eyJ…`, `session-eyJ…`, `refresh-token-eyJ…`), and after `sk-` only the first segment was masked, leaving payload and signature readable. The JWT pattern now starts after any non-word character and a cheap lookahead requires the first `.` before the expensive scan, so it stays linear (`eyJ-eyJ-…` at 512 KiB: 0.32 s). `sshpass -p` and `--password` are also redacted after `:`, `/`, `.`, `-`, `+`, `<`, `|` and `*`, a gap that predates this branch. Compared with 13.0.1 and 13.0.2 across 450 token/context pairs: no leak and no regression.

## 13.0.4 - 2026-10-07

### Fixed
- pr-reviewer round 5 on 13.0.3: the linear-time rewrite stopped redacting tokens that follow `=`, `/`, `.`, `+` or `-` (`KEY=sk-…`, `export STRIPE=sk_live_…`, `id=AKIA…`, `?jwt=eyJ…`, `https://…/magic/eyJ…`, `?k=AIza…`) and URL passwords after `-`, `.` or `+`; 123 context/token combinations leaked that 13.0.2 redacted. Token patterns now start after any non-word character; only the JWT pattern also refuses to start after `-`, which keeps it linear. `sshpass -p` is also redacted after `(`, `[`, `{`, quotes, `,`, `;` or `=`. New tests put every token after 17 contexts (108 failures on 13.0.3).

## 13.0.3 - 2026-10-07

### Fixed
- pr-reviewer round 4 on 13.0.2: redaction still had quadratic patterns. The URL-password scheme was unbounded (`a-a-a-…`: 128 KiB took 23 s) and the JWT pattern restarted after every `-` (`eyJ-eyJ-…`: 512 KiB took 47 s). Every pattern now has bounded repetitions and starts at a fixed-width token boundary instead of `\b`, and private-key blocks are removed in one linear pass. A new test runs every credential prefix repeated with every separator and fails on any quadratic pattern (it took 23.9 s on 13.0.2).

## 13.0.2 - 2026-10-07

### Fixed
- pr-reviewer round 3 on 13.0.1: the `mysql -pSECRET` pattern added in 13.0.1 scanned quadratically (240 KB took 33 s, so a hook could exceed its 10 s timeout and drop the turn). Every pattern is now bounded and the body is truncated to `MAX_RECORD_BYTES` before redaction; a test bounds the time on adversarial input.
- Redaction also covers URL passwords with an empty user or containing `/`, quoted `mysql -p'…'` and `--password "…"` values, and inline `Cookie:` headers such as `curl -H "Cookie: …"`.

## 13.0.1 - 2026-10-07

### Fixed
- pr-reviewer round 2 on 13.0.0: redaction also covers PGP private-key blocks, URL passwords containing `@`, `curl --user=`/`-uUSER:PW`, `--password`/`--pass`, `sshpass -p`, `mysql -pSECRET`, `glpat-`, `hf_`, `npm_`, `whsec_`, Azure `AccountKey=`/`SharedAccessKey=`/`sig=`, `Cookie:` headers, backslash-escaped JSON keys, and numeric values assigned to password-like keys.
- A session end already logged is skipped (`ALREADY_LOGGED`), so a resumed session finalized again adds no second `log.md` entry; 13.0.0 said "once per session" without enforcing it.
- An executor result under a symlinked system folder (macOS `/tmp`, `/var`) is recorded again; a result file that is itself a symlink is still refused.
- The glossary no longer lists an Obsidian write as a human checkpoint, and the subagent wiki mirror and the `wiki` field of `archive_invalid` are now tested.

## 13.0.0 - 2026-10-07

### Breaking
- `wiki_journal.py record` takes only `--repo`: `--container` is removed, because the controller the module runs from now decides the container. A vault-resident controller writes only into its own container and only for worktrees registered in its runtime whose path is a Git work tree other than `/` or the home folder; a repository-local controller writes only for its own repository. A `.hermes/obsidian.json` planted in a worktree no longer redirects any write (pr-reviewer and security-reviewer round 1 on 12.0.0).
- The session-end recorder hooks `on_session_finalize` instead of `on_session_end`, which Hermes fires after every turn; `hooks.example.yaml` changes accordingly and the end is logged once per session that has a transcript.

### Fixed
- An action record no longer copies an arbitrary file into the synced vault: the executor result is included only for a `VALID` artifact whose file is a single-link regular file reached without symlinks, opened non-blocking (a FIFO no longer hangs the journal), at most 512 KiB, with the journal's SHA-256 and an `executor_result`/`review_result` JSON shape.
- Hook sessions are recorded only when `cwd` or `TERMINAL_CWD` is inside a served worktree; a session in the parent folder, the home folder or elsewhere is never recorded. Repository-local controllers now record turns too.
- Redaction covers private-key blocks, JWTs, quoted JSON/YAML keys and values with spaces, passwords in URLs, `Basic`/`Token` credentials, `github_pat_`, `AIza…`, `sk_live_`/`rk_`/`pk_`, `ASIA…`, Slack webhooks, `curl -u` and "password is …" phrases, and runs in linear time.
- Transcripts continue in `-partN` files past 4 MiB, `log.md` rotates to `log-YYYY.md` after 500 entries, `index.md`/`log.md` updates hold a lock so concurrent writers lose no index line, titles and frontmatter values are single lines so a title cannot forge a log entry, and only allow-listed metadata keys reach the frontmatter.
- `prepare`, `block` and every leaf worker that stops (`subagent_stop`, metadata only) are now recorded in the wiki, and the `wiki` field of rollover and archive reports is covered by tests.
- Documentation that still described the vault as read-only or approval-gated (`SKILL.md`, `harness.md`, the installed READMEs, the vault baseline note) now matches the code.

## 12.0.0 - 2026-10-07

### Breaking
- The Obsidian project wiki is now **read and written**: everything the orchestrator runs for a project is recorded there without approval. `OBSIDIAN_WRITE` moves from `HUMAN_REQUIRED` to `AUTO_SAFE` in `bounded_run_planner.py` (and is not an external mutation), `context_graph.py propose` reports `approval: AUTO_SAFE` (`OBSIDIAN_WRITE_APPROVAL` replaces the `HUMAN_REQUIRED` constant), and `LOOP_POLICY.md` §18, `BOUNDED_AUTOMATION.md`, `.hermes.md` and the `project-context-guardian` brief drop the read-only rule and the `OBSIDIAN WRITE PROPOSAL`. A consumer that waited for human approval before a wiki write no longer gets that stop.

### Added
- `runtime/wiki_journal.py record` writes stage artifacts, gate results, actions and incidents under `raw/articles/<ticket>/`, Hermes turns under `raw/transcripts/sessions/`, and decisions, concepts, entities, comparisons and queries as Layer-2 pages listed in `index.md`; every record appends to `log.md`. `raw/` records are never overwritten, secrets are redacted, writes go through no-follow descriptors anchored at the container, and a vault-resident controller only writes for worktrees registered in its runtime.
- The action journal mirrors every rolled-over, interrupted and invalid action (with the executor's final message) and `record_incident` mirrors every incident into the wiki; their reports gain a `wiki` field, and a wiki failure never fails the journal.
- Opt-in observer hooks `hooks/record-turn.py` (`post_llm_call`) and `hooks/record-session-end.py` (`on_session_end`) record each Hermes turn and session end of a bound worktree; `hooks.example.yaml` lists them.
- `wiki_layout.append_log_entry` appends a `log.md` entry for any action.

## 11.0.2 - 2026-10-07

### Fixed
- `wiki_layout.py migrate` puts a changed source back with an exclusive hard link instead of a rename, so restoring it can never replace a newer save that landed meanwhile; if one did, both versions stay (the older under its private hidden name) and the error says the copy in the wiki is the pre-change version to reconcile by hand (security-reviewer round 3 on #42).
- A path longer than `PATH_MAX` no longer crashes `init`, `migrate` or `check` with a traceback: inspecting the container and any OS error reaching the CLI return `WIKI_PATH_UNSAFE` as JSON.
- A container already marked (`SCHEMA.md` with `layout_version`, or a binding) may group its demands under any folder (`Archive/`, `_Completed/`, `Sprint 12/`); only an unmarked folder is checked for unmarked child projects, and that refusal now explains how to mark a single project.

## 11.0.1 - 2026-10-07

### Fixed
- `wiki_layout.py migrate` no longer deletes a concurrent save (security-reviewer round 2 on #42). Instead of unlinking the source by name after the last check, it renames it to a private hidden name, compares that exact entry with the open descriptor and unlinks only that file; a file saved over the name in the meantime is never renamed or is put back, and the run stops with `WIKI_SOURCE_CHANGED`. The same applies to deduplicated sources.
- `index.md` and `log.md` are decoded before the first move (a non-UTF-8 file is `WIKI_PATH_UNSAFE`), the index temporary name is random, and a failure while pruning or updating `index.md`/`log.md` after every move is logged and returned as `WIKI_MIGRATION_INCOMPLETE` instead of a traceback.
- A folder whose children are unmarked projects (a child with controller state, or with demand folders that hold `sessions/`, `decisions/`…) is refused with `WIKI_CONTAINER_NESTED`; a single project whose demand folders hold their own legacy folders is still accepted. An unreadable directory is a `WIKI_MIGRATION_CONFLICT`, an OS error while planning is `WIKI_PATH_UNSAFE`, legacy folder names match in any Unicode normalization, and `WIKI_CONTAINER_INVALID` names the real path. The installer closes the directory iterator it opens for keep-files.

## 11.0.0 - 2026-10-07

### Breaking
- `wiki_layout.py migrate --apply` no longer initializes a bare folder: it requires a container already marked by `init --apply` or the installer (`SCHEMA.md` or a binding) and otherwise stops with `WIKI_INIT_REQUIRED`; the dry run reports `init_required`. It also refuses a vault root (`WIKI_CONTAINER_IS_VAULT`), a folder holding other project containers (`WIKI_CONTAINER_NESTED`), a Git work tree (`WIKI_CONTAINER_IS_REPOSITORY`) and a container reached through a symlink, so a scripted `migrate --apply` on such a path must point at one project folder and run `init --apply` first. Index lines for migrated pages are now path links (`[[concepts/a/plan|plan]]`) instead of bare names.

### Fixed
- `wiki_layout.py migrate` could lose or leak notes (security-reviewer and pr-reviewer on #42). It now refuses, before the first move, any destination whose ancestor is a symlink or not a directory, a destination that is the source itself or a hard link to it, destinations that differ only by case or Unicode normalization, and a destination that another move also needs as a directory. Each move opens the source and destination through no-follow descriptors anchored at the container, requires the source to be the exact file the plan hashed (same device, inode, size and mtime), copies and verifies the SHA-256, preserves the modification time and only then unlinks the source; a deduplicated source is removed only after both copies are re-hashed. A changed source stops the run with `WIKI_SOURCE_CHANGED` inside `WIKI_MIGRATION_INCOMPLETE`, and every interruption is logged. `index.md` and `log.md` are written only as single-link regular files (`WIKI_PATH_UNSAFE`), and the index now links pages by path (`[[concepts/a/plan|plan]]`) so pages sharing a name stay distinct.
- `wiki_layout.py` no longer accepts a directory outside a vault because it carries a planted `.hermes/obsidian.json`. Hidden entries stay in place at every depth and are listed in `kept_hidden`; only a Finder `.DS_Store` is removed, and only to empty a folder the run emptied. PDFs and media go to `raw/papers/` and `raw/assets/` even inside legacy folders, and non-note files in folders that map to `concepts/` or `queries/` stay raw sources.
- The installer reports an unsafe wiki path (a file where a wiki directory belongs, a symlinked wiki directory or skeleton file) as `WIKI_PATH_UNSAFE` instead of a raw OS error, and an Obsidian container that still has a root `skills-lock.json` as `TYPESAFE_LOCK_LEGACY_LOCATION`.
- Upgrade path from 9.x Obsidian storage, which the 10.0.0 note left incomplete: (1) copy the new controller templates into `<container>/.hermes/` by hand, because an existing installation is never upgraded automatically and a changed managed file otherwise returns `CONFIG_CONFLICT`; (2) run `wiki_layout.py init --apply`; (3) run `wiki_layout.py migrate` without `--apply` and review the plan — it moves every legacy note, not only the TypeSafe lock; (4) run `migrate --apply`; (5) rerun the installer.

## 10.0.0 - 2026-10-06

### Breaking
- In Obsidian storage the TypeSafe lock moves from the container root to `.hermes/skills-lock.json`, so the wiki root holds only notes. An existing Obsidian install with TypeSafe keeps its lock at the root, where the installer no longer looks; run `wiki_layout.py migrate --apply` on the container (it moves a root `skills-lock.json` into `.hermes/`) before reinstalling or upgrading.

### Added
- Each Obsidian project container is now laid out as a Karpathy LLM Wiki (the bundled `llm-wiki` skill's layout): `SCHEMA.md`, `index.md`, `log.md`, `raw/{articles,papers,transcripts,assets}/`, `entities/`, `concepts/`, `comparisons/` and `queries/`, with the orchestrator hidden under `.hermes/` and `.hermes-runtime/`. The installer creates the missing skeleton and never overwrites a wiki file. New `runtime/wiki_layout.py` (`init`, `migrate`, `check`, dry run unless `--apply`) migrates legacy project folders (`sessions/`, `decisions/`, `boards/`, `_Discovery/`, demand folders…) with copy → SHA-256 verify → remove, refusing every conflict before the first move and recording the moves in `log.md` and `index.md`. Folder index notes (`README.md`, `_INDEX.md`) inside legacy folders stay raw sources instead of becoming wiki pages.

### Changed
- The `project-context-guardian` brief now proposes notes in the wiki layout (raw sources are immutable; pages go to `entities/`, `concepts/`, `comparisons/` or `queries/` with `index.md` and `log.md` updates) instead of free-standing `Decisions/`, `Modules/` and `Specs/` folders.

## 9.0.3 - 2026-10-06

### Fixed
- The installed controller suite now passes when run from an Obsidian project container, the default storage since 8.0.0. Six tests in `test_hooks.py`, `test_obsidian_binding.py` and `test_context_graph.py` picked up the real container binding of the controller they ran from and looked for runtime files in the vault; they now isolate themselves like a source checkout, and the vault-resident tests patch over that. A new installer test installs into an Obsidian container with automatic Jev consent and runs the installed suite, which CI previously exercised only with `--local-storage`. Test-only change: no runtime behavior differs.

## 9.0.2 - 2026-10-06

### Fixed
- The 9.x line now includes the 8.0.1–8.0.3 installer and hook fixes (vault/repository overlap refusal, rollback on repository drift including the TypeSafe integration, identity-based overlap checks, owner-only `GATES.md` adoption, and hooks that trust only the container binding). The untagged 9.0.0 and 9.0.1 release commits predate those fixes; this release is the first 9.x tree that contains both the Jev dispatch gate and them.

## 9.0.1 - 2026-10-06

### Fixed
- The Jev dispatch gate no longer creates the governor cache, its lock or their parent directories: `verify_cached_decision` returns `JEV_GOVERNANCE_RECORD_UNVERIFIED` when either is absent, so `stage_context.py` stays read-only, including inside the Obsidian container. `consent_state` spells out the legacy install-only check instead of relying on operator precedence. The gate tests that need the POSIX-only cache are skipped on other platforms. Migration note for 9.0.0: an older `PROJECT_SETUP.md` with no `typesafe_ai` answer also fails closed at PLAN and IMPLEMENT; record `typesafe_ai: none` to opt out. The hooks page now lists the setup and platform refusals, and the harness page states that in-flight tombstones pass only as `REVIEW` with a human resolution.

## 9.0.0 - 2026-10-06

### Breaking
- Automatic Jev governance is now enforced at dispatch instead of being only an instruction. Projects installed with `--automatic-jev-governance` must add a `semantic_governance` record to PLAN and IMPLEMENT manifests that v8.0.0 accepted; a missing `PROJECT_SETUP.md` now fails closed at those stages. With `automatic_semantic_governance: true` in `PROJECT_SETUP.md`, `stage_context.py check` (and the hooks that call it) refuses PLAN and IMPLEMENT manifests that lack the new optional `semantic_governance` record (`JEV_GOVERNANCE_RECORD_REQUIRED`), cite a fingerprint that is not a structurally valid `LIVE_JEV` governor report for the same ticket in the Jev cache (`JEV_GOVERNANCE_RECORD_UNVERIFIED`), or leave a `REVIEW` outcome without a human `review_resolution` (`JEV_GOVERNANCE_REVIEW_UNRESOLVED`). The gate fails closed: only an explicit `none`, `UNRESOLVED`, `false` or legacy install-only answer disables it, while a missing or unreadable setup gives `JEV_GOVERNANCE_SETUP_INVALID` and consent on a non-POSIX platform gives `JEV_GOVERNANCE_PLATFORM_UNSUPPORTED`. `stage_context.py` gains `--project-setup` and `--jev-cache` (made absolute without resolving symlinks) and loads the governor by path. `semantic_governor.py` reports now record the request `ticket`; a report cached before that gets it written back on its next cache hit without a new paid call. The governor adds the public `consent_state` and `verify_cached_decision` helpers. The record is excluded from the slice hash, so approvals stay valid. The installed test suites isolate the gate from the project's own `PROJECT_SETUP.md`, so they pass in consented projects too.

### Changed
- `AGENTS.md` requires agents changing this repository to route semantic classifications (risk, branch type, changelog level, specialist reviewers) through `semantic_governor.py decide` with inputs in the ignored `.hermes-dev/`, stop when preflight is not `READY`, hand `REVIEW` results to the owner, and announce each use with a `JEV USADO` block.

## 8.0.3 - 2026-10-06

### Fixed
- With a vault-resident controller, hooks now use only the container binding everywhere: the IMPLEMENT vault write check and the transient `STAGE_CONTEXT.json`, `HOOK_BINDING.json` and `VERIFICATION_EVIDENCE.json` (always read from the worktree runtime in the vault) no longer follow a binding or `.hermes` tree inside the repository. In 8.0.2 a repository-local `.hermes/obsidian.json` could still redefine the container for the write check and allow IMPLEMENT writes outside both the repository and the real container. `obsidian_binding.binding_path` keeps its repository-first precedence for operator-invoked CLIs, now documented.

## 8.0.2 - 2026-10-06

### Fixed
- The installer's overlap check compares device/inode identities instead of path spellings, so a case variant on a case-insensitive filesystem can no longer place the vault inside the repository's Git directory, and it also protects linked worktrees and superprojects of the target. Repository drift during the TypeSafe integration now rolls the integration back too (`PROJECT_SETUP.md` restored, skill and lock removed), for fresh and existing containers. `policies/GATES.md` is adopted only in a container this installer already set up (binding and `PROJECT_SETUP.md` present); a fresh container with a foreign `GATES.md` returns `CONFIG_CONFLICT`, and an adopted one is listed under the new report field `preserved_owner_files` with its SHA-256. The per-run storage policy is restored after each run. A `TARGET_WORKTREE_CHANGED` detected after the apply transaction committed reports the drift but does not undo the committed vault files. With a vault-resident controller, hooks use only the container binding (a binding inside the repository can no longer redirect state) and accept only a worktree whose runtime `STATE.md` the installer created.

## 8.0.1 - 2026-10-06

### Fixed
- The Obsidian installer refuses a vault or project container that overlaps the target worktree or its Git directory (`OBSIDIAN_VAULT_OVERLAPS_TARGET`) before writing, and checks `TARGET_WORKTREE_CHANGED` inside the apply transaction so a changed repository rolls back every vault path the run created; before, an overlapping vault left files in the repository. In the vault, tracked-path checks are skipped (decided with `git rev-parse`, not localized stderr), so a Git-tracked vault no longer blocks a TypeSafe reinstall; `policies/GATES.md` is created when absent and then left to the owner, so a second worktree can share a container after the gates are configured; the dry run no longer reports `planned_env_action: CREATE` in Obsidian mode. Storage policy is set per run instead of by a global mutated mid-function. `SKILL.md`, `BOOTSTRAP.md`, the hooks example and the hooks page now describe container-relative paths. Migrating an existing `--local-storage` install: run the installer with the Obsidian flags into a new container, then move open-demand runtime with `migrate_to_vault.py`.

## 8.0.0 - 2026-10-06

### Breaking
- The project installer now stores **everything in the Obsidian project container** and writes nothing to the user's repository: no `.hermes/`, `.hermes.md`, `skills-lock.json`, `.env`, bytecode or `.git/info/exclude` edits. `install_project.py` gains `--obsidian-vault` and `--obsidian-project`, which are required by default (`OBSIDIAN_BINDING_REQUIRED` otherwise), and writes the controller, `PROJECT_SETUP.md` (with the `obsidian` answer pre-resolved), playbooks, `.hermes/obsidian.json` and the per-worktree runtime under `<vault>/<project>/`. The report adds `storage`, `vault`, `project_container`, `worktree_runtime` and `target_writes: []`, and the run fails with `TARGET_WORKTREE_CHANGED` if the target's Git status or Hermes paths moved. The old in-repository layout remains available with `--local-storage` (`STORAGE_MODE_CONFLICT` when combined with the Obsidian flags). In Obsidian mode TypeSafe creates no credential file; keys come from the process environment.

### Changed
- `obsidian_binding.binding_path` falls back to the container binding of the controller it runs from when the repository has none, and `load_path` loads an explicit binding. Hooks accept a vault-resident controller for a clean repository and default their transient files to the worktree runtime in the vault; `stage_context.py` verifies playbook bytes from the container's `.hermes/skills/` while still requiring `project_root` to be the live Git workspace.

## 7.0.0 - 2026-10-05

### Breaking
- Automatic Jev governance now requires a new explicit consent bit at the actual connector boundary: TypeSafe installation records `automatic_semantic_governance:false`, `--automatic-jev-governance` records `true`, and false, missing or legacy `{"install":true}` answers block before request/cache reads or network access. `semantic_governor.py decide` gains `--project-setup` (defaulting to the local orchestration record), rechecks exact consent plus local READY preflight immediately before evaluation, and fails closed before state/network access on Windows because junction-safe held-handle traversal is not implemented.

### Added
- `semantic_governor.py decide` batches non-deterministic classifications into one Jev call after deterministic precedence, accepts confidence `0.70` inclusively, and routes uncertainty to `REVIEW`. Consent and local READY preflight complete before durable state; a proven process-construction failure safely removes the tombstone, while every failure after process start retains retry suppression as potentially billed. The bounded request is streamed over stdin, eliminating a mutable request pathname; `typesafe_connector.py evaluate` adds `--input-stdin` while preserving `--input`. The guarded POSIX cache retains one parent descriptor across lock/read/write, preserves insertion chronology, and evicts truly oldest entries to count/size limits. A post-replacement directory-sync failure is safe because the already-synced final file is visible and a crash can only retain it or the prior durable tombstone; pre-replacement failures still return `REVIEW`. Question IDs, remote model names, dashboard fields and fallback phases must be bounded printable text before terminal rendering.
- A repository-local Hermes CLI progress dashboard exposes provider, current and remaining SDD stages, elapsed time per stage, recent bounded activity, and Jev's live provider/model/classification area. POSIX no-follow ancestor traversal and a held owner-private lock serialize complete read-modify-write operations; Windows fails closed rather than claiming junction-safe handling. The display remains separate from authoritative STATE/journal data and closes successful runs as `DONE (8/8)`.

## 6.9.5 - 2026-10-03

### Fixed
- The context-graph `APPEND` size gate reads the existing note's recorded size defensively, so a graph a caller assembled itself (rather than through `build`, which always records it) gets the normal refusal path instead of an uncaught `KeyError`. An absent size counts as zero, which only ever makes the gate more permissive for a record that never came from a note.

## 6.9.4 - 2026-10-03

### Fixed
- The context-graph note-size gate now covers `APPEND`, not only `CREATE`: the limit applies to the resulting note, so a 1 027-byte fragment appended to a 261 977-byte note is refused with the existing and added sizes instead of being accepted and making the node disappear from the graph. `build` records each node's `note_bytes` so a proposal knows the room left. A fitting append is still accepted and the merged note still loads clean.

## 6.9.3 - 2026-10-03

### Fixed
- An accepted `CREATE` context-graph proposal must now also be *loadable*, not merely reparseable: the rendered note is refused as `GRAPH_PROPOSAL_INVALID` when it exceeds `MAX_NOTE_BYTES`, the same bound the read path already enforced, because approving a larger note produced a note that loaded as nothing and reported the failure against the note instead of the proposal. The exact boundary size is accepted and loads clean.
- A `GRAPH_PROPOSAL_INVALID` round-trip refusal excerpts both readings to `MAX_DETAIL_EXCERPT` instead of embedding the full field value twice: a 200 000-character reason produced a 400 101-character detail inside a dispatch manifest, reintroducing the unbounded detail the cycle finding had just been fixed to avoid. The detail is now constant-size (375 characters for the same input).

## 6.9.2 - 2026-10-03

### Fixed
- A `CREATE` context-graph proposal is now verified by round trip: `propose` parses its own rendered content back with `parse_frontmatter` and refuses a mismatch as `GRAPH_PROPOSAL_INVALID`, naming the field and both readings. Enumerating forbidden spellings had already missed `\n`, then the rest of `LINE_BREAKS`, then a reason like `[deferred]` that a human reads as text and the parser reads as a one-item list — which would have made the approved note load as a different node, or vanish from the graph as `GRAPH_FIELD_INVALID`. The guarantee is the round trip, so no accepted proposal can read back as something other than what the report described; verified over every Unicode code point and 8799 accepted prefix/infix/suffix shapes, and end to end by loading an accepted proposal as a note. The `LINE_BREAKS` and single-trimmed-line rules stay because they name the common mistake precisely.
- Corrected the 6.9.0 entry's claim that a cyclic group is named "with all of its members": since the same release it names at most `MAX_NAMED_CYCLE_MEMBERS` plus a count.

## 6.9.1 - 2026-10-03

### Fixed
- Context-graph hardening from review: a symlinked graph root now reports the containment failure `GRAPH_ROOT_UNSAFE` instead of the misleading `GRAPH_SOURCE_UNAVAILABLE`, because containment is checked before existence. A decision proposal's reason is checked against `LINE_BREAKS`, the parser's own line definition (U+2028, U+2029, U+0085, `\v`, `\f` and the file separators, not only `\n`), so rendered `CREATE` content always reparses through `parse_frontmatter`. A `GRAPH_DEPENDENCY_CYCLE` finding names at most `MAX_NAMED_CYCLE_MEMBERS` members plus a count, so a large cyclic group cannot put a 450 000-character detail into a dispatch manifest. A proposal note path must also be literal: glob characters are refused, because a note path is one file and never a pattern.

## 6.9.0 - 2026-10-02

### Added
- Optional project **context graph**: `runtime/context_graph.py` reads modules, rules, tests, decisions and docs as connected Markdown notes with declarative frontmatter, either from the bound Obsidian project container or from a repository-local directory, with no graph database. `validate` reports stable findings (`GRAPH_*`) for malformed frontmatter, invalid ids/kinds/fields, duplicate nodes, unsafe `code_paths`, dangling or wrongly typed relations, `depends_on` cycles and decisions without a recorded reason or ISO date. Cycles are found as strongly connected components with an iterative pass, so a cycle reachable only through an already-finished node is still reported, each cyclic group is named once, and a long dependency chain cannot exhaust the stack. A repository-local `--root` goes through the same canonical repository-relative path rule as a slice's editable paths: an absolute, escaping, non-canonical or symlink-traversing root is refused as `GRAPH_ROOT_UNSAFE` before any note is read, so the graph cannot be pointed at notes outside the project. `query --node/--path --depth` resolves a repository path to the module that declares it, follows the typed relations outward to a bounded depth, and returns the nodes by kind with the edges that justify them, each in-scope decision with its reason and date, unresolved selectors and selected modules with no test. `propose` renders the note content for a new decision or an appended outcome and writes nothing: the report always carries `written: false`, `action: OBSIDIAN_WRITE` and `approval: HUMAN_REQUIRED`; its note path uses the same path rule (backslash spellings included) and a decision reason must be a single trimmed line so rendered content always reparses. Notes are read through no-follow traversal and refuse symlinks, non-UTF-8 content, special files and notes over 256 KiB; `obsidian_connector.py` gains the read-only `project_notes` enumeration the vault-backed graph reads through.
- The stage context manifest accepts an optional `context_graph` record (`status`, `source`, `selectors`, `nodes`, `decisions`, `unresolved`, `findings`). A project without a graph omits it and is never blocked. When present, `stage_context.py check` requires that it carry no unresolved findings (`CONTEXT_GRAPH_FINDINGS_PRESENT`), that PLAN and IMPLEMENT record what was queried and what came back unless the status is `MISSING` (`CONTEXT_GRAPH_SELECTION_REQUIRED`), that a `PARTIAL` graph name its unresolved selectors while no other status leaves one (`CONTEXT_GRAPH_GAPS_REQUIRED`, `CONTEXT_GRAPH_STATUS_INCONSISTENT`), that an `OBSIDIAN` source pair with a `BOUND` vault, that `NOT_CONFIGURED` carry no content, and that every `DECISION` node in scope carry its reason and date (`CONTEXT_GRAPH_DECISION_UNRECORDED`). The record is excluded from the slice hash, so refreshing the graph never invalidates an approval.

## 6.8.0 - 2026-10-01

### Added
- The TypeSafe/Jev connector validates every `questions` entry against the documented Question contract before sending a request: `type` must be `noul`, `choice` or `score`, `instructions` must be a non-blank string or non-empty object/array, `choice` requires a non-empty map of at most 255 options to a text, structured object/array or `null` description, and `score` an ordered array of 2 to 10 level descriptions that are text or structured objects/arrays, while `noul` `criteria` stay optional and unknown question fields are forwarded untouched. A violation exits 2 as `TYPESAFE_QUESTION_INVALID: <question id>` with no network call and no billing, naming the offending question (printable IDs up to 64 characters) so a contract error is diagnosable without the remote 422 body, which continues to be discarded unread. Documented that `evaluate --input` rejects any path with a symlinked component, so macOS `/tmp` must be given as a canonical path.

## 6.7.0 - 2026-10-01

### Added
- The TypeSafe/Jev connector gains `--provider jev-ai`, which pairs `JEV_AI_API_KEY` exclusively with the independent Jev AI compatible endpoint `https://jev-ai.pro/api` (no fallback to TypeSafe), plus a `models` command for an authenticated `GET /v1/models` lookup without inference. `preflight` reports the resolved Jev destination; `evaluate` enforces Jev's 256000-byte/64-question/64-character-ID limits locally, reports `X-Jev-*` billing headers, maps 402/404/502/503/504 to stable reasons, surfaces an ASCII-digit `Retry-After`, and marks timeouts, lost connections, 504 and malformed success bodies (keeping billing headers) as an uncertain outcome without retrying. The default `typesafe` provider's request headers and error reasons are unchanged.

## 6.6.0 - 2026-09-30

### Added
- A repository-local frontend engineering skill guides React, Next.js, component composition, accessibility, design-token reuse and Vercel-oriented performance work. It provides progressive references distilled from the reviewed Pedro Nauck skills snapshot, retaining project code, installed framework versions and accepted SDD decisions as the authority.

## 6.5.0 - 2026-09-30

### Changed
- The project installer now reports `APPLIED` after a successful write, exposes `exclude_update_planned`, requires `jsonschema` up front, and returns stable actionable diagnostics for detached HEAD.

### Fixed
- Harden base and combined TypeSafe installation against real filesystem races and partial success: descriptor-anchored nonblocking no-follow reads reject FIFOs and special files, exclusive mode-controlled writes run under the Git index lock, failed applies restore exclusion bytes and remove still-owned paths, TypeSafe rollback preserves concurrent skill/lock replacements by inode, and Git exclusions are binary-preserving, symlink-safe, repairable, root-anchored per owned file, and no longer hide unrelated orchestration content.
- Restore concurrent rollback replacements portably: regular files use atomic no-overwrite hard links, non-empty parents remain in place, and expected bytes prevent immediate Linux inode reuse from authorizing deletion.
- Track installer-created TypeSafe credential placeholders by identity/content and remove them if any later integration verification fails.
- Route integration-only TypeSafe mutations through the Git-index-locked transaction so a concurrent `git add` cannot turn an approved untracked destination into a tracked overwrite.

## 6.4.0 - 2026-09-30

### Changed
- TypeSafe opt-in now places the private credential file and its example directly under `.hermes/` as `.hermes/.env` and `.hermes/.env.example`; the connector default and installer exclusions follow the same project-local path.

## 6.3.1 - 2026-09-29

### Fixed
- TypeSafe connector tests now use non-sensitive fixture values and replace only the connector module's environment mapping, preserving coverage without triggering Hermes skill-security credential/exfiltration heuristics.

## 6.3.0 - 2026-09-29

### Added
- Optional TypeSafe/Jev runtime connector: explicit stdlib-only System One evaluation against the fixed non-redirecting TypeSafe endpoint with `jev-latest` by default, descriptor-anchored no-follow credential reads, strict finite/depth-safe JSON and bounded/truncated-response checks, validated timeouts and stable secret-safe error reports. TypeSafe opt-in now creates or repairs an owner-only ignored `.hermes/orchestration/.env` placeholder through verified directory descriptors without overwriting existing credentials, fails closed on unsafe files and ships `.env.example`.

## 6.2.2 - 2026-09-29

### Fixed
- Note-path validation now rejects embedded NUL before direct filesystem access or CLI search-result verification, preserving structured failure instead of exposing a raw `ValueError`.

## 6.2.1 - 2026-09-29

### Fixed
- The guarded Obsidian connector now applies one Markdown-only note policy to direct reads and CLI search results, rejecting non-note files such as `.env` and returning the canonical container-relative spelling of every accepted note path.

## 6.2.0 - 2026-09-29

### Added
- A stdlib-only, repository-local Obsidian connector uses the official CLI for read-only vault discovery, exact bound-vault preflight and project-scoped search, with root-anchored no-follow filesystem note reads/search/tag fallback when Obsidian is unavailable. It requires canonical symlink-free vault/container binding paths, returns only container-relative note paths, rejects runtime/control paths and invalid CLI input/output, refuses lexical CLI opening, exposes no write command, preserves `.hermes/obsidian.json` as the binding authority and leaves every mutation under `OBSIDIAN_WRITE`, `vault_guard` and baseline control. Project onboarding documents CLI discovery/preflight, while Obsidian Headless Sync remains optional deployment plumbing rather than a controller transport.

## 6.1.2 - 2026-09-29

### Fixed
- The project installer now reports stable, actionable `BLOCKED` diagnostics before writing when Python 3.10+ is unavailable, the target is not a Git repository, or the repository has no initial commit, instead of continuing under an unsupported interpreter or exposing raw Git errors.

## 6.1.1 - 2026-09-29

### Fixed
- Hermes Skills Hub installation no longer receives a blocking `DANGEROUS` verdict from defensive test fixtures or negative controller prose. Hook execution now projects only supported SDD path overrides from process state, traversal and non-disclosure tests preserve their invariants with synthetic fixtures, and CI enforces a `SAFE` verdict by executing scanner bytes verified against a pinned real Hermes Git blob.

## 6.1.0 - 2026-09-29

### Added
- Optional repository-local TypeSafe onboarding for Jev workflows: the installer detects an existing `typesafe-ai` skill, previews an explicit install or opt-out, verifies and copies a reviewed snapshot from a pinned TypeSafe commit without executing `npx` or downloading code, preserves unrelated lock data, commits skill/lock/onboarding transactionally, and fails closed on provenance, conflict or filesystem errors without modifying global Hermes configuration.

## 6.0.0 - 2026-09-29

### Added
- Three trusted project-local engineering skills under `.hermes/skills/` provide progressive, stack-neutral playbooks for backend services, architecture/DDD decisions, and database design/migrations without duplicating specialist reviewers. A new read-only `migration-safety-auditor` covers concrete rollout order, mixed-version compatibility, data preservation, locks, restartability and recovery.

### Breaking
- Stage-context schema 2 replaces schema 1. Controllers must add canonical absolute `project_root`, top-level `playbooks` and slice `required_playbooks`; old per-dispatch manifests are ephemeral and must be regenerated rather than reused. The runtime binds the root to the live canonical Git workspace, then binds the exact project-local `SKILL.md` and loaded reference bytes to implementation slices by name/version/path/hash/reason descriptors. Validation rejects root or descendant symlinks/escapes, mismatched bytes/frontmatter, duplicate or unsafe descriptors, unknown slice bindings, and extra or missing playbooks for the current IMPLEMENT slice; canonicalized loaded guidance participates in the approved slice hash, and invalid manifests never report approval reuse. PLAN, TASKS and IMPLEMENT briefs, installation docs and tests describe the new contract. Installation adds exclusions only for the three bundled skill directories—never a broad `.hermes/skills` rule—while preserving pre-existing user exclusions, and still modifies neither Hermes profiles, global skills, SOUL, repository trust nor hook consent.

## 5.1.0 - 2026-09-28

### Added
- A repository-local `orchestration/hooks/` layer with opt-in Hermes shell hooks for exception-safe fail-closed, live-bound slice/vault file-tool scope, evidence-gated completion, bounded secret-safe STATE context, and immutable non-sensitive sub-agent audit events. Context and evidence are bound to workspace, HEAD, ticket, stage, slice, action and attempt; authoritative STATE/journal/history resolve repository-locally or through the bound per-worktree vault runtime. Wrong-shaped STATE never echoes parser details or values. Installation copies the hooks and quoted absolute-path example configuration without changing a Hermes profile, SOUL, global skills, or consent.

### Fixed
- Hook failure responses never echo malformed or non-UTF-8 STATE content, boolean budget values are rejected as wrong-shaped, quoted absolute hook commands support checkout paths containing spaces, and the architecture guide now records the hook layer's responsibility and dependency direction.

## 5.0.0 - 2026-09-28

### Breaking
- `validate_protocol.py` enforces write scope and cited evidence: SPECIFY, CLARIFY, PLAN, TASKS and TEST reject any reported file change; every IMPLEMENT status requires anchored, non-match-all `editable_paths`, matched segment by segment (`*` never crosses `/`); an `AGENT` `PASS` for the current IMPLEMENT slice or in TEST must cite, in backticks, one of the commands the controller bound to that check (`check_verifiers`) recorded as exit 0 with `PASS`; optional `required_commands` must all be recorded as passing. Controllers must now pass `editable_paths` for IMPLEMENT and `check_verifiers` for IMPLEMENT/TEST, and validate read-only IMPLEMENT results (`project-context-guardian`, `data-flow-tracer`) with `role` (`stage_context.py verifier-context --role`). Patterns and written paths must be canonical (no `.`, `..` or empty segments). Malformed `--context` values are rejected with exit 2, and IMPLEMENT's `required_commands` covers only the current slice.

### Added
- `runtime/stage_context.py` and `schemas/STAGE_CONTEXT_SCHEMA.json`: a pure per-dispatch check of the context budget (scoped excerpts, no whole documents), the `project-context-guardian` result required before PLAN and IMPLEMENT, code-authoritative divergence records, and the slice contract (one current slice, safe editable paths, an observable verifier for every check, and per check at least one bound verifier that predates the slice), and it refuses STATE/journal files as sources. `slice_sha256` hashes one slice's contract without the stage or the completed-slice cursor. The per-slice hashes stored at PLAN/TASKS approval (`approved_slice_sha256s`) are reused by every later IMPLEMENT dispatch (`APPROVAL_REUSED`); TEST/REVIEW report `APPROVAL_NOT_APPLICABLE`; a changed slice yields `SCOPE_CHANGE_REQUIRED`. `verifier-context` feeds `validate_protocol.py --context`.
- `runtime/correction_loop.py` and `schemas/CORRECTION_LOOP_SCHEMA.json`: a pure bounded execute → verify → correct decision with configurable attempt, executor-call and cost limits. It stops auditably with `stop_reason`, evidence and `next_step` on `RETRY_BUDGET_REACHED`, `EXECUTOR_CALL_BUDGET_REACHED`, `COST_BUDGET_REACHED`, `NO_NEW_HYPOTHESIS` or `NO_PROGRESS`, and refuses unjustified escalation.
- `validate_protocol.py` gains a CLI (`--action`, `--result`, `--context`, `--json`).
- `docs/components/harness.md` documents both tools; `LOOP_POLICY.md` §25 adds the harness procedure and three stop reasons to the closed list.

### Changed
- `project-context-guardian` is allowed in IMPLEMENT and returns a structured context status, gaps and divergences. During stages it only proposes vault updates; the proposal becomes an approved `OBSIDIAN WRITE PROPOSAL` after REVIEW/DONE. This fixes a contradiction with `LOOP_POLICY.md` §18, which already made Obsidian read-only during stages.
- The PLAN, TASKS, IMPLEMENT and TEST briefs, `EXECUTOR_CONTRACT.md`, `.hermes.md`, `SKILL.md` and the installed README describe the harness steps.

## 4.0.0 - 2026-09-28

### Breaking
- Executor and review result contracts now use schema version 3. Executor results must carry evidence-backed `context_assessment` and stable `acceptance_checks`; the controller owns every check's ID, criterion, verification method, verifier and non-whitespace slice assignment, preventing worker downgrades or unassigned criteria. Early stages cannot claim executed evidence, TASKS assigns a non-empty criterion set, and IMPLEMENT receives exactly one current plus an explicit disjoint completed set. TEST and every REVIEW status must match the full non-empty authoritative mapping with a complete non-empty check set independently of payload-declared values and green gates.

## 3.10.0 - 2026-09-28

### Added
- Project-local orchestrator onboarding: installation creates `PROJECT_SETUP.md`, reports only unresolved questions for issue-tracker access, optional Obsidian binding and other project tools, validates the required connectivity and permission fields fail-closed, and blocks the first demand until explicit answers (including `none`) are recorded without requesting credentials or product requirements.

## 3.9.0 - 2026-09-28

### Changed
- Refactored all three root test modules around behavioral contracts: release and installer coverage now uses public CLIs in isolated Git repositories, documentation checks consume public help and published vocabulary, and unavoidable agent-brief checks are explicit policy contracts rather than production-helper or implementation-shape tests.

## 3.8.3 - 2026-09-28

### Fixed
- CI checks out the real pull-request `head.sha` rather than GitHub's synthetic merge commit, so per-commit `Docs-Impact` attribution sees the branch's actual commits and does not treat the host-generated merge as an unwaived source change.

## 3.8.2 - 2026-09-28

### Fixed
- `tools/check_docs_sync.py` attributes merge commits by unioning diffs against every parent, so an undocumented merge-resolution-only source change cannot be hidden by a later waived commit.
- Per-commit waiver accounting expands renames to both old and new paths, so a valid waiver fully covers its own rename while unwaived source-to-test renames remain enforced.

## 3.8.1 - 2026-09-28

### Fixed
- Preserve every released changelog entry byte-for-byte when stacking the PR-reviewer release; only the new release section is added.

## 3.8.0 - 2026-09-28

### Added
- `sub-agents/pr-reviewer.md`: a global, language- and host-neutral reviewer that evaluates the full diff from the PR’s actual base, audits prior reviews and defers deep findings to specialist roles.
- This repository requires `pr-reviewer` for every pull request and every prior review.

### Fixed
- Local review instructions resolve `baseRefName` from GitHub rather than hard-coding `origin/main`, so stacked and non-main pull requests are reviewed against the correct merge base.

## 3.7.0 - 2026-09-28

### Added
- Branch-per-improvement workflow and SemVer releases: `tools/release.py` infers the level from `## Unreleased`, bumps `SKILL.md`, rolls this changelog and commits the release; after the exact PR head is approved it creates the annotated `vX.Y.Z` tag. It refuses protected/arbitrary branch names, a dirty worktree, an invalid date, an empty section or an existing tag. `tests/test_versioning.py` keeps the version and changelog consistent.

### Changed
- Release tags are created only by `tools/release.py --tag` after CI and `pr-reviewer` approve the exact unchanged release commit; `--apply` never publishes a pre-review tag.

## 3.6.1 - 2026-09-28

### Fixed
- `tools/check_docs_sync.py --base` accepts the first documentation PR when `docs/doc-map.json` does not yet exist in the base revision; pre-diff ownership is empty in that bootstrap case.

## 3.6.0 - 2026-09-28

### Added
- Linked documentation set under `docs/`: an index, an overview, a glossary and one page per component, with a file-ownership map in `docs/doc-map.json`.
- `tests/test_docs.py`, which fails when documentation drifts from the code (coverage, links, reachability, CLI flags, statuses, actions, ecosystems, agent tables).
- `tools/check_docs_sync.py`, the `.githooks/commit-msg` hook and a CI workflow that reject orchestration changes without documentation.
- `AGENTS.md`, with the documentation rule for agents working in this repository.

### Fixed
- The installer ignores Python interpreter artifacts (`__pycache__`, `.pyc`, `.pyo`) in the source template.
- Docs-impact waivers are commit-scoped; renames and deletions preserve ownership checks; Markdown anchors and argparse flags are structurally checked.
- Direct maintainer requests may state `Issue: not applicable — direct request` instead of creating an artificial issue.

## 3.4.0 - 2026-09-28

### Added
- Initial branch/version workflow draft (superseded by the reviewed workflow released after `3.6.1`).

## 3.3.0 - 2026-09-28

### Changed
- The controller is language-agnostic: `FORMAT_DART_CHANGED_FILES` is now `FORMAT_CHANGED_FILES`, and `changed_dart_files_available` is now `changed_files_available` (the legacy name is still accepted).

### Added
- `runtime/detect_stack.py`: evidence-based detection of 13 ecosystems. Its report is included in the installer dry run.
- A language-neutral `GATES.md` template.
- Per-worktree Obsidian vault binding, write containment, bootstrap and migration tools.
