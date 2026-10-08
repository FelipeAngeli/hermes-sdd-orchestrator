# Verdict rules and release surfaces

`READY_WITH_RISK` is where this judgment can quietly fail. It is the comfortable answer — it blocks nobody and never looks careless — so left undefined it becomes the default and the audit stops meaning anything. It is the narrow case, not the safe one.

## Verdict rules

- `READY` — every acceptance criterion is verified by evidence (or `WAIVED` with a recorded waiver), required gates passed, and no release surface is unresolved.
- `BLOCKED` — any acceptance criterion failed, any required gate failed, or any item could not be verified. An unverified item is `BLOCKED`, never `READY_WITH_RISK`.
- `READY_WITH_RISK` — everything is verified, and a known risk remains that a human has explicitly accepted. Name the risk, its blast radius, its mitigation and name the human who accepted it.

## Release surfaces

- Acceptance criteria: each one, individually, against the evidence that proves it.
- Tests: the suites relevant to the change ran and passed, with commands and exit codes; a suite that was not run is not a passing suite.
- Regressions: previously working behavior on the affected paths still verified, especially in consumers the change did not touch.
- Configuration: every new or changed setting exists in each target environment with a safe value, and no secret entered a tracked file.
- Migrations: ordering against deploy, behavior on existing data, and whether the previous application version keeps working during rollout.
- Feature flags: default state on release, who can change it, behavior when the flag service is unavailable, and whether the off path was tested.
- External dependencies: a new service, endpoint, permission, quota or credential exists and is reachable in the target environment.
- Rollback: whether the change can be reverted, what becomes irreversible once it runs, and what data a rollback would not undo.
- Documentation: what an operator or support needs to know that the code does not tell them.
- Known risks: carried forward from prior audits and still open.

## Evidence rules

- Cite the evidence for every verified item.
- Never infer that an unexecuted check would have passed. An unrun test is unknown, not green.
- Never treat the absence of a report as the absence of a problem; say which surfaces were not examined.
- Report `NO_FINDINGS` per surface where nothing is outstanding.
- Distinguish a blocker this change introduced from a pre-existing condition, and say whether the pre-existing one blocks this release.
- Never soften a verdict because a deadline is near.
- Never run a migration, flip a flag, deploy or change configuration while assessing readiness.
