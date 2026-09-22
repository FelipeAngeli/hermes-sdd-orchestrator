# Installed orchestration layout

This directory separates controller concerns while keeping mutable local state at one predictable root.

```text
orchestration/
├── agents/      # stage-specific leaf-worker briefs
├── contracts/   # executor and reviewer interfaces
├── policies/    # FSM, gates, recovery and bounded automation
├── runtime/     # deterministic Python tools
├── schemas/     # JSON Schema documents
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
