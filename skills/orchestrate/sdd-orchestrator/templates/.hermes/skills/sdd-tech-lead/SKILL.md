---
name: sdd-tech-lead
description: Apply tech-lead judgment to design, deps and performance.
version: 0.1.0
author: Felipe Angeli (FelipeAngeli), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [architecture, dependencies, performance, operability, reversibility]
    related_skills: [sdd-architecture-decisions, sdd-product-owner, sdd-database-design-migrations]
---

# SDD Tech Lead

Project-local playbook the stage worker applies itself in PLAN, TASKS and REVIEW. It carries tech-lead judgment — decision quality, declared boundaries, dependency choices, performance budgets, operability, reversibility and security-by-design pointers — into the stage. **The tech-lead role is knowledge the worker applies, not an approver gate**: it never creates a human checkpoint and is never dispatched as a separate worker. A request that names a tech-lead approval is resolved as in `sdd-product-owner` (`references/approvals-and-waivers.md`).

## When to Use

- PLAN: choose the smallest design that respects declared rules, existing dependencies, a stated performance budget and a reversible rollout.
- TASKS: order slices so boundaries, contracts and rollback points exist before their consumers.
- REVIEW: report violations of declared rules, unnecessary dependencies, uncounted costs and irreversible steps in the delivered diff.

Do not use for routine edits inside an established pattern, for taste-based cleanup, or to decide product scope (load `sdd-product-owner`). For an ADR, load `sdd-architecture-decisions`.

## Prerequisites

- The project's declared rules: architecture documents, ADRs, module or boundary configuration, lint settings.
- The manifest and lockfile for any dependency decision.
- The operation, its frequency and its production input size for any performance decision.

## Reference Routing

| Concern | Read |
| --- | --- |
| Layers, dependency direction, misplaced responsibility, leaking abstractions | `references/architecture-compliance.md` |
| New, upgraded or duplicated dependencies | `references/dependencies.md` |
| Duplicate work, query cost, rendering, memory and leaks | `references/performance.md` |
| Operability, reversibility and security-by-design pointers | `references/operability-and-reversibility.md` |

## Procedure

1. **Recover declared rules.** Derive the layer map and allowed dependency direction from project documents and tooling, and state it before judging anything. Done when every rule cites its source.
2. **Check boundaries.** Compare the planned or delivered imports and responsibilities against that map. Separate what this change introduces from inherited debt.
3. **Prefer what exists.** Before adding a package or module, search for an equivalent the project already has; cite manifest and resolved version for every dependency claim.
4. **Count the cost.** For each performance-relevant path, count requests, queries, iterations or renders at the stated input size; a cost without a count is not a decision input.
5. **Plan for operation and reversal.** Name the failure signal, the rollback or roll-forward path and the point of irreversibility of each risky step.
6. **Point at security.** When the change touches credentials, sessions, authorization, personal data or logs, record the surface so the controller can decide on the `security-reviewer` sub-agent per `DISPATCH_POLICY.md`.
7. **Record durable choices.** Route a cross-cutting or expensive-to-reverse decision to `sdd-architecture-decisions`.

## Pitfalls

- Do not enforce an architectural style the project never declared, or rank one style above another on general grounds.
- Do not propose a new dependency when the project already has an equivalent.
- Do not report a cost that "looks slow" without a measurement or counted operation.
- Do not weaken a correctness guarantee to gain speed.
- Do not treat the tech-lead role as a sign-off that blocks IMPLEMENT.

## Verification

- Every rule-based finding quotes the declared rule it relies on.
- Every dependency statement cites the manifest entry, resolved version and command.
- Every performance statement names the counted operation and the input size at which it matters, or says the cost is negligible.
- Every risky step names its rollback or point of irreversibility.
- Security-sensitive surfaces are named for the controller rather than silently judged.
