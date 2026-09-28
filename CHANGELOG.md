# Changelog

All notable changes to the orchestration are recorded here. Every change under `skills/`, `tools/`, `.githooks/` or `.github/workflows/` adds an entry (enforced by `tools/check_docs_sync.py`; see [docs/maintaining-docs.md](docs/maintaining-docs.md)).

## Unreleased

### Added
- Linked documentation set under `docs/`: an index, an overview, a glossary and one page per component, with a file-ownership map in `docs/doc-map.json`.
- `tests/test_docs.py`, which fails when documentation drifts from the code (coverage, links, reachability, CLI flags, statuses, actions, ecosystems, agent tables).
- `tools/check_docs_sync.py`, the `.githooks/commit-msg` hook and a CI workflow that reject orchestration changes without documentation.
- `AGENTS.md`, with the documentation rule for agents working in this repository.

### Fixed
- The installer ignores Python interpreter artifacts (`__pycache__`, `.pyc`, `.pyo`) in the source template. Previously a cache generated while the repository tests ran could be copied into a fixture, recompiled there and make the idempotency check fail on Linux CI.
- `tools/check_docs_sync.py --base`: a `Docs-Impact: none` waiver covers only the commit that carries it, so an unrelated waived commit cannot excuse an undocumented change in the range.
- `tools/check_docs_sync.py`: a rename is checked as a deletion of the old path plus an addition of the new one, so moving a source file into `tests/` cannot bypass the docs requirement.
- `tests/test_docs.py` checks the CLI flags of every `tools/*.py`.
- Direct maintainer requests may use `Issue: not applicable — direct request`; contributors no longer need to create an artificial issue solely to satisfy the PR template.

## 3.3.0

### Changed
- The controller is language-agnostic: `FORMAT_DART_CHANGED_FILES` is now `FORMAT_CHANGED_FILES`, and `changed_dart_files_available` is now `changed_files_available` (the legacy name is still accepted).

### Added
- `runtime/detect_stack.py`: evidence-based detection of 13 ecosystems. Its report is included in the installer dry run.
- A language-neutral `GATES.md` template.
- Per-worktree Obsidian vault binding, write containment, bootstrap and migration tools.
