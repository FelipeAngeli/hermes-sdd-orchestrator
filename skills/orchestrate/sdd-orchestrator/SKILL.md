---
name: sdd-orchestrator
description: Install and run safe project-local SDD orchestration.
version: 6.3.1
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
- Python 3.10+ is available.
- The target is an existing Git worktree root with an attached branch.
- The target owner authorizes adding local `.hermes/` configuration.

## Installation

Install this reusable skill once through the Hermes skill registry:

```text
hermes skills tap add FelipeAngeli/hermes-sdd-orchestrator
hermes skills install FelipeAngeli/hermes-sdd-orchestrator/skills/orchestrate/sdd-orchestrator --yes
```

The installed skill contains a project-local installer. From a Hermes session, invoke it with `terminal` using the installed skill's `scripts/install_project.py` path and the target worktree:

```text
python3 <installed-skill>/scripts/install_project.py --target <project-root> --json
python3 <installed-skill>/scripts/install_project.py --target <project-root> --typesafe-ai install --json
python3 <installed-skill>/scripts/install_project.py --target <project-root> --typesafe-ai install --apply --json
python3 <installed-skill>/scripts/install_project.py --target <project-root> --apply --json
```

The dry run must return `READY` before `--apply`. Installation must return `applied: true` and create an untracked `.hermes/` tree. If Python is older than 3.10, it returns `PYTHON_3_10_REQUIRED`; a target outside a Git worktree returns `GIT_REPOSITORY_REQUIRED`; and a repository without an initial commit returns `GIT_INITIAL_COMMIT_REQUIRED`. These are `BLOCKED` reports with an actionable `next_step`, produced before target writes. It also refuses tracked, conflicting, symlinked, or partial existing SDD state. Project-local engineering skills land under `.hermes/skills/`; Hermes loads them only after the user runs `hermes skills trust` for that repository, normally in a new session. TypeSafe opt-in also creates a private ignored `.hermes/orchestration/.env` placeholder without overwriting an existing regular file; `runtime/typesafe_connector.py preflight` is local-only and `evaluate` contacts Jev only when explicitly invoked. The installed `orchestration/hooks/` layer is inactive by default: activation is an explicit dedicated-profile opt-in after replacing `<ABSOLUTE_PROJECT_ROOT>` in `hooks/hooks.example.yaml`. The installer never edits profile configuration, SOUL, global skills, trust or hook consent.

## Procedure

1. Read the target project's instructions and capture the Git baseline. Completion: protected pre-existing files are recorded before any edit.
2. Read the installed local controller documents: `.hermes.md`, `.hermes/orchestration/STATE.md`, `.hermes/orchestration/PROJECT_SETUP.md`, `.hermes/orchestration/policies/LOOP_POLICY.md`, `.hermes/orchestration/policies/GATES.md`, `.hermes/orchestration/policies/ACTION_RECOVERY.md`, the matching stage brief under `.hermes/orchestration/agents/`, and the applicable contract. Completion: the current FSM state and allowed next action are known.
3. Before the first demand, inspect repository evidence and ask only unresolved onboarding questions from the install report or `PROJECT_SETUP.md`: issue tracker connectivity and separate read/write permission, optional Obsidian binding, optional project-local TypeSafe skill installation for Jev guidance, and other project-specific tools with their purpose and permissions. Accept `none`; never ask for credentials or product requirements; validate configured connectivity read-only before recording the answer. For Obsidian, use `runtime/obsidian_connector.py discover --json` to list official-CLI candidates without a binding, then `preflight --repo . --json` after `.hermes/obsidian.json` exists; `READY_FILESYSTEM` is a valid offline fallback and neither command writes a note. For TypeSafe, `runtime/typesafe_connector.py preflight --json` checks only local key configuration; never run `evaluate` unless the current action explicitly authorizes sending the supplied state/questions to TypeSafe. Completion: all four answers are resolved and setup is marked `COMPLETE`.
4. Configure the target-specific commands in `.hermes/orchestration/policies/GATES.md`. Run `python3 .hermes/orchestration/runtime/detect_stack.py --target .` (the installer's dry run already includes this report under `stack`), prefer the project's own scripts and CI steps over the suggestions, and run each command once before recording it. Completion: formatter, focused test, analysis, and CI policy are explicit and verified; no `UNCONFIGURED` placeholder is used for DONE.
5. In `MANUAL`, perform only an explicitly requested action. Schema 1 BOUNDED_AUTO requires a fresh deterministic preview and approval linked to that exact plan. Schema 2 LOCAL_DELIVERY uses its existing explicit authorization bound to ticket, scope, workspace and cumulative limits; replanning does not request a new approval. Completion: mode, budgets, and authorization are persisted in STATE.
6. Before every worker call, run action recovery; load the matching `agents/<stage>.md` brief or one narrower controller-selected `sub-agents/<role>.md` brief; check the stage context manifest with `runtime/stage_context.py check` (scoped excerpts, `project-context-guardian` before PLAN/IMPLEMENT, slice paths, and byte-verified required project-local `SKILL.md`/reference descriptors; their hashes are part of the approved slice hash); issue a stage-specific structured result contract; validate artifact schema, paths, symbols, write scope, ownership, material context, cited acceptance evidence and required TDD evidence with `runtime/validate_protocol.py --context`. When a valid result fails verification, consult `runtime/correction_loop.py decide` before any retry. Completion: STATE is committed and verified before the next action, or the loop paused with an explicit `stop_reason`.
7. Carry evidence-backed facts, validated assumptions, unresolved-question materiality and stable acceptance checks through every stage. Completion: the controller owns each check's ID, criterion, verification method, verifier and slice assignment; workers change only status/evidence; TASKS assigns a non-empty set; IMPLEMENT receives exactly one current and an explicit disjoint completed set; TEST and every REVIEW status match the full mapping.
8. Run each implementation slice RED → minimal implementation → GREEN. Completion: every slice has expected failure and passing evidence.
9. Run final gates in `.hermes/orchestration/policies/GATES.md` order and perform structured REVIEW. Completion: REVIEW independently verifies accepted outcomes, not only green gates, and all required gate states allow DONE.

## Pitfalls

- This is a global **skill**, but it installs the actual orchestration policy and state **inside the target project**. Do not write SDD state under the Hermes home directory.
- `MANUAL` never advances automatically. Schema 1 BOUNDED_AUTO never starts without an approved fresh plan; schema 2 LOCAL_DELIVERY requires its persisted request authorization and does not seek fresh approval for replans.
- A valid worker envelope is not proof of execution. Validate independent evidence before transitions.
- Never retry a failed verification with an already-tried hypothesis or an unchanged change; the correction loop stops with `NO_NEW_HYPOTHESIS` or `NO_PROGRESS` instead of looping.
- Passing tests and gates prove only the checks they execute. Do not infer product acceptance without mapping evidence to every accepted observable outcome.
- `detect_stack.py` suggestions are inferences from manifests, never verified commands. A gate reported `UNKNOWN` must be configured by a human; do not invent a command for it.
- Do not use polling, background waits, force flags, overwrites, or Git reset/checkout to resolve conflicts.

## Verification

After project-local installation, run with `terminal`:

```text
python3 -m unittest discover -s .hermes/orchestration/tests -p 'test_*.py'
```

The suite must pass. Confirm `git status --short` shows no newly tracked configuration paths and that target-specific gates are configured before starting a demand.
