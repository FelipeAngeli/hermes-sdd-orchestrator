# Changelog

All notable changes to the orchestration are recorded here. Every change under `skills/`, `tools/`, `.githooks/` or `.github/workflows/` adds an entry (enforced by `tools/check_docs_sync.py`; see [docs/maintaining-docs.md](docs/maintaining-docs.md)).

## Unreleased

## 5.0.0 - 2026-09-28

### Breaking
- `validate_protocol.py` enforces write scope and cited evidence: SPECIFY, CLARIFY, PLAN, TASKS and TEST reject any reported file change; every IMPLEMENT status requires anchored, non-match-all `editable_paths`, matched segment by segment (`*` never crosses `/`); an `AGENT` `PASS` for the current IMPLEMENT slice or in TEST must cite, in backticks, one of the commands the controller bound to that check (`check_verifiers`) recorded as exit 0 with `PASS`; optional `required_commands` must all be recorded as passing. Controllers must now pass `editable_paths` for IMPLEMENT and `check_verifiers` for IMPLEMENT/TEST, and validate read-only IMPLEMENT results (`project-context-guardian`, `data-flow-tracer`) with `role`.

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
