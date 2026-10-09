"""Report rendering: BLOCKED diagnostics with next steps and the text summary."""
from __future__ import annotations

import json
import subprocess

from .errors import InstallError

#: Every failure the CLI turns into a BLOCKED report instead of a traceback.
REPORTED_ERRORS = (InstallError, OSError, subprocess.TimeoutExpired)

_NEXT_STEPS = {
    "GIT_REPOSITORY_REQUIRED": "Initialize and commit the target as a Git repository, then rerun the installer.",
    "GIT_INITIAL_COMMIT_REQUIRED": "Create the initial Git commit on an attached branch, then rerun the installer.",
    "ATTACHED_BRANCH_REQUIRED": "Switch the target worktree to an attached branch, then rerun the installer.",
    "OBSIDIAN_BINDING_REQUIRED": (
        "Pass --obsidian-vault <absolute vault> and --obsidian-project <container relative to the vault>; "
        "all orchestrator data is stored there and nothing is written to the project."
    ),
}


def blocked_report(error: BaseException) -> dict[str, object]:
    """Return the BLOCKED report; errors that carry their own guidance keep it."""
    reason = str(error)
    report: dict[str, object] = {"status": "BLOCKED", "reason": reason}
    if reason in _NEXT_STEPS:
        report["next_step"] = _NEXT_STEPS[reason]
    elif reason.startswith("CONFIG_CONFLICT: "):
        report["next_step"] = (
            "An installed controller file differs from this skill's template. If this is an older installation, "
            "rerun the same command with --upgrade (dry run) to see what changes; otherwise restore the file."
        )
    for key in ("next_step", "next_command"):
        value = getattr(error, key, None)
        if value:
            report[key] = value
    for key, value in getattr(error, "details", {}).items():
        report.setdefault(key, value)
    return report


def render(report: dict[str, object], *, as_json: bool) -> str:
    """The stdout line for a successful run."""
    if as_json:
        return json.dumps(report, ensure_ascii=False)
    if "plan_sha256" in report:
        changes = report.get("changes") or {}
        return (
            f'{report["status"]}: upgrade {report.get("from_version")} -> {report.get("to_version")}; '
            f'{len(changes.get("add", []))} add, {len(changes.get("replace", []))} replace, '
            f'{len(changes.get("remove", []))} remove'
        )
    if report.get("storage") == "OBSIDIAN":
        return f'{report["status"]}: {len(report["planned"])} files planned in {report["project_container"]}'
    stack = report.get("stack") or {}
    ecosystems = ", ".join(
        f'{item["ecosystem"]}@{item["path"]}' for item in stack.get("ecosystems", [])
    ) or "none detected"
    return f'{report["status"]}: {len(report["planned"])} files planned; stack: {ecosystems}'
