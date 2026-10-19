"""Obsidian controller layout for `--upgrade`: legacy hidden roots to visible ones.

An Obsidian container installed before the visible layout keeps its controller
in `.hermes/` and its per-worktree runtime in `.hermes-runtime/`. Upgrading it
renames both into `Hermes/` and `Hermes Runtime/` and records the runtime in
the binding. Manifest paths stay logical (`.hermes/...`, the template layout);
only the on-disk location changes.

The migration runs inside the upgrade transaction, after every file change and
before the manifest commit point, in an order that keeps each interrupted state
recoverable by the next `--upgrade --apply`:

1. the binding is rewritten to name `Hermes Runtime` (journaled REPLACE);
2. `.hermes-runtime` is renamed to `Hermes Runtime`;
3. `.hermes` is renamed to `Hermes`.

A process killed after (1) or (2) leaves `.hermes` in place, so the next run
still sees the legacy layout and completes the remaining steps; one killed
after (3) leaves the visible layout with the old manifest, a plain upgrade.
Each rename is no-follow, refuses an existing destination and is undone in
reverse on failure. Two controller roots, or a runtime under both names, block
before any write.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from .constants import (
    LOCAL_CONTROLLER_ROOT,
    OBSIDIAN_BINDING_PATH,
    OBSIDIAN_CONTROLLER_ROOT,
    OBSIDIAN_RUNTIME_SUBPATH,
    OBSIDIAN_VISIBLE_RUNTIME_SUBPATH,
)
from .errors import InstallError, _load_unique_json
from .fsops import _identity, _identity_at, _read_project_file_nofollow, _rename_noreplace_at
from .mode import MODE

LOCAL = "LOCAL"
LEGACY = "LEGACY"
VISIBLE = "VISIBLE"
CONFLICT = "UPGRADE_OBSIDIAN_LAYOUT_CONFLICT"
#: Mirrors obsidian_binding._RESERVED_RUNTIME_ROOTS: a runtime never aliases the controller or the wiki.
_RESERVED_RUNTIME_ROOTS = frozenset({
    ".hermes", "hermes", ".obsidian", ".trash", ".git",
    "raw", "entities", "concepts", "comparisons", "queries", "_archive", "_meta",
})


def _kind(path: Path) -> str:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return "absent"
    if stat.S_ISLNK(metadata.st_mode):
        return "symlink"
    return "directory" if stat.S_ISDIR(metadata.st_mode) else "other"


def layout(root: Path, storage: str) -> str:
    """LOCAL, LEGACY or VISIBLE; anything ambiguous blocks before a write."""
    if storage == LOCAL:
        return LOCAL
    legacy = _kind(root / LOCAL_CONTROLLER_ROOT)
    visible = _kind(root / OBSIDIAN_CONTROLLER_ROOT)
    for name, kind in ((LOCAL_CONTROLLER_ROOT, legacy), (OBSIDIAN_CONTROLLER_ROOT, visible)):
        if kind in {"symlink", "other"}:
            raise InstallError(f"{CONFLICT}: {root / name} is not a real directory")
    if legacy == "directory" and visible == "directory":
        raise InstallError(
            f"{CONFLICT}: both {LOCAL_CONTROLLER_ROOT} and {OBSIDIAN_CONTROLLER_ROOT} exist in {root}",
            next_step=(
                "Keep exactly one controller root: compare both trees, keep the one this installer wrote "
                "(its orchestration/INSTALL_MANIFEST.json matches), move the other out of the vault, then rerun."
            ),
        )
    return LEGACY if legacy == "directory" else VISIBLE


def physical(current: str, relative: str) -> str:
    """Map a logical template path to where it lives in this layout."""
    return MODE.destination(relative) if current == VISIBLE else relative


def runtime_subpath(root: Path, current: str) -> str:
    """The runtime directory the installed binding names."""
    if current == LOCAL:
        return OBSIDIAN_RUNTIME_SUBPATH
    raw = _read_project_file_nofollow(root, physical(current, OBSIDIAN_BINDING_PATH))
    try:
        value = _load_unique_json(raw.decode("utf-8")) if raw is not None else {}
    except (UnicodeError, ValueError) as error:
        raise InstallError(f"{CONFLICT}: the Obsidian binding is not valid JSON ({error})") from error
    subpath = value.get("runtime_subpath") if isinstance(value, dict) else None
    if not isinstance(subpath, str) or not subpath:
        return OBSIDIAN_RUNTIME_SUBPATH
    parts = subpath.split("/")
    if subpath.startswith("/") or "\x00" in subpath or "\\" in subpath or any(p in {"", ".", ".."} for p in parts) \
            or parts[0].casefold() in _RESERVED_RUNTIME_ROOTS:
        raise InstallError(f"{CONFLICT}: runtime_subpath {subpath!r} is not a runtime folder inside the container")
    return subpath


def runtime_roots(root: Path, current: str) -> list[Path]:
    """Every runtime directory an upgrade must find idle, in either name."""
    names = {runtime_subpath(root, current)}
    if current == LEGACY:
        names |= {OBSIDIAN_RUNTIME_SUBPATH, OBSIDIAN_VISIBLE_RUNTIME_SUBPATH}
    return [root / name for name in sorted(names)]


def plan(root: Path, current: str) -> dict[str, object] | None:
    """What a LEGACY container needs; None for any other layout. Reads only."""
    if current != LEGACY:
        return None
    binding_raw = _read_project_file_nofollow(root, OBSIDIAN_BINDING_PATH)
    if binding_raw is None:
        raise InstallError(f"{CONFLICT}: {OBSIDIAN_BINDING_PATH} is missing")
    binding = _load_unique_json(binding_raw.decode("utf-8"))
    if not isinstance(binding, dict):
        raise InstallError(f"{CONFLICT}: {OBSIDIAN_BINDING_PATH} is not a JSON object")
    named = runtime_subpath(root, current)
    moves: list[tuple[str, str]] = []
    if named in {OBSIDIAN_RUNTIME_SUBPATH, OBSIDIAN_VISIBLE_RUNTIME_SUBPATH}:
        legacy_runtime = _kind(root / OBSIDIAN_RUNTIME_SUBPATH)
        visible_runtime = _kind(root / OBSIDIAN_VISIBLE_RUNTIME_SUBPATH)
        for name, kind in ((OBSIDIAN_RUNTIME_SUBPATH, legacy_runtime), (OBSIDIAN_VISIBLE_RUNTIME_SUBPATH, visible_runtime)):
            if kind in {"symlink", "other"}:
                raise InstallError(f"{CONFLICT}: {root / name} is not a real directory")
        if legacy_runtime == "directory" and visible_runtime == "directory":
            raise InstallError(
                f"{CONFLICT}: both {OBSIDIAN_RUNTIME_SUBPATH} and {OBSIDIAN_VISIBLE_RUNTIME_SUBPATH} exist in {root}",
                next_step="Keep exactly one runtime directory (move the stale one out of the vault), then rerun.",
            )
        if legacy_runtime == "directory":
            moves.append((OBSIDIAN_RUNTIME_SUBPATH, OBSIDIAN_VISIBLE_RUNTIME_SUBPATH))
        named = OBSIDIAN_VISIBLE_RUNTIME_SUBPATH
    moves.append((LOCAL_CONTROLLER_ROOT, OBSIDIAN_CONTROLLER_ROOT))
    rewritten = dict(binding, runtime_subpath=named)
    binding_after = (json.dumps(rewritten, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return {
        "binding_before": binding_raw,
        "binding_after": binding_after if binding_after != binding_raw else None,
        "moves": moves,
    }


def describe(migration: dict[str, object] | None) -> dict[str, object] | None:
    """The report/digest view of a migration plan (no file bytes)."""
    if migration is None:
        return None
    return {
        "renames": [{"from": source, "to": destination} for source, destination in migration["moves"]],  # type: ignore[union-attr]
        "binding_rewrite": migration["binding_after"] is not None,
        "hooks": "Profile hook commands naming <container>/.hermes/orchestration must be re-pointed to "
                 "<container>/Hermes/orchestration after the upgrade (Hermes then asks for consent again).",
    }


def rename_directory(root: Path, source: str, destination: str) -> tuple[int, int]:
    """Rename one direct child directory of `root` atomically, never over an entry."""
    if os.name != "posix":
        raise InstallError(f"{CONFLICT}: the layout migration needs an atomic POSIX rename")
    parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        metadata = os.stat(source, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(metadata.st_mode):
            raise InstallError(f"UPGRADE_STATE_CHANGED: {source} is not a directory")
        identity = _identity(metadata)
        if _identity_at(parent, destination) is not None or not _rename_noreplace_at(parent, source, destination):
            raise InstallError(f"{CONFLICT}: {destination} appeared during the upgrade")
        if _identity_at(parent, destination) != identity:
            raise InstallError(f"UPGRADE_STATE_CHANGED: {destination} changed during the rename")
        os.fsync(parent)
        return identity
    finally:
        os.close(parent)


def undo_rename(root: Path, source: str, destination: str, identity: tuple[int, int]) -> None:
    """Move `destination` back to `source` only if it is still the directory we moved."""
    parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if _identity_at(parent, destination) != identity:
            raise InstallError(f"UPGRADE_STATE_CHANGED: {destination} is no longer the migrated directory")
        if not _rename_noreplace_at(parent, destination, source):
            raise InstallError(f"UPGRADE_STATE_CHANGED: {source} reappeared; {destination} kept")
        os.fsync(parent)
    finally:
        os.close(parent)
