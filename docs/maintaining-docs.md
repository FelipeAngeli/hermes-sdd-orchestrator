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
6. For a contract, schema or action change, use `### Added`, `### Changed` or `### Breaking` so [the release](#branches-and-versions) bumps the right level.
7. Run both test suites.

Hermes agents working in this repository follow the same rule; see `AGENTS.md` at the repository root.

## Branches and versions

**One improvement, one branch, one version.** Nothing is committed directly to `main`.

1. Create a branch from an up-to-date `main`. The prefix follows the Conventional Commit type:

   | Prefix | Use for | Typical level |
   | --- | --- | --- |
   | `feat/<topic>` | New capability | MINOR |
   | `fix/<topic>` | Bug fix | PATCH |
   | `docs/<topic>` | Documentation only | PATCH |
   | `refactor/<topic>`, `test/<topic>`, `chore/<topic>` | No behavior change | PATCH |

   ```bash
   git switch main && git pull --ff-only
   git switch -c feat/<topic>
   ```

2. Commit the change with its documentation and an entry under `## Unreleased` in `CHANGELOG.md`. The entry's heading sets the level: `### Breaking` or `### Removed` → **MAJOR**, `### Added` or `### Changed` → **MINOR**, anything else (`### Fixed`, `### Docs`…) → **PATCH**.
3. Cut the version on the same branch with `tools/release.py`:

   ```bash
   python3 tools/release.py              # dry run: shows 3.3.0 -> 3.4.0 (minor)
   python3 tools/release.py --apply      # bumps SKILL.md, rolls CHANGELOG, commits (no tag yet)
   ```

   | Flag | Meaning |
   | --- | --- |
   | `--apply` | Write and commit `chore(release): vX.Y.Z`. It deliberately does not tag before review. Without it the command is a dry run. |
   | `--tag` | After CI and `pr-reviewer` approve the exact release HEAD, verify it and create the annotated tag. |
   | `--level` | Force `major`, `minor` or `patch` instead of inferring it. |
   | `--date` | Release date `YYYY-MM-DD` (default today). |
   | `--repo` | Repository root (default `.`). |

   It refuses with exit code 1 on `main`/`master` (`BRANCH_NOT_ALLOWED`), a branch without an allowed prefix (`BRANCH_NAME_INVALID`), a dirty worktree (`WORKTREE_DIRTY`), an invalid `--date` (`DATE_INVALID`), an empty `Unreleased` section (`UNRELEASED_EMPTY`), a changed head after the release commit (`HEAD_NOT_RELEASE`) or an existing tag (`TAG_EXISTS`). It never pushes.

4. Push the branch and open a pull request to `main`. Run CI and `pr-reviewer` on the exact release commit.
5. Only after the verdict is `APPROVED`, tag that exact reviewed HEAD and push the tag; then merge:

   ```bash
   git push -u origin feat/<topic>
   gh pr create --fill
   # after CI + review approve the unchanged HEAD:
   python3 tools/release.py --tag
   git push origin vX.Y.Z
   ```

The version lives in one place: `version:` in `skills/orchestrate/sdd-orchestrator/SKILL.md`. `tests/test_versioning.py` checks that it is SemVer, that it equals the newest release in `CHANGELOG.md`, that `## Unreleased` stays on top and that release sections descend. Every version since `v3.3.0` has a matching Git tag.
