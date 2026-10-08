"""Repository-local (`--local-storage`) planning and apply transaction."""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from .constants import CONFIG_ROOT, STATE_PATHS, TEMPLATE, TYPESAFE_ANSWER_DISABLED, TYPESAFE_ANSWER_ENABLED
from .errors import InstallError
from .exclude import (
    _open_git_info,
    _portable_exclude_path,
    _read_exclude,
    _read_portable_exclude,
    _render_exclude,
    exclude_update_planned,
)
from .fsops import (
    _acquire_lock,
    _acquire_portable_lock,
    _close_descriptor,
    _create_project_file_nofollow,
    _identity,
    _identity_at,
    _path_identity,
    _read_all,
    _read_project_file_nofollow,
    _rollback_created_paths,
    _unlink_owned_at,
    _unlink_owned_path,
    _write_all,
)
from .gitops import is_tracked
from .onboarding import (
    _render_onboarding_answer,
    _require_typesafe_record_target,
    _write_onboarding_answer,
    onboarding_questions,
)
from .templates import detect_stack, empty_journal, project_setup, state, template_files
from .typesafe import _preflight_typesafe_install, install_typesafe_skill


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


def run_local_install(args, target: Path, workspace: dict[str, str]) -> dict[str, object]:
    """Plan and, with --apply, commit the repository-local installation."""
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
    return report
