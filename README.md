# Hermes SDD Orchestrator

A reusable, Hermes-native **SDD Orchestrator skill**. Install it through the Hermes skills registry; when needed, the skill installs a safety-first controller inside the current project.

The architecture follows the skill-collection pattern used by [Ow1onp/hermes-agent-skills](https://github.com/Ow1onp/hermes-agent-skills): a discoverable `skills/` tree, a self-contained `SKILL.md` entry point, supporting scripts/templates co-located with the skill, and registry-based installation. The target project's orchestration state remains local and untracked.

**Documentation:** [docs/README.md](docs/README.md) — overview, architecture, one page per component, glossary.

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
├── SKILL.md                                  .hermes/obsidian.json  # vault binding (versioned)
├── scripts/install_project.py                .hermes/orchestration/
└── templates/                                ├── agents/      # stage-specific worker briefs
    └── .hermes/                              ├── contracts/   # worker/review interfaces
                                               ├── hooks/       # opt-in Hermes event adapters
                                               ├── policies/    # FSM, gates, recovery
                                               ├── runtime/     # executable controller tools
                                               ├── schemas/     # JSON validation contracts
                                               ├── sub-agents/  # specialized leaf-worker briefs
                                               ├── tests/       # installed protocol tests
                                               ├── STATE.md (fresh local state)
                                               ├── PROJECT_SETUP.md (orchestrator connectivity)
                                               ├── ACTION_JOURNAL.json
                                               └── INCIDENTS.md
```

`.hermes/obsidian.json` is the one exception to the untracked rule: it binds the project to its Obsidian vault and is versioned so connectivity survives a clone. When a worktree is bootstrapped against a vault, `STATE.md`, `ACTION_JOURNAL.json` and `INCIDENTS.md` move into that vault, keyed per worktree — see `templates/.hermes/orchestration/BOOTSTRAP.md`.

The skill is reusable. The controller configuration, project setup, state, incident log, and journal are project-local and added only to the target repository's Git `info/exclude`. The installer never overwrites existing configuration or modifies tracked files.

## Repository-local hooks (opt-in)

Each installation includes `.hermes/orchestration/hooks/`, parallel to `agents/` and `sub-agents/`. The scripts connect Hermes shell-hook events to the existing runtime without introducing global SDD policy:

- `pre_tool_call` fails closed for `write_file` and `patch`, validating every direct/V4A target against the live IMPLEMENT slice or bound vault container.
- `pre_verify` keeps the turn open until HUMAN/AGENT acceptance evidence is bound to the current workspace, HEAD, ticket, stage, slice, action, and attempt.
- `subagent_stop` stores one immutable, non-sensitive result digest in the authoritative local or vault-backed action history.
- optional `pre_llm_call` injects a bounded STATE summary; missing, non-UTF-8, malformed, or wrong-shaped STATE returns only `SDD state unavailable.` and never parser details or source values.

Hooks are installed **inactive**. To opt in, use a dedicated Hermes profile, replace `<ABSOLUTE_PROJECT_ROOT>` in `hooks/hooks.example.yaml`, then merge that block into the profile configuration and approve each `(event, command)` pair. Absolute, quoted commands bind persistent consent to one checkout and work when its path contains spaces. The installer never edits `~/.hermes/config.yaml`, SOUL, global skills, or consent. Full contracts and limitations: [Repository-local Hermes hooks](docs/components/hooks.md).

## Efficient, safe dispatch

The installed entrypoint keeps worker context small and stage-specific, with one leaf worker at a time and controller-owned state transitions. Updated bundles apply to new installations; the installer deliberately does not overwrite an existing project configuration, so migrate an existing installation only after reviewing its local configuration.

## Sub-agents

`pr-reviewer` ships with every installation, so any project can use it. In **this** repository it reviews every pull request and every earlier review of it (see [AGENTS.md](AGENTS.md#pull-request-review)).

`sub-agents/` holds narrow leaf-worker briefs the controller may select when a stage needs a specialist. They are dispatched only by the controller, never by another agent, and never own STATE or transitions. All are language- and stack-agnostic.

| Brief | Stages | Purpose |
| --- | --- | --- |
| `project-context-guardian` | SPECIFY, PLAN, IMPLEMENT | Cache-first project context, required before PLAN and IMPLEMENT; records code/doc divergences. |
| `investigator` | SPECIFY, CLARIFY, PLAN | Bounded codebase investigation before a decision is made. |
| `data-flow-tracer` | PLAN, IMPLEMENT | One demand's path: UI → state → service → repository → API and back. |
| `impact-analyst` | PLAN, TASKS | Full blast radius of one proposed contract or behavior change. |
| `tdd-implementer` | IMPLEMENT | One authorized vertical slice under strict RED → minimal → GREEN. |
| `test-runner` | TEST | Focused test execution with exact commands and exit codes. |
| `code-reviewer` | REVIEW | Delivered changes against requirements, ownership, tests and gates. |
| `security-reviewer` | REVIEW | Exploitable flaws plus disclosure: secrets, storage, auth, logs. |
| `tdd-guardian` | TEST, REVIEW | Whether the suite would go red if the rule broke. |
| `regression-hunter` | TEST, REVIEW | What previously worked and may have stopped. |
| `api-contract-auditor` | PLAN, REVIEW | Client models against the specification and the deployed server. |
| `performance-auditor` | PLAN, REVIEW | Work the system does that it does not need to do. |
| `architecture-guardian` | PLAN, REVIEW | Violations of the project's own declared architectural rules. |
| `spec-consistency-guardian` | TASKS, REVIEW | Breaks in the chain SPEC → PLAN → TASKS → CODE → TESTS. |
| `dependency-auditor` | PLAN, REVIEW | Versions, duplication, compatibility and unmaintained packages. |
| `release-readiness-auditor` | REVIEW | Whether this can ship: READY, BLOCKED or READY_WITH_RISK. |
| `pr-reviewer` | REVIEW | One pull request as it will merge — scope, tests, checks, breaking changes, changelog, commits — plus an audit of every earlier review. Host-neutral; never posts on its own. |
| `documentation-writer` | IMPLEMENT, REVIEW | Documentation, ADRs, README and diagrams realigned with the code. |

The eight audit roles return findings only when evidence supports them, and each is bound by the failure mode specific to its domain:

- The **TDD guardian** proves a weak test by mutating production code and observing which tests stay green, because reading a test yields an opinion while mutating it yields a fact.
- The **regression hunter** runs the suites of consumers the change did not touch; a consumer whose tests pass without exercising the affected path is reported as uncovered risk, not as safe.
- The **API contract auditor** ranks its sources — deployed runtime over served specification over server source over committed spec, with client models last — instead of trusting whichever is nearest, and never invents a contract element to close a gap.
- The **performance auditor** reports a cost only with a measurement or a counted operation behind it, states the input size at which it matters, and may conclude that nothing is worth changing; a role rewarded for findings will produce them.
- The **architecture guardian** cites the project's declared rule behind every violation. An undeclared convention is raised as a question, never enforced, since every codebase violates someone's preferred architecture.
- The **spec consistency guardian** walks SPEC → PLAN → TASKS → CODE → TESTS in both directions and never infers a missing requirement: inferring one would turn unauthorized scope into retroactively justified scope, which is the failure it exists to catch.
- The **dependency auditor** prefers what the project already depends on over anything new, and hands a vulnerability to the security reviewer and a layer violation to the architecture guardian instead of ruling on them; two roles over one domain let each assume the other checked it.
- The **release readiness auditor** returns READY, BLOCKED or READY_WITH_RISK. An unverified item is BLOCKED, never READY_WITH_RISK — not knowing is not the same as knowing and accepting — and the risk verdict requires a named human who accepted it.

All eight keep the workspace read-only, revert every temporary step, repair nothing, and report proven findings separately from unproven suspicions.

`documentation-writer` is the only writing role among these, and its risk runs the other way: a read-only auditor produces a wrong finding that review can reject, while a writer produces fluent prose describing code that does not exist, which readers trust because it reads well. It verifies every symbol, command and path against the repository before writing it, deletes documentation whose subject is gone, and records an unexplained decision as an open question rather than inventing a rationale. Like the implementer, it writes only to paths the controller assigns.

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for layer responsibilities, dependency direction, and change rules, and the [documentation index](docs/README.md) for one page per component.

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
                ├── hooks/
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

Before the first demand, Hermes resolves `.hermes/orchestration/PROJECT_SETUP.md`: it inspects repository evidence and asks only unanswered questions about issue tracker access, optional Obsidian binding, and other project-specific tools. `none` is valid; it never asks for credentials or product requirements. Then configure the target's `.hermes/orchestration/policies/GATES.md` with its real format, test, analysis, and CI commands.

## Any language, any project

The controller is language-neutral. The FSM, contracts, schemas, stage briefs and sub-agents never name a toolchain; gate actions are named by role (`TEST_FOCUSED`, `FORMAT_CHANGED_FILES`, `ANALYZE`, `CI`). The **only** project-specific input is the command table in `policies/GATES.md`.

To bring the orchestrator into a new project:

```bash
# 1. Dry run: preflight + detected stack (read-only).
python3 <installed-skill>/scripts/install_project.py --target /path/to/project --json

# 2. Install the untracked, project-local controller.
python3 <installed-skill>/scripts/install_project.py --target /path/to/project --apply --json

# 3. Inspect the stack again at any time.
cd /path/to/project && python3 .hermes/orchestration/runtime/detect_stack.py --target .
```

The dry run's `stack` field lists every ecosystem found (root and up to two levels deep, for monorepos), the manifest that proves it, the CI providers present, the instruction files to read first (`AGENTS.md`, `CLAUDE.md`, ADRs…) and a suggested command per gate. Supported ecosystems: Node/TypeScript (npm, pnpm, yarn, bun), Python, Go, Rust, Java/Kotlin (Gradle, Maven), .NET, Ruby, PHP, Elixir, Swift, C/C++ (CMake) and Dart/Flutter (with FVM). A gate without evidence is `null`, never guessed.

4. Resolve the installer report's `onboarding.questions` in `PROJECT_SETUP.md`; ask only unresolved orchestrator connectivity questions and accept `none`.
5. Copy the suggestions into `GATES.md` only after running each command once in the project; the project's own scripts and CI steps take precedence over the suggestions.
6. If enabled during onboarding, bind the Obsidian vault with `.hermes/obsidian.json` (see `BOOTSTRAP.md`).
7. Run the installed suite: `python3 -m unittest discover -s .hermes/orchestration/tests -p 'test_*.py'`.

Requirements on the target are only Git and Python 3.10+ (plus `jsonschema` for the bounded-run tools); the project itself can be written in anything.

## Development and verification

```bash
# Skill packaging and sub-agent contracts.
python3 -m unittest discover -s tests -p 'test_*.py'

# Installed controller protocol, drivers and journal.
python3 -m unittest discover -s skills/orchestrate/sdd-orchestrator/templates/.hermes/orchestration/tests -p 'test_*.py'
```

The first suite installs the bundled skill into a fixture repository and asserts that every sub-agent brief ships with a declared role, allowed stages and result schema, and that this README lists exactly the briefs the bundle contains.

## Contributing

This is an open-source project and contributions are welcome. You can [suggest an improvement](https://github.com/FelipeAngeli/hermes-sdd-orchestrator/issues/new?template=feature_request.yml), [request support for a language or tool](https://github.com/FelipeAngeli/hermes-sdd-orchestrator/issues/new?template=ecosystem_support.yml), [report a bug](https://github.com/FelipeAngeli/hermes-sdd-orchestrator/issues/new?template=bug_report.yml) or open a pull request. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, tests and project rules, [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), and [SECURITY.md](SECURITY.md) for private vulnerability reports.

## License

Open source under the [MIT License](LICENSE): free to use, modify, distribute and use commercially, with the copyright notice preserved. Contributions are accepted under the same license.
