#!/usr/bin/env python3
"""Refuse orchestration changes that do not update their documentation.

Every orchestration file has exactly one owning page in ``docs/doc-map.json``.
A change is accepted when, for every changed source file, its owning page and
``CHANGELOG.md`` change in the same diff — or the commit message carries an
explicit ``Docs-Impact: none - <reason>`` trailer.

Only *modified*, *renamed* or *deleted* existing tests are exempt: editing a
test does not change what the orchestration does. Adding a test file still
needs the testing page, because the page lists every suite.

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
DEFAULT_SOURCE_PREFIXES = ("skills/", "tools/", ".githooks/", ".github/workflows/")
TRAILER = re.compile(r"^Docs-Impact:\s*none\s*[-–—:]\s*\S.*$", re.IGNORECASE | re.MULTILINE)
TEST_FILE = re.compile(r"(^|/)tests/test_[^/]+\.py$")


class CheckError(RuntimeError):
    pass


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True, timeout=30, check=False)
    if result.returncode:
        raise CheckError((result.stderr or result.stdout).strip() or "git failed")
    return result.stdout


def changes(repo: Path, staged: bool, base: str | None) -> list[tuple[str, str]]:
    """(status letter, path) for every changed file; renames report the new path."""
    args = ["diff", "--name-status", "-M"] + (["--cached"] if staged else [f"{base}..HEAD"])
    entries = []
    for line in git(repo, *args).splitlines():
        parts = line.split("\t")
        entries.append((parts[0][0], parts[-1]))
    return entries


def messages(repo: Path, base: str | None, message_file: str | None) -> str:
    text = Path(message_file).read_text(encoding="utf-8") if message_file else ""
    if base:
        text += "\n" + git(repo, "log", "--format=%B", f"{base}..HEAD")
    return text


def owning_page(relative: str, doc_map: dict[str, list[str]]) -> list[str]:
    return [doc for doc, globs in doc_map.items() if any(fnmatch.fnmatch(relative, g) for g in globs)]


def check(repo: Path, staged: bool, base: str | None, message_file: str | None, prefixes: tuple[str, ...]) -> list[str]:
    doc_map = json.loads((repo / DOC_MAP).read_text(encoding="utf-8"))["docs"]
    changed = changes(repo, staged, base)
    changed_paths = {path for _, path in changed}
    sources = [
        (status, path) for status, path in changed
        if path.startswith(prefixes) and not (status in "MRD" and TEST_FILE.search(path))
    ]
    if not sources or TRAILER.search(messages(repo, base, message_file)):
        return []
    problems: list[str] = []
    for status, path in sources:
        pages = owning_page(path, doc_map)
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
