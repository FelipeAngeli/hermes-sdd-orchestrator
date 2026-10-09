---
name: sdd-product-owner
description: Apply product-owner judgment: scope, value, acceptance.
version: 0.1.0
author: Felipe Angeli (FelipeAngeli), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [product, scope, acceptance, traceability, approvals]
    related_skills: [sdd-tech-lead, sdd-release-readiness]
---

# SDD Product Owner

Project-local playbook the stage worker applies itself in SPECIFY, CLARIFY, TASKS and REVIEW. It carries product-owner judgment — value, scope, observable acceptance criteria, deliverable kind and requirement traceability — into the stage. **The PO role is knowledge the worker applies, not an approver gate**: loading this skill never creates a human checkpoint, and it is never dispatched as a separate worker.

## When to Use

- SPECIFY and CLARIFY: classify `deliverable_kind` and `implementation_in_scope`, bound scope, write observable acceptance criteria, decide whether one material question is needed.
- TASKS: confirm every task traces to a requirement and no task adds unauthorized scope.
- REVIEW: walk SPEC → PLAN → TASKS → CODE → TESTS in both directions and report each break.

Do not use for technical design choices (load `sdd-tech-lead`) or to invent approvals the request does not name.

## Prerequisites

- The literal request text; quotes from it are the evidence for every classification.
- `PROJECT_SETUP.md` `approvers` (`product_owner`, `tech_lead`); when absent, every role resolves to `requester`.
- The accepted specification, plan and tasks artifacts for the demand, with their requirement identifiers.

## Reference Routing

| Concern | Read |
| --- | --- |
| Deliverable kind, implementation scope and the single material question | `references/deliverable-kind.md` |
| Approvals named in a request, requester decisions and `WAIVED` evidence | `references/approvals-and-waivers.md` |
| Value, scope, exclusions and observable acceptance criteria | `references/acceptance-and-scope.md` |
| SPEC → PLAN → TASKS → CODE → TESTS traceability audit | `references/traceability-audit.md` |

## Procedure

1. **Quote the request.** Copy the sentences that state the objective, the verb and any named approval. Done when each later decision can cite one quote.
2. **Classify the deliverable.** Set `deliverable_kind` to `CODE`, `DECISION_DOC` or `BOTH` and `implementation_in_scope` to true or false, each with a request quote, in `context_assessment.facts`. An imperative to implement or orchestrate an implementation keeps code in scope. Done when no classification rests on inference alone.
3. **Ask at most one material question.** When the quotes conflict (for example "implement" plus "close the decisions"), route a single material question to CLARIFY naming both readings and the default. Never ask what the request already answers.
4. **Write observable acceptance.** Each criterion names a behavior a user, operator or command can observe, the verification method and the verifier. Done when no criterion depends on intent.
5. **Bound scope.** List inclusions and explicit exclusions; mark anything the request does not authorize as out of scope rather than silently adding it.
6. **Resolve named approvals.** When the request names an approval ("approved by the PO/tech lead"), resolve the role through `approvers` (default `requester`). A requester approval or explicit waiver already in the conversation is the evidence: record it as a HUMAN check with the quote, or as `WAIVED` with a `waiver` record. Never ask again, and never let a role-approval check block IMPLEMENT unless the request literally says implementation must wait for it.
7. **Trace (TASKS, REVIEW).** Follow each requirement forward to plan, task, code and test, and each code change backward to its requirement. Report breaks with identifier, stage and path.

## Pitfalls

- Do not convert "approved by PO/tech lead" into a gate that blocks IMPLEMENT; that wording names who decides, not when work may start.
- Do not reclassify an implementation request as `DECISION_DOC` because its objective mentions decisions.
- Do not invent a human approval gate, an approver or an acceptance criterion the request does not state.
- Do not ask for an approval the requester already gave or waived.
- Do not infer a requirement to justify code; unauthorized scope is reported, not legitimized.

## Verification

- `deliverable_kind` and `implementation_in_scope` each cite a literal request quote.
- At most one material question was routed, and only when the quotes genuinely conflict.
- Every acceptance criterion is observable and names its verifier.
- Every named approval resolves to an approver from `approvers` (or `requester`) with recorded evidence; none blocks IMPLEMENT without a literal request requirement.
- REVIEW reports each traceability break or an explicit `NO_FINDINGS`.
