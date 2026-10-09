"""Every Git subprocess call of the installer."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .errors import InstallError
from .mode import MODE


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
    if not MODE.tracked_destinations_guarded:
        return False
    result = subprocess.run(
        ["git", "-C", str(target), "ls-files", "--error-unmatch", "--", relative],
        text=True, capture_output=True, timeout=20, check=False,
    )
    return result.returncode == 0


def tracked_under(target: Path, relative: str) -> bool:
    if not MODE.tracked_destinations_guarded or not _inside_work_tree(target):
        # Decided by `rev-parse`, never by localized stderr text.
        return False
    result = subprocess.run(
        ["git", "-C", str(target), "ls-files", "--", relative],
        text=True, capture_output=True, timeout=20, check=False,
    )
    if result.returncode:
        raise InstallError((result.stderr or result.stdout).strip() or "Git preflight failed.")
    return bool(result.stdout.strip())


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


def _target_snapshot(target: Path) -> tuple[str, frozenset[str]]:
    status = git(target, "status", "--porcelain", "--untracked-files=all", "--ignored")
    hermes = frozenset(
        str(path.relative_to(target))
        for name in (".hermes", ".hermes.md", "skills-lock.json")
        for path in [target / name]
        if path.exists() or path.is_symlink()
    )
    return status, hermes
