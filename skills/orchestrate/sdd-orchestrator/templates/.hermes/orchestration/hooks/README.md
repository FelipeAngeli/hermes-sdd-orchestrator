# Repository-local shell hooks

These scripts adapt Hermes shell-hook events to the SDD runtime. They are copied with the rest of `.hermes/orchestration`; installation does **not** edit a profile, `~/.hermes/config.yaml`, SOUL, or global skills. Activation is explicit: use a dedicated Hermes profile, replace `<ABSOLUTE_PROJECT_ROOT>` in `hooks.example.yaml`, merge its `hooks:` entries into that profile, and approve each `(event, command)` pair when Hermes asks. Consent persists by exact event and command; keeping an absolute checkout path prevents approval for one repository from silently authorizing same-named code in another checkout. Shell hooks inherit the profile process environment, which is another reason not to share this activation across unrelated repositories.

| File | Event | Matcher | Failure policy | Purpose |
| --- | --- | --- | --- | --- |
| `enforce-slice-scope.py` | `pre_tool_call` | `write_file|patch` | fail closed | Allows direct file-tool writes only under the current slice's `editable_paths` or inside the bound vault `project_container`; validates every V4A target and rewrites accepted paths to canonical absolute paths. |
| `require-verification.py` | `pre_verify` | — | continue turn | Keeps a coding turn open until every in-scope acceptance check has `PASS` evidence. |
| `record-subagent-stop.py` | `subagent_stop` | — | observe only | Writes one immutable event per leaf worker under `action-journal-history/subagent-events/`, linked to action ID and attempt, and records the same metadata in the project wiki; it stores a result digest, not summaries or tool history. |
| `inject-state-summary.py` | `pre_llm_call` | — | context only | Injects ticket, stage, mode, and executor-call usage without copying the STATE body. Optional in the example. |
| `record-turn.py` | `post_llm_call` | — | observe only | Appends the turn's user message and response to the project wiki (`raw/transcripts/sessions/`) when the session's `cwd` or `TERMINAL_CWD` is inside a worktree this controller serves; secrets are redacted first. |
| `record-session-end.py` | `on_session_finalize` | — | observe only | Logs the end of a session that has a transcript in this wiki to `log.md`, once per session. |
| `hooks.example.yaml` | configuration | — | — | Opt-in profile snippet for these six scripts. |
| `README.md` | documentation | — | — | Installed operator reference and hook catalogue. |

## Controller inputs

Before an active IMPLEMENT turn, the controller atomically writes three mutually bound files:

- `.hermes/orchestration/STAGE_CONTEXT.json`: a manifest accepted by `runtime/stage_context.py check`. `SDD_STAGE_CONTEXT` may point at another controller-owned path.
- `.hermes/orchestration/HOOK_BINDING.json`: schema 1 binding for the context SHA-256, canonical workspace, live Git HEAD, ticket, stage, current slice IDs, active action ID, and attempt. `SDD_HOOK_BINDING` may override its path. The hook also compares these values with live Git and the authoritative STATE/journal paths: repository-local before vault migration, or `obsidian_binding.runtime_dir(...)` afterwards. Terminal journal states and any mismatch fail closed.
- `.hermes/orchestration/VERIFICATION_EVIDENCE.json`: the same binding plus `{"checks":{"AC-1":{"status":"PASS","evidence":"V1 exited 0","verifiers":[{"command":"python3 -m unittest tests.test_feature","exit_code":0}]}}}`. Every command bound to the check in `required_verification` must appear with exit code `0`. `SDD_VERIFICATION_EVIDENCE` may override its path.

The context file is mandatory for the scope hook. Missing, malformed, stale, or contract-invalid context blocks matched tool calls. This is why the example declares `fail_closed: true`. The verification hook emits a bounded `continue` directive when context or evidence is missing; Hermes still applies `agent.max_verify_nudges`.

Direct `write_file`, replace-mode `patch`, and every target in a V4A patch are resolved canonically, including existing symlinks. Repository targets must match IMPLEMENT `editable_paths`; external targets are writable only during IMPLEMENT and must pass `obsidian_binding.load` plus `vault_guard.assert_writable` inside the bound project container. Accepted target arguments are rewritten to canonical absolute paths before dispatch. This is a policy guard around Hermes file tools, not an operating-system sandbox: `terminal` is deliberately outside its matcher because an arbitrary shell command's writes cannot be proven from its command string. The controller updates context/evidence and runs validated verifier commands through its normal terminal path.

`pre_llm_call` resolves authoritative `STATE.md` through the repository-local path or the bound per-worktree vault runtime; `SDD_STATE` can override it. It requires exact string/integer types (booleans are not integers) and emits only bounded ticket, stage, current slice, mode, remaining executor calls, and remaining transitions. Missing, non-UTF-8, malformed, or wrong-shaped state returns the fixed `SDD state unavailable.` notice; parser details and offending values never enter context, scope-block, or verification-continuation messages.

## JSON protocol

All scripts read one Hermes shell-hook payload from stdin and write one JSON object to stdout. Policy decisions use Hermes-canonical shapes:

- block: `{"action":"block","message":"..."}`
- continue verification: `{"action":"continue","message":"..."}`
- inject context: `{"context":"..."}`
- no-op: `{}`

The scope adapter converts malformed input into a block response. Combined with `fail_closed: true`, spawn failures, timeouts, and non-JSON output also block in Hermes.
