# Document databases

Model-specific layer for document stores (for example MongoDB, Couchbase, Amazon DocumentDB, Azure Cosmos DB, Firestore). Products share a document model but differ in transactions, consistency, limits, indexing and validation; do not assume MongoDB semantics for another product, and do not assume a MongoDB-compatible API implements every MongoDB feature. Confirm each guarantee in the official documentation for the detected product and version.

## Model for access patterns

- List the application's access patterns first: each query and write, its filter, sort, projection, frequency, latency need and result size. The document shape follows from them, not from a normalized entity diagram.
- Store together what is read together and written with the same lifecycle.
- Name the single document that each atomic update must touch; in many products a single-document write is atomic while multi-document writes need an explicit transaction or are not atomic at all.

## Embed or reference

Embed when the child data:

- is read with the parent and owned by it;
- is bounded in count and size;
- does not need to be queried or updated independently at high frequency.

Reference when the child data:

- is unbounded or grows without limit (comments, events, logs);
- is shared by many parents or updated independently;
- would push the document toward the product's size or nesting limits.

Never design an unbounded array inside one document. Record the expected maximum cardinality for every embedded array.

## Duplication and consistency

- Duplicating fields for read performance is legitimate; name the source of truth, the copies, who updates them, and the tolerated staleness.
- Update copies in the same transaction when the product supports it and the invariant requires it; otherwise use an asynchronous process with retry and a reconciliation check.
- Uniqueness across documents requires a unique index (or the product's equivalent); application checks alone race.

## Schema validation and document evolution

- Document databases are not schema-less in practice; the schema lives in validators, in code, or both. Use the product's validation feature when available (for example MongoDB `$jsonSchema` validators with configurable `validationLevel` and `validationAction`), and keep it consistent with application models.
- Add a schema version field when document shapes change; readers accept every version present in storage and in the rollout.
- Tighten validation only after existing documents conform; some products let validation apply only to new or already-valid documents, which is a transition tool, not a substitute for backfilling.
- Treat a missing field, a `null` field and an empty value as distinct states when behavior differs.

## Indexes, aggregations and limits

- Every frequent query has a supporting index; check the plan with the product's explain tool. Compound index field order follows equality, sort, then range in MongoDB-style engines; verify the rule for the detected product.
- Count index write and storage cost; avoid indexes no query uses.
- Respect hard limits from the product documentation: maximum document size or item size (for example, 16 MiB per BSON document in MongoDB and 400 KB per item in DynamoDB), nesting depth, index count and key size. Design to stay well below them.
- Aggregation and query engines can impose memory or execution limits (for example, MongoDB limits memory per pipeline stage and spills to disk only when allowed by the version and settings); check the detected product's limits, and filter and project early.

## Transactions and consistency

- State the guarantees actually used: single-document atomicity, multi-document transactions (availability depends on product, version and deployment, such as replica set or sharded cluster), read/write concern or consistency level.
- Multi-document transactions have product-specific runtime and size limits and cost; prefer a model where the invariant fits one document when that is natural.
- Reads from secondaries or replicas may be stale; state when the application tolerates it.

## Pagination, partitioning and sharding

- Paginate by a range on an indexed, stable sort key with a unique tie-breaker; avoid large skip/offset scans.
- When the product shards or partitions data, choose the shard or partition key from the access patterns: high cardinality, even write distribution, and present in the dominant queries. Queries without it may fan out to all shards.
- A shard key can be costly or impossible to change after the fact in some products; treat it as an architectural decision.

## Data migration and mixed-version rollout

- Prefer lazy migration (upgrade on read/write) plus a bounded background backfill for complete coverage; record when the old shape can be removed.
- Backfills run in batches by indexed key, are idempotent, and do not overwrite newer application writes.
- During a rollout every application version must read and write documents the other versions understand.

## Verification

Product and version are cited; each access pattern has a supporting index and plan; embedded arrays have a stated bound; validators and application models agree; migration was exercised on documents of every schema version present.
