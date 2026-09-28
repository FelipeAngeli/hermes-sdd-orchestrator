#!/usr/bin/env python3
"""Cut a SemVer release: bump SKILL.md, roll CHANGELOG, commit and tag.

Run on the improvement's branch, never on ``main``. Dry run by default.

The level comes from the headings of the ``## Unreleased`` section unless
``--level`` is given:

* ``### Breaking`` (or ``### Removed``)  → MAJOR
* ``### Added`` or ``### Changed``       → MINOR
* anything else (``### Fixed``, ``### Docs``…) → PATCH

``--apply`` writes ``version:`` in SKILL.md, renames ``## Unreleased`` to
``## X.Y.Z - <date>`` under a fresh empty ``## Unreleased``, commits
``chore(release): vX.Y.Z`` and creates the annotated tag ``vX.Y.Z``. It
never pushes: push the branch and the tag explicitly after review.

Exit status: 0 ok, 1 refused (reason printed), 2 usage or Git error.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

SKILL = "skills/orchestrate/sdd-orchestrator/SKILL.md"
CHANGELOG = "CHANGELOG.md"
PROTECTED_BRANCHES = {"main", "master"}
VERSION_LINE = re.compile(r"^version: *(\S+) *$", re.M)
RELEASE_HEADING = re.compile(r"^## (\d+\.\d+\.\d+)\b", re.M)
UNRELEASED = re.compile(r"^## Unreleased[ \t]*\n(?P<body>.*?)(?=^## |\Z)", re.M | re.S)
LEVELS = ("major", "minor", "patch")


class ReleaseError(RuntimeError):
    """A refusal with a stable code as the first word of the message."""


def bump(version: str, level: str) -> str:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ReleaseError(f"VERSION_INVALID: {version!r} is not MAJOR.MINOR.PATCH")
    major, minor, patch = map(int, version.split("."))
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def infer_level(unreleased_body: str) -> str:
    headings = {h.strip().lower() for h in re.findall(r"^### (.+)$", unreleased_body, re.M)}
    if not re.search(r"^\s*[-*] \S", unreleased_body, re.M):
        raise ReleaseError("UNRELEASED_EMPTY: add entries under '## Unreleased' before releasing")
    if headings & {"breaking", "removed"}:
        return "major"
    if headings & {"added", "changed"}:
        return "minor"
    return "patch"


def changelog_versions(text: str) -> list[str]:
    return RELEASE_HEADING.findall(text)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True, timeout=30, check=False)
    if result.returncode:
        raise OSError((result.stderr or result.stdout).strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def plan(repo: Path, level: str | None) -> dict[str, str]:
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if branch in PROTECTED_BRANCHES or branch == "HEAD":
        raise ReleaseError(f"BRANCH_NOT_ALLOWED: release from the improvement branch, not {branch!r}")
    skill_text = (repo / SKILL).read_text(encoding="utf-8")
    match = VERSION_LINE.search(skill_text)
    if not match:
        raise ReleaseError(f"VERSION_MISSING: no 'version:' line in {SKILL}")
    changelog = (repo / CHANGELOG).read_text(encoding="utf-8")
    unreleased = UNRELEASED.search(changelog)
    if not unreleased:
        raise ReleaseError("UNRELEASED_MISSING: CHANGELOG.md needs a '## Unreleased' section")
    inferred = infer_level(unreleased.group("body"))
    current = match.group(1)
    new = bump(current, level or inferred)
    if git(repo, "tag", "--list", f"v{new}"):
        raise ReleaseError(f"TAG_EXISTS: v{new} already exists")
    return {"branch": branch, "current": current, "new": new, "level": level or inferred}


def apply(repo: Path, info: dict[str, str], date: str) -> None:
    if git(repo, "status", "--porcelain"):
        raise ReleaseError("WORKTREE_DIRTY: commit or stash changes before releasing")
    skill_path, changelog_path = repo / SKILL, repo / CHANGELOG
    skill_path.write_text(VERSION_LINE.sub(f"version: {info['new']}", skill_path.read_text(encoding="utf-8"), count=1), encoding="utf-8")
    changelog = changelog_path.read_text(encoding="utf-8")
    changelog = re.sub(r"^## Unreleased[ \t]*\n", f"## Unreleased\n\n## {info['new']} - {date}\n", changelog, count=1, flags=re.M)
    changelog_path.write_text(changelog, encoding="utf-8")
    tag = f"v{info['new']}"
    git(repo, "add", "--", SKILL, CHANGELOG)
    git(repo, "commit", "-q", "-m", f"chore(release): {tag}", "-m", "Docs-Impact: none - version bump and changelog roll only")
    git(repo, "tag", "-a", tag, "-m", f"{tag}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--repo", default=".", help="repository root (default: current directory)")
    parser.add_argument("--level", choices=LEVELS, help="override the level inferred from '## Unreleased'")
    parser.add_argument("--apply", action="store_true", help="write, commit and tag (default: dry run)")
    parser.add_argument("--date", help="release date YYYY-MM-DD (default: today)")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    try:
        if args.apply and git(repo, "status", "--porcelain"):
            raise ReleaseError("WORKTREE_DIRTY: commit or stash changes before releasing")
        info = plan(repo, args.level)
        summary = f"{info['current']} -> {info['new']} ({info['level']}) on {info['branch']}"
        if not args.apply:
            print(f"release: DRY RUN {summary}; rerun with --apply")
            return 0
        apply(repo, info, args.date or dt.date.today().isoformat())
        print(f"release: {summary}; tagged v{info['new']}")
        print(f"next: git push -u origin {info['branch']} && git push origin v{info['new']}")
        return 0
    except ReleaseError as error:
        print(f"release: REFUSED {error}")
        return 1
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"release: ERROR {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
