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
    └── .hermes/orchestration/
        ├── agents/           # stage-specific leaf-worker briefs
        ├── contracts/        # worker and reviewer result contracts
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

### Agents

Details: [Stage agents](components/stage-agents.md).

`agents/` contains one leaf-worker brief per executable FSM stage: SPECIFY, CLARIFY, PLAN, TASKS, IMPLEMENT, TEST and REVIEW. Each brief narrows the mission, method, result schema and safety boundaries. Agents never own STATE, transitions or recursive dispatch.

### Contracts

Details: [Contracts and schemas](components/contracts-and-schemas.md).

`contracts/` defines the information exchanged with implementation workers and reviewers. Contracts describe transport, evidence and acceptance semantics; they do not execute actions or own state transitions.

### Policies

Details: [FSM and bounded loop](components/fsm-and-loop.md), [Action journal](components/action-journal.md), [Gates and stack detection](components/gates-and-stack-detection.md), [Sub-agents and dispatch](components/sub-agents.md).

`policies/` defines controller governance: FSM order, budgets, recovery, gates and bounded automation. Policy documents are authoritative prose and must not contain executable state.

### Runtime

Details: [FSM and bounded loop](components/fsm-and-loop.md), [Action journal](components/action-journal.md), [Gates and stack detection](components/gates-and-stack-detection.md), [Obsidian vault](components/obsidian-vault.md).

`runtime/` contains deterministic Python tools for planning, continuation decisions, journal recovery, result validation and stack detection. Runtime modules may depend on `schemas/`; they must not import tests or mutate project source code.

The runtime is language-neutral: gate actions are named by role (`TEST_FOCUSED`, `FORMAT_CHANGED_FILES`, `ANALYZE`), never by toolchain, and the only project-specific data are the commands in `policies/GATES.md`. `detect_stack.py` is the single place that knows ecosystems; it is read-only and suggests commands only from manifest, lockfile or tool-configuration evidence. The legacy snapshot field `changed_dart_files_available` is still accepted as an alias of `changed_files_available` so older installations keep validating.

### Schemas

Details: [Contracts and schemas](components/contracts-and-schemas.md).

`schemas/` contains machine-readable boundaries for plans, journals and executor/reviewer results. Runtime validation resolves schemas from this directory, avoiding implicit same-folder coupling.

### Sub-agents

Details: [Sub-agents and dispatch](components/sub-agents.md).

`sub-agents/` contains specialized leaf-worker briefs for investigation, impact analysis, TDD implementation, focused test execution, security review, code review, test-suite auditing, regression hunting, API contract auditing, performance auditing, documentation maintenance and architectural conformance. They are dispatched directly by the controller, never by another agent, and do not own STATE or transitions.

Five of them return findings only when evidence supports them. The TDD guardian decides whether a suite actually tests behavior by mutating production code and observing which tests stay green; the regression hunter decides whether a change broke existing behavior by running the suites of the consumers the change did not touch; the API contract auditor compares client models against the published specification and the deployed server, ranking the sources rather than choosing the convenient one; the performance auditor reports a cost only with a measurement or a counted operation behind it, and may conclude that nothing is worth changing; the architecture guardian reports a violation only by quoting the project's own declared rule, since every codebase violates someone's preferred architecture and a guardian reasoning from general principle would rewrite deliberate choices as defects. All five keep the workspace read-only, revert every temporary step, repair nothing, and separate proven findings from unproven suspicions — a suspicion presented as proof is worse than no report, because the controller cannot act on it.

The documentation writer is the exception that writes, and its risk runs the other way. A read-only auditor produces a wrong finding that review can reject; a writer produces fluent prose describing code that does not exist, which readers trust because it reads well. It therefore verifies every symbol, command and path against the repository before writing it, deletes documentation whose subject is gone, and records an unexplained decision as an open question rather than inventing a rationale. Like the implementer, it writes only to paths the controller assigns, and it never edits code to match the text.

### Tests

Details: [Testing](components/testing.md).

The installed `tests/` suite validates protocol behavior against the exact runtime and schemas delivered to target projects. Repository-level tests validate packaging, safe installation and the layered directory contract.

## Dependency direction

```text
.hermes.md / SKILL.md
        │
        ▼
      agents ─────────► contracts
        │                  │
        ▼                  │
     policies              │
        │                  │
        ▼                  ▼
      runtime ───────────► schemas
        ▲
        │
      tests
```

`PROJECT_SETUP.md` is created during installation and remains at the local orchestration root. It gates the first demand until orchestrator-only connectivity questions are resolved. Runtime files (`STATE.md`, `ACTION_JOURNAL.json`, `INCIDENTS.md` and journal history) are initially created there too; an Obsidian-bound worktree relocates its runtime files to the vault as documented in [Obsidian vault](components/obsidian-vault.md).

## Change rules

- Put stage-specific worker instructions in `agents/`; keep controller authority out of them.
- Put executable controller behavior in `runtime/` and cover it in installed `tests/`.
- Put JSON validation shapes in `schemas/`; do not embed duplicate schemas in Python.
- Put reusable specialist roles in `sub-agents/`; keep them leaf-only and controller-dispatched.
- Put governance prose in `policies/` and external worker interfaces in `contracts/`.
- Keep `.hermes.md` compact and reference the layered paths rather than duplicating policy text.
- Preserve installer idempotency and fail closed on tracked, partial, conflicting or symlinked destinations.
- Update this document, the installed layout README and acceptance tests when introducing a new layer.
- Update the owning documentation page, `docs/doc-map.json` and `CHANGELOG.md` in the same change; see [Maintaining the docs](maintaining-docs.md).
- Never name a language, framework or toolchain in an action, schema field, contract or brief. Ecosystem knowledge belongs only in `runtime/detect_stack.py` and the reference table of `policies/GATES.md`; `test_controller_contracts_do_not_assume_a_specific_toolchain` enforces this.
