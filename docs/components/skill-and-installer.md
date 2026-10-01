# Skill and installer

[Docs index](../README.md) · Next: [Gates and stack detection](gates-and-stack-detection.md) · Related: [Obsidian vault](obsidian-vault.md), [FSM and bounded loop](fsm-and-loop.md)

**Files:** `SKILL.md`, `scripts/install_project.py`, `vendor/typesafe-ai/SKILL.md`, `vendor/typesafe-ai/LICENSE`, `templates/.hermes.md`, `templates/.hermes/.env.example`, `orchestration/runtime/typesafe_connector.py`, and `orchestration/README.md` (the installed layout guide, `templates/.hermes/orchestration/README.md`).

## `SKILL.md`: Hermes entry point

`skills/orchestrate/sdd-orchestrator/SKILL.md` is what Hermes loads with `/skill sdd-orchestrator`. Its frontmatter carries the name, the version (bumped on every contract change) and the tags. Its body explains when to use the skill, the prerequisites (Git, Python 3.10+, an attached-branch worktree root), installation, the nine-step operating procedure, pitfalls and verification. The procedure now carries evidence-backed context and acceptance checks across stages, checks each dispatch's context and slice contract, bounds corrective retries, and requires REVIEW to verify outcomes independently of technical gates.

Install it once per Hermes profile:

```text
hermes skills tap add FelipeAngeli/hermes-sdd-orchestrator
hermes skills install FelipeAngeli/hermes-sdd-orchestrator/skills/orchestrate/sdd-orchestrator --yes
```

The complete published bundle is scanned in CI with Hermes `skills-guard-v5` from pinned hermes-agent commit `4e9d3c713a3e3d47319ab18a8d8dfade5665270d`. CI requires a `SAFE` verdict, so the normal community-source installation needs neither `--force` nor a scanner bypass. Defensive fixtures construct synthetic paths and non-sensitive sentinels without weakening their traversal, containment, or non-disclosure assertions.

## `install_project.py`: per-project installer

```text
python3 <skill>/scripts/install_project.py --target <repo-root> --json                             # dry run
python3 <skill>/scripts/install_project.py --target <repo-root> --apply --json                     # write
python3 <skill>/scripts/install_project.py --target <repo-root> --typesafe-ai install --json       # preview opt-in
python3 <skill>/scripts/install_project.py --target <repo-root> --typesafe-ai install --apply --json
```

| Flag | Meaning |
| --- | --- |
| `--target` | Existing Git worktree root (default `.`). A subdirectory is rejected with `TARGET_NOT_REPOSITORY_ROOT`. |
| `--apply` | Write files and apply an explicitly selected TypeSafe action. Without it the command only plans. |
| `--typesafe-ai install\|none` | Explicitly install/record the repository-local TypeSafe skill, or record an opt-out. Omit it to leave the onboarding answer unresolved. |
| `--json` | Machine-readable report. |

The report contains `status`, `planned`, `exclude_update_planned`, `applied`, optional `warnings`, `next_step`, **`stack`**, and **`onboarding`**. `planned` lists project files missing before apply; `exclude_update_planned` separately reports drift in the installer-managed Git exclusions and becomes `false` after a successful repair. A completed apply remains `APPLIED` if an owned lock cannot be cleaned up after commit; `warnings` then reports `LOCK_CLEANUP_REQUIRES_REVIEW` instead of falsely reporting the completed transaction as blocked. `stack` is the read-only output of [`detect_stack.py`](gates-and-stack-detection.md#detect_stackpy) for the target. `onboarding` has scope `ORCHESTRATOR_ONLY` and lists only unresolved questions about issue-tracker access, optional Obsidian binding, optional TypeSafe skill installation for Jev guidance, and other project-specific tools; every integration accepts an explicit `none` answer. Its `integrations.typesafe_ai` object reports `status`, `planned_action`, `env_status`, `planned_env_action`, the official upstream command for reference, and the repository-local skill, lock and credential-file paths. It also reports `record_valid`, `record_status`, `record_issues`, and `integration_issues`; malformed setup records or a mismatch between the recorded TypeSafe choice and the verified local installation remain visibly unresolved instead of failing open.

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
3. Whether to install the repository-local TypeSafe skill used for TypeSafe workflows, including its Jev model guidance.
4. Which other project-specific tools it needs, why it needs each one, and whether access is read-only or writable.

`none` is a valid explicit answer for every item. Otherwise the value is compact JSON on the same line: issue tracker requires exactly `provider` and `project` strings plus Boolean `read` and `write`; Obsidian requires exactly an absolute `vault` and a relative traversal-free `project_container`; TypeSafe requires exactly `{"install":true}`; project tools require a non-empty array whose objects contain exactly `tool`, `purpose`, `read`, and `write`. Generic strings such as `"GitHub"`, `"yes"`, `"Jev"`, or `"Jira"` are incomplete and remain unresolved. Jev is the model name; the installed integration is the `typesafe-ai` skill. The onboarding must not ask product requirements, implementation preferences, passwords, tokens, or verification codes. Connectivity is verified read-only before it is recorded; any external mutation still requires explicit authorization. For Obsidian, the installed stdlib-only `runtime/obsidian_connector.py discover --json` lists official-CLI vault candidates before a binding exists; `preflight --repo . --json` then verifies the exact bound vault/container and reports `READY_CLI` or the valid `READY_FILESYSTEM` offline fallback without creating a note. Hermes replaces each `UNRESOLVED` value with the answer and sets `status: COMPLETE` only after all four items are resolved. On later installer runs, the `onboarding.questions` array contains only values that remain unresolved; empty, comment-only, malformed/unterminated, YAML-null, quoted/case-variant unresolved markers and missing answers all remain unresolved. Invalid encoding, malformed structure, duplicate keys, unsupported schema versions, incomplete values, invalid record statuses, and status/answer mismatches fail closed with `record_valid: false`. A successful `--apply` reloads the new record before reporting it. When no questions remain **and** the record declares `status: COMPLETE`, the computed onboarding status is `COMPLETE`.

For TypeSafe, `--typesafe-ai install` is only a preview until combined with `--apply`. A successful apply writes `{"install":true}` only after the vetted bundled snapshot and lock entry verify; `--typesafe-ai none --apply` records `none` only when no TypeSafe skill is discoverable; an existing valid or conflicting installation must be reviewed and removed explicitly before opting out. Repeating either resolved choice is a no-op; an explicit choice also inserts the missing TypeSafe key into a legacy schema-1 setup record while preserving its other answers. A valid existing `.hermes/skills/typesafe-ai/SKILL.md` installation with the same pinned lock entry and trusted content digest is recorded without overwrite. A malformed, partial, symlinked, tracked or differently sourced installation remains unresolved and returns `BLOCKED`. Updating the TypeSafe source commit requires reviewing the new bytes and updating the pinned lock hash, trusted digest and vendored snapshot together. The generated optional skill and `skills-lock.json` stay project-local and are not hidden by the orchestrator's Git excludes; the credential file is covered by the existing orchestration exclusion.

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
| `--input` | JSON request file required by `evaluate`. |
| `--timeout` | Finite network timeout in seconds from greater than 0 through 300 for `models` and `evaluate` (default 30). |
| `--json` | Emit the stable machine-readable report. |

The tracked `.hermes/.env.example` contains only `TYPESAFE_API_KEY=`. TypeSafe opt-in writes and fsyncs a private random temporary file under the verified `.hermes` parent directory, then hard-links it into the absent `.env` name without overwrite and removes the temporary name. Because the final credential path is never deleted during rollback, a concurrent replacement cannot be mistaken for the installer-created file and removed. An existing owner-only regular file is preserved byte-for-byte; a tracked, symlinked, special, wrong-owner or group/world-accessible file reopens a recorded install and blocks install/reinstall. If the placeholder is deleted from an otherwise valid installation, repeating `--typesafe-ai install --apply` recreates it transactionally. A verified `none` opt-out depends only on the TypeSafe skill being absent and leaves any unrelated `.env` conflict untouched without reopening the TypeSafe question. Put the real key there or export it in the process environment; never commit it. `JEV_AI_API_KEY` for the `jev-ai` provider goes in the same owner-only `.hermes/.env` (one line, never committed) or in the deployment's server-side secret store.

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
```

The installer does not run `hermes skills trust`; after inspecting `.hermes/skills/`, the user opts in for that repository and starts a new session. No SOUL, profile-level skill or global configuration is changed.

After installation, configure the gates: [Gates and stack detection](gates-and-stack-detection.md).
