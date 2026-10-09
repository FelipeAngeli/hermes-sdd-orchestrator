---
name: sdd-tdd
description: Write tests that fail for the right reason, then prove it.
version: 0.1.0
author: Felipe Angeli (FelipeAngeli), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [TDD, testing, mutation, evidence, verification]
    related_skills: [sdd-release-readiness, sdd-backend-engineering]
---

# SDD TDD

Project-local playbook for test-first implementation and for judging whether a suite actually protects a rule. The stage worker applies it in IMPLEMENT (one slice under RED → minimal implementation → GREEN), TEST (focused validation) and REVIEW (test quality). It is never dispatched as a separate worker. Coverage that cannot fail is worse than absent coverage: it reports safety the product does not have.

## When to Use

- IMPLEMENT: design the focused test for the current slice before production code.
- TEST: run the authorized focused validation and judge whether the suite proves the accepted rules.
- REVIEW: decide whether the delivered tests would go red if the rule broke.

Do not use to rewrite unrelated suites, regenerate snapshots to turn a red result green, or weaken a gate.

## Prerequisites

- The controller-owned acceptance mapping for the slice and its observable verifiers.
- The project's own test command for the affected area, scoped to the change.
- A captured baseline (workspace status and the command that reproduces it) before any temporary mutation.

## Reference Routing

| Concern | Read |
| --- | --- |
| Deriving tests from business rules and writing RED first | `references/test-design.md` |
| Proving a weak test by mutating production code | `references/mutation-proof.md` |
| Running focused validation and classifying failures | `references/focused-validation.md` |

## Procedure

1. **Derive the rule.** State the business rule and observable outcome the slice must satisfy, and the bug each test detects. Done when a test name or docstring says what breaks if it fails.
2. **Write RED first.** Write the focused test before production code, run it, and confirm an expected functional failure. Infrastructure failures are not valid RED evidence.
3. **Implement minimally.** Make the smallest change within the slice's editable paths, then run GREEN with exact command, exit code and result.
4. **Prove the test can fail.** Propose three simple production-code mutations that must each make at least one relevant test fail; in TEST or REVIEW, apply them one at a time and revert each.
5. **Validate focused scope.** Run only the controller-authorized commands in order with finite timeouts, preserving command, exit code and failure output.
6. **Report honestly.** Separate product failure, timeout, environment failure and blocker; report weak tests separately from product failures.

## Pitfalls

- Do not mirror the implementation or assert private control flow.
- Do not use mocks that make the outcome inevitable; prefer real value objects and deterministic boundaries.
- Green tests alone are not sufficient evidence: explain why assertions fail when the rule is violated.
- Do not leave a mutation, temporary file or modified snapshot behind; residue is a blocker.
- Do not treat a disabled gate as PASS.

## Verification

- Each slice records RED (non-zero, expected functional failure) and GREEN (exit 0) commands.
- Each test states the bug it detects and covers the happy path, boundaries, and failures relevant to the slice.
- Mutations are applied one at a time, reverted, and the workspace is byte-identical to the baseline afterwards.
- Every acceptance `PASS` cites a recorded passing command bound to that check.
