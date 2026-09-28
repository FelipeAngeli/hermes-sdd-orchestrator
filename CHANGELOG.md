# Changelog

All notable changes to the orchestration are recorded here. Every change under `skills/`, `tools/`, `.githooks/` or `.github/workflows/` adds an entry (enforced by `tools/check_docs_sync.py`; see [docs/maintaining-docs.md](docs/maintaining-docs.md)).

## Unreleased

## 3.4.0 - 2026-09-28

### Added
- Linked documentation set under `docs/`: an index, an overview, a glossary and one page per component, with a file-ownership map in `docs/doc-map.json`.
- `tests/test_docs.py`, which fails when documentation drifts from the code (coverage, links, reachability, CLI flags, statuses, actions, ecosystems, agent tables).
- `tools/check_docs_sync.py`, the `.githooks/commit-msg` hook and a CI workflow that reject orchestration changes without documentation.
- `AGENTS.md`, with the documentation rule for agents working in this repository.
- Branch-per-improvement workflow and SemVer releases: `tools/release.py` infers the level from `## Unreleased`, bumps `SKILL.md`, rolls this changelog, commits and tags `vX.Y.Z`; it refuses on `main`, a dirty worktree, an empty section or an existing tag. `tests/test_versioning.py` keeps `SKILL.md`, the changelog and tags consistent.

## 3.3.0 - 2026-09-28

### Changed
- The controller is language-agnostic: `FORMAT_DART_CHANGED_FILES` is now `FORMAT_CHANGED_FILES`, and `changed_dart_files_available` is now `changed_files_available` (the legacy name is still accepted).

### Added
- `runtime/detect_stack.py`: evidence-based detection of 13 ecosystems. Its report is included in the installer dry run.
- A language-neutral `GATES.md` template.
- Per-worktree Obsidian vault binding, write containment, bootstrap and migration tools.
