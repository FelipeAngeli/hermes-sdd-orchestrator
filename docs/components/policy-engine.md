# Security policy engine (opt-in)

[Docs index](../README.md) · Related: [Hooks](hooks.md), [Loop engine](loop-engine.md), [Validation gates](validation-gates.md), [Agent manager](agent-manager.md)

**Files:** `runtime/policy_registry.py`, `runtime/policy_commands.py`, `runtime/policy_git.py`, `runtime/policy_evaluator.py`, `runtime/policy_budget.py`, `runtime/policy_approval.py`, `runtime/policy_audit.py`, `runtime/policy_hooks.py`, `runtime/policy_cli.py`, `runtime/loop_security.py`, `policies/SECURITY_POLICY.json`, `schemas/SECURITY_POLICY_SCHEMA.json`.

A deterministic security policy for agents, sub-agents, tools, retries, budgets and gates. It is **off by default**: nothing changes until the owner adds the `enforce-policy.py` hook to a dedicated Hermes profile, or a host program passes `policy=` to `LoopEngine` or `GateEngine`. It never replaces an existing control. The slice-scope hook, Hermes' own approvals, the executor sandbox and the HSM supervisor's approval rules keep running unchanged, and the policy only adds refusals on top of them.

## Components

| Component | Module | Role |
| --- | --- | --- |
| PolicyRegistry | `policy_registry.py` | Loads `SECURITY_POLICY.json` (schema-checked, owner-only writable, no symlink, at most `MAX_POLICY_BYTES`), returns it with its `version` and the SHA-256 of its bytes. Floors live in code (`FLOORS`, `FLOOR_PROTECTED_PATHS`, `MIN_REPETITION_WINDOW_SECONDS`, `RAISABLE_LIMITS`): a file may tighten them, and one that loosens any of them is refused with `POLICY_WEAKENS_FLOOR`. Other refusals: `POLICY_UNREADABLE`, `POLICY_FILE_UNSAFE`, `POLICY_SCHEMA_INVALID` (also for an `extra_deny_patterns` entry where a group holding a quantifier or an alternation is itself quantified, at any depth, or with a back-reference; both can backtrack catastrophically). |
| PolicyEvaluator | `policy_evaluator.py`, `policy_commands.py`, `policy_git.py` | `evaluate(policy, action, workspace, extra_roots)` returns `ALLOW`, `DENY` or `REQUIRE_APPROVAL`, the rules (`P1`…`P12`) and reason codes. Command rules are the HSM approval families (`DENY_FAMILIES`, `APPROVAL_FAMILIES`), applied to the raw text and to each segment with wrappers (`env X=…`, `nohup`, privilege wrappers…) stripped and the program reduced to its basename. See [How commands are read](#how-commands-are-read). |
| BudgetGuard | `policy_budget.py` | Checks attempts, tokens, node runs, time and concurrency against the policy and the loop's own `limits`/`usage` (`USAGE_KEYS`); it keeps no counters. A budget raise needs an explicit rule: an allowed limit, at most `max_factor` times the current value, from a named human with their words. |
| ApprovalBridge | `policy_approval.py` | One approval per exact action, used once (`consume` renames the grant atomically, so of two racing executions one wins). The grant is bound to the action's SHA-256, so a changed action is not covered, and it expires within the policy TTL. It can be revoked before use. Only a named person can approve: machine names (`loop_control.is_machine_name`) and teams, roles, bots or mailboxes (`is_human_approver`) are refused, also when two group words are glued (`opsteam`), for `team…`/`group…` compounds (`teamcity`) and for tool bots (`slackbot`, `gitbot`). Surnames that merely contain such a word (`Botelho`, `Talbot`, `Steamer`) stay valid, so an unlisted `<word>bot` name can pass this check; `is_machine_name` still applies. There is no session, always or all scope. |
| SecurityAudit | `policy_audit.py` | Append-only `policy-state/security-audit.jsonl` (0600, under `flock`). Each record carries `prev_sha256` and its own `sha256`, so an edit, deletion or reordering breaks the chain at that record (`verify`). Records hold codes, ids and hashes; command text is stored only as `command_sha256`, and strings go through `redaction.redact` plus a mask for short secret keys (`pass=`, `pin:`, `otp=`). A decision that cannot be audited is a DENY. |
| HookDispatcher | `policy_hooks.py` | Maps the proposed hooks onto existing events (table below), audits each verdict, writes an `ON_POLICY_VIOLATION` record for every DENY, and lets a matching grant turn `REQUIRE_APPROVAL` into `ALLOW`. Any internal error is a DENY with `policy_state_unavailable`. |
| Shell hook and CLI | `hooks/enforce-policy.py`, `policy_cli.py` | `pre_tool_call` adapter (fail closed) and the owner's commands. |

## Hooks

| Proposed hook | Rides on | Action kind |
| --- | --- | --- |
| `before_tool_execution` | Hermes `pre_tool_call` for `terminal`, `execute_code`, `write_file`, `patch`, `read_file`, `search_files` | `tool_execution` |
| `before_subagent_spawn` | Hermes `pre_tool_call` for `delegate_task` | `subagent_spawn` |
| `before_agent_start` | `LoopEngine(policy=…)` before a node's handler runs | `agent_start` |
| `before_loop_retry` | `LoopEngine(policy=…)` before an automatic retry | `loop_retry` |
| `before_gate_decision` | `GateEngine(policy=…)` before a gate evaluates | `gate_decision` |
| `after_task_completion` | `LoopEngine(policy=…)` after a node completes | `task_completion` |
| `on_policy_violation` | written by the dispatcher for every DENY | — |

Hermes children inherit the parent's toolsets: `delegate_task` has no model-facing toolsets argument, and Hermes enforces `delegation.max_spawn_depth`. So for `delegate_task` the hook checks the batch size against `max_children_per_parent`, and a host that knows the real tool lists checks the subset rule through `subagent_spawn`. In the loop engine a `DENY` halts the node with `POLICY_DENIED`, and an action that still needs a human approval halts with `POLICY_APPROVAL_REQUIRED`. Both are human decisions (`resolve --decision retry` after the cause is fixed or the approval granted), and the retry is checked again.

## How commands are read

`policy_commands.classify` reads a `terminal` command roughly as the shell would before matching the rules:

- `${IFS}` and `$IFS` become a space. Any `$` in a word, including the program word, needs a human approval, because a variable can stand for any program or argument. Brace expansion (`{a,b}`) needs one too.
- Quotes are removed, and each word with a glob (`*`, `?`, `[…]`) is also matched against the files it expands to, including a directory glob in front of a file that does not exist yet. So `[p]olicy_cli.py grant` and `"polic"y_cli.py grant` are refused like the plain name.
- Every output redirection (`>`, `>>`, `>|`, `N>`, `&>`, and `N>&word` when `word` is not a descriptor) to a protected path is denied, whatever the program.
- The program name is lower-cased before matching, because the default macOS and Windows filesystems run `RM` as `rm`. Grouping and keywords before it (`{`, `(`, glued or as a separate word, `!`, `if`, `then`, `do`, `while`…) are skipped, so `{ RM -rf x; }` is read like `rm -rf x`.
- `find` is read with each command it runs through `-exec`/`-execdir`/`-ok`/`-okdir`, and `-fprint`/`-fls`/`-delete` count as writes. Every argument of an interpreter may be an output, so a protected path there is denied, with one exception: the script itself, when only known-safe options precede it (`detect_stack.SCRIPT_SAFE_OPTIONS`, for example `python3 -B`, `bash -x`, `ruby -w`, `node --inspect`) and no environment assignment comes first, neither as a prefix nor in an earlier segment (`export`, `declare -x`, `typeset -x`, `set -a`, a bare `VAR=…`): `NODE_OPTIONS`, `RUBYOPT`, `PERL5OPT`, `BASH_ENV`, `PYTHONSTARTUP` can preload code.
- A `git` call that sets a config value git runs as a program (`-c alias.x=!…`, `core.sshCommand`, `core.pager` other than `cat`/`less`/`more`, `--config-env`, `git config alias.*`, or a `GIT_*` environment assignment) needs a human approval.
- A word with globs in more than `MAX_GLOB_COMPONENTS` path components, or more than `MAX_GLOB_WORDS` glob words in one command, is not expanded (cost inside the hook) and needs a human approval; other globs expand to at most `MAX_GLOB_MATCHES` matches. A command longer than `MAX_COMMAND` characters is not analysed and needs an approval.
- Any other option (`-`, `-s`, `-e`, `-c`, `-p`, `-r`, `-M`, `-m`, `-l`, clusters such as `-xs`, glued values such as `-e1` or `--eval=`, `--require`, `--loader`) may read code from stdin, inline or a preload, so there is no script argument.
- `policy_git.py` decides which git calls run another program. Reading or removing a value passes when the action comes before the key (`git config --get k`, `--list`, `--unset k`, `get k`); a word such as `unset` or `--show-origin` after the key does not make a write a read. A pager or editor passes only as the bare name `cat`, `true`, or `less`/`more` with display flags (`-R`, `-F`, `-X`, `-S`, `-K`…); a path such as `./less` runs a program from the repository and needs an approval. An empty `credential.helper` passes. Of the `GIT_*` variables only `GIT_AUTHOR_*`, `GIT_COMMITTER_*` (name, email, date), `GIT_TERMINAL_PROMPT`, `GIT_DIR=.git` and a safe `GIT_PAGER` pass; any other (`GIT_INDEX_FILE`, `GIT_WORK_TREE`, `GIT_TRACE`, `GIT_CONFIG_*`, `GIT_SSH*`…) needs an approval.
- Relative paths resolve against the tool's `workdir` when the call has one, otherwise against the workspace. A `workdir` outside the workspace needs a human approval.
- Protected paths are compared case-insensitively after Unicode normalisation, because the default macOS and Windows filesystems resolve `.HERMES/...` and `.hermes/...` to the same file.
- Interpreter names come from `detect_stack.INLINE_CODE_INTERPRETERS`, which keeps the controller language-neutral.

The source splits a few privileged command names (`"su" "do"`, `su[d]o`, `print[e]nv`), and the tests assemble their attack strings at run time. Hermes `skills-guard` reads those literals as threats even inside a deny rule, and a published skill it rates `DANGEROUS` cannot be installed. Behaviour is identical.

## Permission matrix

`policy_cli.py matrix` prints the same table. Rules: P1 a child never widens the parent's permissions, P2 destructive operations stay blocked, P3 approvals are single and bounded, P4 no agent changes its own policy, P5 budget raises need an explicit rule, P6 delegation is bounded, P7 repeated dangerous operations are detected, P8 secrets are never exposed, P9 workspaces stay isolated, P10 nothing runs outside the authorised scope, P11 security policy beats agent instructions, P12 Jev output never changes a security decision.

| Kind | Condition | Outcome | Rules |
| --- | --- | --- | --- |
| `tool_execution` | `terminal`: destructive, privileged, permission widening, network exfiltration | `DENY` | P2 |
| `tool_execution` | `terminal`: credential stores and environment dumps | `DENY` | P8 |
| `tool_execution` | `terminal`: `--yolo`, `--dangerously-*`, `bypassPermissions`, approve-all | `DENY` | P1 |
| `tool_execution` | `terminal`: `policy_cli` grant/revoke, `policy-state`, the security audit | `DENY` | P3, P4 |
| `tool_execution` | `terminal`: unverifiable construct (`$()`, `eval`, `$VAR`, brace expansion, inline interpreter) | `REQUIRE_APPROVAL` | P10 |
| `tool_execution` | `terminal`/`execute_code`: `workdir` outside the workspace | `REQUIRE_APPROVAL` | P10 |
| `tool_execution` | `terminal`/read: a path outside the workspace | `REQUIRE_APPROVAL` | P10 |
| `tool_execution` | `write_file`/`patch`: a path outside the workspace | `DENY` | P9 |
| `tool_execution` | a write to a protected controller path (`protected_paths`) | `DENY` | P4 |
| `tool_execution` | `execute_code` (arbitrary code) | `REQUIRE_APPROVAL` | P10 |
| `tool_execution` | the same refused action repeated `max_denied_repeats` times in the window | `DENY` | P7 |
| `subagent_spawn` | child tools not a subset of the parent's | `DENY` | P1 |
| `subagent_spawn` | depth above `max_depth` or batch above `max_children_per_parent` | `DENY` | P6 |
| `agent_start` | tools beyond `allowed_tools`, bypass mode, `danger-full-access` | `DENY` | P1 |
| `loop_retry` | attempts, tokens, node runs or time at their limit | `DENY` | P5 |
| `budget_raise` | limit not allowed, above `max_factor`, or not a named person with their words (no agent, team, role or mailbox) | `DENY` | P5 |
| `gate_decision` | mode below `gates.min_mode` | `DENY` | P11 |
| `task_completion` | verdict not `PASS` or no evidence | `DENY` | P11 |
| `policy_change` | any change requested by an agent | `DENY` | P4 |
| any | unknown kind or field (agent instructions, Jev output) | `DENY` | P10, P11, P12 |

P11 and P12 hold by construction: each kind has an allowlist of fields (`FIELDS`), so a payload carrying instructions, `jev` or `recommendation` is rejected as `action_malformed` instead of being read. The repository workspace is the authorised scope; the bound Obsidian project container (when the controller lives there) is added as an extra root.

## Using it

- **Activate:** merge the `enforce-policy.py` entry of `hooks/hooks.example.yaml` into a dedicated profile, with `fail_closed: true`, and approve the `(pre_tool_call, command)` pair when Hermes asks.
- **Approve one action:** when the hook blocks with a request id, the owner runs `policy_cli.py pending`, then `policy_cli.py grant --request <id> --by <name> --quote "<words>"` outside the agent session. The next identical call is allowed once. `revoke --request <id>` cancels a grant before use.
- **Check the policy:** `policy_cli.py check` validates the file. `evaluate --action FILE --workspace DIR` prints a verdict without auditing. `audit-verify` checks the chain and prints its head hash (exit 1 when broken). Global options: `--policy`, `--state-dir`.
- **Tighten it:** edit `SECURITY_POLICY.json` outside a session (lower a maximum, add `protected_paths` or `extra_deny_patterns`, set `gates.min_mode` to `ENFORCE`) and bump `version`.

### `policy_cli.py` commands

Global options `--policy` (default `policies/SECURITY_POLICY.json`) and `--state-dir` (default `<controller>/policy-state`).

| Command | Options | Effect |
| --- | --- | --- |
| `check` | — | Validates the policy file; prints `VALID` and its identity, or the refusal code. |
| `evaluate` | `--action FILE`, `--workspace DIR` | Prints the verdict for one action JSON; not audited. |
| `pending` | — | Lists request ids waiting for a human approval. |
| `grant` | `--request ID`, `--by NAME`, `--quote TEXT` | Approves that request once; refused for machine names or empty words. |
| `revoke` | `--request ID` | Cancels a grant before it is used. |
| `audit-verify` | — | Verifies the audit chain and prints its head; exit 1 when broken. |
| `matrix` | — | Prints the permission matrix. |

## Compatibility

- With the hook inactive and no `policy=` passed, behaviour is unchanged. The loop engine (83 tests) and gate engine (140 tests) suites pass unmodified.
- Two stop reasons are new in the loop engine: `POLICY_DENIED` maps to Hermes `ACTION_BLOCKED`, and `POLICY_APPROVAL_REQUIRED` maps to `HUMAN_APPROVAL_REQUIRED`. Both accept `resolve --decision retry`.
- The command rules can refuse commands the scope hook allowed before, for example `rm -rf build` in IMPLEMENT. That is the intent. To run one, a human does it outside the agent, or the owner adds a narrower rule in a stricter policy. Commands the rules cannot analyse need a human approval instead of passing.

## Rollback

1. Remove the `enforce-policy.py` entry from the profile's `pre_tool_call` hooks. The other hooks are independent.
2. Stop passing `policy=` to `LoopEngine`/`GateEngine` in host code. `None` is the default.
3. Keep `policy-state/` for the record, or archive it after `audit-verify`. Nothing else reads it.

Rolling back never needs a policy edit, and editing the policy never loosens anything below the code floors.

## Limits

- **The `terminal` command check is best effort.** It is a deny list over the command string, not a sandbox or a shell parser, and three review rounds kept finding commands it reads as harmless that write a protected path or run something else. Known gaps, accepted for this release:
  - control structures beyond the leading keyword (`case`, functions defined earlier in the same command, `for x in …; do`) can still hide a program word;
  - a quoted redirection target (`>"path"`, `&>'path'`) is not unquoted before matching;
  - writing an ancestor of a protected path (`cp -r dir .hermes/orchestration/`, `rsync`, `tar -C`, `git checkout ref -- .hermes`), and `cd dir && cmd`, which changes the base later segments resolve against;
  - `awk`/`sed` programs (`system()`, `print > file`, `sed w`/`e`);
  - more git keys that run programs (`remote.*.uploadpack`, `difftool.*.cmd`, `pager.*`, `ext::` URLs), and edits of `.git/config` or `.git/hooks`;
  - `git push` with a `+refspec`, `-d` or `:branch`;
  - polynomial deny patterns (`.*.*.*x`) the owner may add to the policy.
- Rely on the other controls for those: Hermes' own approvals for dangerous commands, the executor sandbox, the scope hook for structured file tools, and the HSM supervisor for remote approvals all keep running. The structured file tools (`write_file`, `patch`) are checked by path and are not affected by the shell gaps.
- The audit chain proves integrity against edits that do not rewrite every later hash. Whoever can rewrite the whole file can forge a new chain, so pin the `audit-verify` head somewhere the agent cannot write if you need proof.
- Repetition detection reads the audit within `window_seconds`, so it counts only decisions made through the dispatcher.
- Hermes `delegate_task` does not expose child toolsets, so the subset rule applies to hosts that pass real tool lists (`subagent_spawn`). For Hermes, the child's tools are the parent's minus `DELEGATE_BLOCKED_TOOLS`.
