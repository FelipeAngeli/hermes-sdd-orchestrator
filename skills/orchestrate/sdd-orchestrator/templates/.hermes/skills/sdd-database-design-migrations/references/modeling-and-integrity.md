# Modeling and integrity

## Ownership

Model data around authoritative ownership and invariants, not screen shape. Record each table/entity's identity, lifecycle, tenant scope and deletion policy.

## Keys and relationships

- Prefer stable surrogate or natural keys according to project convention; document externally visible identity separately.
- Enforce required relationships and uniqueness in the database when it owns the invariant.
- Define cascade/restrict/set-null behavior deliberately; implicit deletion chains are operational behavior.
- Keep denormalization tied to a measured read need and define its synchronization owner.

## Null, default and state

Null means one explicit thing; distinguish unknown, not applicable, not yet supplied and empty when behavior differs. A default affects omitted writes, not existing rows unless a backfill says so. State/status fields define allowed transitions and obsolete values before constraints tighten.

## Money, time and precision

Store units explicitly. Preserve decimal precision and currency. Record timestamps with timezone semantics and define whether they represent event, receipt or processing time.

## Integrity verification

Use database constraints plus application behavior where each is authoritative. Tests cover duplicate keys, missing relationships, invalid transitions, tenant isolation and deletion behavior with a real database when adapter behavior matters.
