# Caching, queues and external services

Use only when the slice adds or changes one of these. Follow the cache, broker and client libraries the project already uses; their documented semantics (delivery guarantees, eviction, ordering) for the deployed version are authoritative over this file.

## Caching and invalidation

Before adding a cache, record:

- the measured or counted cost it removes (see `sdd-tech-lead` `references/performance.md`);
- the source of truth and the key: every input that changes the result, including tenant, principal, locale and version;
- tolerated staleness, stated as a product decision, and the invalidation rule: TTL, explicit eviction on write, versioned keys or event-driven;
- behavior on cache failure: the request falls back to the source or fails, never returns another principal's data.

Rules:

- Never cache authorization-dependent data under a key that omits the principal or tenant.
- Invalidate after the source write commits; a write that fails must not leave a cached value it never persisted.
- Protect hot keys from a stampede (request coalescing, early refresh or jitter) when the miss cost is material.
- A cache is not a store of record unless the project explicitly treats it as one.

Test: write-then-read returns the new value within the tolerated staleness, keys differ across tenants, and the path works with the cache unavailable.

## Queues and asynchronous messages

- Assume at-least-once delivery unless the broker and its configuration prove otherwise; make consumers idempotent (see `references/errors-retries-jobs.md`).
- Version the message payload; a consumer accepts every version a producer in the rollout may emit.
- State ordering needs explicitly. Ordering usually holds only within a partition/key or single queue; do not rely on global ordering.
- Configure retry limits and a dead-letter or parking destination, with an owner and a replay procedure.
- Publish only after the state the message describes is durable, or use the project's outbox/transactional mechanism; never publish inside an uncommitted transaction as if it were atomic.
- Bound consumer concurrency and prefetch so one slow dependency cannot exhaust workers.

## External services

- Wrap each external service behind the project's existing adapter/client boundary; the domain does not depend on its wire types.
- Record the contract source: the provider's official API documentation or specification and its version. Do not infer a field or status from one observed response.
- Set timeouts, bounded retries for transient failures only, and a defined degraded behavior when the provider is down.
- Keep credentials in configuration (see `references/security-config-and-limits.md`) and scope them to the minimum permission.
- Tests use a fake or recorded contract at the adapter boundary, plus one integration check against a sandbox or provider test mode when available and authorized. Never call a real production account from tests.

## Verification

Each new cache, queue or external dependency has its key/payload/contract source, failure behavior and a test at the boundary that owns it.
