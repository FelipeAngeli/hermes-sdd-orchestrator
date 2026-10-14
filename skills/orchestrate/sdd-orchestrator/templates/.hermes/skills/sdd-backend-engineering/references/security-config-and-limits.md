# Security, configuration and resource limits

Application-level guidance. A dedicated security judgment (threat model, credential exposure, trust boundaries) belongs to the `security-reviewer` sub-agent when the controller dispatches it; this file is the implementer's floor.

## Authorization at the resource

- Authenticate the caller, then authorize against the target resource: owner, tenant, role and action. Authentication alone never grants access to an identifier the caller supplied.
- Load the resource scoped by tenant/owner (or check ownership after loading) before any side effect. A list, search, export or bulk operation applies the same scope to every item.
- Decide where the check lives using the project's existing mechanism (middleware, policy object, service guard). One authority per rule; do not duplicate it in the handler and the service with different semantics.
- Choose deliberately between "not found" and "forbidden" for an existing resource the caller may not see; follow the project's disclosure convention.
- Validate input at the first trusted boundary: types, lengths, ranges, allowed values and collection sizes. Server-side validation is required even when the client validates.
- Test the denial path: another tenant's identifier, missing role, revoked/expired credentials and mass-assignment of fields the caller may not set.

## Configuration and secrets

- Read configuration through the project's existing mechanism (environment, config files, secret manager). Do not add a second mechanism without a recorded decision.
- Validate required settings at startup and fail fast with a message naming the setting, never its value.
- Secrets never appear in source, tests, fixtures, example files with real values, image layers, logs, error responses, metrics labels or trace attributes. Example files contain placeholders only.
- Distinguish configuration that may differ per environment from constants that must not. A secret's rotation must not require a code change.
- Treat personal and sensitive data by minimization: store, log and return only what the requirement needs, under the project's retention and masking rules.

## Resource limits, timeouts and concurrency

- Every outbound call (database, cache, queue, HTTP/RPC) has a timeout derived from the caller's deadline or project configuration; no unbounded wait.
- Bound concurrency explicitly: connection pool sizes, worker counts, parallel fan-out, in-flight requests. A limit that is not configured is the library default; find it before relying on it.
- Bound input and output: request body size, page size, batch size, upload size, result count and memory held per request.
- Apply backpressure instead of unbounded buffering: reject, queue with a bound or shed load with a retryable status the client contract defines.
- Rate limit or quota abusive or expensive paths when the requirement or project policy calls for it; key the limit by principal or tenant, not only by IP.
- For shared mutable state, name the concurrency control (database constraint, optimistic version, lock, single consumer) and test two concurrent writers when the invariant matters.

## Verification

- A denial test exists for each new resource-level rule.
- Startup with a missing or malformed required setting fails with a safe message.
- A search of the diff finds no literal credential, token or connection string with real values.
- Each new outbound call cites its timeout source; each new pool or fan-out cites its bound.
