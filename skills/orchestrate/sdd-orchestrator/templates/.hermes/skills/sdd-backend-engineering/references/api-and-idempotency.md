# API contracts and idempotency

## Boundary contract

For each endpoint or message, record method/topic, path/routing key, authentication, request fields, response/status variants, error envelope, pagination and compatibility constraints. Validation belongs at the first trusted boundary; domain rules stay with their model owner.

## Idempotency decision

Require an idempotency strategy when a retry can duplicate money movement, provisioning, messages or durable state. Define:

- key source, tenant scope and expiry;
- whether identical keys with different payloads are rejected;
- persisted result or operation status returned to repeats;
- concurrency behavior for two first requests;
- downstream effects covered by the same guarantee.

Safe reads may retry when the project contract permits. A write is not retryable merely because the transport failed before a response arrived.

## Pagination and limits

Every collection crossing a process boundary has an explicit bound. Preserve deterministic ordering with a stable tie-breaker. Cursor contents are opaque to clients and versioned when their representation may change.

## Verification

Use contract fixtures sourced from the authoritative server/specification, not models copied from the client. Test duplicate idempotency keys concurrently when the guarantee matters.
