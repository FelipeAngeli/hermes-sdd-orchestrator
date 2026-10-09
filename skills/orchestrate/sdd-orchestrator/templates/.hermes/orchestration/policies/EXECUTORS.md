# Executors

Project-owned file. It selects, per stage, which external CLI runs the worker, which model it uses, and its hard timeout. Edit the JSON block below to change executors; never edit `.hermes.md`, `LOOP_POLICY.md` or a command line for that. `runtime/executor_launch.py` is the only reader, and it is the only way to dispatch a worker.

```json
{
  "executors_version": 1,
  "stages": {
    "SPECIFY":   {"executor": "claude", "model": null, "timeout_seconds": 600, "max_turns": 40},
    "CLARIFY":   {"executor": "claude", "model": null, "timeout_seconds": 600, "max_turns": 40},
    "PLAN":      {"executor": "claude", "model": null, "timeout_seconds": 900, "max_turns": 60},
    "TASKS":     {"executor": "codex",  "model": null, "timeout_seconds": 600, "max_turns": null},
    "IMPLEMENT": {"executor": "codex",  "model": null, "timeout_seconds": 900, "max_turns": null},
    "TEST":      {"executor": "codex",  "model": null, "timeout_seconds": 600, "max_turns": null},
    "REVIEW":    {"executor": "claude", "model": null, "timeout_seconds": 600, "max_turns": 40}
  }
}
```

## Fields

- `executor`: `claude` or `codex`.
- `model`: `null` uses the CLI's configured default; otherwise a plain model name passed as `--model`.
- `timeout_seconds`: hard wall-clock limit in seconds, 1–7200. On expiry the launcher kills the whole process group and records exit code `124`.
- `max_turns`: Claude only (`--max-turns`); `null` leaves the CLI default. Codex ignores it.
- Optional `tools` (Claude `--tools`; default `Read,Grep,Glob`), `permission_mode` (Claude `--permission-mode`) and `sandbox` (Codex `read-only` or `workspace-write`; default `workspace-write` for IMPLEMENT and TEST, `read-only` otherwise).
- Optional top-level `allow_bypass_permissions` (default `false`). A stage with `permission_mode: "bypassPermissions"` is refused with `EXECUTOR_POLICY_UNSAFE` unless the owner sets `"allow_bypass_permissions": true` next to `executors_version`, accepting that the worker then runs every tool without a check.

A stage omitted from `stages` keeps the default shown above. A missing file means the same defaults. An invalid block returns `POLICY_INVALID` and nothing is dispatched.

## Trust and isolation

Whoever can edit this file chooses the program that runs, its permissions and its model, so treat it like code: only the project owner edits it, and the installer reports its SHA-256 for review. `sdd.py start` pins that SHA-256 (together with `GATES.md`) in `delivery.controller_policies`, and every `sdd.py gate` re-verifies both: a file that changed mid-demand stops the loop with `CONTROLLER_POLICY_CHANGED_DURING_DEMAND` until the user re-confirms it with `sdd.py confirm-policy --name executors --quote '<their words>'`. In `--local-storage` mode this file lives inside the repository a writing worker may edit, so the pin is what distinguishes the owner's change from a worker's.

- A writing worker (Codex with `workspace-write`, or Claude with any tool other than `Read`, `Grep`, `Glob`, `LS`, `NotebookRead`) can write only inside the repository. Codex runs with `--config sandbox_workspace_write.writable_roots=[]`; `--add-dir` outside the repository or under the controller's `.hermes` directory is refused with `ADD_DIR_WRITABLE_REFUSED`. In Obsidian mode the controller container (playbooks, briefs) is passed with `--read-dir`: read-only Claude receives it as `--add-dir`, Codex reads it through its sandbox, and a Claude stage with write tools is refused, so the worker cannot reach `GATES.md`, STATE or the journal at all. In `--local-storage` mode they live *inside* the repository the worker writes to, so prevention is impossible and the controller detects instead: the pinned hashes above stop any gate under a changed `GATES.md`/`EXECUTORS.md`, and `sdd.py accept` refuses a STATE that changed outside the journal's own commit (`STATE_MODIFIED_DURING_ACTION`). Obsidian storage remains the stronger configuration.
- The worker inherits only `PATH`, `HOME`, `LANG`, `LC_*`, `TMPDIR`, `USER`, `LOGNAME`, `SHELL`, `TERM`, `TZ`, `XDG_*` directories, certificate and proxy variables, plus its own `ANTHROPIC_*`/`CLAUDE_*` (Claude) or `OPENAI_*`/`CODEX_*` (Codex). `TYPESAFE_*`, `JEV_*`, `HERMES_*` and every other `*_API_KEY`/`*_TOKEN` are dropped.
- On timeout, SIGTERM or SIGHUP the launcher kills the worker's process group (TERM, then KILL). A grandchild that calls `setsid` leaves that group and can survive; the CLIs are trusted not to do that.

## Before the first demand

Run `python3 .hermes/orchestration/runtime/executor_launch.py preflight --executor <claude|codex> [--model <model>] --probe` for every executor this file selects. `READY` means the CLI, its required flags, the login and the model work. `BLOCKED` names the reason and the fix.
