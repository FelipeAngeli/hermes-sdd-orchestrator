---
name: sdd-release-readiness
description: Decide READY, BLOCKED or READY_WITH_RISK from evidence.
version: 0.1.0
author: Felipe Angeli (FelipeAngeli), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [release, regression, documentation, rollback, readiness]
    related_skills: [sdd-tdd, sdd-product-owner, sdd-tech-lead]
---

# SDD Release Readiness

Project-local playbook the REVIEW worker applies itself near DONE to answer one question with objective evidence: can this ship? It also covers what previously worked and may have stopped, and documentation that the change made false. It is never dispatched as a separate worker.

## When to Use

- REVIEW of a change that is about to reach DONE, especially one with migrations, flags, configuration, external dependencies or untouched consumers.
- IMPLEMENT of a documentation slice (for example a `DECISION_DOC` deliverable), to keep documentation true to the code.

Do not use as a substitute for the accepted acceptance criteria, or to soften a verdict because a deadline is near.

## Prerequisites

- The controller-owned acceptance mapping and the recorded evidence for each check.
- Gate results with commands and exit codes; review outcomes already recorded.
- The changed files and the public symbols they declare or alter.

## Reference Routing

| Concern | Read |
| --- | --- |
| Verdict rules and release surfaces | `references/verdict-and-surfaces.md` |
| Untouched consumers and regressions | `references/regression-hunt.md` |
| Documentation, ADRs, README and diagrams kept true to the code | `references/documentation-sync.md` |

## Procedure

1. **Use acceptance as the checklist.** Verify each accepted criterion against evidence that already exists; identify what has not been verified rather than assuming it was.
2. **Walk release surfaces.** Configuration, migrations, feature flags, external dependencies, rollback and operator documentation, each with evidence or `NO_FINDINGS`.
3. **Hunt regressions.** Locate consumers of changed symbols that the change did not touch, and run their own suites when authorized.
4. **Check documentation truth.** Compare docs that describe the changed behavior with the code; stale text is a finding.
5. **Assign the verdict.** `READY`, `BLOCKED` or `READY_WITH_RISK`, from the evidence and not from the effort spent.

## Pitfalls

- An unverified item is `BLOCKED`, never `READY_WITH_RISK`: not knowing is not the same as knowing and accepting.
- `READY_WITH_RISK` requires a named human who accepted the risk; without one the verdict is unavailable.
- A `WAIVED` acceptance check counts as satisfied only with its recorded waiver; cite it.
- Never infer that an unexecuted check would have passed.
- Never report a pre-existing failure as a new regression.

## Verification

- Every verified item cites command, exit code, artifact, gate result, recorded waiver or review outcome.
- Every release surface is reported with evidence or `NO_FINDINGS`, and unexamined surfaces are named.
- Regressions distinguish this change from pre-existing failures.
- The verdict follows the rules exactly.
