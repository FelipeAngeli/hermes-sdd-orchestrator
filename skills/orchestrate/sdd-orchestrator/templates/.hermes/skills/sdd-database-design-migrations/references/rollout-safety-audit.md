# Rollout safety audit

Decide whether one schema/data migration can roll out without data loss, mixed-version incompatibility, unbounded locking or unrecoverable partial state. Treat mixed-version compatibility as an explicit rollout contract. This audit fails by turning every persistence edit into a database ceremony: apply it when a PLAN or REVIEW decision depends on migration safety and deterministic schema diff, SQL inspection and tests did not answer the operational judgment.

## Method

1. Establish the authoritative database, migration mechanism, deployment topology, supported source schema/data state and whether old and new application versions overlap.
2. Reconstruct the ordered rollout: expansion, compatible application deployment, backfill, validation, authority switch and contraction. Do not review files independently of execution order.
3. Compare every application version that may run against every schema state it may observe; report incompatible reads, writes, defaults, nullability, enums and removed fields.
4. Trace existing rows through each DML/DDL step: backfill predicate, batching, idempotency or checkpoint, live-write races and independent postcondition queries.
5. Evaluate material locks, rewrites, index builds, transaction/log growth and timeout/cancellation from database evidence. Unknown cardinality or lock behavior is a gap, not a safe estimate.
6. Walk interruption and recovery at every step: irreversible points, preserved source data, backup/restore evidence, and whether rollback or roll-forward is actually possible.
7. Report the smallest correction or missing evidence the pending decision needs.

## Review surfaces

- Ordering: data satisfies a tighter constraint before validation or enforcement.
- Compatibility: old and new binaries coexist with every intermediate schema when rollout overlaps.
- Integrity: keys, relationships, null/default semantics and tenant boundaries remain valid.
- Data conversion: narrowing, renaming, enum/status mapping, deduplication and precision/time/unit changes preserve meaning.
- Operational safety: lock scope and duration, table rewrite, transaction size, replication/log impact, throttling and maintenance window.
- Restartability: backfill and migration resume or safely detect completion after interruption.
- Recovery: rollback preserves information, or roll-forward has a bounded tested path.
- Verification: fresh-install and existing-data upgrades use project-owned commands and independent postconditions.

## Evidence rules

- Cite migration files, schema definitions, application readers/writers, deployment policy and command results for every finding.
- Separate proven defects from unknown production properties and other unproven suspicions; an unknown that blocks safe rollout is a blocker, not an inferred defect.
- Do not infer lock duration, row count, backup availability or rollback from syntax alone.
- A generated migration or green ORM check proves syntax, not mixed-version or data safety.
- Distinguish risk introduced by the change from inherited data debt; return `NO_FINDINGS` when the rollout and evidence answer the decision safely.
- Never execute a migration, backfill, destructive query, lock experiment or shared-environment benchmark during the audit.

## Neighbouring judgments

- Query latency or index value backed by measurement → `sdd-tech-lead` (`references/performance.md`).
- Persistence-layer dependency violation → `sdd-tech-lead` (`references/architecture-compliance.md`).
- API or model wire divergence → `sdd-api-contracts`.
- Credential, authorization or disclosure issue → the `security-reviewer` sub-agent.
