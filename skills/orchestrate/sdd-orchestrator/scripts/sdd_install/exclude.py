"""Managed `.git/info/exclude` entries (repository-local storage only)."""
from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

from .errors import InstallError
from .fsops import _close_descriptor
from .templates import managed_exclude_entries


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
