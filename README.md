# Hermes SDD Orchestrator

Project-local **Specification-Driven Development** controller for Hermes Agent. It keeps the controller/worker boundary explicit and supplies the V2.5 state machine, bounded execution policy, action journal/recovery protocol, strict worker-result schemas, review contract, and deterministic planner/driver tooling.

It does **not** install a global Hermes skill, alter `~/.hermes`, create a ticket, start an agent, commit, push, or change a product repository's tracked files.

## Install into another project

Prerequisites: Git, Python 3.10+, and [Hermes Agent](https://hermes-agent.nousresearch.com/docs). `jsonschema` is required only to run the included protocol tests.

```bash
git clone git@github.com:FelipeAngeli/hermes-sdd-orchestrator.git ~/src/hermes-sdd-orchestrator
cd ~/src/hermes-sdd-orchestrator

# Inspect what would be installed; no writes.
python3 install.py --target /absolute/path/to/your-project --json

# Install after reviewing the dry run.
python3 install.py --target /absolute/path/to/your-project --apply --json
```

The target must be an existing Git worktree root with an attached branch. Installation refuses symlinked, tracked, or non-empty SDD state/configuration paths. It writes the configuration under the target's `.hermes/`, initializes fresh `STATE.md`, `INCIDENTS.md`, and `ACTION_JOURNAL.json`, and adds only these local paths to Git's `info/exclude`. Nothing is overwritten.

## First use

1. Edit the target project's `.hermes/orchestration/GATES.md` with real formatter, test, analyzer, and CI commands. The distributed generic policy has CI explicitly disabled until the target owner configures it.
2. Start Hermes in the target project.
3. Request one explicit SDD action in `MANUAL` mode, or ask for a bounded-run preview before authorizing `BOUNDED_AUTO`.
4. Keep product-specific conventions in the target project's `AGENTS.md` / `CLAUDE.md`; this package does not replace them.

## Guardrails

- Fixed FSM: `SPECIFY → CLARIFY → PLAN → TASKS → IMPLEMENT → TEST → REVIEW → DONE`.
- Git baseline and protected pre-existing-file ownership are mandatory.
- Workers return schema-validated, stage-specific result envelopes; workers do not own state or transitions.
- Implementation is sliced TDD: expected RED followed by GREEN for every slice.
- The controller validates paths, symbols, impact, ownership, gates, and action-journal recovery before state changes.
- `MANUAL` is the default. `BOUNDED_AUTO` requires a fresh approved plan and has finite budgets.
- Commit, push, PR, external mutations, and destructive operations require explicit user approval.

## Verification

Run the self-contained Python protocol tests from the target after installing `jsonschema`:

```bash
python3 -m pip install jsonschema
python3 -m unittest discover -s .hermes/orchestration -p 'test_*.py'
```

The target repository's own required gates remain defined by its local `GATES.md`.

## Contents

- `template/.hermes.md` — controller operating rules
- `template/.hermes/orchestration/` — contracts, JSON schemas, policy documents, action journal/recovery, bounded-run planner and drivers, and tests
- `install.py` — safe project-local installer

## License

MIT. See [LICENSE](LICENSE).
