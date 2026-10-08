# Focused validation

Execute only the controller-authorized focused validation commands and return reproducible evidence without changing source, tests, snapshots, thresholds or configuration.

## Method

1. Verify each command exists and is scoped to the assigned change.
2. Execute commands in the supplied order with finite timeouts.
3. Preserve the exact command, exit code and relevant failure output.
4. Distinguish product failure, timeout, environment failure and blocker.
5. When assessing whether the supplied suite protects the requested behavior, derive expectations from business rules, state the bug each test detects, or report the missing protection as a finding.
6. Report weak tests separately from product failures; do not edit them in TEST.

## Evidence

- Every acceptance `PASS` cites, in backticks, a command recorded in `commands` with exit code 0 and bound to that check by the controller.
- A HUMAN check passes only with a recorded human decision, or is `WAIVED` with a recorded waiver; the worker's opinion is neither.
- A disabled or unconfigured gate is reported as such, never as PASS.
