# Migrations and backfills

## Expand/contract

For mixed-version rollout:

1. expand schema with backward-compatible nullable/new structures;
2. deploy code that reads old and new, writing the transition-safe form;
3. backfill in bounded, resumable batches;
4. validate data and observability;
5. switch reads/authority;
6. remove compatibility behavior and contract schema later.

State when this sequence is unnecessary and why.

## Ordering

Normalize existing rows before adding stricter null, enum, check, unique or foreign-key constraints. Build supporting indexes before constraints/queries depend on them when the database permits. Separate DML from blocking DDL unless atomicity outweighs operational risk with evidence.

## Backfills

Define selection predicate, stable batching order, batch/transaction size, idempotency or checkpoint, concurrency with live writes, throttling, retry/resume and completion query. Avoid OFFSET over a changing set. A backfill that can overwrite newer application data needs compare-and-set or another ownership rule.

## Destructive changes

Renames, narrowing types, dropped values/columns and transformations need source preservation or an explicit irreversible point. “Rollback migration” cannot restore discarded information without a retained source.

## Verification

Run migration from the oldest supported schema/data state and from empty. Assert postconditions with queries independent of migration code. Verify old/new application compatibility when rollout overlaps versions.
