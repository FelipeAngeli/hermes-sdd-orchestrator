# Harness: stage context and bounded correction

[Docs index](../README.md) · Related: [FSM and bounded loop](fsm-and-loop.md), [Contracts and schemas](contracts-and-schemas.md), [Sub-agents](sub-agents.md), [Action journal](action-journal.md)

**Files:** `runtime/stage_context.py`, `schemas/STAGE_CONTEXT_SCHEMA.json`, `runtime/correction_loop.py`, `schemas/CORRECTION_LOOP_SCHEMA.json`.

These two tools add the harness around every worker dispatch. They do not replace the FSM, the planner or the drivers. The controller uses them to check that a worker gets the right context and a verifiable contract before it runs, and that a failed verification is corrected only within explicit limits. `correction_loop.py` is pure. `stage_context.py` reads controller-owned JSON plus only the declared project-local `SKILL.md` and reference files to verify their bytes/frontmatter; it never reads other repository files, the vault or STATE. Neither tool mutates state, dispatches a worker or runs a verifier.

```text
stage_context.py check ──► dispatch one worker ──► validate_protocol.py ──► verified?
        ▲                                                                   │ no
        │                                                                   ▼
        └──────────── correction_loop.py decide ◄─────── append attempt ◄───┘
                        CORRECT │ VERIFIED │ STOP (stop_reason, evidence, next_step)
```

## Stage context manifest schema 2 (`stage_context.py`)

Before each dispatch the controller writes a schema-2 manifest that validates against `STAGE_CONTEXT_SCHEMA.json`. Schema 2 replaces schema 1: controllers regenerate the ephemeral manifest with canonical absolute `project_root`, top-level `playbooks` and slice `required_playbooks`. The runtime compares `project_root` to the canonical Git toplevel of its live working directory; a different, non-canonical or symlinked root is invalid. The root is then used only to contain and verify declared playbook files. The manifest also holds:

- `sources`: scoped excerpts, each with `kind` (`RULES`, `ARCHITECTURE`, `SPEC`, `DECISION`, `EVIDENCE`, `CODE`, `CONTRACT`), path, line range and hash. `limits.max_sources` and `limits.max_lines_per_source` bound the list. The defaults the controller uses are those already in `.hermes.md`: 12 files and 250 lines per file. The check refuses excerpts longer than the per-source limit and any source path containing a controller runtime file (`FORBIDDEN_SOURCE_NAMES`: `STATE.md`, `ACTION_JOURNAL.json`, `INCIDENTS.md`, `action-journal-history`). The manifest does not record file lengths, so it cannot detect that a short file was sent in full, and it cannot detect a transcript pasted under another name. Keeping those out remains the controller's job under `.hermes.md`.
- `project_context`: the outcome of `project-context-guardian`. The status is `CURRENT`, `REFRESHED`, `PARTIAL` or `MISSING`, and the record also holds the checked HEAD, the Obsidian binding (`BOUND`, `UNBOUND`, `NOT_CONFIGURED`), evidence and gaps. The guardian must have been consulted before stages in `PROJECT_CONTEXT_STAGES` (PLAN and IMPLEMENT). A `PARTIAL` result has to name what it did not examine. An unbound vault is allowed; missing context is not.
- `playbooks`: project-local engineering skills loaded for the dispatch, each with exact name, semantic version, `.hermes/skills/<name>/SKILL.md` path, SHA-256, loaded `references` path/hash descriptors and reason. The checker resolves these paths under `project_root`, rejects symlinks/escapes, hashes the actual bytes and matches strict plain-scalar `name`/`version` frontmatter to the descriptor; quoted, tagged, commented, block or duplicate identity scalars fail closed. Ordinary source excerpts remain in `sources`.
- `divergences`: code/documentation mismatches, each citing both sides. `authority` is always `CODE`, because the code describes what is implemented. The divergence is recorded; no decision is invented to settle it.
- `context_graph`: optional. The [context graph](context-graph.md) query made for this dispatch — `status` (`CURRENT`, `REFRESHED`, `PARTIAL`, `MISSING`, `NOT_CONFIGURED`), `source` (`OBSIDIAN` or `REPOSITORY`), the `selectors` queried, the returned `nodes` with kind/note/distance, the in-scope `decisions` with reason and date, `unresolved` selectors and any `findings`. Omitting the key or sending `null` is valid: a project without a graph is never blocked. The record is excluded from the slice hash, so refreshing the graph never invalidates an approval.
- `semantic_governance`: required for `GOVERNANCE_STAGES` (PLAN and IMPLEMENT) only when `PROJECT_SETUP.md` holds the exact automatic Jev consent record (`automatic_semantic_governance: true`). It carries the `fingerprint` of the `semantic_governor.py decide` report for this ticket and a `review_resolution`, the human decision that replaces a `REVIEW` outcome (`null` otherwise). The checker reads the report back from the governor cache under its lock and validates its full structure (`semantic_governor.verify_cached_decision`), so an invented fingerprint, a minimal hand-written entry or a report for another ticket fails. Only an explicit non-consent answer (`none`, `UNRESOLVED`, the `false` object, or the legacy install-only object) disables the gate (`semantic_governor.consent_state`); a missing setup, a missing or duplicated `typesafe_ai` line, or malformed JSON fails closed. With consent on a non-POSIX platform, where the governor cannot run, the gate also fails closed. The decision is bound to the ticket, not to a stage or slice: one governor decision covers the ticket's PLAN and IMPLEMENT dispatches until the controller requests a new one. The cache is owner-private, which proves the controller followed the governor, not that a process running as the same user could not write it. The record is excluded from the slice hash.
- `slice`: required for `SLICE_STAGES` (IMPLEMENT, TEST, REVIEW). It declares `current_slice_ids`, `completed_slice_ids`, `editable_paths`, project-local `required_playbooks` bound to slice IDs, the authoritative `acceptance` mapping and `required_verification`.
- `approval`: `approved_slice_sha256s`, the per-slice hashes stored when a human approved PLAN/TASKS, and the evidence for that approval.

### Rules enforced by `check`

| Code | Rule |
| --- | --- |
| `CONTEXT_BUDGET_EXCEEDED`, `CONTEXT_EXCERPT_TOO_LARGE`, `CONTEXT_EXCERPT_INVALID`, `CONTEXT_SOURCE_DUPLICATED`, `CONTEXT_SOURCE_FORBIDDEN` | Context stays within the declared budget, as excerpts, each listed once, and never includes STATE or journal files. |
| `PROJECT_CONTEXT_REQUIRED`, `PROJECT_CONTEXT_GAPS_REQUIRED` | PLAN and IMPLEMENT need a guardian result with evidence and HEAD; a partial result must list its gaps. |
| `CONTEXT_GRAPH_FINDINGS_PRESENT`, `CONTEXT_GRAPH_SELECTION_REQUIRED`, `CONTEXT_GRAPH_GAPS_REQUIRED`, `CONTEXT_GRAPH_STATUS_INCONSISTENT`, `CONTEXT_GRAPH_DECISION_UNRECORDED` | Only checked when a [`context_graph`](context-graph.md) record is present. A declared graph carries no unresolved findings; PLAN and IMPLEMENT record which selectors were queried and what came back (unless the status is `MISSING`); a `PARTIAL` graph names its unresolved selectors and no other status leaves one; an `OBSIDIAN` source requires a `BOUND` vault; `NOT_CONFIGURED` carries no content; and every `DECISION` node in scope carries its reason and date, with no recorded decision that is not in scope. |
| `SLICE_REQUIRED`, `SLICE_CURRENT_INVALID` | IMPLEMENT/TEST/REVIEW carry a slice. IMPLEMENT has exactly one current slice, disjoint from the completed ones. |
| `SLICE_EDITABLE_PATHS_REQUIRED`, `SLICE_EDITABLE_PATH_UNSAFE`, `ANALYSIS_STAGE_EDITABLE_PATHS` | Only IMPLEMENT declares editable paths; TEST and REVIEW declare none. A pattern must be a canonical repository-relative path: no leading `/` or `~`, no empty, `.` or `..` segment. Its first segment must also be literal. That rejects `*`, `*/*`, `?*`, `**/**`, `./**` and `.`. Matching is segment by segment: `*`, `?` and `[...]` never cross `/`, and only a whole `**` segment spans directories. `src/*` covers `src/a.py`, not `src/deep/a.py`; use `src/**` for the subtree. |
| `PLAYBOOK_CONTENT_MISMATCH`, `PLAYBOOK_DUPLICATED`, `PLAYBOOK_PATH_INVALID`, `PLAYBOOK_REQUIRED`, `PLAYBOOK_ROOT_INVALID`, `PLAYBOOK_UNDECLARED`, `PLAYBOOK_UNKNOWN_SLICE` | `project_root` equals the canonical non-symlinked live Git workspace. When that workspace has no `.hermes/skills/` and the controller is vault-resident (its container carries `.hermes/obsidian.json` and `.hermes/skills/`), playbook bytes are verified from the container instead; descriptor paths keep the same `.hermes/skills/<name>/…` form. A loaded playbook is unique, byte-verified from exact non-symlinked skill/reference paths and matches `SKILL.md` name/version; each requirement names a real acceptance slice; the current IMPLEMENT slice loads exactly its required playbooks, no more and no fewer. |
| `ACCEPTANCE_CHECK_UNVERIFIED`, `VERIFIER_COMMAND_REQUIRED`, `VERIFIER_UNKNOWN_CHECK` | Every AGENT check in scope is bound to an observable verifier (`TEST`, `STATIC_ANALYSIS`, `SCHEMA_VALIDATION`, `STATE_INSPECTION`, `LOG_INSPECTION`) that has a command, and every HUMAN check is bound to a `HUMAN` verifier. |
| `INDEPENDENT_VERIFIER_REQUIRED` | Each AGENT check in scope needs at least one bound verifier that predates the slice (`introduced_by_slice: false`). A test the slice has just written cannot be its only proof, and a pre-existing verifier bound to another check does not count. |
| `SCOPE_CHANGE_REQUIRED` | The approved hash no longer matches the slice contract. |
| `JEV_GOVERNANCE_RECORD_REQUIRED`, `JEV_GOVERNANCE_RECORD_UNVERIFIED`, `JEV_GOVERNANCE_REVIEW_UNRESOLVED`, `JEV_GOVERNANCE_SETUP_INVALID`, `JEV_GOVERNANCE_PLATFORM_UNSUPPORTED` | Only for PLAN and IMPLEMENT. With automatic Jev consent the manifest carries `semantic_governance`; its fingerprint is a structurally valid `LIVE_JEV` report for the same ticket in the governor cache (`JEV_CACHE.json`, or `--jev-cache`); a `REVIEW` report carries a human `review_resolution`. A `PROJECT_SETUP.md` (or `--project-setup`) without an explicit answer fails closed, and so does consent on a platform where the governor cannot run. |
| `SCHEMA_INVALID` | The manifest does not match the schema. |

The full list is published as `CONTEXT_ERROR_CODES`.

### Approval reuse

`slice_sha256` hashes one slice's contract: the ticket, the slice ID, the editable paths, the byte-verified descriptors of project-local `SKILL.md` and reference files required by that slice, the checks assigned to that slice, and the verifiers bound to those checks. The stage, `project_root` and completed-slice cursor are excluded, and lists are sorted. This makes the hash stable across the flow:

1. When the human approves PLAN/TASKS, the controller computes the hash of each planned slice (a manifest with `stage: IMPLEMENT` and that slice as current) and stores them as `approved_slice_sha256s`.
2. Each IMPLEMENT dispatch checks its current slice against that list. A match gives `APPROVAL_REUSED`, so the controller does not ask again. Finishing S1 does not change S2's hash, and refreshing context sources does not change any hash.
3. TEST and REVIEW authorize no writes and have no slice hash; with an approval present they report `APPROVAL_NOT_APPLICABLE`.
4. Any change to the slice's editable paths, criteria, canonicalized loaded skill/reference descriptors or bound verifiers gives `APPROVAL_REQUIRED` plus `SCOPE_CHANGE_REQUIRED`: this is new scope that needs its own approval. Any other invalid manifest also reports `APPROVAL_REQUIRED`, never a misleading reused approval. With no approval recorded, the result is `APPROVAL_NOT_REQUESTED`. This check never authorizes commit, push, issue-tracker, Obsidian, backend or DEV E2E actions; those remain `HUMAN_REQUIRED` in the [planner](fsm-and-loop.md#actions).

### Feeding the validator

`verifier-context` projects a valid manifest into the keyword arguments of [`validate_protocol.py`](contracts-and-schemas.md#validate_protocolpy): `expected_acceptance`, `required_commands`, `check_verifiers` (check ID → the commands of the verifiers bound to it) and, for IMPLEMENT, `current_slice_ids`, `completed_slice_ids` and `editable_paths`. For IMPLEMENT, `required_commands` holds only the verifiers bound to the current slice's checks, because a later slice's verifier may be a test that does not exist yet. For TEST and REVIEW it holds every verifier.

`--role` (`PROJECT_CONTEXT_GUARDIAN` or `DATA_FLOW_TRACER`, only in that role's stages) builds the context for a read-only dispatch. The role is added and editable paths are left out. The missing or partial project context is tolerated, because the guardian's dispatch is what produces it; every other rule still applies. With that context, the validator rejects any write, TDD slice or new evidence. The read-only result must also carry completed-slice checks forward as `PASS` with evidence. An AGENT `PASS` must cite one of *its own* bound commands, and that command must be recorded as passing. So a worker cannot prove a check with a command it chose (for example `true`) or with the verifier of a different check. The exit code is still self-reported by the worker. The controller confirms actual execution from its own records, as `EXECUTOR_CONTRACT.md` already requires. The controller passes this output unchanged with `--context`. The worker never supplies or edits it.

```text
stage_context.py check --context ctx.json --json             # exit 2 on any finding
# every command also accepts --project-setup PATH and --jev-cache PATH (defaults: the controller's PROJECT_SETUP.md and
# JEV_CACHE.json); paths are made absolute without resolving symlinks, so the governor's no-follow walk still applies
stage_context.py hash --context ctx.json --json              # slice_sha256 of the current IMPLEMENT slice (exit 2 for other stages)
stage_context.py verifier-context --context ctx.json [--role PROJECT_CONTEXT_GUARDIAN] --json  # validator context
```

Subcommands `check`, `hash` and `verifier-context`. Flags: `--context`, `--role` (verifier-context only), `--json`.

## Bounded correction loop (`correction_loop.py`)

The controller keeps one record per failing action, validated by `CORRECTION_LOOP_SCHEMA.json`. The record holds the limits, the usage so far, every attempt and the next proposed attempt. Each attempt stores a `hypothesis`, a `tier` (`DETERMINISTIC`, `WORKER` or `ESCALATED`), an escalation reason, digests of the change and of the collected evidence, the verification result, the failed check IDs and a cost. `decide` returns one of `DECISIONS`:

| Decision | Meaning |
| --- | --- |
| `VERIFIED` | The last attempt passed verification. Commit the result to STATE and continue. |
| `CORRECT` | Exactly one more correction is allowed, for the listed failed checks, with the proposed hypothesis and tier. |
| `STOP` | Pause with an auditable `stop_reason`, the evidence (attempts, hypotheses, failed checks, last evidence digest), a concrete `next_step` and `state_updates` (`mode: PAUSED`, loop inactive). |

The stop reasons are `LOOP_STOP_REASONS`, and all of them are in the closed list of `LOOP_POLICY.md` §9:

- `RETRY_BUDGET_REACHED`: `limits.max_attempts` has been reached.
- `EXECUTOR_CALL_BUDGET_REACHED`: a `WORKER`/`ESCALATED` attempt would exceed `limits.max_executor_calls`. `DETERMINISTIC` attempts (formatter, codemod, host script) do not use executor calls.
- `COST_BUDGET_REACHED`: the recorded cost plus the proposal's estimate would exceed `limits.max_cost_units`. When a cost limit is configured and the usage is unknown, the tool stops. When no cost limit is configured (`null`), cost is only recorded.
- `NO_NEW_HYPOTHESIS`: there is no proposal, or the proposal repeats a hypothesis already tried (compared after case and whitespace normalization).
- `NO_PROGRESS`: the last two attempts produced the same change digest or the same evidence digest.

`ESCALATED` is refused (`ESCALATION_UNJUSTIFIED`) until at least one attempt has failed, and it always needs a written reason. The record also rejects attempts that are not numbered contiguously, any attempt recorded after a pass, and executor-call usage lower than the recorded worker attempts.

Default limits come from the existing budgets rather than a new policy. `max_attempts` is 1 plus `max_corrective_retries_per_action`, so 2 attempts. `max_executor_calls` is the executor calls remaining in the round or in the LOCAL_DELIVERY authorization. `max_cost_units` is `null` unless the project configures it. Review cycles and stage transitions are still limited by the [planner and drivers](fsm-and-loop.md).

```text
correction_loop.py decide --record loop.json --json
correction_loop.py validate --record loop.json --json
```

Subcommands `decide` and `validate`. Flags: `--record`, `--json`. A stop decision exits `0`, because stopping is a valid decision. A malformed record exits `2`.

## Relationship to existing controls

- A contract-invalid artifact still goes through the [action journal](action-journal.md): its single `CORRECTIVE_RETRY_AVAILABLE` retry, then `RETRY_BUDGET_REACHED`. The correction loop covers the other case: a valid result whose verification failed.
- Each correction is still a separate journaled action with its own `parent_action_id`. The loop record never replaces the journal or STATE.
- The driver still decides whether the round continues; the loop decides only whether this failure may be retried.
