# Approvals named in a request, and waivers

## Roles are knowledge, not gates

Product owner and tech lead are judgments the stage worker applies with the `sdd-product-owner` and `sdd-tech-lead` playbooks. They are never sub-agents, never dispatched, and never human checkpoints by default.

## Who approves

`PROJECT_SETUP.md` may declare `approvers: {product_owner: requester|<name>, tech_lead: requester|<name>}`. A missing key or a missing record resolves to `requester`, the human driving the demand.

When a request says a decision is "approved by the PO" or "validated by the tech lead":

1. Resolve the role through `approvers`.
2. If it resolves to `requester`, the requester's own statement ("it is approved", "go ahead", "we don't need that, skip it") is the decision. Record it; do not ask again.
3. If it resolves to a named person and no decision is recorded, the check stays open, but it is a HUMAN acceptance check, not a stage gate.

## Recording the decision

- Approval: an acceptance check with `verifier: HUMAN`, `status: PASS` and evidence quoting the decision and who gave it.
- Waiver: `status: WAIVED` with evidence and a `waiver` record `{by, reason, quote, recorded_at}`: the approver, why the check is waived, the literal quote, and an ISO-8601 timestamp with offset. `validate_protocol.py` accepts `WAIVED` wherever `PASS` is required for a HUMAN check. An AGENT (command-verified) check is waivable only when the controller supplies the identical record in `recorded_waivers`; a worker can never waive a verifiable check on its own.

## Never block IMPLEMENT

A role-approval check never blocks IMPLEMENT unless the request literally says implementation must wait for it (for example "do not start coding until the PO signs off"). Absent that wording, assign the check to the slice it actually verifies, or to REVIEW, and let implementation proceed. Never create an acceptance criterion such as "implementation may not start before separate PO and tech-lead approval" from a request that only mentions those roles.
