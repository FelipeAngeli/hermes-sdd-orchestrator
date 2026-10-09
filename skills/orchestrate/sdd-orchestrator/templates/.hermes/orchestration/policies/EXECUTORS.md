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

A stage omitted from `stages` keeps the default shown above. A missing file means the same defaults. An invalid block returns `POLICY_INVALID` and nothing is dispatched.

## Before the first demand

Run `python3 .hermes/orchestration/runtime/executor_launch.py preflight --executor <claude|codex> [--model <model>] --probe` for every executor this file selects. `READY` means the CLI, its required flags, the login and the model work. `BLOCKED` names the reason and the fix.
