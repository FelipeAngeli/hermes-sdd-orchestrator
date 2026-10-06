# Repository-local Hermes hooks

[Docs index](../README.md) · Related: [Harness](harness.md), [Action journal](action-journal.md), [Obsidian vault](obsidian-vault.md), [Skill and installer](skill-and-installer.md)

**Files:** `hooks/*`, `runtime/hook_runtime.py`.

The `hooks/` layer is the event-driven edge of the SDD harness, parallel to `agents/` and `sub-agents/`. It contains shell-hook adapters, not policy: `runtime/hook_runtime.py` translates Hermes JSON stdin/stdout into calls to `stage_context.py`, `validate_protocol.py` path matching, `obsidian_binding.py`, and `vault_guard.py`. Installation copies the layer but never changes `~/.hermes/config.yaml`, SOUL, or global skills. Activation remains an explicit, consented profile choice.

## Catalogue

[`tests/test_docs.py`](testing.md#skill-suite-tests) requires one row for every installed file.

| File | Event | Matcher | Behavior |
| --- | --- | --- | --- |
| `hooks/enforce-slice-scope.py` | `pre_tool_call` | `write_file`, `patch` | Fails closed when context is missing or invalid. Direct file-tool writes must match the current IMPLEMENT slice's `editable_paths`; external targets must be inside the bound Obsidian `project_container`; all V4A targets are checked and accepted paths are rewritten canonically. |
| `hooks/require-verification.py` | `pre_verify` | — | Returns `continue` while an in-scope acceptance check lacks non-empty `PASS` evidence in `VERIFICATION_EVIDENCE.json`. Hermes bounds repeated nudges with `agent.max_verify_nudges`. |
| `hooks/record-subagent-stop.py` | `subagent_stop` | — | Writes one immutable event under `action-journal-history/subagent-events/`, linked to action ID and attempt, with IDs, role, status, duration, and a result digest. It excludes child summaries and tool history. |
| `hooks/inject-state-summary.py` | `pre_llm_call` | — | Optionally injects only bounded ticket, stage, current slice, mode, remaining executor calls, and remaining transitions; it never injects the STATE document. |
| `hooks/hooks.example.yaml` | configuration | — | Opt-in profile snippet. Its events are Hermes `VALID_HOOKS`: `pre_tool_call`, `pre_verify`, `subagent_stop`, and the optional `pre_llm_call`. |
| `hooks/README.md` | documentation | — | Installed operator guide, inputs, failure behavior, and JSON protocol. |

`runtime/hook_runtime.py` owns the shared implementation and stable default paths. The four scripts only select a handler and run `shell_main`; this keeps event transport separate from harness policy and prevents four copies of path and JSON validation. Default hook execution projects only the supported `SDD_STAGE_CONTEXT`, `SDD_HOOK_BINDING`, `SDD_VERIFICATION_EVIDENCE`, and `SDD_STATE` overrides from the process environment instead of exposing unrelated environment state to the runtime. Tests may still inject an explicit mapping, which is used as-is and does not consult process state.

## Activation and consent

Replace `<ABSOLUTE_PROJECT_ROOT>` in `.hermes/orchestration/hooks/hooks.example.yaml`, then merge the block into a **dedicated Hermes profile** for that checkout. Hermes asks for first-use consent for every `(event, command)` pair unless the user has deliberately enabled auto-accept. Consent persists by exact event and command, so repository-relative commands are unsafe here: approval in one checkout could otherwise authorize same-named code in a different current directory. The absolute path binds consent to this checkout. Shell hooks inherit the profile process environment, so do not reuse the dedicated profile for unrelated repositories. The installer never changes a profile or grants consent.

The scope entry uses:

```yaml
hooks:
  pre_tool_call:
    - matcher: "write_file|patch"
      command: 'python3 "<ABSOLUTE_PROJECT_ROOT>/.hermes/orchestration/hooks/enforce-slice-scope.py"'
      timeout: 10
      fail_closed: true
```

`fail_closed: true` is required because this hook is a write boundary: command-not-found, timeout, malformed stdout, and other hook failures block instead of silently allowing a write. Observer and context hooks do not use fail-closed behavior.

## Controller-owned inputs

The controller atomically maintains three mutually bound ignored runtime files before an active IMPLEMENT turn:

- `STAGE_CONTEXT.json`, or the `SDD_STAGE_CONTEXT` override, must pass `stage_context.py check`. It carries `editable_paths`, exact verifier commands, and the authoritative acceptance mapping.
- `HOOK_BINDING.json`, or `SDD_HOOK_BINDING`, binds the context digest to the canonical workspace, live Git HEAD, ticket, stage, current slices, active action ID, and attempt. The hook compares it with Git and the authoritative STATE/journal resolved repository-locally or through `obsidian_binding.runtime_dir`; stale/mismatched values and terminal journal actions fail closed.
- `VERIFICATION_EVIDENCE.json`, or the `SDD_VERIFICATION_EVIDENCE` override, repeats that exact binding and has a `checks` object keyed by acceptance-check ID. A passing record has non-empty evidence plus a `verifiers` entry with exit code `0` for every command bound to that check in `required_verification`.

When the controller is installed in the Obsidian project container (the default since 8.0.0), the hook `cwd` may be a repository with no `.hermes/orchestration` at all: `_root` accepts it as long as the container that holds the running `hook_runtime.py` carries `.hermes/obsidian.json`, and otherwise fails closed as before. In that case `STAGE_CONTEXT.json`, `HOOK_BINDING.json` and `VERIFICATION_EVIDENCE.json` default to the worktree runtime directory in the vault (next to `STATE.md`), STATE and journal resolve through the container binding, and the hook commands point at the absolute vault paths. Explicit `SDD_*` overrides still win.

`inject-state-summary.py` resolves authoritative `STATE.md` repository-locally or through the bound per-worktree vault runtime (`SDD_STATE` can override it), requires the selected fields to have exact string/integer types (booleans are not integers), then emits only bounded ticket, stage, current slice, mode, remaining executor calls, and remaining transitions. Missing, non-UTF-8, malformed, or wrong-shaped state returns exactly `SDD state unavailable.`; parser details and offending STATE values are never copied into any context, scope-block, or verification-continuation message.

## Boundaries

Direct file targets are canonically resolved, so existing symlink components are removed from the dispatched path. Repository writes are matched segment-by-segment using the same `path_matches` function as the executor-result validator. Replace-mode calls and every Add/Update/Delete/Move path in a V4A patch are checked; accepted arguments are rewritten to canonical absolute paths. A target outside the repository is writable only in IMPLEMENT, with a valid Obsidian binding and `vault_guard.assert_writable` proof that it is inside the bound project container; all other stages keep the vault read-only.

This is a fail-closed policy guard for Hermes' structured file tools, not an operating-system sandbox. `terminal` is deliberately not matched: shell commands can have arbitrary indirect side effects that cannot be inferred safely from a command string. The controller can therefore refresh `STAGE_CONTEXT.json` and `VERIFICATION_EVIDENCE.json` and run already-validated verifiers without deadlocking the file-tool hook; terminal authorization remains governed by the SDD controller and Hermes approvals.

The `subagent_stop` observer does not mutate transactional `ACTION_JOURNAL.json`; doing so would corrupt its action FSM. It resolves the active journal repository-locally or from the per-worktree vault runtime, reads the action ID and attempt, hashes (but never stores) the child result, and uses `action_journal.atomic_create_history` to create one immutable event under that runtime's `action-journal-history/subagent-events/<ticket>/`. That existing primitive traverses directories with descriptors, rejects symlinked/replaced parents, and makes retries idempotent.
