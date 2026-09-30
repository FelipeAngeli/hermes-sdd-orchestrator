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

The report contains `status`, `planned`, `applied`, `next_step`, **`stack`**, and **`onboarding`**. `stack` is the read-only output of [`detect_stack.py`](gates-and-stack-detection.md#detect_stackpy) for the target. `onboarding` has scope `ORCHESTRATOR_ONLY` and lists only unresolved questions about issue-tracker access, optional Obsidian binding, optional TypeSafe skill installation for Jev guidance, and other project-specific tools; every integration accepts an explicit `none` answer. Its `integrations.typesafe_ai` object reports `status`, `planned_action`, `env_status`, `planned_env_action`, the official upstream command for reference, and the repository-local skill, lock and credential-file paths. It also reports `record_valid`, `record_status`, `record_issues`, and `integration_issues`; malformed setup records or a mismatch between the recorded TypeSafe choice and the verified local installation remain visibly unresolved instead of failing open.

| `status` | Meaning |
| --- | --- |
| `READY` | Files can be installed; rerun with `--apply`. |
| `ALREADY_INITIALIZED` | Every template file is identical and state exists; nothing to do. |
| `BLOCKED` | Exit code 2 with `reason`. Prerequisite diagnostics are `PYTHON_3_10_REQUIRED`, `GIT_REPOSITORY_REQUIRED`, and `GIT_INITIAL_COMMIT_REQUIRED`; each includes an actionable `next_step` and occurs before target writes. Other reasons cover existing installer conflicts plus TypeSafe refusals such as `TYPESAFE_VENDOR_DIGEST_MISMATCH`, `TYPESAFE_VENDOR_INVALID`, `TYPESAFE_SKILL_CONFLICT`, `TYPESAFE_LOCK_PRESERVATION_FAILED`, or `TYPESAFE_ROLLBACK_FAILED`. |

Guarantees, all covered by [the packaging tests](testing.md#skill-suite-tests):

- It copies the distributable template tree, including the inactive-by-default repository-local `hooks/` layer and the project-local engineering skills, ignoring interpreter artifacts (`__pycache__`, `.pyc`, `.pyo`). It never overwrites a differing file, writes to a tracked path or through a symlink, edits a Hermes profile, modifies global skills/trust, or grants shell-hook consent.
- The installer source remains parseable by Python 3.9 solely so it can stop immediately with `PYTHON_3_10_REQUIRED`; installation and the runtime still require Python 3.10+. A directory outside a Git worktree stops with `GIT_REPOSITORY_REQUIRED`; an initialized repository without a commit stops with `GIT_INITIAL_COMMIT_REQUIRED`. Both direct the caller to complete the missing Git prerequisite before retrying.
- TypeSafe installation is a separate explicit opt-in. The installer never executes `npx` or downloads code: the official `npx skills add typesafe-ai/skills --skill typesafe-ai` command is informational, while apply copies the reviewed snapshot in `vendor/typesafe-ai/` from immutable upstream commit `65a39f393687675ce170e6094757de20370365b9`. Preflight verifies the exact regular-file set, an independently trusted length-framed digest, and a safe untracked `.hermes/.env` destination before any project write. The controller preserves unrelated lock metadata and commits the skill, merged pinned lock entry, private mode-`0600` empty credential file and onboarding answer as one rollback-covered operation; it never overwrites an existing regular `.env`. An unsafe replacement topology returns `TYPESAFE_ROLLBACK_FAILED` rather than touching an external target.
- It creates a fresh `STATE.md` (schema 2, `ticket: IDLE`, `mode: MANUAL`), `PROJECT_SETUP.md` (pending project connectivity), `INCIDENTS.md` and an empty, schema-valid `ACTION_JOURNAL.json` (see [Action journal](action-journal.md)).
- It adds only `.hermes.md`, `.hermes/.env`, `.hermes/.env.example`, `.hermes/orchestration` and the three bundled `.hermes/skills/<name>` paths to `.git/info/exclude`, never `.hermes/skills` as a whole and never `.gitignore`. Therefore it does not newly hide unrelated project skills. Pre-existing user-owned exclusion entries are preserved verbatim, including any broader rule the user already configured.
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

`runtime/typesafe_connector.py` is a stdlib-only, explicit client for `POST https://api.typesafe.ai/v1/systemone`. It reads `TYPESAFE_API_KEY` from the process environment first, then `.hermes/.env`; `preflight --json` validates only local configuration and never contacts TypeSafe. On POSIX, credential paths are opened root-first with a directory descriptor per component plus `O_NOFOLLOW|O_NONBLOCK`, then ownership, regular-file type and mode are checked with `fstat()` on the opened descriptor before reading. Malformed, non-UTF-8, duplicate-key, symlinked, special-file, non-owner or group/world-accessible credential files fail closed without blocking. Keys containing leading, trailing or embedded whitespace, control characters or non-ASCII bytes are rejected, and neither keys nor remote error bodies appear in output.

`evaluate --input <json> --json` opens the input through the same descriptor-anchored nonblocking no-follow traversal and requires a regular file, so FIFOs and other special files fail before reading. It accepts at most 4 MiB of strict finite JSON with no more than 64 container levels, containing exactly `state`, a non-empty `questions` map, and optional `model`; the default is `jev-latest`. It has no configurable endpoint and refuses redirects, so a credential cannot be redirected by CLI input or an HTTP response. The command is never invoked automatically: running it is the explicit decision to send the supplied state/questions to TypeSafe. Responses are limited to 4 MiB and 64 levels, and HTTP error handles are closed without reading their bodies. Success returns `{"status":"OK","result":...}`. Local configuration/input failures exit 2 as `BLOCKED`; HTTP 401/422/429/529, timeouts, transport failures, oversized or truncated responses and malformed remote responses exit 3 as stable `ERROR` reasons without returning response bodies.

| Command/option | Meaning |
| --- | --- |
| `preflight` | Validate local credential configuration without network access. |
| `evaluate` | Send one explicitly supplied System One request. |
| `--env-file` | Override the local credential-file path; the default is `.hermes/.env`. |
| `--input` | JSON request file required by `evaluate`. |
| `--timeout` | Finite network timeout in seconds from greater than 0 through 300 for `evaluate` (default 30). |
| `--json` | Emit the stable machine-readable report. |

The tracked `.hermes/.env.example` contains only `TYPESAFE_API_KEY=`. TypeSafe opt-in writes and fsyncs a private random temporary file under the verified `.hermes` parent directory, then hard-links it into the absent `.env` name without overwrite and removes the temporary name. Because the final credential path is never deleted during rollback, a concurrent replacement cannot be mistaken for the installer-created file and removed. An existing owner-only regular file is preserved byte-for-byte; a tracked, symlinked, special, wrong-owner or group/world-accessible file reopens a recorded install and blocks install/reinstall. If the placeholder is deleted from an otherwise valid installation, repeating `--typesafe-ai install --apply` recreates it transactionally. A verified `none` opt-out depends only on the TypeSafe skill being absent and leaves any unrelated `.env` conflict untouched without reopening the TypeSafe question. Put the real key there or export it in the process environment; never commit it.

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
