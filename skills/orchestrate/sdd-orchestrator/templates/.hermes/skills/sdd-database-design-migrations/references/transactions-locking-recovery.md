# Transactions, locking and recovery

## Transaction boundary

A transaction protects one declared consistency unit. Keep external network calls outside an open database transaction unless the project has a proven coordination protocol. State isolation assumptions and how concurrent writers detect conflicts.

## Lock analysis

For material DDL/DML, identify:

- lock type and acquisition point;
- rows/table/index touched;
- expected duration from real cardinality or a declared gap;
- effect on reads and writes;
- statement/lock timeout;
- cancellation and retry behavior.

Large operations need batching, online/concurrent database features, a maintenance window or a documented stop.

## Partial completion

For each step, define observable completion, safe rerun and next action after interruption. Multi-system consistency uses an explicit outbox, saga/compensation or reconciliation owner rather than pretending one local transaction covers everything.

## Recovery

Choose roll back or roll forward before execution. Record backup/snapshot requirements, recovery-time/data-loss objectives when supplied, irreversible points and verification after recovery. Never claim backup availability without testing restore evidence appropriate to the risk.

## Verification

Exercise concurrency or lock behavior when material, inject interruption into restartable work, and verify transaction rollback leaves no externally visible partial state.
