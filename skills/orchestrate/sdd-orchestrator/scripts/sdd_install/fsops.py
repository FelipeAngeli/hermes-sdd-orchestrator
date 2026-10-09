"""Descriptor-anchored, no-follow filesystem primitives (the only ctypes use)."""
from __future__ import annotations

import ctypes
import errno
import os
import secrets
import shutil
import stat
import sys
from pathlib import Path

from .errors import InstallError
from .gitops import is_tracked


def reject_symlinks(target: Path, relative: str) -> None:
    current = target
    for part in Path(relative).parts:
        current /= part
        if current.is_symlink():
            raise InstallError(f"SYMLINK_REJECTED: {current}")


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


def _close_descriptor(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass


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
