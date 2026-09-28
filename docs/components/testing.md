# Testing

[Docs index](../README.md) · Related: [Maintaining the docs](../maintaining-docs.md), [Contracts and schemas](contracts-and-schemas.md), [Skill and installer](skill-and-installer.md)

**Files:** `tests/*` (skill suite, at the repository root) and `orchestration/tests/*` (installed controller suite, shipped to every project).

Both suites use `unittest` and need no external services. The bounded-run tools additionally need `jsonschema`. CI runs both suites on every push and pull request (`.github/workflows/ci.yml`).

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s skills/orchestrate/sdd-orchestrator/templates/.hermes/orchestration/tests -p 'test_*.py'
```

## Skill suite tests

| File | Guarantees |
| --- | --- |
| `tests/test_sdd_orchestrator_skill.py` | Layered payload structure. Every stage agent and sub-agent has complete, controller-safe frontmatter. Role-specific rules for each audit brief. The README lists exactly the shipped sub-agents. The dispatch policy defaults to not dispatching. No legacy flat paths. `.hermes.md` stays compact and portable. The payload is not coupled to one language. The installer reports the detected stack and installs idempotently into a fixture repository with a clean `git status`. |
| `tests/test_docs.py` | Documentation stays true: every orchestration file has exactly one owning page in `docs/doc-map.json`, and that page names it. Every relative link resolves. Every page is reachable from `docs/README.md`, and every component page links back to the index and to a sibling. CLI flags, subcommands, journal statuses, driver decisions, planner actions and detected ecosystems extracted from code appear on their page. The sub-agent and stage-agent tables match each brief's frontmatter. `tools/check_docs_sync.py` blocks undocumented changes. |
| `tests/test_versioning.py` | Versioning stays consistent: `SKILL.md` has a SemVer version equal to the newest `CHANGELOG.md` release, `## Unreleased` stays on top, releases descend without duplicates. `tools/release.py` infers the level, bumps, rolls the changelog, commits and tags, and refuses on `main`, a dirty worktree, an empty section or an existing tag. |

## Installed controller tests

These tests ship inside every project (`.hermes/orchestration/tests/`), so a target can verify its own copy.

| File | Covers |
| --- | --- |
| `tests/test_protocol.py` | Executor and review envelopes, schema selection, semantic rejection, TDD evidence ([Contracts](contracts-and-schemas.md)). |
| `tests/test_action_journal.py` | Journal lifecycle, atomic writes, rollover, archives, recovery decisions, corrective retry ([Action journal](action-journal.md)). |
| `tests/test_bounded_run_planner.py` | Plan determinism, classification, gate preconditions, schema 1 vs 2 authorization, the language-neutral action names and the legacy snapshot alias ([FSM](fsm-and-loop.md)). |
| `tests/test_bounded_run_driver.py` | `bind`/`next` decisions, budget stops, stale plans, recovery stops. |
| `tests/test_bounded_loop_driver.py` | Same-turn continuation and identity drift. |
| `tests/test_detect_stack.py` | Evidence-based detection for every ecosystem, lockfile-driven package managers, monorepo members, ignored directories and the CLI ([Gates](gates-and-stack-detection.md)). |
| `tests/test_obsidian_binding.py` | Binding validation, environment override, worktree slug stability ([Obsidian](obsidian-vault.md)). |
| `tests/test_vault_guard.py` | Write containment and baseline preservation. |
| `tests/test_bootstrap_worktree.py` | Worktree bootstrap preflight, conflicts and idempotency. |
| `tests/test_bootstrap_obsidian.py` | Bootstrap with a mandatory vault binding. |
| `tests/test_migrate_to_vault.py` | Copy-verify-remove migration and its refusals. |
| `tests/test_consolidate_runtime.py` | Deduplicating shared history without moving per-worktree runtime. |
| `tests/test_state_format.py` | Parsing and round-trip normalisation of the three STATE dialects. |

**Adding a test file:** add a row here. Editing an existing test needs no documentation change; see [Maintaining the docs](../maintaining-docs.md).
