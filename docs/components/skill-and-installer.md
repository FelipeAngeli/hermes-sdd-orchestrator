# Skill and installer

[Docs index](../README.md) · Next: [Gates and stack detection](gates-and-stack-detection.md) · Related: [Obsidian vault](obsidian-vault.md), [FSM and bounded loop](fsm-and-loop.md)

**Files:** `SKILL.md`, `scripts/install_project.py`, `templates/.hermes.md`, `orchestration/README.md` (the installed layout guide, `templates/.hermes/orchestration/README.md`).

## `SKILL.md`: Hermes entry point

`skills/orchestrate/sdd-orchestrator/SKILL.md` is what Hermes loads with `/skill sdd-orchestrator`. Its frontmatter carries the name, the version (bumped on every contract change) and the tags. Its body explains when to use the skill, the prerequisites (Git, Python 3.10+, an attached-branch worktree root), installation, the seven-step operating procedure, pitfalls and verification.

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

The report contains `status`, `planned`, `applied`, `next_step` and **`stack`**, which is the read-only output of [`detect_stack.py`](gates-and-stack-detection.md#detect_stackpy) for the target.

| `status` | Meaning |
| --- | --- |
| `READY` | Files can be installed; rerun with `--apply`. |
| `ALREADY_INITIALIZED` | Every template file is identical and state exists; nothing to do. |
| `BLOCKED` | Exit code 2 with `reason`: `TRACKED_DESTINATION_PATH`, `CONFIG_CONFLICT`, `SYMLINK_REJECTED`, `LOCAL_STATE_REQUIRES_REVIEW`, `TARGET_NOT_DIRECTORY`, or a Git preflight error. |

Guarantees, all covered by [the packaging tests](testing.md#skill-suite-tests):

- It copies the distributable template tree, ignoring interpreter artifacts (`__pycache__`, `.pyc`, `.pyo`). It never overwrites a differing file and never writes to a tracked path or through a symlink.
- It creates a fresh `STATE.md` (schema 2, `ticket: IDLE`, `mode: MANUAL`), `INCIDENTS.md` and an empty, schema-valid `ACTION_JOURNAL.json` (see [Action journal](action-journal.md)).
- It adds the local paths to `.git/info/exclude`, never to `.gitignore`, so `git status` stays clean.
- A second run returns `ALREADY_INITIALIZED`. A partially present state returns `LOCAL_STATE_REQUIRES_REVIEW`.

An existing installation is **never upgraded automatically**. To pick up new template files, review and copy them by hand, or reinstall into a clean worktree.

## `.hermes.md`: controller entry point

`templates/.hermes.md` is installed at the project root and is what Hermes reads first in that project. It is kept under 8,000 characters (enforced by a test). It states the controller role, the [FSM](fsm-and-loop.md), the safety rules, the dispatch and context rules for [stage agents](stage-agents.md) and [sub-agents](sub-agents.md), the [result schemas](contracts-and-schemas.md) and the loop modes. It links to policies instead of duplicating them.

## Installed layout (`orchestration/README.md`)

The installed `README.md` inside `.hermes/orchestration/` is the on-disk guide for a project's tree:

```text
.hermes.md
.hermes/obsidian.json            # optional, versioned — see Obsidian vault
.hermes/orchestration/
├── agents/      → stage-agents.md
├── contracts/   → contracts-and-schemas.md
├── policies/    → fsm-and-loop.md, gates-and-stack-detection.md, action-journal.md, sub-agents.md
├── runtime/     → one page per tool group (see the doc map)
├── schemas/     → contracts-and-schemas.md, fsm-and-loop.md, action-journal.md
├── sub-agents/  → sub-agents.md
├── tests/       → testing.md
├── STATE.md, ACTION_JOURNAL.json, INCIDENTS.md   (runtime data, untracked)
```

After installation, configure the gates: [Gates and stack detection](gates-and-stack-detection.md).
