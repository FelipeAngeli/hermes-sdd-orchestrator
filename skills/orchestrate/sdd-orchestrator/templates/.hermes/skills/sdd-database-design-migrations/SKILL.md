---
name: sdd-database-design-migrations
description: Guide safe database design, migrations, and recovery.
version: 0.2.0
author: Felipe Angeli (FelipeAngeli), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [database, schema, migrations, transactions, recovery, relational, nosql]
    related_skills: [sdd-backend-engineering, sdd-architecture-decisions]
---

# SDD Database Design and Migrations

Project-local playbook for persistence changes that must preserve data and mixed-version application behavior. The actual database engine and version, migration mechanism, project scripts and operational policy remain authoritative. Shared guidance applies to every data model; model-specific references add what each model actually guarantees.

## When to Use

Use when a slice changes tables/collections, columns/fields, types, defaults, nullability, constraints or validation rules, relationships, indexes, stored data, transaction boundaries or migration/rollback behavior, in a relational or NoSQL database.

Do not use for read-only query analysis with no design change, non-persistent data, or production execution without explicit authorization.

## Prerequisites

- Identify the data model, engine, version and hosting (self-managed or managed service) from repository evidence: drivers, migration files, configuration, container or infrastructure files. Unknown version is recorded as a gap.
- Inspect current schema or validation rules, migration history, representative existing data and deployment topology.
- Confirm whether old and new application versions overlap during rollout.
- Name the recovery objective before choosing an irreversible operation.

## Reference Routing

| Concern | Read |
| --- | --- |
| Relational: isolation levels, normalization, keys, constraints, plans | `references/relational-databases.md` |
| Document: access patterns, embed vs reference, validation, size limits, transactions, sharding | `references/document-databases.md` |
| Key-value, wide-column, search and other NoSQL models | `references/other-nosql-models.md` |
| Ownership, keys, null/default semantics, units (all models) | `references/modeling-and-integrity.md` |
| Expand/contract, backfills, ordering, mixed versions (all models) | `references/migrations-and-backfills.md` |
| Transactions, locks, rollback and recovery | `references/transactions-locking-recovery.md` |
| Query plans, indexes, pagination and measured performance | `references/query-and-index-evidence.md` |
| Ordered rollout, mixed versions, restartability and recovery audit | `references/rollout-safety-audit.md` |

## Procedure

1. **Identify the data model.** Classify it as relational, document, key-value, wide-column, search or another model, then load the matching reference. Done when engine and version are named or recorded as unknown.
2. **Classify the change.** Mark it schema-only, data-only, data+schema or operational; identify destructive, rewriting and long-running operations. Done when risk is tied to concrete statements or operations.
3. **State invariants and compatibility.** Define valid old/new records, application versions that may coexist and the owner of each invariant (database constraint, validator or application). Done when null/missing/default semantics are unambiguous.
4. **Design rollout order.** Prefer expand → deploy compatible code → bounded backfill → validate → contract. Separate data normalization from constraint tightening. Done when every step can run against the preceding deployed state.
5. **Bound operational impact.** Evaluate record count, lock or contention behavior, transaction size, replication/log growth and retry/resume using real evidence or explicit unknowns. Done when impact is measured or blocked as unknown.
6. **Design recovery.** State rollback/roll-forward conditions, backup needs, irreversible points and partial-completion handling. Done when interruption at each step has a safe next action.
7. **Verify both histories.** Exercise an empty database and an existing-data upgrade with project-owned checks. Add plan evidence only for changed access paths.
8. **Audit rollout safety.** When rollout can lose data, block mixed versions or exceed downtime limits, apply `references/rollout-safety-audit.md` in PLAN and again in REVIEW.

## Pitfalls

- Do not apply relational patterns to a NoSQL model, or assume NoSQL means no schema or no transactions.
- Do not state an engine guarantee, limit or default without the official documentation for the detected version.
- Do not tighten a constraint or validator before existing data satisfies it.
- Do not treat ORM/ODM generation success as proof of safe rollout.
- Do not combine an unbounded backfill and blocking DDL, or promise rollback after destructive conversion without preserved source data.
- Do not recommend an index without the target query and measured plan.
- Do not run shared or production migrations without explicit authorization and readback.

## Verification

- Data model, engine and version are named; model-specific guarantees cite official documentation.
- Empty-database and existing-data paths both pass; mixed-version compatibility is proven or not required.
- Backfills are bounded, restartable or idempotent; lock, transaction and recovery evidence is recorded for material operations.
- Every destructive step has explicit authorization and a verified recovery decision.
