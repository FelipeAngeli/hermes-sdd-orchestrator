# Hermes SDD Orchestrator

A reusable, Hermes-native **SDD Orchestrator skill**. Install it through the Hermes skills registry; when needed, the skill installs a safety-first controller inside the current project.

The architecture follows the skill-collection pattern used by [Ow1onp/hermes-agent-skills](https://github.com/Ow1onp/hermes-agent-skills): a discoverable `skills/` tree, a self-contained `SKILL.md` entry point, supporting scripts/templates co-located with the skill, and registry-based installation. The target project's orchestration state remains local and untracked.

## Quick start

```bash
# Register this GitHub repository as a Hermes skill source.
hermes skills tap add FelipeAngeli/hermes-sdd-orchestrator

# Install the SDD Orchestrator skill in the active Hermes profile.
hermes skills install FelipeAngeli/hermes-sdd-orchestrator/skills/orchestrate/sdd-orchestrator --yes
```

Then load `/skill sdd-orchestrator` in Hermes. It guides safe installation of the project-local controller.

## What gets installed where

```text
Hermes profile (once)                         Target Git project (per project)
─────────────────────                         ──────────────────────────────
~/.hermes/skills/sdd-orchestrator/            .hermes.md
├── SKILL.md                                  .hermes/orchestration/
├── scripts/install_project.py                ├── agents/      # stage-specific worker briefs
└── templates/                                ├── contracts/   # worker/review interfaces
    └── .hermes/                              ├── policies/    # FSM, gates, recovery
                                               ├── runtime/     # executable controller tools
                                               ├── schemas/     # JSON validation contracts
                                               ├── sub-agents/  # specialized leaf-worker briefs
                                               ├── tests/       # installed protocol tests
                                               ├── STATE.md (fresh local state)
                                               ├── ACTION_JOURNAL.json
                                               └── INCIDENTS.md
```

The skill is reusable. The controller configuration, state, incident log, and journal are project-local and added only to the target repository's Git `info/exclude`. The installer never overwrites existing configuration or modifies tracked files.

## Efficient, safe dispatch

The installed entrypoint keeps worker context small and stage-specific, with one leaf worker at a time and controller-owned state transitions. Updated bundles apply to new installations; the installer deliberately does not overwrite an existing project configuration, so migrate an existing installation only after reviewing its local configuration.

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for layer responsibilities, dependency direction, and change rules.

```text
skills/
└── orchestrate/
    └── sdd-orchestrator/
        ├── SKILL.md                 # Hermes-native entry point
        ├── scripts/install_project.py
        └── templates/.hermes/       # project-local controller payload
            └── orchestration/
                ├── agents/
                ├── contracts/
                ├── policies/
                ├── runtime/
                ├── schemas/
                ├── sub-agents/
                └── tests/

tests/
└── test_sdd_orchestrator_skill.py   # installs the bundled skill into a fixture repo
```

## Project-local installation

The skill runs the bundled installer. It requires an existing Git worktree root with an attached branch:

```bash
python3 <installed-skill>/scripts/install_project.py \
  --target /absolute/path/to/project --json

python3 <installed-skill>/scripts/install_project.py \
  --target /absolute/path/to/project --apply --json
```

First run is a dry run. Apply only when it returns `READY`. Re-running a complete installation returns `ALREADY_INITIALIZED`; partial, tracked, symlinked, or conflicting configuration is blocked.

Before the first demand, configure the target's `.hermes/orchestration/policies/GATES.md` with its real format, test, analysis, and CI commands.

## Development and verification

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m unittest discover -s skills/orchestrate/sdd-orchestrator/templates/.hermes/orchestration/tests -p 'test_*.py'
```

## License

MIT. See [LICENSE](LICENSE).
