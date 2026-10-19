"""Skeleton files of an LLM Wiki project container: SCHEMA.md, index.md, log.md.

Split from ``wiki_layout`` so the layout rules and the page templates evolve
separately; ``wiki_layout.skeleton_files`` re-exports ``skeleton_files``.
"""
from __future__ import annotations

LAYOUT_VERSION = 1
PAGE_SECTIONS = (("entities", "Entities"), ("concepts", "Concepts"), ("comparisons", "Comparisons"), ("queries", "Queries"))


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
- `Hermes/` and `Hermes Runtime/` — SDD orchestrator state; not wiki content.

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
