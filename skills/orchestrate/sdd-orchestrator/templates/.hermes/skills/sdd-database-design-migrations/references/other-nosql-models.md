# Other NoSQL models

Load this only when the project actually uses one of these models. Each product defines its own consistency, transaction, limit and query semantics; this file lists what to establish, not what each product does. Read the official documentation for the detected product and version before writing guidance or code, and record any property that the documentation does not settle as a gap.

Do not port relational normalization, joins or ad hoc queries to these models, and do not assume they lack schema rules, conditional writes or transactions.

## Key-value stores

Examples: Redis, Memcached, Amazon DynamoDB used as key-value, etcd.

- Design keys from the exact lookups; there is usually no efficient query by value. Include tenant and version in the key when they affect the result.
- State durability: an in-memory store may lose data on restart unless persistence is configured; do not use it as a store of record without that evidence.
- State expiry (TTL) and eviction policy; an evicted key must be recoverable or acceptable to lose.
- Use the product's conditional or atomic operations (compare-and-set, increments, conditional writes) for concurrent updates.
- Bound value size and key count from the product's documented limits.

## Wide-column stores

Examples: Apache Cassandra, ScyllaDB, Google Cloud Bigtable, Amazon DynamoDB tables with composite keys.

- Design one table per query pattern; the partition key selects the partition and the clustering/sort key orders rows within it.
- Choose the partition key for even distribution and bounded partition size; a hot or unbounded partition is a design defect.
- Writes are often upserts; state how a stale write is prevented (timestamps, conditional writes or lightweight transactions where supported, and their cost).
- State the consistency level for reads and writes and whether reads may be eventual.
- Secondary indexes and filtering can fan out across partitions; prefer a denormalized table for frequent queries.

## Search engines

Examples: Elasticsearch, OpenSearch, Apache Solr.

- Treat the index as a derived projection unless the project explicitly makes it the store of record; define the source of truth and how it is reindexed.
- Define mappings explicitly; a dynamic mapping can fix a field type from the first document seen.
- Indexing is near real time by default in many engines; state the tolerated delay before a write becomes searchable.
- Changing a field's mapping usually requires a reindex; plan it with an alias swap or the engine's documented mechanism.
- Use the engine's pagination mechanism for deep results instead of large from/size offsets.

## Graph, time-series and others

Identify the model and product, state the access patterns, consistency and retention, and follow the shared references for ownership, rollout and recovery. Add product-specific guidance only from official documentation.

## Verification

The model, product and version are cited; key or partition design is tied to named access patterns; consistency, durability and limits used by the design link to official documentation or are recorded as gaps.
