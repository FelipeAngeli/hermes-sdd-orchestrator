#!/usr/bin/env python3
"""Require a SAFE verdict from a pinned Hermes Skills Guard checkout."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

PINNED_HERMES_COMMIT = "4e9d3c713a3e3d47319ab18a8d8dfade5665270d"
PINNED_SCANNER_VERSION = "skills-guard-v5"
DEFAULT_SKILL = Path("skills/orchestrate/sdd-orchestrator")


def git_head(repository: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip() or "cannot resolve scanner commit")
    return result.stdout.strip()


def require_clean_checkout(repository: Path) -> None:
    result = subprocess.run(
        ["git", "-C", str(repository), "status", "--porcelain", "--untracked-files=all"],
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip() or "cannot inspect scanner checkout")
    if result.stdout.strip():
        raise RuntimeError("scanner checkout is not clean; refusing mutable scanner bytes")


def git_blob(repository: Path, commit: str, relative: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repository), "cat-file", "blob", f"{commit}:{relative}"],
        capture_output=True,
        timeout=20,
        check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or f"cannot read {relative} from pinned scanner commit")
    return result.stdout


def load_scanner(hermes_root: Path, commit: str) -> ModuleType:
    scanner_file = hermes_root / "tools" / "skills_guard.py"
    source = scanner_file.read_bytes()
    if source != git_blob(hermes_root, commit, "tools/skills_guard.py"):
        raise RuntimeError("scanner source differs from pinned commit")
    module_name = "pinned_hermes_skills_guard"
    module = ModuleType(module_name)
    module.__file__ = str(scanner_file)
    sys.modules[module_name] = module
    try:
        exec(compile(source, str(scanner_file), "exec"), module.__dict__)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise RuntimeError(f"scanner import failed: {exc.__class__.__name__}: {exc}") from exc
    return module


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--repo", default=".", help="Repository root (default: current directory).")
    result.add_argument("--skill", default=str(DEFAULT_SKILL), help="Skill directory, absolute or relative to --repo.")
    result.add_argument(
        "--hermes-root",
        default=os.environ.get("HERMES_AGENT_ROOT", str(Path.home() / ".hermes" / "hermes-agent")),
        help="Pinned hermes-agent checkout containing tools/skills_guard.py.",
    )
    result.add_argument("--expected-commit", default=PINNED_HERMES_COMMIT, help=argparse.SUPPRESS)
    result.add_argument("--expected-version", default=PINNED_SCANNER_VERSION, help=argparse.SUPPRESS)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    repo = Path(args.repo).expanduser().resolve()
    hermes_root = Path(args.hermes_root).expanduser().resolve()
    skill = Path(args.skill).expanduser()
    skill = skill.resolve() if skill.is_absolute() else (repo / skill).resolve()
    try:
        actual_commit = git_head(hermes_root)
        if actual_commit != args.expected_commit:
            raise RuntimeError(
                f"scanner commit mismatch: expected {args.expected_commit}, found {actual_commit}"
            )
        require_clean_checkout(hermes_root)
        scanner = load_scanner(hermes_root, actual_commit)
        actual_version = getattr(scanner, "SCANNER_VERSION", None)
        if actual_version != args.expected_version:
            raise RuntimeError(
                f"scanner version mismatch: expected {args.expected_version}, found {actual_version}"
            )
        if not skill.is_dir():
            raise RuntimeError(f"skill directory unavailable: {skill}")
        result = scanner.scan_skill(skill, source="community")
        print(scanner.format_scan_report(result))
        print(f"Scanner provenance: {actual_version}; commit {actual_commit}")
        if result.verdict != "safe":
            print(f"SKILL_SECURITY_BLOCKED: expected SAFE, found {result.verdict.upper()}", file=sys.stderr)
            return 1
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"SKILL_SECURITY_CHECK_FAILED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
