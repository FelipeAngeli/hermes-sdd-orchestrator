# Skill and installer

[Docs index](../README.md) · Next: [Gates and stack detection](gates-and-stack-detection.md) · Related: [Obsidian vault](obsidian-vault.md), [FSM and bounded loop](fsm-and-loop.md)

**Files:** `SKILL.md`, `scripts/install_project.py`, `templates/.hermes.md`, `orchestration/README.md` (the installed layout guide, `templates/.hermes/orchestration/README.md`).

## `SKILL.md`: Hermes entry point

`skills/orchestrate/sdd-orchestrator/SKILL.md` is what Hermes loads with `/skill sdd-orchestrator`. Its frontmatter carries the name, the version (bumped on every contract change) and the tags. Its body explains when to use the skill, the prerequisites (Git, Python 3.10+, an attached-branch worktree root), installation, the nine-step operating procedure, pitfalls and verification. The procedure now carries evidence-backed context and acceptance checks across stages, checks each dispatch's context and slice contract, bounds corrective retries, and requires REVIEW to verify outcomes independently of technical gates.

Install it once per Hermes profile:

```text
hermes skills tap add FelipeAngeli/hermes-sdd-orchestrator
hermes skills install FelipeAngeli/hermes-sdd-orchestrator/skills/orchestrate/sdd-orchestrator --yes
```

## `install_project.py`: per-project installer

```text
python3 <skill>/scripts/install_project.py --target <repo-root> --json           # dry run
python3 <skill>/scripts/install_project.py --target <repo-root> --apply --json   # write
```

| Flag | Meaning |
| --- | --- |
| `--target` | Existing Git worktree root (default `.`). A subdirectory is rejected with `TARGET_NOT_REPOSITORY_ROOT`. |
| `--apply` | Write files. Without it the command only plans. |
| `--json` | Machine-readable report. |

The report contains `status`, `planned`, `applied`, `next_step`, **`stack`**, and **`onboarding`**. `stack` is the read-only output of [`detect_stack.py`](gates-and-stack-detection.md#detect_stackpy) for the target. `onboarding` has scope `ORCHESTRATOR_ONLY` and lists only unresolved questions about issue-tracker access, optional Obsidian binding, and other project-specific tools; every integration accepts an explicit `none` answer. It also reports `record_valid`, `record_status`, and `record_issues` so malformed, unsupported, incomplete, or non-UTF-8 setup records remain visibly unresolved instead of failing open.

| `status` | Meaning |
| --- | --- |
| `READY` | Files can be installed; rerun with `--apply`. |
| `ALREADY_INITIALIZED` | Every template file is identical and state exists; nothing to do. |
| `BLOCKED` | Exit code 2 with `reason`: `TRACKED_DESTINATION_PATH`, `CONFIG_CONFLICT`, `SYMLINK_REJECTED`, `LOCAL_STATE_REQUIRES_REVIEW`, `TARGET_NOT_DIRECTORY`, or a Git preflight error. |

Guarantees, all covered by [the packaging tests](testing.md#skill-suite-tests):

- It copies the distributable template tree, including the inactive-by-default repository-local `hooks/` layer, ignoring interpreter artifacts (`__pycache__`, `.pyc`, `.pyo`). It never overwrites a differing file, writes to a tracked path or through a symlink, edits a Hermes profile, or grants shell-hook consent.
- It creates a fresh `STATE.md` (schema 2, `ticket: IDLE`, `mode: MANUAL`), `PROJECT_SETUP.md` (pending project connectivity), `INCIDENTS.md` and an empty, schema-valid `ACTION_JOURNAL.json` (see [Action journal](action-journal.md)).
- It adds the local paths to `.git/info/exclude`, never to `.gitignore`, so `git status` stays clean.
- A second run returns `ALREADY_INITIALIZED`. A partially present state returns `LOCAL_STATE_REQUIRES_REVIEW`.

An existing installation is **never upgraded automatically**. To pick up new template files, review and copy them by hand, or reinstall into a clean worktree.

## Project onboarding (`PROJECT_SETUP.md`)

A fresh installation creates the untracked controller-owned `.hermes/orchestration/PROJECT_SETUP.md`. Before the first demand, Hermes inspects repository evidence and asks only unresolved questions needed by the orchestrator:

1. Which issue tracker and project it should read, plus separate permission to create or update issues.
2. Whether to bind Obsidian and, only when enabled, which vault and project container to use.
3. Which other project-specific tools it needs, why it needs each one, and whether access is read-only or writable.

`none` is a valid explicit answer for every item. Otherwise the value is compact JSON on the same line: issue tracker requires exactly `provider` and `project` strings plus Boolean `read` and `write`; Obsidian requires exactly an absolute `vault` and a relative traversal-free `project_container`; project tools require a non-empty array whose objects contain exactly `tool`, `purpose`, `read`, and `write`. Generic strings such as `"GitHub"`, `"yes"`, or `"Jira"` are incomplete and remain unresolved. The onboarding must not ask product requirements, implementation preferences, passwords, tokens, or verification codes. Connectivity is verified read-only before it is recorded; any external mutation still requires explicit authorization. Hermes replaces each `UNRESOLVED` value with the answer and sets `status: COMPLETE` only after all three items are resolved. On later installer runs, the `onboarding.questions` array contains only values that remain unresolved; empty, comment-only, malformed/unterminated, YAML-null, quoted/case-variant unresolved markers and missing answers all remain unresolved. Invalid encoding, malformed structure, duplicate keys, unsupported schema versions, incomplete values, invalid record statuses, and status/answer mismatches fail closed with `record_valid: false`. A successful `--apply` reloads the new record before reporting it. When no questions remain **and** the record declares `status: COMPLETE`, the computed onboarding status is `COMPLETE`.

## `.hermes.md`: controller entry point

`templates/.hermes.md` is installed at the project root and is what Hermes reads first in that project. It is kept under 8,000 characters (enforced by a test). It states the controller role, the [FSM](fsm-and-loop.md), the safety rules, the dispatch and context rules for [stage agents](stage-agents.md) and [sub-agents](sub-agents.md), the [result schemas](contracts-and-schemas.md), the opt-in [hook](hooks.md) state inputs, and the loop modes. The controller carries `context_assessment` and stable `acceptance_checks` through executor stages, routes material unknowns from SPECIFY to CLARIFY, requires TASKS to assign a non-empty set, and retains the complete authoritative mapping (ID, criterion, verification method, verifier and slice assignment). Workers change only status/evidence; IMPLEMENT, TEST and every REVIEW status are validated against that mapping plus the current/completed slice context. It also names the harness steps: `runtime/stage_context.py check` before each dispatch, reuse of an approved slice hash, and `runtime/correction_loop.py decide` before any retry of a failed verification ([Harness](harness.md)). It links to policies instead of duplicating them.

## Installed layout (`orchestration/README.md`)

The installed `README.md` inside `.hermes/orchestration/` is the on-disk guide for a project's tree:

```text
.hermes.md
.hermes/obsidian.json            # optional, versioned — see Obsidian vault
.hermes/orchestration/
├── agents/      → stage-agents.md
├── contracts/   → contracts-and-schemas.md
├── hooks/       → hooks.md (installed but inactive until explicit profile opt-in)
├── policies/    → fsm-and-loop.md, gates-and-stack-detection.md, action-journal.md, sub-agents.md
├── runtime/     → one page per tool group (see the doc map)
├── schemas/     → contracts-and-schemas.md, fsm-and-loop.md, harness.md, action-journal.md
├── sub-agents/  → sub-agents.md
├── tests/       → testing.md
├── STATE.md, PROJECT_SETUP.md, ACTION_JOURNAL.json, INCIDENTS.md   (runtime data, untracked)
```

After installation, configure the gates: [Gates and stack detection](gates-and-stack-detection.md).
