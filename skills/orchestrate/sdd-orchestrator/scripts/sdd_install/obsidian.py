"""Obsidian container storage: planning, apply and drift checks."""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import shlex
import stat
import sys
import urllib.parse
from pathlib import Path, PurePosixPath

from .constants import (
    CONFIG_ROOT,
    OBSIDIAN_BINDING_PATH,
    OBSIDIAN_RUNTIME_SUBPATH,
    HIDDEN_CONTROLLER_NOTE,
    INSTALL_MANIFEST_PATH,
    OWNER_FILES,
    TEMPLATE,
    TYPESAFE_ANSWER_DISABLED,
    TYPESAFE_ANSWER_ENABLED,
    TYPESAFE_LOCK_PATH_OBSIDIAN,
    WORKTREE_RUNTIME_FILES,
)
from .errors import InstallError
from .fsops import (
    _create_project_file_nofollow,
    _identity_chain,
    _overwrite_project_file_nofollow,
    _read_project_file_nofollow,
    _read_project_regular_snapshot,
    _rollback_created_paths,
)
from .gitops import _protected_roots, _target_snapshot
from .manifest import install_manifest
from .mode import MODE, storage_mode
from .onboarding import _render_onboarding_answer, _require_typesafe_record_target, onboarding_questions
from .templates import (
    _load_template_module,
    detect_stack,
    empty_journal,
    project_setup,
    state,
    template_files,
)
from .typesafe import _preflight_typesafe_install, install_typesafe_skill, typesafe_skill_status


def _reject_storage_overlap(vault: Path, project: str, target: Path, workspace: dict[str, str]) -> None:
    """The vault and every checkout of the user's repository must be disjoint."""
    storage = [vault, vault / project]
    for root in _protected_roots(target, workspace):
        root_chain = _identity_chain(root)
        for place in storage:
            place_chain = _identity_chain(place)
            inside_root = root_chain[0] in place_chain
            contains_root = place.exists() and place_chain[0] in root_chain
            if inside_root or contains_root:
                raise InstallError(f"OBSIDIAN_VAULT_OVERLAPS_TARGET: {root}")


def resolve_obsidian_storage(vault_value: str | None, project_value: str | None) -> tuple[Path, str]:
    """Validate the Obsidian vault and project container used as the only storage root."""
    if not vault_value or not project_value:
        raise InstallError("OBSIDIAN_BINDING_REQUIRED")
    vault = Path(vault_value).expanduser()
    if not vault.is_absolute():
        raise InstallError("OBSIDIAN_VAULT_NOT_ABSOLUTE")
    if not vault.is_dir():
        raise InstallError("OBSIDIAN_VAULT_NOT_FOUND")
    project = Path(project_value)
    if (
        project.is_absolute()
        or not project.parts
        or any(part in {"", ".", ".."} or part.startswith(".") for part in project.parts)
        or "\\" in project_value
        or "\x00" in project_value
    ):
        raise InstallError("OBSIDIAN_PROJECT_INVALID")
    return vault.resolve(strict=True), project.as_posix()


def _worktree_slug(target: Path) -> str:
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        module = _load_template_module("sdd_obsidian_binding", "runtime/obsidian_binding.py")
    finally:
        sys.dont_write_bytecode = previous
    return module.worktree_slug(target)


def obsidian_binding_content(vault: Path, project: str) -> bytes:
    payload = {
        "schema_version": 1,
        "vault_path": str(vault),
        "project_container": project,
        "runtime_subpath": OBSIDIAN_RUNTIME_SUBPATH,
    }
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def obsidian_project_setup(vault: Path, project: str) -> str:
    answer = json.dumps(
        {"vault": str(vault), "project_container": project},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return project_setup().replace("  obsidian: UNRESOLVED", f"  obsidian: {answer}", 1)


def _wiki_module():
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        return _load_template_module("sdd_wiki_layout", "runtime/wiki_layout.py")
    finally:
        sys.dont_write_bytecode = previous


def _wiki_skeleton(project: str) -> dict[str, bytes]:
    """Skeleton files plus a hidden keep-file for each empty wiki directory."""
    module = _wiki_module()
    name = PurePosixPath(project).name
    files = dict(module.skeleton_files(project=name, today=datetime.date.today().isoformat()))
    for directory in module.DIRECTORIES:
        files[f"{directory}/.gitkeep"] = b""
    return files


def _wiki_path_state(container: Path, relative: str) -> str:
    """Classify one skeleton path without following links.

    ``absent`` and ``directory-absent`` are planned; ``present`` is left alone.
    A keep-file is planned only for an absent or empty directory. Any link or
    non-directory on the way, or a non-regular file where a skeleton file
    belongs, is refused with ``WIKI_PATH_UNSAFE`` instead of a raw OS error.
    """
    parts = PurePosixPath(relative).parts
    keep_file = parts[-1] == ".gitkeep"
    current = container
    for index, part in enumerate(parts):
        current = current / part
        final = index == len(parts) - 1
        try:
            status = os.lstat(current)
        except FileNotFoundError:
            return "directory-absent" if keep_file and index == len(parts) - 2 else "absent"
        if stat.S_ISLNK(status.st_mode):
            raise InstallError(f"WIKI_PATH_UNSAFE: {current} is a symlink")
        if not final:
            if not stat.S_ISDIR(status.st_mode):
                raise InstallError(f"WIKI_PATH_UNSAFE: {current} is not a directory")
            if keep_file and index == len(parts) - 2:
                with os.scandir(current) as entries:
                    if any(entries):
                        return "present"
        elif not stat.S_ISREG(status.st_mode):
            raise InstallError(f"WIKI_PATH_UNSAFE: {current} is not a regular file")
    return "present"


def _plan_obsidian_files(
    vault: Path,
    project: str,
    target: Path,
    workspace: dict[str, str],
) -> tuple[dict[str, bytes], str, list[str]]:
    """Return missing vault-relative files, the runtime path and preserved owner edits.

    Controller files and the binding must be byte-identical when present. The
    per-worktree runtime is all-or-nothing so an interrupted or foreign STATE is
    never combined with a fresh journal.
    """
    expected: dict[str, bytes] = {}
    for source in template_files():
        expected[f"{project}/{source.relative_to(TEMPLATE).as_posix()}"] = source.read_bytes()
    expected[f"{project}/{OBSIDIAN_BINDING_PATH}"] = obsidian_binding_content(vault, project)
    planned: dict[str, bytes] = {}
    previously_installed = all(
        _read_project_file_nofollow(vault, f"{project}/{relative}") is not None
        for relative in (OBSIDIAN_BINDING_PATH, f"{CONFIG_ROOT}/PROJECT_SETUP.md")
    )
    # Owner-edited files are trusted only in a container this installer already
    # set up; a fresh container never adopts gate commands it did not write.
    user_configured = (
        {f"{project}/{relative}" for relative in OWNER_FILES} if previously_installed else set()
    )
    customized: list[str] = []
    for relative, content in expected.items():
        existing = _read_project_file_nofollow(vault, relative)
        if existing is None:
            planned[relative] = content
        elif existing != content:
            if relative not in user_configured:
                raise InstallError(f"CONFIG_CONFLICT: {relative}")
            customized.append(f"{relative}:sha256:{hashlib.sha256(existing).hexdigest()}")

    setup = f"{project}/{CONFIG_ROOT}/PROJECT_SETUP.md"
    if _read_project_file_nofollow(vault, setup) is None:
        planned[setup] = obsidian_project_setup(vault, project).encode("utf-8")

    # LLM Wiki skeleton: created only where absent, never compared or replaced,
    # because the wiki files belong to the project once they exist.
    for relative, content in _wiki_skeleton(project).items():
        if _wiki_path_state(vault / project, relative) != "present":
            planned[f"{project}/{relative}"] = content

    runtime = f"{project}/{OBSIDIAN_RUNTIME_SUBPATH}/{_worktree_slug(target)}"
    present = [
        name for name in WORKTREE_RUNTIME_FILES
        if _read_project_file_nofollow(vault, f"{runtime}/{name}") is not None
    ]
    if present and len(present) != len(WORKTREE_RUNTIME_FILES):
        raise InstallError(f"LOCAL_STATE_REQUIRES_REVIEW: {', '.join(f'{runtime}/{name}' for name in present)}")
    if not present:
        planned[f"{runtime}/STATE.md"] = state(workspace).encode("utf-8")
        planned[f"{runtime}/INCIDENTS.md"] = b"# SDD Orchestration Incidents\n\nNo incidents recorded.\n"
        planned[f"{runtime}/ACTION_JOURNAL.json"] = empty_journal(target, workspace).encode("utf-8")
    # Written only by a fresh install, never implicitly for an older container.
    manifest = f"{project}/{INSTALL_MANIFEST_PATH}"
    if not previously_installed and _read_project_file_nofollow(vault, manifest) is None:
        planned[manifest] = install_manifest("OBSIDIAN")
    return planned, runtime, customized


def _apply_obsidian_files(vault: Path, planned: dict[str, bytes], after_base=None) -> None:
    created_files: list[tuple[str, tuple[int, int], bytes]] = []
    created_directories: list[tuple[str, tuple[int, int]]] = []
    try:
        for relative, content in planned.items():
            _create_project_file_nofollow(
                vault,
                relative,
                content,
                created_files,
                created_directories,
                check_tracked=False,
            )
        if after_base is not None:
            after_base()
    except BaseException as error:
        try:
            _rollback_created_paths(vault, created_files, created_directories)
        except InstallError as rollback:
            raise InstallError(f"{error}; {rollback}") from error
        if isinstance(error, FileExistsError):
            raise InstallError(f"CONFIG_DESTINATION_CHANGED: {error.filename}") from error
        raise


#: Characters an executor CLI or a naive shell interpolation may refuse or
#: misread in a working directory; a plain space is common and handled.
_SHELL_SPECIAL = frozenset("$`\"'\\!*?[]{}()<>|&;#~=%^")


def container_path_warnings(container: Path) -> list[str]:
    """`CONTAINER_PATH_SHELL_UNSAFE` when the path may be refused as a workdir."""
    text = str(container)
    unsafe = sorted({char for char in text if ord(char) > 126 or ord(char) < 32 or char in _SHELL_SPECIAL})
    if not unsafe:
        return []
    shown = " ".join(repr(char) for char in unsafe[:8])
    return [
        f"CONTAINER_PATH_SHELL_UNSAFE: {text} contains {shown}; some executor CLIs refuse such a working "
        "directory. Run executors with the repository as working directory and pass container paths as "
        "arguments, or choose a plain-ASCII container."
    ]


def obsidian_url(vault: Path, project: str) -> str:
    """Link that opens the container's wiki index in the Obsidian app."""
    return (
        f"obsidian://open?vault={urllib.parse.quote(vault.name, safe='')}"
        f"&file={urllib.parse.quote(f'{project}/index.md', safe='')}"
    )


def _is_project_folder(directory: Path, wiki) -> bool:
    if wiki._has_marker(directory):
        return True
    try:
        names = os.listdir(directory)
    except OSError:
        return False
    return any(wiki._key(name) in wiki.CONTROLLER_SIGNS for name in names)


def _reject_nested_container(vault: Path, project: str, args, target: Path) -> None:
    """Refuse in the dry run what `wiki_layout` would refuse after install.

    The container may neither sit inside another project container nor hold
    one; both would make every later wiki command fail with
    `WIKI_CONTAINER_NESTED`.
    """
    wiki = _wiki_module()
    parts = PurePosixPath(project).parts
    for depth in range(1, len(parts)):
        ancestor = vault.joinpath(*parts[:depth])
        if ancestor.is_symlink() or not ancestor.is_dir():
            break
        if _is_project_folder(ancestor, wiki):
            sibling = "/".join((*parts[: depth - 1], " ".join(parts[depth - 1:])))
            raise _nested_error(
                f"{vault.joinpath(*parts)} would be nested inside the project container {ancestor}",
                sibling, args, target,
            )
    container = vault / project
    if container.is_dir() and not container.is_symlink():
        try:
            nested = wiki._nested_containers(container, guess_unmarked=not wiki._has_marker(container))
        except OSError as error:
            raise InstallError(f"WIKI_PATH_UNSAFE: cannot inspect {error.filename or container}") from error
        if nested:
            raise _nested_error(
                f"{container} holds other project containers: {', '.join(nested[:5])}",
                f"{project} Project", args, target,
            )


def _nested_error(detail: str, sibling: str, args, target: Path) -> InstallError:
    command = " ".join(
        shlex.quote(part)
        for part in (
            sys.executable, str(Path(sys.argv[0]).resolve()) if sys.argv and sys.argv[0] else "install_project.py",
            "--target", str(target), "--obsidian-vault", str(args.obsidian_vault), "--obsidian-project", sibling,
            "--json",
        )
    )
    return InstallError(
        f"WIKI_CONTAINER_NESTED: {detail}",
        next_step=(
            f"Use a sibling container instead, for example --obsidian-project '{sibling}', so no project folder "
            "contains another. Nothing was written; never move a container or edit its binding by hand."
        ),
        next_command=command,
    )


def run_obsidian_install(args, target: Path, workspace: dict[str, str]) -> dict[str, object]:
    with storage_mode(obsidian=True):
        return _run_obsidian_install(args, target, workspace)


def _run_obsidian_install(args, target: Path, workspace: dict[str, str]) -> dict[str, object]:
    vault, project = resolve_obsidian_storage(args.obsidian_vault, args.obsidian_project)
    _reject_storage_overlap(vault, project, target, workspace)
    container = vault / project
    legacy_lock = container / "skills-lock.json"
    if os.path.lexists(legacy_lock):
        raise InstallError(
            f"TYPESAFE_LOCK_LEGACY_LOCATION: {legacy_lock}; move it to {container / TYPESAFE_LOCK_PATH_OBSIDIAN} "
            "(wiki_layout.py migrate does this together with the legacy notes) and run the installer again"
        )
    _reject_nested_container(vault, project, args, target)
    target_before = _target_snapshot(target)

    def require_target_unchanged() -> None:
        if _target_snapshot(target) != target_before:
            raise InstallError("TARGET_WORKTREE_CHANGED")
    planned, runtime, customized = _plan_obsidian_files(vault, project, target, workspace)
    setup_present = f"{project}/{CONFIG_ROOT}/PROJECT_SETUP.md" not in planned
    if args.typesafe_ai and setup_present:
        _require_typesafe_record_target(container)
    onboarding = onboarding_questions(
        container if setup_present else None,
        args.typesafe_ai,
        args.automatic_jev_governance,
    )
    integration = onboarding["integrations"]["typesafe_ai"]
    if not setup_present:
        integration.update(typesafe_skill_status(container))
        integration["planned_action"] = "INSTALL" if args.typesafe_ai == "install" else (
            "RECORD_NONE" if args.typesafe_ai == "none" else "NONE"
        )
        integration["planned_env_action"] = (
            "CREATE"
            if MODE.credential_file_managed and args.typesafe_ai == "install" and integration["env_status"] == "ABSENT"
            else "NONE"
        )
    action = integration.get("planned_action")
    if action == "BLOCKED":
        if integration.get("env_status") == "CONFLICT":
            raise InstallError(f"TYPESAFE_ENV_CONFLICT: {integration.get('env_issue')}")
        raise InstallError(f"TYPESAFE_SKILL_CONFLICT: {integration.get('issue') or integration.get('status')}")
    if action == "INSTALL" and setup_present:
        _preflight_typesafe_install(container)
    report: dict[str, object] = {
        "status": "READY" if planned or action != "NONE" else "ALREADY_INITIALIZED",
        "storage": "OBSIDIAN",
        "target": str(target),
        "vault": str(vault),
        "project_container": str(container),
        "worktree_runtime": str(vault / runtime),
        "planned": sorted(planned),
        "target_writes": [],
        "preserved_owner_files": customized,
        "exclude_update_planned": False,
        "controller_location": str(container / CONFIG_ROOT),
        "obsidian_url": obsidian_url(vault, project),
        "hidden_controller_note": HIDDEN_CONTROLLER_NOTE,
        "warnings": container_path_warnings(container),
        "applied": False,
        "stack": detect_stack(target),
        "onboarding": onboarding,
        "next_step": "Resolve the project onboarding questions, then configure verified commands in the container GATES.md.",
    }
    integration_result: dict[str, str] = {}

    def apply_integration() -> None:
        """Run the integration with the drift check inside its own rollback."""
        if args.typesafe_ai == "install":
            answer = TYPESAFE_ANSWER_ENABLED if args.automatic_jev_governance else TYPESAFE_ANSWER_DISABLED
            onboarding_after = _render_onboarding_answer(container, "typesafe_ai", answer)
            integration_result["applied_action"] = install_typesafe_skill(
                container, onboarding_after, before_commit=require_target_unchanged
            )
            return
        relative = f"{CONFIG_ROOT}/PROJECT_SETUP.md"
        snapshot = _read_project_regular_snapshot(container, relative)
        if snapshot is None:
            raise InstallError("ONBOARDING_RECORD_MISSING")
        before, owned = snapshot
        after = _render_onboarding_answer(container, "typesafe_ai", "none", source=before)
        _overwrite_project_file_nofollow(container, relative, owned, after, expected=before)
        try:
            require_target_unchanged()
        except BaseException:
            _overwrite_project_file_nofollow(container, relative, owned, before, expected=after)
            raise
        integration_result["applied_action"] = "RECORD_NONE"

    integration_requested = bool(args.apply and args.typesafe_ai and action != "NONE")

    def finish_inside_transaction() -> None:
        # Checked before and after the integration and inside every rollback,
        # so a changed repository undoes every vault path this run wrote.
        require_target_unchanged()
        if integration_requested:
            apply_integration()
        else:
            require_target_unchanged()

    if args.apply and (planned or integration_requested):
        _apply_obsidian_files(vault, planned, finish_inside_transaction)
        report["applied"] = True
        report["status"] = "APPLIED"
        report["onboarding"] = onboarding_questions(container)
        if integration_result:
            report["onboarding"]["integrations"]["typesafe_ai"]["applied_action"] = integration_result["applied_action"]
    if _target_snapshot(target) != target_before:
        raise InstallError("TARGET_WORKTREE_CHANGED")
    return report
