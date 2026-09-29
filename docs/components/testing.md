# Testing

[Docs index](../README.md) · Related: [Maintaining the docs](../maintaining-docs.md), [Contracts and schemas](contracts-and-schemas.md), [Skill and installer](skill-and-installer.md)

**Files:** `tests/*` (skill suite, at the repository root) and `orchestration/tests/*` (installed controller suite, shipped to every project).

Both suites use `unittest` and need no external services. The bounded-run tools additionally need `jsonschema`. CI runs both suites on every push and pull request (`.github/workflows/ci.yml`). Before the suites, CI checks out hermes-agent commit `4e9d3c713a3e3d47319ab18a8d8dfade5665270d` and requires the complete distributable skill to receive a `SAFE` verdict from `skills-guard-v5` through `tools/check_skill_security.py`.

The root suite is behavioral at its executable boundaries: installer, documentation-sync and release scenarios run their public CLIs against temporary Git repositories and assert observable files, output, commits and tags. Documentation and agent briefs are treated as published contracts; their tests assert metadata, catalogues, safety boundaries and decision concepts without importing production helpers or mirroring internal control flow.

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s skills/orchestrate/sdd-orchestrator/templates/.hermes/orchestration/tests -p 'test_*.py'
```

## Skill suite tests

| File | Guarantees |
| --- | --- |
| `tests/test_sdd_orchestrator_skill.py` | Public installer behavior in temporary Git repositories: read-only stack detection, scoped/resumable project onboarding, explicit TypeSafe install/opt-out and private credential-file planning/repair, opt-out independence from unrelated credential-file conflicts, vetted pinned-snapshot installation without Node.js or `npx`, prerequisite and credential-path conflict refusals, trusted provenance/content verification, unrelated-lock preservation, atomic multi-file installation and idempotency, complete byte-identical installation, initial state and journal, installed-suite execution, cache exclusion, exact bundled-skill exclusions that do not newly hide unrelated project skills, Git exclusions and refusals for non-root, tracked, conflicting, symlinked or partial destinations. Published bundle contracts cover layered payload structure, project-local engineering-skill frontmatter/references/portability, agent metadata and migration-auditor decision boundaries. |
| `tests/test_docs.py` | Live documentation behavior: complete ownership, resolvable links and anchors, graph reachability, public CLI help and subcommands, published runtime vocabulary, exact project-skill file catalogue and semantic agent catalogues. Black-box temporary-repository scenarios verify that `tools/check_docs_sync.py` accepts and rejects staged/range changes, waivers, merges, renames, bootstrap and deletion correctly. |
| `tests/test_versioning.py` | Release behavior through the public CLI only: dry-run immutability, SemVer inference, changelog rollover, commits, annotated tags, stable refusal codes and exact reviewed-HEAD protection in isolated Git repositories. Repository-level tests keep the published version, changelog order and contributor workflow consistent. |
| `tests/test_skill_security.py` | Public security-gate behavior: the configured scanner checkout and version are pinned, the source bytes executed are identical to the Git blob at that commit even when index flags hide worktree changes, and any verdict other than `SAFE` fails closed with an actionable result. CI supplies the real Hermes scanner; tests use an isolated deterministic scanner fixture. |

## Installed controller tests

These tests ship inside every project (`.hermes/orchestration/tests/`), so a target can verify its own copy.

| File | Covers |
| --- | --- |
| `tests/test_protocol.py` | Executor and review envelopes, schema selection, semantic rejection, analysis-only write scope, editable paths, cited evidence, TDD evidence ([Contracts](contracts-and-schemas.md)). |
| `tests/test_stage_context.py` | Schema-1 rejection/schema-2 contract, context budget, required project context, canonical live-Git root binding, exact project-local playbook/reference path, byte/hash/frontmatter and slice-requirement checks, reference-order-independent approval hashing, invalid-manifest approval refusal, slice editable paths, observable/independent verifiers and validator hand-off ([Harness](harness.md)). |
| `tests/test_hooks.py` | Shell-hook JSON transport, exception-safe fail-closed behavior, live-bound slice/vault scope, V4A multi-target paths, repository-local and vault-backed runtime resolution, evidence-gated HUMAN/AGENT completion, secret-safe structurally bounded STATE context and descriptor-safe immutable sub-agent audit events ([Hooks](hooks.md)). |
| `tests/test_correction_loop.py` | Every loop exit: verified, attempt/executor-call/cost limits, repeated hypothesis, unchanged evidence or change, escalation rules and the CLI ([Harness](harness.md)). |
| `tests/test_action_journal.py` | Journal lifecycle, atomic writes, rollover, archives, recovery decisions, corrective retry ([Action journal](action-journal.md)). |
| `tests/test_bounded_run_planner.py` | Plan determinism, classification, gate preconditions, schema 1 vs 2 authorization, the language-neutral action names and the legacy snapshot alias ([FSM](fsm-and-loop.md)). |
| `tests/test_bounded_run_driver.py` | `bind`/`next` decisions, budget stops, stale plans, recovery stops. |
| `tests/test_bounded_loop_driver.py` | Same-turn continuation and identity drift. |
| `tests/test_detect_stack.py` | Evidence-based detection for every ecosystem, lockfile-driven package managers, monorepo members, ignored directories and the CLI ([Gates](gates-and-stack-detection.md)). |
| `tests/test_obsidian_binding.py` | Binding validation, environment override, worktree slug stability ([Obsidian](obsidian-vault.md)). |
| `tests/test_obsidian_connector.py` | Official-CLI discovery/preflight, exact bound-vault selection, timeout/malformed-output fallback, Markdown-only canonical project paths including NUL refusal, project-scoped CLI/filesystem search, no-follow note reads, excluded runtime paths, symlink/TOCTOU refusal, tag isolation, denied lexical UI opening and machine-readable commands ([Obsidian](obsidian-vault.md)). |
| `tests/test_typesafe_connector.py` | Local-only credential preflight, descriptor-anchored nonblocking no-follow credential and input reads, malformed/symlinked/FIFO/insecure file refusal, exact whitespace-preserving key validation, secret non-disclosure, fixed non-redirecting System One endpoint and `jev-latest` request shape, 4 MiB/64-level strict finite JSON input refusal, bounded/depth/truncated response validation, HTTP-error closure, syntactic and semantic timeout checks, transport handling and documented HTTP status mapping ([Skill and installer](skill-and-installer.md#typesafejev-runtime-connector)). |
| `tests/test_vault_guard.py` | Write containment and baseline preservation. |
| `tests/test_bootstrap_worktree.py` | Worktree bootstrap preflight, conflicts and idempotency. |
| `tests/test_bootstrap_obsidian.py` | Bootstrap with a mandatory vault binding. |
| `tests/test_migrate_to_vault.py` | Copy-verify-remove migration and its refusals. |
| `tests/test_consolidate_runtime.py` | Deduplicating shared history without moving per-worktree runtime. |
| `tests/test_state_format.py` | Parsing and round-trip normalisation of the three STATE dialects. |

**Adding a test file:** add a row here. Editing an existing test needs no documentation change; see [Maintaining the docs](../maintaining-docs.md).
