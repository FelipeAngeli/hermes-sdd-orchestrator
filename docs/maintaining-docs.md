# Maintaining the docs

[Docs index](README.md) · Related: [Testing](components/testing.md), [CONTRIBUTING](../CONTRIBUTING.md)

**Rule: an orchestration change and its documentation ship in the same commit or pull request.** This rule is enforced by tooling, not left to memory.

## How it is enforced

| Layer | What it checks | Files |
| --- | --- | --- |
| Ownership map | Every orchestration file has exactly one owning page. | `docs/doc-map.json` |
| Content tests | Pages name their files. Links and anchors resolve. The page graph is connected. CLI flags, subcommands, statuses, decisions, actions, ecosystems and agent tables match the code. | `tests/test_docs.py` |
| Change check | A changed source file requires its owning page **and** `CHANGELOG.md` to change in the same diff. | `tools/check_docs_sync.py` |
| Local hook | Runs the change check on every commit. | `.githooks/commit-msg` |
| CI | Runs both test suites and, on pull requests, the change check against the base branch. | `.github/workflows/ci.yml` |

Enable the hook once per clone:

```bash
git config core.hooksPath .githooks
```

## `tools/check_docs_sync.py`

```text
python3 tools/check_docs_sync.py --staged [--message-file .git/COMMIT_EDITMSG]
python3 tools/check_docs_sync.py --base origin/main
```

| Flag | Meaning |
| --- | --- |
| `--staged` | Check staged changes (used by the hook). |
| `--base` | Check `<base>..HEAD` (used by CI). |
| `--message-file` | Commit message to read a waiver trailer from. |
| `--source-prefix` | Path prefix counted as orchestration source (repeatable). The defaults are `skills/`, `tools/`, `.githooks/` and `.github/workflows/`. |
| `--repo` | Repository root (default `.`). |

Exit codes: `0` in sync, `1` documentation missing (each missing page is listed), `2` usage or Git error.

Editing or deleting an **existing** test needs no documentation. Adding a test file does, because [Testing](components/testing.md) lists every suite. A rename counts as deleting the old path and adding the new one, so a source file moved into `tests/` still needs its page.

**Bootstrap.** On the first documentation PR, `docs/doc-map.json` may not exist in the base revision. The checker treats pre-diff ownership as empty in that one case; once the map exists, deleted and renamed paths always resolve their previous owner from the base map.

**Waiver.** A change with truly no documentation impact, such as a comment typo, may carry the trailer below. The reason is mandatory and reviewers see it in the history. A waiver covers only the files changed by the commit that carries it; a later waived release or metadata commit never excuses another commit in the range:

```text
Docs-Impact: none - fix typo in a comment
```

## Checklist for any orchestration change

1. Find the owning page in `docs/doc-map.json`.
2. Update that page so it describes the new behavior. Remove text about behavior that no longer exists.
3. For a new file, add it to `doc-map.json` under the right page and name it on that page.
4. For a new component, create `docs/components/<name>.md` with a link back to the [index](README.md) and to at least one related page, then add it to the index table and to `doc-map.json`.
5. Add an entry under `Unreleased` in `CHANGELOG.md`.
6. For a contract, schema or action change, bump the version in `SKILL.md`.
7. Run both test suites.

Hermes agents working in this repository follow the same rule; see `AGENTS.md` at the repository root.
