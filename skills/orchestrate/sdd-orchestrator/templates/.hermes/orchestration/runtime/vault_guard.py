"""Write containment and baseline preservation for the Obsidian vault.

The repository is protected by a git baseline (`git status --short`, `git diff
--name-only`, ...). Once orchestration runtime and knowledge live in the vault,
that baseline no longer covers them: git sees nothing outside the repo. This
module restores the equivalent guarantee for the vault.

Two independent protections:

1. Containment — every write target must resolve inside the bound
   ``project_container``. Anything else (a sibling project, the shared Workflow
   area, the wider filesystem) is refused and needs explicit human
   authorisation.
2. Baseline — a content snapshot of the container taken before the demand and
   compared at the end. Violations are REPORTED, never rolled back, matching the
   repository regime.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence

import obsidian_binding
from obsidian_binding import Binding

#: Directories never included in the knowledge baseline.
#: Runtime is excluded because STATE/journal are rewritten on every transition;
#: including them would make preservation checks always fail.
_ALWAYS_EXCLUDED = (".obsidian", ".trash", ".git")

_HASH_CHUNK = 65536


class VaultWriteRefused(Exception):
    """A write was aimed outside the bound project container."""

    def __init__(self, code: str, message: str, *, target: str = "", container: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.target = target
        self.container = container

    def __str__(self) -> str:  # pragma: no cover - trivial formatting
        return f"{self.code}: {self.message} | target={self.target} | container={self.container}"


class VaultBaselineViolation(Exception):
    """The container changed in ways the demand did not authorise."""

    def __init__(self, changes: Sequence["BaselineChange"]):
        self.changes = list(changes)
        summary = ", ".join(f"{c.kind} {c.relative_path}" for c in self.changes[:10])
        extra = "" if len(self.changes) <= 10 else f" (+{len(self.changes) - 10} more)"
        super().__init__(f"Vault baseline not preserved: {summary}{extra}")


@dataclass(frozen=True)
class BaselineChange:
    kind: str  # ADDED | REMOVED | MODIFIED
    relative_path: str


def assert_writable(binding: Binding, target: Path) -> Path:
    """Refuse any write outside the bound project container.

    Returns the resolved target so callers can write to the canonical path
    rather than re-resolving it (and re-introducing a symlink window).
    """
    if not obsidian_binding.is_inside_container(binding, target):
        raise VaultWriteRefused(
            "VAULT_WRITE_OUTSIDE_CONTAINER",
            "Write refused: target is outside this repository's project container. "
            "Writing there requires explicit human authorisation.",
            target=str(target),
            container=str(binding.container_path),
        )
    return Path(os.path.abspath(str(target)))


def capture_vault_baseline(binding: Binding) -> Dict[str, str]:
    """Snapshot the container's knowledge files as ``relative path -> sha256``.

    Content hashes, not mtimes: Obsidian and sync tools touch mtimes without
    changing content, which would produce false violations.
    """
    container = binding.container_path
    baseline: Dict[str, str] = {}
    if not container.is_dir():
        return baseline

    runtime_root = binding.runtime_root

    for current_root, dirnames, filenames in os.walk(container):
        current = Path(current_root)
        if _is_within(current, runtime_root):
            dirnames[:] = []
            continue
        dirnames[:] = [
            d
            for d in dirnames
            if d.lower() not in _ALWAYS_EXCLUDED
            and not _is_within(current / d, runtime_root)
        ]
        for filename in filenames:
            if filename == ".DS_Store":
                continue
            path = current / filename
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(container).as_posix()
            baseline[relative] = _sha256(path)
    return baseline


def diff_baseline(
    before: Dict[str, str], after: Dict[str, str]
) -> List[BaselineChange]:
    changes: List[BaselineChange] = []
    for relative in sorted(set(before) | set(after)):
        old = before.get(relative)
        new = after.get(relative)
        if old == new:
            continue
        if old is None:
            changes.append(BaselineChange("ADDED", relative))
        elif new is None:
            changes.append(BaselineChange("REMOVED", relative))
        else:
            changes.append(BaselineChange("MODIFIED", relative))
    return changes


def assert_baseline_preserved(
    before: Dict[str, str], after: Dict[str, str]
) -> None:
    changes = diff_baseline(before, after)
    if changes:
        raise VaultBaselineViolation(changes)


def _is_within(path: Path, ancestor: Path) -> bool:
    path_parts = Path(os.path.abspath(str(path))).parts
    ancestor_parts = Path(os.path.abspath(str(ancestor))).parts
    return path_parts[: len(ancestor_parts)] == ancestor_parts


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()
