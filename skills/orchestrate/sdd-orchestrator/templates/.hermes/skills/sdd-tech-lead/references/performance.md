# Performance budgets and waste

Performance is the domain where plausible reasoning is most often wrong. A finding without a measurement or a counted operation is an opinion, and optimizing on opinion trades correctness and readability for nothing.

## Method

1. Establish what the path is supposed to cost: the operation, its expected frequency and the production input size.
2. Count the work before judging it — requests, queries, iterations, allocations, renders — from code paths, existing instrumentation or an authorized measurement.
3. Separate a cost that grows with input from a constant one; a scan of a bounded list is not a finding, the same scan over an unbounded collection is.
4. Locate where the cost lands: startup, an interactive path the user waits on, a background task, or a server request under concurrency.
5. When judging a diff, compare against the previous path so a regression is distinguished from a pre-existing cost.
6. Name the cheapest change that removes the waste and what it would not fix.

## Waste surfaces

- Duplicate work: the same request issued more than once per user action, retries that duplicate, a request on every keystroke without debouncing.
- Caching: a value recomputed or refetched when it could not have changed; a cache that never invalidates; a key too loose or too tight.
- Query cost: an N+1 access pattern, a query without an index for its filter or sort, a join that grows with total data instead of the requested page.
- Unbounded results: no pagination or limit, a response that grows with tenant data.
- Recomputation and rendering: work repeated on every render or frame for unchanged inputs; expensive derivation inside a loop.
- Blocking the critical path: synchronous file, network, serialization or cryptographic work on the main thread or in a request handler.
- Memory: an allocation inside a hot loop, a retained large object, an accumulating buffer or a cache that only grows.
- Leaks: a listener, subscription, stream, timer or observer never disposed. A leak worsens the longer the process runs, so it outranks a constant cost of similar size.
- Startup: eager work at launch that only a minority of sessions needs.

## Evidence rules

- Report a cost only with a measurement or a counted operation; state the input size at which the cost becomes material.
- Reporting no material finding is a valid and useful result; inventing work to justify the review wastes effort on noise.
- Never weaken a correctness guarantee to gain speed: dropping validation, loosening a transaction, widening a cache key or removing a retry is a correctness change.
- Never run a load test or a benchmark against a shared environment without explicit authorization.
- Distinguish a cost this change introduces from a pre-existing one.
