#!/usr/bin/env python3
"""Prepare an existing Git worktree with local SDD V2.5 configuration."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import action_journal
import obsidian_binding
from jsonschema import Draft202012Validator

TIMEOUT_SECONDS = 15
OBSIDIAN_BINDING_FILE = ".hermes/obsidian.json"
CONFIG_FILES = (
    ".hermes.md",
    ".hermes/orchestration/policies/LOOP_POLICY.md",
    ".hermes/orchestration/policies/GATES.md",
    ".hermes/orchestration/contracts/EXECUTOR_CONTRACT.md",
    ".hermes/orchestration/schemas/EXECUTOR_RESULT_SCHEMA.json",
    ".hermes/orchestration/contracts/REVIEW_CONTRACT.md",
    ".hermes/orchestration/schemas/REVIEW_RESULT_SCHEMA.json",
    ".hermes/orchestration/runtime/validate_protocol.py",
    ".hermes/orchestration/tests/test_protocol.py",
    ".hermes/orchestration/policies/ACTION_RECOVERY.md",
    ".hermes/orchestration/schemas/ACTION_JOURNAL_SCHEMA.json",
    ".hermes/orchestration/runtime/action_journal.py",
    ".hermes/orchestration/tests/test_action_journal.py",
    ".hermes/orchestration/policies/BOUNDED_AUTOMATION.md",
    ".hermes/orchestration/schemas/BOUNDED_RUN_PLAN_SCHEMA.json",
    ".hermes/orchestration/runtime/bounded_run_planner.py",
    ".hermes/orchestration/tests/test_bounded_run_planner.py",
    ".hermes/orchestration/policies/BOUNDED_RUN_DRIVER.md",
    ".hermes/orchestration/runtime/bounded_run_driver.py",
    ".hermes/orchestration/tests/test_bounded_run_driver.py",
    ".hermes/orchestration/runtime/bounded_loop_driver.py",
    ".hermes/orchestration/tests/test_bounded_loop_driver.py",
)
LOCAL_STATE_FILES = (
    "STATE.md",
    "INCIDENTS.md",
    "ACTION_JOURNAL.json",
)
LOCAL_HISTORY_DIRECTORY = ".hermes/orchestration/action-journal-history/"


@dataclass(frozen=True)
class ObsidianTarget:
    """Where this worktree's Obsidian second brain and runtime live."""

    vault_path: Path
    project_container: str
    runtime_dir: Path

    @property
    def container_path(self) -> Path:
        return self.vault_path / self.project_container


class BootstrapError(Exception):
    def __init__(self, status: str, message: str, *, expected: str | None = None, actual: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.expected = expected
        self.actual = actual


@dataclass(frozen=True)
class Worktree:
    root: Path
    branch: str
    head: str
    common_dir: Path
    exclude_path: Path


@dataclass(frozen=True)
class RegisteredWorktree:
    path: Path
    attributes: dict[str, str]


@dataclass(frozen=True)
class Plan:
    source: Worktree
    target: Worktree
    source_hashes: dict[str, str]
    target_hashes: dict[str, str | None]
    creates: tuple[str, ...]
    status: str
    warnings: tuple[dict[str, str], ...]
    obsidian: ObsidianTarget | None = None
    runtime_creates: tuple[str, ...] = ()


def run_git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )
    if check and result.returncode:
        raise BootstrapError(
            "GIT_COMMAND_FAILED",
            "Git command failed during preflight.",
            expected="git command to succeed",
            actual=(result.stderr or result.stdout).strip(),
        )
    return result


def git_value(root: Path, *args: str) -> str:
    value = run_git(root, *args).stdout.strip()
    if not value:
        raise BootstrapError("GIT_COMMAND_FAILED", "Git returned an empty required value.", expected="non-empty Git value", actual="empty")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_directory(value: str, label: str) -> Path:
    path = Path(value)
    if not path.exists() or not path.is_dir():
        raise BootstrapError("PATH_NOT_DIRECTORY", f"{label} must be an existing directory.", expected="existing directory", actual=str(path))
    return path.resolve(strict=True)


def discover_worktree(value: str, label: str) -> Worktree:
    candidate = resolve_directory(value, label)
    root_text = git_value(candidate, "rev-parse", "--show-toplevel")
    root = Path(root_text).resolve(strict=True)
    if root != candidate:
        raise BootstrapError("NOT_WORKTREE_ROOT", f"{label} must be the Git worktree root, not a subdirectory.", expected=str(root), actual=str(candidate))
    branch_result = run_git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if branch_result.returncode:
        raise BootstrapError("DETACHED_HEAD", f"{label} has a detached HEAD and cannot receive local configuration.", expected="attached branch", actual="detached HEAD")
    branch = branch_result.stdout.strip()
    common_dir = Path(git_value(root, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve(strict=True)
    exclude_path = Path(git_value(root, "rev-parse", "--path-format=absolute", "--git-path", "info/exclude"))
    return Worktree(root=root, branch=branch, head=git_value(root, "rev-parse", "HEAD"), common_dir=common_dir, exclude_path=exclude_path)


def path_identity(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def registered_worktrees(root: Path) -> tuple[RegisteredWorktree, ...]:
    fields = run_git(root, "worktree", "list", "--porcelain", "-z").stdout.split("\0")
    records: list[RegisteredWorktree] = []
    record: list[str] = []
    for field in fields:
        if field:
            record.append(field)
            continue
        if not record:
            continue
        if not record[0].startswith("worktree "):
            raise BootstrapError("GIT_COMMAND_FAILED", "Git returned an invalid worktree porcelain record.", actual=record[0])
        attributes: dict[str, str] = {}
        for attribute in record[1:]:
            key, separator, value = attribute.partition(" ")
            attributes[key] = value if separator else ""
        records.append(RegisteredWorktree(Path(record[0].removeprefix("worktree ")), attributes))
        record = []
    return tuple(records)


def registered_participant(records: tuple[RegisteredWorktree, ...], worktree: Worktree, label: str) -> RegisteredWorktree:
    for record in records:
        if path_identity(record.path) == path_identity(worktree.root):
            if record.path.resolve(strict=True) != worktree.root:
                raise BootstrapError("REGISTERED_PATH_MISMATCH", f"{label} registration does not resolve to its Git root.", expected=str(worktree.root), actual=str(record.path))
            return record
    raise BootstrapError(f"{label.upper()}_NOT_REGISTERED", f"{label} is not registered in git worktree list.", expected=f"registered {label}", actual=str(worktree.root))


def reject_symlink_components(root: Path, relative: str, label: str) -> None:
    current = root
    for component in Path(relative).parts:
        current = current / component
        if current.is_symlink():
            raise BootstrapError("SYMLINK_REJECTED", f"{label} contains a symlink that could redirect a write.", expected="no symlink", actual=str(current))


def ensure_source_config(source: Worktree) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in CONFIG_FILES:
        reject_symlink_components(source.root, relative, "source configuration")
        path = source.root / relative
        if not path.is_file():
            raise BootstrapError("SOURCE_CONFIG_MISSING", "A required canonical configuration file is missing.", expected=relative, actual=str(path))
        hashes[relative] = sha256(path)
    return hashes


def is_tracked(target: Worktree, relative: str) -> bool:
    return run_git(target.root, "ls-files", "--error-unmatch", "--", relative, check=False).returncode == 0


def resolve_obsidian_target(
    target: Worktree, vault_arg: str | None, project_arg: str | None
) -> ObsidianTarget:
    """Validate the Obsidian binding inputs for a worktree.

    Both flags are mandatory. Deriving the project name silently is what let a
    bootstrapped repository inherit another project's container and write notes
    into it; an explicit failure is the safer contract.
    """
    if not vault_arg or not project_arg:
        raise BootstrapError(
            "OBSIDIAN_BINDING_REQUIRED",
            "Obsidian binding is mandatory: pass --obsidian-vault and --obsidian-project.",
            expected="--obsidian-vault <abs path> --obsidian-project <path under the vault>",
            actual=json.dumps({"vault": vault_arg, "project": project_arg}),
        )

    vault = Path(vault_arg)
    if not vault.is_absolute():
        raise BootstrapError(
            "OBSIDIAN_BINDING_INVALID",
            "--obsidian-vault must be an absolute path.",
            expected="absolute path",
            actual=vault_arg,
        )
    if not vault.is_dir():
        raise BootstrapError(
            "OBSIDIAN_VAULT_NOT_FOUND",
            "Obsidian vault directory does not exist.",
            expected="existing directory",
            actual=str(vault),
        )

    container_relative = project_arg.strip("/")
    if Path(project_arg).is_absolute() or ".." in Path(container_relative).parts or not container_relative:
        raise BootstrapError(
            "OBSIDIAN_BINDING_INVALID",
            "--obsidian-project must be a non-empty path relative to the vault root.",
            expected="relative path without '..'",
            actual=project_arg,
        )

    runtime = (
        vault
        / container_relative
        / obsidian_binding._DEFAULT_RUNTIME_SUBPATH
        / obsidian_binding.worktree_slug(target.root)
    )
    return ObsidianTarget(vault, container_relative, runtime)


def render_binding(obsidian: ObsidianTarget) -> str:
    payload = {
        "schema_version": obsidian_binding.SCHEMA_VERSION,
        "vault_path": str(obsidian.vault_path),
        "project_container": obsidian.project_container,
        "runtime_subpath": obsidian_binding._DEFAULT_RUNTIME_SUBPATH,
        "protocol_path": "2 - 🗂 Workflow/AI/Hermes/HERMES_OBSIDIAN_PROTOCOL.md",
        "new_project_script": "2 - 🗂 Workflow/AI/scripts/new_project.py",
        "sessions_subpath": "sessions",
        "decisions_subpath": "decisions",
        "boards_subpath": "boards",
        "write_policy": {
            "preauthorized_scope": "project_container",
            "outside_scope": "requires_human_authorization",
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def build_plan(
    source_arg: str,
    target_arg: str,
    vault_arg: str | None = None,
    project_arg: str | None = None,
) -> Plan:
    source = discover_worktree(source_arg, "source")
    target = discover_worktree(target_arg, "target")
    if source.root == target.root:
        raise BootstrapError("SAME_WORKTREE", "Source and target must be distinct existing worktrees.", expected="different roots", actual=str(source.root))
    if source.common_dir != target.common_dir:
        raise BootstrapError("DIFFERENT_REPOSITORY", "Source and target do not share a Git common directory.", expected=str(source.common_dir), actual=str(target.common_dir))
    registrations = registered_worktrees(source.root)
    registered_participant(registrations, source, "source")
    registered_participant(registrations, target, "target")
    warnings = tuple(
        {"code": "UNRELATED_PRUNABLE_WORKTREE", "path": str(record.path), "action": "NOT_TOUCHED"}
        for record in registrations
        if "prunable" in record.attributes
        and path_identity(record.path) not in {path_identity(source.root), path_identity(target.root)}
    )

    source_hashes = ensure_source_config(source)
    obsidian = resolve_obsidian_target(target, vault_arg, project_arg)
    target_hashes: dict[str, str | None] = {}
    creates: list[str] = []
    for relative in (*CONFIG_FILES, OBSIDIAN_BINDING_FILE):
        reject_symlink_components(target.root, relative, "target configuration")
        if is_tracked(target, relative):
            raise BootstrapError("TRACKED_DESTINATION_PATH", "A destination configuration path is tracked and cannot be made local.", expected="untracked path", actual=relative)
        path = target.root / relative
        if relative == OBSIDIAN_BINDING_FILE:
            # The binding is generated per target, not copied, so it has no
            # canonical source hash to compare against.
            if not path.exists():
                creates.append(relative)
            elif not path.is_file():
                raise BootstrapError("CONFIG_CONFLICT", "Destination binding path is not a regular file.", expected="regular file", actual=str(path))
            continue
        if path.exists():
            if not path.is_file():
                raise BootstrapError("CONFIG_CONFLICT", "Destination configuration path is not a regular file.", expected="regular file", actual=str(path))
            target_hashes[relative] = sha256(path)
            if target_hashes[relative] != source_hashes[relative]:
                raise BootstrapError("CONFIG_CONFLICT", "Destination configuration differs from the canonical source.", expected=source_hashes[relative], actual=target_hashes[relative])
        else:
            target_hashes[relative] = None
            creates.append(relative)

    runtime_creates: list[str] = []
    for name in LOCAL_STATE_FILES:
        runtime_file = obsidian.runtime_dir / name
        if runtime_file.is_symlink():
            raise BootstrapError("SYMLINK_REJECTED", "A runtime path in the vault is a symlink and cannot be used.", expected="regular file", actual=str(runtime_file))
        if not runtime_file.exists():
            runtime_creates.append(name)

    journal_path = obsidian.runtime_dir / "ACTION_JOURNAL.json"
    if journal_path.exists() and "STATE.md" in runtime_creates:
        raise BootstrapError("EXISTING_ACTION_JOURNAL_REQUIRES_REVIEW", "Target already has an action journal; bootstrap will not combine it with a new STATE.", expected="missing journal for fresh initialization", actual=str(journal_path))

    state_path = obsidian.runtime_dir / "STATE.md"
    if not creates and not runtime_creates:
        status = "ALREADY_INITIALIZED"
    elif state_path.exists():
        raise BootstrapError("EXISTING_STATE_REQUIRES_REVIEW", "Target has STATE.md and bootstrap would add configuration; review is required before any write.", expected="no existing STATE.md when changes are planned", actual=str(state_path))
    else:
        status = "READY"
    return Plan(source, target, source_hashes, target_hashes, tuple(creates), status, warnings, obsidian, tuple(runtime_creates))


def q(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_state(target: Worktree) -> str:
    text = f'''# SDD Orchestration State

O bloco YAML é a fonte de verdade. O Execution Log é somente histórico.

```yaml
schema_version: 2
workspace:
  path: {q(str(target.root))}
  git_common_dir: {q(str(target.common_dir))}
repository:
  name: {q(target.root.name)}
  branch: {q(target.branch)}
  head: {q(target.head)}
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
  run:
    id: null
    started_at: null
    finished_at: null
  budgets:
    stage_transitions: {{max: 3, used: 0}}
    executor_calls: {{max: 8, used: 0}}
    corrective_retries: {{max_per_action: 1, used_current_action: 0}}
    tdd_slices: {{max: 3, used: 0}}
    investigation_expansions: {{max_per_stage: 1, used_current_stage: 0}}
    review_cycles: {{max: 2, used: 0}}
    ci_runs: {{max: 1, used: 0}}
    external_mutations: {{max: 0, used: 0}}
  control:
    stop_reason: NONE
    human_approval_required: false
    requested_approval: null
    loop_active: false
  progress:
    start_stage: null
    current_action: null
    current_executor: null
    current_slice: null
    current_command: null
    command_timeout_seconds: null
    last_result: null
    next_action: null
  last_run:
    id: null
    result: null
    stop_reason: NONE
    stage_transitions: 0
    executor_calls: 0
    tdd_slices_completed: 0
    review_cycles: 0
    ci_runs: 0
    repository_integrity: null
bounded_run_plan: null # No preview or authorization is inherited by a new worktree.
resume:
  last_gate: null
  next_action: null
  next_command: null
baseline:
  captured: false
  head: null
  branch: null
  protected_preexisting: []
ownership:
  agent_owned: []
  protected_preexisting: []
  generated_or_ignored: []
  out_of_scope: []
environment:
  preflight_completed: false
  executor:
    can_edit: UNKNOWN
    can_run_focused_tests: UNKNOWN
    can_format: UNKNOWN
    can_analyze: UNKNOWN
    can_ci: UNKNOWN
  host:
    can_format: UNKNOWN
    can_analyze: UNKNOWN
    can_ci: UNKNOWN
gates:
  focused_tests: {{status: PENDING}}
  format: {{status: PENDING}}
  analyze: {{status: PENDING}}
  review: {{status: PENDING}}
  ci: {{status: PENDING}}
blockers: []
fallbacks: []
evidence:
  tdd_slices: []
  historical_validation: []
  historical_review: null
  final_ci: null
```

## Execution Log

- STATE initialized locally by bootstrap_worktree.py; no demand has started.
'''
    required = ("id: IDLE", "current: IDLE", "status: WAITING", "mode: MANUAL", "loop_active: false", "captured: false")
    if not all(item in text for item in required):
        raise BootstrapError("STATE_VALIDATION_FAILED", "Generated STATE did not satisfy required initial invariants.")
    return text


def render_incidents() -> str:
    return "# SDD Orchestration Incidents\n\nNo incidents recorded.\n"


def render_action_journal(target: Worktree, schema_path: Path) -> str:
    workspace = {"path": str(target.root), "branch": target.branch, "head": target.head, "git_common_dir": str(target.common_dir)}
    value = action_journal.empty_journal(workspace)
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema).iter_errors(value))
        action_journal.validate_journal(value)
    except (OSError, ValueError, action_journal.JournalError) as exc:
        raise BootstrapError("ACTION_JOURNAL_TEMPLATE_INVALID", "Canonical action journal template could not be validated.", actual=str(exc)) from exc
    if errors:
        raise BootstrapError("ACTION_JOURNAL_TEMPLATE_INVALID", "Canonical action journal template does not match ACTION_JOURNAL_SCHEMA.json.", actual="; ".join(error.message for error in errors))
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"


def create_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise BootstrapError("PREWRITE_CHANGED", "Destination file appeared after preflight.", expected="missing path", actual=str(path))
    with path.open("xb") as file:
        file.write(content)


def update_exclude(target: Worktree) -> None:
    exclude = target.exclude_path
    if exclude.is_symlink():
        raise BootstrapError("SYMLINK_REJECTED", "Git info/exclude is a symlink and cannot be safely changed.", expected="regular file", actual=str(exclude))
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    lines = existing.splitlines()
    # The Obsidian binding is deliberately absent: it is versioned so the
    # connectivity survives a clone. Runtime paths no longer live in the repo.
    additions = [relative for relative in (*CONFIG_FILES, LOCAL_HISTORY_DIRECTORY) if relative not in lines]
    if not additions:
        return
    suffix = ("" if not existing or existing.endswith("\n") else "\n") + "\n".join(additions) + "\n"
    with exclude.open("a", encoding="utf-8") as file:
        file.write(suffix)


def fingerprint(plan: Plan) -> tuple[dict[str, str], dict[str, str | None], str, str]:
    obsidian = plan.obsidian
    current = build_plan(
        str(plan.source.root),
        str(plan.target.root),
        str(obsidian.vault_path) if obsidian else None,
        obsidian.project_container if obsidian else None,
    )
    return current.source_hashes, current.target_hashes, current.source.head, current.target.head


def apply(plan: Plan) -> list[str]:
    before = (plan.source_hashes, plan.target_hashes, plan.source.head, plan.target.head)
    if fingerprint(plan) != before:
        raise BootstrapError("PREWRITE_CHANGED", "Git identity or configuration hashes changed after preflight.", expected="preflight snapshot", actual="changed before write")
    if plan.status == "ALREADY_INITIALIZED":
        return []
    if plan.obsidian is None:
        raise BootstrapError("OBSIDIAN_BINDING_REQUIRED", "Apply requires a resolved Obsidian binding.")
    journal_content = (
        render_action_journal(plan.target, plan.source.root / ".hermes/orchestration/schemas/ACTION_JOURNAL_SCHEMA.json").encode("utf-8")
        if "ACTION_JOURNAL.json" in plan.runtime_creates
        else None
    )
    created: list[str] = []
    try:
        for relative in CONFIG_FILES:
            if relative in plan.creates:
                destination = plan.target.root / relative
                create_file(destination, (plan.source.root / relative).read_bytes())
                if sha256(destination) != plan.source_hashes[relative]:
                    raise BootstrapError("COPY_VERIFICATION_FAILED", "Copied configuration hash did not match source.", expected=plan.source_hashes[relative], actual=sha256(destination))
                created.append(relative)
        if OBSIDIAN_BINDING_FILE in plan.creates:
            create_file(plan.target.root / OBSIDIAN_BINDING_FILE, render_binding(plan.obsidian).encode("utf-8"))
            created.append(OBSIDIAN_BINDING_FILE)
        runtime = plan.obsidian.runtime_dir
        if "STATE.md" in plan.runtime_creates:
            create_file(runtime / "STATE.md", render_state(plan.target).encode("utf-8"))
            created.append(str(runtime / "STATE.md"))
        if "INCIDENTS.md" in plan.runtime_creates:
            create_file(runtime / "INCIDENTS.md", render_incidents().encode("utf-8"))
            created.append(str(runtime / "INCIDENTS.md"))
        if "ACTION_JOURNAL.json" in plan.runtime_creates:
            if journal_content is None:
                raise BootstrapError("ACTION_JOURNAL_TEMPLATE_INVALID", "Fresh action journal content was not prepared before writes.")
            create_file(runtime / "ACTION_JOURNAL.json", journal_content)
            created.append(str(runtime / "ACTION_JOURNAL.json"))
        update_exclude(plan.target)
        return created
    except BootstrapError as exc:
        if created:
            raise BootstrapError(
                "PARTIAL",
                "Bootstrap stopped after creating local files; no rollback was performed.",
                expected="all approved writes to complete",
                actual=json.dumps({"created": created, "cause": exc.status}),
            ) from exc
        raise
    except OSError as exc:
        raise BootstrapError(
            "PARTIAL",
            "Operational failure after some local files were created; no rollback was performed.",
            actual=json.dumps({"created": created, "cause": str(exc)}),
        ) from exc


def report(plan: Plan, *, created: list[str] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": plan.status,
        "source": {"path": str(plan.source.root), "branch": plan.source.branch, "head": plan.source.head},
        "target": {"path": str(plan.target.root), "branch": plan.target.branch, "head": plan.target.head, "git_common_dir": str(plan.target.common_dir)},
        "creates": list(plan.creates),
        "created": created or [],
        "dry_run": created is None,
        "warnings": list(plan.warnings),
    }
    if plan.obsidian is not None:
        payload["obsidian"] = {
            "vault_path": str(plan.obsidian.vault_path),
            "project_container": plan.obsidian.project_container,
            "runtime_dir": str(plan.obsidian.runtime_dir),
            "runtime_creates": list(plan.runtime_creates),
        }
    return payload


def emit(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"{payload['status']}: {payload.get('message', 'preflight completed')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare an existing worktree with local SDD V2.5 configuration.")
    parser.add_argument("--source", required=True, help="Canonical existing Git worktree root.")
    parser.add_argument("--target", required=True, help="Existing registered Git worktree root to prepare.")
    parser.add_argument("--apply", action="store_true", help="Apply the preflighted local changes. Without it, only simulate.")
    parser.add_argument("--json", action="store_true", help="Emit a structured JSON report.")
    parser.add_argument("--obsidian-vault", help="Absolute path to the Obsidian vault holding this project's second brain.")
    parser.add_argument("--obsidian-project", help="Project container path relative to the vault root.")
    args = parser.parse_args(argv)
    try:
        plan = build_plan(args.source, args.target, args.obsidian_vault, args.obsidian_project)
        created = apply(plan) if args.apply else None
        emit(report(plan, created=created), args.json)
        return 0
    except BootstrapError as exc:
        emit({"status": exc.status, "message": exc.message, "expected": exc.expected, "actual": exc.actual}, args.json)
        return 2
    except subprocess.TimeoutExpired as exc:
        emit({"status": "TIMEOUT", "message": "A Git preflight command exceeded its timeout.", "actual": str(exc)}, args.json)
        return 2


if __name__ == "__main__":
    sys.exit(main())
