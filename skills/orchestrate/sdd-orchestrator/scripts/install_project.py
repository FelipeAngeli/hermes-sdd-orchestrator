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
    f"{CONFIG_ROOT}/PROJECT_SETUP.md",
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
    """Return distributable template files, never interpreter artifacts."""
    return sorted(
        path
        for path in TEMPLATE.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    )


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
    spec.loader.exec_module(module)
    return module


def detect_stack(target: Path) -> dict:
    """Read-only, evidence-based stack report used to configure GATES.md."""
    return _load_template_module("sdd_detect_stack", "runtime/detect_stack.py").detect(target)


def _answer_state(value: str) -> tuple[bool, bool]:
    normalized = value.strip()
    if normalized.casefold() in {"", "unresolved", "null", "~", "{}", "[]"}:
        return True, True
    if normalized.casefold() == "none":
        return False, True
    try:
        decoded = json.loads(normalized)
    except (json.JSONDecodeError, TypeError):
        return True, False
    if not isinstance(decoded, str) or not decoded.strip():
        return True, False
    if decoded.strip().casefold() in {"unresolved", "null", "~", "{}", "[]"}:
        return True, True
    return False, True


def _unresolved_answer(value: str) -> bool:
    unresolved, valid = _answer_state(value)
    return unresolved or not valid


def _read_onboarding_record(target: Path, question_ids: set[str]) -> tuple[dict[str, str], bool, str | None, list[str]]:
    path = target / CONFIG_ROOT / "PROJECT_SETUP.md"
    if not path.is_file():
        return {}, False, None, ["RECORD_MISSING"]
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeError:
        return {}, False, None, ["INVALID_ENCODING"]

    try:
        start = lines.index("```yaml") + 1
        end = lines.index("```", start)
    except ValueError:
        return {}, False, None, ["YAML_BLOCK_INVALID"]

    metadata: dict[str, str] = {}
    answers: dict[str, str] = {}
    issues: list[str] = []
    in_answers = False
    for line in lines[start:end]:
        if not line.strip():
            continue
        if in_answers and line.startswith("  "):
            if line.startswith("    ") or ":" not in line:
                issues.append("ANSWER_LINE_INVALID")
                continue
            key, value = line.strip().split(":", 1)
            if key not in question_ids:
                issues.append("ANSWER_KEY_UNKNOWN")
            elif key in answers:
                issues.append("ANSWER_KEY_DUPLICATE")
            else:
                answers[key] = value.strip()
            continue
        in_answers = False
        if line.startswith(" ") or ":" not in line:
            issues.append("SETUP_LINE_INVALID")
            continue
        key, value = line.split(":", 1)
        if key not in {"schema_version", "status", "answers"}:
            issues.append("SETUP_KEY_UNKNOWN")
        elif key in metadata:
            issues.append("SETUP_KEY_DUPLICATE")
        else:
            metadata[key] = value.strip()
            in_answers = key == "answers" and not value.strip()

    if metadata.get("schema_version") != "1":
        issues.append("SCHEMA_VERSION_UNSUPPORTED")
    if metadata.get("status") not in {"PENDING", "COMPLETE"}:
        issues.append("STATUS_INVALID")
    if metadata.get("answers") != "":
        issues.append("ANSWERS_SECTION_INVALID")
    missing = question_ids - set(answers)
    if missing:
        issues.append("ANSWERS_MISSING")
    for value in answers.values():
        _, valid = _answer_state(value)
        if not valid:
            issues.append("ANSWER_VALUE_INVALID")
    all_resolved = not missing and all(not _unresolved_answer(answers[key]) for key in question_ids)
    expected_status = "COMPLETE" if all_resolved else "PENDING"
    if metadata.get("status") in {"PENDING", "COMPLETE"} and metadata["status"] != expected_status:
        issues.append("STATUS_ANSWER_MISMATCH")

    non_structural = {"ANSWERS_MISSING", "ANSWER_VALUE_INVALID", "STATUS_ANSWER_MISMATCH"}
    structural_issues = set(issues) - non_structural
    if structural_issues:
        answers = {}
    return answers, not issues, metadata.get("status"), sorted(set(issues))


def onboarding_questions(target: Path | None = None) -> dict[str, object]:
    """Return only unresolved project-local questions after installation."""
    questions = [
        {
            "id": "issue_tracker",
            "prompt": "Which issue tracker should the orchestrator read, and may it create or update issues?",
            "accepted_answers": ["provider and project with read/write permissions", "none"],
        },
        {
            "id": "obsidian",
            "prompt": "Should the orchestrator connect this project to an Obsidian vault?",
            "accepted_answers": ["vault and project container", "none"],
        },
        {
            "id": "project_tools",
            "prompt": "Which other project-specific tools must the orchestrator use, and with what permissions?",
            "accepted_answers": ["tool, purpose, and read/write permissions", "none"],
        },
    ]
    answers: dict[str, str] = {}
    record_valid = False
    record_status: str | None = None
    record_issues = ["RECORD_MISSING"]
    if target:
        answers, record_valid, record_status, record_issues = _read_onboarding_record(
            target, {str(question["id"]) for question in questions}
        )
        questions = [
            question
            for question in questions
            if _unresolved_answer(answers.get(str(question["id"]), "UNRESOLVED"))
        ]
    complete = record_valid and record_status == "COMPLETE" and not questions
    return {
        "status": "COMPLETE" if complete else "REQUIRED",
        "scope": "ORCHESTRATOR_ONLY",
        "ask_only_unresolved": True,
        "record_valid": record_valid,
        "record_status": record_status,
        "record_issues": record_issues,
        "questions": questions,
    }


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
  project_tools: UNRESOLVED
```

## Onboarding rules

- Ask only about orchestrator connectivity, never product requirements or implementation preferences.
- Inspect repository evidence first and ask only questions whose answers remain unresolved.
- Accept `none` as an explicit answer for every integration; write any descriptive answer as a JSON double-quoted string on the same line.
- For an issue tracker, record the provider, project identifier, and separate read/write permission; verify connectivity read-only before any mutation.
- For Obsidian, record whether it is enabled and, only when enabled, the vault and project container required by `BOOTSTRAP.md`.
- For other project tools, record each tool's purpose and separate read/write permission.
- Never request passwords, tokens, verification codes, or other secrets in chat. Use Hermes credential facilities when authentication is required.
- Set `status: COMPLETE` only after all three answers are resolved, including explicit `none` answers.
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
        state_paths = (
            f"{CONFIG_ROOT}/STATE.md",
            f"{CONFIG_ROOT}/PROJECT_SETUP.md",
            f"{CONFIG_ROOT}/INCIDENTS.md",
            f"{CONFIG_ROOT}/ACTION_JOURNAL.json",
        )
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
        report = {
            "status": "READY" if planned else "ALREADY_INITIALIZED", "target": str(target), "planned": planned,
            "applied": False, "stack": detect_stack(target), "onboarding": onboarding_questions(target),
            "next_step": "Resolve the project onboarding questions, then configure verified commands in GATES.md.",
        }
        if args.apply and planned:
            for source in template_files():
                relative = source.relative_to(TEMPLATE)
                destination = target / relative
                if not destination.exists():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)
            (target / CONFIG_ROOT / "STATE.md").write_text(state(workspace), encoding="utf-8")
            (target / CONFIG_ROOT / "PROJECT_SETUP.md").write_text(project_setup(), encoding="utf-8")
            (target / CONFIG_ROOT / "INCIDENTS.md").write_text("# SDD Orchestration Incidents\n\nNo incidents recorded.\n", encoding="utf-8")
            (target / CONFIG_ROOT / "ACTION_JOURNAL.json").write_text(empty_journal(target, workspace), encoding="utf-8")
            update_exclude(target)
            report["applied"] = True
            report["onboarding"] = onboarding_questions(target)
        if args.json:
            print(json.dumps(report, ensure_ascii=False))
        else:
            ecosystems = ", ".join(f'{item["ecosystem"]}@{item["path"]}' for item in report["stack"]["ecosystems"]) or "none detected"
            print(f'{report["status"]}: {len(planned)} files planned; stack: {ecosystems}')
        return 0
    except (InstallError, OSError, subprocess.TimeoutExpired) as error:
        report = {"status": "BLOCKED", "reason": str(error)}
        print(json.dumps(report, ensure_ascii=False) if args.json else f'BLOCKED: {error}', file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
