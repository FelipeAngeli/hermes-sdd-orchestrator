# Obsidian vault

[Docs index](../README.md) · Related: [Action journal](action-journal.md), [Skill and installer](skill-and-installer.md), [FSM and bounded loop](fsm-and-loop.md)

**Files:** `BOOTSTRAP.md`, `runtime/obsidian_binding.py`, `runtime/obsidian_connector.py`, `runtime/vault_guard.py`, `runtime/bootstrap_worktree.py`, `runtime/migrate_to_vault.py`, `runtime/migrate_all_worktrees.py`, `runtime/consolidate_runtime.py`, `runtime/state_format.py`.

Since 8.0.0 the Obsidian project container is the **default storage** for everything the orchestrator owns: the [installer](skill-and-installer.md#obsidian-storage-default) writes the controller, setup, playbooks, binding and per-worktree runtime under `<vault>/<project>/` and leaves the user's repository untouched. `--local-storage` keeps the old in-repository layout. During SPECIFY → TEST the vault's knowledge notes are read-only. After REVIEW or DONE, Hermes may propose a write, and every write needs human approval (`OBSIDIAN_WRITE` is a [`HUMAN_REQUIRED` action](fsm-and-loop.md#actions)). The vault is never a condition for DONE.

## Binding: `.hermes/obsidian.json` (`obsidian_binding.py`)

The binding is the only place that decides vault paths. With Obsidian storage it lives in the container, `<vault>/<project>/.hermes/obsidian.json`, next to the controller that reads it. `binding_path(repo)` prefers a legacy repository-local `<repo>/.hermes/obsidian.json` when one exists (worktrees prepared by `bootstrap_worktree.py` or older installs); otherwise it returns the container binding of the controller it is running from (`runtime/` → `orchestration/` → `.hermes/` → container). `load_path(path)` validates one explicit binding file with the same error codes.

```json
{"schema_version": 1, "vault_path": "/abs/vault", "project_container": "Projects/<name>", "runtime_subpath": ".hermes-runtime", "protocol_path": "…"}
```

- `vault_path` must be absolute. On another machine, `HERMES_OBSIDIAN_VAULT` overrides it. A missing vault is an error, never a silent fallback.
- `project_container` is relative and may not contain `..`. `runtime_subpath` must start with `.` so Obsidian never indexes it.
- Error codes: `BINDING_MISSING`, `BINDING_INVALID`, `BINDING_SCHEMA_UNSUPPORTED`.

Public API: `load`, `load_path`, `binding_path`, `worktree_slug`, `runtime_dir`, `state_path`, `journal_path`, `is_inside_container`.

Hooks (`hook_runtime.py`) differ from `binding_path` on one point: with a vault-resident controller they read only the container binding, for runtime state, transient hook files and the vault write check alike, because a repository-local binding could redirect them. Hooks and the playbook check (`stage_context.py`) follow the same rule: when the repository has no `.hermes/orchestration`, a vault-resident controller is accepted, transient hook files (`STAGE_CONTEXT.json`, `HOOK_BINDING.json`, `VERIFICATION_EVIDENCE.json`) default to the worktree runtime directory in the vault, and playbook bytes are verified from the container's `.hermes/skills/`. `project_root` must still be the live Git workspace; only where the bytes are read changes.

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

Library API: `discover_vaults`, `preflight`, `search`, `project_notes`, `read_note`, `tags`, `open_note`. Search uses `obsidian ... search ... path=<project_container> format=json`, then independently filters every result through the shared project-note policy and proves the `.md` note readable through root-anchored, component-by-component no-follow descriptors before returning only canonical container-relative paths. The shared policy rejects traversal, NUL and non-Markdown paths. Direct reads apply the same policy and normalize accepted spellings such as `./note.md` in their returned path. `project_notes` enumerates the readable Markdown notes of the bound container as canonical container-relative paths, skipping unreadable notes instead of reporting them, and is what the [context graph](context-graph.md) reads a vault-backed graph through. Note reads and tag aggregation always use the filesystem, skip `.hermes-runtime`, the configured runtime subpath, `.obsidian`, `.trash`, `.git` and symlinks, and use the same descriptor traversal. `tags` stays filesystem-scoped because the official global tags command cannot be restricted to a folder. `open_note` validates the requested relative policy and then returns `OPEN_UNSUPPORTED` without dispatching the CLI.

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

`bootstrap_worktree.py` requires both Obsidian flags for `--apply` (`OBSIDIAN_BINDING_REQUIRED`) and never derives the project name from the directory. It writes a legacy per-worktree binding into the target and keeps it versioned; the project installer's default storage instead keeps the binding in the container and writes nothing to the repository. It has no `--force`. Conflicts return `CONFIG_CONFLICT` or `EXISTING_STATE_REQUIRES_REVIEW`. The full procedure is in `BOOTSTRAP.md`. `bootstrap_worktree.py` is the per-worktree counterpart of the [project installer](skill-and-installer.md#install_projectpy-per-project-installer).
