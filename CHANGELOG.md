# Changelog

All notable changes to the orchestration are recorded here. Every change under `skills/`, `tools/`, `.githooks/` or `.github/workflows/` adds an entry (enforced by `tools/check_docs_sync.py`; see [docs/maintaining-docs.md](docs/maintaining-docs.md)).

## Unreleased

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
