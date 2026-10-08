"""Template payload discovery and generated controller files."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from .constants import (
    CONFIG_ROOT,
    INSTALL_MANIFEST_PATH,
    UPGRADE_BACKUPS_PATH,
    UPGRADE_LOCK_PATH,
    JEV_CACHE_LOCK_PATH,
    JEV_CACHE_PATH,
    STATE_PATHS,
    TEMPLATE,
    TERMINAL_PROGRESS_LOCK_PATH,
    TERMINAL_PROGRESS_PATH,
    TYPESAFE_ENV_PATH,
)
from .errors import InstallError


def template_files() -> list[Path]:
    """Return distributable template files, never interpreter artifacts."""
    return sorted(
        path
        for path in TEMPLATE.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    )


def managed_exclude_entries() -> tuple[str, ...]:
    """Return root-anchored files owned by the installer, not broad trees."""
    relative_paths = [
        *(path.relative_to(TEMPLATE).as_posix() for path in template_files()),
        *STATE_PATHS,
        TYPESAFE_ENV_PATH,
        JEV_CACHE_PATH,
        TERMINAL_PROGRESS_PATH,
        JEV_CACHE_LOCK_PATH,
        TERMINAL_PROGRESS_LOCK_PATH,
        f"{CONFIG_ROOT}/action-journal-history/",
        INSTALL_MANIFEST_PATH,
        UPGRADE_LOCK_PATH,
        f"{UPGRADE_BACKUPS_PATH}/",
    ]
    return tuple(dict.fromkeys(f"/{relative}" for relative in relative_paths))


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


def _load_template_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, TEMPLATE / CONFIG_ROOT / relative)
    if spec is None or spec.loader is None:
        raise InstallError(f"TEMPLATE_MODULE_UNAVAILABLE: {relative}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def detect_stack(target: Path) -> dict:
    """Read-only, evidence-based stack report used to configure GATES.md."""
    return _load_template_module("sdd_detect_stack", "runtime/detect_stack.py").detect(target)


def project_setup() -> str:
    """Return the controller-owned record for one-time project onboarding."""
    return '''# SDD Project Setup

The YAML block records only project-specific connectivity needed by the orchestrator.

```yaml
schema_version: 1
status: PENDING
answers:
  issue_tracker: UNRESOLVED
  obsidian: UNRESOLVED
  typesafe_ai: UNRESOLVED
  project_tools: UNRESOLVED
```

## Onboarding rules

- Ask only about orchestrator connectivity, never product requirements or implementation preferences.
- Inspect repository evidence first and ask only questions whose answers remain unresolved.
- Accept `none` as an explicit answer for every integration. Otherwise use compact JSON on the same line: issue tracker requires `provider`, `project`, `read`, and `write`; Obsidian requires an absolute `vault` and relative `project_container`; TypeSafe requires `install:true` plus explicit `automatic_semantic_governance:true|false`; project tools require a non-empty array of objects with `tool`, `purpose`, `read`, and `write`.
- For an issue tracker, record the provider, project identifier, and separate read/write permission; verify connectivity read-only before any mutation.
- For Obsidian, record whether it is enabled and, only when enabled, the vault and project container required by `BOOTSTRAP.md`. When the official CLI is available, discover candidates read-only with `python3 .hermes/orchestration/runtime/obsidian_connector.py discover --json`; after `.hermes/obsidian.json` exists, verify the bound container and selected read transport with `python3 .hermes/orchestration/runtime/obsidian_connector.py preflight --repo . --json`. A filesystem fallback is valid; neither command writes a note.
- Jev is a TypeSafe model, not the installed product. `--typesafe-ai install --apply` installs guidance without authorizing automatic external calls; add `--automatic-jev-governance` only after explicit consent to potentially billed semantic transmissions. Existing `{"install":true}` records never grant that consent and require explicit migration. Use `--typesafe-ai none --apply` to opt out only when no TypeSafe installation is discoverable; the official `npx` command is informational and is never executed by this installer.
- For other project tools, record each tool's purpose and separate read/write permission.
- Never request passwords, tokens, verification codes, or other secrets in chat. Use Hermes credential facilities when authentication is required.
- Set `status: COMPLETE` only after all four answers are resolved, including explicit `none` answers.
'''


def empty_journal(target: Path, workspace: dict[str, str]) -> str:
    module_path = TEMPLATE / CONFIG_ROOT / "runtime" / "action_journal.py"
    spec = importlib.util.spec_from_file_location("sdd_action_journal", module_path)
    if spec is None or spec.loader is None:
        raise InstallError("ACTION_JOURNAL_MODULE_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    previous_dont_write_bytecode = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous_dont_write_bytecode
    journal_workspace = {
        key: workspace[key]
        for key in ("path", "branch", "head", "git_common_dir")
    }
    journal = module.empty_journal(journal_workspace)
    module.validate_journal(journal)
    return json.dumps(journal, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
