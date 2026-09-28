# Agent instructions

These rules apply to any agent (Hermes, Claude Code, Codex…) that changes this repository.

## Documentation is part of every orchestration change

Documentation lives in [docs/README.md](docs/README.md). Each orchestration file has exactly one owning page, listed in [docs/doc-map.json](docs/doc-map.json).

When you change anything under `skills/`, `tools/`, `.githooks/` or `.github/workflows/`, do all of the following in the **same** commit:

1. Update the owning page so it describes the behavior as it is now. Delete text about behavior that no longer exists.
2. Register a new file in `docs/doc-map.json` and name it on its page.
3. Add an entry under `Unreleased` in `CHANGELOG.md`.
4. For a change to a contract, schema, FSM action or CLI, bump the version in `skills/orchestrate/sdd-orchestrator/SKILL.md`.
5. Run both test suites. `tests/test_docs.py` fails when a page is out of date:

   ```bash
   python3 -m unittest discover -s tests -p 'test_*.py'
   python3 -m unittest discover -s skills/orchestrate/sdd-orchestrator/templates/.hermes/orchestration/tests -p 'test_*.py'
   python3 tools/check_docs_sync.py --staged
   ```

Never document behavior you have not verified in the code. When the code and a page disagree, the code is the source of truth: fix the page and say so in the changelog entry.

The full procedure is in [docs/maintaining-docs.md](docs/maintaining-docs.md). Project conventions are in [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
