# Architecture

[Docs index](README.md) · [Overview](overview.md) · [Glossary](glossary.md)

Each layer below has a component page with full detail; the map of which page owns which file is [doc-map.json](doc-map.json).

## Purpose

This repository distributes one Hermes skill and a project-local SDD controller. The skill is the delivery boundary; the installed `.hermes/orchestration/` tree is the runtime boundary.

## Repository layout

```text
skills/orchestrate/sdd-orchestrator/
├── SKILL.md                  # Hermes discovery and operating procedure
├── scripts/
│   └── install_project.py    # safe, idempotent project installer
└── templates/
    ├── .hermes.md            # compact controller entry point
    └── .hermes/
        ├── skills/           # trusted project-local engineering playbooks
        └── orchestration/
            ├── agents/           # stage-specific leaf-worker briefs
            ├── contracts/        # worker and reviewer result contracts
            ├── hooks/            # opt-in Hermes event adapters
            ├── policies/         # FSM, gates, recovery and bounded automation
            ├── runtime/          # deterministic executable controller tools
            ├── schemas/          # JSON Schema validation boundaries
            ├── sub-agents/       # specialized leaf-worker briefs
            ├── tests/            # tests shipped with the installed controller
            └── README.md         # installed layout guide

tests/
└── test_sdd_orchestrator_skill.py  # packaging and installation acceptance tests
```

## Layer responsibilities

### Skill boundary

Details: [Skill and installer](components/skill-and-installer.md).

`SKILL.md` explains when and how Hermes should install and operate the controller. `scripts/install_project.py` owns installation preflight, conflict prevention, initial local state and Git exclusion. It copies the template without changing tracked files in the target repository.

### Project-local engineering skills

Details: [Project-local engineering skills](components/project-local-skills.md).

`templates/.hermes/skills/` contains reusable role and engineering procedure (product owner, tech lead, architecture decisions, API contracts, backend, frontend, database, TDD, release readiness) for every stage. These playbooks are trusted and discovered by Hermes at the project boundary; they do not dispatch, own state or replace evidence-driven specialist review. The controller records each required playbook descriptor in the stage manifest, and the harness binds it to slice approval.

### Agents

Details: [Stage agents](components/stage-agents.md).

`agents/` contains one leaf-worker brief per executable FSM stage: SPECIFY, CLARIFY, PLAN, TASKS, IMPLEMENT, TEST and REVIEW. Each brief narrows the mission, method, result schema and safety boundaries. Agents never own STATE, transitions or recursive dispatch.

### Contracts

Details: [Contracts and schemas](components/contracts-and-schemas.md).

`contracts/` defines the information exchanged with implementation workers and reviewers. Contracts describe transport, evidence and acceptance semantics; they do not execute actions or own state transitions.

### Hooks

Details: [Repository-local Hermes hooks](components/hooks.md).

`hooks/` is the opt-in event edge of the controller. Its scripts translate Hermes shell-hook JSON into calls to centralized `runtime/` policy; they do not duplicate FSM, path-matching, vault, STATE, journal, or acceptance rules. Installation copies the layer inactive, and activation remains an explicit dedicated-profile decision with checkout-bound consent. Hook adapters may depend on `runtime/`; runtime never depends on hook scripts. The layer observes or gates structured events but does not own STATE transitions, dispatch, profile configuration, or consent.

### Policies

Details: [FSM and bounded loop](components/fsm-and-loop.md), [Action journal](components/action-journal.md), [Gates and stack detection](components/gates-and-stack-detection.md), [Sub-agents and dispatch](components/sub-agents.md).

`policies/` defines controller governance: FSM order, budgets, recovery, gates and bounded automation. Policy documents are authoritative prose and must not contain executable state.

### Runtime

Details: [FSM and bounded loop](components/fsm-and-loop.md), [Harness](components/harness.md), [Action journal](components/action-journal.md), [Gates and stack detection](components/gates-and-stack-detection.md), [Obsidian vault](components/obsidian-vault.md).

`runtime/` contains deterministic Python tools plus narrowly scoped connectors. `terminal_progress.py` is a non-authoritative presentation edge while STATE/journal retain control. External access requires repository-local onboarding: TypeSafe installation alone authorizes no calls; only the exact enabled `--automatic-jev-governance` record plus READY local preflight lets `semantic_governor.py` invoke Jev automatically after deterministic precedence. It writes a durable uncertain tombstone before the paid boundary and returns non-success if final persistence cannot replace it. The semantic governor/cache and progress-state path guarantees use held POSIX directory descriptors and locks; on Windows those optional tools fail closed before state/network access rather than claiming junction-safe equivalence, while the installer and remaining controller stay supported. The governor batches and caches classifications while the FSM, permissions and final transitions remain deterministic. Runtime modules may depend on `schemas/`; they must not import tests or mutate project source code.

The runtime is language-neutral: gate actions are named by role (`TEST_FOCUSED`, `FORMAT_CHANGED_FILES`, `ANALYZE`), never by toolchain, and the only project-specific data are the commands in `policies/GATES.md`. `detect_stack.py` is the single place that knows ecosystems; it is read-only and suggests commands only from manifest, lockfile or tool-configuration evidence. The legacy snapshot field `changed_dart_files_available` is still accepted as an alias of `changed_files_available` so older installations keep validating.

### Schemas

Details: [Contracts and schemas](components/contracts-and-schemas.md).

`schemas/` contains machine-readable boundaries for plans, journals and executor/reviewer results. Runtime validation resolves schemas from this directory, avoiding implicit same-folder coupling.

### Sub-agents

Details: [Sub-agents and dispatch](components/sub-agents.md).

`sub-agents/` contains four leaf-worker briefs: project context, bounded data-flow/impact investigation, pull-request review and security review. They are dispatched directly by the controller, never by another agent, and do not own STATE or transitions. Product-owner, tech-lead, API-contract, migration-safety, TDD and release-readiness judgment are project-local playbooks the stage worker loads itself.

Six of them return findings only when evidence supports them. The TDD guardian decides whether a suite actually tests behavior by mutating production code and observing which tests stay green; the regression hunter decides whether a change broke existing behavior by running the suites of the consumers the change did not touch; the API contract auditor compares client models against the published specification and the deployed server, ranking the sources rather than choosing the convenient one; the performance auditor reports a cost only with a measurement or a counted operation behind it, and may conclude that nothing is worth changing; the architecture guardian reports a violation only by quoting the project's own declared rule; and the migration safety auditor reconstructs rollout order, mixed-version states, data conversion, locks, interruption and recovery instead of treating generated SQL as proof. All six keep the workspace read-only, repair nothing, and separate proven findings from unproven suspicions.

The documentation writer is the exception that writes, and its risk runs the other way. A read-only auditor produces a wrong finding that review can reject; a writer produces fluent prose describing code that does not exist, which readers trust because it reads well. It therefore verifies every symbol, command and path against the repository before writing it, deletes documentation whose subject is gone, and records an unexplained decision as an open question rather than inventing a rationale. Like the implementer, it writes only to paths the controller assigns, and it never edits code to match the text.

### Tests

Details: [Testing](components/testing.md).

The installed `tests/` suite validates protocol behavior against the exact runtime and schemas delivered to target projects. Repository-level tests validate packaging, safe installation and the layered directory contract.

## Dependency direction

```text
project skills ──scoped briefing──► agents
.hermes.md / SKILL.md ─────► agents ─────► contracts ─────────► schemas
          │                    │                                  ▲
          └────► hooks ────────┼──────────────┐                   │
                               ▼              ▼                   │
                            policies ───────► runtime ─────────────┘
                                                ▲
                                                │
                                              tests
```

`PROJECT_SETUP.md` is created during installation and remains at the local orchestration root. It gates the first demand until orchestrator-only connectivity questions are resolved. Runtime files (`STATE.md`, `ACTION_JOURNAL.json`, `INCIDENTS.md` and journal history) are initially created there too; an Obsidian-bound worktree relocates its runtime files to the vault as documented in [Obsidian vault](components/obsidian-vault.md).

## Change rules

- Put stage-specific worker instructions in `agents/`; keep controller authority out of them.
- Put reusable engineering procedure in project `skills/`, load references progressively, and bind required playbooks to slices; do not add a sub-agent where a playbook suffices.
- Put executable controller behavior in `runtime/` and cover it in installed `tests/`.
- Keep `hooks/` as thin opt-in event adapters over `runtime/`; they never own policy, STATE transitions, profile configuration or consent.
- Put JSON validation shapes in `schemas/`; do not embed duplicate schemas in Python.
- Put reusable specialist roles in `sub-agents/`; keep them leaf-only and controller-dispatched.
- Put governance prose in `policies/` and external worker interfaces in `contracts/`.
- Keep `.hermes.md` compact and reference the layered paths rather than duplicating policy text.
- Preserve installer idempotency and fail closed on tracked, partial, conflicting or symlinked destinations.
- Update this document, the installed layout README and acceptance tests when introducing a new layer.
- Update the owning documentation page, `docs/doc-map.json` and `CHANGELOG.md` in the same change; see [Maintaining the docs](maintaining-docs.md).
- Never name a language, framework or toolchain in an action, schema field, contract or brief. Ecosystem knowledge belongs only in `runtime/detect_stack.py` and the reference table of `policies/GATES.md`; `test_controller_contracts_do_not_assume_a_specific_toolchain` enforces this.
