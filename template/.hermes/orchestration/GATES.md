# Gate Policy — configure for this repository

This file is intentionally generic. Before starting work, replace the placeholder commands with commands that exist in this repository. Do not disable a failing project CI automatically.

```yaml
project_ci_policy:
  enabled: false
  status_when_skipped: DISABLED_BY_PROJECT_POLICY
  reason: Configure this policy explicitly for the target repository before declaring DONE.
  temporary: false
```

## Required order

1. TDD RED/GREEN for every implementation slice
2. Focused tests
3. Formatter restricted to files owned and changed by the agent
4. Static analysis
5. REVIEW
6. CI only when `project_ci_policy.enabled: true`
7. DONE

## Target-project commands

| Gate | Command | Executor | Timeout |
| --- | --- | --- | --- |
| TDD RED | target-project focused test command | worker | 300 s |
| TDD GREEN | target-project focused test command | worker | 300 s |
| Focused tests | target-project focused test command | worker/host | 300 s |
| Format | target-project formatter restricted to changed files | host | 120 s |
| Analyze | target-project static-analysis command | host | 300 s |
| CI | target-project CI command | host | 900 s |

When CI is enabled, DONE requires focused tests, format, analysis, review, and CI to pass. When disabled by the explicit policy above, record `ci: DISABLED_BY_PROJECT_POLICY`; this does not convert an already-failed CI execution into a pass.

A timeout is `TIMEOUT`, must be recorded in STATE, and blocks advancement. Do not use polling or background waits.
