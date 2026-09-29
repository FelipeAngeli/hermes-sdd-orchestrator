#!/usr/bin/env python3
"""Refuse orchestration changes that do not update their documentation.

Every orchestration file has exactly one owning page in ``docs/doc-map.json``.
A change is accepted when, for every changed source file, its owning page and
``CHANGELOG.md`` change in the same diff — or the commit that changes it
carries an explicit ``Docs-Impact: none - <reason>`` trailer. In ``--base``
mode each commit's waiver covers only the files that commit touched, so a
release commit's waiver never excuses another commit in the range.

Only *modified* or *deleted* existing tests are exempt: editing a test does
not change what the orchestration does. Adding a test file still needs the
testing page, because the page lists every suite. A rename counts as deleting
the old path and adding the new one.

Modes:
  --staged              check the index (pre-commit hook)
  --base <rev>          check <rev>..HEAD (CI, pull requests)

Exit status: 0 in sync, 1 documentation missing, 2 usage or Git error.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

DOC_MAP = "docs/doc-map.json"
CHANGELOG = "CHANGELOG.md"
DEFAULT_SOURCE_PREFIXES = (
    "skills/", "tools/", ".githooks/", ".github/workflows/", "hermes-pack.yaml"
)
TRAILER = re.compile(r"^Docs-Impact:\s*none\s*[-–—:]\s*\S.*$", re.IGNORECASE | re.MULTILINE)
TEST_FILE = re.compile(r"(^|/)tests/test_[^/]+\.py$")


class CheckError(RuntimeError):
    pass


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True, timeout=30, check=False)
    if result.returncode:
        raise CheckError((result.stderr or result.stdout).strip() or "git failed")
    return result.stdout


def parse_name_status(text: str) -> list[tuple[str, str]]:
    """Parse Git name-status output, expanding a rename to old delete + new add."""
    entries: list[tuple[str, str]] = []
    for line in text.splitlines():
        parts = line.split("\t")
        if parts[0].startswith("R") and len(parts) == 3:
            entries += [("D", parts[1]), ("A", parts[2])]
        else:
            entries.append((parts[0][0], parts[-1]))
    return entries


def changes(repo: Path, staged: bool, base: str | None) -> list[tuple[str, str]]:
    """(status letter, path) for every changed file.

    A rename yields two entries, so moving source into a test path is not exempt.
    """
    args = ["diff", "--name-status", "-M"] + (["--cached"] if staged else [f"{base}..HEAD"])
    return parse_name_status(git(repo, *args))


def commit_paths(repo: Path, sha: str) -> set[str]:
    """All paths attributable to one commit, including merge resolutions."""
    record = git(repo, "rev-list", "--parents", "-n", "1", sha).split()
    parents = record[1:]
    if not parents:
        output = git(repo, "diff-tree", "--root", "--no-commit-id", "--name-status", "-r", "-M", sha)
        return {path for _, path in parse_name_status(output)}
    paths: set[str] = set()
    # A merge diff is empty without an explicit parent. Union every parent diff
    # so a change introduced only while resolving the merge cannot disappear.
    for parent in parents:
        output = git(repo, "diff", "--name-status", "-M", parent, sha)
        paths.update(path for _, path in parse_name_status(output))
    return paths


def waived_paths(repo: Path, base: str) -> set[str]:
    """Paths touched ONLY by commits that carry their own Docs-Impact waiver.

    A waiver covers the commit it is written on, never the rest of the range:
    a release commit's waiver must not excuse an undocumented feature commit.
    """
    waived: set[str] = set()
    unwaived: set[str] = set()
    for sha in git(repo, "rev-list", f"{base}..HEAD").split():
        body = git(repo, "log", "-1", "--format=%B", sha)
        files = commit_paths(repo, sha)
        (waived if TRAILER.search(body) else unwaived).update(files)
    return waived - unwaived


def owning_page(relative: str, doc_map: dict[str, list[str]]) -> list[str]:
    return [doc for doc, globs in doc_map.items() if any(fnmatch.fnmatch(relative, g) for g in globs)]


def map_at(repo: Path, ref: str) -> dict[str, list[str]]:
    """Load pre-diff ownership; the first docs PR legitimately has no map."""
    exists = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{ref}:{DOC_MAP}"],
        text=True, capture_output=True, timeout=30, check=False,
    )
    if exists.returncode:
        return {}
    return json.loads(git(repo, "show", f"{ref}:{DOC_MAP}"))["docs"]


def check(repo: Path, staged: bool, base: str | None, message_file: str | None, prefixes: tuple[str, ...]) -> list[str]:
    doc_map = json.loads((repo / DOC_MAP).read_text(encoding="utf-8"))["docs"]
    old_doc_map = map_at(repo, base or "HEAD")
    changed = changes(repo, staged, base)
    changed_paths = {path for _, path in changed}
    sources = [
        (status, path) for status, path in changed
        if path.startswith(prefixes) and not (status in "MD" and TEST_FILE.search(path))
    ]
    if base:
        exempt = waived_paths(repo, base)
        sources = [(status, path) for status, path in sources if path not in exempt]
    elif message_file and TRAILER.search(Path(message_file).read_text(encoding="utf-8")):
        return []
    if not sources:
        return []
    problems: list[str] = []
    for status, path in sources:
        pages = owning_page(path, old_doc_map if status == "D" else doc_map)
        if not pages:
            problems.append(f"{path}: no owning page in {DOC_MAP} — add it to the map")
        elif not set(pages) & changed_paths:
            problems.append(f"{path}: update {pages[0]}")
    if CHANGELOG not in changed_paths:
        problems.append(f"orchestration changed: add an entry to {CHANGELOG}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--repo", default=".", help="repository root (default: current directory)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true", help="check the staged changes (pre-commit)")
    mode.add_argument("--base", help="check <base>..HEAD (CI / pull request)")
    parser.add_argument("--message-file", help="commit message file to read a Docs-Impact trailer from (commit-msg hook)")
    parser.add_argument("--source-prefix", action="append", help="path prefix that counts as orchestration source (repeatable)")
    args = parser.parse_args(argv)
    prefixes = tuple(args.source_prefix) if args.source_prefix else DEFAULT_SOURCE_PREFIXES
    try:
        problems = check(Path(args.repo).resolve(), args.staged, args.base, args.message_file, prefixes)
    except (CheckError, OSError, json.JSONDecodeError, KeyError) as error:
        print(f"docs-sync: ERROR {error}")
        return 2
    if not problems:
        print("docs-sync: OK")
        return 0
    print("docs-sync: documentation is out of date with the orchestration change:")
    for problem in problems:
        print(f"  - {problem}")
    print("See docs/maintaining-docs.md. For a change with no documentation impact, add the trailer\n"
          "  Docs-Impact: none - <reason>\nto the commit message.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
