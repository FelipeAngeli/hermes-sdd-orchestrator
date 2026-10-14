# Relational databases

Model-specific layer for SQL databases. Shared rules live in `modeling-and-integrity.md`, `migrations-and-backfills.md`, `transactions-locking-recovery.md` and `query-and-index-evidence.md`; this file adds what differs by engine. Behavior below varies by engine and version: confirm each claim in the official documentation for the detected engine and version before relying on it.

## Identify the engine and version

Name the engine and version from repository evidence (driver, migration tool configuration, container image tag, infrastructure files, managed-service settings). Engine and version decide default isolation, which DDL is transactional, which index builds are online, and the syntax of plan analysis. When the version is unknown, say so and avoid version-specific advice.

## Modeling and normalization

- Normalize to remove update anomalies for data with one owner; each fact lives in one place. Denormalize only for a measured read need, and name the process that keeps the copy consistent.
- Declare primary keys, `NOT NULL`, unique, check and foreign key constraints where the database owns the invariant. A constraint the application cannot violate is still the cheapest proof under concurrency.
- Choose foreign key actions (`RESTRICT`, `CASCADE`, `SET NULL`) deliberately; a cascade is operational behavior that can delete far more than the triggering row.
- Index foreign key columns used in joins or parent deletes when the engine does not do it automatically; verify with the plan, not by habit.
- Use precise types: exact decimal for money, time zone semantics for timestamps, sized text only when the limit is a real rule.

## Transactions, isolation and concurrency

- Name the isolation level each transaction runs at. The default isolation level differs by engine (for example, PostgreSQL documents Read Committed as its default); read it from the engine documentation and the project's driver/session configuration.
- State which anomaly the code must prevent (lost update, write skew, phantom) and the mechanism: constraint, `SELECT ... FOR UPDATE`, optimistic version column, or a stricter isolation level.
- Stricter isolation can abort transactions with a serialization failure or deadlock; code that uses it must retry the whole transaction with a bound, and only when the transaction is safe to repeat.
- Do not raise the isolation level globally to fix one path; scope the change and measure contention.
- Keep transactions short and free of network calls to other systems.

## Migrations, locks and backfills

- Check which DDL takes locks that block reads or writes, and whether it rewrites the table, for the detected engine and version. Set lock and statement timeouts for migrations when the engine supports them.
- Use the engine's online/concurrent index build where available, and account for its restrictions (for example, some engines cannot run it inside a transaction block) and the cleanup needed after a failed build.
- Add a constraint in a not-yet-validated state and validate it after a backfill when the engine supports that split.
- Batch backfills by key range with a stable order; never update an unbounded table in one transaction.

## Query analysis

- Use the engine's plan tool (`EXPLAIN` and its analyze variant, or the engine's equivalent) on representative data, never an analyzed execution of an expensive or writing statement on shared production without authorization.
- Compare estimated and actual rows; a large gap points to statistics or predicate problems before it points to a missing index.
- Relate every index proposal to a named query, its frequency and its write cost.

## Recovery

Record backup and point-in-time recovery capability as configured, not as assumed. A migration that drops or narrows data is irreversible unless the source data is preserved.

## Verification

Engine and version are cited; every isolation, lock or index claim links to that engine's documentation or a recorded plan; concurrency-sensitive invariants have a test with concurrent writers against the real engine.
