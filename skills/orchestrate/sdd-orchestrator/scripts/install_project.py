#!/usr/bin/env python3
"""Install the SDD Orchestrator as untracked, project-local configuration."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
PACKAGE = "sdd_install"


def _package():
    """Import the implementation package that sits next to this file.

    Another copy of the skill (a test fixture, an older install) may already
    have imported a package with the same name in this interpreter; its
    modules are dropped so this entry point never runs foreign code.
    """
    loaded = sys.modules.get(PACKAGE)
    expected = SCRIPTS / PACKAGE
    if loaded is not None:
        origin = getattr(loaded, "__file__", None)
        if origin is None or Path(origin).resolve().parent != expected:
            for name in [name for name in sys.modules if name == PACKAGE or name.startswith(PACKAGE + ".")]:
                del sys.modules[name]
    sys.dont_write_bytecode = True
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    return importlib.import_module(PACKAGE)


def __getattr__(name: str):
    """Expose the package's names (`require_root`, `InstallError`, ...) on this module."""
    if name.startswith("__"):
        raise AttributeError(name)
    try:
        return getattr(_package(), name)
    except AttributeError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default=".", help="existing Git worktree root (default: current directory)")
    parser.add_argument("--obsidian-vault", help="absolute path to the Obsidian vault")
    parser.add_argument("--obsidian-project", help="project container path relative to the Obsidian vault")
    parser.add_argument(
        "--local-storage",
        action="store_true",
        help="legacy mode: install the controller inside the target worktree instead of Obsidian",
    )
    parser.add_argument("--apply", action="store_true", help="write files after a successful dry run")
    parser.add_argument(
        "--typesafe-ai",
        choices=("install", "none"),
        help="explicitly install the project-local TypeSafe skill or record that it is not used",
    )
    parser.add_argument(
        "--automatic-jev-governance",
        action="store_true",
        help="authorize automatic, potentially billed Jev classifications (requires --typesafe-ai install)",
    )
    parser.add_argument("--json", action="store_true", help="emit a machine-readable report")
    return parser


def _blocked_early(args: argparse.Namespace, reason: str, next_step: str | None = None) -> int:
    report = {"status": "BLOCKED", "reason": reason}
    if next_step:
        report["next_step"] = next_step
    print(json.dumps(report, ensure_ascii=False) if args.json else f"BLOCKED: {reason}", file=sys.stderr)
    return 2


def main() -> int:
    args = build_parser().parse_args()
    if args.automatic_jev_governance and args.typesafe_ai != "install":
        return _blocked_early(args, "AUTOMATIC_JEV_GOVERNANCE_REQUIRES_TYPESAFE_INSTALL")
    if sys.version_info < (3, 10):
        return _blocked_early(
            args, "PYTHON_3_10_REQUIRED", "Install or select Python 3.10 or newer, then rerun the installer."
        )
    if importlib.util.find_spec("jsonschema") is None:
        return _blocked_early(
            args,
            "JSONSCHEMA_REQUIRED",
            "Install the jsonschema package for this Python interpreter, then rerun the installer.",
        )
    package = _package()
    return package.cli.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
