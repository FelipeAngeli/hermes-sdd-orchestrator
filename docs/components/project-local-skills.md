# Project-local engineering skills

[Docs index](../README.md) · Related: [Skill and installer](skill-and-installer.md), [Harness](harness.md), [Stage agents](stage-agents.md), [Sub-agents](sub-agents.md)

**Files:** `templates/.hermes/skills/*`.

These skills carry reusable engineering procedure into the target repository without adding generic reviewer roles. Hermes discovers them under `<project-root>/.hermes/skills/` only for sessions in that checkout. Installation copies them but never edits profile configuration or trust; the user explicitly runs `hermes skills trust` for the repository, and a new session then sees them as project skills.

Project rules, code, accepted decisions and authoritative contracts override every generic skill rule. Skills answer **how to do the work** inside PLAN/IMPLEMENT. Sub-agents answer a named independent question; they remain controller-selected, isolated and budgeted. The controller never dispatches a generic backend or architecture reviewer merely because a skill exists.

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
| Backend, architecture or database implementation guidance | Load the applicable project skill and relevant references. |
| Compliance with declared architecture | Existing `architecture-guardian`. |
| API/server/client divergence | Existing `api-contract-auditor`. |
| Measured query/index performance | Existing `performance-auditor`. |
| Migration rollout/data/recovery judgment not answered deterministically | `migration-safety-auditor`. |

The default remains **do not dispatch**. A migration specialist runs only when the journal names the pending decision, deterministic attempt and effect of `NO_FINDINGS`.

## Security and lifecycle

- Project skills are repository content and require explicit Hermes project trust; the installer never grants it.
- Skill instructions never authorize external mutation, production migration or shared-environment tests.
- A current session may not discover newly installed skills; start a new session after trust/installation.
- Hash the actual loaded `SKILL.md` and every loaded reference in the descriptor; ordinary code/spec excerpts remain `sources` with line ranges and hashes.
- Never load all references by default. Progressive loading keeps context proportional to the current decision.

## Verification

- Packaging tests require exactly these four skill directories, valid portable frontmatter, required sections and at least three references each.
- Documentation tests require this catalogue to match every installed project-skill file in both directions.
- Stage-context tests cover missing, extra, duplicate, unsafe, unknown-slice and byte/frontmatter-mismatched playbooks/references and prove loaded guidance changes the approved slice hash.
- Installer regression tests prove the installer adds exclusions only for bundled skill directories, does not newly hide unrelated project skills and preserves user-owned exclusion entries.
