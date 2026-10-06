#!/usr/bin/env python3
"""Lay out an Obsidian project container as a Karpathy LLM Wiki.

Each project container becomes the wiki root::

    SCHEMA.md  index.md  log.md
    raw/{articles,papers,transcripts,assets}/   Layer 1: immutable sources
    entities/ concepts/ comparisons/ queries/   Layer 2: agent-owned pages

The orchestrator stays hidden under ``.hermes/`` and ``.hermes-runtime/``.

``init`` creates the missing skeleton and never overwrites a file. ``plan``
maps legacy project folders onto the layout without touching the disk.
``migrate`` applies that plan file by file with copy -> verify SHA-256 ->
remove, refuses every conflict before the first move, never follows a
symlink, and appends the move list to ``log.md``. ``check`` reports what does
not follow the layout. Stdlib only; nothing here contacts the network.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import stat
import sys
from pathlib import Path, PurePosixPath
from typing import Any

LAYOUT_VERSION = 1
DIRECTORIES = (
    "raw/articles",
    "raw/papers",
    "raw/transcripts",
    "raw/assets",
    "entities",
    "concepts",
    "comparisons",
    "queries",
)
SKELETON_FILES = ("SCHEMA.md", "index.md", "log.md")
#: Top-level entries that already belong to the wiki or to hidden state.
WIKI_ROOTS = frozenset({"raw", "entities", "concepts", "comparisons", "queries", "_archive", "_meta"})
HIDDEN_PREFIX = "."
LOG_ROTATION = re.compile(r"^log-\d{4}\.md$")
#: Legacy folder name -> wiki destination prefix. Matching is case-insensitive.
LEGACY_FOLDERS = {
    "sessions": "raw/transcripts",
    "_meetings": "raw/transcripts/meetings",
    "decisions": "concepts",
    "decisões": "concepts",
    "boards": "queries/boards",
    "_discovery": "raw/articles/discovery",
    "_references": "raw/articles/references",
    "_reviews": "raw/articles/reviews",
    "demandas": "raw/articles/demandas",
}
CONTROLLER_FILES = {"skills-lock.json": ".hermes/skills-lock.json"}
PAPER_SUFFIXES = {".pdf"}
ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".heic", ".mp4", ".mov", ".mp3", ".wav"}
QUERY_SUFFIXES = {".base"}
#: Folder index notes stay raw sources instead of becoming Layer-2 pages.
FOLDER_INDEX_NAMES = frozenset({"readme.md", "_index.md", "index.md"})
PAGE_SECTIONS = (("entities", "Entities"), ("concepts", "Concepts"), ("comparisons", "Comparisons"), ("queries", "Queries"))


class WikiError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


# --------------------------------------------------------------------------- skeleton


def _schema(project: str, today: str) -> str:
    return f"""---
layout_version: {LAYOUT_VERSION}
created: {today}
---

# Wiki Schema — {project}

## Domain
Engineering knowledge for the {project} project: product decisions, architecture,
modules, integrations and the SDD demands that changed them.

## Layout
- `raw/` — Layer 1, immutable sources. Read, never edit; corrections go into wiki pages.
  - `raw/articles/` — demand artifacts (PRD, techspec, tasks, reviews), discovery notes, references.
  - `raw/papers/` — PDFs and papers.
  - `raw/transcripts/` — session logs, meetings, interviews.
  - `raw/assets/` — images and diagrams referenced by sources.
- `entities/` — one page per module, service, integration, person or organization.
- `concepts/` — one page per concept, rule or decision (decisions use `type: decision`).
- `comparisons/` — side-by-side analyses.
- `queries/` — filed answers worth keeping, plus Obsidian Bases under `queries/boards/`.
- `.hermes/` and `.hermes-runtime/` — SDD orchestrator state. Hidden; not wiki content.

## Conventions
- File names: lowercase, hyphens, no spaces for new pages (`payments-module.md`).
- Every wiki page starts with YAML frontmatter (below) and links at least two pages with `[[wikilinks]]`.
- Every new page is added to `index.md`; every action is appended to `log.md`.
- Bump `updated` on every edit. On pages synthesizing 3+ sources, end claims with `^[raw/...]`.

## Frontmatter
```yaml
---
title: Page Title
created: YYYY-MM-DD
updated: YYYY-MM-DD
type: entity | concept | decision | comparison | query | summary
tags: [from taxonomy below]
sources: [raw/articles/source-name.md]
confidence: high | medium | low   # optional
---
```

Raw sources carry `source_url`, `ingested` and `sha256` (of the body) when ingested.

## Tag Taxonomy
- Product: feature, flow, ux, accessibility
- Engineering: architecture, module, api, data, testing, security, performance
- Delivery: demand, decision, incident, release
- Meta: comparison, timeline, open-question

Add a tag here before using it.

## Page Thresholds
- Create a page when an entity or concept appears in 2+ sources or is central to one.
- Split a page over ~200 lines; archive superseded pages under `_archive/` and drop them from the index.

## Update Policy
Newer sources supersede older ones. Genuine contradictions keep both claims with dates and
sources, set `contradictions: [page]` and are flagged for review.
"""


def _index(project: str, today: str) -> str:
    sections = "\n\n".join(f"## {title}" for _, title in PAGE_SECTIONS)
    return f"""# Wiki Index — {project}

> Content catalog. Every wiki page listed under its type with a one-line summary.
> Last updated: {today} | Total pages: 0

{sections}
"""


def _log(today: str) -> str:
    return f"""# Wiki Log

> Chronological record of all wiki actions. Append-only.
> Format: `## [YYYY-MM-DD] action | subject`
> Actions: ingest, update, query, lint, create, archive, delete, migrate
> When this file exceeds 500 entries, rotate: rename to log-YYYY.md, start fresh.

## [{today}] create | Wiki initialized
- Layout version {LAYOUT_VERSION}: SCHEMA.md, index.md, log.md, raw/, entities/, concepts/, comparisons/, queries/
"""


def skeleton_files(*, project: str, today: str) -> dict[str, bytes]:
    """Return the skeleton files, relative to the container, as bytes."""
    return {
        "SCHEMA.md": _schema(project, today).encode("utf-8"),
        "index.md": _index(project, today).encode("utf-8"),
        "log.md": _log(today).encode("utf-8"),
    }


def _require_container(container: Path) -> Path:
    container = Path(container)
    if not container.is_absolute():
        raise WikiError("WIKI_CONTAINER_INVALID", f"{container} must be absolute")
    if not container.is_dir() or container.is_symlink():
        raise WikiError("WIKI_CONTAINER_INVALID", f"{container} must be an existing real directory")
    for ancestor in (container, *container.parents):
        if (ancestor / ".obsidian").is_dir() or (ancestor / ".hermes" / "obsidian.json").is_file():
            return container
    raise WikiError("WIKI_VAULT_REQUIRED", f"{container} is not inside an Obsidian vault")


def _create_exclusive(path: Path, content: bytes) -> bool:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    return True


def _safe_mkdir(container: Path, relative: str) -> None:
    current = container
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            raise WikiError("WIKI_PATH_UNSAFE", f"{current} is a symlink")
        if not current.exists():
            current.mkdir()
        elif not current.is_dir():
            raise WikiError("WIKI_PATH_UNSAFE", f"{current} is not a directory")


def init(container: Path, *, project: str, today: str) -> list[str]:
    """Create the missing skeleton; return the relative paths created."""
    container = _require_container(container)
    created: list[str] = []
    for directory in DIRECTORIES:
        if not (container / directory).is_dir():
            _safe_mkdir(container, directory)
            created.append(f"{directory}/")
    for relative, content in skeleton_files(project=project, today=today).items():
        if (container / relative).is_symlink():
            raise WikiError("WIKI_PATH_UNSAFE", f"{relative} is a symlink")
        if _create_exclusive(container / relative, content):
            created.append(relative)
    return created


# --------------------------------------------------------------------------- classification


def _is_hidden_or_wiki(parts: tuple[str, ...]) -> bool:
    top = parts[0]
    if top.startswith(HIDDEN_PREFIX) or top in WIKI_ROOTS:
        return True
    return len(parts) == 1 and (top in SKELETON_FILES or LOG_ROTATION.match(top) is not None)


def classify(relative: str) -> str | None:
    """Return the wiki destination of a container-relative file, or None to keep it."""
    path = PurePosixPath(relative)
    parts = path.parts
    if not parts or _is_hidden_or_wiki(parts):
        return None
    if len(parts) == 1 and parts[0] in CONTROLLER_FILES:
        return CONTROLLER_FILES[parts[0]]
    top = parts[0]
    legacy = LEGACY_FOLDERS.get(top.casefold())
    if legacy is not None and len(parts) > 1:
        if not legacy.startswith("raw/") and len(parts) == 2 and parts[1].casefold() in FOLDER_INDEX_NAMES:
            return str(PurePosixPath("raw/articles", top.casefold(), parts[1]))
        return str(PurePosixPath(legacy, *parts[1:]))
    suffix = path.suffix.lower()
    if len(parts) > 1 and top.lower().endswith(" assets"):
        return str(PurePosixPath("raw/assets", *parts))
    if suffix in ASSET_SUFFIXES:
        return str(PurePosixPath("raw/assets", *parts))
    if suffix in PAPER_SUFFIXES:
        return str(PurePosixPath("raw/papers", *parts))
    if len(parts) == 1 and suffix in QUERY_SUFFIXES:
        return str(PurePosixPath("queries", *parts))
    return str(PurePosixPath("raw/articles", *parts))


# --------------------------------------------------------------------------- planning


def _walk(container: Path) -> tuple[list[str], list[str]]:
    """Return (regular files, symlinks) relative to the container, never following links."""
    files: list[str] = []
    links: list[str] = []
    for current, dirnames, filenames in os.walk(container, followlinks=False):
        here = Path(current)
        relative_dir = here.relative_to(container)
        keep = []
        for name in sorted(dirnames):
            entry = here / name
            relative = (relative_dir / name).as_posix()
            if entry.is_symlink():
                links.append(relative)
            elif not (relative_dir == Path(".") and name.startswith(HIDDEN_PREFIX)):
                keep.append(name)
        dirnames[:] = keep
        for name in sorted(filenames):
            entry = here / name
            relative = (relative_dir / name).as_posix()
            mode = entry.lstat().st_mode
            if stat.S_ISLNK(mode):
                links.append(relative)
            elif stat.S_ISREG(mode):
                files.append(relative)
    return files, links


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def plan(container: Path) -> dict[str, Any]:
    """Describe the migration without touching the disk."""
    container = _require_container(container)
    files, links = _walk(container)
    moves: list[dict[str, str]] = []
    duplicates: list[str] = []
    conflicts: list[dict[str, str]] = []
    claimed: dict[str, str] = {}
    for relative in files:
        destination = classify(relative)
        if destination is None or destination == relative:
            continue
        if destination in claimed:
            conflicts.append({"source": relative, "destination": destination, "reason": f"also claimed by {claimed[destination]}"})
            continue
        claimed[destination] = relative
        target = container / destination
        if target.is_symlink() or (target.exists() and not target.is_file()):
            conflicts.append({"source": relative, "destination": destination, "reason": "destination is not a regular file"})
        elif target.exists():
            if _sha256(target) == _sha256(container / relative):
                duplicates.append(relative)
            else:
                conflicts.append({"source": relative, "destination": destination, "reason": "destination exists with different content"})
        else:
            moves.append({"source": relative, "destination": destination})
    return {
        "container": str(container),
        "layout_version": LAYOUT_VERSION,
        "moves": moves,
        "deduplicated": duplicates,
        "conflicts": conflicts,
        "skipped_symlinks": sorted(links),
    }


# --------------------------------------------------------------------------- migration


def _copy_verified(container: Path, source: str, destination: str) -> None:
    source_path, target = container / source, container / destination
    _safe_mkdir(container, str(PurePosixPath(destination).parent))
    expected = _sha256(source_path)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
    try:
        with open(source_path, "rb") as reader, os.fdopen(descriptor, "wb") as writer:
            descriptor = -1
            shutil.copyfileobj(reader, writer)
            writer.flush()
            os.fsync(writer.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    shutil.copystat(source_path, target, follow_symlinks=False)
    if _sha256(target) != expected:
        target.unlink()
        raise OSError(f"hash mismatch copying {source}")


def _prune_empty(container: Path, relatives: list[str]) -> None:
    candidates = sorted({PurePosixPath(item).parent for item in relatives}, key=lambda p: len(p.parts), reverse=True)
    for directory in candidates:
        current = directory
        while current.parts:
            path = container / current
            if path.is_symlink() or not path.is_dir() or any(path.iterdir()):
                break
            path.rmdir()
            current = current.parent


def _wikilink(relative: str) -> str:
    return f"[[{PurePosixPath(relative).stem}]]"


def _index_pages(container: Path, moved: list[str]) -> None:
    """Add moved Layer-2 pages to index.md under their section, alphabetically."""
    index = container / "index.md"
    text = index.read_text(encoding="utf-8")
    added = False
    for prefix, title in PAGE_SECTIONS:
        pages = sorted(
            item for item in moved
            if item.startswith(f"{prefix}/") and item.endswith(".md") and _wikilink(item) not in text
        )
        if not pages:
            continue
        heading = f"## {title}\n"
        if heading not in text:
            text = text.rstrip("\n") + f"\n\n{heading}"
        entries = "".join(f"- {_wikilink(item)} — migrated from the legacy layout; summary pending\n" for item in pages)
        position = text.index(heading) + len(heading)
        text = text[:position] + entries + text[position:]
        added = True
    if added:
        total = len(_layer_two_pages(container))
        text = re.sub(r"Total pages: \d+", f"Total pages: {total}", text, count=1)
        index.write_text(text, encoding="utf-8")


def _append_log(container: Path, today: str, subject: str, lines: list[str]) -> None:
    entry = f"\n## [{today}] migrate | {subject}\n" + "".join(f"- {line}\n" for line in lines)
    with open(container / "log.md", "a", encoding="utf-8") as handle:
        handle.write(entry)


def migrate(container: Path, *, project: str, today: str) -> dict[str, Any]:
    """Apply the plan: copy -> verify -> remove, all conflicts refused up front."""
    container = _require_container(container)
    report = plan(container)
    if report["conflicts"]:
        raise WikiError(
            "WIKI_MIGRATION_CONFLICT",
            "; ".join(f"{item['source']} -> {item['destination']}: {item['reason']}" for item in report["conflicts"]),
        )
    created = init(container, project=project, today=today)
    if not report["moves"] and not report["deduplicated"]:
        report.update(status="ALREADY_MIGRATED" if not created else "INITIALIZED", created=created)
        return report
    moved: list[str] = []
    try:
        for move in report["moves"]:
            _copy_verified(container, move["source"], move["destination"])
            (container / move["source"]).unlink()
            moved.append(move["destination"])
        for duplicate in report["deduplicated"]:
            (container / duplicate).unlink()
    except OSError as error:
        _append_log(container, today, "Legacy migration interrupted", [
            f"{move['source']} -> {move['destination']}" for move in report["moves"] if move["destination"] in moved
        ] + [f"stopped: {error}"])
        raise WikiError(
            "WIKI_MIGRATION_INCOMPLETE",
            f"{len(moved)} of {len(report['moves'])} files moved before: {error}. Every source not yet moved "
            "is still in place; rerun migrate to continue.",
        ) from error
    _prune_empty(container, [move["source"] for move in report["moves"]] + report["deduplicated"])
    _index_pages(container, moved)
    _append_log(
        container,
        today,
        "Legacy project folders moved into the LLM Wiki layout",
        [f"{move['source']} -> {move['destination']}" for move in report["moves"]]
        + [f"{item} (identical copy already in place, removed)" for item in report["deduplicated"]],
    )
    report.update(status="APPLIED", created=created)
    return report


# --------------------------------------------------------------------------- check


def _layer_two_pages(container: Path) -> list[str]:
    pages = []
    for prefix, _ in PAGE_SECTIONS:
        root = container / prefix
        if root.is_dir() and not root.is_symlink():
            pages.extend(
                path.relative_to(container).as_posix()
                for path in sorted(root.rglob("*.md"))
                if path.is_file() and not path.is_symlink()
            )
    return pages


def check(container: Path) -> dict[str, Any]:
    """Report what does not follow the LLM Wiki layout. Never writes."""
    container = _require_container(container)
    findings: list[dict[str, str]] = []
    for relative in (*SKELETON_FILES, *DIRECTORIES):
        if not (container / relative).exists():
            findings.append({"code": "WIKI_SKELETON_MISSING", "path": relative})
    for entry in sorted(container.iterdir()):
        name = entry.name
        if name.startswith(HIDDEN_PREFIX) or name in WIKI_ROOTS or name in SKELETON_FILES or LOG_ROTATION.match(name):
            continue
        findings.append({"code": "WIKI_LEGACY_ENTRY", "path": name})
    index = container / "index.md"
    if index.is_file():
        text = index.read_text(encoding="utf-8")
        for page in _layer_two_pages(container):
            if _wikilink(page) not in text:
                findings.append({"code": "WIKI_PAGE_NOT_INDEXED", "path": page})
    return {"container": str(container), "layout_version": LAYOUT_VERSION, "findings": findings}


# --------------------------------------------------------------------------- CLI


def _today(value: str | None) -> str:
    if value is None:
        return dt.date.today().isoformat()
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError:
        raise WikiError("WIKI_DATE_INVALID", f"{value!r} must be YYYY-MM-DD") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "migrate", "check"):
        command = commands.add_parser(name)
        command.add_argument("--container", required=True, help="absolute path of the Obsidian project container")
        command.add_argument("--json", action="store_true")
        if name in {"init", "migrate"}:
            command.add_argument("--project", help="project name for SCHEMA.md and index.md (default: folder name)")
            command.add_argument("--date", help="YYYY-MM-DD for log entries (default: today)")
            command.add_argument("--apply", action="store_true", help="write; without it the command is a dry run")
    args = parser.parse_args(argv)
    container = Path(args.container)
    try:
        if args.command == "check":
            report = check(container)
            code = 2 if report["findings"] else 0
        else:
            project = args.project or container.name
            today = _today(args.date)
            if not args.apply:
                report = plan(container)
                missing = [item for item in (*SKELETON_FILES, *DIRECTORIES) if not (container / item).exists()]
                report["planned_skeleton"] = missing
                if args.command == "init":
                    report["moves"] = []
                report["status"] = "BLOCKED" if report["conflicts"] and args.command == "migrate" else "READY"
                code = 2 if report["status"] == "BLOCKED" else 0
            elif args.command == "init":
                report = {"status": "APPLIED", "created": init(container, project=project, today=today)}
                code = 0
            else:
                report = migrate(container, project=project, today=today)
                code = 0
    except WikiError as error:
        report, code = {"status": "BLOCKED", "reason": error.code, "detail": error.detail}, 2
    print(json.dumps(report, ensure_ascii=False, indent=None if args.json else 2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
