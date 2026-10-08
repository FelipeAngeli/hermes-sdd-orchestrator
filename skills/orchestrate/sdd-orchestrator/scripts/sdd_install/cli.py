"""Dispatch one parsed command line to the selected storage mode."""
from __future__ import annotations

import sys

from .errors import InstallError
from .gitops import require_root
from .local_install import run_local_install
from .mode import storage_mode
from .obsidian import run_obsidian_install
from .report import REPORTED_ERRORS, blocked_report, render


def run(args) -> int:
    """Run the installer for `args` and print the report; return the exit code."""
    try:
        target, workspace = require_root(args.target)
        if args.local_storage and (args.obsidian_vault or args.obsidian_project):
            raise InstallError("STORAGE_MODE_CONFLICT")
        if args.local_storage:
            with storage_mode(obsidian=False):
                report = run_local_install(args, target, workspace)
        else:
            report = run_obsidian_install(args, target, workspace)
        print(render(report, as_json=args.json))
        return 0
    except REPORTED_ERRORS as error:
        report = blocked_report(error)
        print(render(report, as_json=True) if args.json else f"BLOCKED: {error}", file=sys.stderr)
        return 2
