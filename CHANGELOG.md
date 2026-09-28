# Changelog

All notable changes to the orchestration are recorded here. Every change under `skills/`, `tools/`, `.githooks/` or `.github/workflows/` adds an entry.

## Unreleased

### Added
- `sub-agents/pr-reviewer.md`: global, language- and host-neutral PR review from the real merge-base diff, including an audit of prior reviews and deference to specialist roles.
- This repository requires `pr-reviewer` on every pull request and prior review.

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
