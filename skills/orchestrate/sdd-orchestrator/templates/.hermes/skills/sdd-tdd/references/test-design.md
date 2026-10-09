# Test design: from business rule to RED

- Derive tests from business rules and observable outcomes, not from the current implementation structure.
- State the bug each test detects in a short docstring or test description.
- Cover the happy path, boundaries, and failures relevant to the authorized slice.
- Write the focused test before production code, run RED, and confirm the expected functional failure.
- Make the smallest change within the assigned paths, then run GREEN with exact command, exit code and result.
- Stop after the assigned slice.

## Test quality

- Do not mirror the implementation or assert private control flow.
- Do not use mocks that make the outcome inevitable; prefer real value objects and deterministic boundaries.
- Green tests alone are not sufficient evidence: explain why assertions fail when the business rule is violated.
- Propose three simple production-code mutations that must each make at least one relevant test fail.
- Reject vacuous assertions, snapshot-only confidence, and tests that verify only mock calls.
- A test the slice just wrote is never the only proof of an acceptance check; cite the controller-bound verifier.
