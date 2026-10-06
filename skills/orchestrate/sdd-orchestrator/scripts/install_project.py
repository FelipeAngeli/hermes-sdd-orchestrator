#!/usr/bin/env python3
"""Install the SDD Orchestrator as untracked, project-local configuration."""
from __future__ import annotations

import argparse
import ctypes
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
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT.parent / "templates"
CONFIG_ROOT = ".hermes/orchestration"
TYPESAFE_SKILL_ROOT = ".hermes/skills/typesafe-ai"
TYPESAFE_SKILL_PATH = f"{TYPESAFE_SKILL_ROOT}/SKILL.md"
TYPESAFE_LOCK_PATH = "skills-lock.json"
TYPESAFE_ENV_PATH = ".hermes/.env"
TYPESAFE_ENV_CONTENT = b"TYPESAFE_API_KEY=\nJEV_AI_API_KEY=\n"
JEV_CACHE_PATH = f"{CONFIG_ROOT}/JEV_CACHE.json"
TERMINAL_PROGRESS_PATH = f"{CONFIG_ROOT}/TERMINAL_PROGRESS.json"
JEV_CACHE_LOCK_PATH = f"{CONFIG_ROOT}/.JEV_CACHE.json.lock"
TERMINAL_PROGRESS_LOCK_PATH = f"{CONFIG_ROOT}/.TERMINAL_PROGRESS.json.lock"
TYPESAFE_ANSWER_DISABLED = '{"install":true,"automatic_semantic_governance":false}'
TYPESAFE_ANSWER_ENABLED = '{"install":true,"automatic_semantic_governance":true}'
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
STATE_PATHS = (
    f"{CONFIG_ROOT}/STATE.md",
    f"{CONFIG_ROOT}/PROJECT_SETUP.md",
    f"{CONFIG_ROOT}/INCIDENTS.md",
    f"{CONFIG_ROOT}/ACTION_JOURNAL.json",
)
#: False in Obsidian storage mode: credentials never live in the vault or the
#: user's repository, so no `.env` placeholder is created and the connector
#: reads keys from the process environment.
CREDENTIAL_FILE_MANAGED = True
#: False in Obsidian storage mode: the vault may be its own Git repository
#: (for example with obsidian-git); being tracked there is not a conflict,
#: because the user's repository is never a destination.
TRACKED_DESTINATIONS_GUARDED = True


def _set_storage_mode(obsidian: bool) -> None:
    """Apply the per-run storage policy in one place."""
    global CREDENTIAL_FILE_MANAGED, TRACKED_DESTINATIONS_GUARDED
    CREDENTIAL_FILE_MANAGED = not obsidian
    TRACKED_DESTINATIONS_GUARDED = not obsidian


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
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
        text=True, capture_output=True, timeout=20, check=False,
    )
    if head.returncode:
        raise InstallError("GIT_INITIAL_COMMIT_REQUIRED")
    branch_result = subprocess.run(
        ["git", "-C", str(root), "symbolic-ref", "--quiet", "--short", "HEAD"],
        text=True, capture_output=True, timeout=20, check=False,
    )
    if branch_result.returncode:
        raise InstallError("ATTACHED_BRANCH_REQUIRED")
    return root, {
        "path": str(root),
        "branch": branch_result.stdout.strip(),
        "head": head.stdout.strip(),
        "git_dir": git(root, "rev-parse", "--path-format=absolute", "--git-dir"),
        "git_common_dir": git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"),
    }


def _inside_work_tree(target: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "--is-inside-work-tree"],
        text=True, capture_output=True, timeout=20, check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def is_tracked(target: Path, relative: str) -> bool:
    if not TRACKED_DESTINATIONS_GUARDED:
        return False
    result = subprocess.run(
        ["git", "-C", str(target), "ls-files", "--error-unmatch", "--", relative],
        text=True, capture_output=True, timeout=20, check=False,
    )
    return result.returncode == 0


def tracked_under(target: Path, relative: str) -> bool:
    if not TRACKED_DESTINATIONS_GUARDED or not _inside_work_tree(target):
        # Decided by `rev-parse`, never by localized stderr text.
        return False
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
    ]
    return tuple(dict.fromkeys(f"/{relative}" for relative in relative_paths))


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _identity_at(parent: int, name: str) -> tuple[int, int] | None:
    try:
        return _identity(os.stat(name, dir_fd=parent, follow_symlinks=False))
    except FileNotFoundError:
        return None


def _rename_noreplace_at(parent: int, source: str, destination: str) -> bool:
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename = libc.renameatx_np
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(parent, source_bytes, parent, destination_bytes, 0x00000004)
    elif sys.platform.startswith("linux"):
        rename = libc.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(parent, source_bytes, parent, destination_bytes, 0x00000001)
    else:
        return False
    if result == 0:
        return True
    error = ctypes.get_errno()
    if error in {errno.EEXIST, errno.ENOTEMPTY}:
        return False
    raise OSError(error, os.strerror(error))


def _quarantine_name(name: str) -> str:
    return f".{name}.sdd-remove-{os.getpid()}-{secrets.token_hex(16)}"


def _unlink_owned_at(parent: int, name: str, owned: tuple[int, int], reason: str) -> None:
    current = _identity_at(parent, name)
    if current is None:
        return
    if current != owned:
        raise InstallError(f"{reason}: preserved as {name}")
    quarantine = _quarantine_name(name)
    try:
        os.rename(name, quarantine, src_dir_fd=parent, dst_dir_fd=parent)
    except FileNotFoundError:
        return
    moved = _identity_at(parent, quarantine)
    if moved != owned:
        try:
            os.link(
                quarantine,
                name,
                src_dir_fd=parent,
                dst_dir_fd=parent,
                follow_symlinks=False,
            )
            os.unlink(quarantine, dir_fd=parent)
            restored = True
        except FileExistsError:
            restored = False
        location = name if restored else quarantine
        raise InstallError(f"{reason}: preserved as {location}")
    os.unlink(quarantine, dir_fd=parent)


def _path_identity(path: Path) -> tuple[int, int] | None:
    try:
        return _identity(path.lstat())
    except FileNotFoundError:
        return None


def _unlink_owned_path(path: Path, owned: tuple[int, int], reason: str) -> None:
    if os.name == "posix":
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        parent = os.open(path.parent, flags)
        try:
            _unlink_owned_at(parent, path.name, owned, reason)
        finally:
            _close_descriptor(parent)
        return
    quarantine = path.with_name(_quarantine_name(path.name))
    try:
        os.rename(path, quarantine)
    except FileNotFoundError:
        return
    if _path_identity(quarantine) != owned:
        try:
            os.rename(quarantine, path)
        except OSError:
            pass
        raise InstallError(reason)
    quarantine.unlink()


def _remove_owned_directory_path(
    path: Path,
    owned: tuple[int, int],
    reason: str,
    *,
    recursive: bool = False,
) -> None:
    quarantine = path.with_name(_quarantine_name(path.name))
    if not recursive:
        if os.name == "posix":
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            try:
                descriptor = os.open(path, flags)
            except FileNotFoundError:
                return
            try:
                if _identity(os.fstat(descriptor)) != owned:
                    raise InstallError(reason)
                if os.listdir(descriptor):
                    raise InstallError(f"{reason}: non-empty directory preserved as {path.name}")
            finally:
                _close_descriptor(descriptor)
        elif path.exists() and any(path.iterdir()):
            raise InstallError(f"{reason}: non-empty directory preserved as {path.name}")
    if os.name == "posix":
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        parent = os.open(path.parent, flags)
        try:
            try:
                os.rename(path.name, quarantine.name, src_dir_fd=parent, dst_dir_fd=parent)
            except FileNotFoundError:
                return
            moved = _identity_at(parent, quarantine.name)
            if moved != owned:
                restored = _rename_noreplace_at(parent, quarantine.name, path.name)
                location = path.name if restored else quarantine.name
                raise InstallError(f"{reason}: preserved as {location}")
            if not recursive:
                try:
                    os.rmdir(quarantine.name, dir_fd=parent)
                    return
                except OSError as error:
                    if error.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
                        raise
                    restored = _rename_noreplace_at(parent, quarantine.name, path.name)
                    location = path.name if restored else quarantine.name
                    raise InstallError(f"{reason}: non-empty directory preserved as {location}")
        finally:
            _close_descriptor(parent)
    else:
        try:
            os.rename(path, quarantine)
        except FileNotFoundError:
            return
        if _path_identity(quarantine) != owned:
            try:
                os.rename(quarantine, path)
            except OSError:
                pass
            raise InstallError(reason)
        if not recursive:
            try:
                quarantine.rmdir()
                return
            except OSError:
                try:
                    os.rename(quarantine, path)
                except OSError:
                    pass
                raise InstallError(f"{reason}: non-empty directory preserved")
    if _path_identity(quarantine) != owned:
        raise InstallError(f"{reason}: quarantine ownership changed")
    shutil.rmtree(quarantine)


def _open_project_parent_nofollow(
    target: Path,
    relative: str,
    *,
    create: bool,
    created_directories: list[tuple[str, tuple[int, int]]] | None = None,
) -> int:
    path = Path(relative)
    if path.is_absolute() or not path.name or any(part in {"", ".", ".."} for part in path.parts):
        raise InstallError(f"CONFIG_DESTINATION_INVALID: {relative}")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(target, flags)
    traversed: list[str] = []
    try:
        for part in path.parent.parts:
            traversed.append(part)
            try:
                next_descriptor = os.open(part, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, 0o755, dir_fd=descriptor)
                created_identity = _identity(os.stat(part, dir_fd=descriptor, follow_symlinks=False))
                if created_directories is not None:
                    created_directories.append(("/".join(traversed), created_identity))
                next_descriptor = None
                try:
                    next_descriptor = os.open(part, flags, dir_fd=descriptor)
                    if _identity(os.fstat(next_descriptor)) != created_identity:
                        raise InstallError(f"CONFIG_DESTINATION_CHANGED: {'/'.join(traversed)}")
                    if _identity_at(descriptor, part) != created_identity:
                        raise InstallError(f"CONFIG_DESTINATION_CHANGED: {'/'.join(traversed)}")
                    if hasattr(os, "fchmod"):
                        os.fchmod(next_descriptor, 0o755)
                except BaseException:
                    if next_descriptor is not None:
                        _close_descriptor(next_descriptor)
                    raise
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        _close_descriptor(descriptor)
        raise


def _read_project_file_nofollow(target: Path, relative: str) -> bytes | None:
    if os.name != "posix":
        reject_symlinks(target, relative)
        path = target / relative
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(metadata.st_mode):
            raise InstallError(f"CONFIG_DESTINATION_INVALID: {relative}")
        return path.read_bytes()

    parent: int | None = None
    descriptor: int | None = None
    try:
        parent = _open_project_parent_nofollow(target, relative, create=False)
        descriptor = os.open(
            Path(relative).name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent,
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise InstallError(f"CONFIG_DESTINATION_INVALID: {relative}")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            return stream.read()
    except FileNotFoundError:
        return None
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise InstallError(f"SYMLINK_REJECTED: {target / relative}") from error
        raise
    finally:
        if descriptor is not None:
            _close_descriptor(descriptor)
        if parent is not None:
            _close_descriptor(parent)


def _create_project_file_nofollow(
    target: Path,
    relative: str,
    content: bytes,
    created_files: list[tuple[str, tuple[int, int], bytes]],
    created_directories: list[tuple[str, tuple[int, int]]],
    *,
    check_tracked: bool = True,
) -> None:
    if check_tracked and is_tracked(target, relative):
        raise InstallError(f"TRACKED_DESTINATION_PATH: {relative}")
    if os.name != "posix":
        reject_symlinks(target, relative)
        destination = target / relative
        current = target
        traversed: list[str] = []
        for part in Path(relative).parent.parts:
            traversed.append(part)
            current /= part
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                current.mkdir(mode=0o755)
                metadata = current.lstat()
                created_directories.append(("/".join(traversed), _identity(metadata)))
                current.chmod(0o755)
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise InstallError(f"SYMLINK_REJECTED: {current}")
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    else:
        parent = _open_project_parent_nofollow(
            target,
            relative,
            create=True,
            created_directories=created_directories,
        )
        try:
            descriptor = os.open(
                Path(relative).name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o644,
                dir_fd=parent,
            )
        finally:
            _close_descriptor(parent)
    try:
        owned_identity = _identity(os.fstat(descriptor))
        created_files.append((relative, owned_identity, content))
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            _close_descriptor(descriptor)


def _rollback_created_paths(
    target: Path,
    files: list[tuple[str, tuple[int, int], bytes]],
    directories: list[tuple[str, tuple[int, int]]],
) -> None:
    errors: list[str] = []
    for relative, owned, expected_content in reversed(files):
        try:
            current_content = _read_project_file_nofollow(target, relative)
            if current_content is None:
                continue
            if current_content != expected_content:
                raise InstallError(f"ROLLBACK_DESTINATION_STATE_CHANGED: {relative}")
            if os.name == "posix":
                parent = _open_project_parent_nofollow(target, relative, create=False)
                try:
                    _unlink_owned_at(
                        parent,
                        Path(relative).name,
                        owned,
                        f"ROLLBACK_DESTINATION_OWNERSHIP_LOST: {relative}",
                    )
                finally:
                    _close_descriptor(parent)
            else:
                _unlink_owned_path(
                    target / relative,
                    owned,
                    f"ROLLBACK_DESTINATION_OWNERSHIP_LOST: {relative}",
                )
        except (InstallError, OSError) as error:
            errors.append(f"{relative}: {error}")
    for relative, owned in reversed(directories):
        try:
            _remove_owned_directory_path(
                target / relative,
                owned,
                f"ROLLBACK_DESTINATION_OWNERSHIP_LOST: {relative}",
            )
        except (InstallError, OSError) as error:
            errors.append(f"{relative}: {error}")
    if errors:
        raise InstallError(f"INSTALL_ROLLBACK_FAILED: {'; '.join(errors)}")


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


def _typesafe_env_ready(status: dict[str, object]) -> bool:
    """A credential file is required only where the installer manages one."""
    if status.get("env_status") == "PRESENT":
        return True
    return not CREDENTIAL_FILE_MANAGED and status.get("env_status") == "ABSENT"


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
            and set(decoded) == {"install", "automatic_semantic_governance"}
            and decoded["install"] is True
            and isinstance(decoded["automatic_semantic_governance"], bool)
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


def onboarding_questions(
    target: Path | None = None,
    typesafe_choice: str | None = None,
    automatic_jev_governance: bool = False,
) -> dict[str, object]:
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
            "prompt": "Should the orchestrator install TypeSafe, and may it send automatic, potentially billed semantic classifications to Jev?",
            "accepted_answers": [
                "JSON object with install true and automatic_semantic_governance true or false",
                "none",
            ],
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
    recorded_install = (
        isinstance(recorded_value, dict)
        and set(recorded_value) == {"install", "automatic_semantic_governance"}
        and recorded_value.get("install") is True
        and isinstance(recorded_value.get("automatic_semantic_governance"), bool)
    )
    recorded_automatic = bool(
        isinstance(recorded_value, dict)
        and recorded_install
        and recorded_value["automatic_semantic_governance"] is True
    )
    recorded_none = recorded_typesafe.strip().casefold() == "none"
    integration_issues: list[str] = []
    if recorded_install:
        typesafe_healthy = typesafe["status"] == "INSTALLED" and _typesafe_env_ready(typesafe)
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
                if (
                    recorded_install
                    and recorded_automatic == automatic_jev_governance
                    and _typesafe_env_ready(typesafe)
                )
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
        if CREDENTIAL_FILE_MANAGED and typesafe_choice == "install" and typesafe["env_status"] == "ABSENT"
        else "NONE"
    )
    typesafe["automatic_semantic_governance"] = recorded_automatic
    typesafe["planned_automatic_semantic_governance"] = (
        automatic_jev_governance if typesafe_choice == "install" else recorded_automatic
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
- Accept `none` as an explicit answer for every integration. Otherwise use compact JSON on the same line: issue tracker requires `provider`, `project`, `read`, and `write`; Obsidian requires an absolute `vault` and relative `project_container`; TypeSafe requires `install:true` plus explicit `automatic_semantic_governance:true|false`; project tools require a non-empty array of objects with `tool`, `purpose`, `read`, and `write`.
- For an issue tracker, record the provider, project identifier, and separate read/write permission; verify connectivity read-only before any mutation.
- For Obsidian, record whether it is enabled and, only when enabled, the vault and project container required by `BOOTSTRAP.md`. When the official CLI is available, discover candidates read-only with `python3 .hermes/orchestration/runtime/obsidian_connector.py discover --json`; after `.hermes/obsidian.json` exists, verify the bound container and selected read transport with `python3 .hermes/orchestration/runtime/obsidian_connector.py preflight --repo . --json`. A filesystem fallback is valid; neither command writes a note.
- Jev is a TypeSafe model, not the installed product. `--typesafe-ai install --apply` installs guidance without authorizing automatic external calls; add `--automatic-jev-governance` only after explicit consent to potentially billed semantic transmissions. Existing `{"install":true}` records never grant that consent and require explicit migration. Use `--typesafe-ai none --apply` to opt out only when no TypeSafe installation is discoverable; the official `npx` command is informational and is never executed by this installer.
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
    legacy_value = answers.get("typesafe_ai", "")
    try:
        legacy_typesafe = _load_unique_json(legacy_value) == {"install": True}
    except (ValueError, TypeError):
        legacy_typesafe = False
    if (
        set(answers) == question_ids
        and legacy_typesafe
        and set(issues).issubset({"ANSWER_VALUE_INVALID", "STATUS_ANSWER_MISMATCH"})
    ):
        return
    reason = issues[0] if issues else "UNKNOWN"
    raise InstallError(f"ONBOARDING_RECORD_INVALID: {reason}")


def _render_onboarding_answer(
    target: Path,
    question_id: str,
    value: str,
    source: bytes | None = None,
) -> bytes:
    path = target / CONFIG_ROOT / "PROJECT_SETUP.md"
    reject_symlinks(target, f"{CONFIG_ROOT}/PROJECT_SETUP.md")
    raw = path.read_bytes() if source is None else source
    lines = raw.decode("utf-8").splitlines()
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


def _read_project_regular_snapshot(
    target: Path,
    relative: str,
) -> tuple[bytes, tuple[int, int]] | None:
    if os.name != "posix":
        reject_symlinks(target, relative)
        path = target / relative
        try:
            descriptor = os.open(path, os.O_RDONLY)
        except FileNotFoundError:
            return None
        parent = None
    else:
        try:
            parent = _open_project_parent_nofollow(target, relative, create=False)
        except FileNotFoundError:
            return None
        try:
            descriptor = os.open(
                Path(relative).name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent,
            )
        except FileNotFoundError:
            _close_descriptor(parent)
            return None
        except BaseException:
            _close_descriptor(parent)
            raise
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise InstallError(f"CONFIG_DESTINATION_INVALID: {relative}")
        owned = _identity(metadata)
        current = (
            _identity_at(parent, Path(relative).name)
            if parent is not None
            else _path_identity(target / relative)
        )
        if current != owned:
            raise InstallError(f"CONFIG_DESTINATION_CHANGED: {relative}")
        return _read_all(descriptor), owned
    finally:
        _close_descriptor(descriptor)
        if parent is not None:
            _close_descriptor(parent)


def _create_project_regular_owned(
    target: Path,
    relative: str,
    content: bytes,
    mode: int = 0o644,
) -> tuple[int, int]:
    parent: int | None = None
    descriptor: int | None = None
    owned: tuple[int, int] | None = None
    try:
        if os.name == "posix":
            parent = _open_project_parent_nofollow(target, relative, create=False)
            descriptor = os.open(
                Path(relative).name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                mode,
                dir_fd=parent,
            )
        else:
            reject_symlinks(target, relative)
            descriptor = os.open(target / relative, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        owned = _identity(os.fstat(descriptor))
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, mode)
        _write_all(descriptor, content)
        os.fsync(descriptor)
        current = (
            _identity_at(parent, Path(relative).name)
            if parent is not None
            else _path_identity(target / relative)
        )
        if current != owned:
            raise InstallError(f"DESTINATION_OWNERSHIP_LOST: {relative}")
        return owned
    except BaseException:
        if descriptor is not None:
            _close_descriptor(descriptor)
            descriptor = None
        if owned is not None:
            if parent is not None:
                _unlink_owned_at(
                    parent,
                    Path(relative).name,
                    owned,
                    f"DESTINATION_OWNERSHIP_LOST: {relative}",
                )
            else:
                _unlink_owned_path(
                    target / relative,
                    owned,
                    f"DESTINATION_OWNERSHIP_LOST: {relative}",
                )
        raise
    finally:
        if descriptor is not None:
            _close_descriptor(descriptor)
        if parent is not None:
            _close_descriptor(parent)


def _create_project_directory_owned(
    target: Path,
    relative: str,
    mode: int = 0o755,
) -> tuple[int | None, tuple[int, int]]:
    path = Path(relative)
    if os.name != "posix":
        reject_symlinks(target, str(path.parent))
        destination = target / path
        destination.mkdir(mode=mode)
        destination.chmod(mode)
        return None, _identity(destination.lstat())
    parent = _open_project_parent_nofollow(target, relative, create=False)
    parent_metadata = os.fstat(parent)
    if stat.S_IMODE(parent_metadata.st_mode) & 0o022:
        _close_descriptor(parent)
        raise InstallError(f"INSECURE_PARENT_PERMISSIONS: {path.parent}")
    descriptor: int | None = None
    owned: tuple[int, int] | None = None
    try:
        os.mkdir(path.name, mode, dir_fd=parent)
        owned = _identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False))
        descriptor = os.open(
            path.name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent,
        )
        if _identity(os.fstat(descriptor)) != owned:
            raise InstallError(f"DESTINATION_OWNERSHIP_LOST: {relative}")
        if _identity_at(parent, path.name) != owned:
            raise InstallError(f"DESTINATION_OWNERSHIP_LOST: {relative}")
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, mode)
        return descriptor, owned
    except BaseException:
        if descriptor is not None:
            _close_descriptor(descriptor)
        if owned is not None:
            _remove_owned_directory_path(
                target / relative,
                owned,
                f"DESTINATION_OWNERSHIP_LOST: {relative}",
            )
        raise
    finally:
        _close_descriptor(parent)


def _overwrite_project_file_nofollow(
    target: Path,
    relative: str,
    owned: tuple[int, int],
    content: bytes,
    *,
    expected: bytes | None = None,
) -> None:
    if os.name != "posix":
        path = target / relative
        if _path_identity(path) != owned:
            raise InstallError(f"DESTINATION_OWNERSHIP_LOST: {relative}")
        descriptor = os.open(path, os.O_RDWR)
        parent = None
    else:
        parent = _open_project_parent_nofollow(target, relative, create=False)
        descriptor = os.open(
            Path(relative).name,
            os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent,
        )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or _identity(metadata) != owned:
            raise InstallError(f"DESTINATION_OWNERSHIP_LOST: {relative}")
        if os.name == "posix" and parent is not None:
            if _identity_at(parent, Path(relative).name) != owned:
                raise InstallError(f"DESTINATION_OWNERSHIP_LOST: {relative}")
        elif _path_identity(target / relative) != owned:
            raise InstallError(f"DESTINATION_OWNERSHIP_LOST: {relative}")
        before = _read_all(descriptor)
        if expected is not None and before != expected:
            raise InstallError(f"DESTINATION_CONTENT_CHANGED: {relative}")
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.ftruncate(descriptor, 0)
            _write_all(descriptor, content)
            os.fsync(descriptor)
            current = (
                _identity_at(parent, Path(relative).name)
                if parent is not None
                else _path_identity(target / relative)
            )
            if current != owned:
                raise InstallError(f"DESTINATION_OWNERSHIP_LOST: {relative}")
        except BaseException as error:
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                os.ftruncate(descriptor, 0)
                _write_all(descriptor, before)
                os.fsync(descriptor)
            except BaseException as rollback:
                raise InstallError(f"DESTINATION_ROLLBACK_FAILED: {relative}: {rollback}") from error
            raise
    finally:
        _close_descriptor(descriptor)
        if parent is not None:
            _close_descriptor(parent)


def _write_onboarding_answer(target: Path, question_id: str, value: str) -> None:
    relative = f"{CONFIG_ROOT}/PROJECT_SETUP.md"
    snapshot = _read_project_regular_snapshot(target, relative)
    if snapshot is None:
        raise InstallError("ONBOARDING_RECORD_MISSING")
    before, owned = snapshot
    content = _render_onboarding_answer(target, question_id, value, source=before)
    _overwrite_project_file_nofollow(target, relative, owned, content, expected=before)


def _restore_typesafe_install(
    target: Path,
    lock_before: bytes | None,
    lock_after: bytes | None,
    setup_before: bytes,
    setup_after: bytes,
    skill_identity: tuple[int, int],
    lock_identity: tuple[int, int] | None,
    setup_identity: tuple[int, int],
) -> None:
    skill_root = target / TYPESAFE_SKILL_ROOT
    lock_path = target / TYPESAFE_LOCK_PATH
    errors: list[BaseException] = []
    try:
        reject_symlinks(target, ".hermes/skills")
        _remove_owned_directory_path(
            skill_root,
            skill_identity,
            "TYPESAFE_SKILL_ROLLBACK_OWNERSHIP_LOST",
            recursive=True,
        )
    except (InstallError, OSError) as error:
        errors.append(error)
    try:
        if lock_identity is not None:
            lock_snapshot = _read_project_regular_snapshot(target, TYPESAFE_LOCK_PATH)
            if lock_snapshot is None or lock_snapshot[1] != lock_identity:
                raise InstallError("TYPESAFE_LOCK_ROLLBACK_OWNERSHIP_LOST")
            current_lock = lock_snapshot[0]
            if lock_before is None:
                if lock_after is None or current_lock != lock_after:
                    raise InstallError("TYPESAFE_LOCK_ROLLBACK_STATE_CHANGED")
                _unlink_owned_path(
                    lock_path,
                    lock_identity,
                    "TYPESAFE_LOCK_ROLLBACK_OWNERSHIP_LOST",
                )
            elif current_lock == lock_before:
                pass
            elif lock_after is not None and current_lock == lock_after:
                _overwrite_project_file_nofollow(
                    target,
                    TYPESAFE_LOCK_PATH,
                    lock_identity,
                    lock_before,
                    expected=lock_after,
                )
            else:
                raise InstallError("TYPESAFE_LOCK_ROLLBACK_STATE_CHANGED")
    except (InstallError, OSError) as error:
        errors.append(error)
    try:
        setup_relative = f"{CONFIG_ROOT}/PROJECT_SETUP.md"
        setup_snapshot = _read_project_regular_snapshot(target, setup_relative)
        if setup_snapshot is None or setup_snapshot[1] != setup_identity:
            raise InstallError("ONBOARDING_ROLLBACK_OWNERSHIP_LOST")
        current_setup = setup_snapshot[0]
        if current_setup == setup_before:
            pass
        elif current_setup == setup_after:
            _overwrite_project_file_nofollow(
                target,
                setup_relative,
                setup_identity,
                setup_before,
                expected=setup_after,
            )
        else:
            raise InstallError("ONBOARDING_ROLLBACK_STATE_CHANGED")
    except (InstallError, OSError) as error:
        errors.append(error)
    if errors:
        details = "; ".join(str(item) for item in errors)
        raise InstallError(f"TYPESAFE_ROLLBACK_FAILED: {details}")


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


def _ensure_typesafe_env(target: Path) -> tuple[bool, tuple[int, int] | None]:
    """Create a private empty credential file without touching an existing one."""
    environment = typesafe_env_status(target)
    if environment["env_status"] == "PRESENT":
        return False, None
    if not CREDENTIAL_FILE_MANAGED and environment["env_status"] == "ABSENT":
        return False, None
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
    identity = _path_identity(target / path)
    if identity is None:
        raise InstallError("TYPESAFE_ENV_CREATION_FAILED")
    return True, identity


def _rollback_typesafe_env(target: Path, identity: tuple[int, int] | None) -> None:
    if identity is None:
        return
    snapshot = _read_project_regular_snapshot(target, TYPESAFE_ENV_PATH)
    if snapshot is None:
        return
    content, current_identity = snapshot
    if current_identity != identity or content != TYPESAFE_ENV_CONTENT:
        raise InstallError("TYPESAFE_ENV_ROLLBACK_STATE_CHANGED")
    _unlink_owned_path(
        target / TYPESAFE_ENV_PATH,
        identity,
        "TYPESAFE_ENV_ROLLBACK_OWNERSHIP_LOST",
    )


def install_typesafe_skill(
    target: Path,
    onboarding_after: bytes,
    before_commit: Callable[[], None] | None = None,
) -> str:
    """Commit the vetted bundled skill, merged lock and onboarding answer.

    `before_commit` runs after every write and inside this function's own
    rollback, so a caller-side check that fails undoes the integration too.
    """
    current = _preflight_typesafe_install(target)
    if current["status"] == "INSTALLED":
        setup_relative = f"{CONFIG_ROOT}/PROJECT_SETUP.md"
        setup_snapshot = _read_project_regular_snapshot(target, setup_relative)
        if setup_snapshot is None:
            raise InstallError("ONBOARDING_RECORD_MISSING")
        setup_before, setup_identity = setup_snapshot
        env_identity: tuple[int, int] | None = None
        try:
            _overwrite_project_file_nofollow(
                target,
                setup_relative,
                setup_identity,
                onboarding_after,
                expected=setup_before,
            )
            _, env_identity = _ensure_typesafe_env(target)
            if _path_identity(target / setup_relative) != setup_identity:
                raise InstallError("ONBOARDING_RECORD_CHANGED_DURING_APPLY")
            if before_commit is not None:
                before_commit()
        except BaseException as error:
            rollback_errors: list[BaseException] = []
            try:
                current_setup = _read_project_regular_snapshot(target, setup_relative)
                if current_setup is None or current_setup[1] != setup_identity:
                    raise InstallError("ONBOARDING_RECORD_CHANGED_DURING_APPLY")
                if current_setup[0] != setup_before:
                    _overwrite_project_file_nofollow(
                        target,
                        setup_relative,
                        setup_identity,
                        setup_before,
                        expected=onboarding_after,
                    )
            except (InstallError, OSError) as rollback:
                rollback_errors.append(rollback)
            try:
                _rollback_typesafe_env(target, env_identity)
            except (InstallError, OSError) as rollback:
                rollback_errors.append(rollback)
            if rollback_errors:
                details = "; ".join(str(item) for item in rollback_errors)
                raise InstallError(f"TYPESAFE_ROLLBACK_FAILED: {details}") from error
            raise
        return "RECORD"

    lock_path = target / TYPESAFE_LOCK_PATH
    lock_snapshot = _read_project_regular_snapshot(target, TYPESAFE_LOCK_PATH)
    lock_before = lock_snapshot[0] if lock_snapshot is not None else None
    lock_identity_before = lock_snapshot[1] if lock_snapshot is not None else None
    setup_snapshot = _read_project_regular_snapshot(target, f"{CONFIG_ROOT}/PROJECT_SETUP.md")
    if setup_snapshot is None:
        raise InstallError("ONBOARDING_RECORD_MISSING")
    setup_before, setup_identity = setup_snapshot
    entry: dict[str, object] = {
        "source": "typesafe-ai/skills",
        "ref": TYPESAFE_SOURCE_REF,
        "sourceType": "github",
        "skillPath": "skills/typesafe-ai/SKILL.md",
        "computedHash": TYPESAFE_UPSTREAM_HASH,
    }
    merged_lock = _merged_typesafe_lock(lock_before, entry)

    reject_symlinks(target, ".hermes/skills")
    try:
        skill_descriptor, skill_identity = _create_project_directory_owned(
            target,
            TYPESAFE_SKILL_ROOT,
        )
    except FileExistsError as error:
        raise InstallError(f"TYPESAFE_SKILL_CONFLICT: {TYPESAFE_SKILL_ROOT}") from error
    lock_identity: tuple[int, int] | None = None
    env_identity: tuple[int, int] | None = None

    try:
        for source in sorted(TYPESAFE_VENDOR.iterdir()):
            content = source.read_bytes()
            if skill_descriptor is not None:
                descriptor = os.open(
                    source.name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o644,
                    dir_fd=skill_descriptor,
                )
                try:
                    if hasattr(os, "fchmod"):
                        os.fchmod(descriptor, 0o644)
                    _write_all(descriptor, content)
                    os.fsync(descriptor)
                finally:
                    _close_descriptor(descriptor)
            else:
                _create_project_regular_owned(
                    target,
                    f"{TYPESAFE_SKILL_ROOT}/{source.name}",
                    content,
                )
        if skill_descriptor is not None:
            if _identity(os.fstat(skill_descriptor)) != skill_identity:
                raise InstallError("TYPESAFE_SKILL_OWNERSHIP_LOST")
            _close_descriptor(skill_descriptor)
            skill_descriptor = None
        if _path_identity(target / TYPESAFE_SKILL_ROOT) != skill_identity:
            raise InstallError("TYPESAFE_SKILL_OWNERSHIP_LOST")
        if lock_before is None:
            lock_identity = _create_project_regular_owned(target, TYPESAFE_LOCK_PATH, merged_lock)
        else:
            if lock_identity_before is None:
                raise InstallError("TYPESAFE_LOCK_CHANGED_DURING_APPLY")
            lock_identity = lock_identity_before
            _overwrite_project_file_nofollow(
                target,
                TYPESAFE_LOCK_PATH,
                lock_identity,
                merged_lock,
                expected=lock_before,
            )
        if lock_before is not None:
            before = _load_unique_json(lock_before.decode("utf-8"))
            lock_after_snapshot = _read_project_regular_snapshot(target, TYPESAFE_LOCK_PATH)
            if lock_after_snapshot is None or lock_after_snapshot[1] != lock_identity:
                raise InstallError("TYPESAFE_LOCK_CHANGED_DURING_APPLY")
            after = _load_unique_json(lock_after_snapshot[0].decode("utf-8"))
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
        _overwrite_project_file_nofollow(
            target,
            f"{CONFIG_ROOT}/PROJECT_SETUP.md",
            setup_identity,
            onboarding_after,
            expected=setup_before,
        )
        _, env_identity = _ensure_typesafe_env(target)
        verified = typesafe_skill_status(target)
        if verified["status"] != "INSTALLED":
            raise InstallError(f"TYPESAFE_INSTALL_FAILED: target verification: {verified['issue']}")
        if _path_identity(target / TYPESAFE_SKILL_ROOT) != skill_identity:
            raise InstallError("TYPESAFE_SKILL_OWNERSHIP_LOST")
        if _path_identity(lock_path) != lock_identity:
            raise InstallError("TYPESAFE_LOCK_CHANGED_DURING_APPLY")
        if _path_identity(target / CONFIG_ROOT / "PROJECT_SETUP.md") != setup_identity:
            raise InstallError("ONBOARDING_RECORD_CHANGED_DURING_APPLY")
        if before_commit is not None:
            before_commit()
    except BaseException as error:
        if skill_descriptor is not None:
            _close_descriptor(skill_descriptor)
            skill_descriptor = None
        rollback_errors: list[BaseException] = []
        try:
            _restore_typesafe_install(
                target,
                lock_before,
                merged_lock,
                setup_before,
                onboarding_after,
                skill_identity,
                lock_identity,
                setup_identity,
            )
        except (InstallError, OSError) as rollback:
            rollback_errors.append(rollback)
        try:
            _rollback_typesafe_env(target, env_identity)
        except (InstallError, OSError) as rollback:
            rollback_errors.append(rollback)
        if rollback_errors:
            details = "; ".join(str(item) for item in rollback_errors)
            raise InstallError(f"TYPESAFE_ROLLBACK_FAILED: {details}") from error
        raise
    return "INSTALL"


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


def _plan_base_files(target: Path) -> tuple[list[str], list[str]]:
    planned: list[str] = []
    for source in template_files():
        relative = source.relative_to(TEMPLATE).as_posix()
        if is_tracked(target, relative):
            raise InstallError(f"TRACKED_DESTINATION_PATH: {relative}")
        existing = _read_project_file_nofollow(target, relative)
        if existing is not None and existing != source.read_bytes():
            raise InstallError(f"CONFIG_CONFLICT: {relative}")
        if existing is None:
            planned.append(relative)

    existing_state: list[str] = []
    for relative in STATE_PATHS:
        if is_tracked(target, relative):
            raise InstallError(f"TRACKED_DESTINATION_PATH: {relative}")
        if _read_project_file_nofollow(target, relative) is not None:
            existing_state.append(relative)
    if existing_state:
        if len(existing_state) != len(STATE_PATHS) or planned:
            raise InstallError(f"LOCAL_STATE_REQUIRES_REVIEW: {', '.join(existing_state)}")
    else:
        planned.extend(STATE_PATHS)
    return planned, existing_state


def _open_git_info(workspace: dict[str, str]) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY
    if os.name == "posix":
        flags |= os.O_NOFOLLOW
    common: int | None = None
    try:
        common = os.open(workspace["git_common_dir"], flags)
        info = os.open("info", flags, dir_fd=common)
        return info
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise InstallError("EXCLUDE_SYMLINK_REJECTED") from error
        raise InstallError(f"EXCLUDE_INVALID: {error}") from error
    finally:
        if common is not None:
            _close_descriptor(common)


def _read_exclude(info: int) -> bytes | None:
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY
        if os.name == "posix":
            flags |= os.O_NOFOLLOW | os.O_NONBLOCK
        descriptor = os.open("exclude", flags, dir_fd=info)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise InstallError("EXCLUDE_INVALID")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            return stream.read()
    except FileNotFoundError:
        return None
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise InstallError("EXCLUDE_SYMLINK_REJECTED") from error
        raise InstallError(f"EXCLUDE_INVALID: {error}") from error
    finally:
        if descriptor is not None:
            _close_descriptor(descriptor)


def _render_exclude(existing: bytes | None) -> bytes:
    content = existing or b""
    entries = set(content.splitlines())
    additions = []
    for item in managed_exclude_entries():
        encoded = item.encode("utf-8")
        if encoded not in entries and encoded.lstrip(b"/") not in entries:
            additions.append(encoded)
    if not additions:
        return content
    separator = b"" if not content or content.endswith(b"\n") else b"\n"
    return content + separator + b"\n".join(additions) + b"\n"


def _portable_exclude_path(workspace: dict[str, str]) -> Path:
    common = Path(workspace["git_common_dir"])
    info = common / "info"
    if common.is_symlink() or info.is_symlink():
        raise InstallError("EXCLUDE_SYMLINK_REJECTED")
    if not info.is_dir():
        raise InstallError("EXCLUDE_INVALID")
    path = info / "exclude"
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return path
    if stat.S_ISLNK(metadata.st_mode):
        raise InstallError("EXCLUDE_SYMLINK_REJECTED")
    if not stat.S_ISREG(metadata.st_mode):
        raise InstallError("EXCLUDE_INVALID")
    return path


def _read_portable_exclude(workspace: dict[str, str]) -> bytes | None:
    path = _portable_exclude_path(workspace)
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def exclude_update_planned(workspace: dict[str, str]) -> bool:
    if os.name != "posix":
        existing = _read_portable_exclude(workspace)
        return _render_exclude(existing) != (existing or b"")
    info = _open_git_info(workspace)
    try:
        existing = _read_exclude(info)
        return _render_exclude(existing) != (existing or b"")
    finally:
        _close_descriptor(info)


def _read_all(descriptor: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 65536)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short write")
        view = view[written:]


def _acquire_lock(
    parent: int,
    name: str,
    reason: str,
) -> tuple[int, tuple[int, int]]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if os.name == "posix":
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    owned: tuple[int, int] | None = None
    try:
        descriptor = os.open(name, flags, 0o600, dir_fd=parent)
        owned = _identity(os.fstat(descriptor))
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        return descriptor, owned
    except FileExistsError as error:
        raise InstallError(reason) from error
    except BaseException:
        if descriptor is not None:
            _close_descriptor(descriptor)
        if owned is not None:
            _unlink_owned_at(parent, name, owned, f"{reason}_OWNERSHIP_LOST")
        raise


def _acquire_portable_lock(path: Path, reason: str) -> tuple[int, tuple[int, int]]:
    descriptor: int | None = None
    owned: tuple[int, int] | None = None
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        owned = _identity(os.fstat(descriptor))
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        else:
            path.chmod(0o600)
        return descriptor, owned
    except FileExistsError as error:
        raise InstallError(reason) from error
    except BaseException:
        if descriptor is not None:
            _close_descriptor(descriptor)
        if owned is not None:
            _unlink_owned_path(path, owned, f"{reason}_OWNERSHIP_LOST")
        raise


def _apply_base_install_portable(
    target: Path,
    workspace: dict[str, str],
    expected_planned: list[str],
    after_base,
) -> list[str]:
    index_path = Path(workspace["git_dir"]) / "index.lock"
    exclude_path = _portable_exclude_path(workspace)
    exclude_lock_path = exclude_path.with_name("exclude.sdd-orchestrator.lock")
    index_lock: int | None = None
    index_lock_identity: tuple[int, int] | None = None
    exclude_lock: int | None = None
    exclude_lock_identity: tuple[int, int] | None = None
    exclude_descriptor: int | None = None
    exclude_identity: tuple[int, int] | None = None
    exclude_before: bytes | None = None
    exclude_after = b""
    exclude_changed = False
    exclude_created = False
    created_files: list[tuple[str, tuple[int, int], bytes]] = []
    created_directories: list[tuple[str, tuple[int, int]]] = []
    try:
        index_lock, index_lock_identity = _acquire_portable_lock(index_path, "GIT_INDEX_LOCKED")
        exclude_lock, exclude_lock_identity = _acquire_portable_lock(exclude_lock_path, "EXCLUDE_LOCKED")
        exclude_before = _read_portable_exclude(workspace)
        exclude_after = _render_exclude(exclude_before)
        exclude_changed = exclude_after != (exclude_before or b"")
        locked_planned, _ = _plan_base_files(target)
        if locked_planned != expected_planned:
            raise InstallError("INSTALL_STATE_CHANGED")

        if exclude_changed:
            flags = os.O_RDWR
            if exclude_before is None:
                flags |= os.O_CREAT | os.O_EXCL
            exclude_descriptor = os.open(exclude_path, flags, 0o644)
            metadata = os.fstat(exclude_descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise InstallError("EXCLUDE_INVALID")
            exclude_identity = _identity(metadata)
            exclude_created = exclude_before is None
            if _read_all(exclude_descriptor) != (exclude_before or b""):
                raise InstallError("EXCLUDE_CHANGED_DURING_APPLY")
            if _path_identity(exclude_path) != exclude_identity:
                raise InstallError("EXCLUDE_CHANGED_DURING_APPLY")
            if _read_all(exclude_descriptor) != (exclude_before or b""):
                raise InstallError("EXCLUDE_CHANGED_DURING_APPLY")
            os.lseek(exclude_descriptor, 0, os.SEEK_SET)
            os.ftruncate(exclude_descriptor, 0)
            _write_all(exclude_descriptor, exclude_after)
            os.fsync(exclude_descriptor)
            if _path_identity(exclude_path) != exclude_identity:
                raise InstallError("EXCLUDE_CHANGED_DURING_APPLY")

        planned_set = set(expected_planned)
        for source in template_files():
            relative = source.relative_to(TEMPLATE).as_posix()
            if relative in planned_set:
                _create_project_file_nofollow(
                    target,
                    relative,
                    source.read_bytes(),
                    created_files,
                    created_directories,
                )
        generated = {
            f"{CONFIG_ROOT}/STATE.md": state(workspace).encode("utf-8"),
            f"{CONFIG_ROOT}/PROJECT_SETUP.md": project_setup().encode("utf-8"),
            f"{CONFIG_ROOT}/INCIDENTS.md": b"# SDD Orchestration Incidents\n\nNo incidents recorded.\n",
            f"{CONFIG_ROOT}/ACTION_JOURNAL.json": empty_journal(target, workspace).encode("utf-8"),
        }
        for relative in STATE_PATHS:
            if relative in planned_set:
                _create_project_file_nofollow(
                    target,
                    relative,
                    generated[relative],
                    created_files,
                    created_directories,
                )
        if exclude_changed and exclude_descriptor is not None:
            if _path_identity(exclude_path) != exclude_identity:
                raise InstallError("EXCLUDE_CHANGED_DURING_APPLY")
            if _read_all(exclude_descriptor) != exclude_after:
                raise InstallError("EXCLUDE_CHANGED_DURING_APPLY")
        if after_base is not None:
            after_base()
    except BaseException as error:
        rollback_errors: list[BaseException] = []
        try:
            _rollback_created_paths(target, created_files, created_directories)
        except BaseException as rollback:
            rollback_errors.append(rollback)
        try:
            if exclude_changed and exclude_identity is not None:
                if _path_identity(exclude_path) != exclude_identity:
                    raise InstallError("EXCLUDE_ROLLBACK_OWNERSHIP_LOST")
                if exclude_created:
                    if exclude_descriptor is not None:
                        _close_descriptor(exclude_descriptor)
                        exclude_descriptor = None
                    _unlink_owned_path(
                        exclude_path,
                        exclude_identity,
                        "EXCLUDE_ROLLBACK_OWNERSHIP_LOST",
                    )
                elif exclude_descriptor is not None and exclude_before is not None:
                    if _read_all(exclude_descriptor) != exclude_after:
                        raise InstallError("EXCLUDE_ROLLBACK_STATE_CHANGED")
                    os.lseek(exclude_descriptor, 0, os.SEEK_SET)
                    os.ftruncate(exclude_descriptor, 0)
                    _write_all(exclude_descriptor, exclude_before)
                    os.fsync(exclude_descriptor)
        except BaseException as rollback:
            rollback_errors.append(rollback)
        if rollback_errors:
            details = "; ".join(str(item) for item in rollback_errors)
            raise InstallError(f"INSTALL_ROLLBACK_FAILED: {details}") from error
        raise
    finally:
        active_error = sys.exc_info()[1]
        cleanup_errors: list[BaseException] = []
        if exclude_descriptor is not None:
            _close_descriptor(exclude_descriptor)
        if exclude_lock is not None:
            _close_descriptor(exclude_lock)
            if exclude_lock_identity is not None:
                try:
                    _unlink_owned_path(
                        exclude_lock_path,
                        exclude_lock_identity,
                        "EXCLUDE_LOCK_OWNERSHIP_LOST",
                    )
                except InstallError as cleanup:
                    if "_OWNERSHIP_LOST" not in str(cleanup):
                        cleanup_errors.append(cleanup)
                except BaseException as cleanup:
                    cleanup_errors.append(cleanup)
        if index_lock is not None:
            _close_descriptor(index_lock)
            if index_lock_identity is not None:
                try:
                    _unlink_owned_path(
                        index_path,
                        index_lock_identity,
                        "GIT_INDEX_LOCK_OWNERSHIP_LOST",
                    )
                except InstallError as cleanup:
                    if "_OWNERSHIP_LOST" not in str(cleanup):
                        cleanup_errors.append(cleanup)
                except BaseException as cleanup:
                    cleanup_errors.append(cleanup)
        if cleanup_errors and active_error is not None:
            details = "; ".join(str(item) for item in cleanup_errors)
            raise InstallError(
                f"{active_error}; INSTALL_LOCK_CLEANUP_FAILED: {details}"
            ) from active_error
    return [str(item) for item in cleanup_errors]


def _apply_base_install(
    target: Path,
    workspace: dict[str, str],
    expected_planned: list[str],
    after_base=None,
) -> list[str]:
    if os.name != "posix":
        return _apply_base_install_portable(target, workspace, expected_planned, after_base)

    git_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    git_directory = os.open(workspace["git_dir"], git_flags)
    index_lock: int | None = None
    index_lock_identity: tuple[int, int] | None = None
    info: int | None = None
    exclude_lock: int | None = None
    exclude_lock_identity: tuple[int, int] | None = None
    exclude_descriptor: int | None = None
    exclude_identity: tuple[int, int] | None = None
    exclude_before: bytes | None = None
    exclude_after = b""
    exclude_changed = False
    exclude_created = False
    created_files: list[tuple[str, tuple[int, int], bytes]] = []
    created_directories: list[tuple[str, tuple[int, int]]] = []
    try:
        index_lock, index_lock_identity = _acquire_lock(git_directory, "index.lock", "GIT_INDEX_LOCKED")
        info = _open_git_info(workspace)
        exclude_lock, exclude_lock_identity = _acquire_lock(
            info,
            "exclude.sdd-orchestrator.lock",
            "EXCLUDE_LOCKED",
        )
        exclude_before = _read_exclude(info)
        exclude_after = _render_exclude(exclude_before)
        exclude_changed = exclude_after != (exclude_before or b"")

        locked_planned, _ = _plan_base_files(target)
        if locked_planned != expected_planned:
            raise InstallError("INSTALL_STATE_CHANGED")

        if exclude_changed:
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
            if exclude_before is None:
                flags |= os.O_CREAT | os.O_EXCL
            exclude_descriptor = os.open("exclude", flags, 0o644, dir_fd=info)
            metadata = os.fstat(exclude_descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise InstallError("EXCLUDE_INVALID")
            exclude_identity = _identity(metadata)
            exclude_created = exclude_before is None
            if _read_all(exclude_descriptor) != (exclude_before or b""):
                raise InstallError("EXCLUDE_CHANGED_DURING_APPLY")
            if _identity_at(info, "exclude") != exclude_identity:
                raise InstallError("EXCLUDE_CHANGED_DURING_APPLY")
            if _read_all(exclude_descriptor) != (exclude_before or b""):
                raise InstallError("EXCLUDE_CHANGED_DURING_APPLY")
            os.lseek(exclude_descriptor, 0, os.SEEK_SET)
            os.ftruncate(exclude_descriptor, 0)
            _write_all(exclude_descriptor, exclude_after)
            os.fsync(exclude_descriptor)
            if _identity_at(info, "exclude") != exclude_identity:
                raise InstallError("EXCLUDE_CHANGED_DURING_APPLY")

        planned_set = set(expected_planned)
        for source in template_files():
            relative = source.relative_to(TEMPLATE).as_posix()
            if relative in planned_set:
                _create_project_file_nofollow(
                    target,
                    relative,
                    source.read_bytes(),
                    created_files,
                    created_directories,
                )
        generated = {
            f"{CONFIG_ROOT}/STATE.md": state(workspace).encode("utf-8"),
            f"{CONFIG_ROOT}/PROJECT_SETUP.md": project_setup().encode("utf-8"),
            f"{CONFIG_ROOT}/INCIDENTS.md": b"# SDD Orchestration Incidents\n\nNo incidents recorded.\n",
            f"{CONFIG_ROOT}/ACTION_JOURNAL.json": empty_journal(target, workspace).encode("utf-8"),
        }
        for relative in STATE_PATHS:
            if relative in planned_set:
                _create_project_file_nofollow(
                    target,
                    relative,
                    generated[relative],
                    created_files,
                    created_directories,
                )
        if exclude_changed and info is not None and exclude_descriptor is not None:
            if _identity_at(info, "exclude") != exclude_identity:
                raise InstallError("EXCLUDE_CHANGED_DURING_APPLY")
            if _read_all(exclude_descriptor) != exclude_after:
                raise InstallError("EXCLUDE_CHANGED_DURING_APPLY")
        if after_base is not None:
            after_base()
    except BaseException as error:
        rollback_errors: list[BaseException] = []
        try:
            _rollback_created_paths(target, created_files, created_directories)
        except BaseException as rollback:
            rollback_errors.append(rollback)
        try:
            if exclude_changed and info is not None and exclude_identity is not None:
                if _identity_at(info, "exclude") != exclude_identity:
                    raise InstallError("EXCLUDE_ROLLBACK_OWNERSHIP_LOST")
                if exclude_created:
                    if exclude_descriptor is not None:
                        _close_descriptor(exclude_descriptor)
                        exclude_descriptor = None
                    _unlink_owned_at(
                        info,
                        "exclude",
                        exclude_identity,
                        "EXCLUDE_ROLLBACK_OWNERSHIP_LOST",
                    )
                elif exclude_descriptor is not None and exclude_before is not None:
                    if _read_all(exclude_descriptor) != exclude_after:
                        raise InstallError("EXCLUDE_ROLLBACK_STATE_CHANGED")
                    os.lseek(exclude_descriptor, 0, os.SEEK_SET)
                    os.ftruncate(exclude_descriptor, 0)
                    _write_all(exclude_descriptor, exclude_before)
                    os.fsync(exclude_descriptor)
        except BaseException as rollback:
            rollback_errors.append(rollback)
        if rollback_errors:
            details = "; ".join(str(item) for item in rollback_errors)
            raise InstallError(f"INSTALL_ROLLBACK_FAILED: {details}") from error
        raise
    finally:
        active_error = sys.exc_info()[1]
        cleanup_errors: list[BaseException] = []
        if exclude_descriptor is not None:
            _close_descriptor(exclude_descriptor)
        if exclude_lock is not None:
            _close_descriptor(exclude_lock)
            if info is not None and exclude_lock_identity is not None:
                try:
                    _unlink_owned_at(
                        info,
                        "exclude.sdd-orchestrator.lock",
                        exclude_lock_identity,
                        "EXCLUDE_LOCK_OWNERSHIP_LOST",
                    )
                except InstallError as cleanup:
                    if "_OWNERSHIP_LOST" not in str(cleanup):
                        cleanup_errors.append(cleanup)
                except BaseException as cleanup:
                    cleanup_errors.append(cleanup)
        if info is not None:
            _close_descriptor(info)
        if index_lock is not None:
            _close_descriptor(index_lock)
            if index_lock_identity is not None:
                try:
                    _unlink_owned_at(
                        git_directory,
                        "index.lock",
                        index_lock_identity,
                        "GIT_INDEX_LOCK_OWNERSHIP_LOST",
                    )
                except InstallError as cleanup:
                    if "_OWNERSHIP_LOST" not in str(cleanup):
                        cleanup_errors.append(cleanup)
                except BaseException as cleanup:
                    cleanup_errors.append(cleanup)
        _close_descriptor(git_directory)
        if cleanup_errors and active_error is not None:
            details = "; ".join(str(item) for item in cleanup_errors)
            raise InstallError(
                f"{active_error}; INSTALL_LOCK_CLEANUP_FAILED: {details}"
            ) from active_error
    return [str(item) for item in cleanup_errors]


OBSIDIAN_RUNTIME_SUBPATH = ".hermes-runtime"
OBSIDIAN_BINDING_PATH = ".hermes/obsidian.json"
WORKTREE_RUNTIME_FILES = ("STATE.md", "INCIDENTS.md", "ACTION_JOURNAL.json")
#: Controller files the owner is told to edit after install. Created when
#: absent, never compared, so a second worktree can share the container.
OBSIDIAN_USER_CONFIGURED = frozenset({f"{CONFIG_ROOT}/policies/GATES.md"})


def _identity_chain(path: Path) -> list[tuple[int, int]]:
    """Device/inode of `path` (or its deepest existing ancestor) and every ancestor.

    Comparing identities instead of spellings is immune to case-insensitive
    filesystems, symlinks and `..`, so a differently cased path cannot pass.
    """
    current = Path(os.path.abspath(path))
    while not current.exists() and current != current.parent:
        current = current.parent
    chain: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    while True:
        metadata = os.stat(current)
        identity = (metadata.st_dev, metadata.st_ino)
        if identity in seen:
            break
        seen.add(identity)
        chain.append(identity)
        parent = current.parent
        if parent == current:
            break
        current = parent
    return chain


def _git_text(directory: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(directory), *arguments], text=True, capture_output=True, timeout=20, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _protected_roots(target: Path, workspace: dict[str, str]) -> list[Path]:
    """The target, its Git directory, every linked worktree and every superproject."""
    roots: list[Path] = []
    pending = [target]
    visited: set[str] = set()
    while pending:
        tree = pending.pop()
        key = os.path.normcase(os.path.abspath(tree))
        if key in visited:
            continue
        visited.add(key)
        roots.append(tree)
        common = _git_text(tree, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if common:
            roots.append(Path(common))
        for line in _git_text(tree, "worktree", "list", "--porcelain").splitlines():
            if line.startswith("worktree "):
                pending.append(Path(line[len("worktree "):]))
        superproject = _git_text(tree, "rev-parse", "--show-superproject-working-tree")
        if superproject:
            pending.append(Path(superproject))
    roots.append(Path(workspace["git_common_dir"]))
    return [root for root in roots if root.exists()]


def _reject_storage_overlap(vault: Path, project: str, target: Path, workspace: dict[str, str]) -> None:
    """The vault and every checkout of the user's repository must be disjoint."""
    storage = [vault, vault / project]
    for root in _protected_roots(target, workspace):
        root_chain = _identity_chain(root)
        for place in storage:
            place_chain = _identity_chain(place)
            inside_root = root_chain[0] in place_chain
            contains_root = place.exists() and place_chain[0] in root_chain
            if inside_root or contains_root:
                raise InstallError(f"OBSIDIAN_VAULT_OVERLAPS_TARGET: {root}")


def resolve_obsidian_storage(vault_value: str | None, project_value: str | None) -> tuple[Path, str]:
    """Validate the Obsidian vault and project container used as the only storage root."""
    if not vault_value or not project_value:
        raise InstallError("OBSIDIAN_BINDING_REQUIRED")
    vault = Path(vault_value).expanduser()
    if not vault.is_absolute():
        raise InstallError("OBSIDIAN_VAULT_NOT_ABSOLUTE")
    if not vault.is_dir():
        raise InstallError("OBSIDIAN_VAULT_NOT_FOUND")
    project = Path(project_value)
    if (
        project.is_absolute()
        or not project.parts
        or any(part in {"", ".", ".."} or part.startswith(".") for part in project.parts)
        or "\\" in project_value
        or "\x00" in project_value
    ):
        raise InstallError("OBSIDIAN_PROJECT_INVALID")
    return vault.resolve(strict=True), project.as_posix()


def _worktree_slug(target: Path) -> str:
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        module = _load_template_module("sdd_obsidian_binding", "runtime/obsidian_binding.py")
    finally:
        sys.dont_write_bytecode = previous
    return module.worktree_slug(target)


def obsidian_binding_content(vault: Path, project: str) -> bytes:
    payload = {
        "schema_version": 1,
        "vault_path": str(vault),
        "project_container": project,
        "runtime_subpath": OBSIDIAN_RUNTIME_SUBPATH,
    }
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def obsidian_project_setup(vault: Path, project: str) -> str:
    answer = json.dumps(
        {"vault": str(vault), "project_container": project},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return project_setup().replace("  obsidian: UNRESOLVED", f"  obsidian: {answer}", 1)


def _plan_obsidian_files(
    vault: Path,
    project: str,
    target: Path,
    workspace: dict[str, str],
) -> tuple[dict[str, bytes], str, list[str]]:
    """Return missing vault-relative files, the runtime path and preserved owner edits.

    Controller files and the binding must be byte-identical when present. The
    per-worktree runtime is all-or-nothing so an interrupted or foreign STATE is
    never combined with a fresh journal.
    """
    expected: dict[str, bytes] = {}
    for source in template_files():
        expected[f"{project}/{source.relative_to(TEMPLATE).as_posix()}"] = source.read_bytes()
    expected[f"{project}/{OBSIDIAN_BINDING_PATH}"] = obsidian_binding_content(vault, project)
    planned: dict[str, bytes] = {}
    previously_installed = all(
        _read_project_file_nofollow(vault, f"{project}/{relative}") is not None
        for relative in (OBSIDIAN_BINDING_PATH, f"{CONFIG_ROOT}/PROJECT_SETUP.md")
    )
    # Owner-edited files are trusted only in a container this installer already
    # set up; a fresh container never adopts gate commands it did not write.
    user_configured = (
        {f"{project}/{relative}" for relative in OBSIDIAN_USER_CONFIGURED} if previously_installed else set()
    )
    customized: list[str] = []
    for relative, content in expected.items():
        existing = _read_project_file_nofollow(vault, relative)
        if existing is None:
            planned[relative] = content
        elif existing != content:
            if relative not in user_configured:
                raise InstallError(f"CONFIG_CONFLICT: {relative}")
            customized.append(f"{relative}:sha256:{hashlib.sha256(existing).hexdigest()}")

    setup = f"{project}/{CONFIG_ROOT}/PROJECT_SETUP.md"
    if _read_project_file_nofollow(vault, setup) is None:
        planned[setup] = obsidian_project_setup(vault, project).encode("utf-8")

    runtime = f"{project}/{OBSIDIAN_RUNTIME_SUBPATH}/{_worktree_slug(target)}"
    present = [
        name for name in WORKTREE_RUNTIME_FILES
        if _read_project_file_nofollow(vault, f"{runtime}/{name}") is not None
    ]
    if present and len(present) != len(WORKTREE_RUNTIME_FILES):
        raise InstallError(f"LOCAL_STATE_REQUIRES_REVIEW: {', '.join(f'{runtime}/{name}' for name in present)}")
    if not present:
        planned[f"{runtime}/STATE.md"] = state(workspace).encode("utf-8")
        planned[f"{runtime}/INCIDENTS.md"] = b"# SDD Orchestration Incidents\n\nNo incidents recorded.\n"
        planned[f"{runtime}/ACTION_JOURNAL.json"] = empty_journal(target, workspace).encode("utf-8")
    return planned, runtime, customized


def _apply_obsidian_files(vault: Path, planned: dict[str, bytes], after_base=None) -> None:
    created_files: list[tuple[str, tuple[int, int], bytes]] = []
    created_directories: list[tuple[str, tuple[int, int]]] = []
    try:
        for relative, content in planned.items():
            _create_project_file_nofollow(
                vault,
                relative,
                content,
                created_files,
                created_directories,
                check_tracked=False,
            )
        if after_base is not None:
            after_base()
    except BaseException as error:
        try:
            _rollback_created_paths(vault, created_files, created_directories)
        except InstallError as rollback:
            raise InstallError(f"{error}; {rollback}") from error
        if isinstance(error, FileExistsError):
            raise InstallError(f"CONFIG_DESTINATION_CHANGED: {error.filename}") from error
        raise


def _target_snapshot(target: Path) -> tuple[str, frozenset[str]]:
    status = git(target, "status", "--porcelain", "--untracked-files=all", "--ignored")
    hermes = frozenset(
        str(path.relative_to(target))
        for name in (".hermes", ".hermes.md", "skills-lock.json")
        for path in [target / name]
        if path.exists() or path.is_symlink()
    )
    return status, hermes


def run_obsidian_install(args, target: Path, workspace: dict[str, str]) -> dict[str, object]:
    previous = (CREDENTIAL_FILE_MANAGED, TRACKED_DESTINATIONS_GUARDED)
    _set_storage_mode(obsidian=True)
    try:
        return _run_obsidian_install(args, target, workspace)
    finally:
        _restore_storage_mode(previous)


def _restore_storage_mode(previous: tuple[bool, bool]) -> None:
    global CREDENTIAL_FILE_MANAGED, TRACKED_DESTINATIONS_GUARDED
    CREDENTIAL_FILE_MANAGED, TRACKED_DESTINATIONS_GUARDED = previous


def _run_obsidian_install(args, target: Path, workspace: dict[str, str]) -> dict[str, object]:
    vault, project = resolve_obsidian_storage(args.obsidian_vault, args.obsidian_project)
    _reject_storage_overlap(vault, project, target, workspace)
    container = vault / project
    target_before = _target_snapshot(target)

    def require_target_unchanged() -> None:
        if _target_snapshot(target) != target_before:
            raise InstallError("TARGET_WORKTREE_CHANGED")
    planned, runtime, customized = _plan_obsidian_files(vault, project, target, workspace)
    setup_present = f"{project}/{CONFIG_ROOT}/PROJECT_SETUP.md" not in planned
    if args.typesafe_ai and setup_present:
        _require_typesafe_record_target(container)
    onboarding = onboarding_questions(
        container if setup_present else None,
        args.typesafe_ai,
        args.automatic_jev_governance,
    )
    integration = onboarding["integrations"]["typesafe_ai"]
    if not setup_present:
        integration.update(typesafe_skill_status(container))
        integration["planned_action"] = "INSTALL" if args.typesafe_ai == "install" else (
            "RECORD_NONE" if args.typesafe_ai == "none" else "NONE"
        )
        integration["planned_env_action"] = (
            "CREATE"
            if CREDENTIAL_FILE_MANAGED and args.typesafe_ai == "install" and integration["env_status"] == "ABSENT"
            else "NONE"
        )
    action = integration.get("planned_action")
    if action == "BLOCKED":
        if integration.get("env_status") == "CONFLICT":
            raise InstallError(f"TYPESAFE_ENV_CONFLICT: {integration.get('env_issue')}")
        raise InstallError(f"TYPESAFE_SKILL_CONFLICT: {integration.get('issue') or integration.get('status')}")
    if action == "INSTALL" and setup_present:
        _preflight_typesafe_install(container)
    report: dict[str, object] = {
        "status": "READY" if planned or action != "NONE" else "ALREADY_INITIALIZED",
        "storage": "OBSIDIAN",
        "target": str(target),
        "vault": str(vault),
        "project_container": str(container),
        "worktree_runtime": str(vault / runtime),
        "planned": sorted(planned),
        "target_writes": [],
        "preserved_owner_files": customized,
        "exclude_update_planned": False,
        "applied": False,
        "stack": detect_stack(target),
        "onboarding": onboarding,
        "next_step": "Resolve the project onboarding questions, then configure verified commands in the container GATES.md.",
    }
    integration_result: dict[str, str] = {}

    def apply_integration() -> None:
        """Run the integration with the drift check inside its own rollback."""
        if args.typesafe_ai == "install":
            answer = TYPESAFE_ANSWER_ENABLED if args.automatic_jev_governance else TYPESAFE_ANSWER_DISABLED
            onboarding_after = _render_onboarding_answer(container, "typesafe_ai", answer)
            integration_result["applied_action"] = install_typesafe_skill(
                container, onboarding_after, before_commit=require_target_unchanged
            )
            return
        relative = f"{CONFIG_ROOT}/PROJECT_SETUP.md"
        snapshot = _read_project_regular_snapshot(container, relative)
        if snapshot is None:
            raise InstallError("ONBOARDING_RECORD_MISSING")
        before, owned = snapshot
        after = _render_onboarding_answer(container, "typesafe_ai", "none", source=before)
        _overwrite_project_file_nofollow(container, relative, owned, after, expected=before)
        try:
            require_target_unchanged()
        except BaseException:
            _overwrite_project_file_nofollow(container, relative, owned, before, expected=after)
            raise
        integration_result["applied_action"] = "RECORD_NONE"

    integration_requested = bool(args.apply and args.typesafe_ai and action != "NONE")

    def finish_inside_transaction() -> None:
        # Checked before and after the integration and inside every rollback,
        # so a changed repository undoes every vault path this run wrote.
        require_target_unchanged()
        if integration_requested:
            apply_integration()
        else:
            require_target_unchanged()

    if args.apply and (planned or integration_requested):
        _apply_obsidian_files(vault, planned, finish_inside_transaction)
        report["applied"] = True
        report["status"] = "APPLIED"
        report["onboarding"] = onboarding_questions(container)
        if integration_result:
            report["onboarding"]["integrations"]["typesafe_ai"]["applied_action"] = integration_result["applied_action"]
    if _target_snapshot(target) != target_before:
        raise InstallError("TARGET_WORKTREE_CHANGED")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default=".", help="existing Git worktree root (default: current directory)")
    parser.add_argument("--obsidian-vault", help="absolute path to the Obsidian vault")
    parser.add_argument("--obsidian-project", help="project container path relative to the Obsidian vault")
    parser.add_argument(
        "--local-storage",
        action="store_true",
        help="legacy mode: install the controller inside the target worktree instead of Obsidian",
    )
    parser.add_argument("--apply", action="store_true", help="write files after a successful dry run")
    parser.add_argument(
        "--typesafe-ai",
        choices=("install", "none"),
        help="explicitly install the project-local TypeSafe skill or record that it is not used",
    )
    parser.add_argument(
        "--automatic-jev-governance",
        action="store_true",
        help="authorize automatic, potentially billed Jev classifications (requires --typesafe-ai install)",
    )
    parser.add_argument("--json", action="store_true", help="emit a machine-readable report")
    args = parser.parse_args()
    if args.automatic_jev_governance and args.typesafe_ai != "install":
        report = {
            "status": "BLOCKED",
            "reason": "AUTOMATIC_JEV_GOVERNANCE_REQUIRES_TYPESAFE_INSTALL",
        }
        print(
            json.dumps(report, ensure_ascii=False) if args.json else f"BLOCKED: {report['reason']}",
            file=sys.stderr,
        )
        return 2
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
    if importlib.util.find_spec("jsonschema") is None:
        report = {
            "status": "BLOCKED",
            "reason": "JSONSCHEMA_REQUIRED",
            "next_step": "Install the jsonschema package for this Python interpreter, then rerun the installer.",
        }
        print(
            json.dumps(report, ensure_ascii=False) if args.json else "BLOCKED: JSONSCHEMA_REQUIRED",
            file=sys.stderr,
        )
        return 2
    try:
        target, workspace = require_root(args.target)
        if args.local_storage and (args.obsidian_vault or args.obsidian_project):
            raise InstallError("STORAGE_MODE_CONFLICT")
        _set_storage_mode(obsidian=not args.local_storage)
        if not args.local_storage:
            report = run_obsidian_install(args, target, workspace)
            if args.json:
                print(json.dumps(report, ensure_ascii=False))
            else:
                print(f'{report["status"]}: {len(report["planned"])} files planned in {report["project_container"]}')
            return 0
        planned, existing_state = _plan_base_files(target)
        exclude_planned = exclude_update_planned(workspace)
        if args.typesafe_ai and existing_state:
            _require_typesafe_record_target(target)
        onboarding = onboarding_questions(
            target,
            args.typesafe_ai,
            args.automatic_jev_governance,
        )
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
            "status": "READY" if planned or exclude_planned or integration_planned else "ALREADY_INITIALIZED",
            "target": str(target),
            "planned": planned,
            "exclude_update_planned": exclude_planned,
            "applied": False,
            "stack": detect_stack(target),
            "onboarding": onboarding,
            "next_step": "Resolve the project onboarding questions, then configure verified commands in GATES.md.",
        }
        integration_requested = bool(
            args.apply and args.typesafe_ai and typesafe_action != "NONE"
        )
        integration_result: dict[str, str] = {}

        def apply_integration() -> None:
            if args.typesafe_ai == "install":
                answer = (
                    TYPESAFE_ANSWER_ENABLED
                    if args.automatic_jev_governance
                    else TYPESAFE_ANSWER_DISABLED
                )
                onboarding_after = _render_onboarding_answer(target, "typesafe_ai", answer)
                integration_result["applied_action"] = install_typesafe_skill(target, onboarding_after)
            else:
                _write_onboarding_answer(target, "typesafe_ai", "none")
                integration_result["applied_action"] = "RECORD_NONE"

        if args.apply and (planned or exclude_planned or integration_requested):
            cleanup_warnings = _apply_base_install(
                target,
                workspace,
                planned,
                apply_integration if integration_requested else None,
            )
            if cleanup_warnings:
                report["warnings"] = [
                    f"LOCK_CLEANUP_REQUIRES_REVIEW: {warning}"
                    for warning in cleanup_warnings
                ]
            report["applied"] = True
            report["exclude_update_planned"] = False
        if integration_result:
            updated_onboarding = onboarding_questions(target)
            integrations = updated_onboarding["integrations"]
            if not isinstance(integrations, dict) or not isinstance(integrations.get("typesafe_ai"), dict):
                raise InstallError("TYPESAFE_REPORT_INVALID")
            integrations["typesafe_ai"]["applied_action"] = integration_result["applied_action"]
            report["onboarding"] = updated_onboarding
        elif report["applied"]:
            report["onboarding"] = onboarding_questions(target)
        if report["applied"]:
            report["status"] = "APPLIED"
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
        elif str(error) == "ATTACHED_BRANCH_REQUIRED":
            report["next_step"] = "Switch the target worktree to an attached branch, then rerun the installer."
        elif str(error) == "OBSIDIAN_BINDING_REQUIRED":
            report["next_step"] = (
                "Pass --obsidian-vault <absolute vault> and --obsidian-project <container relative to the vault>; "
                "all orchestrator data is stored there and nothing is written to the project."
            )
        print(json.dumps(report, ensure_ascii=False) if args.json else f'BLOCKED: {error}', file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
