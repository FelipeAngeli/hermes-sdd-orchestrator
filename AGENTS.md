# Agent instructions

These rules apply to any agent (Hermes, Claude Code, Codex…) that changes this repository.

## One improvement, one branch, one version

Never commit to `main`. For every improvement:

1. `git switch main && git pull --ff-only && git switch -c <type>/<topic>`, with `<type>` one of `feat`, `fix`, `docs`, `refactor`, `test`, `chore`.
2. Commit the change with its documentation and a `CHANGELOG.md` entry under `## Unreleased` (see below).
3. `python3 tools/release.py --apply` on that branch. It bumps `SKILL.md`, rolls the changelog and commits `chore(release): vX.Y.Z` without tagging (MAJOR for `### Breaking`/`### Removed`, MINOR for `### Added`/`### Changed`, otherwise PATCH).
4. Push the branch and open a pull request. After CI and `pr-reviewer` approve that exact HEAD, run `python3 tools/release.py --tag` and push the tag. Merge only then.

Details: [docs/maintaining-docs.md#branches-and-versions](docs/maintaining-docs.md#branches-and-versions).

## Jev decides semantic classifications

Every semantic classification an agent makes while changing this repository goes through Jev: the risk of a change (`LOW`…`CRITICAL`), the branch `<type>`, whether a change is `### Added`, `### Changed`, `### Breaking` or `### Fixed`, and which specialist reviewer a pull request needs. Deterministic facts (Git state, test results, schema validity, the version computed by `tools/release.py`) never go to Jev.

1. Inputs live in the ignored `.hermes-dev/` directory: `PROJECT_SETUP.md` records the owner's automatic consent, plus the request JSON and `JEV_CACHE.json`. The key comes from `.env` (`TYPESAFE_API_KEY`, or `JEV_AI_API_KEY` with `--provider jev-ai`) or the environment.
2. Run `python3 skills/orchestrate/sdd-orchestrator/templates/.hermes/orchestration/runtime/typesafe_connector.py preflight --env-file .env --json`. If it is not `READY`, stop and report the blocker; do not classify in its place.
3. Batch every question for the change into one request and run `semantic_governor.py decide --input .hermes-dev/<ticket>.json --project-setup .hermes-dev/PROJECT_SETUP.md --cache .hermes-dev/JEV_CACHE.json --env-file .env --json`.
4. Use only answers at confidence `0.70` or higher. Anything returned as `REVIEW` goes to the user; never substitute your own classification.
5. Announce every use in the reply with a **JEV USADO** block: script, number of files evaluated, decided versus review, and the fingerprint artifact.

## Pull request review

Every pull request in this repository is reviewed with the `pr-reviewer` sub-agent, and so is every review a pull request has already received. There are no exceptions for small or documentation-only changes.

1. Load `skills/orchestrate/sdd-orchestrator/templates/.hermes/orchestration/sub-agents/pr-reviewer.md` as the reviewer's brief.
2. Read the PR's real base before building the diff: `BASE=$(gh pr view <n> --json baseRefName --jq .baseRefName)`, `git fetch origin "$BASE"`, then `git diff $(git merge-base "origin/$BASE" HEAD)..HEAD`. Give the reviewer that full diff, the commits, PR description, existing reviews and checks from `gh pr view <n> --json title,body,commits,reviews,comments,statusCheckRollup,baseRefName,headRefName,headRefOid`. Never assume the base is `main`; stacked PRs have a different base.
3. The result is a `review_result` (see `docs/components/contracts-and-schemas.md`). Report it to the user. Post it to GitHub only when the user asks.
4. When it names a specialist (`security-reviewer`, `dependency-auditor`…), run that sub-agent next only if a pending merge decision depends on its answer.
5. Merge only after the verdict is `APPROVED` and the user authorizes the merge.

## Documentation is part of every orchestration change

Documentation lives in [docs/README.md](docs/README.md). Each orchestration file has exactly one owning page, listed in [docs/doc-map.json](docs/doc-map.json).

When you change anything under `skills/`, `tools/`, `.githooks/` or `.github/workflows/`, do all of the following in the **same** commit:

1. Update the owning page so it describes the behavior as it is now. Delete text about behavior that no longer exists.
2. Register a new file in `docs/doc-map.json` and name it on its page.
3. Add an entry under `Unreleased` in `CHANGELOG.md`.
4. For a contract, schema, FSM action or CLI change, record it under `### Added`, `### Changed` or `### Breaking` so the release gets the right level; `tools/release.py` bumps the version in `skills/orchestrate/sdd-orchestrator/SKILL.md`.
5. Run both test suites. `tests/test_docs.py` fails when a page is out of date:

   ```bash
   python3 -m unittest discover -s tests -p 'test_*.py'
   python3 -m unittest discover -s skills/orchestrate/sdd-orchestrator/templates/.hermes/orchestration/tests -p 'test_*.py'
   python3 tools/check_docs_sync.py --staged
   ```

Never document behavior you have not verified in the code. When the code and a page disagree, the code is the source of truth: fix the page and say so in the changelog entry.

The full procedure is in [docs/maintaining-docs.md](docs/maintaining-docs.md). Project conventions are in [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
