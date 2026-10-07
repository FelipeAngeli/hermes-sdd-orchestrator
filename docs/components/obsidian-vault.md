# Obsidian vault

[Docs index](../README.md) · Related: [Action journal](action-journal.md), [Skill and installer](skill-and-installer.md), [FSM and bounded loop](fsm-and-loop.md)

**Files:** `BOOTSTRAP.md`, `runtime/wiki_layout.py`, `runtime/wiki_journal.py`, `runtime/obsidian_binding.py`, `runtime/obsidian_connector.py`, `runtime/vault_guard.py`, `runtime/bootstrap_worktree.py`, `runtime/migrate_to_vault.py`, `runtime/migrate_all_worktrees.py`, `runtime/consolidate_runtime.py`, `runtime/state_format.py`.

Since 8.0.0 the Obsidian project container is the **default storage** for everything the orchestrator owns: the [installer](skill-and-installer.md#obsidian-storage-default) writes the controller, setup, playbooks, binding and per-worktree runtime under `<vault>/<project>/` and leaves the user's repository untouched. `--local-storage` keeps the old in-repository layout. Since 12.0.0 the project wiki is **read and written**: everything the orchestrator runs for the project is recorded there without approval ([`wiki_journal.py`](#recording-everything-in-the-wiki-wiki_journalpy); `OBSIDIAN_WRITE` is an [`AUTO_SAFE` action](fsm-and-loop.md#actions)). A failed wiki write never blocks the loop, and the vault is never a condition for DONE.

## Project container layout: LLM Wiki (`wiki_layout.py`)

Each project container is a [Karpathy LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f), the layout of the bundled `llm-wiki` Hermes skill. The orchestrator stays hidden, so the visible root holds only the wiki:

```text
<vault>/<project>/
├── SCHEMA.md      # domain, conventions, frontmatter, tag taxonomy; `layout_version: 1`
├── index.md       # sectioned catalog: Entities, Concepts, Comparisons, Queries
├── log.md         # append-only action log, rotated to log-YYYY.md
├── raw/           # Layer 1, immutable: articles/ papers/ transcripts/ assets/
├── entities/      # Layer 2: modules, services, integrations, organizations
├── concepts/      # Layer 2: concepts, rules and decisions (`type: decision`)
├── comparisons/   # Layer 2: side-by-side analyses
├── queries/       # Layer 2: filed answers; Obsidian Bases under queries/boards/
├── .hermes/ .hermes.md          # controller, binding, playbooks, skills-lock.json
└── .hermes-runtime/<worktree>/  # per-worktree STATE, journal, incidents
```

The [installer](skill-and-installer.md#obsidian-storage-default) creates the skeleton (`SCHEMA.md`, `index.md`, `log.md` and a hidden `.gitkeep` in each empty directory) only where it is absent and never compares or replaces a wiki file, because it belongs to the project once it exists. In this mode the TypeSafe lock lives at `.hermes/skills-lock.json`.

`wiki_layout.py` initializes, migrates and checks a container. It is stdlib-only and never contacts the network:

```text
python3 <container>/.hermes/orchestration/runtime/wiki_layout.py init    --container <abs> [--project <name>] [--date YYYY-MM-DD] [--apply] [--json]
python3 <container>/.hermes/orchestration/runtime/wiki_layout.py migrate --container <abs> [--project <name>] [--date YYYY-MM-DD] [--apply] [--json]
python3 <container>/.hermes/orchestration/runtime/wiki_layout.py check   --container <abs> [--json]
```

Without `--apply`, `init` and `migrate` are dry runs that report `planned_skeleton`, `moves`, `deduplicated`, `conflicts`, `skipped_symlinks`, `kept_hidden` and, for `migrate`, `init_required`. `migrate --apply` requires a container that `init --apply` (or the installer) already marked with `SCHEMA.md` or a binding (`WIKI_INIT_REQUIRED`), then maps legacy project folders onto the layout (folder names match case-insensitively in any Unicode normalization). Suffix rules come first: PDFs → `raw/papers/<path>`, images and media (and anything under a `* assets/` folder) → `raw/assets/<path>`. Then folders: `sessions/` → `raw/transcripts/`, `_Meetings/` → `raw/transcripts/meetings/`, `decisions/` and `Decisões/` → `concepts/`, `boards/` → `queries/boards/`, `_Discovery/`, `_References/` and `_Reviews/` → `raw/articles/<name>/`. In a folder that maps to `concepts/` or `queries/`, only `.md` and `.base` files become pages; folder index notes (`README.md`, `_INDEX.md`, `index.md`) and other files stay raw sources under `raw/articles/<folder>/`. Every demand folder and other loose note goes to `raw/articles/`, root `.base` files to `queries/`, and a root `skills-lock.json` to `.hermes/`. Hidden entries at every depth, the skeleton files, `log-YYYY.md` and the wiki directories (compared case-insensitively, plus `_archive/` and `_meta/`) stay where they are; hidden entries inside moved folders are listed in `kept_hidden`, and only a Finder `.DS_Store` is deleted, to empty a folder the run emptied.

Every conflict blocks the whole run with `WIKI_MIGRATION_CONFLICT` before anything moves: a destination that exists with different content, is not a regular file, is the source itself or a hard link to it, has a symlink or non-directory ancestor, differs only by case or Unicode normalization from an existing entry or another destination, or is a directory another move needs. A byte-identical destination is deduplicated. Each move works through no-follow descriptors anchored at the container: the source must still be the exact file the plan hashed (device, inode, size, mtime), its bytes are copied into an `O_EXCL` destination, the SHA-256 is verified on both sides, the modification time is preserved, and only then is the source retired: it is renamed to a private hidden name, that exact entry is compared with the open descriptor, and only it is unlinked, so a file saved over the name meanwhile is never deleted. If the renamed entry is not the planned file, it is put back with an exclusive hard link that never replaces a newer save (both versions then stay, the older under its private name), and the run stops with `WIKI_SOURCE_CHANGED`, naming the copy in the wiki as the pre-change version to reconcile by hand. A deduplicated source is retired the same way after both copies are re-hashed. Symlinks are never followed or moved. A changed source raises `WIKI_SOURCE_CHANGED`; any failure, including one while pruning or updating `index.md`/`log.md` after the moves, returns `WIKI_MIGRATION_INCOMPLETE`, leaves every unhandled source in place, logs what happened and can be rerun. `index.md` and `log.md` must decode as UTF-8 before the first move and are read and written only as single-link regular files (`WIKI_PATH_UNSAFE`), the index through a randomly named temporary file and rename. An unreadable directory is a conflict, and an OS error while planning is `WIKI_PATH_UNSAFE`. A completed run prunes emptied legacy folders, adds moved pages to `index.md` under their section as path links (`[[concepts/a/plan|plan]]`) and appends the full move list to `log.md`; a second run returns `ALREADY_MIGRATED`.

`check` exits 2 with `WIKI_SKELETON_MISSING`, `WIKI_LEGACY_ENTRY` (a visible root entry outside the layout) or `WIKI_PAGE_NOT_INDEXED` (a Layer-2 page missing from `index.md`; a bare-name link counts only when no other page shares that name). The container must be an existing directory reached without symlinks (`WIKI_CONTAINER_INVALID`) strictly inside a vault with `.obsidian/` (`WIKI_VAULT_REQUIRED`); the vault root itself (`WIKI_CONTAINER_IS_VAULT`), a folder holding other project containers (`WIKI_CONTAINER_NESTED`): marked ones always, and for a folder that is not itself marked, also children with controller state or children whose own folders hold legacy folders, so a marked project may group its demands under any folder and a Git work tree (`WIKI_CONTAINER_IS_REPOSITORY`) are refused. Other errors are `WIKI_PATH_UNSAFE` (also for any OS error such as a path longer than `PATH_MAX`, always as JSON) and `WIKI_DATE_INVALID`. `entities`, `concepts`, `comparisons` and `queries` are the Layer-2 sections the index and check use.

Upgrading a 9.x Obsidian container: copy the new controller templates into `<container>/.hermes/` by hand (an existing installation is never upgraded automatically, and a changed managed file returns `CONFIG_CONFLICT`), run `init --apply`, review `migrate` without `--apply` (it moves every legacy note, not only the lock), run `migrate --apply`, then rerun the installer. Until the root `skills-lock.json` moves, the installer stops with `TYPESAFE_LOCK_LEGACY_LOCATION`.

## Recording everything in the wiki (`wiki_journal.py`)

`wiki_journal.py` writes what runs in the project into its container; its one command is `record`. Records go where the LLM Wiki puts them:

| Kind | Destination | Written by |
| --- | --- | --- |
| `stage` | `raw/articles/<ticket>/<stamp>-<stage>-<title>.md` | the controller, at the end of each stage (LOOP_POLICY §18) |
| `action` | `raw/articles/<ticket>/actions/` | the runtime: each `prepare` (`PREPARED`), `block` (`BLOCKED`), rollover (`RELEASED`), interrupted archive (`INTERRUPTED`) and invalid archive (`INVALID`) of the [action journal](action-journal.md), and each leaf worker that stops (the [`subagent_stop` hook](hooks.md): role, status, duration, never its summary) |
| `gate` | `raw/articles/<ticket>/gates/` | the controller, for each gate result |
| `incident` | `raw/articles/<ticket>/incidents/` | the runtime, each time `record_incident` appends to `INCIDENTS.md` |
| `turn` | `raw/transcripts/sessions/<date>-<session>.md`, continued in `-part2`, `-part3`… past `MAX_TRANSCRIPT_BYTES` (4 MiB) | the [`post_llm_call` hook](hooks.md), one section per Hermes turn |
| `decision`, `concept` | `concepts/<title>.md` (`type: decision` for a decision) | the controller |
| `entity`, `comparison`, `query` | `entities/`, `comparisons/`, `queries/` | the controller |

A record without a ticket goes under `no-ticket/`. `raw/` records are created once with `O_EXCL` and never rewritten; a clashing name gets a numeric suffix. A session transcript only grows. A Layer-2 page is created with the `SCHEMA.md` frontmatter, later records append a dated section, and the page is added to its `index.md` section. Every record appends an entry to `log.md` (`ingest`, `create` or `update`); once it holds `MAX_LOG_ENTRIES` (500) entries it moves to `log-YYYY.md` (a second rotation in a year goes to `_archive/log-YYYY-N.md`) and a fresh `log.md` keeps its header. The [`on_session_finalize` hook](hooks.md) logs the end of a session that has a transcript in this wiki, once per session. Titles, subjects and frontmatter values are written as single lines, so a title cannot forge a log entry, and only the metadata keys in `METADATA_KEYS` reach the frontmatter. `index.md` and `log.md` updates hold an exclusive lock on `.wiki-journal.lock` in the container, so concurrent writers never lose an index line.

Before writing, credential shapes become `[REDACTED]`: private-key blocks, JWTs, `sk-`/`sk_live_`/`rk_`/`pk_` keys, `github_pat_`/`gh?_`, Slack tokens and webhooks, Google `AIza…`, AWS `AKIA…`/`ASIA…`, the password in `scheme://user:password@host`, `Bearer`/`Basic`/`Token` credentials, `curl -u user:password`, phrases such as "password is …", and any value assigned to a key containing `key`, `secret`, `token`, `password`, `pwd`, `credential` or `auth` (quoted JSON/YAML keys, quoted values with spaces, `export X=…`); numbers and booleans are kept. Redaction is a safety net, not a guarantee: never paste a credential into a conversation or artifact that is recorded. A body larger than `MAX_RECORD_BYTES` (512 KiB) is truncated with a note.

Every write goes through no-follow descriptors anchored at the container, like the migration: a symlinked folder or file, or a hard-linked page, is refused with `WIKI_PATH_UNSAFE` and nothing is written outside the container. An uninitialized container gets the wiki skeleton first; the vault root is refused (`WIKI_CONTAINER_IS_VAULT`).

The controller the module runs from decides where it writes; no binding inside a worktree is ever consulted. A **vault-resident** controller (its own `.hermes/obsidian.json` names the container it lives in) writes only into that container and only for a worktree registered by a runtime `STATE.md` under `.hermes-runtime/` whose `workspace.path` is an absolute Git work tree (has `.git`) that is neither `/`, the home folder nor one of its ancestors. A **repository-local** controller (`--local-storage`) writes only for its own repository, into the container its binding names. Any other worktree is `WIKI_WORKSPACE_NOT_REGISTERED`; a controller without a binding is `WIKI_BINDING_MISSING`. Hooks record a session only when its `cwd`, or Hermes' `TERMINAL_CWD`, is inside a served worktree; a session in a parent folder, the home folder or elsewhere is never recorded.

An action record copies the executor's final message only when the artifact is `VALID` and the file is a single-link regular file reached without any symlink, opened non-blocking, no larger than 512 KiB, whose SHA-256 equals the journal's artifact hash and which parses as an `executor_result` or `review_result` JSON object; otherwise only the action metadata is recorded. The runtime's automatic records never raise: a failure returns `SKIPPED` with its reason and the journal, incident and hook carry on.

```text
python3 <container>/.hermes/orchestration/runtime/wiki_journal.py record --repo <worktree> \
    --kind <kind> --title <title> [--body <text> | --body-file <path|->] [--ticket <id>] [--stage <stage>] [--session <id>] [--json]
```

It prints `WRITTEN` with the `path` relative to the container, or exits 2 with `BLOCKED` and the reason (also `WIKI_RECORD_KIND_INVALID`, `WIKI_RECORD_INVALID`, `WIKI_RECORD_NAME_EXHAUSTED`). `NO_TICKET` is the folder name `no-ticket`.

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

The filesystem remains authoritative for runtime and all writes. Obsidian Headless Sync may place a remotely synced vault on disk for a server, but it is only deployment plumbing: it does not replace the binding, containment or baseline. Do not run desktop Sync and Headless Sync on the same device.

### Runtime location

Runtime lives in `<vault>/<project_container>/<runtime_subpath>/<worktree-slug>/`. The slug is the worktree name plus 8 hex characters of the SHA-256 of its **resolved** absolute path. Two worktrees therefore never share a `STATE.md` or [journal](action-journal.md), which preserves the one-worker-per-worktree rule.

## Write containment: `vault_guard.py`

Git cannot see the vault, so this module restores the equivalent of the Git baseline:

- `assert_writable` refuses any path outside the bound container, resolving symlinks and comparing path parts rather than string prefixes. It raises `VaultWriteRefused`.
- `capture_vault_baseline`, `diff_baseline` and `assert_baseline_preserved` hash every file (mtimes are not trusted) and raise `VaultBaselineViolation`. The runtime directory, `.obsidian`, `.trash` and `.git` are excluded. Since the wiki is written continuously, a baseline only holds between two of the orchestrator's own writes; no runtime path currently calls these functions.

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
