"""Atomic, no-follow single-file replace and remove used by `--upgrade`."""
from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

from .errors import InstallError
from .fsops import (
    _close_descriptor,
    _identity,
    _identity_at,
    _open_project_parent_nofollow,
    _read_all,
    _read_project_regular_snapshot,
    _unlink_owned_at,
    _write_all,
)


def _replace_file(
    root: Path,
    relative: str,
    expected: bytes,
    content: bytes,
    expected_identity: tuple[int, int] | None = None,
) -> tuple[tuple[int, int], int]:
    """Replace `relative` through a temporary sibling and rename, keeping its mode.

    Returns the new identity and the preserved mode. The current bytes (and
    identity, when given) must still be the planned ones.
    """
    name = Path(relative).name
    temporary = f".{name}.sdd-upgrade-{os.getpid()}-{secrets.token_hex(8)}"
    if os.name != "posix":
        path = root / relative
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or path.read_bytes() != expected:
            raise InstallError(f"UPGRADE_STATE_CHANGED: {relative}")
        mode = stat.S_IMODE(metadata.st_mode)
        staging = path.with_name(temporary)
        staging.write_bytes(content)
        staging.chmod(mode)
        os.replace(staging, path)
        return _identity(path.lstat()), mode
    parent = _open_project_parent_nofollow(root, relative, create=False)
    created = False
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or _read_all(descriptor) != expected:
                raise InstallError(f"UPGRADE_STATE_CHANGED: {relative}")
        finally:
            _close_descriptor(descriptor)
        before = _identity(metadata)
        if expected_identity is not None and before != expected_identity:
            raise InstallError(f"UPGRADE_STATE_CHANGED: {relative}")
        mode = stat.S_IMODE(metadata.st_mode)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=parent)
        created = True
        try:
            os.fchmod(descriptor, mode)
            _write_all(descriptor, content)
            os.fsync(descriptor)
        finally:
            _close_descriptor(descriptor)
        if _identity_at(parent, name) != before:
            raise InstallError(f"UPGRADE_STATE_CHANGED: {relative}")
        os.rename(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        created = False
        os.fsync(parent)
        after = _identity_at(parent, name)
        if after is None:
            raise InstallError(f"UPGRADE_STATE_CHANGED: {relative}")
        return after, mode
    finally:
        if created:
            try:
                os.unlink(temporary, dir_fd=parent)
            except OSError:
                pass
        _close_descriptor(parent)


def _remove_file(root: Path, relative: str, expected: bytes) -> tuple[tuple[int, int], int]:
    snapshot = _read_project_regular_snapshot(root, relative)
    if snapshot is None or snapshot[0] != expected:
        raise InstallError(f"UPGRADE_STATE_CHANGED: {relative}")
    owned = snapshot[1]
    mode = stat.S_IMODE((root / relative).lstat().st_mode)
    if os.name != "posix":
        (root / relative).unlink()
        return owned, mode
    parent = _open_project_parent_nofollow(root, relative, create=False)
    try:
        _unlink_owned_at(parent, Path(relative).name, owned, f"UPGRADE_STATE_CHANGED: {relative}")
    finally:
        _close_descriptor(parent)
    return owned, mode
