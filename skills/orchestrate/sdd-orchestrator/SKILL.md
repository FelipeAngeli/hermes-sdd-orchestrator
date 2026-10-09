---
name: sdd-orchestrator
description: Install and run safe project-local SDD orchestration.
version: 14.0.2
author: Felipe Angeli (FelipeAngeli), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [sdd, orchestration, tdd, code-review, bounded-automation, language-agnostic]
    related_skills: []
---

# SDD Orchestrator Skill

Installs and operates a project-local SDD controller. It provides a strict FSM, controller/worker separation, state and journal recovery, structured worker contracts, bounded execution, protected-file ownership, TDD evidence, and final validation gates. It does not replace target-project instructions or approve external mutations.

## When to Use

- The user wants to start, resume, or govern an SDD delivery in a Git project.
- The user needs reproducible controller safeguards across projects.
- The project has no configured local SDD controller yet.
- The project may use any language or stack: the FSM, contracts, schemas and runtime are language-neutral, and only `policies/GATES.md` carries project-specific commands.

Do not use it to bypass a target project's `AGENTS.md`, `CLAUDE.md`, existing tracked configuration, or explicit approval requirements.

## Prerequisites

- Hermes Agent and Git are installed.
- Python 3.10+ and the `jsonschema` package are available to the same interpreter.
- The target is an existing Git worktree root with an attached branch.
- An existing Obsidian vault and a project container inside it. By default nothing is written to the target repository; `--local-storage` is the legacy opt-in that installs `.hermes/` inside the repository instead.

## Installation

Install this reusable skill once through the Hermes skill registry:

```text
hermes skills tap add FelipeAngeli/hermes-sdd-orchestrator
hermes skills install FelipeAngeli/hermes-sdd-orchestrator/skills/orchestrate/sdd-orchestrator --yes
```

The installed skill contains the installer. From a Hermes session, invoke it with `terminal` using the installed skill's `scripts/install_project.py` path, the target worktree, and the Obsidian vault/project container that will hold **all** orchestrator data:

```text
python3 <installed-skill>/scripts/install_project.py --target <project-root> --obsidian-vault <abs-vault> --obsidian-project <Projects/Name> --json
python3 <installed-skill>/scripts/install_project.py --target <project-root> --obsidian-vault <abs-vault> --obsidian-project <Projects/Name> --apply --json
python3 <installed-skill>/scripts/install_project.py --target <project-root> --obsidian-vault <abs-vault> --obsidian-project <Projects/Name> --typesafe-ai install --automatic-jev-governance --apply --json
```

With Obsidian storage the controller, `PROJECT_SETUP.md`, playbooks, binding and per-worktree runtime land under `<vault>/<project>/` (runtime in `.hermes-runtime/<worktree-slug>/`); the report shows `target_writes: []` and the run fails with `TARGET_WORKTREE_CHANGED` if the repository moved. Run the controller tools from the container, for example `python3 <vault>/<project>/.hermes/orchestration/runtime/<tool>.py`, with the repository as working directory. Missing Obsidian flags return `OBSIDIAN_BINDING_REQUIRED`; a vault or container that overlaps any checkout of the repository (the target, linked worktrees, superprojects) or its Git directory returns `OBSIDIAN_VAULT_OVERLAPS_TARGET` before any write, and a repository that changes during the run rolls back every vault path the run wrote, including the TypeSafe integration. The dry run refuses a container nested in, or holding, another project container with `WIKI_CONTAINER_NESTED` and suggests a sibling container; never move a container or edit its binding by hand. The report gives `controller_location` (the controller sits in the hidden `.hermes` folder), an `obsidian_url` link, and a `CONTAINER_PATH_SHELL_UNSAFE` warning for non-ASCII or shell-special container paths. Controller files must match the template byte for byte, except the owner files `policies/GATES.md` and `policies/EXECUTORS.md` once this installer set up the controller (in both storage modes): they are then owned by the project, listed with their hash under `preserved_owner_files`, and anyone who can write the vault controls the gate commands. To update an existing installation to this skill version, rerun with `--upgrade` (dry run, report `UPGRADE_READY`) and then `--upgrade --apply`; it keeps owner files, blocks on edited managed files, backs up what it replaces and is idempotent (`ALREADY_CURRENT`). Installations without `.hermes/orchestration/INSTALL_MANIFEST.json` need `--accept-current-as-baseline` after review. A vault that is itself a Git repository is fine: tracked-path conflicts apply only to the user's repository. The installer does not register the container with Hermes skill discovery; the controller loads its playbooks through the stage manifest. No credential file is created in this mode; export `TYPESAFE_API_KEY`/`JEV_AI_API_KEY` in the environment. The rest of this paragraph describes the legacy `--local-storage` layout.

The dry run must return `READY` before `--apply`. A successful write returns `status: APPLIED` with `applied: true` and creates an untracked `.hermes/` tree; a later no-op returns `ALREADY_INITIALIZED`. If Python is older than 3.10, it returns `PYTHON_3_10_REQUIRED`; if the interpreter lacks `jsonschema`, it returns `JSONSCHEMA_REQUIRED`; a target outside a Git worktree returns `GIT_REPOSITORY_REQUIRED`; a repository without an initial commit returns `GIT_INITIAL_COMMIT_REQUIRED`; and detached HEAD returns `ATTACHED_BRANCH_REQUIRED`. These are `BLOCKED` reports with an actionable `next_step`, produced before target writes. It also refuses tracked, conflicting, symlinked, special-file, or partial existing SDD state. Apply uses descriptor-anchored no-follow exclusive writes, safe file modes, a Git index lock, exact root-anchored exclusions, and rollback of newly created paths and exclusion edits on failure. Missing managed exclusions are repaired on a later apply without rewriting installed controller files. Project-local engineering skills land under `.hermes/skills/`; Hermes loads them only after the user runs `hermes skills trust` for that repository, normally in a new session. TypeSafe opt-in also creates a private ignored `.hermes/.env` placeholder without overwriting an existing regular file; `runtime/typesafe_connector.py preflight` is local-only and `evaluate` contacts Jev only when explicitly invoked. The installed `orchestration/hooks/` layer is inactive by default: activation is an explicit dedicated-profile opt-in after replacing `<ABSOLUTE_PROJECT_ROOT>` in `hooks/hooks.example.yaml`. The installer never edits profile configuration, SOUL, global skills, trust or hook consent.

## Procedure

Paths below are relative to the controller root: the Obsidian project container `<vault>/<project>/` by default, or the repository with `--local-storage`. Commands run with the repository as working directory. `SDD` stands for `python3 .hermes/orchestration/runtime/sdd.py`.

1. Read the target project's instructions and the controller card `.hermes.md` (about 4 KB). Do **not** read the policies, schemas, runtime sources or `--help` up front: `sdd.py` encodes them. Completion: you know the loop below.
2. Run `SDD status`. It returns one compact JSON with storage paths, stage/status/mode/ticket, the journal recovery decision, budgets left, `next_action` and `next_command`. Completion: the current FSM state and allowed next action are known.
3. Before the first demand, read `.hermes/orchestration/PROJECT_SETUP.md` and ask only unresolved onboarding questions: issue tracker connectivity and separate read/write permission, Obsidian binding, approvers, optional project-local TypeSafe skill installation, explicit automatic Jev consent, and other project-specific tools with their purpose and permissions. Accept `none`; never ask for credentials or product requirements; validate configured connectivity read-only (`runtime/obsidian_connector.py discover|preflight`). Only `--automatic-jev-governance` recording the exact enabled consent plus READY local preflight is standing authorization; after it, run `runtime/semantic_governor.py decide` after deterministic facts without asking again — batch and cache classifications; confidence below `0.70` is `REVIEW`, never retried (details: `policies/DISPATCH_POLICY.md`). Completion: setup is `COMPLETE`.
4. Configure gate commands in `.hermes/orchestration/policies/GATES.md` (`runtime/detect_stack.py --target .` suggests them; prefer the project's own scripts and run each once). Completion: no `UNCONFIGURED` placeholder is needed for DONE; `SDD next` stops with `GATE_COMMAND_UNCONFIGURED` otherwise.
5. Start a demand with `SDD start --ticket <id> --title <t> --objective <o> [--deliverable-kind CODE|DECISION_DOC|BOTH]`. It moves IDLE → SPECIFY, records protected pre-existing files as the baseline and prints the authorization preview with its limits once. Schema 1 BOUNDED_AUTO requires a fresh deterministic preview and approval linked to that exact plan. Schema 2 LOCAL_DELIVERY uses its existing explicit authorization bound to ticket, scope, workspace and cumulative limits; replanning does not request a new approval. A request such as "orquestre/implemente <demand>" is that authorization; in MANUAL, "continue/pode seguir" authorizes progress to the next human checkpoint.
6. Loop: run `SDD next`, execute exactly the printed `commands` in order (stop at the first non-zero exit), and run `SDD next` again. The batch covers recovery, the stage-context manifest (`SDD manifest`), `stage_context.py check` (including `max_prompt_bytes`), the executor launch, result validation with `validate_protocol.py --context`, the journaled STATE commit, gates, wiki records and the transition. Completion: `end_turn` is true with a `stop_reason`.
7. At a stop, report `stop_reason` and `next_step` to the user. Human decisions go through the printed command: `SDD answer` (material question), `SDD waive --check AC-n --by requester --quote "..." --reason "..."` (HUMAN acceptance), `SDD budget --raise <budget> --quote "..."`, `SDD unblock`. Every stop reason has exactly one next action in `policies/LOOP_POLICY.md` §9.
8. Terminal progress (POSIX, optional): `runtime/terminal_progress.py start|activity|stage|finish`; it never replaces STATE.

## Pitfalls

- This is a global **skill**, but it installs the actual orchestration policy and state **inside the project container**. Do not write SDD state under the Hermes home directory.
- Never edit STATE.md or the action journal by hand, and never hand-build manifests, snapshots or payloads: `sdd.py` generates them. If a printed command fails, run `SDD next` — it reads the journal and prints the recovery.
- `MANUAL` never advances past a human checkpoint. Schema 1 BOUNDED_AUTO never starts without an approved fresh plan; schema 2 LOCAL_DELIVERY requires its persisted request authorization and does not seek fresh approval for replans.
- A valid worker envelope is not proof of execution; passing gates prove only the checks they execute. Acceptance needs evidence mapped to every check, or a recorded waiver.
- Never retry a failed verification with an already-tried hypothesis or an unchanged change; the correction loop stops with `NO_NEW_HYPOTHESIS` or `NO_PROGRESS`.
- An `EXECUTOR_TIMEOUT` is recovered once with a reduced-context FULL_REPLACEMENT retry; a second timeout blocks. Do not raise timeouts blindly.
- Do not use polling, background waits, force flags, overwrites, or Git reset/checkout to resolve conflicts.

## Verification

After project-local installation, run with `terminal`:

```text
python3 -m unittest discover -s .hermes/orchestration/tests -p 'test_*.py'
```

The suite must pass. Confirm `git status --short` shows no newly tracked configuration paths and that target-specific gates are configured before starting a demand.
