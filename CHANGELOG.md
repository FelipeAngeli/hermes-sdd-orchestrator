# Changelog

All notable changes to the orchestration are recorded here. Every change under `skills/`, `tools/`, `.githooks/` or `.github/workflows/` adds an entry.

## Unreleased

### Fixed
- `tools/check_docs_sync.py` attributes merge commits by unioning diffs against every parent, so an undocumented merge-resolution-only source change cannot be hidden by a later waived commit.
- Per-commit waiver accounting expands renames to both old and new paths, so a valid waiver fully covers its own rename while unwaived source-to-test renames remain enforced.

## 3.8.0 - 2026-09-28

### Added
- `sub-agents/pr-reviewer.md`: global, language- and host-neutral PR review from the real merge-base diff, including an audit of prior reviews and deference to specialist roles.
- This repository requires `pr-reviewer` on every pull request and prior review.

### Fixed
- Local PR-review instructions resolve `baseRefName` from GitHub instead of hard-coding `origin/main`, so stacked and non-main PRs are reviewed against the correct merge base.

## 3.7.0 - 2026-09-28

### Added
- Branch-per-improvement workflow and SemVer release commits through `tools/release.py`.

### Changed
- Release tags are created only after CI and `pr-reviewer` approve the exact unchanged release commit.

## 3.6.1 - 2026-09-28

### Fixed
- Initial docs-map bootstrap in the base-aware synchronization checker.

## 3.6.0 - 2026-09-28

### Added
- Linked per-component documentation, ownership map, drift tests, hook and CI.

### Fixed
- Python caches are excluded from installer templates; documentation waivers, renames, deletions, anchors and CLI flags are checked structurally.
- Direct maintainer requests may state that no issue applies.

## 3.5.0 - 2026-09-28

### Added
- Initial `pr-reviewer` draft (superseded by the reviewed release based on `3.7.0`).

## 3.4.0 - 2026-09-28

### Added
- Initial branch/version workflow draft (superseded by `3.7.0`).

## 3.3.0 - 2026-09-28

### Changed
- The controller is language-agnostic; the legacy Dart snapshot field remains accepted.

### Added
- Evidence-based stack detection and a language-neutral gates template.
- Per-worktree Obsidian vault binding, containment, bootstrap and migration.
