"""Implementation package of `scripts/install_project.py`.

The entry point owns argparse and the interpreter checks; this package holds
the installer itself. Importing it re-exports every module-level name of the
submodules for introspection, but a test that replaces a function must patch
the submodule that looks the name up (for example `obsidian._target_snapshot`).
"""
from __future__ import annotations

from . import (  # noqa: F401  (import order is the dependency order)
    constants,
    errors,
    mode,
    gitops,
    fsops,
    templates,
    onboarding,
    typesafe,
    exclude,
    local_install,
    obsidian,
    report,
    cli,
)

_SUBMODULES = (
    constants, errors, mode, gitops, fsops, templates, onboarding, typesafe,
    exclude, local_install, obsidian, report, cli,
)
for _module in _SUBMODULES:
    for _name, _value in vars(_module).items():
        if not _name.startswith("__") and _name not in globals():
            globals()[_name] = _value
del _module, _name, _value
