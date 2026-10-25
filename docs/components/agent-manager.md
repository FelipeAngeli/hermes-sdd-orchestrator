# Agent manager (PR 1)

[Docs index](../README.md) · Related: [Contracts and schemas](contracts-and-schemas.md), [Stage agents](stage-agents.md), [Sub-agents](sub-agents.md)

**Files:** `runtime/agent_registry.py`, `runtime/delegation_config.py`, `runtime/delegation_contract.py`, `policies/DELEGATION.md`, `schemas/AGENT_REGISTRY_SCHEMA.json`, `schemas/DELEGATION_CONTRACT_SCHEMA.json`.

PR 1 covers only the read-only registry, the configuration and contract validation. It does not:
- dispatch a worker;
- create a delegation record;
- access `state.db`;
- touch worktrees;
- change the existing controller flow.

No existing runtime module imports these files.

## Registry

`agent_registry.py --root <orchestration-dir> --json` is deterministic. It reads only these files under `--root`:
- `agents/*.md`;
- `sub-agents/*.md`;
- `policies/EXECUTORS.md` (when that file is absent, it uses the `executor_launch` defaults);
- `schemas/AGENT_REGISTRY_SCHEMA.json`;
- the sibling project-local `../skills/*/SKILL.md`.

A relative `--root` is resolved first.

The output holds:
- a version;
- the agents and playbooks, sorted;
- the source hashes;
- a canonical `registry_sha256`.

It has no absolute paths or timestamps. The command never writes. An unreadable or invalid agent source makes it fail with `REGISTRY_SOURCE_INVALID` (exit 2). That includes:
- an invalid line in an agent or sub-agent frontmatter (a nested indented block is skipped);
- a missing registry schema;
- a role whose `allowed_stages` names an unknown SDD stage.

Playbooks never break the registry, because project skills can come from third parties (for example the vendored `typesafe-ai` skill, whose frontmatter has a folded `description` and no `version`). A playbook whose frontmatter can't be read is listed with `status: "UNPARSED"`, its folder name and `version: null`. One without a `version` gets `version: null`. Both are still hashed, so a change to them shows in `registry_sha256`. A playbook whose bytes can't be read at all (a directory or broken symlink named `SKILL.md`, or a file without read permission) can't be hashed, so it fails closed with `REGISTRY_SOURCE_INVALID`.

**Stage agents.** Each carries:
- from its frontmatter: identity, stage, executor policy and result schema;
- from `executor_launch.stage_settings` and `is_writing_worker`: executor, model, timeout, turns, tools, sandbox, permission mode and `writes`.

`tools` is a list for a Claude stage, or `null`. `null` means either a Codex stage, whose capability comes from its sandbox, or Claude with `tools: "default"`, which is unrestricted.

**Role agents** (sub-agents) are selected by the controller at dispatch time. They are always read-only, and their executor settings are `null`.

Every agent is `leaf_only`.

**Playbooks** have a name, a version (or `null`), a `status` (`PARSED` or `UNPARSED`), a relative source and a source hash. They are capabilities, not agents.

`AGENT_REGISTRY_SCHEMA.json` closes the output shape.

## Opt-in configuration

`DELEGATION.md` holds one fenced JSON object. `delegation_config.load_config()` never raises:
- a missing file gives `CONFIG_MISSING`;
- malformed JSON, a duplicate key, an unknown or missing key, a wrong type or an unsafe value gives `CONFIG_INVALID`;
- a valid file returns its `sha256` along with the values.

The shipped configuration is disabled.

Its exact keys are:
- `schema_version` (the integer 1);
- `enabled`, `concurrency_enabled`;
- `max_concurrent_worktrees`, `max_children_per_parent`, `max_depth`;
- `default_token_budget`, `bytes_per_token`;
- `on_budget_exceeded` (only `BLOCK`), `session_binding` (only `EXPLICIT`), `worktree_management` (only `PREPARED_ONLY`);
- `auto_delegate_on_conflict` (only `false`), `on_parent_cancel`.

`effective_limits()` returns the configured worktree and child limits only when **both** `enabled` and `concurrency_enabled` are true. Otherwise both limits are 1.

The hard ceilings are 3 worktrees, 3 children and depth 2. The loader rejects anything higher, and `effective_limits()` caps the values again.

**Owner decisions.** The configuration only accepts values consistent with these decisions:
- prepared worktrees only, and the manager never creates, deletes or modifies a worktree;
- state, journal and temporary files stay per worktree;
- no concurrent writes to shared resources;
- with the flags off, the current one-leaf-worker rule is unchanged.

PR 1 has no code that runs worktrees, locks or journals. These rules are recorded here and will be enforced by the code added in PR 2 to PR 4.

## Contract validation

`DELEGATION_CONTRACT_SCHEMA.json` is a new, closed v1 schema; it changes no existing schema. It has `schema_version` plus these fields:
- `task_id`;
- parent and child agent ids;
- `session_id`, `workspace_id`;
- `stage`, `objective`;
- input artifacts, acceptance criteria;
- allowed tools, dependencies;
- timeout, retry policy, token budget;
- output schema.

Artifact paths are not checked against the workspace yet; the code that reads them (PR 2 onward) must confine them.

### CLI

`delegation_contract.py` subcommand `validate` takes:
- `--contract FILE` (required);
- `--prompt-bytes N`, `--root DIR`, `--config FILE`, `--json` (optional).

It never writes. Its results:

| Result | Exit | Output |
|---|---|---|
| `VALID` | 0 | |
| `REVIEW_REQUIRED` or `REJECTED` | 2 | |
| `CONTRACT_UNREADABLE`, `PROMPT_BYTES_INVALID` (a negative byte count), `CONFIG_MISSING`, `CONFIG_INVALID`, or invalid agent sources | 1 | `{status, next_step}` |

### Errors

`validate_contract()` never changes its input. Each error is structured as `{code, field, message, next_step}`:
- `CONTRACT_SCHEMA_INVALID`;
- `SESSION_ID_REQUIRED`;
- `CHILD_AGENT_UNKNOWN`, `PARENT_AGENT_UNKNOWN`;
- `STAGE_NOT_ALLOWED`;
- `PERMISSION_WIDENING`;
- `DEPENDENCY_INVALID`;
- `OUTPUT_SCHEMA_MISMATCH`;
- `TOKEN_BUDGET_EXCEEDED`.

A contract that fails the schema gets `CONTRACT_SCHEMA_INVALID`, plus `SESSION_ID_REQUIRED` when the id is missing, and nothing else. The agent, permission, dependency and budget checks run only on a contract that passes the schema.

**Session id.** A missing, `null`, empty or blank session id is never guessed and never taken from another session. The error's next step asks for `--session-id <existing Hermes session id>`. PR 2 adds the read-only check against `state.db` that the session, project and worktree belong together.

### Permissions

Effective permissions are the intersection of:
- the requested tools and the registry tools;
- the timeout, capped at the child's setting (a role agent has no timeout of its own, so the requested one stands);
- the registry's sandbox and `writes` value.

A widening request is rejected, never silently trimmed. Each cause gets its own `PERMISSION_WIDENING` error with its `field`:

| Field | Cause |
|---|---|
| `allowed_tools` | A tool outside the registry list. |
| `allowed_tools` | Any tool for an agent whose tools are `null`. Those agents accept only `allowed_tools: []`. |
| `allowed_tools` | A writing Claude child limited to read-only tools. An empty list counts as read-only here. |
| `timeout` | A timeout above the child's. |
| `token_budget` | A token budget above `default_token_budget`. |

So a writing Claude stage configured with `tools: "default"` (unrestricted, so `tools: null`) can never get a valid contract: any non-empty list widens it, and `[]` would leave a writer without a write tool. To delegate to it, give the stage an explicit tool list in `EXECUTORS.md`. Codex stages are not affected, because their write capability comes from the sandbox.

### Token estimate

The estimate is `ceil(prompt_bytes / bytes_per_token)`, always labelled `BYTES_DIV_N` / `UNMEASURED` with `measured_tokens: null`. It is not measured provider usage and does not cover context the provider loads internally (`covers_provider_internal_context: false`).

The budget is the contract's `token_budget`, or `default_token_budget` when the contract's is `null`. When the estimate exceeds it, the result is `REVIEW_REQUIRED` if that is the only error. Without `--prompt-bytes` there is no estimate, so the budget is not checked.

## Planned, not implemented yet

- **PR 2:**
  - the read-only session, project and worktree check;
  - persisted delegation records and the delegation state machine;
  - locks;
  - admission (depth, children, dependencies, progress), still one child at a time;
  - admission also checks `enabled`.
- **PR 3:** supervision, result collection, cascading cancellation and recovery.
- **PR 4:** concurrency across prepared worktrees, and the compatibility report.

A read-only `delegations.list` query through the hermes-sdd-manager proxy is a separate pull request in that repository.
