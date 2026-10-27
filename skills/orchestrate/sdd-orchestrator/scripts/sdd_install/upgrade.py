"""`--upgrade`: bring an existing controller to the bundled template, safely.

Every template path is classified against the stored INSTALL_MANIFEST.json:

=====================  =========================================================
ADD / RESTORE          absent; RESTORE when the manifest says it was installed
UNCHANGED              already identical to the new template
REPLACE                pristine per the manifest (or accepted current baseline)
MODIFIED_BY_OWNER      differs from what the manifest recorded: blocks
UNKNOWN_BASELINE       no manifest entry to prove it pristine: blocks unless
                       ``--accept-current-as-baseline``
REMOVE                 in the manifest, gone from the template, still pristine;
                       or, without a manifest, listed in RETIRED_TEMPLATE_PATHS
                       and ``--accept-current-as-baseline`` given
OBSOLETE_MODIFIED      gone from the template but edited: kept, warned
OBSOLETE_UNVERIFIED    in RETIRED_TEMPLATE_PATHS, no manifest, no accepted
                       baseline: kept, warned with the rerun command
=====================  =========================================================

Before planning, every runtime must be idle (``runtime_busy``): each
ACTION_JOURNAL.json IDLE or RELEASED, and each STATE.md (parsed with the
template's ``state_format``, JSON or YAML payload) with ``loop.control.loop_active``
false and ``stage.status`` not RUNNING; an unparseable STATE blocks too
(UPGRADE_CONTROLLER_BUSY). The one exception is an IMPLEMENT/TEST result held by
the workspace audit, which the upgraded runtime then re-audits.

Owner files (policies/GATES.md, policies/EXECUTORS.md, PROJECT_SETUP.md) are
never read for comparison nor rewritten; an absent GATES.md/EXECUTORS.md is
created from the template. Apply re-plans under a lock, refuses a different
plan, backs up every file it replaces or removes, replaces atomically through
a temporary sibling (keeping the file mode), writes the manifest last as the
commit point and undoes every step in reverse on failure.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import shlex
import stat
import sys
from pathlib import Path

from .constants import (
    CONFIG_ROOT,
    INSTALL_MANIFEST_PATH,
    OBSIDIAN_BINDING_PATH,
    OWNER_FILES,
    PROJECT_SETUP_PATH,
    RETIRED_TEMPLATE_PATHS,
    UPGRADE_BACKUPS_PATH,
    UPGRADE_LOCK_PATH,
)
from .errors import InstallError
from . import layout_migration as lm
from .fsops import (
    _acquire_lock,
    _close_descriptor,
    _create_project_file_nofollow,
    _create_project_regular_owned,
    _open_project_parent_nofollow,
    _read_project_file_nofollow,
    _rollback_created_paths,
    _unlink_owned_at,
)
from .gitops import _target_snapshot, git
from .manifest import manifest_body, manifest_bytes, parse_manifest, sha256, skill_version, template_payload, version_key
from .mode import storage_mode
from .runtime_busy import runtime_busy_reasons
from .templates import _load_template_module
from .upgrade_files import _remove_file, _replace_file

# --- planning -------------------------------------------------------------------


def _controller_root(args, target: Path, workspace: dict[str, str]) -> tuple[Path, str, Path | None]:
    """Return (controller root, storage name, vault) after the storage checks."""
    if args.local_storage:
        return target, "LOCAL", None
    from .obsidian import _reject_storage_overlap, resolve_obsidian_storage

    vault, project = resolve_obsidian_storage(args.obsidian_vault, args.obsidian_project)
    _reject_storage_overlap(vault, project, target, workspace)
    return vault / project, "OBSIDIAN", vault


def _upgrade_command(args, target: Path, *extra: str) -> str:
    parts = [sys.executable, str(Path(sys.argv[0]).resolve()) if sys.argv and sys.argv[0] else "install_project.py"]
    parts += ["--target", str(target)]
    if args.local_storage:
        parts.append("--local-storage")
    else:
        parts += ["--obsidian-vault", str(args.obsidian_vault), "--obsidian-project", str(args.obsidian_project)]
    parts += ["--upgrade", *extra, "--json"]
    return " ".join(shlex.quote(part) for part in parts)


def _install_command(args, target: Path) -> str:
    command = _upgrade_command(args, target, "--apply")
    return command.replace(" --upgrade --apply", " --apply")


def _require_installed(root: Path, storage: str, args, target: Path, current: str) -> None:
    required = [PROJECT_SETUP_PATH, ".hermes.md"]
    if storage == "OBSIDIAN":
        required.append(OBSIDIAN_BINDING_PATH)
    missing = [path for path in required if _read_project_file_nofollow(root, lm.physical(current, path)) is None]
    if missing:
        raise InstallError(
            "UPGRADE_NOT_INSTALLED",
            next_step=(
                f"No controller is installed at {root} (missing {', '.join(missing)}). "
                "Run a normal installation first; --upgrade only updates an existing one."
            ),
            next_command=_install_command(args, target),
        )


def _runtime_directories(root: Path, storage: str, current: str) -> list[Path]:
    directories = [root / lm.physical(current, CONFIG_ROOT)]
    for runtime in lm.runtime_roots(root, current) if storage == "OBSIDIAN" else []:
        if runtime.is_dir() and not runtime.is_symlink():
            directories += sorted(
                child for child in runtime.iterdir() if child.is_dir() and not child.is_symlink()
            )
    return directories


def _parse_state(text: str):
    """Parse a STATE payload with the template's ``state_format``, the parser ``sdd.py`` itself uses."""
    return _load_template_module("sdd_upgrade_state_format", "runtime/state_format.py").parse(text)


def _require_idle(root: Path, storage: str, args=None, target: Path | None = None, current: str = lm.LOCAL) -> None:
    """Every runtime idle (``runtime_busy``), or holding only an IMPLEMENT/TEST result held by the workspace audit."""
    busy = [reason for directory in _runtime_directories(root, storage, current)
            for reason in runtime_busy_reasons(directory, _parse_state)]
    if busy:
        raise InstallError(
            f"UPGRADE_CONTROLLER_BUSY: {'; '.join(busy)}",
            next_step=(
                "The controller is in use. In every listed runtime: finish or release the running action "
                "(ACTION_JOURNAL status IDLE or RELEASED; `action_journal.py recover` names the step), let the "
                "bounded loop stop (loop.control.loop_active false) and bring the demand to DONE or back to IDLE "
                "(stage.status not RUNNING; `sdd.py status` shows it). A STATE that cannot be parsed must be "
                "restored first. Then rerun --upgrade."
            ),
            next_command=_upgrade_command(args, target) if args is not None and target is not None else None,
        )


def _plan(
    root: Path, storage: str, accept_baseline: bool, accept_command: str = "", layout: str = lm.LOCAL,
) -> dict[str, object]:
    """Classify every path; reads only, never writes.

    ``accept_command`` is the exact rerun with ``--accept-current-as-baseline``
    named by each OBSOLETE_UNVERIFIED warning.
    """
    payload = template_payload()
    on_disk = lambda relative: _read_project_file_nofollow(root, lm.physical(layout, relative))  # noqa: E731
    manifest_raw = on_disk(INSTALL_MANIFEST_PATH)
    manifest = parse_manifest(manifest_raw) if manifest_raw is not None else None
    recorded: dict[str, dict[str, object]] = dict(manifest["files"]) if manifest else {}  # type: ignore[arg-type]
    recorded_owner: dict[str, dict[str, object]] = dict(manifest["owner_files"]) if manifest else {}  # type: ignore[arg-type]
    add: dict[str, bytes] = {}
    restore: dict[str, bytes] = {}
    replace: dict[str, tuple[bytes, bytes]] = {}
    remove: dict[str, bytes] = {}
    conflicts: list[dict[str, object]] = []
    warnings: list[str] = []
    owner_files: list[dict[str, object]] = []
    owner_add: dict[str, bytes] = {}
    unchanged = 0
    for relative, content in sorted(payload.items()):
        current = on_disk(relative)
        if relative in OWNER_FILES:
            template_sha = sha256(content)
            known = recorded_owner.get(relative, {}).get("template_sha256") if manifest else None
            entry: dict[str, object] = {
                "path": relative,
                "action": "ADD" if current is None else "KEEP",
                "template_changed": (known != template_sha) if known else None,
                "template_sha256": template_sha,
                "current_sha256": sha256(current) if current is not None else None,
            }
            owner_files.append(entry)
            if current is None:
                owner_add[relative] = content
            continue
        if current is None:
            (restore if relative in recorded else add)[relative] = content
        elif current == content:
            unchanged += 1
        elif relative in recorded:
            if sha256(current) == recorded[relative].get("sha256"):
                replace[relative] = (current, content)
            else:
                conflicts.append({
                    "path": relative, "classification": "MODIFIED_BY_OWNER",
                    "current_sha256": sha256(current), "installed_sha256": recorded[relative].get("sha256"),
                })
        elif accept_baseline:
            replace[relative] = (current, content)
        else:
            conflicts.append({"path": relative, "classification": "UNKNOWN_BASELINE", "current_sha256": sha256(current)})
    for relative, entry in sorted(recorded.items()):
        if relative in payload or relative in OWNER_FILES:
            continue
        current = on_disk(relative)
        if current is None:
            continue
        if sha256(current) == entry.get("sha256"):
            remove[relative] = current
        else:
            warnings.append(f"OBSOLETE_MODIFIED: {relative} is no longer shipped but was edited; it is kept")
    # Paths an older release shipped that no manifest records (installs made
    # before INSTALL_MANIFEST.json): only an accepted baseline removes them.
    for relative in RETIRED_TEMPLATE_PATHS:
        if relative in payload or relative in recorded:
            continue
        current = on_disk(relative)
        if current is None:
            continue
        if accept_baseline:
            remove[relative] = current
        else:
            warnings.append(
                f"OBSOLETE_UNVERIFIED: {relative} was retired from the template and no manifest proves it unmodified; "
                f"it is kept. Rerun with --accept-current-as-baseline to remove it (a backup is kept): {accept_command}"
            )
    owner_files.append({
        "path": PROJECT_SETUP_PATH, "action": "KEEP", "template_changed": False,
        "template_sha256": None,
        "current_sha256": sha256(on_disk(PROJECT_SETUP_PATH) or b""),
    })
    body = manifest_body(storage, payload)
    current_body = {key: value for key, value in (manifest or {}).items() if key != "written_at"}
    migration = lm.plan(root, layout)
    plan: dict[str, object] = {
        "layout": layout, "migration": migration,
        "from_version": manifest["skill_version"] if manifest else None,
        "to_version": body["skill_version"],
        "manifest": "PRESENT" if manifest else "ABSENT",
        "baseline": "MANIFEST" if manifest else ("CURRENT_ACCEPTED" if accept_baseline else "NONE"),
        "manifest_before": manifest_raw,
        "manifest_current": manifest is not None and current_body == body,
        "manifest_body": body,
        "add": add, "restore": restore, "replace": replace, "remove": remove,
        "owner_add": owner_add, "unchanged_count": unchanged,
        "conflicts": conflicts, "warnings": warnings, "owner_files": owner_files,
    }
    plan["plan_sha256"] = _plan_digest(plan)
    return plan


def _plan_digest(plan: dict[str, object]) -> str:
    canonical = {
        "from": plan["from_version"], "to": plan["to_version"], "baseline": plan["baseline"],
        "manifest": sha256(plan["manifest_before"]) if plan["manifest_before"] is not None else None,  # type: ignore[arg-type]
        "add": {k: sha256(v) for k, v in plan["add"].items()},  # type: ignore[union-attr]
        "restore": {k: sha256(v) for k, v in plan["restore"].items()},  # type: ignore[union-attr]
        "replace": {k: [sha256(a), sha256(b)] for k, (a, b) in plan["replace"].items()},  # type: ignore[union-attr]
        "remove": {k: sha256(v) for k, v in plan["remove"].items()},  # type: ignore[union-attr]
        "owner_add": {k: sha256(v) for k, v in plan["owner_add"].items()},  # type: ignore[union-attr]
        "conflicts": plan["conflicts"], "layout": plan["layout"],
        "migration": lm.describe(plan["migration"]),  # type: ignore[arg-type]
        "binding": sha256(plan["migration"]["binding_before"]) if plan["migration"] else None,  # type: ignore[index]
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _has_changes(plan: dict[str, object]) -> bool:
    return any(plan[key] for key in ("migration", "add", "restore", "replace", "remove", "owner_add"))


def _final_layout(plan: dict[str, object]) -> str:
    return lm.VISIBLE if plan["migration"] else str(plan["layout"])


def _report(plan: dict[str, object], root: Path, storage: str) -> dict[str, object]:
    return {
        "status": "UPGRADE_READY",
        "storage": storage,
        "controller_location": str(root / lm.physical(_final_layout(plan), CONFIG_ROOT)),
        "layout_migration": lm.describe(plan["migration"]),  # type: ignore[arg-type]
        "from_version": plan["from_version"],
        "to_version": plan["to_version"],
        "manifest": plan["manifest"],
        "baseline": plan["baseline"],
        "plan_sha256": plan["plan_sha256"],
        "changes": {
            "add": sorted([*plan["add"], *plan["owner_add"]]),  # type: ignore[misc]
            "restore": sorted(plan["restore"]),  # type: ignore[arg-type]
            "replace": sorted(plan["replace"]),  # type: ignore[arg-type]
            "remove": sorted(plan["remove"]),  # type: ignore[arg-type]
            "unchanged_count": plan["unchanged_count"],
        },
        "conflicts": plan["conflicts"],
        "owner_files": plan["owner_files"],
        "backup": None,
        "target_writes": [],
        "applied": False,
        "warnings": list(plan["warnings"]),  # type: ignore[call-overload]
    }


def _commit_manifest(
    root: Path, before: bytes | None, content: bytes, created_files, created_directories, current: str = lm.LOCAL,
) -> None:
    """The commit point: once the new manifest is in place the upgrade is done."""
    path = lm.physical(current, INSTALL_MANIFEST_PATH)
    if before is None:
        _create_project_file_nofollow(root, path, content, created_files, created_directories)
    else:
        _replace_file(root, path, before, content)


def _backup_directory(root: Path, plan: dict[str, object]) -> str:
    """Backup location for this run, physical in the layout before the migration."""
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f"{lm.physical(str(plan['layout']), UPGRADE_BACKUPS_PATH)}/{stamp}-{plan['from_version'] or 'unknown'}-to-{plan['to_version']}"
    candidate, counter = base, 1
    while os.path.lexists(root / candidate):
        counter += 1
        candidate = f"{base}-{counter}"
    return candidate


def _rollback(
    root: Path,
    journal: list[tuple[str, str, bytes, bytes, tuple[int, int], int]],
    created_files,
    created_directories,
    renames: list[tuple[str, str, tuple[int, int]]] | None = None,
) -> None:
    """Undo directory renames, then replaced and removed files in reverse, then every created path."""
    errors: list[str] = []
    moved = {destination for _, destination, _ in renames or []}
    # A manifest created under a renamed root must go before that root moves back.
    after = [entry for entry in created_files if entry[0].split("/", 1)[0] in moved]
    created_files[:] = [entry for entry in created_files if entry not in after]
    try:
        _rollback_created_paths(root, after, [])
    except InstallError as error:
        errors.append(str(error))
    for source, destination, identity in reversed(renames or []):
        try:
            lm.undo_rename(root, source, destination, identity)
        except (InstallError, OSError) as error:
            errors.append(f"{destination}: {error}")
    for action, relative, old, new, identity, mode in reversed(journal):
        try:
            if action == "REPLACE":
                _replace_file(root, relative, new, old, expected_identity=identity)
            else:
                if _read_project_file_nofollow(root, relative) is not None:
                    raise InstallError(f"UPGRADE_STATE_CHANGED: {relative} reappeared")
                _create_project_regular_owned(root, relative, old, mode)
        except (InstallError, OSError) as error:
            errors.append(f"{relative}: {error}")
    try:
        _rollback_created_paths(root, created_files, created_directories)
    except InstallError as error:
        errors.append(str(error))
    if errors:
        raise InstallError(
            f"UPGRADE_ROLLBACK_FAILED: {'; '.join(errors)}",
            next_step=(
                "Restore the listed files from the newest upgrade-backups/<run>/files copy "
                "(BACKUP_MANIFEST.json lists each original hash and mode), then rerun --upgrade."
            ),
        )


def _apply(root: Path, plan: dict[str, object], check_target) -> str:
    """Write the plan; return the backup directory (relative to `root`)."""
    created_files: list[tuple[str, tuple[int, int], bytes]] = []
    created_directories: list[tuple[str, tuple[int, int]]] = []
    journal: list[tuple[str, str, bytes, bytes, tuple[int, int], int]] = []
    renames: list[tuple[str, str, tuple[int, int]]] = []
    backup = _backup_directory(root, plan)
    on_disk = lambda relative: lm.physical(str(plan["layout"]), relative)  # noqa: E731
    migration: dict[str, object] | None = plan["migration"]  # type: ignore[assignment]
    try:
        originals: dict[str, dict[str, object]] = {}
        for action, items in (("REPLACE", plan["replace"]), ("REMOVE", plan["remove"])):
            for relative in sorted(items):  # type: ignore[arg-type]
                value = items[relative]  # type: ignore[index]
                old = value[0] if action == "REPLACE" else value
                mode = stat.S_IMODE((root / on_disk(relative)).lstat().st_mode)
                _create_project_file_nofollow(
                    root, f"{backup}/files/{relative}", old, created_files, created_directories
                )
                originals[relative] = {"action": action, "sha256": sha256(old), "mode": oct(mode)}
        backup_manifest = {
            "from_version": plan["from_version"], "to_version": plan["to_version"],
            "plan_sha256": plan["plan_sha256"],
            "created_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "files": originals,
        }
        _create_project_file_nofollow(
            root, f"{backup}/BACKUP_MANIFEST.json",
            (json.dumps(backup_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            created_files, created_directories,
        )
        for items in (plan["add"], plan["restore"], plan["owner_add"]):
            for relative, content in sorted(items.items()):  # type: ignore[union-attr]
                _create_project_file_nofollow(root, on_disk(relative), content, created_files, created_directories)
        for relative, (old, new) in sorted(plan["replace"].items()):  # type: ignore[union-attr]
            identity, mode = _replace_file(root, on_disk(relative), old, new)
            journal.append(("REPLACE", on_disk(relative), old, new, identity, mode))
        for relative, old in sorted(plan["remove"].items()):  # type: ignore[union-attr]
            identity, mode = _remove_file(root, on_disk(relative), old)
            journal.append(("REMOVE", on_disk(relative), old, b"", identity, mode))
        if migration is not None and migration["binding_after"] is not None:
            old, new = migration["binding_before"], migration["binding_after"]
            identity, mode = _replace_file(root, OBSIDIAN_BINDING_PATH, old, new)  # type: ignore[arg-type]
            journal.append(("REPLACE", OBSIDIAN_BINDING_PATH, old, new, identity, mode))  # type: ignore[arg-type]
        check_target()
        for source, destination in migration["moves"] if migration is not None else []:  # type: ignore[union-attr]
            renames.append((source, destination, lm.rename_directory(root, source, destination)))
        _commit_manifest(
            root, plan["manifest_before"], manifest_bytes(plan["manifest_body"]),  # type: ignore[arg-type]
            created_files, created_directories, _final_layout(plan),
        )
    except BaseException:
        _rollback(root, journal, created_files, created_directories, renames)
        raise
    return lm.physical(_final_layout(plan), backup) if migration is not None else backup


# --- entry point ---------------------------------------------------------------------


def run_upgrade(args, target: Path, workspace: dict[str, str]) -> dict[str, object]:
    with storage_mode(obsidian=not args.local_storage):
        return _run_upgrade(args, target, workspace)


def _run_upgrade(args, target: Path, workspace: dict[str, str]) -> dict[str, object]:
    root, storage, _ = _controller_root(args, target, workspace)
    current = lm.layout(root, storage)
    _require_installed(root, storage, args, target, current)
    to_version = skill_version()
    manifest_raw = _read_project_file_nofollow(root, lm.physical(current, INSTALL_MANIFEST_PATH))
    if manifest_raw is not None:
        from_version = str(parse_manifest(manifest_raw)["skill_version"])
        if version_key(from_version) > version_key(to_version):
            raise InstallError(
                f"UPGRADE_DOWNGRADE_REFUSED: installed {from_version}, this skill is {to_version}",
                next_step="Run --upgrade from a skill at least as new as the installed controller.",
            )
    _require_idle(root, storage, args, target, current)
    accept = bool(args.accept_current_as_baseline)

    def snapshot():
        # Obsidian: nothing in the repository may move. Local: the controller
        # lives in the worktree, so only HEAD and tracked files are compared.
        if storage == "OBSIDIAN":
            return _target_snapshot(target)
        return git(target, "rev-parse", "HEAD"), git(target, "status", "--porcelain", "--untracked-files=no")

    target_before = snapshot()

    def require_target_unchanged() -> None:
        if snapshot() != target_before:
            raise InstallError(
                "TARGET_WORKTREE_CHANGED",
                next_step="The repository changed during the upgrade; nothing was kept. Rerun --upgrade.",
            )

    accept_command = _upgrade_command(args, target, "--accept-current-as-baseline")
    plan = _plan(root, storage, accept, accept_command, current)
    report = _report(plan, root, storage)
    exclude_planned = False
    if storage == "LOCAL":
        from .exclude import exclude_update_planned

        exclude_planned = exclude_update_planned(workspace)
        report["exclude_update_planned"] = exclude_planned
    if plan["conflicts"]:
        blocking = {item["classification"] for item in plan["conflicts"]}  # type: ignore[union-attr]
        if blocking == {"UNKNOWN_BASELINE"}:
            next_step = (
                "No INSTALL_MANIFEST.json proves these files are unmodified. Compare each listed file with the "
                "template; if the current copies are yours to discard, rerun with --accept-current-as-baseline "
                "(each is backed up before it is replaced)."
            )
            next_command = _upgrade_command(args, target, "--accept-current-as-baseline")
        else:
            next_step = (
                "Owner edits were found in installer-managed files. Move each edit into a project file (or "
                "restore the file from the template), then rerun --upgrade; owner files such as GATES.md are "
                "never touched."
            )
            next_command = _upgrade_command(args, target)
        report.update(status="BLOCKED", next_step=next_step, next_command=next_command)
        raise InstallError("UPGRADE_CONFLICT", next_step=next_step, next_command=next_command, details=report)
    if plan["manifest_current"] and not _has_changes(plan) and not exclude_planned:
        report.update(status="ALREADY_CURRENT", next_step="Nothing to upgrade; the controller matches this skill.")
        return report
    if not args.apply:
        report["next_step"] = "Review changes, conflicts and owner_files, then rerun with --apply."
        report["next_command"] = _upgrade_command(args, target, *(("--accept-current-as-baseline",) if accept else ()), "--apply")
        return report
    result: dict[str, str] = {}

    lock_path = lm.physical(current, UPGRADE_LOCK_PATH)

    def locked_apply() -> None:
        parent = _open_project_parent_nofollow(root, lock_path, create=False)
        try:
            lock, owned = _acquire_lock(parent, Path(UPGRADE_LOCK_PATH).name, "UPGRADE_LOCKED")
        except InstallError as error:
            _close_descriptor(parent)
            raise InstallError(
                str(error),
                next_step=f"Another upgrade holds {root / lock_path}; wait for it, or delete the file "
                "if no installer is running, then rerun.",
            ) from error
        try:
            replanned = _plan(root, storage, accept, accept_command, current)
            if replanned["plan_sha256"] != plan["plan_sha256"]:
                raise InstallError(
                    "UPGRADE_STATE_CHANGED",
                    next_step="The controller changed after planning; nothing was written. Rerun --upgrade to re-plan.",
                    next_command=_upgrade_command(args, target),
                )
            require_target_unchanged()
            _require_idle(root, storage, args, target, current)
            result["backup"] = _apply(root, replanned, require_target_unchanged)
        finally:
            _close_descriptor(lock)
            try:
                _unlink_owned_at(parent, Path(UPGRADE_LOCK_PATH).name, owned, "UPGRADE_LOCK_OWNERSHIP_LOST")
            except InstallError:
                pass
            _close_descriptor(parent)

    if storage == "LOCAL":
        from .local_install import _apply_base_install

        warnings = _apply_base_install(target, workspace, [], locked_apply, replan=lambda _target: ([], []))
        report["warnings"].extend(f"LOCK_CLEANUP_REQUIRES_REVIEW: {item}" for item in warnings)  # type: ignore[union-attr]
        report["exclude_update_planned"] = False
    else:
        locked_apply()
    require_target_unchanged()
    report.update(
        status="UPGRADED",
        applied=True,
        backup=str(root / result["backup"]),
        next_step="Review owner_files with template_changed true against the new template, then start a new session.",
    )
    return report
