# Architecture decision workflow

## Decision record

Capture:

1. **Decision** — one sentence naming the choice.
2. **Status** — proposed, accepted, superseded or rejected.
3. **Context** — current behavior, forces and evidence.
4. **Constraints** — compatibility, ownership, scale, delivery and compliance.
5. **Options** — including the current design where viable.
6. **Consequences** — benefits, costs, risks and operational burden.
7. **Migration/reversal** — ordered transition and point of irreversibility.
8. **Verification** — acceptance checks and signals that would reopen the choice.

## Option comparison

Use project-relevant quality attributes: correctness, change isolation, coupling, consistency, latency, throughput, availability, operability, security, delivery cost and reversibility. Unknown numbers remain unknown; do not manufacture scale assumptions.

## ADR gate

Create or update an ADR when the project uses them and the decision is durable, cross-cutting or expensive to reverse. Do not create an ADR for routine application of an already accepted pattern. After implementation, align the ADR with what shipped rather than preserving a plan that no longer describes reality.

## Verification

A reviewer can trace every consequence to evidence, distinguish accepted trade-offs from gaps and identify the condition that would invalidate the decision.
