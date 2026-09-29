# Query and index evidence

## Start from the access path

Record the actual query, parameter/selectivity shape, ordering, result bound, frequency and data cardinality. A table scan is not automatically wrong; an index is not automatically useful.

## Plan evidence

Use the database's project-approved explain/analysis tooling on safe representative data. Distinguish estimates from observed rows/time. Never run an expensive analyzed query against shared production without authorization.

Check:

- scan/join method and row estimates versus actuals;
- filters applied late;
- sort/hash spill;
- repeated query count (N+1);
- index prefix/order and included columns;
- write/storage cost of another index;
- tenant and pagination predicates.

## Pagination

Use a deterministic order with a stable tie-breaker. Bound page size. Keyset/cursor pagination is preferred for large or changing sets when the product contract permits; offset may be sufficient for bounded/admin lists.

## Index changes

Name the exact queries improved and writes made more expensive. Check duplicate/overlapping indexes. For large tables, use the database's online/concurrent mechanism when supported and account for its transaction restrictions and failure residue.

## Verification

Compare before/after plans or counted operations at a stated cardinality. Keep correctness tests separate from performance evidence. Route general performance judgment to `performance-auditor` when a pending release decision depends on it.
