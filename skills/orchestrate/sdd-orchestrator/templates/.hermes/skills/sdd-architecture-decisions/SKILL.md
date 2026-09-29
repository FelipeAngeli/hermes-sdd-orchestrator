---
name: sdd-architecture-decisions
description: Guide evidence-based architecture decisions and ADRs.
version: 0.1.0
author: Felipe Angeli (FelipeAngeli), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [architecture, ADR, boundaries, DDD, trade-offs]
    related_skills: [sdd-backend-engineering, sdd-database-design-migrations]
---

# SDD Architecture Decisions

Project-local playbook for making one consequential design decision from repository evidence. It guides PLAN; the existing `architecture-guardian` independently audits compliance with declared project rules.

## When to Use

Use when a demand changes module boundaries, dependency direction, deployment units, integration style, consistency model, public abstractions or a decision that warrants an ADR.

Do not use for routine changes inside an established pattern, taste-based cleanup, or broad architecture review without a pending decision.

## Prerequisites

- Read project architecture documents, ADRs, boundary tooling and the impacted code.
- State the pending decision and the accepted constraints.
- Separate current behavior, declared intent and proposed behavior.
- Treat missing business or operational evidence as a gap, not permission to invent it.

## Reference Routing

| Concern | Read |
| --- | --- |
| Options, trade-offs and ADR production | `references/decision-workflow.md` |
| Layers, modules, dependency direction and integration boundaries | `references/boundaries-and-dependencies.md` |
| Whether DDD, bounded contexts or aggregates fit | `references/ddd-fit.md` |

## Procedure

1. **Frame one decision.** Write the decision, affected quality attributes, constraints and what happens if nothing changes. Done when two readers would evaluate the same question.
2. **Recover authority.** Cite project rules, existing patterns and runtime evidence. Record code/documentation divergence with code as implemented truth. Done when every enforced constraint has a source.
3. **Generate real alternatives.** Include the current design where viable and at least one materially different option; do not create cosmetic variants. Done when each option changes cost or risk.
4. **Compare trade-offs.** Evaluate delivery cost, coupling, failure isolation, data consistency, operability, reversibility and migration path at the scale the project actually has. Done when no option is labeled simply “best practice.”
5. **Choose the smallest sufficient design.** Name rejected alternatives and the evidence that would reopen the decision. Done when the choice is falsifiable.
6. **Record impact.** List changed boundaries, contracts, consumers, rollout steps and acceptance verifiers. Produce or update an ADR only when the project uses ADRs or the decision is durable enough to warrant one.

## Pitfalls

- Do not enforce an architectural style the project never declared.
- Do not introduce interfaces, services, queues or microservices as proof of architecture.
- Do not use DDD terminology when the model has no domain language or invariants.
- Do not hide unknown throughput, ownership or consistency needs behind plausible estimates.
- Do not let the skill approve its own implementation; REVIEW remains independent.

## Verification

- The decision and constraints cite repository or accepted human evidence.
- Alternatives are materially distinct and include migration/reversal cost.
- Every new boundary names its contract, owner and allowed dependency direction.
- The selected design maps to acceptance checks and observable verification.
- Any ADR matches the implemented result before the demand reaches DONE.
