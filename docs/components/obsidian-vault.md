# Obsidian vault

[Docs index](../README.md) · Related: [Action journal](action-journal.md), [Skill and installer](skill-and-installer.md), [FSM and bounded loop](fsm-and-loop.md)

**Files:** `BOOTSTRAP.md`, `runtime/obsidian_binding.py`, `runtime/obsidian_connector.py`, `runtime/vault_guard.py`, `runtime/bootstrap_worktree.py`, `runtime/migrate_to_vault.py`, `runtime/migrate_all_worktrees.py`, `runtime/consolidate_runtime.py`, `runtime/state_format.py`.

Obsidian is **optional**. A project can bind its orchestration knowledge and runtime state to an Obsidian vault, which then acts as a second brain. During SPECIFY → TEST the vault is read-only. After REVIEW or DONE, Hermes may propose a write, and every write needs human approval (`OBSIDIAN_WRITE` is a [`HUMAN_REQUIRED` action](fsm-and-loop.md#actions)). The vault is never a condition for DONE.

## Binding: `.hermes/obsidian.json` (`obsidian_binding.py`)

The binding is the only place that decides vault paths. It is **versioned**, unlike the rest of `.hermes/`, so the connection survives a clone.

```json
{"schema_version": 1, "vault_path": "/abs/vault", "project_container": "Projects/<name>", "runtime_subpath": ".hermes-runtime", "protocol_path": "…"}
```

- `vault_path` must be absolute. On another machine, `HERMES_OBSIDIAN_VAULT` overrides it. A missing vault is an error, never a silent fallback.
- `project_container` is relative and may not contain `..`. `runtime_subpath` must start with `.` so Obsidian never indexes it.
- Error codes: `BINDING_MISSING`, `BINDING_INVALID`, `BINDING_SCHEMA_UNSUPPORTED`.

Public API: `load`, `binding_path`, `worktree_slug`, `runtime_dir`, `state_path`, `journal_path`, `is_inside_container`.

## Read connectivity: `obsidian_connector.py`

The stdlib-only connector optionally uses the **official Obsidian CLI** for read-side vault discovery and project-scoped search. It requires the Obsidian 1.12.7+ installer, the Command line interface setting enabled, and the desktop app available. Note content is always read through no-follow filesystem descriptors, even when the CLI is available. CLI `open` is refused with `OPEN_UNSUPPORTED` because the official command accepts only a lexical path and cannot preserve containment atomically across path replacement. The connector never exposes create, append, prepend, move, rename, delete, property mutation, plugin management, arbitrary command execution or `eval`.

Before a binding exists, discover candidates read-only:

```text
python3 .hermes/orchestration/runtime/obsidian_connector.py discover [--cli <path>] [--timeout <seconds>] [--json]
```

After `.hermes/obsidian.json` exists, verify the selected vault, project container and transport:

```text
python3 .hermes/orchestration/runtime/obsidian_connector.py preflight --repo . [--cli <path>] [--timeout <seconds>] [--json]
```

Preflight reports `BINDING_MISSING`, `BINDING_INVALID`, `BINDING_SCHEMA_UNSUPPORTED`, `VAULT_UNREACHABLE`, `CONTAINER_UNAVAILABLE` or `BINDING_PATH_UNSAFE` as not ready. The connector requires every lexical component of the bound vault/container path to be an existing real directory, not a symlink; use the canonical path in `.hermes/obsidian.json` or `HERMES_OBSIDIAN_VAULT`. A valid binding is `READY_CLI` when the official CLI returns that exact absolute vault path, otherwise `READY_FILESYSTEM` with `CLI_UNAVAILABLE`, `CLI_INPUT_INVALID`, `CLI_OUTPUT_INVALID` or `CLI_VAULT_UNREACHABLE` as a diagnostic. CLI timeout, invalid/non-finite input, nonzero exit, non-UTF-8 or malformed output fail closed to the filesystem; none of these checks creates or edits a note.

Library API: `discover_vaults`, `preflight`, `search`, `read_note`, `tags`, `open_note`. Search uses `obsidian ... search ... path=<project_container> format=json`, then independently filters every result through the shared project-note policy and proves the note readable through root-anchored, component-by-component no-follow descriptors before returning only container-relative paths. Note reads and tag aggregation always use the filesystem, skip `.hermes-runtime`, the configured runtime subpath, `.obsidian`, `.trash`, `.git` and symlinks, and use the same descriptor traversal. `tags` stays filesystem-scoped because the official global tags command cannot be restricted to a folder. `open_note` validates the requested relative policy and then returns `OPEN_UNSUPPORTED` without dispatching the CLI.

The filesystem remains authoritative for runtime and all writes. Obsidian Headless Sync may place a remotely synced vault on disk for a server, but it is only deployment plumbing: it does not replace the binding, write approval, containment or baseline. Do not run desktop Sync and Headless Sync on the same device.

### Runtime location

Runtime lives in `<vault>/<project_container>/<runtime_subpath>/<worktree-slug>/`. The slug is the worktree name plus 8 hex characters of the SHA-256 of its **resolved** absolute path. Two worktrees therefore never share a `STATE.md` or [journal](action-journal.md), which preserves the one-worker-per-worktree rule.

## Write containment: `vault_guard.py`

Git cannot see the vault, so this module restores the equivalent of the Git baseline:

- `assert_writable` refuses any path outside the bound container, resolving symlinks and comparing path parts rather than string prefixes. It raises `VaultWriteRefused`.
- `capture_vault_baseline`, `diff_baseline` and `assert_baseline_preserved` hash every file (mtimes are not trusted) and raise `VaultBaselineViolation`. The runtime directory, `.obsidian`, `.trash` and `.git` are excluded.

## Tools

| Tool | Purpose | Flags |
| --- | --- | --- |
| `obsidian_connector.py` | Discover official-CLI vault candidates and preflight the bound read transport; library calls provide guarded project-scoped search plus no-follow filesystem note reads/tags, expose no write API, and refuse lexical CLI opening. | `discover`, `preflight`, `--repo`, `--cli`, `--timeout`, `--json` |
| `bootstrap_worktree.py` | Prepare an already-registered worktree from a canonical source worktree and write its binding. Dry run by default. | `--source`, `--target`, `--obsidian-vault`, `--obsidian-project`, `--apply`, `--json` |
| `migrate_to_vault.py` | Move one worktree's knowledge and runtime into the vault using copy → verify hash → remove (never a bare `mv`). Refuses an open demand. | `--repo`, `--apply`, `--json`, `--skip-open-demand` |
| `migrate_all_worktrees.py` | Run the migration for every registered worktree independently. One refusal never aborts the others. | `--repo`, `--vault`, `--project`, `--apply` |
| `consolidate_runtime.py` | Move project history duplicated byte-for-byte across worktree runtime directories into `_shared/`. Per-worktree `STATE.md`, `ACTION_JOURNAL.json` and `INCIDENTS.md` never move. | `--repo`, `--apply`, `--json` |
| `state_format.py` | Library. Reads the three STATE dialects (JSON, indented YAML, inline YAML) with a stdlib-only parser and normalises them to JSON, verified by round-trip. Public API: `split_fence`, `detect`, `parse`, `normalise`. | none |

`bootstrap_worktree.py` requires both Obsidian flags for `--apply` (`OBSIDIAN_BINDING_REQUIRED`) and never derives the project name from the directory. It has no `--force`. Conflicts return `CONFIG_CONFLICT` or `EXISTING_STATE_REQUIRES_REVIEW`. The full procedure is in `BOOTSTRAP.md`. `bootstrap_worktree.py` is the per-worktree counterpart of the [project installer](skill-and-installer.md#install_projectpy-per-project-installer).
