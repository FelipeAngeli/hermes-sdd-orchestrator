---
name: sdd-database-design-migrations
description: Guide safe database design, migrations, and recovery.
version: 0.1.0
author: Felipe Angeli (FelipeAngeli), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [database, schema, migrations, transactions, recovery]
    related_skills: [sdd-backend-engineering, sdd-architecture-decisions]
---

# SDD Database Design and Migrations

Project-local playbook for persistence changes that must preserve data and mixed-version application behavior. The actual database, migration framework, project scripts and operational policy remain authoritative.

## When to Use

Use when a slice changes tables, columns, types, defaults, nullability, constraints, relationships, indexes, stored data, transaction boundaries or migration/rollback behavior.

Do not use for read-only query analysis with no design change, non-persistent data, or production execution without explicit authorization.

## Prerequisites

- Identify the database and migration mechanism from repository evidence.
- Inspect current schema, migration history, representative existing-data states and deployment topology.
- Confirm whether old and new application versions overlap during rollout.
- Name the recovery objective before choosing an irreversible operation.

## Reference Routing

| Concern | Read |
| --- | --- |
| Tables, keys, nullability, constraints and data ownership | `references/modeling-and-integrity.md` |
| Expand/contract, backfills, ordering and mixed versions | `references/migrations-and-backfills.md` |
| Transactions, isolation, locks, rollback and recovery | `references/transactions-locking-recovery.md` |
| Query plans, indexes, pagination and measured performance | `references/query-and-index-evidence.md` |

## Procedure

1. **Classify the change.** Mark it schema-only, data-only, data+schema or operational; identify destructive and table-rewrite operations. Done when risk is tied to concrete DDL/DML.
2. **State invariants and compatibility.** Define valid old/new rows, application versions that may coexist and the owner of each invariant. Done when null/default/constraint semantics are unambiguous.
3. **Design rollout order.** Prefer expand → deploy compatible code → bounded backfill → validate → contract. Separate data normalization from constraint tightening. Done when every step can run against the preceding deployed state.
4. **Bound operational impact.** Evaluate row count, lock mode/duration, transaction size, replication/log growth and retry/resume behavior using real evidence or explicit unknowns. Done when production impact is measured or blocked as unknown.
5. **Design recovery.** State rollback/roll-forward conditions, backup or snapshot needs, irreversible points and what happens after partial completion. Done when interruption at each step has a safe next action.
6. **Verify both histories.** Exercise a fresh database and an existing-data upgrade with project-owned migration and application checks. Add query-plan evidence only for changed access paths.
7. **Prepare independent audit.** When rollout can lose data, block mixed versions or exceed downtime limits, route the exact plan and evidence to `migration-safety-auditor`.

## Pitfalls

- Do not tighten a constraint before existing rows satisfy it.
- Do not treat ORM generation success as proof of safe rollout.
- Do not combine an unbounded backfill and blocking DDL without an operational reason.
- Do not promise rollback after destructive data conversion without preserved source data.
- Do not recommend an index without the target query and measured plan.
- Do not run shared or production migrations without explicit authorization and readback.

## Verification

- Fresh-install and existing-data migration paths both pass.
- Mixed-version compatibility is proven or explicitly not required.
- Backfills are bounded, restartable or idempotent where interruption is possible.
- Lock, transaction and recovery evidence is recorded for material operations.
- Every destructive step has explicit authorization and a verified recovery decision.
