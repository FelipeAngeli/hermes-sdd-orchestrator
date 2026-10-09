---
name: sdd-backend-engineering
description: Guide backend service design and implementation safely.
version: 0.1.0
author: Felipe Angeli (FelipeAngeli), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [backend, services, APIs, reliability, testing]
    related_skills: [sdd-architecture-decisions, sdd-database-design-migrations]
---

# SDD Backend Engineering

Project-local playbook for planning and implementing backend behavior. Project rules, existing code, public contracts and accepted SDD decisions override this generic floor.

## When to Use

Use when a slice changes an API handler, service/use case, authorization path, integration, background job, cache, transaction boundary or operational failure behavior.

Do not use for frontend-only work, pure schema migrations with no application behavior, or a review that already belongs to a controller-selected sub-agent.

## Prerequisites

- Read the project instructions, architecture and current slice contract.
- Trace the affected path before proposing a new abstraction.
- Identify the authoritative API, persistence and error contracts.
- Load only the references required by the change.

## Reference Routing

| Concern | Read |
| --- | --- |
| HTTP/RPC contract, validation, idempotency, pagination | `references/api-and-idempotency.md` |
| Errors, retries, timeouts, jobs, partial failure | `references/errors-retries-jobs.md` |
| Logging, metrics, tracing and behavioral tests | `references/observability-and-testing.md` |

## Procedure

1. **Map the path.** Name the entry point, service/use case, dependencies, persistence/external effects and returned contract. Done when every changed hop has a real path and symbol.
2. **Place each rule once.** Keep transport parsing at the boundary, orchestration in the application/service layer, domain invariants with their model owner and persistence details behind the existing repository boundary. Done when no rule needs two authorities.
3. **Define failure semantics.** For every I/O boundary, state timeout/cancellation, retry eligibility, idempotency, error translation and partial-failure behavior. Done when a repeated or interrupted request has a deterministic outcome.
4. **Preserve compatibility.** Check request/response fields, status/error envelopes, existing callers and rolling-version coexistence. Done when every affected consumer is accounted for or recorded as a gap.
5. **Add observable proof.** Bind acceptance checks to the lowest behavioral test that can fail for the rule, plus a real integration check when mocks cannot prove wiring. Done when each check cites a command or explicit human evidence.
6. **Expose operations safely.** Add only the logs, metrics or traces needed to diagnose the path, excluding secrets and personal data. Done when a production failure can be located without reconstructing user content.

## Pitfalls

- Do not create a generic service/repository/interface merely to satisfy a pattern.
- Do not retry validation, authorization or non-idempotent writes blindly.
- Do not catch and flatten errors before the boundary that owns translation.
- Do not claim integration from unit tests whose fixtures mirror the implementation.
- Do not replace project conventions with framework defaults or this playbook.

## Verification

- Every modified boundary has a named contract and owner.
- Cancellation, timeout, retry and idempotency decisions are explicit where applicable.
- Authorization is checked against the target resource, not only authentication.
- Acceptance checks have observable evidence from project-owned commands.
- No secret, credential or personal data is added to logs or errors.
