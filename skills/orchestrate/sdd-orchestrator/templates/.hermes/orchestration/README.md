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
├── PROJECT_SETUP.md
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

Before the first demand, resolve `.hermes/orchestration/PROJECT_SETUP.md`. Inspect project evidence first, then ask only unresolved questions about issue tracker connectivity and read/write access, optional Obsidian binding, and other project-specific tools with their purpose and permissions. Accept `none`, never ask for credentials or product requirements, and validate connectivity read-only before recording it.

Configure project-specific validation in `policies/GATES.md` before starting a demand. `runtime/detect_stack.py` is a read-only helper for that step: it reports each ecosystem (Node, Python, Go, Rust, JVM, .NET, Ruby, PHP, Elixir, Swift, C/C++, Dart…) with the manifest that proves it, the CI providers present and a suggested command per gate, leaving a gate `null` when nothing supports it. The FSM gate names (`TEST_FOCUSED`, `FORMAT_CHANGED_FILES`, `ANALYZE`) are identical for every language. The mutable state files are controller-owned and must not be moved into a source layer.

## Obsidian second brain

`runtime/obsidian_binding.py` resolves every vault path through `.hermes/obsidian.json` at the repository root. That binding is deliberately **versioned**, not excluded: it is the connectivity itself and must survive a clone. `HERMES_OBSIDIAN_VAULT` overrides `vault_path` on a machine where the vault sits elsewhere; a missing vault is an error, never a silent fallback.

`runtime/vault_guard.py` refuses writes outside the bound `project_container` and hashes content for `capture_vault_baseline` / `assert_baseline_preserved`, because Git cannot see the vault and mtimes change without edits. The runtime directory is excluded from that baseline: every transition rewrites it by design.

`runtime/bootstrap_worktree.py` prepares an already-registered worktree and is the per-worktree counterpart to `scripts/install_project.py`. Runtime state lands in `<project_container>/<runtime_subpath>/<worktree-slug>/`, where the slug hashes the worktree's resolved absolute path, so two worktrees of one repository never share a `STATE.md` or journal — that sharing would break the one-executor-at-a-time guarantee. See `BOOTSTRAP.md`.

`runtime/migrate_to_vault.py`, `runtime/migrate_all_worktrees.py` and `runtime/consolidate_runtime.py` move pre-vault installations onto this model; `runtime/state_format.py` is the shared STATE serializer.

> A fresh `install_project.py` writes `STATE.md`, `INCIDENTS.md` and `ACTION_JOURNAL.json` at the orchestration root. Once a worktree is bootstrapped against a vault, those files live in the vault instead and the local copies are no longer the source of truth.

Before dispatching a worker, load the matching brief from `agents/` together with only the applicable contract, policy excerpt and scoped project evidence. The brief never grants STATE or transition authority. Executor schema version 3 carries evidence-backed facts, material assumptions/questions and stable acceptance-check IDs through every stage. TASKS produces the controller's authoritative non-empty acceptance mapping: ID, criterion, verification method, verifier and slice assignment. Workers may change only status and evidence. IMPLEMENT receives exactly one current slice ID plus an explicit disjoint completed set and may not add another TDD slice; TEST and every REVIEW status must match the full mapping rather than treating payload-declared values or green gates as authority.

When a stage needs a narrower role, the controller may select one matching brief from `sub-agents/` instead, subject to `policies/DISPATCH_POLICY.md`, whose default is **not** to dispatch: a specialist runs only when the controller can name the pending decision that depends on its answer, and only when a deterministic tool cannot answer the question first. Stage agents never dispatch sub-agents; the one-leaf-worker invariant remains unchanged. A successful specialized action returns evidence to the controller but never completes or transitions the enclosing stage by itself.

`tdd-guardian.md` and `regression-hunter.md` are audit roles for TEST and REVIEW. The guardian answers whether the suite would go red if the rule broke, by mutating production code and reverting each mutation; the hunter answers what previously worked and may have stopped, by running the suites of consumers the change did not touch. Both are read-only, repair nothing, and mark every finding as proven or unproven. Grant them an explicit mutation and execution budget, and treat residue in the workspace as a blocker.

`api-contract-auditor.md` is an audit role for PLAN and REVIEW. It answers whether the client models still match the API, comparing them against the published specification and the deployed server across field names, types, nullability, enums, endpoint lifecycle and error envelopes. It ranks its sources instead of picking the convenient one, never invents a contract element to close a gap, and reports an unresolvable divergence as a gap for human decision. Tell it which environment is authoritative, and authorize any live call explicitly.

`security-reviewer.md` covers exploitable flaws and disclosure together: hardcoded credentials, insecure storage, authentication and authorization gaps, and sensitive data reaching logs or telemetry. It never reproduces a discovered secret value anywhere, and reports a committed secret as compromised and requiring rotation, because deleting the line does not revoke the credential.

`performance-auditor.md` is an audit role for PLAN and REVIEW. It looks for work the system does not need to do: duplicate requests, missing or wrong caching, N+1 and unindexed queries, unbounded results, recomputation and rebuilds, blocked critical paths, wasteful allocation and undisposed resources. Every finding carries a measurement or a counted operation and the input size at which it matters, and concluding that nothing is worth changing is an accepted result — a brief that rewards findings produces noise. Authorize any load test or shared-environment benchmark explicitly.

`documentation-writer.md` is the only writing role among these: it reads the implemented code and brings technical documentation, ADRs, README and diagrams back in line with it. Assign its writable paths explicitly, as with the implementer. It treats existing documentation as a claim to verify rather than text to paraphrase, deletes documentation for code that no longer exists, and records an unexplained decision as an open question instead of inventing a rationale. It never edits code to match the text: a mismatch is reported, and the controller decides which side is wrong.

`architecture-guardian.md` is an audit role for PLAN and REVIEW. It reports layer traversal, wrong dependency direction, misplaced services, leaking abstractions and circular module dependencies — but only against rules the project itself declares, quoting the document, configuration or lint setting behind each finding. An undeclared convention is raised as a question, never enforced, and an inherited violation is reported separately from one the change introduced. Point it at the architecture documents and boundary tooling that state the rules; without them it has nothing legitimate to enforce.
