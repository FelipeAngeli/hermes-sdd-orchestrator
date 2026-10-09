# Local worktree bootstrap

`bootstrap_worktree.py` prepares an already-existing, registered Git worktree with the local SDD Orchestrator V2.5 configuration, response protocol v2, and its Obsidian binding.

It is a project-local Python tool, not a native `hermes bootstrap` command. It does not create branches or worktrees, start a demand, run an agent, activate BOUNDED_AUTO, run product validation, or change a ticket, baseline, counters, blockers, or history in an existing `STATE.md`.

## Usage

Run it from any directory with explicit roots. The Obsidian binding is mandatory:

```text
python3 .hermes/orchestration/runtime/bootstrap_worktree.py \
  --source /absolute/path/to/canonical-worktree \
  --target /absolute/path/to/existing-target-worktree \
  --obsidian-vault /absolute/path/to/vault \
  --obsidian-project "1 - 💡 Knowledge/Projects/<Project>"
```

Both Obsidian arguments are required for `--apply`; without them bootstrap stops with `OBSIDIAN_BINDING_REQUIRED` and writes nothing. The project name is never derived from the directory: silent derivation is how a bootstrapped repository inherited another project's container and wrote notes into it.

Without `--apply`, the command is a dry run. It performs Git identity, common-repository, registration, symlink, tracked-path, hash, conflict, and vault preflight checks, then reports the planned files without writing anything.

Apply only after reviewing the dry-run result:

```text
python3 .hermes/orchestration/runtime/bootstrap_worktree.py \
  --source /absolute/path/to/canonical-worktree \
  --target /absolute/path/to/existing-target-worktree \
  --apply --json
```

`--json` emits a structured report. Nonzero exit status means the bootstrap was blocked or an operational error occurred. There is intentionally no `--force`, overwrite, reset, or branch-switching option.

## What is copied

The allowlist is explicit: `.hermes.md`, V2.5 loop and gate policy, executor/review contracts and schemas, the local protocol validator and its synthetic tests, `ACTION_RECOVERY.md`, `ACTION_JOURNAL_SCHEMA.json`, `action_journal.py`, `test_action_journal.py`, the Phase 2A preview assets: `BOUNDED_AUTOMATION.md`, `BOUNDED_RUN_PLAN_SCHEMA.json`, `bounded_run_planner.py`, and `test_bounded_run_planner.py`, the Phase 2B runtime assets: `BOUNDED_RUN_DRIVER.md`, `bounded_run_driver.py`, and `test_bounded_run_driver.py`, and the deprecated legacy bounded-loop driver assets: `bounded_loop_driver.py` and `test_bounded_loop_driver.py`. No directory is copied wholesale. This legacy allowlist does not include the controller entry point `runtime/sdd.py` and its dependencies; install with `scripts/install_project.py` (which copies the whole controller) to drive demands with `sdd.py`.

`.hermes/obsidian.json` is generated per target from the `--obsidian-*` arguments rather than copied, because its contents are specific to that worktree's project. `bootstrap_worktree.py` writes it into the target worktree and keeps it out of `info/exclude`, so this legacy per-worktree binding is versioned and survives a clone. The project installer's default Obsidian storage instead keeps the binding in the container (`<vault>/<project>/.hermes/obsidian.json`) and writes nothing to the repository.

The tool never copies `STATE.md`, `INCIDENTS.md`, `ACTION_JOURNAL.json`, approved plans, run history, diagnostics, backups, credentials, `.env` files, global Hermes configuration, or product artifacts. Runtime state is created in the vault, never in the repository: `<project_container>/<runtime_subpath>/<worktree-slug>/` receives a new `STATE.md` with the target worktree identity, `ticket: IDLE`, `stage: IDLE`, `status: WAITING`, an uncaptured baseline, no history, `bounded_run_plan: null`, pending gates, and `loop.mode: MANUAL`; an empty incident register; and an `ACTION_JOURNAL.json` with journal version 1, real worktree identity, and `action.id: null` / `action.status: IDLE`. The slug hashes the worktree's resolved absolute path, so two worktrees never share a runtime directory. The journal is constructed by the canonical `action_journal.empty_journal` function and validated against the distributed `ACTION_JOURNAL_SCHEMA.json` before any bootstrap files are written. A template/schema failure returns `ACTION_JOURNAL_TEMPLATE_INVALID` without creating partial local configuration. Existing journals are never overwritten; an orphaned existing journal blocks fresh initialization for review.

Configuration is separate from demand state. If target configuration needs a change while `STATE.md` already exists, bootstrap stops with `EXISTING_STATE_REQUIRES_REVIEW`; it never silently changes a worktree that may host a demand. If all files are already identical, it returns `ALREADY_INITIALIZED` without rewriting state.

## Safety and conflicts

The source is explicit; the branch name never determines canonical content. Source and target must be distinct registered worktree roots in the same Git common directory, and the target must have an attached branch. The tool rejects symlinks that could redirect configuration writes and blocks any tracked destination path rather than modifying the index.

An existing configuration file with different bytes causes `CONFIG_CONFLICT`; nothing is overwritten. Before `--apply` writes, identity and source/target hashes are checked again. New files are created without overwrite and copied bytes are hashed after creation. The Git `info/exclude` path is discovered through Git and receives only exact local paths, including `ACTION_JOURNAL.json`, without changing `.gitignore`. That exclude file can be shared by worktrees through the Git common directory. A second successful application returns `ALREADY_INITIALIZED` and preserves journal bytes.

Use a Python environment with the standard library available. The distributed protocol test suite additionally requires its existing `jsonschema` dependency. Bootstrap does not install dependencies automatically.

This utility has no claim that real IMPLEMENT execution with protocol v2 or budget-exhaustion behavior has been validated; it only prepares local configuration deterministically.
