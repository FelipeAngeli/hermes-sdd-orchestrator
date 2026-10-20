"""Controller isolation: physical-location checks and the move-out exit.

These helpers decide whether a writing worker can reach the controller and build
the exact commands that move a repository-local controller into an Obsidian
container. They are kept out of ``sdd.py`` because they are a cohesive unit, not
to reduce the controller entry point.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


def _identity(path: Path) -> tuple[int, int] | None:
    try:
        info = os.stat(path)
    except OSError:
        return None
    return info.st_dev, info.st_ino


def controller_inside_repository(repository: Path, orchestration_parent: Path) -> bool:
    """True when the controller's ``.hermes`` directory physically lives inside ``repository``.

    This is the launcher's criterion (``executor_launch.check_controller_isolation``), not the
    storage label: a ``--local-storage`` install that also carries a ``.hermes/obsidian.json``
    reports ``OBSIDIAN`` storage while its controller still sits in the repository. The walk
    compares device and inode of every ancestor, so a differently-cased spelling of the same
    directory (case-insensitive macOS volumes) or a symlinked path still matches.
    """
    target = _identity(repository)
    if target is None:
        return False
    current = orchestration_parent
    while True:
        if _identity(current) == target:
            return True
        if current.parent == current:
            return False
        current = current.parent


def installer_mode_arguments(controller_root: Path) -> list[str]:
    """Installer flags that reproduce this controller's own storage (``--upgrade`` keeps it)."""
    try:
        import obsidian_binding

        source = obsidian_binding._installed_container_binding()
        binding = obsidian_binding.load_path(source) if source is not None else None
    except Exception:  # an unreadable binding falls back to the placeholders the user fills
        binding = None
    if binding is not None and not (controller_root / ".git").exists():
        return ["--obsidian-vault", str(binding.raw.get("vault_path") or binding.vault_path),
                "--obsidian-project", str(binding.project_container)]
    if (controller_root / ".git").exists():
        return ["--local-storage"]
    return ["--obsidian-vault", "<vault>", "--obsidian-project", "<project>"]


def installer_command(repository: Path, *arguments: str) -> str:
    """The skill's installer, whose path only the Hermes session knows (``<installed-skill>``)."""
    return shlex.join([sys.executable, "<installed-skill>/scripts/install_project.py", "--target", str(repository), *arguments, "--json"])


def demand_exit_prefix(stage: str, sdd_command, reason: str) -> list[str]:
    """``abandon`` when a demand is open (it also archives an undispatched PREPARED action)."""
    if stage in {"IDLE", "DONE"}:
        return [sdd_command("close")] if stage == "DONE" else []
    return [sdd_command("abandon", "--reason", reason, "--quote", "<user words>")]


def restart_command(script: str | Path) -> str:
    return shlex.join([sys.executable, str(script), "start", "--ticket", "<ticket-id>", "--title", "<title>", "--objective", "<objective>"])


def move_out_commands(
    ctx,
    *,
    orchestration: Path,
    controller_root: Path,
    controller_policy_files: dict[str, Path],
    sdd_command,
) -> list[str]:
    """Abandon the demand, install the controller in an Obsidian container, set the in-repo one aside, restart.

    ``migrate_to_vault.py`` is not this exit: it moves STATE and the journal but keeps
    ``runtime/*.py`` and ``policies/`` in the repository by design, refuses an open demand
    and needs a binding a ``--local-storage`` install does not have. A fresh Obsidian install
    is what puts the controller out of every worker's reach.
    """
    repository = ctx.repo
    container = "<vault>/<project>"
    backup = f"{container}/.hermes-local-controller-backup"
    commands = demand_exit_prefix(ctx.stage, sdd_command, "move the controller out of the repository (CONTROLLER_WRITABLE_BY_WORKER)")
    install = ["--obsidian-vault", "<vault>", "--obsidian-project", "<project>"]
    commands += [installer_command(repository, *install), installer_command(repository, *install, "--apply")]
    if controller_inside_repository(repository, orchestration.parent):
        commands.append(shlex.join(["mkdir", "-p", backup]))
        commands.append(shlex.join(["mv", str(orchestration), f"{backup}/orchestration"]))
        # A repository-local binding would outrank the container's for operator CLIs, and the
        # card points at the old controller: both leave with it. Nothing else under .hermes moves
        # (a TypeSafe .env stays out of the vault).
        for leftover in (orchestration.parent / "obsidian.json", controller_root / ".hermes.md"):
            if leftover.is_file():
                commands.append(shlex.join(["mv", str(leftover), f"{backup}/{leftover.name}"]))
    new_sdd = f"{container}/Hermes/orchestration/runtime/sdd.py"
    # The new container holds the template GATES.md/EXECUTORS.md (the in-repository copies were
    # writable by workers, so they are not carried over). A runtime the binding already kept in
    # the vault still pins the old digests, and `start` refuses a policy that differs from its
    # last pin: the user confirms the new files first.
    for name in sorted(controller_policy_files):
        commands.append(shlex.join([sys.executable, new_sdd, "confirm-policy", "--name", name, "--by", "requester", "--quote", "<user words>"]))
    commands.append(restart_command(new_sdd))
    return commands


def controller_isolation_error_data(
    storage: str,
    stage: str,
    ctx,
    *,
    orchestration: Path,
    controller_root: Path,
    controller_policy_files: dict[str, Path],
    sdd_command,
) -> dict[str, Any] | None:
    """Return the controller-writable-by-worker refusal data, or None when allowed."""
    inside = controller_inside_repository(ctx.repo, orchestration.parent) if ctx is not None else storage == "LOCAL"
    if not inside:
        return None
    import executor_launch

    try:
        settings = executor_launch.stage_settings(executor_launch.load_policy(), stage, executor=None, model=None, timeout=None)
    except executor_launch.LaunchError:
        return None
    if not executor_launch.is_writing_worker(settings):
        return None
    if ctx is not None:
        commands = move_out_commands(
            ctx,
            orchestration=orchestration,
            controller_root=controller_root,
            controller_policy_files=controller_policy_files,
            sdd_command=sdd_command,
        )
    else:
        commands = [installer_command(controller_root, "--obsidian-vault", "<vault>", "--obsidian-project", "<project>", "--apply"),
                    restart_command("<vault>/<project>/Hermes/orchestration/runtime/sdd.py")]
    return {
        "code": "CONTROLLER_WRITABLE_BY_WORKER",
        "message": (
            f"{stage} runs a writing worker ({settings['executor']}) and the controller lives inside the repository it can write "
            f"({orchestration}): it could rewrite policies/GATES.md (whose commands run on the host), policies/EXECUTORS.md, the "
            "controller runtime, STATE or the journal together with every hash they are checked against."
        ),
        "next_step": (
            "Nothing was dispatched. Ask the user for an Obsidian vault and a project container in it, then run "
            "`exit_commands` in order, stopping at the first non-zero exit: `abandon` closes the demand with their words "
            "(nothing in the worktree is reverted; an undispatched action is archived), the installer's dry run must "
            "return READY before `--apply` installs the controller in the container, which is never a writable root "
            "for a worker, the in-repository controller is moved aside into the container, the user confirms the new "
            "controller's template GATES.md/EXECUTORS.md and its `start` re-runs the demand (configure the gate rows "
            "again; the old in-repository copies were writable by workers and are not adopted). Fill `<installed-skill>` with this skill's installed path and `<vault>`/"
            "`<project>` with the user's answer. Read-only stages (SPECIFY, CLARIFY, PLAN, TASKS, REVIEW) would still "
            "run here; only a writing stage is refused, because with the controller inside the worker's writable tree "
            "no detection can hold."
        ),
        "next_command": commands[0],
        "extra": {
            "exit_commands": commands,
            "stage": stage,
            "executor": settings["executor"],
            "controller_root": str(controller_root),
        },
    }
