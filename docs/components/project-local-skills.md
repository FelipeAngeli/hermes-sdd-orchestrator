# Project-local engineering skills

[Docs index](../README.md) · Related: [Skill and installer](skill-and-installer.md), [Harness](harness.md), [Stage agents](stage-agents.md), [Sub-agents](sub-agents.md)

**Files:** `templates/.hermes/skills/*`.

These skills carry reusable engineering and role judgment (product owner, tech lead, TDD, release readiness) into the target repository without adding reviewer workers. Each [stage brief](stage-agents.md) names the skills to load in its `Playbooks` section. Hermes discovers them under `<project-root>/.hermes/skills/` only for sessions in that checkout. Installation copies them but never edits profile configuration or trust; the user explicitly runs `hermes skills trust` for the repository, and a new session then sees them as project skills.

Project rules, code, accepted decisions and authoritative contracts override every generic skill rule. Skills answer **how to do the work** inside every stage. Sub-agents answer a named independent question; they remain controller-selected, isolated and budgeted. The controller never dispatches a generic backend or architecture reviewer merely because a skill exists.

## Catalogue

| File | Purpose |
| --- | --- |
| `templates/.hermes/skills/sdd-backend-engineering/SKILL.md` | Routes backend slices through boundary, failure, compatibility, evidence and operations decisions. |
| `templates/.hermes/skills/sdd-backend-engineering/references/api-and-idempotency.md` | API contracts, pagination and duplicate-write guarantees. |
| `templates/.hermes/skills/sdd-backend-engineering/references/errors-retries-jobs.md` | Error ownership, cancellation, bounded retries and durable job behavior. |
| `templates/.hermes/skills/sdd-backend-engineering/references/observability-and-testing.md` | Secret-safe observability and the lowest behavioral test that owns an invariant. |
| `templates/.hermes/skills/sdd-architecture-decisions/SKILL.md` | Produces evidence-based architectural options, decisions, impact and ADRs. |
| `templates/.hermes/skills/sdd-architecture-decisions/references/decision-workflow.md` | Decision record, alternatives, trade-offs, migration and reopening evidence. |
| `templates/.hermes/skills/sdd-architecture-decisions/references/boundaries-and-dependencies.md` | Module/layer ownership, public surfaces and dependency direction. |
| `templates/.hermes/skills/sdd-architecture-decisions/references/ddd-fit.md` | Proceed/downgrade/refuse gate for strategic and tactical DDD. |
| `templates/.hermes/skills/sdd-database-design-migrations/SKILL.md` | Plans data/schema changes, rollout, recovery and independent migration audit. |
| `templates/.hermes/skills/sdd-database-design-migrations/references/modeling-and-integrity.md` | Ownership, keys, relationships, null/default semantics, units and constraints. |
| `templates/.hermes/skills/sdd-database-design-migrations/references/migrations-and-backfills.md` | Expand/contract ordering, bounded backfills and mixed-version compatibility. |
| `templates/.hermes/skills/sdd-database-design-migrations/references/transactions-locking-recovery.md` | Transaction scope, lock evidence, interruption and recovery. |
| `templates/.hermes/skills/sdd-database-design-migrations/references/query-and-index-evidence.md` | Query plans, pagination and evidence required for index decisions. |
| `templates/.hermes/skills/sdd-frontend-engineering/SKILL.md` | Guides React, Next.js, UI and Vercel-oriented performance work using project-local evidence. |
| `templates/.hermes/skills/sdd-frontend-engineering/references/react-and-next-boundaries.md` | React state/effects/compiler guidance and Next.js rendering/data boundaries. |
| `templates/.hermes/skills/sdd-frontend-engineering/references/composition-and-accessibility.md` | Component composition, design-token reuse, interaction states and accessibility floor. |
| `templates/.hermes/skills/sdd-frontend-engineering/references/performance-and-delivery.md` | Evidence-led waterfall, bundle, render and client-performance decisions. |
| `templates/.hermes/skills/sdd-product-owner/SKILL.md` | Product-owner judgment applied by the stage worker (SPECIFY, CLARIFY, TASKS, REVIEW): value, scope, observable acceptance, `deliverable_kind` with request quotes, approvals resolved through `approvers`; never an approver gate. |
| `templates/.hermes/skills/sdd-product-owner/references/deliverable-kind.md` | `deliverable_kind` (CODE, DECISION_DOC, BOTH) and `implementation_in_scope` from request quotes; at most one material question. |
| `templates/.hermes/skills/sdd-product-owner/references/approvals-and-waivers.md` | Named approvals resolved through PROJECT_SETUP `approvers` (default requester), HUMAN evidence and `WAIVED` records; never blocks IMPLEMENT unless the request says so. |
| `templates/.hermes/skills/sdd-product-owner/references/acceptance-and-scope.md` | Value, inclusions, exclusions and observable acceptance criteria with verifier. |
| `templates/.hermes/skills/sdd-product-owner/references/traceability-audit.md` | SPEC → PLAN → TASKS → CODE → TESTS walk, break types and evidence rules (formerly `spec-consistency-guardian`). |
| `templates/.hermes/skills/sdd-tech-lead/SKILL.md` | Tech-lead judgment applied by the stage worker (PLAN, TASKS, REVIEW): declared rules, dependencies, performance, operability, reversibility; never an approver gate. |
| `templates/.hermes/skills/sdd-tech-lead/references/architecture-compliance.md` | Violations of declared architecture rules only, inherited vs introduced (formerly `architecture-guardian`). |
| `templates/.hermes/skills/sdd-tech-lead/references/dependencies.md` | Manifest/lockfile evidence, duplication, maintenance and prefer-what-exists (formerly `dependency-auditor`). |
| `templates/.hermes/skills/sdd-tech-lead/references/performance.md` | Counted or measured cost at a stated input size, waste surfaces and leaks (formerly `performance-auditor`). |
| `templates/.hermes/skills/sdd-tech-lead/references/operability-and-reversibility.md` | Failure signals, points of irreversibility and security-by-design pointers to `security-reviewer`. |
| `templates/.hermes/skills/sdd-api-contracts/SKILL.md` | Client models, API specifications and servers kept in agreement across PLAN, IMPLEMENT and REVIEW (formerly `api-contract-auditor`). |
| `templates/.hermes/skills/sdd-api-contracts/references/source-hierarchy.md` | Ranked contract sources and rollout order for a contract change. |
| `templates/.hermes/skills/sdd-api-contracts/references/contract-surfaces.md` | Field, type, nullability, enum, collection, endpoint, request, response and semantic surfaces. |
| `templates/.hermes/skills/sdd-api-contracts/references/evidence-and-limits.md` | Citation rules, client-written fixtures and live-call authorization limits. |
| `templates/.hermes/skills/sdd-tdd/SKILL.md` | Test-first slices and proof that a suite can fail, for IMPLEMENT, TEST and REVIEW (formerly `tdd-guardian`, `tdd-implementer`, `test-runner`). |
| `templates/.hermes/skills/sdd-tdd/references/test-design.md` | Tests derived from business rules, RED before code, test-quality rules. |
| `templates/.hermes/skills/sdd-tdd/references/mutation-proof.md` | One-at-a-time production mutations, reverted, with a byte-identical baseline. |
| `templates/.hermes/skills/sdd-tdd/references/focused-validation.md` | Authorized focused commands, failure classification and acceptance evidence. |
| `templates/.hermes/skills/sdd-release-readiness/SKILL.md` | READY / BLOCKED / READY_WITH_RISK from evidence near DONE (formerly `release-readiness-auditor`, `regression-hunter`, `documentation-writer`). |
| `templates/.hermes/skills/sdd-release-readiness/references/verdict-and-surfaces.md` | Verdict rules and release surfaces: config, migrations, flags, dependencies, rollback. |
| `templates/.hermes/skills/sdd-release-readiness/references/regression-hunt.md` | Untouched consumers, non-local reach and consumer suites. |
| `templates/.hermes/skills/sdd-release-readiness/references/documentation-sync.md` | Documentation, ADRs, README and diagrams verified against the code. |
| `templates/.hermes/skills/sdd-database-design-migrations/references/rollout-safety-audit.md` | Ordered rollout, mixed-version compatibility, locks, restartability and recovery audit (formerly `migration-safety-auditor`). |

## Selection

PLAN names a playbook only when its guidance changes a pending design or implementation decision. TASKS binds that name to one or more slice IDs. Before IMPLEMENT, the controller loads the full `SKILL.md` and only the relevant references, then regenerates a stage-context schema-2 manifest (schema 1 is not reusable) with this descriptor:

```json
{
  "project_root": "/absolute/project/root",
  "playbooks": [{
    "name": "sdd-database-design-migrations",
    "version": "0.1.0",
    "path": ".hermes/skills/sdd-database-design-migrations/SKILL.md",
    "sha256": "<sha256 of the loaded SKILL.md>",
    "references": [{
      "path": ".hermes/skills/sdd-database-design-migrations/references/migrations-and-backfills.md",
      "sha256": "<sha256 of the loaded reference>"
    }],
    "reason": "S2 tightens status constraints after a data backfill"
  }]
}
```

The slice contract separately lists the skill name and slice IDs that require it. `stage_context.py` first binds `project_root` to the canonical non-symlinked Git toplevel of the live process, then resolves each declared file beneath it, rejects descendant symlinks/escapes, verifies actual hashes and `SKILL.md` name/version frontmatter, and rejects duplicate descriptors, unknown slice IDs, extra playbooks or a current IMPLEMENT slice whose required playbook is absent. The byte-verified skill/reference descriptors are canonicalized before `slice_sha256`, so reference ordering is irrelevant while changing any loaded guidance changes the approved scope rather than silently reusing approval.

`verifier-context` remains the validator-argument projection; playbook contents travel in the worker briefing as scoped context, not as `validate_protocol.py` keyword arguments.

## Skills versus sub-agents

| Need | Mechanism |
| --- | --- |
| Scope, acceptance, deliverable kind, traceability | `sdd-product-owner`, loaded by the stage worker. |
| Declared architecture, dependencies, performance, reversibility | `sdd-tech-lead`, loaded by the stage worker. |
| API/server/client divergence | `sdd-api-contracts`. |
| Migration rollout/data/recovery judgment | `sdd-database-design-migrations` (`references/rollout-safety-audit.md`). |
| Whether tests can fail; focused validation | `sdd-tdd`. |
| Release verdict, regressions, documentation truth | `sdd-release-readiness`. |
| Bounded code evidence, data-flow trace or impact map | `data-flow-tracer` sub-agent. |
| Independent PR or security review | `pr-reviewer` or `security-reviewer` sub-agent. |

Playbooks are never dispatched and never act as approvers. Product-owner and tech-lead approvals named in a request resolve through `PROJECT_SETUP.md` `approvers` (default: the requester) and are recorded as HUMAN evidence or `WAIVED`; they never block IMPLEMENT unless the request literally says so. The default for sub-agents remains **do not dispatch**.

## Security and lifecycle

- Project skills are repository content and require explicit Hermes project trust; the installer never grants it.
- Skill instructions never authorize external mutation, production migration or shared-environment tests.
- A current session may not discover newly installed skills; start a new session after trust/installation.
- Hash the actual loaded `SKILL.md` and every loaded reference in the descriptor; ordinary code/spec excerpts remain `sources` with line ranges and hashes.
- Never load all references by default. Progressive loading keeps context proportional to the current decision.

## Verification

- Packaging tests require exactly these nine skill directories, valid portable frontmatter, required sections and at least three references each.
- Documentation tests require this catalogue to match every installed project-skill file in both directions.
- Stage-context tests cover missing, extra, duplicate, unsafe, unknown-slice and byte/frontmatter-mismatched playbooks/references and prove loaded guidance changes the approved slice hash.
- Installer regression tests prove the installer adds exclusions only for bundled skill directories, does not newly hide unrelated project skills and preserves user-owned exclusion entries.
