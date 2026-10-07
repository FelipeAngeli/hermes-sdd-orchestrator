#!/usr/bin/env python3
"""Lay out an Obsidian project container as a Karpathy LLM Wiki.

Each project container becomes the wiki root::

    SCHEMA.md  index.md  log.md
    raw/{articles,papers,transcripts,assets}/   Layer 1: immutable sources
    entities/ concepts/ comparisons/ queries/   Layer 2: agent-owned pages

The orchestrator stays hidden under ``.hermes/`` and ``.hermes-runtime/``.

``init`` creates the missing skeleton and never overwrites a file. ``plan``
maps legacy project folders onto the layout without touching the disk.
``migrate`` applies that plan: every conflict is refused before the first
move, and each file is copied through no-follow descriptors, its SHA-256 and
identity re-verified, and only then is the source removed. ``check`` reports
what does not follow the layout. Stdlib only; nothing here contacts the
network.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import stat
import unicodedata
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
#: Top-level entries that already belong to the wiki. Compared case-insensitively.
WIKI_ROOTS = frozenset({"raw", "entities", "concepts", "comparisons", "queries", "_archive", "_meta"})
HIDDEN_PREFIX = "."
LOG_ROTATION = re.compile(r"^log-\d{4}\.md$", re.IGNORECASE)
#: Legacy folder name (case-insensitive) -> wiki destination prefix.
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
#: Only these files become Layer-2 pages when a legacy folder maps to Layer 2.
LAYER_TWO_SUFFIXES = {".md", ".base"}
#: Folder index notes stay raw sources instead of becoming Layer-2 pages.
FOLDER_INDEX_NAMES = frozenset({"readme.md", "_index.md", "index.md"})
#: Finder metadata: the only file migrate deletes without moving, and only to empty a folder it emptied.
FINDER_METADATA = ".DS_Store"
#: A container opted into the wiki carries one of these; migrate --apply requires it.
CONTAINER_MARKERS = ("SCHEMA.md", ".hermes/obsidian.json")
PAGE_SECTIONS = (("entities", "Entities"), ("concepts", "Concepts"), ("comparisons", "Comparisons"), ("queries", "Queries"))

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_CHUNK = 1 << 20


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


# --------------------------------------------------------------------------- container


def _key(relative: str) -> str:
    """Comparison key for a path on a case- and normalization-insensitive volume."""
    return unicodedata.normalize("NFC", relative).casefold()


def _is_hidden(name: str) -> bool:
    return name.startswith(HIDDEN_PREFIX)


def _has_marker(directory: Path) -> bool:
    schema = directory / "SCHEMA.md"
    if schema.is_file() and not schema.is_symlink():
        try:
            with open(schema, "rb") as handle:
                if handle.read(64).startswith(b"---\nlayout_version:"):
                    return True
        except OSError:
            pass
    return os.path.lexists(directory / ".hermes" / "obsidian.json")


#: Controller state that marks a folder as a project of its own.
CONTROLLER_SIGNS = frozenset({"skills-lock.json", ".hermes", ".hermes-runtime", ".hermes.md"})


def _subdirectories(directory: Path) -> list[Path]:
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    return [
        directory / name for name in names
        if not _is_hidden(name) and (directory / name).is_dir() and not (directory / name).is_symlink()
    ]


def _has_legacy_folder(directory: Path) -> bool:
    return any(_key(child.name) in LEGACY_FOLDERS for child in _subdirectories(directory))


def _looks_like_project(directory: Path) -> bool:
    """A child is a project, not a demand folder, when it carries controller state or
    holds its own folders (demands) that in turn hold legacy folders."""
    try:
        names = os.listdir(directory)
    except OSError:
        return False
    if any(_key(name) in CONTROLLER_SIGNS for name in names):
        return True
    return any(
        _key(child.name) not in LEGACY_FOLDERS and _has_legacy_folder(child) for child in _subdirectories(directory)
    )


def _nested_containers(container: Path, *, guess_unmarked: bool) -> list[str]:
    nested: list[str] = []
    try:
        children = sorted(os.listdir(container)) if guess_unmarked else []
    except OSError:
        children = []
    for name in children:
        child = container / name
        if _is_hidden(name) or _key(name) in LEGACY_FOLDERS or _key(name) in WIKI_ROOTS:
            continue
        if child.is_dir() and not child.is_symlink() and _looks_like_project(child):
            nested.append(name)
    for current, dirnames, _ in os.walk(container, followlinks=False):
        here = Path(current)
        dirnames[:] = [name for name in sorted(dirnames) if not _is_hidden(name) and not (here / name).is_symlink()]
        if here != container and _has_marker(here):
            nested.append(here.relative_to(container).as_posix())
            dirnames[:] = []
    return nested


def _require_container(container: Path) -> Path:
    container = Path(container)
    if not container.is_absolute():
        raise WikiError("WIKI_CONTAINER_INVALID", f"{container} must be absolute")
    if container.is_symlink() or not container.is_dir() or Path(os.path.realpath(container)) != container:
        raise WikiError(
            "WIKI_CONTAINER_INVALID",
            f"{container} must be an existing directory reached without symlinks (real path: {os.path.realpath(container)})",
        )
    if os.path.lexists(container / ".obsidian"):
        raise WikiError("WIKI_CONTAINER_IS_VAULT", f"{container} is a vault root; pass one project folder inside it")
    if not any((ancestor / ".obsidian").is_dir() for ancestor in container.parents):
        raise WikiError("WIKI_VAULT_REQUIRED", f"{container} is not inside an Obsidian vault")
    if os.path.lexists(container / ".git"):
        raise WikiError("WIKI_CONTAINER_IS_REPOSITORY", f"{container} is a Git work tree, not a vault project")
    marked = _has_marker(container)
    try:
        nested = _nested_containers(container, guess_unmarked=not marked)
    except OSError as error:
        raise WikiError("WIKI_PATH_UNSAFE", f"cannot inspect {error.filename or container}: {error.strerror or error}") from error
    if nested:
        hint = "" if marked else (
            " If this folder is a single project whose subfolders only group its own demands, mark it first "
            "by installing the orchestrator into it or by adding a SCHEMA.md that starts with the "
            "'layout_version' front matter, then rerun."
        )
        raise WikiError(
            "WIKI_CONTAINER_NESTED",
            f"{container} holds other wiki or project containers: {', '.join(nested[:5])}.{hint}",
        )
    return container


def _open_root(container: Path) -> int:
    return os.open(container, os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC)


def _open_dir(root_fd: int, parts: tuple[str, ...], *, create: bool) -> int:
    """Open a directory below ``root_fd`` one component at a time, never following a link."""
    fd = os.dup(root_fd)
    try:
        for part in parts:
            try:
                child = os.open(part, os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, 0o755, dir_fd=fd)
                child = os.open(part, os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _split(relative: str) -> tuple[tuple[str, ...], str]:
    path = PurePosixPath(relative)
    return path.parent.parts, path.name


def init(container: Path, *, project: str, today: str) -> list[str]:
    """Create the missing skeleton; return the relative paths created."""
    container = _require_container(container)
    created: list[str] = []
    root_fd = _open_root(container)
    try:
        for directory in DIRECTORIES:
            existed = os.path.lexists(container / directory)
            try:
                os.close(_open_dir(root_fd, PurePosixPath(directory).parts, create=True))
            except OSError as error:
                raise WikiError("WIKI_PATH_UNSAFE", f"{directory}: {error.strerror or error}") from error
            if not existed:
                created.append(f"{directory}/")
        for relative, content in skeleton_files(project=project, today=today).items():
            try:
                fd = os.open(relative, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC, 0o644, dir_fd=root_fd)
            except FileExistsError:
                if not stat.S_ISREG(os.stat(relative, dir_fd=root_fd, follow_symlinks=False).st_mode):
                    raise WikiError("WIKI_PATH_UNSAFE", f"{relative} is not a regular file") from None
                continue
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            created.append(relative)
    finally:
        os.close(root_fd)
    return created


# --------------------------------------------------------------------------- classification


def classify(relative: str) -> str | None:
    """Return the wiki destination of a container-relative file, or None to keep it."""
    path = PurePosixPath(relative)
    parts = path.parts
    if not parts or any(_is_hidden(part) for part in parts):
        return None
    top = parts[0]
    folded = _key(top)
    if folded in WIKI_ROOTS:
        return None
    if len(parts) == 1 and (folded in {name.casefold() for name in SKELETON_FILES} or LOG_ROTATION.match(top)):
        return None
    if len(parts) == 1 and top in CONTROLLER_FILES:
        return CONTROLLER_FILES[top]
    suffix = path.suffix.lower()
    if suffix in ASSET_SUFFIXES or (len(parts) > 1 and folded.endswith(" assets")):
        return str(PurePosixPath("raw/assets", *parts))
    if suffix in PAPER_SUFFIXES:
        return str(PurePosixPath("raw/papers", *parts))
    legacy = LEGACY_FOLDERS.get(folded)
    if legacy is not None and len(parts) > 1:
        layer_two = not legacy.startswith("raw/")
        if layer_two and (suffix not in LAYER_TWO_SUFFIXES or parts[-1].casefold() in FOLDER_INDEX_NAMES):
            return str(PurePosixPath("raw/articles", folded, *parts[1:]))
        return str(PurePosixPath(legacy, *parts[1:]))
    if len(parts) == 1 and suffix in QUERY_SUFFIXES:
        return str(PurePosixPath("queries", *parts))
    return str(PurePosixPath("raw/articles", *parts))


# --------------------------------------------------------------------------- planning


def _walk(container: Path) -> tuple[list[str], list[str], list[str], list[str]]:
    """Return (regular files, symlinks, hidden entries kept below the root, unreadable directories).

    Hidden entries are skipped at every depth and links are never followed.
    """
    files: list[str] = []
    links: list[str] = []
    hidden: list[str] = []
    unreadable: list[str] = []

    def record(error: OSError) -> None:
        location = Path(error.filename) if error.filename else container
        try:
            unreadable.append(location.relative_to(container).as_posix())
        except ValueError:
            unreadable.append(str(location))

    for current, dirnames, filenames in os.walk(container, followlinks=False, onerror=record):
        here = Path(current)
        relative_dir = here.relative_to(container)
        nested = relative_dir != Path(".")
        keep = []
        for name in sorted(dirnames):
            relative = (relative_dir / name).as_posix()
            if _is_hidden(name):
                if nested:
                    hidden.append(f"{relative}/")
            elif (here / name).is_symlink():
                links.append(relative)
            else:
                keep.append(name)
        dirnames[:] = keep
        for name in sorted(filenames):
            relative = (relative_dir / name).as_posix()
            if _is_hidden(name):
                if nested and name != FINDER_METADATA:
                    hidden.append(relative)
                continue
            mode = (here / name).lstat().st_mode
            if stat.S_ISLNK(mode):
                links.append(relative)
            elif stat.S_ISREG(mode):
                files.append(relative)
    return files, links, hidden, unreadable


def _identity(status: os.stat_result) -> tuple[int, int, int, int]:
    return (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns)


def _hash_fd(fd: int) -> str:
    digest = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    while chunk := os.read(fd, _CHUNK):
        digest.update(chunk)
    return digest.hexdigest()


def _hash_regular(path: Path) -> tuple[str, os.stat_result]:
    fd = os.open(path, os.O_RDONLY | _NOFOLLOW | _CLOEXEC)
    try:
        status = os.fstat(fd)
        if not stat.S_ISREG(status.st_mode):
            raise OSError(f"{path} is not a regular file")
        return _hash_fd(fd), status
    finally:
        os.close(fd)


def _case_variant(parent: Path, name: str) -> str | None:
    try:
        entries = os.listdir(parent)
    except OSError:
        return None
    key = _key(name)
    return next((entry for entry in entries if entry != name and _key(entry) == key), None)


def _inspect_destination(container: Path, destination: str) -> tuple[str, Any]:
    """Return ("absent", None), ("file", stat) or ("conflict", reason), checking every ancestor."""
    current = container
    parts = PurePosixPath(destination).parts
    for index, part in enumerate(parts):
        parent, current = current, current / part
        relative = PurePosixPath(*parts[: index + 1]).as_posix()
        try:
            status = os.lstat(current)
        except FileNotFoundError:
            variant = _case_variant(parent, part)
            if variant is not None:
                return "conflict", f"{relative} differs only by case from existing {variant}"
            return "absent", None
        except NotADirectoryError:
            return "conflict", f"an ancestor of {relative} is not a directory"
        if stat.S_ISLNK(status.st_mode):
            return "conflict", f"{relative} is a symlink"
        if index < len(parts) - 1:
            if not stat.S_ISDIR(status.st_mode):
                return "conflict", f"{relative} is not a directory"
        elif not stat.S_ISREG(status.st_mode):
            return "conflict", "destination is not a regular file"
        else:
            return "file", status
    return "conflict", "empty destination"


def _plan(container: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    files, links, hidden, unreadable = _walk(container)
    entries = [(source, destination) for source in files if (destination := classify(source)) not in (None, source)]
    conflicts: list[dict[str, str]] = [
        {"source": item, "destination": "", "reason": "directory could not be read"} for item in unreadable
    ]
    claimed: dict[str, str] = {}
    ancestors: dict[str, str] = {}
    for source, destination in entries:
        key = _key(destination)
        if key in claimed:
            conflicts.append({"source": source, "destination": destination, "reason": f"also claimed by {claimed[key]}"})
        else:
            claimed[key] = source
        for parent in PurePosixPath(destination).parents:
            if parent.parts:
                ancestors.setdefault(_key(parent.as_posix()), source)
    moves: list[dict[str, str]] = []
    duplicates: list[str] = []
    expected: dict[str, dict[str, Any]] = {}
    for source, destination in entries:
        key = _key(destination)
        if claimed.get(key) != source:
            continue
        if key in ancestors:
            conflicts.append({"source": source, "destination": destination,
                              "reason": f"is also a directory needed by {ancestors[key]}"})
            continue
        try:
            source_sha, source_status = _hash_regular(container / source)
        except OSError as error:
            conflicts.append({"source": source, "destination": destination, "reason": f"source unreadable: {error}"})
            continue
        kind, detail = _inspect_destination(container, destination)
        if kind == "conflict":
            conflicts.append({"source": source, "destination": destination, "reason": detail})
            continue
        record = {"destination": destination, "sha256": source_sha, "identity": _identity(source_status)}
        if kind == "absent":
            moves.append({"source": source, "destination": destination})
            expected[source] = record
            continue
        if os.path.samestat(source_status, detail) or detail.st_nlink != 1:
            conflicts.append({"source": source, "destination": destination,
                              "reason": "destination is the source itself or shares its data through a link"})
            continue
        try:
            destination_sha, destination_status = _hash_regular(container / destination)
        except OSError as error:
            conflicts.append({"source": source, "destination": destination, "reason": f"destination unreadable: {error}"})
            continue
        if destination_sha != source_sha:
            conflicts.append({"source": source, "destination": destination, "reason": "destination exists with different content"})
            continue
        duplicates.append(source)
        expected[source] = {**record, "destination_identity": _identity(destination_status)}
    report = {
        "container": str(container),
        "layout_version": LAYOUT_VERSION,
        "moves": moves,
        "deduplicated": duplicates,
        "conflicts": conflicts,
        "skipped_symlinks": sorted(links),
        "kept_hidden": hidden,
    }
    return report, expected


def _plan_safely(container: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    try:
        return _plan(container)
    except OSError as error:
        raise WikiError("WIKI_PATH_UNSAFE", f"cannot plan {error.filename or container}: {error.strerror or error}") from error


def plan(container: Path) -> dict[str, Any]:
    """Describe the migration without touching the disk."""
    return _plan_safely(_require_container(container))[0]


# --------------------------------------------------------------------------- migration


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def _require_source(directory_fd: int, name: str, record: dict[str, Any], source: str) -> int:
    fd = os.open(name, os.O_RDONLY | _NOFOLLOW | _CLOEXEC, dir_fd=directory_fd)
    status = os.fstat(fd)
    if not stat.S_ISREG(status.st_mode) or _identity(status) != record["identity"]:
        os.close(fd)
        raise WikiError("WIKI_SOURCE_CHANGED", f"{source} changed after the plan")
    return fd


def _require_unchanged_name(directory_fd: int, name: str, record: dict[str, Any], source: str) -> None:
    status = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    if not stat.S_ISREG(status.st_mode) or _identity(status) != record["identity"]:
        raise WikiError("WIKI_SOURCE_CHANGED", f"{source} changed during the move")


def _retire_source(directory_fd: int, name: str, source_fd: int, record: dict[str, Any], source: str) -> None:
    """Remove the verified source without deleting a concurrent save.

    The name is first renamed to a private hidden name, the renamed entry is
    compared with the open descriptor, and only that exact file is unlinked.
    A file saved over the name in the meantime is either never renamed or is
    put back with an exclusive hard link, which never replaces a newer save,
    so an edit can never be deleted by the check-then-unlink gap.
    """
    private = f".{name}.wiki-migrate-{secrets.token_hex(8)}"
    try:
        os.stat(private, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        raise WikiError("WIKI_SOURCE_CHANGED", f"{source}: private name collision")
    os.rename(name, private, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
    renamed = os.stat(private, dir_fd=directory_fd, follow_symlinks=False)
    if os.path.samestat(renamed, os.fstat(source_fd)) and _identity(renamed) == record["identity"]:
        os.unlink(private, dir_fd=directory_fd)
        return
    copy_note = (
        f"the copy at {record['destination']} is the version planned before the change; "
        "reconcile it by hand before rerunning"
    )
    try:
        os.link(private, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd, follow_symlinks=False)
    except FileExistsError:
        raise WikiError(
            "WIKI_SOURCE_CHANGED",
            f"{source} changed during the move; the newer save stays at {source}, the version that was there "
            f"is kept as {private} next to it, and {copy_note}",
        ) from None
    os.unlink(private, dir_fd=directory_fd)
    raise WikiError("WIKI_SOURCE_CHANGED", f"{source} changed during the move; {copy_note}")


def _discard_created(directory_fd: int, name: str, fd: int) -> None:
    """Remove a destination this run created, only while the name still points at it."""
    try:
        if os.path.samestat(os.stat(name, dir_fd=directory_fd, follow_symlinks=False), os.fstat(fd)):
            os.unlink(name, dir_fd=directory_fd)
    except OSError:
        pass


def _move_verified(root_fd: int, source: str, record: dict[str, Any]) -> None:
    source_parts, source_name = _split(source)
    target_parts, target_name = _split(record["destination"])
    source_dir = _open_dir(root_fd, source_parts, create=False)
    try:
        source_fd = _require_source(source_dir, source_name, record, source)
        try:
            source_status = os.fstat(source_fd)
            target_dir = _open_dir(root_fd, target_parts, create=True)
            try:
                target_fd = os.open(
                    target_name,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC,
                    stat.S_IMODE(source_status.st_mode) or 0o644,
                    dir_fd=target_dir,
                )
                try:
                    digest = hashlib.sha256()
                    while chunk := os.read(source_fd, _CHUNK):
                        digest.update(chunk)
                        _write_all(target_fd, chunk)
                    os.fsync(target_fd)
                    if digest.hexdigest() != record["sha256"] or _hash_fd(target_fd) != record["sha256"]:
                        raise WikiError("WIKI_SOURCE_CHANGED", f"{source} content differs from the plan")
                    os.utime(target_fd, ns=(source_status.st_atime_ns, source_status.st_mtime_ns))
                    _require_unchanged_name(source_dir, source_name, record, source)
                except BaseException:
                    _discard_created(target_dir, target_name, target_fd)
                    raise
                finally:
                    os.close(target_fd)
            finally:
                os.close(target_dir)
            _retire_source(source_dir, source_name, source_fd, record, source)
        finally:
            os.close(source_fd)
    finally:
        os.close(source_dir)


def _remove_duplicate(root_fd: int, source: str, record: dict[str, Any]) -> None:
    source_parts, source_name = _split(source)
    target_parts, target_name = _split(record["destination"])
    source_dir = _open_dir(root_fd, source_parts, create=False)
    try:
        source_fd = _require_source(source_dir, source_name, record, source)
        try:
            target_dir = _open_dir(root_fd, target_parts, create=False)
            try:
                target_fd = os.open(target_name, os.O_RDONLY | _NOFOLLOW | _CLOEXEC, dir_fd=target_dir)
                try:
                    target_status = os.fstat(target_fd)
                    if (
                        not stat.S_ISREG(target_status.st_mode)
                        or os.path.samestat(target_status, os.fstat(source_fd))
                        or _identity(target_status) != record["destination_identity"]
                        or _hash_fd(target_fd) != record["sha256"]
                        or _hash_fd(source_fd) != record["sha256"]
                    ):
                        raise WikiError("WIKI_SOURCE_CHANGED", f"{source} or its copy changed after the plan")
                finally:
                    os.close(target_fd)
            finally:
                os.close(target_dir)
            _require_unchanged_name(source_dir, source_name, record, source)
            _retire_source(source_dir, source_name, source_fd, record, source)
        finally:
            os.close(source_fd)
    finally:
        os.close(source_dir)


def _remove_empty_directory(root_fd: int, directory: PurePosixPath) -> bool:
    try:
        parent = _open_dir(root_fd, directory.parent.parts, create=False)
    except OSError:
        return False
    try:
        try:
            fd = os.open(directory.name, os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC, dir_fd=parent)
        except OSError:
            return False
        try:
            entries = os.listdir(fd)
            if entries == [FINDER_METADATA]:
                if stat.S_ISREG(os.stat(FINDER_METADATA, dir_fd=fd, follow_symlinks=False).st_mode):
                    os.unlink(FINDER_METADATA, dir_fd=fd)
                    entries = []
            if entries:
                return False
        finally:
            os.close(fd)
        try:
            os.rmdir(directory.name, dir_fd=parent)
        except OSError:
            return False
        return True
    finally:
        os.close(parent)


def _prune_empty(root_fd: int, relatives: list[str]) -> None:
    candidates = sorted(
        {PurePosixPath(item).parent for item in relatives if PurePosixPath(item).parent.parts},
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in candidates:
        current = directory
        while current.parts and _remove_empty_directory(root_fd, current):
            current = current.parent


def _open_wiki_file(root_fd: int, name: str, flags: int) -> int:
    fd = os.open(name, flags | _NOFOLLOW | _CLOEXEC, dir_fd=root_fd)
    status = os.fstat(fd)
    if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
        os.close(fd)
        raise WikiError("WIKI_PATH_UNSAFE", f"{name} must be a regular file with a single link")
    return fd


def _read_wiki_file(root_fd: int, name: str) -> str:
    fd = _open_wiki_file(root_fd, name, os.O_RDONLY)
    try:
        chunks = []
        while chunk := os.read(fd, _CHUNK):
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8")
    finally:
        os.close(fd)


def _replace_wiki_file(root_fd: int, name: str, text: str) -> None:
    os.close(_open_wiki_file(root_fd, name, os.O_RDONLY))
    temporary = f".{name}.{secrets.token_hex(8)}.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC, 0o644, dir_fd=root_fd)
    try:
        _write_all(fd, text.encode("utf-8"))
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        os.unlink(temporary, dir_fd=root_fd)
        raise
    os.close(fd)
    os.rename(temporary, name, src_dir_fd=root_fd, dst_dir_fd=root_fd)


def append_log_entry(root_fd: int, today: str, action: str, subject: str, lines: list[str]) -> None:
    """Append one ``## [date] action | subject`` entry to log.md (single-link regular file only)."""
    entry = f"\n## [{today}] {action} | {subject}\n" + "".join(f"- {line}\n" for line in lines)
    fd = _open_wiki_file(root_fd, "log.md", os.O_WRONLY | os.O_APPEND)
    try:
        _write_all(fd, entry.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def _append_log(root_fd: int, today: str, subject: str, lines: list[str]) -> None:
    append_log_entry(root_fd, today, "migrate", subject, lines)


def _link_target(page: str) -> str:
    return page[:-3] if page.endswith(".md") else page


def _unique_stems(pages: list[str]) -> set[str]:
    counts: dict[str, int] = {}
    for page in pages:
        stem = PurePosixPath(page).stem
        counts[stem] = counts.get(stem, 0) + 1
    return {stem for stem, count in counts.items() if count == 1}


def _is_indexed(text: str, page: str, unique: set[str]) -> bool:
    """A page is indexed by its path link, or by its bare name when no other page shares it."""
    targets = [_link_target(page)]
    stem = PurePosixPath(page).stem
    if stem in unique:
        targets.append(stem)
    return any(f"[[{target}]]" in text or f"[[{target}|" in text for target in targets)


def _index_pages(container: Path, root_fd: int, moved: list[str]) -> None:
    """Add moved Layer-2 pages to index.md under their section, alphabetically."""
    text = _read_wiki_file(root_fd, "index.md")
    unique = _unique_stems(_layer_two_pages(container))
    added = False
    for prefix, title in PAGE_SECTIONS:
        pages = sorted(
            item for item in moved
            if item.startswith(f"{prefix}/") and item.endswith(".md") and not _is_indexed(text, item, unique)
        )
        if not pages:
            continue
        heading = f"## {title}\n"
        if heading not in text:
            text = text.rstrip("\n") + f"\n\n{heading}"
        entries = "".join(
            f"- [[{_link_target(item)}|{PurePosixPath(item).stem}]] — migrated from the legacy layout; summary pending\n"
            for item in pages
        )
        position = text.index(heading) + len(heading)
        text = text[:position] + entries + text[position:]
        added = True
    if added:
        text = re.sub(r"Total pages: \d+", f"Total pages: {len(_layer_two_pages(container))}", text, count=1)
        _replace_wiki_file(root_fd, "index.md", text)


def _require_safe_wiki_files(root_fd: int) -> None:
    """Fail before the first move if index.md or log.md could not be updated afterwards."""
    for name in ("index.md", "log.md"):
        try:
            _read_wiki_file(root_fd, name)
        except FileNotFoundError:
            continue
        except UnicodeDecodeError as error:
            raise WikiError("WIKI_PATH_UNSAFE", f"{name} is not valid UTF-8") from error
        except OSError as error:
            raise WikiError("WIKI_PATH_UNSAFE", f"{name}: {error.strerror or error}") from error


def _has_container_marker(container: Path) -> bool:
    return any(os.path.lexists(container / marker) for marker in CONTAINER_MARKERS)


def migrate(container: Path, *, project: str, today: str) -> dict[str, Any]:
    """Apply the plan; every conflict is refused before the first move."""
    container = _require_container(container)
    if not _has_container_marker(container):
        raise WikiError("WIKI_INIT_REQUIRED", f"{container} has no SCHEMA.md or binding; run init --apply first")
    root_fd = _open_root(container)
    try:
        _require_safe_wiki_files(root_fd)
        report, expected = _plan_safely(container)
        if report["conflicts"]:
            raise WikiError(
                "WIKI_MIGRATION_CONFLICT",
                "; ".join(f"{item['source']} -> {item['destination']}: {item['reason']}" for item in report["conflicts"]),
            )
        created = init(container, project=project, today=today)
        if not report["moves"] and not report["deduplicated"]:
            report.update(status="ALREADY_MIGRATED" if not created else "INITIALIZED", created=created)
            return report
        done: list[str] = []
        try:
            for move in report["moves"]:
                _move_verified(root_fd, move["source"], expected[move["source"]])
                done.append(f"{move['source']} -> {move['destination']}")
            for source in report["deduplicated"]:
                _remove_duplicate(root_fd, source, expected[source])
                done.append(f"{source} (identical copy already at {expected[source]['destination']}, removed)")
        except (OSError, WikiError) as error:
            try:
                _append_log(root_fd, today, "Legacy migration interrupted", [*done, f"stopped: {error}"])
            except (OSError, WikiError):
                pass
            raise WikiError(
                "WIKI_MIGRATION_INCOMPLETE",
                f"{len(done)} of {len(report['moves']) + len(report['deduplicated'])} files handled before: {error}. "
                "Every source not yet handled is still in place; rerun migrate to continue.",
            ) from error
        try:
            _prune_empty(root_fd, [move["source"] for move in report["moves"]] + report["deduplicated"])
            _index_pages(container, root_fd, [move["destination"] for move in report["moves"]])
            _append_log(root_fd, today, "Legacy project folders moved into the LLM Wiki layout", done)
        except (OSError, UnicodeError, WikiError) as error:
            try:
                _append_log(root_fd, today, "Legacy migration moved every file but did not finish", [*done, f"stopped: {error}"])
            except (OSError, UnicodeError, WikiError):
                pass
            raise WikiError(
                "WIKI_MIGRATION_INCOMPLETE",
                f"every file was moved, but cleaning up or updating index.md/log.md failed: {error}. "
                "No note was lost; run check to list pages missing from index.md.",
            ) from error
        report.update(status="APPLIED", created=created)
        return report
    finally:
        os.close(root_fd)


# --------------------------------------------------------------------------- check


def _layer_two_pages(container: Path) -> list[str]:
    pages = []
    for prefix, _ in PAGE_SECTIONS:
        root = container / prefix
        if not root.is_dir() or root.is_symlink():
            continue
        for current, dirnames, filenames in os.walk(root, followlinks=False):
            here = Path(current)
            dirnames[:] = sorted(name for name in dirnames if not _is_hidden(name) and not (here / name).is_symlink())
            for name in sorted(filenames):
                path = here / name
                if name.endswith(".md") and not _is_hidden(name) and not path.is_symlink() and path.is_file():
                    pages.append(path.relative_to(container).as_posix())
    return pages


def check(container: Path) -> dict[str, Any]:
    """Report what does not follow the LLM Wiki layout. Never writes."""
    container = _require_container(container)
    findings: list[dict[str, str]] = []
    for relative in (*SKELETON_FILES, *DIRECTORIES):
        if not os.path.lexists(container / relative):
            findings.append({"code": "WIKI_SKELETON_MISSING", "path": relative})
    for entry in sorted(container.iterdir()):
        name = entry.name
        if _is_hidden(name) or name.casefold() in WIKI_ROOTS:
            continue
        if not entry.is_dir() and classify(name) is None:
            continue
        findings.append({"code": "WIKI_LEGACY_ENTRY", "path": name})
    if os.path.lexists(container / "index.md"):
        root_fd = _open_root(container)
        try:
            text = _read_wiki_file(root_fd, "index.md")
        finally:
            os.close(root_fd)
        pages = _layer_two_pages(container)
        unique = _unique_stems(pages)
        for page in pages:
            if not _is_indexed(text, page, unique):
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
                report["planned_skeleton"] = [
                    item for item in (*SKELETON_FILES, *DIRECTORIES) if not os.path.lexists(container / item)
                ]
                if args.command == "init":
                    report["moves"] = []
                    report["deduplicated"] = []
                else:
                    report["init_required"] = not _has_container_marker(container)
                blocked = args.command == "migrate" and bool(report["conflicts"])
                report["status"] = "BLOCKED" if blocked else "READY"
                code = 2 if blocked else 0
            elif args.command == "init":
                report = {"status": "APPLIED", "created": init(container, project=project, today=today)}
                code = 0
            else:
                report = migrate(container, project=project, today=today)
                code = 0
    except WikiError as error:
        report, code = {"status": "BLOCKED", "reason": error.code, "detail": error.detail}, 2
    except OSError as error:
        detail = f"{error.filename or container}: {error.strerror or error}"
        report, code = {"status": "BLOCKED", "reason": "WIKI_PATH_UNSAFE", "detail": detail}, 2
    print(json.dumps(report, ensure_ascii=False, indent=None if args.json else 2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
