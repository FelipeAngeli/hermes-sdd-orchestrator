#!/usr/bin/env python3
"""Install the SDD Orchestrator as untracked, project-local configuration."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT.parent / "templates"
CONFIG_ROOT = ".hermes/orchestration"
LOCAL_PATHS = (
    ".hermes.md",
    CONFIG_ROOT,
    f"{CONFIG_ROOT}/STATE.md",
    f"{CONFIG_ROOT}/INCIDENTS.md",
    f"{CONFIG_ROOT}/ACTION_JOURNAL.json",
    f"{CONFIG_ROOT}/action-journal-history/",
)


class InstallError(RuntimeError):
    pass


def git(target: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(target), *args], text=True, capture_output=True, timeout=20, check=False
    )
    if result.returncode:
        raise InstallError((result.stderr or result.stdout).strip() or "Git preflight failed.")
    return result.stdout.strip()


def require_root(value: str) -> tuple[Path, dict[str, str]]:
    target = Path(value).expanduser().resolve()
    if not target.is_dir():
        raise InstallError("TARGET_NOT_DIRECTORY")
    root = Path(git(target, "rev-parse", "--show-toplevel")).resolve()
    if root != target:
        raise InstallError(f"TARGET_NOT_REPOSITORY_ROOT: use {root}")
    branch = git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    return root, {
        "path": str(root),
        "branch": branch,
        "head": git(root, "rev-parse", "HEAD"),
        "git_common_dir": git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"),
    }


def is_tracked(target: Path, relative: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(target), "ls-files", "--error-unmatch", "--", relative],
        text=True, capture_output=True, timeout=20, check=False,
    )
    return result.returncode == 0


def reject_symlinks(target: Path, relative: str) -> None:
    current = target
    for part in Path(relative).parts:
        current /= part
        if current.is_symlink():
            raise InstallError(f"SYMLINK_REJECTED: {current}")


def template_files() -> list[Path]:
    return sorted(path for path in TEMPLATE.rglob("*") if path.is_file())


def state(workspace: dict[str, str]) -> str:
    q = lambda value: json.dumps(value, ensure_ascii=False)
    return f'''# SDD Orchestration State

The YAML block is the source of truth. Execution Log is historical only.

```yaml
schema_version: 2
workspace:
  path: {q(workspace["path"])}
  git_common_dir: {q(workspace["git_common_dir"])}
repository:
  name: {q(Path(workspace["path"]).name)}
  branch: {q(workspace["branch"])}
  head: {q(workspace["head"])}
ticket:
  id: IDLE
  title: null
  objective: null
  scope_confirmed: []
stage:
  current: IDLE
  status: WAITING
  completed: []
  skipped: []
stage_provenance: {{}}
loop:
  policy_version: "2.5"
  policy_path: ".hermes/orchestration/policies/LOOP_POLICY.md"
  mode: MANUAL
  run: {{id: null, started_at: null, finished_at: null}}
  budgets:
    stage_transitions: {{max: 3, used: 0}}
    executor_calls: {{max: 8, used: 0}}
    corrective_retries: {{max_per_action: 1, used_current_action: 0}}
    tdd_slices: {{max: 3, used: 0}}
    investigation_expansions: {{max_per_stage: 1, used_current_stage: 0}}
    review_cycles: {{max: 2, used: 0}}
    ci_runs: {{max: 1, used: 0}}
    external_mutations: {{max: 0, used: 0}}
  control: {{stop_reason: NONE, human_approval_required: false, requested_approval: null, loop_active: false}}
  progress: {{start_stage: null, current_action: null, current_executor: null, current_slice: null, current_command: null, command_timeout_seconds: null, last_result: null, next_action: null}}
  last_run: {{id: null, result: null, stop_reason: NONE, stage_transitions: 0, executor_calls: 0, tdd_slices_completed: 0, review_cycles: 0, ci_runs: 0, repository_integrity: null}}
bounded_run_plan: null
resume: {{last_gate: null, next_action: null, next_command: null}}
baseline: {{captured: false, head: null, branch: null, protected_preexisting: []}}
ownership: {{agent_owned: [], protected_preexisting: [], generated_or_ignored: [], out_of_scope: []}}
environment:
  preflight_completed: false
  executor: {{can_edit: UNKNOWN, can_run_focused_tests: UNKNOWN, can_format: UNKNOWN, can_analyze: UNKNOWN, can_ci: UNKNOWN}}
  host: {{can_format: UNKNOWN, can_analyze: UNKNOWN, can_ci: UNKNOWN}}
gates:
  focused_tests: {{status: PENDING}}
  format: {{status: PENDING}}
  analyze: {{status: PENDING}}
  review: {{status: PENDING}}
  ci: {{status: PENDING}}
blockers: []
fallbacks: []
evidence: {{tdd_slices: [], historical_validation: [], historical_review: null, final_ci: null}}
```

## Execution Log

- Initialized by `hermes-sdd-orchestrator`; no demand has started.
'''


def empty_journal(target: Path, workspace: dict[str, str]) -> str:
    module_path = target / CONFIG_ROOT / "runtime" / "action_journal.py"
    spec = importlib.util.spec_from_file_location("sdd_action_journal", module_path)
    if spec is None or spec.loader is None:
        raise InstallError("ACTION_JOURNAL_MODULE_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    journal = module.empty_journal(workspace)
    module.validate_journal(journal)
    return json.dumps(journal, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"


def update_exclude(target: Path) -> None:
    path = Path(git(target, "rev-parse", "--path-format=absolute", "--git-path", "info/exclude"))
    if path.is_symlink():
        raise InstallError("EXCLUDE_SYMLINK_REJECTED")
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    entries = existing.splitlines()
    additions = [item for item in LOCAL_PATHS if item not in entries]
    if additions:
        path.write_text(existing + ("" if not existing or existing.endswith("\n") else "\n") + "\n".join(additions) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default=".", help="existing Git worktree root (default: current directory)")
    parser.add_argument("--apply", action="store_true", help="write files after a successful dry run")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable report")
    args = parser.parse_args()
    try:
        target, workspace = require_root(args.target)
        planned: list[str] = []
        for source in template_files():
            relative = source.relative_to(TEMPLATE).as_posix()
            reject_symlinks(target, relative)
            destination = target / relative
            if is_tracked(target, relative):
                raise InstallError(f"TRACKED_DESTINATION_PATH: {relative}")
            if destination.exists() and destination.read_bytes() != source.read_bytes():
                raise InstallError(f"CONFIG_CONFLICT: {relative}")
            if not destination.exists():
                planned.append(relative)
        state_paths = (f"{CONFIG_ROOT}/STATE.md", f"{CONFIG_ROOT}/INCIDENTS.md", f"{CONFIG_ROOT}/ACTION_JOURNAL.json")
        existing_state: list[str] = []
        for relative in state_paths:
            reject_symlinks(target, relative)
            if is_tracked(target, relative):
                raise InstallError(f"TRACKED_DESTINATION_PATH: {relative}")
            if (target / relative).exists():
                existing_state.append(relative)
        if existing_state:
            if len(existing_state) != len(state_paths) or planned:
                raise InstallError(f"LOCAL_STATE_REQUIRES_REVIEW: {', '.join(existing_state)}")
        else:
            planned.extend(state_paths)
        report = {"status": "READY" if planned else "ALREADY_INITIALIZED", "target": str(target), "planned": planned, "applied": False}
        if args.apply and planned:
            for source in template_files():
                relative = source.relative_to(TEMPLATE)
                destination = target / relative
                if not destination.exists():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)
            (target / CONFIG_ROOT / "STATE.md").write_text(state(workspace), encoding="utf-8")
            (target / CONFIG_ROOT / "INCIDENTS.md").write_text("# SDD Orchestration Incidents\n\nNo incidents recorded.\n", encoding="utf-8")
            (target / CONFIG_ROOT / "ACTION_JOURNAL.json").write_text(empty_journal(target, workspace), encoding="utf-8")
            update_exclude(target)
            report["applied"] = True
        print(json.dumps(report, ensure_ascii=False) if args.json else f'{report["status"]}: {len(planned)} files planned')
        return 0
    except (InstallError, OSError, subprocess.TimeoutExpired) as error:
        report = {"status": "BLOCKED", "reason": str(error)}
        print(json.dumps(report, ensure_ascii=False) if args.json else f'BLOCKED: {error}', file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
