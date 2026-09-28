# Installed orchestration layout

This directory separates controller concerns while keeping mutable local state at one predictable root.

```text
orchestration/
├── agents/      # stage-specific leaf-worker briefs
├── contracts/   # executor and reviewer interfaces
├── policies/    # FSM, gates, recovery and bounded automation
├── runtime/     # deterministic Python tools
├── schemas/     # JSON Schema documents
├── sub-agents/  # specialized leaf-worker briefs
├── tests/       # installed protocol tests
├── STATE.md
├── ACTION_JOURNAL.json
├── INCIDENTS.md
└── action-journal-history/  # created on demand
```

## Commands

Run the installed protocol suite:

```text
python3 -m unittest discover -s .hermes/orchestration/tests -p 'test_*.py'
```

Inspect individual runtime tools with `--help`, for example:

```text
python3 .hermes/orchestration/runtime/action_journal.py --help
python3 .hermes/orchestration/runtime/bounded_run_planner.py --help
python3 .hermes/orchestration/runtime/bounded_run_driver.py --help
```

Configure project-specific validation in `policies/GATES.md` before starting a demand. The mutable state files are controller-owned and must not be moved into a source layer.

Before dispatching a worker, load the matching brief from `agents/` together with only the applicable contract, policy excerpt and scoped project evidence. The brief never grants STATE or transition authority.

When a stage needs a narrower role, the controller may select one matching brief from `sub-agents/` instead. Stage agents never dispatch sub-agents; the one-leaf-worker invariant remains unchanged. A successful specialized action returns evidence to the controller but never completes or transitions the enclosing stage by itself.

`tdd-guardian.md` and `regression-hunter.md` are audit roles for TEST and REVIEW. The guardian answers whether the suite would go red if the rule broke, by mutating production code and reverting each mutation; the hunter answers what previously worked and may have stopped, by running the suites of consumers the change did not touch. Both are read-only, repair nothing, and mark every finding as proven or unproven. Grant them an explicit mutation and execution budget, and treat residue in the workspace as a blocker.

`api-contract-auditor.md` is an audit role for PLAN and REVIEW. It answers whether the client models still match the API, comparing them against the published specification and the deployed server across field names, types, nullability, enums, endpoint lifecycle and error envelopes. It ranks its sources instead of picking the convenient one, never invents a contract element to close a gap, and reports an unresolvable divergence as a gap for human decision. Tell it which environment is authoritative, and authorize any live call explicitly.

`security-reviewer.md` covers exploitable flaws and disclosure together: hardcoded credentials, insecure storage, authentication and authorization gaps, and sensitive data reaching logs or telemetry. It never reproduces a discovered secret value anywhere, and reports a committed secret as compromised and requiring rotation, because deleting the line does not revoke the credential.

`performance-auditor.md` is an audit role for PLAN and REVIEW. It looks for work the system does not need to do: duplicate requests, missing or wrong caching, N+1 and unindexed queries, unbounded results, recomputation and rebuilds, blocked critical paths, wasteful allocation and undisposed resources. Every finding carries a measurement or a counted operation and the input size at which it matters, and concluding that nothing is worth changing is an accepted result — a brief that rewards findings produces noise. Authorize any load test or shared-environment benchmark explicitly.
