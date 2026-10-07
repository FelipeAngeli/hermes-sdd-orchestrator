# Skill and installer

[Docs index](../README.md) · Next: [Gates and stack detection](gates-and-stack-detection.md) · Related: [Obsidian vault](obsidian-vault.md), [FSM and bounded loop](fsm-and-loop.md)

**Files:** `SKILL.md`, `scripts/install_project.py`, `vendor/typesafe-ai/SKILL.md`, `vendor/typesafe-ai/LICENSE`, `templates/.hermes.md`, `templates/.hermes/.env.example`, `orchestration/runtime/typesafe_connector.py`, and `orchestration/README.md` (the installed layout guide, `templates/.hermes/orchestration/README.md`).

## `SKILL.md`: Hermes entry point

`skills/orchestrate/sdd-orchestrator/SKILL.md` is what Hermes loads with `/skill sdd-orchestrator`. Its frontmatter carries the name, the version (bumped on every contract change) and the tags. Its body explains when to use the skill, the prerequisites (Git, Python 3.10+, an attached-branch worktree root), installation, the nine-step operating procedure, pitfalls and verification. The procedure now carries evidence-backed context and acceptance checks across stages, checks each dispatch's context and slice contract, queries the optional [context graph](context-graph.md) for the modules and paths in scope before PLAN and IMPLEMENT (and proposes the resulting decision for human approval after REVIEW or DONE), bounds corrective retries, and requires REVIEW to verify outcomes independently of technical gates.

Install it once per Hermes profile:

```text
hermes skills tap add FelipeAngeli/hermes-sdd-orchestrator
hermes skills install FelipeAngeli/hermes-sdd-orchestrator/skills/orchestrate/sdd-orchestrator --yes
```

The complete published bundle is scanned in CI with Hermes `skills-guard-v5` from pinned hermes-agent commit `4e9d3c713a3e3d47319ab18a8d8dfade5665270d`. CI requires a `SAFE` verdict, so the normal community-source installation needs neither `--force` nor a scanner bypass. Defensive fixtures construct synthetic paths and non-sensitive sentinels without weakening their traversal, containment, or non-disclosure assertions.

## `install_project.py`: per-project installer

**Default storage is the Obsidian project container. Nothing is written to the user's repository.**

```text
V="--obsidian-vault /abs/vault --obsidian-project Projects/App"
python3 <skill>/scripts/install_project.py --target <repo-root> $V --json                          # dry run
python3 <skill>/scripts/install_project.py --target <repo-root> $V --apply --json                  # write into the vault
python3 <skill>/scripts/install_project.py --target <repo-root> $V --typesafe-ai install --apply --json
python3 <skill>/scripts/install_project.py --target <repo-root> $V --typesafe-ai install --automatic-jev-governance --apply --json
python3 <skill>/scripts/install_project.py --target <repo-root> --local-storage --apply --json     # legacy in-repo layout
```

| Flag | Meaning |
| --- | --- |
| `--target` | Existing Git worktree root (default `.`). A subdirectory is rejected with `TARGET_NOT_REPOSITORY_ROOT`. |
| `--obsidian-vault` | Absolute path of an existing Obsidian vault. Required unless `--local-storage`. |
| `--obsidian-project` | Project container relative to the vault (for example `Projects/App`). Absolute paths, `..`, hidden (`.`-prefixed) components, backslashes and NUL are refused with `OBSIDIAN_PROJECT_INVALID`. |
| `--local-storage` | Legacy mode: install `.hermes.md` and `.hermes/` inside the target worktree, hidden through `.git/info/exclude`, exactly as before 8.0.0. Combining it with the Obsidian flags returns `STORAGE_MODE_CONFLICT`. |
| `--apply` | Write files and apply an explicitly selected TypeSafe action. Without it the command only plans. |
| `--typesafe-ai install\|none` | Explicitly install/record the repository-local TypeSafe skill, or record an opt-out. Omit it to leave the onboarding answer unresolved. |
| `--automatic-jev-governance` | With `--typesafe-ai install`, explicitly authorize automatic, potentially billed semantic classifications. Installation alone records `false`. |
| `--json` | Machine-readable report. |

### Obsidian storage (default)

Without `--local-storage`, both Obsidian flags are mandatory; omitting them returns `BLOCKED` with `OBSIDIAN_BINDING_REQUIRED` and a `next_step`, before any write. A missing vault returns `OBSIDIAN_VAULT_NOT_FOUND` and a relative one `OBSIDIAN_VAULT_NOT_ABSOLUTE`. The Git checks (repository, initial commit, attached branch) still run against `--target`, and the stack is still detected from it, but every write lands under `<vault>/<project>/`:

```text
<vault>/<project>/
├── .hermes.md
├── .hermes/obsidian.json                         # binding (vault_path, project_container, runtime_subpath)
├── .hermes/orchestration/…                        # complete controller payload + PROJECT_SETUP.md
├── .hermes/skills/…                               # engineering playbooks (+ typesafe-ai when opted in)
├── .hermes/skills-lock.json                       # only with TypeSafe opt-in
├── SCHEMA.md, index.md, log.md, raw/, entities/, concepts/, comparisons/, queries/   # LLM Wiki skeleton
└── .hermes-runtime/<worktree-slug>/STATE.md, ACTION_JOURNAL.json, INCIDENTS.md
```

`PROJECT_SETUP.md` is created with the `obsidian` answer already resolved to that vault and container. The visible container root is a [LLM Wiki](obsidian-vault.md#project-container-layout-llm-wiki-wiki_layoutpy): the installer creates `SCHEMA.md`, `index.md`, `log.md` and the `raw/`, `entities/`, `concepts/`, `comparisons/` and `queries/` directories (with a hidden `.gitkeep` in each empty one) only where absent, and never compares or replaces an existing wiki file. In this mode the TypeSafe lock is `.hermes/skills-lock.json`, so the wiki root holds only notes; a container that still has a root `skills-lock.json` stops with `TYPESAFE_LOCK_LEGACY_LOCATION`, and a symlink or a file where a wiki path belongs stops with `WIKI_PATH_UNSAFE`. The report adds `storage: OBSIDIAN`, `vault`, `project_container`, `worktree_runtime` and `target_writes: []`; `exclude_update_planned` is always `false` because nothing in the worktree needs hiding. Before planning, a vault or container that equals, contains or lies inside the target worktree, its Git common directory, any linked worktree or any superproject returns `OBSIDIAN_VAULT_OVERLAPS_TARGET`; paths are compared by device and inode, so case variants on case-insensitive filesystems, symlinks and `..` cannot slip through. The installer snapshots the target's `git status --ignored` plus the presence of `.hermes`, `.hermes.md` and `skills-lock.json` before the run and compares it again inside the apply transaction, before and after the TypeSafe integration and inside its own rollback, so `TARGET_WORKTREE_CHANGED` restores `PROJECT_SETUP.md` and removes every vault path the run wrote. The installer checks once more after the transaction commits; a drift seen only then is reported as `TARGET_WORKTREE_CHANGED` but the committed vault files stay. Container files must be byte-identical to the bundle when they already exist (`CONFIG_CONFLICT` otherwise), except `policies/GATES.md` once the container already holds this installer's binding and `PROJECT_SETUP.md`: it is then owned by the project so several worktrees can share the container, and the report lists it under `preserved_owner_files` with its SHA-256. A fresh container never adopts a foreign `GATES.md` (`CONFIG_CONFLICT`); whoever can write the vault controls the gate commands, so review that hash. Every other controller file stays managed; tracked-path checks are skipped in the vault, which may be its own Git repository, and `planned_env_action` is always `NONE` because no credential file is managed; the per-worktree runtime is all-or-nothing (`LOCAL_STATE_REQUIRES_REVIEW`). A failed apply rolls back every file and directory it created, by identity and content. A repeated identical apply returns `ALREADY_INITIALIZED` and changes no byte. Bytecode is never generated by the installer.

In Obsidian mode TypeSafe installs its vetted skill and `skills-lock.json` into the container but **no `.env` file is created anywhere**: credentials never belong in a synced vault nor in the repository. Export `TYPESAFE_API_KEY` or `JEV_AI_API_KEY` in the process environment; the connector reads it there when no credential file exists.

### Repository-local storage (`--local-storage`)

The report contains `status`, `planned`, `exclude_update_planned`, `applied`, optional `warnings`, `next_step`, **`stack`**, and **`onboarding`**. `planned` lists project files missing before apply; `exclude_update_planned` separately reports drift in the installer-managed Git exclusions and becomes `false` after a successful repair. A completed apply remains `APPLIED` if an owned lock cannot be cleaned up after commit; `warnings` then reports `LOCK_CLEANUP_REQUIRES_REVIEW` instead of falsely reporting the completed transaction as blocked. `stack` is the read-only output of [`detect_stack.py`](gates-and-stack-detection.md#detect_stackpy) for the target. `onboarding` has scope `ORCHESTRATOR_ONLY` and lists only unresolved questions about issue-tracker access, optional Obsidian binding, optional TypeSafe skill installation and automatic Jev consent, and other project-specific tools; every integration accepts an explicit `none` answer. Its `integrations.typesafe_ai` object reports `status`, `planned_action`, `env_status`, `planned_env_action`, `automatic_semantic_governance`, `planned_automatic_semantic_governance`, the official upstream command for reference, and the repository-local skill, lock and credential-file paths. It also reports `record_valid`, `record_status`, `record_issues`, and `integration_issues`; malformed setup records or a mismatch between the recorded TypeSafe choice and the verified local installation remain visibly unresolved instead of failing open.

| `status` | Meaning |
| --- | --- |
| `READY` | The dry run found project files, Git exclusions, or an integration action to apply. |
| `APPLIED` | This invocation completed at least one requested write or exclusion repair. |
| `ALREADY_INITIALIZED` | Every template file is identical, managed exclusions are present and no integration action remains. |
| `BLOCKED` | Exit code 2 with `reason`. Prerequisite diagnostics are `PYTHON_3_10_REQUIRED`, `JSONSCHEMA_REQUIRED`, `GIT_REPOSITORY_REQUIRED`, `GIT_INITIAL_COMMIT_REQUIRED`, and `ATTACHED_BRANCH_REQUIRED`; each includes an actionable `next_step` and occurs before target writes. Other reasons cover existing installer conflicts, special files, concurrent Git/exclusion changes, rollback failures, plus TypeSafe refusals such as `TYPESAFE_VENDOR_DIGEST_MISMATCH`, `TYPESAFE_VENDOR_INVALID`, `TYPESAFE_SKILL_CONFLICT`, `TYPESAFE_LOCK_PRESERVATION_FAILED`, or `TYPESAFE_ROLLBACK_FAILED`. |

Guarantees, all covered by [the packaging tests](testing.md#skill-suite-tests):

- It copies the distributable template tree, including the inactive-by-default repository-local `hooks/` layer and the project-local engineering skills, ignoring interpreter artifacts (`__pycache__`, `.pyc`, `.pyo`). Existing destinations are opened nonblocking through no-follow directory descriptors and must be regular files with identical bytes; FIFOs and other special objects fail before reading. Apply holds the worktree index lock, revalidates the plan, creates each missing file exclusively with mode `0644` and each new directory with mode `0755`, and never overwrites a differing, newly tracked or raced destination. A failed base apply rolls back its newly created paths and Git-exclude edit before returning `BLOCKED`.
- The installer source remains parseable by Python 3.9 solely so it can stop immediately with `PYTHON_3_10_REQUIRED`; installation and the runtime require Python 3.10+ plus `jsonschema`. A missing package returns `JSONSCHEMA_REQUIRED`; a directory outside a Git worktree returns `GIT_REPOSITORY_REQUIRED`; an initialized repository without a commit returns `GIT_INITIAL_COMMIT_REQUIRED`; and detached HEAD returns `ATTACHED_BRANCH_REQUIRED`. All direct the caller to complete the missing prerequisite before retrying.
- TypeSafe installation is a separate explicit opt-in. The installer never executes `npx` or downloads code: the official `npx skills add typesafe-ai/skills --skill typesafe-ai` command is informational, while apply copies the reviewed snapshot in `vendor/typesafe-ai/` from immutable upstream commit `65a39f393687675ce170e6094757de20370365b9`. Preflight verifies the exact regular-file set, an independently trusted length-framed digest, and a safe untracked `.hermes/.env` destination before any project write; apply also refuses a group/world-writable skill parent. A fresh combined base-plus-TypeSafe apply runs the integration inside the base transaction, so an integration failure restores exclusions and removes the base payload. TypeSafe retains a no-follow descriptor for the newly created skill directory and creates each bundled file exclusively through it; lock/setup snapshots bind bytes and inode in one open, compare current bytes immediately before mutation, restore original bytes after partial-write failure, and rollback each still-owned object independently. Concurrent replacements and same-inode exclusion edits are preserved and reported as ownership/state loss rather than overwritten or deleted. The controller preserves unrelated lock metadata and commits the skill, merged pinned lock entry, private mode-`0600` empty credential file and onboarding answer as one rollback-covered operation; an installer-created credential placeholder is removed by verified identity/content if later integration verification fails, and an existing regular `.env` is never overwritten or removed.
- It creates a fresh `STATE.md` (schema 2, `ticket: IDLE`, `mode: MANUAL`), `PROJECT_SETUP.md` (pending project connectivity), `INCIDENTS.md` and an empty, schema-valid `ACTION_JOURNAL.json` (see [Action journal](action-journal.md)).
- It adds root-anchored entries for each bundled/generated file, the optional `.hermes/.env`, and the controller-owned `action-journal-history/` directory to `.git/info/exclude`; it never excludes `.hermes/orchestration` or `.hermes/skills` as whole trees and never edits `.gitignore`. Therefore unrelated files under either tree remain visible. Exclude access is descriptor-anchored, no-follow, binary-preserving and lock-serialized; symlinked or special exclude files are rejected, user-owned bytes are preserved, and a later apply repairs deleted managed entries. Rollback binds each created file to both its recorded identity and expected bytes, so immediate inode reuse cannot authorize deleting a foreign replacement. Rollback and lock cleanup leave an already-mismatched pathname untouched; otherwise they rename the owned candidate to an unpredictable quarantine name, verify the moved inode, then remove it. A mismatched concurrent regular-file replacement is restored with an atomic no-overwrite hard link on POSIX, non-empty base directories are preserved in place, and directory mismatches otherwise use no-overwrite rename or remain quarantined with an explicit failure when no safe restore is possible.
- A second run returns `ALREADY_INITIALIZED`. A partially present state returns `LOCAL_STATE_REQUIRES_REVIEW`.

An existing installation is **never upgraded automatically**. To pick up new template files, review and copy them by hand, or reinstall into a clean worktree.

## Project onboarding (`PROJECT_SETUP.md`)

A fresh installation creates the untracked controller-owned `.hermes/orchestration/PROJECT_SETUP.md`. Before the first demand, Hermes inspects repository evidence and asks only unresolved questions needed by the orchestrator:

1. Which issue tracker and project it should read, plus separate permission to create or update issues.
2. Whether to bind Obsidian and, only when enabled, which vault and project container to use.
3. Whether to install the repository-local TypeSafe skill and, separately, whether automatic potentially billed Jev classifications are authorized.
4. Which other project-specific tools it needs, why it needs each one, and whether access is read-only or writable.

`none` is valid for every item. Otherwise TypeSafe requires `{"install":true,"automatic_semantic_governance":true|false}`. `--typesafe-ai install` records `false`; adding `--automatic-jev-governance` records `true`. The legacy `{"install":true}` value is deliberately invalid and reopens onboarding, so an old install-only answer can never become network/billing consent. The other integrations retain their documented structured permission shapes. The onboarding never asks for credentials or product requirements and becomes `COMPLETE` only when all four items are valid and resolved.

For TypeSafe, `--typesafe-ai install` is only a preview until combined with `--apply`; it installs guidance and records automatic governance as disabled. Add `--automatic-jev-governance` only after explicit consent. Repeating the same choice is a no-op; changing `false` to `true` updates onboarding authorization after installation verification. `--typesafe-ai none --apply` records an opt-out only when no TypeSafe skill is discoverable. Existing legacy install-only answers remain unresolved until explicitly migrated.

## Automatic Jev semantic governance

After explicit automatic Jev consent through `--automatic-jev-governance` and a READY connector preflight, the controller has standing authorization and does not ask before every inference. TypeSafe installation alone is not consent. Deterministic facts, calculations, Git state, schema validation, permissions and final FSM transitions stay in code. Remaining semantic classifications are sent through `runtime/semantic_governor.py decide` as one batch.

```text
python3 .hermes/orchestration/runtime/semantic_governor.py decide \
  --input .hermes/orchestration/JEV_REQUEST.json \
  --project-setup .hermes/orchestration/PROJECT_SETUP.md --json
```

The input contains `schema_version: 1`, a non-blank `ticket`, JSON `state`, a non-empty map of at most 64 typed `choice`/`noul` questions and optional `model`. Question IDs are non-empty, at most 64 characters and entirely printable; terminal controls are refused before cache or output. Jev may choose only controller-supplied options. Choice answers must return the selected option, the complete finite probability map and finite confidence; Noul confidence is derived as `max(p, 1-p)`. The fixed acceptance threshold is `0.70`, inclusive. Anything lower, missing, malformed, unavailable or uncertain yields `REVIEW` with `value: null`; the controller never invents an answer, silently substitutes main-model classification or retries a possibly billed request.

Before it reads the request or cache, the CLI descriptor-opens `PROJECT_SETUP.md` without following any path component and requires the `typesafe_ai` answer to be exactly `{"install":true,"automatic_semantic_governance":true}`; `false`, missing, malformed, duplicate-key and legacy install-only values return `JEV_GOVERNANCE_CONSENT_REQUIRED`. On a cache miss, connector preparation rechecks that record and requires its local, network-free `preflight` to return `READY` before writing the in-flight tombstone. A failed consent check or preflight returns without inference or a tombstone, so correcting that pre-network condition permits a later attempt.

The governor fingerprints the semantic input with provider, threshold and policy version. Every report also records the request's `ticket`, which lets [`stage_context.py`](harness.md#stage-context-manifest-schema-2-stage_contextpy) refuse PLAN or IMPLEMENT under automatic consent unless the manifest cites a cached governor fingerprint for the same ticket; a report cached before this field existed gets the ticket written back on its next cache hit, under the lock and without a new paid call, and only then satisfies the gate. `consent_state` and `verify_cached_decision` are the governor's public helpers for that gate; `verify_cached_decision` reads under the existing cache lock and creates nothing when the cache or lock is absent. A matching private cache entry is reused without a paid call. One retained parent-directory descriptor anchors the lock file, cache reads, tombstone, cache temporary file and atomic replacement for the full transaction; replacing the pathname cannot move cache I/O outside the lock domain. Cache serialization deliberately preserves the insertion order of fingerprint entries (canonical sorted encoding remains limited to fingerprint material), so reload does not turn age into lexical hash order. Writes retain at most 128 entries and evict the true oldest until the complete encoded file also fits the 4 MiB read limit; an individually oversized newest entry fails before replacement, leaving the durable tombstone intact. After consent and preflight succeed, the governor writes that `IN_FLIGHT` tombstone before evaluation. The bounded semantic payload is streamed to the connector over stdin, so there is no mutable request pathname to replace. An `OSError` while constructing the process proves it never started, atomically restores a cache miss and returns `JEV_GOVERNANCE_EVALUATION_NOT_STARTED`; if restoration fails, the tombstone remains and the operation fails closed. Once process construction succeeds, every communication, timeout or response failure is treated as potentially billed. Remote model names must be non-empty, trimmed, printable and at most 240 characters or the response becomes `REVIEW`. A final file-write or atomic-replacement failure returns non-success `REVIEW` and leaves the prior tombstone visible. If only the parent-directory `fsync` fails after a successful file `fsync` and replacement, the operation keeps the visible final report: after a crash, either that report or the prior durable tombstone survives, and both suppress automatic rebilling.

These owner/mode and ancestor no-follow guarantees are implemented with POSIX directory descriptors and `flock`. Automatic semantic governance deliberately returns `JEV_GOVERNANCE_PLATFORM_UNSUPPORTED` on Windows before project state or network access; it does not claim junction-safe or descriptor-equivalent Windows handling. This is an optional runtime limitation, not a loss of Windows support for the installer or the rest of the SDD skill.

Each live use writes `JEV EM USO` before network access and a timed `JEV USADO` block afterward with provider, current phase, semantic area, question IDs, evaluated-file count, decided-versus-review totals and fingerprint artifact while stdout remains machine-readable JSON. A fallback phase read from semantic state is emitted only when it is non-empty, printable and at most 64 characters; otherwise the receipt uses `não informada`. When `TERMINAL_PROGRESS.json` exists, the governor also marks Jev active/completed in that dashboard; a process-construction or other blocked live-start error closes the active entry as `BLOCKED`, while cache hits do not create an entry because no live request occurs. The installer root-anchors both private state files and their sibling lock files in Git `info/exclude`; none contains credentials or the submitted semantic state.

| Command/option | Meaning |
| --- | --- |
| `decide` | Resolve one cached batch of semantic classifications. |
| `--input` | Governance request JSON described above. |
| `--provider` | Fixed credential/destination pair: `typesafe` (default) or `jev-ai`. |
| `--env-file` | Credential file override; default `.hermes/.env`. |
| `--cache` | Private decision-cache path; default `.hermes/orchestration/JEV_CACHE.json`. |
| `--progress` | Private terminal-dashboard state; default `.hermes/orchestration/TERMINAL_PROGRESS.json`. An absent file disables dashboard synchronization without creating a run. |
| `--project-setup` | Guarded consent record; default `.hermes/orchestration/PROJECT_SETUP.md`. Automatic evaluation requires the exact enabled TypeSafe consent object. |
| `--timeout` | Finite connector timeout greater than 0 through 300 seconds. |
| `--json` | Emit the stable machine-readable report on stdout. |

## TypeSafe/Jev runtime connector

`runtime/typesafe_connector.py` is a stdlib-only, explicit System One client with two fixed credential/destination pairs selected by `--provider`. Each provider reads only its own key, from the process environment first and then `.hermes/.env`; a key is never sent to the other provider and there is no automatic fallback.

| `--provider` | Key | Base URL | Decision endpoint | Model lookup |
| --- | --- | --- | --- | --- |
| `typesafe` (default) | `TYPESAFE_API_KEY` | `https://api.typesafe.ai` | `POST https://api.typesafe.ai/v1/systemone` | not offered |
| `jev-ai` | `JEV_AI_API_KEY` | `https://jev-ai.pro/api` | `POST https://jev-ai.pro/api/v1/systemone` | `GET https://jev-ai.pro/api/v1/models` |

Jev AI is an independent hosted endpoint compatible with TypeSafe's System One API; a Jev AI key and balance belong to Jev AI. `preflight --json` validates only local configuration and never contacts a provider; with `--provider jev-ai` it also reports `key_name`, `base_url`, `systemone` and `models` so the resolved destination can be checked before any request. On POSIX, credential paths are opened root-first with a directory descriptor per component plus `O_NOFOLLOW|O_NONBLOCK`, then ownership, regular-file type and mode are checked with `fstat()` on the opened descriptor before reading. Malformed, non-UTF-8, duplicate-key, symlinked, special-file, non-owner or group/world-accessible credential files fail closed without blocking. Keys containing leading, trailing or embedded whitespace, control characters or non-ASCII bytes are rejected, and neither keys nor remote error bodies appear in output. Missing or invalid keys report `<KEY_NAME>_MISSING` or `<KEY_NAME>_INVALID`.

`models --provider jev-ai --json` performs an authenticated `GET` without a body and returns `{"status":"OK","provider","endpoint","models":[{"name","description"?}]}`. It never runs inference; it checks connectivity and authentication but does not guarantee that a later decision call will succeed. With the `typesafe` provider it returns `BLOCKED` `MODELS_UNSUPPORTED` before network access.

`evaluate --input <json> --json` opens the input through the same descriptor-anchored nonblocking no-follow traversal and requires a regular file, so FIFOs and other special files fail before reading. It accepts at most 4 MiB of strict finite JSON with no more than 64 container levels, containing exactly `state`, a non-empty `questions` map, and optional `model`; the default is `jev-latest`. Every entry in `questions` is checked against the documented Question contract before any request: `type` must be `noul`, `choice` or `score`; `instructions` must be a non-blank string or a non-empty object/array; `choice` requires a non-empty `criteria` map of at most 255 options to a description, which may be text, a structured object/array of caller-named fields, or `null` for no extra detail; `score` requires a `criteria` array of 2 to 10 ordered level descriptions, each text or a structured object/array; `noul` `criteria` are optional and, when present, must be an object. Unknown question fields are forwarded untouched so a future API field is not refused locally. A violation is refused as `TYPESAFE_QUESTION_INVALID: <question id>`, naming the offending question so a contract error is diagnosable without the remote response body; the ID is echoed only when it is printable and at most 64 characters. Because the input path uses the same no-follow traversal, every component must be a real directory: on macOS `/tmp` is a symlink to `/private/tmp` and is refused as `TYPESAFE_INPUT_INVALID`, so use a canonical path inside the repository, such as a file you create under `.hermes/orchestration/` itself. For `jev-ai` the normalized body must also respect the documented API validator limits — at most 256000 bytes, 64 questions and 64-character question IDs — or it is refused locally as `TYPESAFE_INPUT_OVER_LIMIT`. Endpoints are not configurable from the CLI and redirects are refused, so a credential cannot be redirected by CLI input or an HTTP response. The command is never invoked automatically: running it is the explicit decision to send the supplied state/questions to the selected provider, and with `jev-ai` it is billed to the Jev AI balance. Each invocation sends exactly one request and never retries. Responses are limited to 4 MiB and 64 levels, and HTTP error handles are closed without reading their bodies. The default `typesafe` provider keeps its original request headers (`Authorization`, `Content-Type`) and error contract; `jev-ai` additionally sends `Accept: application/json`. Success returns `{"status":"OK","result":...}`; `jev-ai` adds `provider`, `endpoint` and a `billing` object built from the documented `X-Jev-Run-Id`, `X-Jev-Billing`, `X-Jev-Paid-Input-Tokens-Used`, `X-Jev-Credits-Charged`, `X-Jev-Tokens-Remaining` and `X-Jev-Credits-Remaining` response headers. Local configuration/input failures exit 2 as `BLOCKED`. Remote failures exit 3 as stable `ERROR` reasons without returning response bodies. Both providers map 401, 422, 429 and 529; the remaining rows apply only to `jev-ai`, and the `typesafe` provider reports them as `TYPESAFE_HTTP_ERROR`:

| HTTP | Reason | Caller action |
| --- | --- | --- |
| 401 | `TYPESAFE_AUTHENTICATION_FAILED` | Check the destination host and the key. |
| 402 | `TYPESAFE_PAYMENT_REQUIRED` | Insufficient balance or paused spending; check billing. |
| 404 | `TYPESAFE_ENDPOINT_NOT_FOUND` | Check the base URL and final path. |
| 422 | `TYPESAFE_REQUEST_REJECTED` | Correct the request; do not retry unchanged. |
| 429 | `TYPESAFE_RATE_LIMITED` | Back off; for `jev-ai`, `retry_after_seconds` is reported when `Retry-After` is 1–6 ASCII digits (other forms are ignored). |
| 502/503 | `TYPESAFE_UNAVAILABLE` | Retry later with bounded backoff. |
| 504 | `TYPESAFE_UPSTREAM_TIMEOUT` | Outcome may be uncertain; check usage first. |
| 529 | `TYPESAFE_OVERLOADED` | Retry later with bounded backoff. |
| other | `TYPESAFE_HTTP_ERROR` | Inspect `http_status`. |

Timeouts report `TYPESAFE_TIMEOUT`; connection, reset and other transport failures report `TYPESAFE_TRANSPORT_ERROR`; oversized, truncated or malformed remote responses report `TYPESAFE_RESPONSE_INVALID`. For a `jev-ai` evaluation, a 504, timeout, transport failure or malformed success body also reports `"outcome":"UNCERTAIN"`: the request may have run and been billed, so check Jev AI usage before sending it again. Billing headers are captured before the body is parsed, so a malformed success report still includes `billing`.

| Command/option | Meaning |
| --- | --- |
| `preflight` | Validate local credential configuration and report the resolved destination without network access. |
| `models` | Authenticated model lookup without inference (`jev-ai` only). |
| `evaluate` | Send one explicitly supplied System One request. |
| `--provider` | `typesafe` (default) or `jev-ai`; selects the fixed key/destination pair. |
| `--env-file` | Override the local credential-file path; the default is `.hermes/.env`. |
| `--input` | Read the bounded JSON request from a descriptor-anchored file. Mutually exclusive with `--input-stdin`; one source is required by `evaluate`. |
| `--input-stdin` | Read the bounded JSON request from stdin. The semantic governor uses this source so the connector consumes the exact bytes supplied without reopening a pathname. |
| `--timeout` | Finite network timeout in seconds from greater than 0 through 300 for `models` and `evaluate` (default 30). |
| `--json` | Emit the stable machine-readable report. |

The tracked `.hermes/.env.example` contains empty `TYPESAFE_API_KEY=` and `JEV_AI_API_KEY=` slots. TypeSafe opt-in writes and fsyncs a private random temporary file under the verified `.hermes` parent directory, then hard-links it into the absent `.env` name without overwrite and removes the temporary name. Because the final credential path is never deleted during rollback, a concurrent replacement cannot be mistaken for the installer-created file and removed. An existing owner-only regular file is preserved byte-for-byte; a tracked, symlinked, special, wrong-owner or group/world-accessible file reopens a recorded install and blocks install/reinstall. If the placeholder is deleted from an otherwise valid installation, repeating `--typesafe-ai install --apply` recreates it transactionally. A verified `none` opt-out depends only on the TypeSafe skill being absent and leaves any unrelated `.env` conflict untouched without reopening the TypeSafe question. Put only the selected provider's real key there or export it in the process environment; never commit it.

## `.hermes.md`: controller entry point

`templates/.hermes.md` is installed at the project root and is what Hermes reads first in that project. It is kept under 8,000 characters (enforced by a test). It states the controller role, the [FSM](fsm-and-loop.md), the safety rules, the dispatch and context rules for [stage agents](stage-agents.md) and [sub-agents](sub-agents.md), the [result schemas](contracts-and-schemas.md), the opt-in [hook](hooks.md) state inputs, and the loop modes. Each leaf worker receives only stage-scoped context; conversation history, transcripts, archives and state dumps are omitted while the controller retains transition and STATE authority. The controller carries `context_assessment` and stable `acceptance_checks` through executor stages, routes material unknowns from SPECIFY to CLARIFY, requires TASKS to assign a non-empty set, and retains the complete authoritative mapping (ID, criterion, verification method, verifier and slice assignment). Workers change only status/evidence; IMPLEMENT, TEST and every REVIEW status are validated against that mapping plus the current/completed slice context. It also names the harness steps: `runtime/stage_context.py check` before each dispatch, exact project-local playbook descriptors bound to the approved slice hash, and `runtime/correction_loop.py decide` before any retry of a failed verification ([Harness](harness.md)). It links to policies instead of duplicating them.

## Installed layout (`orchestration/README.md`)

The installed `README.md` inside `.hermes/orchestration/` is the on-disk guide for a project's tree:

```text
.hermes.md
skills-lock.json                 # optional TypeSafe project lock
.hermes/.env                    # created only by TypeSafe opt-in, ignored, mode 0600
.hermes/.env.example            # TypeSafe variable name; no secret
.hermes/obsidian.json            # optional, versioned — see Obsidian vault
.hermes/skills/                  → project-local-skills.md (explicit repository trust)
├── sdd-backend-engineering/
├── sdd-architecture-decisions/
├── sdd-database-design-migrations/
└── typesafe-ai/                 # optional external TypeSafe skill
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
├── JEV_CACHE.json, TERMINAL_PROGRESS.json                          (private generated data, untracked)
```

The installer does not run `hermes skills trust`; after inspecting `.hermes/skills/`, the user opts in for that repository and starts a new session. No SOUL, profile-level skill or global configuration is changed.

After installation, configure the gates: [Gates and stack detection](gates-and-stack-detection.md).
