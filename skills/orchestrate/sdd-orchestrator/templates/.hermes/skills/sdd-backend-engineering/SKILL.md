---
name: sdd-backend-engineering
description: Guide backend service design and implementation safely.
version: 0.2.0
author: Felipe Angeli (FelipeAngeli), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [backend, services, APIs, security, reliability, testing]
    related_skills: [sdd-architecture-decisions, sdd-database-design-migrations, sdd-api-contracts, sdd-docker-containers]
---

# SDD Backend Engineering

Project-local playbook for planning and implementing backend behavior. Project rules, existing code, public contracts and accepted SDD decisions override this generic floor. It names concerns, not frameworks: use the project's existing libraries, layers and configuration mechanism, and introduce a new one only with a recorded decision.

## When to Use

Use when a slice changes an API handler, service/use case, authorization path, configuration or secret, resource limit, integration, queue consumer/producer, background job, cache, transaction boundary or operational failure behavior.

Do not use for frontend-only work, pure schema migrations with no application behavior (`sdd-database-design-migrations`), specification authoring (`sdd-api-contracts`), container files (`sdd-docker-containers`), or a review that already belongs to a controller-selected sub-agent.

## Prerequisites

- Read the project instructions, architecture and current slice contract.
- Trace the affected path before proposing a new abstraction.
- Identify the authoritative API, persistence, error and configuration contracts.
- Load only the references required by the change.

## Reference Routing

| Concern | Read |
| --- | --- |
| HTTP/RPC contract, validation, idempotency, pagination | `references/api-and-idempotency.md` |
| Errors, retries, timeouts, jobs, partial failure | `references/errors-retries-jobs.md` |
| Resource authorization, configuration, secrets, limits, concurrency | `references/security-config-and-limits.md` |
| Caching and invalidation, queues, external services | `references/caching-and-integrations.md` |
| Logs, metrics, traces, debugging evidence, test-layer choice | `references/observability-and-testing.md` |

## Procedure

1. **Map the path.** Name the entry point, service/use case, dependencies, persistence/external effects and returned contract. Done when every changed hop has a real path and symbol.
2. **Place each rule once.** Keep transport parsing at the boundary, orchestration in the application layer, invariants with their model owner and persistence behind the existing repository boundary, following the project's layering. Done when no rule needs two authorities.
3. **Authorize the resource.** State who may act on which resource and tenant, and where that check runs. Done when an authenticated caller cannot reach another principal's resource by changing an identifier.
4. **Bound resources and failures.** For every I/O boundary, state timeout/cancellation, concurrency limit, retry eligibility, idempotency, error translation and partial-failure behavior. Done when a slow, repeated or interrupted request has a deterministic, bounded outcome.
5. **Keep configuration and secrets out of code.** Read settings through the project's mechanism, validate them at startup, never log or commit a secret. Done when a missing value fails at startup with a safe message.
6. **Preserve compatibility.** Check fields, status/error envelopes, cache keys, message versions, existing callers and rolling-version coexistence. Done when every affected consumer is accounted for or recorded as a gap.
7. **Add observable proof.** Choose the test layer with the criteria in `references/observability-and-testing.md`. Done when each acceptance check cites a project-owned command or explicit human evidence.
8. **Expose operations safely.** Add only the logs, metrics or traces needed to diagnose the path, excluding secrets and personal data. Done when a failure can be located without reconstructing user content.

## Pitfalls

- Do not create a generic service/repository/interface merely to satisfy a pattern.
- Do not retry validation, authorization or non-idempotent writes blindly.
- Do not catch and flatten errors before the boundary that owns translation.
- Do not add a cache without an invalidation rule and a tolerated staleness.
- Do not call a real third-party or shared environment from tests without authorization.
- Do not claim integration from unit tests whose fixtures mirror the implementation.
- Do not replace project conventions with framework defaults or this playbook.

## Verification

- Every modified boundary has a named contract and owner.
- Authorization is checked against the target resource and tenant, not only authentication.
- Timeout, cancellation, concurrency, retry and idempotency decisions are explicit where applicable.
- Configuration is validated at startup; no secret or personal data reaches code, logs or errors.
- Acceptance checks have observable evidence from project-owned commands.
