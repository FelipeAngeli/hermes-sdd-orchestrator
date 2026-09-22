---
name: sdd-orchestrator
description: Install and run safe project-local SDD orchestration.
version: 2.0.0
author: Felipe Angeli (FelipeAngeli), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [sdd, orchestration, tdd, code-review, bounded-automation]
    related_skills: []
---

# SDD Orchestrator Skill

Installs and operates a project-local SDD controller. It provides a strict FSM, controller/worker separation, state and journal recovery, structured worker contracts, bounded execution, protected-file ownership, TDD evidence, and final validation gates. It does not replace target-project instructions or approve external mutations.

## When to Use

- The user wants to start, resume, or govern an SDD delivery in a Git project.
- The user needs reproducible controller safeguards across projects.
- The project has no configured local SDD controller yet.

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
python3 <installed-skill>/scripts/install_project.py --target <project-root> --apply --json
```

The dry run must return `READY` before `--apply`. Installation must return `applied: true` and create an untracked `.hermes/` tree. It refuses tracked, conflicting, symlinked, or partial existing SDD state.

## Procedure

1. Read the target project's instructions and capture the Git baseline. Completion: protected pre-existing files are recorded before any edit.
2. Read the installed local controller documents: `.hermes.md`, `STATE.md`, `LOOP_POLICY.md`, `GATES.md`, `ACTION_RECOVERY.md`, and the stage contract. Completion: the current FSM state and allowed next action are known.
3. Configure the target-specific commands in `GATES.md`. Completion: formatter, focused test, analysis, and CI policy are explicit; no generic placeholder is used for DONE.
4. In `MANUAL`, perform only an explicitly requested action. For `BOUNDED_AUTO`, generate a fresh deterministic preview and obtain explicit approval linked to it. Completion: mode, budgets, and authorization are persisted in STATE.
5. Before every worker call, run action recovery; issue a stage-specific structured result contract; validate artifact schema, paths, symbols, ownership, and required evidence. Completion: STATE is committed and verified before the next action.
6. Run each implementation slice RED → minimal implementation → GREEN. Completion: every slice has expected failure and passing evidence.
7. Run final gates in `GATES.md` order and perform structured REVIEW. Completion: all required gate states allow DONE.

## Pitfalls

- This is a global **skill**, but it installs the actual orchestration policy and state **inside the target project**. Do not write SDD state under the Hermes home directory.
- `MANUAL` never advances automatically. `BOUNDED_AUTO` never starts without an approved fresh plan.
- A valid worker envelope is not proof of execution. Validate independent evidence before transitions.
- Do not use polling, background waits, force flags, overwrites, or Git reset/checkout to resolve conflicts.

## Verification

After project-local installation, run with `terminal`:

```text
python3 -m unittest discover -s .hermes/orchestration -p 'test_*.py'
```

The suite must pass. Confirm `git status --short` shows no newly tracked configuration paths and that target-specific gates are configured before starting a demand.
