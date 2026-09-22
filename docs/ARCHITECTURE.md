# Architecture

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
        ├── contracts/        # worker and reviewer result contracts
        ├── policies/         # FSM, gates, recovery and bounded automation
        ├── runtime/          # deterministic executable controller tools
        ├── schemas/          # JSON Schema validation boundaries
        ├── tests/            # tests shipped with the installed controller
        └── README.md         # installed layout guide

tests/
└── test_sdd_orchestrator_skill.py  # packaging and installation acceptance tests
```

## Layer responsibilities

### Skill boundary

`SKILL.md` explains when and how Hermes should install and operate the controller. `scripts/install_project.py` owns installation preflight, conflict prevention, initial local state and Git exclusion. It copies the template without changing tracked files in the target repository.

### Contracts

`contracts/` defines the information exchanged with implementation workers and reviewers. Contracts describe transport, evidence and acceptance semantics; they do not execute actions or own state transitions.

### Policies

`policies/` defines controller governance: FSM order, budgets, recovery, gates and bounded automation. Policy documents are authoritative prose and must not contain executable state.

### Runtime

`runtime/` contains deterministic Python tools for planning, continuation decisions, journal recovery and result validation. Runtime modules may depend on `schemas/`; they must not import tests or mutate project source code.

### Schemas

`schemas/` contains machine-readable boundaries for plans, journals and executor/reviewer results. Runtime validation resolves schemas from this directory, avoiding implicit same-folder coupling.

### Tests

The installed `tests/` suite validates protocol behavior against the exact runtime and schemas delivered to target projects. Repository-level tests validate packaging, safe installation and the layered directory contract.

## Dependency direction

```text
.hermes.md / SKILL.md
        │
        ▼
     policies ─────► contracts
        │               │
        ▼               ▼
      runtime ─────────► schemas
        ▲
        │
      tests
```

State files (`STATE.md`, `ACTION_JOURNAL.json`, `INCIDENTS.md` and journal history) are created only during installation and remain at the orchestration root. They are runtime data, not source layers.

## Change rules

- Put executable controller behavior in `runtime/` and cover it in installed `tests/`.
- Put JSON validation shapes in `schemas/`; do not embed duplicate schemas in Python.
- Put governance prose in `policies/` and external worker interfaces in `contracts/`.
- Keep `.hermes.md` compact and reference the layered paths rather than duplicating policy text.
- Preserve installer idempotency and fail closed on tracked, partial, conflicting or symlinked destinations.
- Update this document, the installed layout README and acceptance tests when introducing a new layer.
