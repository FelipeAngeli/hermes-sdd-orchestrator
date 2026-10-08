---
name: sdd-api-contracts
description: Keep client models, API specs and servers in agreement.
version: 0.1.0
author: Felipe Angeli (FelipeAngeli), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [API, contracts, schemas, serialization, compatibility]
    related_skills: [sdd-backend-engineering, sdd-tech-lead]
---

# SDD API Contracts

Project-local playbook for changes that cross a wire boundary: client models, serializers, published API specifications and the server implementation. A client model that disagrees with the server compiles, passes its own mocked tests and fails in production; the divergence is only visible when the sources are read side by side. The stage worker applies it in PLAN, IMPLEMENT and REVIEW; it is never dispatched as a separate worker.

## When to Use

Use when a slice adds or changes an endpoint, request or response shape, field, enum, nullability, error envelope, pagination contract, authentication scheme or a client model that mirrors one.

Do not use for internal function signatures with no wire boundary, or to call a live API without explicit authorization.

## Prerequisites

- The controller-assigned scope: endpoints, models or a changed area, plus the environment whose contract is authoritative.
- Physical paths of the client models, serializers and call sites, and of the server routes, request/response types and validation rules.
- Any authorization to observe a deployed environment; without it, the deployed contract is a gap.

## Reference Routing

| Concern | Read |
| --- | --- |
| Which source wins when specification, server and client disagree | `references/source-hierarchy.md` |
| Field-by-field comparison surfaces | `references/contract-surfaces.md` |
| Evidence rules, fixtures and live-call limits | `references/evidence-and-limits.md` |

## Procedure

1. **Resolve scope and authority.** Name the endpoints, models and the authoritative environment. Done when every source to compare has a path or an explicit gap.
2. **Align field by field, both directions.** A field the client sends that the server ignores and a field the server returns that the client drops are different defects.
3. **Classify blast radius.** Silent data loss, runtime deserialization failure, wrong value reaching a business rule, or dead code.
4. **Plan compatibility.** For a change, state which clients keep working, which version or prefix carries the change, and how unknown enum values fail closed.
5. **Prove with real payloads.** Fixtures come from a real server payload or the authoritative specification, not from the client model under test.
6. **Report gaps.** Request a human or backend-owner decision for an unresolvable divergence instead of choosing a winner by plausibility.

## Pitfalls

- Do not resolve a conflict by picking the convenient source.
- Do not treat optional-with-default as nullable, or an empty collection as a null one.
- Do not invent a field, endpoint, status code, enum value or nullability to close a gap.
- Do not trust a green client test whose fixtures were written from the client model.
- Do not edit a read-only server checkout; it is reference material.

## Verification

- Every divergence cites the exact field, path and source.
- Conflicts between higher-ranked sources are reported as findings in their own right.
- Compatibility for existing clients is stated for each changed field or route.
- Fixtures trace to a real payload or the authoritative specification.
- Unresolvable divergences are recorded as gaps for a named decision.
