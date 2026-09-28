# Changelog

All notable changes to the orchestration are recorded here. Every change under `skills/`, `tools/`, `.githooks/` or `.github/workflows/` adds an entry (enforced by `tools/check_docs_sync.py`; see [docs/maintaining-docs.md](docs/maintaining-docs.md)).

## Unreleased

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
