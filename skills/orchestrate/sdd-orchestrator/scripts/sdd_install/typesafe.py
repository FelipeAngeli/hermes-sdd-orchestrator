"""Vetted TypeSafe skill installation, credential placeholder and rollback."""
from __future__ import annotations

import errno
import hashlib
import json
import os
import secrets
import stat
from pathlib import Path
from typing import Callable

from .constants import (
    CONFIG_ROOT,
    TYPESAFE_ENV_CONTENT,
    TYPESAFE_ENV_PATH,
    TYPESAFE_OFFICIAL_COMMAND,
    TYPESAFE_SKILL_PATH,
    TYPESAFE_SKILL_ROOT,
    TYPESAFE_SOURCE_REF,
    TYPESAFE_TRUSTED_DIGEST,
    TYPESAFE_UPSTREAM_HASH,
    TYPESAFE_VENDOR,
)
from .errors import InstallError, _load_unique_json
from .fsops import (
    _close_descriptor,
    _create_project_directory_owned,
    _create_project_regular_owned,
    _identity,
    _open_project_directory_nofollow,
    _overwrite_project_file_nofollow,
    _path_identity,
    _read_project_regular_snapshot,
    _remove_owned_directory_path,
    _unlink_owned_path,
    _write_all,
    reject_symlinks,
)
from .gitops import is_tracked, tracked_under
from .mode import MODE


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


def _typesafe_env_ready(status: dict[str, object]) -> bool:
    """A credential file is required only where the installer manages one."""
    if status.get("env_status") == "PRESENT":
        return True
    return not MODE.credential_file_managed and status.get("env_status") == "ABSENT"


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
    lock_path = target / MODE.typesafe_lock_path
    lock_entry: object | None = None
    issue: str | None = None

    try:
        reject_symlinks(target, TYPESAFE_SKILL_PATH)
        reject_symlinks(target, MODE.typesafe_lock_path)
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
        tracked_under(target, TYPESAFE_SKILL_ROOT) or is_tracked(target, MODE.typesafe_lock_path)
    ):
        status = "CONFLICT"
        issue = "TRACKED_DESTINATION_PATH"

    return {
        "status": status,
        "skill_path": TYPESAFE_SKILL_PATH,
        "lock_path": MODE.typesafe_lock_path,
        "lock_entry": "PRESENT" if lock_entry is not None else "ABSENT",
        "issue": issue,
        "official_command": list(TYPESAFE_OFFICIAL_COMMAND),
        **typesafe_env_status(target),
    }


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
    lock_path = target / MODE.typesafe_lock_path
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
            lock_snapshot = _read_project_regular_snapshot(target, MODE.typesafe_lock_path)
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
                    MODE.typesafe_lock_path,
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
    if is_tracked(target, MODE.typesafe_lock_path):
        raise InstallError(f"TRACKED_DESTINATION_PATH: {MODE.typesafe_lock_path}")
    reject_symlinks(target, TYPESAFE_SKILL_PATH)
    reject_symlinks(target, MODE.typesafe_lock_path)
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
    if not MODE.credential_file_managed and environment["env_status"] == "ABSENT":
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

    lock_path = target / MODE.typesafe_lock_path
    lock_snapshot = _read_project_regular_snapshot(target, MODE.typesafe_lock_path)
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
            lock_identity = _create_project_regular_owned(target, MODE.typesafe_lock_path, merged_lock)
        else:
            if lock_identity_before is None:
                raise InstallError("TYPESAFE_LOCK_CHANGED_DURING_APPLY")
            lock_identity = lock_identity_before
            _overwrite_project_file_nofollow(
                target,
                MODE.typesafe_lock_path,
                lock_identity,
                merged_lock,
                expected=lock_before,
            )
        if lock_before is not None:
            before = _load_unique_json(lock_before.decode("utf-8"))
            lock_after_snapshot = _read_project_regular_snapshot(target, MODE.typesafe_lock_path)
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
