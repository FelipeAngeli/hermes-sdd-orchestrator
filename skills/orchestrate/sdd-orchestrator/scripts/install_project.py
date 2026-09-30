#!/usr/bin/env python3
"""Install the SDD Orchestrator as untracked, project-local configuration."""
from __future__ import annotations

import argparse
import errno
import hashlib
import importlib.util
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT.parent / "templates"
CONFIG_ROOT = ".hermes/orchestration"
TYPESAFE_SKILL_ROOT = ".hermes/skills/typesafe-ai"
TYPESAFE_SKILL_PATH = f"{TYPESAFE_SKILL_ROOT}/SKILL.md"
TYPESAFE_LOCK_PATH = "skills-lock.json"
TYPESAFE_ENV_PATH = ".hermes/.env"
TYPESAFE_ENV_CONTENT = b"TYPESAFE_API_KEY=\n"
TYPESAFE_SOURCE_REF = "65a39f393687675ce170e6094757de20370365b9"
TYPESAFE_UPSTREAM_HASH = "9cd84c5e535dec8dec59917c110f9c00b4a61faadb86b432ec7e41051170af12"
TYPESAFE_TRUSTED_DIGEST = "5266f2a9acfb6ae5fd58717bdf366f38e224cb57a55824e2aa81a0922d5e6964"
TYPESAFE_VENDOR = ROOT.parent / "vendor" / "typesafe-ai"
TYPESAFE_OFFICIAL_COMMAND = (
    "npx", "skills", "add", "typesafe-ai/skills", "--skill", "typesafe-ai",
)
PROJECT_SKILLS = (
    ".hermes/skills/sdd-backend-engineering",
    ".hermes/skills/sdd-architecture-decisions",
    ".hermes/skills/sdd-database-design-migrations",
)
LOCAL_PATHS = (
    ".hermes.md",
    *PROJECT_SKILLS,
    ".hermes/.env",
    ".hermes/.env.example",
    CONFIG_ROOT,
    f"{CONFIG_ROOT}/STATE.md",
    f"{CONFIG_ROOT}/PROJECT_SETUP.md",
    f"{CONFIG_ROOT}/INCIDENTS.md",
    f"{CONFIG_ROOT}/ACTION_JOURNAL.json",
    f"{CONFIG_ROOT}/action-journal-history/",
)


class InstallError(RuntimeError):
    pass


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _load_unique_json(value: str) -> object:
    return json.loads(value, object_pairs_hook=_unique_json_object)


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
    worktree = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "--is-inside-work-tree"],
        text=True, capture_output=True, timeout=20, check=False,
    )
    if worktree.returncode or worktree.stdout.strip() != "true":
        raise InstallError("GIT_REPOSITORY_REQUIRED")
    root = Path(git(target, "rev-parse", "--show-toplevel")).resolve()
    if root != target:
        raise InstallError(f"TARGET_NOT_REPOSITORY_ROOT: use {root}")
    branch = git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
        text=True, capture_output=True, timeout=20, check=False,
    )
    if head.returncode:
        raise InstallError("GIT_INITIAL_COMMIT_REQUIRED")
    return root, {
        "path": str(root),
        "branch": branch,
        "head": head.stdout.strip(),
        "git_common_dir": git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"),
    }


def is_tracked(target: Path, relative: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(target), "ls-files", "--error-unmatch", "--", relative],
        text=True, capture_output=True, timeout=20, check=False,
    )
    return result.returncode == 0


def tracked_under(target: Path, relative: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(target), "ls-files", "--", relative],
        text=True, capture_output=True, timeout=20, check=False,
    )
    if result.returncode:
        raise InstallError((result.stderr or result.stdout).strip() or "Git preflight failed.")
    return bool(result.stdout.strip())


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


def _typesafe_trusted_digest(skill_root: Path) -> str:
    files: list[tuple[str, bytes]] = []
    for path in sorted(skill_root.rglob("*")):
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode):
            raise InstallError(f"TYPESAFE_SKILL_OBJECT_INVALID: {path.relative_to(skill_root)}")
        relative = path.relative_to(skill_root).as_posix()
        files.append((relative, path.read_bytes()))
    if [relative for relative, _ in files] != ["LICENSE", "SKILL.md"]:
        raise InstallError("TYPESAFE_SKILL_FILESET_INVALID")
    digest = hashlib.sha256()
    for relative, content in files:
        encoded = relative.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _valid_typesafe_lock_entry(entry: object) -> bool:
    return (
        isinstance(entry, dict)
        and set(entry) == {"source", "ref", "sourceType", "skillPath", "computedHash"}
        and entry.get("source") == "typesafe-ai/skills"
        and entry.get("ref") == TYPESAFE_SOURCE_REF
        and entry.get("sourceType") == "github"
        and entry.get("skillPath") == "skills/typesafe-ai/SKILL.md"
        and entry.get("computedHash") == TYPESAFE_UPSTREAM_HASH
    )


def _open_project_directory_nofollow(target: Path, relative: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(target, flags)
    try:
        for part in relative.parts:
            if part in {"", ".", ".."}:
                raise InstallError(f"TYPESAFE_ENV_CONFLICT: INVALID_PATH")
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            previous_descriptor = descriptor
            try:
                os.close(previous_descriptor)
            except OSError:
                try:
                    os.close(next_descriptor)
                except OSError:
                    pass
                descriptor = -1
                raise
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise


def _open_typesafe_env_descriptor(target: Path, flags: int, mode: int = 0o600) -> int:
    path = Path(TYPESAFE_ENV_PATH)
    if os.name != "posix":
        reject_symlinks(target, TYPESAFE_ENV_PATH)
        return os.open(target / path, flags, mode)
    parent = _open_project_directory_nofollow(target, path.parent)
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path.name,
            flags | os.O_NOFOLLOW | os.O_NONBLOCK,
            mode,
            dir_fd=parent,
        )
        try:
            os.close(parent)
        except OSError:
            try:
                os.close(descriptor)
            except OSError:
                pass
            descriptor = None
            parent = -1
            raise
        parent = -1
        return descriptor
    finally:
        if parent >= 0:
            try:
                os.close(parent)
            except OSError:
                pass


def _close_descriptor(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass


def typesafe_env_status(target: Path) -> dict[str, object]:
    issue: str | None = None
    status = "CONFLICT"
    if issue is None and is_tracked(target, TYPESAFE_ENV_PATH):
        issue = "TRACKED_DESTINATION_PATH"
    if issue is not None:
        status = "CONFLICT"
    else:
        descriptor: int | None = None
        try:
            descriptor = _open_typesafe_env_descriptor(target, os.O_RDONLY)
        except FileNotFoundError:
            status = "ABSENT"
        except (InstallError, OSError) as error:
            status = "CONFLICT"
            issue = "SYMLINK_REJECTED" if isinstance(error, OSError) and error.errno in {errno.ELOOP, errno.ENOTDIR} else "ENV_INVALID"
        else:
            try:
                try:
                    metadata = os.fstat(descriptor)
                except OSError:
                    status = "CONFLICT"
                    issue = "ENV_INVALID"
                    metadata = None
                if metadata is None:
                    pass
                elif not stat.S_ISREG(metadata.st_mode):
                    status = "CONFLICT"
                    issue = "ENV_INVALID"
                elif os.name == "posix" and (
                    stat.S_IMODE(metadata.st_mode) & 0o077
                    or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
                ):
                    status = "CONFLICT"
                    issue = "INSECURE_PERMISSIONS"
                else:
                    status = "PRESENT"
            finally:
                _close_descriptor(descriptor)
    return {"env_path": TYPESAFE_ENV_PATH, "env_status": status, "env_issue": issue}


def typesafe_skill_status(target: Path) -> dict[str, object]:
    """Detect and verify a project-local TypeSafe skill without mutation."""
    skill_root = target / TYPESAFE_SKILL_ROOT
    skill_path = target / TYPESAFE_SKILL_PATH
    lock_path = target / TYPESAFE_LOCK_PATH
    lock_entry: object | None = None
    issue: str | None = None

    try:
        reject_symlinks(target, TYPESAFE_SKILL_PATH)
        reject_symlinks(target, TYPESAFE_LOCK_PATH)
    except InstallError:
        issue = "SYMLINK_REJECTED"

    if issue is None and lock_path.exists():
        if not lock_path.is_file():
            issue = "LOCK_INVALID"
        else:
            try:
                lock = _load_unique_json(lock_path.read_text(encoding="utf-8"))
                if (
                    not isinstance(lock, dict)
                    or type(lock.get("version")) is not int
                    or lock["version"] != 1
                    or not isinstance(lock.get("skills"), dict)
                ):
                    issue = "LOCK_INVALID"
                else:
                    lock_entry = lock["skills"].get("typesafe-ai")
                    if lock_entry is not None and not _valid_typesafe_lock_entry(lock_entry):
                        issue = "LOCK_ENTRY_INVALID"
            except (UnicodeError, ValueError, OSError):
                issue = "LOCK_INVALID"

    skill_valid = False
    if issue is None and skill_path.is_file():
        try:
            content = skill_path.read_text(encoding="utf-8")
            if content.startswith("---\n"):
                header = content.split("---\n", 2)[1]
                skill_valid = any(line.strip() == "name: typesafe-ai" for line in header.splitlines())
        except (IndexError, UnicodeError, OSError):
            skill_valid = False

    if issue is None and skill_valid and isinstance(lock_entry, dict):
        try:
            if _typesafe_trusted_digest(skill_root) != TYPESAFE_TRUSTED_DIGEST:
                issue = "TRUSTED_DIGEST_MISMATCH"
        except InstallError as error:
            issue = str(error).split(":", 1)[0]
        except OSError:
            issue = "TRUSTED_DIGEST_MISMATCH"

    if issue is not None:
        status = "CONFLICT"
    elif skill_valid and lock_entry is not None:
        status = "INSTALLED"
    elif skill_root.exists() or lock_entry is not None or skill_path.exists():
        status = "CONFLICT"
        issue = "LOCK_ENTRY_MISSING" if skill_valid else "SKILL_INVALID"
    else:
        status = "NOT_INSTALLED"

    if status == "INSTALLED" and (
        tracked_under(target, TYPESAFE_SKILL_ROOT) or is_tracked(target, TYPESAFE_LOCK_PATH)
    ):
        status = "CONFLICT"
        issue = "TRACKED_DESTINATION_PATH"

    return {
        "status": status,
        "skill_path": TYPESAFE_SKILL_PATH,
        "lock_path": TYPESAFE_LOCK_PATH,
        "lock_entry": "PRESENT" if lock_entry is not None else "ABSENT",
        "issue": issue,
        "official_command": list(TYPESAFE_OFFICIAL_COMMAND),
        **typesafe_env_status(target),
    }


def _answer_state(question_id: str, value: str) -> tuple[bool, bool]:
    normalized = value.strip()
    if normalized.casefold() in {"", "unresolved", "null", "~", "{}", "[]"}:
        return True, True
    if normalized.casefold() == "none":
        return False, True
    try:
        decoded = _load_unique_json(normalized)
    except (ValueError, TypeError):
        return True, False
    if isinstance(decoded, str):
        marker = decoded.strip().casefold()
        if marker in {"unresolved", "null", "~", "{}", "[]"}:
            return True, True
        return (False, True) if marker == "none" else (True, False)

    def nonempty(item: object) -> bool:
        return isinstance(item, str) and bool(item.strip())

    if question_id == "issue_tracker":
        valid = (
            isinstance(decoded, dict)
            and set(decoded) == {"provider", "project", "read", "write"}
            and nonempty(decoded["provider"])
            and nonempty(decoded["project"])
            and isinstance(decoded["read"], bool)
            and isinstance(decoded["write"], bool)
        )
    elif question_id == "obsidian":
        valid = (
            isinstance(decoded, dict)
            and set(decoded) == {"vault", "project_container"}
            and nonempty(decoded["vault"])
            and Path(decoded["vault"]).is_absolute()
            and nonempty(decoded["project_container"])
            and not Path(decoded["project_container"]).is_absolute()
            and ".." not in Path(decoded["project_container"]).parts
        )
    elif question_id == "typesafe_ai":
        valid = (
            isinstance(decoded, dict)
            and set(decoded) == {"install"}
            and decoded["install"] is True
        )
    elif question_id == "project_tools":
        valid = isinstance(decoded, list) and bool(decoded) and all(
            isinstance(tool, dict)
            and set(tool) == {"tool", "purpose", "read", "write"}
            and nonempty(tool["tool"])
            and nonempty(tool["purpose"])
            and isinstance(tool["read"], bool)
            and isinstance(tool["write"], bool)
            for tool in decoded
        )
    else:
        valid = False
    return (False, True) if valid else (True, False)


def _unresolved_answer(question_id: str, value: str) -> bool:
    unresolved, valid = _answer_state(question_id, value)
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
    for key, value in answers.items():
        _, valid = _answer_state(key, value)
        if not valid:
            issues.append("ANSWER_VALUE_INVALID")
    all_resolved = not missing and all(not _unresolved_answer(key, answers[key]) for key in question_ids)
    expected_status = "COMPLETE" if all_resolved else "PENDING"
    if metadata.get("status") in {"PENDING", "COMPLETE"} and metadata["status"] != expected_status:
        issues.append("STATUS_ANSWER_MISMATCH")

    non_structural = {"ANSWERS_MISSING", "ANSWER_VALUE_INVALID", "STATUS_ANSWER_MISMATCH"}
    structural_issues = set(issues) - non_structural
    if structural_issues:
        answers = {}
    return answers, not issues, metadata.get("status"), sorted(set(issues))


def onboarding_questions(target: Path | None = None, typesafe_choice: str | None = None) -> dict[str, object]:
    """Return only unresolved project-local questions after installation."""
    questions = [
        {
            "id": "issue_tracker",
            "prompt": "Which issue tracker should the orchestrator read, and may it create or update issues?",
            "accepted_answers": ["JSON object with provider, project, read, and write", "none"],
        },
        {
            "id": "obsidian",
            "prompt": "Should the orchestrator connect this project to an Obsidian vault?",
            "accepted_answers": ["JSON object with absolute vault and relative project_container", "none"],
        },
        {
            "id": "typesafe_ai",
            "prompt": "Should the orchestrator install the project-local TypeSafe skill for Hermes (including guidance for Jev)?",
            "accepted_answers": ["JSON object with install set to true", "none"],
        },
        {
            "id": "project_tools",
            "prompt": "Which other project-specific tools must the orchestrator use, and with what permissions?",
            "accepted_answers": ["JSON array of tool, purpose, read, and write objects", "none"],
        },
    ]
    all_questions = list(questions)
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
            if _unresolved_answer(str(question["id"]), answers.get(str(question["id"]), "UNRESOLVED"))
        ]
    typesafe = typesafe_skill_status(target) if target else {
        "status": "NOT_CHECKED",
        "skill_path": TYPESAFE_SKILL_PATH,
        "lock_path": TYPESAFE_LOCK_PATH,
        "lock_entry": "UNKNOWN",
        "issue": None,
        "official_command": list(TYPESAFE_OFFICIAL_COMMAND),
        "env_path": TYPESAFE_ENV_PATH,
        "env_status": "NOT_CHECKED",
        "env_issue": None,
    }
    recorded_typesafe = answers.get("typesafe_ai", "")
    try:
        recorded_value = _load_unique_json(recorded_typesafe)
    except (ValueError, TypeError):
        recorded_value = None
    recorded_install = recorded_value == {"install": True}
    recorded_none = recorded_typesafe.strip().casefold() == "none"
    integration_issues: list[str] = []
    if recorded_install:
        typesafe_healthy = typesafe["status"] == "INSTALLED" and typesafe["env_status"] == "PRESENT"
    elif recorded_none:
        typesafe_healthy = typesafe["status"] == "NOT_INSTALLED"
    else:
        typesafe_healthy = True
    if not typesafe_healthy:
        integration_issues.append("TYPESAFE_INTEGRATION_STATE_MISMATCH")
        unresolved = {str(question["id"]) for question in questions}
        unresolved.add("typesafe_ai")
        questions = [question for question in all_questions if str(question["id"]) in unresolved]
    complete = record_valid and record_status == "COMPLETE" and not questions and not integration_issues
    if typesafe_choice == "install" and typesafe["env_status"] == "CONFLICT":
        typesafe["planned_action"] = "BLOCKED"
    elif typesafe_choice == "install":
        if typesafe["status"] == "INSTALLED":
            typesafe["planned_action"] = (
                "NONE"
                if recorded_install and typesafe["env_status"] == "PRESENT"
                else "RECORD"
            )
        else:
            typesafe["planned_action"] = "INSTALL"
    elif typesafe_choice == "none":
        if typesafe["status"] == "NOT_INSTALLED":
            typesafe["planned_action"] = "NONE" if recorded_none else "RECORD_NONE"
        else:
            typesafe["planned_action"] = "BLOCKED"
    else:
        typesafe["planned_action"] = "NONE"
    typesafe["planned_env_action"] = (
        "CREATE"
        if typesafe_choice == "install" and typesafe["env_status"] == "ABSENT"
        else "NONE"
    )
    return {
        "status": "COMPLETE" if complete else "REQUIRED",
        "scope": "ORCHESTRATOR_ONLY",
        "ask_only_unresolved": True,
        "record_valid": record_valid,
        "record_status": record_status,
        "record_issues": record_issues,
        "integration_issues": integration_issues,
        "questions": questions,
        "integrations": {"typesafe_ai": typesafe},
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
  typesafe_ai: UNRESOLVED
  project_tools: UNRESOLVED
```

## Onboarding rules

- Ask only about orchestrator connectivity, never product requirements or implementation preferences.
- Inspect repository evidence first and ask only questions whose answers remain unresolved.
- Accept `none` as an explicit answer for every integration. Otherwise use compact JSON on the same line: issue tracker requires `provider`, `project`, `read`, and `write`; Obsidian requires an absolute `vault` and relative `project_container`; TypeSafe requires `{"install":true}`; project tools require a non-empty array of objects with `tool`, `purpose`, `read`, and `write`.
- For an issue tracker, record the provider, project identifier, and separate read/write permission; verify connectivity read-only before any mutation.
- For Obsidian, record whether it is enabled and, only when enabled, the vault and project container required by `BOOTSTRAP.md`. When the official CLI is available, discover candidates read-only with `python3 .hermes/orchestration/runtime/obsidian_connector.py discover --json`; after `.hermes/obsidian.json` exists, verify the bound container and selected read transport with `python3 .hermes/orchestration/runtime/obsidian_connector.py preflight --repo . --json`. A filesystem fallback is valid; neither command writes a note.
- Jev is a TypeSafe model, not the installed product. Use `--typesafe-ai install --apply` to copy the vetted project-local TypeSafe skill snapshot or `--typesafe-ai none --apply` to opt out only when no TypeSafe installation is discoverable; the official `npx` command is informational and is never executed by this installer.
- For other project tools, record each tool's purpose and separate read/write permission.
- Never request passwords, tokens, verification codes, or other secrets in chat. Use Hermes credential facilities when authentication is required.
- Set `status: COMPLETE` only after all four answers are resolved, including explicit `none` answers.
'''


def _require_typesafe_record_target(target: Path) -> None:
    question_ids = {"issue_tracker", "obsidian", "typesafe_ai", "project_tools"}
    answers, valid, _, issues = _read_onboarding_record(target, question_ids)
    if valid:
        return
    legacy_keys = question_ids - {"typesafe_ai"}
    legacy_issues = {"ANSWERS_MISSING", "STATUS_ANSWER_MISMATCH"}
    if set(answers) == legacy_keys and set(issues).issubset(legacy_issues):
        return
    reason = issues[0] if issues else "UNKNOWN"
    raise InstallError(f"ONBOARDING_RECORD_INVALID: {reason}")


def _render_onboarding_answer(target: Path, question_id: str, value: str) -> bytes:
    path = target / CONFIG_ROOT / "PROJECT_SETUP.md"
    reject_symlinks(target, f"{CONFIG_ROOT}/PROJECT_SETUP.md")
    lines = path.read_text(encoding="utf-8").splitlines()
    prefix = f"  {question_id}:"
    matches = [index for index, line in enumerate(lines) if line.startswith(prefix)]
    if len(matches) > 1:
        raise InstallError(f"ONBOARDING_RECORD_INVALID: {question_id}")
    if matches:
        lines[matches[0]] = f"  {question_id}: {value}"
    elif question_id == "typesafe_ai":
        insertion_points = [index for index, line in enumerate(lines) if line.startswith("  project_tools:")]
        if len(insertion_points) != 1:
            raise InstallError(f"ONBOARDING_RECORD_INVALID: {question_id}")
        lines.insert(insertion_points[0], f"  {question_id}: {value}")
    else:
        raise InstallError(f"ONBOARDING_RECORD_INVALID: {question_id}")

    question_ids = {"issue_tracker", "obsidian", "typesafe_ai", "project_tools"}
    answers: dict[str, str] = {}
    for line in lines:
        if line.startswith("  ") and not line.startswith("    ") and ":" in line:
            key, answer = line.strip().split(":", 1)
            if key in question_ids:
                answers[key] = answer.strip()
    resolved = set(answers) == question_ids and all(
        not _unresolved_answer(key, answers[key]) for key in question_ids
    )
    status_lines = [index for index, line in enumerate(lines) if line.startswith("status:")]
    if len(status_lines) != 1:
        raise InstallError("ONBOARDING_RECORD_INVALID: status")
    lines[status_lines[0]] = f"status: {'COMPLETE' if resolved else 'PENDING'}"
    return ("\n".join(lines) + "\n").encode("utf-8")


def _atomic_write(path: Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()
        raise


def _write_onboarding_answer(target: Path, question_id: str, value: str) -> None:
    path = target / CONFIG_ROOT / "PROJECT_SETUP.md"
    _atomic_write(path, _render_onboarding_answer(target, question_id, value))


def _restore_typesafe_install(
    target: Path,
    lock_before: bytes | None,
    setup_before: bytes,
) -> None:
    skill_root = target / TYPESAFE_SKILL_ROOT
    try:
        reject_symlinks(target, ".hermes/skills")
        if skill_root.is_symlink() or skill_root.is_file():
            skill_root.unlink()
        elif skill_root.exists():
            shutil.rmtree(skill_root)
        lock_path = target / TYPESAFE_LOCK_PATH
        if lock_path.exists() and lock_path.is_dir() and not lock_path.is_symlink():
            raise InstallError("lock path became a directory")
        if lock_before is None:
            if lock_path.exists() or lock_path.is_symlink():
                lock_path.unlink()
        else:
            _atomic_write(lock_path, lock_before)
        setup_path = target / CONFIG_ROOT / "PROJECT_SETUP.md"
        if setup_path.read_bytes() != setup_before:
            _atomic_write(setup_path, setup_before)
    except (InstallError, OSError) as error:
        raise InstallError(f"TYPESAFE_ROLLBACK_FAILED: {error}") from error


def _preflight_typesafe_install(target: Path) -> dict[str, object]:
    environment = typesafe_env_status(target)
    if environment["env_status"] == "CONFLICT":
        raise InstallError(f"TYPESAFE_ENV_CONFLICT: {environment['env_issue']}")
    current = typesafe_skill_status(target)
    if current["status"] == "INSTALLED":
        return current
    if current["status"] != "NOT_INSTALLED" or current["issue"] is not None:
        raise InstallError(f"TYPESAFE_SKILL_CONFLICT: {current['issue'] or current['status']}")
    if tracked_under(target, TYPESAFE_SKILL_ROOT):
        raise InstallError(f"TRACKED_DESTINATION_PATH: {TYPESAFE_SKILL_ROOT}")
    if is_tracked(target, TYPESAFE_LOCK_PATH):
        raise InstallError(f"TRACKED_DESTINATION_PATH: {TYPESAFE_LOCK_PATH}")
    reject_symlinks(target, TYPESAFE_SKILL_PATH)
    reject_symlinks(target, TYPESAFE_LOCK_PATH)
    try:
        if _typesafe_trusted_digest(TYPESAFE_VENDOR) != TYPESAFE_TRUSTED_DIGEST:
            raise InstallError("TYPESAFE_VENDOR_DIGEST_MISMATCH")
    except OSError as error:
        raise InstallError(f"TYPESAFE_VENDOR_INVALID: {error}") from error
    return current


def _merged_typesafe_lock(lock_before: bytes | None, entry: dict[str, object]) -> bytes:
    if lock_before is None:
        lock: dict[str, object] = {"version": 1, "skills": {}}
    else:
        decoded = _load_unique_json(lock_before.decode("utf-8"))
        if not isinstance(decoded, dict) or not isinstance(decoded.get("skills"), dict):
            raise InstallError("TYPESAFE_LOCK_INVALID")
        lock = decoded
    skills = dict(lock["skills"])
    skills["typesafe-ai"] = entry
    lock["skills"] = {name: skills[name] for name in sorted(skills)}
    return (json.dumps(lock, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _ensure_typesafe_env(target: Path) -> bool:
    """Create a private empty credential file without touching an existing one."""
    environment = typesafe_env_status(target)
    if environment["env_status"] == "PRESENT":
        return False
    if environment["env_status"] == "CONFLICT":
        raise InstallError(f"TYPESAFE_ENV_CONFLICT: {environment['env_issue']}")
    path = Path(TYPESAFE_ENV_PATH)
    temporary_name = f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    parent: int | None = None
    descriptor: int | None = None
    try:
        if os.name == "posix":
            parent = _open_project_directory_nofollow(target, path.parent)
            descriptor = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
        else:
            reject_symlinks(target, str(path.parent))
            descriptor = os.open(target / path.parent / temporary_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        stream = os.fdopen(descriptor, "wb")
        descriptor = None
        with stream:
            stream.write(TYPESAFE_ENV_CONTENT)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "posix":
            os.link(
                temporary_name,
                path.name,
                src_dir_fd=parent,
                dst_dir_fd=parent,
                follow_symlinks=False,
            )
        else:
            os.link(target / path.parent / temporary_name, target / path)
    except FileExistsError as error:
        raise InstallError(f"TYPESAFE_ENV_CONFLICT: DESTINATION_EXISTS") from error
    finally:
        if descriptor is not None:
            _close_descriptor(descriptor)
        try:
            if os.name == "posix" and parent is not None:
                os.unlink(temporary_name, dir_fd=parent)
            elif os.name != "posix":
                (target / path.parent / temporary_name).unlink(missing_ok=True)
        except OSError:
            pass
        finally:
            if parent is not None:
                _close_descriptor(parent)
    return True


def install_typesafe_skill(target: Path, onboarding_after: bytes) -> str:
    """Commit the vetted bundled skill, merged lock and onboarding answer."""
    current = _preflight_typesafe_install(target)
    if current["status"] == "INSTALLED":
        setup_path = target / CONFIG_ROOT / "PROJECT_SETUP.md"
        setup_before = setup_path.read_bytes()
        try:
            _atomic_write(setup_path, onboarding_after)
            _ensure_typesafe_env(target)
        except BaseException:
            if setup_path.read_bytes() != setup_before:
                _atomic_write(setup_path, setup_before)
            raise
        return "RECORD"

    lock_path = target / TYPESAFE_LOCK_PATH
    setup_path = target / CONFIG_ROOT / "PROJECT_SETUP.md"
    lock_before = lock_path.read_bytes() if lock_path.exists() else None
    setup_before = setup_path.read_bytes()
    entry: dict[str, object] = {
        "source": "typesafe-ai/skills",
        "ref": TYPESAFE_SOURCE_REF,
        "sourceType": "github",
        "skillPath": "skills/typesafe-ai/SKILL.md",
        "computedHash": TYPESAFE_UPSTREAM_HASH,
    }
    merged_lock = _merged_typesafe_lock(lock_before, entry)

    reject_symlinks(target, ".hermes/skills")
    skill_root = target / TYPESAFE_SKILL_ROOT
    try:
        skill_root.mkdir()
    except FileExistsError as error:
        raise InstallError(f"TYPESAFE_SKILL_CONFLICT: {TYPESAFE_SKILL_ROOT}") from error

    try:
        for source in sorted(TYPESAFE_VENDOR.iterdir()):
            shutil.copy2(source, skill_root / source.name)
        _atomic_write(lock_path, merged_lock)
        verified = typesafe_skill_status(target)
        if verified["status"] != "INSTALLED":
            raise InstallError(f"TYPESAFE_INSTALL_FAILED: target verification: {verified['issue']}")
        if lock_before is not None:
            before = _load_unique_json(lock_before.decode("utf-8"))
            after = _load_unique_json(lock_path.read_text(encoding="utf-8"))
            if not isinstance(before, dict) or not isinstance(after, dict):
                raise InstallError("TYPESAFE_LOCK_PRESERVATION_FAILED")
            before_skills = dict(before["skills"])
            after_skills = dict(after["skills"])
            before_skills.pop("typesafe-ai", None)
            after_skills.pop("typesafe-ai", None)
            before["skills"] = before_skills
            after["skills"] = after_skills
            if before != after:
                raise InstallError("TYPESAFE_LOCK_PRESERVATION_FAILED")
        _atomic_write(setup_path, onboarding_after)
        _ensure_typesafe_env(target)
    except BaseException:
        _restore_typesafe_install(target, lock_before, setup_before)
        raise
    return "INSTALL"


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
        path.write_text(
            existing + ("" if not existing or existing.endswith("\n") else "\n") + "\n".join(additions) + "\n",
            encoding="utf-8",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default=".", help="existing Git worktree root (default: current directory)")
    parser.add_argument("--apply", action="store_true", help="write files after a successful dry run")
    parser.add_argument(
        "--typesafe-ai",
        choices=("install", "none"),
        help="explicitly install the project-local TypeSafe skill or record that it is not used",
    )
    parser.add_argument("--json", action="store_true", help="emit a machine-readable report")
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        report = {
            "status": "BLOCKED",
            "reason": "PYTHON_3_10_REQUIRED",
            "next_step": "Install or select Python 3.10 or newer, then rerun the installer.",
        }
        print(
            json.dumps(report, ensure_ascii=False) if args.json else "BLOCKED: PYTHON_3_10_REQUIRED",
            file=sys.stderr,
        )
        return 2
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
        if args.typesafe_ai and existing_state:
            _require_typesafe_record_target(target)
        onboarding = onboarding_questions(target, args.typesafe_ai)
        onboarding_integrations = onboarding["integrations"]
        if not isinstance(onboarding_integrations, dict) or not isinstance(
            onboarding_integrations.get("typesafe_ai"), dict
        ):
            raise InstallError("TYPESAFE_REPORT_INVALID")
        typesafe_action = onboarding_integrations["typesafe_ai"].get("planned_action")
        if typesafe_action == "BLOCKED":
            integration = onboarding_integrations["typesafe_ai"]
            if integration.get("env_status") == "CONFLICT":
                raise InstallError(f"TYPESAFE_ENV_CONFLICT: {integration.get('env_issue')}")
            raise InstallError(f"TYPESAFE_SKILL_CONFLICT: {integration.get('issue') or integration.get('status')}")
        if typesafe_action == "INSTALL":
            _preflight_typesafe_install(target)
        integration_planned = typesafe_action != "NONE"
        report = {
            "status": "READY" if planned or integration_planned else "ALREADY_INITIALIZED",
            "target": str(target),
            "planned": planned,
            "applied": False,
            "stack": detect_stack(target),
            "onboarding": onboarding,
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
        if args.apply and args.typesafe_ai and typesafe_action != "NONE":
            if args.typesafe_ai == "install":
                onboarding_after = _render_onboarding_answer(target, "typesafe_ai", '{"install":true}')
                applied_action = install_typesafe_skill(target, onboarding_after)
            else:
                applied_action = "RECORD_NONE"
                _write_onboarding_answer(target, "typesafe_ai", "none")
            report["applied"] = True
            updated_onboarding = onboarding_questions(target)
            integrations = updated_onboarding["integrations"]
            if not isinstance(integrations, dict) or not isinstance(integrations.get("typesafe_ai"), dict):
                raise InstallError("TYPESAFE_REPORT_INVALID")
            integrations["typesafe_ai"]["applied_action"] = applied_action
            report["onboarding"] = updated_onboarding
        elif report["applied"]:
            report["onboarding"] = onboarding_questions(target)
        if args.json:
            print(json.dumps(report, ensure_ascii=False))
        else:
            ecosystems = ", ".join(f'{item["ecosystem"]}@{item["path"]}' for item in report["stack"]["ecosystems"]) or "none detected"
            print(f'{report["status"]}: {len(planned)} files planned; stack: {ecosystems}')
        return 0
    except (InstallError, OSError, subprocess.TimeoutExpired) as error:
        report = {"status": "BLOCKED", "reason": str(error)}
        if str(error) == "GIT_REPOSITORY_REQUIRED":
            report["next_step"] = "Initialize and commit the target as a Git repository, then rerun the installer."
        elif str(error) == "GIT_INITIAL_COMMIT_REQUIRED":
            report["next_step"] = "Create the initial Git commit on an attached branch, then rerun the installer."
        print(json.dumps(report, ensure_ascii=False) if args.json else f'BLOCKED: {error}', file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
