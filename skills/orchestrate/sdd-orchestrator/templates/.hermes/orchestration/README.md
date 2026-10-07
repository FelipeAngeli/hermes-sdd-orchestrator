# Installed orchestration layout

This directory separates controller concerns while keeping mutable local state at one predictable root.

```text
.hermes/
├── .env         # created on TypeSafe/Jev opt-in; ignored and private
├── .env.example # supported provider key names; contains no credential
└── orchestration/
    ├── agents/      # stage-specific leaf-worker briefs
    ├── contracts/   # executor and reviewer interfaces
    ├── hooks/       # opt-in Hermes shell-hook adapters
    ├── policies/    # FSM, gates, recovery and bounded automation
    ├── runtime/     # deterministic Python tools and explicit connectors
    ├── schemas/     # JSON Schema documents
    ├── sub-agents/  # specialized leaf-worker briefs
    ├── tests/       # installed protocol tests
    ├── STATE.md
    ├── PROJECT_SETUP.md
    ├── ACTION_JOURNAL.json
    ├── INCIDENTS.md
    ├── JEV_CACHE.json             # created on first live decision; ignored and private
    ├── TERMINAL_PROGRESS.json     # current CLI presentation state; ignored and private
    └── action-journal-history/  # created on demand
```

## Commands

Run the installed protocol suite:

```text
python3 -m unittest discover -s .hermes/orchestration/tests -p 'test_*.py'
```

Inspect individual runtime tools with `--help`, for example:

```text
python3 .hermes/orchestration/runtime/action_journal.py --help
python3 .hermes/orchestration/runtime/bounded_run_planner.py --help
python3 .hermes/orchestration/runtime/bounded_run_driver.py --help
```

## Terminal progress

`runtime/terminal_progress.py` gives the classic Hermes terminal a persistent, dependency-free SDD dashboard. It shows the active provider, current stage and its position, remaining stages, time per stage, Jev status/provider/model and exact classification area, plus recent activity. This presentation file never replaces `STATE.md` or the action journal and must not contain secrets or hidden chain-of-thought.

```text
python3 .hermes/orchestration/runtime/terminal_progress.py start --provider openai-codex --stage SPECIFY
python3 .hermes/orchestration/runtime/terminal_progress.py activity --message "Inspecting repository context"
python3 .hermes/orchestration/runtime/terminal_progress.py stage --name CLARIFY
python3 .hermes/orchestration/runtime/terminal_progress.py show
python3 .hermes/orchestration/runtime/terminal_progress.py finish --status DONE
```

Every mutation immediately renders the updated dashboard; `--json` keeps the same state machine available to automation. The controller records one concise activity before every command, worker dispatch, gate and material action. Only `CLARIFY` may be skipped. Live semantic-governor calls automatically mark Jev as active before network access and completed afterward, while cache hits leave Jev inactive because no paid request occurred.

## Project-local engineering skills

The sibling `.hermes/skills/` directory contains three progressive playbooks: `sdd-backend-engineering`, `sdd-architecture-decisions` and `sdd-database-design-migrations`. They guide PLAN/IMPLEMENT; existing specialist sub-agents remain the independent auditors. Installation does not trust a repository or mutate global skills. After inspection, run `hermes skills trust` in the repository and start a new session so Hermes can discover them. Stage-context schema 2 binds `project_root` to the canonical live Git workspace and records every required `SKILL.md`/reference path and hash; `stage_context.py check` verifies actual bytes/frontmatter and refuses extra, missing or changed guidance. Regenerate schema-1 manifests.

## Opt-in Hermes hooks

The installed `hooks/` directory mirrors the `agents/` and `sub-agents/` source layers. Its shell scripts connect Hermes events to the existing deterministic runtime: slice/vault scope at `pre_tool_call`, acceptance evidence at `pre_verify`, non-sensitive leaf-worker audit metadata at `subagent_stop`, an optional bounded STATE summary at `pre_llm_call`, and the wiki recorders: each turn of a session inside a served worktree at `post_llm_call` (`raw/transcripts/sessions/`) and the session's end at `on_session_finalize` (`log.md`).

Installation only copies these files. It never edits a Hermes profile or grants hook consent. To activate them, inspect `hooks/README.md`, replace `<ABSOLUTE_PROJECT_ROOT>` in `hooks/hooks.example.yaml`, and merge it into a dedicated profile for this checkout. Absolute paths bind Hermes' persistent `(event, command)` consent to this repository. The controller must atomically maintain `STAGE_CONTEXT.json`, `HOOK_BINDING.json`, and `VERIFICATION_EVIDENCE.json` before an active implementation turn; the scope hook fails closed when live Git, STATE, journal, binding, and context cannot be proven consistent.

Before the first demand, resolve `.hermes/orchestration/PROJECT_SETUP.md`. Ask separately about TypeSafe guidance installation and automatic potentially billed Jev classifications. Installation records `automatic_semantic_governance: false`; only `--automatic-jev-governance` records explicit consent. Legacy install-only answers reopen onboarding. Accept `none`, never ask for credentials, and validate connectivity read-only.

## Optional TypeSafe skill and Jev connector

Jev is TypeSafe's flagship System One model; the installable project integration is the `typesafe-ai` skill. Preview with the project installer using `--target <repo-root> --typesafe-ai install --json`, then apply only after explicit opt-in by adding `--apply`. The installer does not execute the official `npx skills add typesafe-ai/skills --skill typesafe-ai` command or download code. Instead it verifies and copies a reviewed snapshot from an immutable upstream commit, preserves unrelated lock data, and commits the skill, merged pinned lock entry, onboarding answer and a private `.env` placeholder as one rollback-covered operation. An existing regular `.env` is never overwritten. An existing skill installation is recorded without overwrite only when the same pinned lock and trusted digest verification passes; a conflict fails closed. Use `--typesafe-ai none --apply` to record an explicit opt-out only when no TypeSafe installation is discoverable. The generated skill and lock remain repository-local and are not added to global Hermes configuration.

Set the selected provider key in owner-only `.hermes/.env`, then run `typesafe_connector.py preflight --json`. Only explicit automatic Jev consent plus READY preflight is standing authorization for `semantic_governor.py decide`; TypeSafe installation alone authorizes no calls. The governor descriptor-reads `PROJECT_SETUP.md`, rechecks exact enabled consent and local preflight at the connector boundary, batches classifications, accepts confidence `0.70` or higher, and writes a durable in-flight tombstone before the paid request. A failed pre-call write prevents evaluation; a failed final write returns `REVIEW` with a receipt and leaves the tombstone so the fingerprint is not retried. Live calls print timed `JEV EM USO`/`JEV USADO` receipts and update an active terminal dashboard; cache hits make no paid call. The guarded governor/cache and progress dashboard are POSIX-only and fail closed before state/network access on Windows rather than claiming junction-safe handling; the rest of the skill remains supported there. The raw connector remains available for explicit typed requests. Fixed endpoints refuse redirects and never print keys or remote error bodies.

To use the independent Jev AI compatible endpoint instead, set `JEV_AI_API_KEY` in the same `.hermes/.env` (or the server environment) and pass `--provider jev-ai`. It targets only `https://jev-ai.pro/api` (`/v1/systemone`, `/v1/models`) and never falls back to TypeSafe. Run `preflight --provider jev-ai --json` to see the resolved destination, `models --provider jev-ai --json` for an authenticated lookup without inference, and `evaluate --provider jev-ai --input request.json --json` for one billed decision. Requests above 256000 bytes, 64 questions or 64-character question IDs are refused locally. On a timeout, lost connection or 504 the report says `"outcome":"UNCERTAIN"`: check Jev AI usage before resending.

Configure project-specific validation in `policies/GATES.md` before starting a demand. `runtime/detect_stack.py` is a read-only helper for that step: it reports each ecosystem (Node, Python, Go, Rust, JVM, .NET, Ruby, PHP, Elixir, Swift, C/C++, Dart…) with the manifest that proves it, the CI providers present and a suggested command per gate, leaving a gate `null` when nothing supports it. The FSM gate names (`TEST_FOCUSED`, `FORMAT_CHANGED_FILES`, `ANALYZE`) are identical for every language. The mutable state files are controller-owned and must not be moved into a source layer.

## Obsidian second brain

`runtime/obsidian_binding.py` resolves every vault path through `.hermes/obsidian.json` at the repository root. That binding is deliberately **versioned**, not excluded: it is the connectivity itself and must survive a clone. `HERMES_OBSIDIAN_VAULT` overrides `vault_path` on a machine where the vault sits elsewhere; a missing vault is an error, never a silent fallback.

`runtime/obsidian_connector.py` is a stdlib-only, read-only adapter for the official Obsidian CLI. `discover --json` lists candidates before binding; `preflight --repo . --json` validates the exact bound vault/container and reports `READY_CLI` or a valid `READY_FILESYSTEM` fallback. Connector use requires a canonical, symlink-free bound vault/container path. Library calls provide container-scoped CLI search plus root-anchored no-follow filesystem note reads and tag aggregation; they return only container-relative note paths, reject traversal/symlinks/runtime paths, expose no vault mutation command and refuse lexical CLI opening with `OPEN_UNSUPPORTED`. The official CLI needs Obsidian 1.12.7+, its Command line interface setting and the desktop app. Headless Sync may supply a local vault on a server but never replaces SDD binding or write controls.

`runtime/vault_guard.py` refuses writes outside the bound `project_container` and hashes content for `capture_vault_baseline` / `assert_baseline_preserved`, because Git cannot see the vault and mtimes change without edits. The runtime directory is excluded from that baseline: every transition rewrites it by design.

`runtime/bootstrap_worktree.py` prepares an already-registered worktree and is the per-worktree counterpart to `scripts/install_project.py`. Runtime state lands in `<project_container>/<runtime_subpath>/<worktree-slug>/`, where the slug hashes the worktree's resolved absolute path, so two worktrees of one repository never share a `STATE.md` or journal — that sharing would break the one-executor-at-a-time guarantee. See `BOOTSTRAP.md`.

`runtime/migrate_to_vault.py`, `runtime/migrate_all_worktrees.py` and `runtime/consolidate_runtime.py` move pre-vault installations onto this model; `runtime/state_format.py` is the shared STATE serializer.

> A fresh `install_project.py` writes `STATE.md`, `INCIDENTS.md` and `ACTION_JOURNAL.json` at the orchestration root. Once a worktree is bootstrapped against a vault, those files live in the vault instead and the local copies are no longer the source of truth.

Before dispatching a worker, load the matching brief from `agents/` together with only the applicable contract, policy excerpt and scoped project evidence. The brief never grants STATE or transition authority. Executor schema version 3 carries evidence-backed facts, material assumptions/questions and stable acceptance-check IDs through every stage. TASKS produces the controller's authoritative non-empty acceptance mapping: ID, criterion, verification method, verifier and slice assignment. Workers may change only status and evidence. IMPLEMENT receives exactly one current slice ID plus an explicit disjoint completed set and may not add another TDD slice; TEST and every REVIEW status must match the full mapping rather than treating payload-declared values or green gates as authority.

Before each dispatch, the controller checks a per-stage context manifest with `runtime/stage_context.py`. The manifest holds scoped excerpts, the `project-context-guardian` result required before PLAN and IMPLEMENT, the `semantic_governance` Jev decision those stages need when automatic Jev consent is recorded, and the slice's editable paths and observable verifiers. An approved slice hash is reused rather than asked for again. When a valid result fails verification, `runtime/correction_loop.py` decides whether one more correction is allowed or the loop pauses with an explicit `stop_reason`. Both tools only read controller-owned files and never write state.

When a stage needs a narrower role, the controller may select one matching brief from `sub-agents/` instead, subject to `policies/DISPATCH_POLICY.md`, whose default is **not** to dispatch: a specialist runs only when the controller can name the pending decision that depends on its answer, and only when a deterministic tool cannot answer the question first. Stage agents never dispatch sub-agents; the one-leaf-worker invariant remains unchanged. A successful specialized action returns evidence to the controller but never completes or transitions the enclosing stage by itself.

`tdd-guardian.md` and `regression-hunter.md` are audit roles for TEST and REVIEW. The guardian answers whether the suite would go red if the rule broke, by mutating production code and reverting each mutation; the hunter answers what previously worked and may have stopped, by running the suites of consumers the change did not touch. Both are read-only, repair nothing, and mark every finding as proven or unproven. Grant them an explicit mutation and execution budget, and treat residue in the workspace as a blocker.

`api-contract-auditor.md` is an audit role for PLAN and REVIEW. It answers whether the client models still match the API, comparing them against the published specification and the deployed server across field names, types, nullability, enums, endpoint lifecycle and error envelopes. It ranks its sources instead of picking the convenient one, never invents a contract element to close a gap, and reports an unresolvable divergence as a gap for human decision. Tell it which environment is authoritative, and authorize any live call explicitly.

`security-reviewer.md` covers exploitable flaws and disclosure together: hardcoded credentials, insecure storage, authentication and authorization gaps, and sensitive data reaching logs or telemetry. It never reproduces a discovered secret value anywhere, and reports a committed secret as compromised and requiring rotation, because deleting the line does not revoke the credential.

`performance-auditor.md` is an audit role for PLAN and REVIEW. It looks for work the system does not need to do: duplicate requests, missing or wrong caching, N+1 and unindexed queries, unbounded results, recomputation and rebuilds, blocked critical paths, wasteful allocation and undisposed resources. Every finding carries a measurement or a counted operation and the input size at which it matters, and concluding that nothing is worth changing is an accepted result — a brief that rewards findings produces noise. Authorize any load test or shared-environment benchmark explicitly.

`documentation-writer.md` is the only writing role among these: it reads the implemented code and brings technical documentation, ADRs, README and diagrams back in line with it. Assign its writable paths explicitly, as with the implementer. It treats existing documentation as a claim to verify rather than text to paraphrase, deletes documentation for code that no longer exists, and records an unexplained decision as an open question instead of inventing a rationale. It never edits code to match the text: a mismatch is reported, and the controller decides which side is wrong.

`architecture-guardian.md` is an audit role for PLAN and REVIEW. It reports layer traversal, wrong dependency direction, misplaced services, leaking abstractions and circular module dependencies — but only against rules the project itself declares, quoting the document, configuration or lint setting behind each finding. An undeclared convention is raised as a question, never enforced, and an inherited violation is reported separately from one the change introduced. Point it at the architecture documents and boundary tooling that state the rules; without them it has nothing legitimate to enforce.
