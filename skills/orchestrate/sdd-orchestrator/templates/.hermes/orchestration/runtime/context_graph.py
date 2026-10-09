#!/usr/bin/env python3
"""Read the project's context graph: modules, rules, tests, decisions and docs.

The graph is plain Markdown notes with declarative frontmatter. Every note is
one node; its frontmatter names the related nodes and, for a decision, the
reason it was taken. Nothing here is a graph database: the notes are the graph,
and they live either in the bound Obsidian project container or in a
repository-local directory.

This module itself never creates, edits or moves a note. ``propose`` returns the
exact note content; the controller writes it into the project's wiki (the vault
is read and written, LOOP_POLICY §18), so the result carries the ``OBSIDIAN_WRITE``
action classified ``AUTO_SAFE``.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any

from validate_protocol import editable_pattern_is_safe, path_matches

NODE_KINDS = ("MODULE", "RULE", "TEST", "DECISION", "DOC")
#: relation -> (kinds allowed on the left, kinds allowed on the right)
RELATIONS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "depends_on": (("MODULE",), ("MODULE",)),
    "governed_by": (("MODULE", "TEST"), ("RULE",)),
    "verified_by": (("MODULE", "RULE"), ("TEST",)),
    "decided_by": (("MODULE", "RULE", "TEST"), ("DECISION",)),
    "documented_by": (("MODULE", "RULE", "DECISION"), ("DOC",)),
    "supersedes": (("DECISION",), ("DECISION",)),
}
DECISION_FIELDS = ("decision_reason", "decision_date")
SCALAR_FIELDS = ("graph_node", "graph_kind", *DECISION_FIELDS)
LIST_FIELDS = ("code_paths", *RELATIONS)
GRAPH_ERROR_CODES = (
    "GRAPH_SOURCE_UNAVAILABLE",
    "GRAPH_ROOT_UNSAFE",
    "GRAPH_NODE_ID_INVALID",
    "GRAPH_NODE_DUPLICATED",
    "GRAPH_KIND_INVALID",
    "GRAPH_FRONTMATTER_INVALID",
    "GRAPH_FIELD_INVALID",
    "GRAPH_CODE_PATH_UNSAFE",
    "GRAPH_EDGE_DANGLING",
    "GRAPH_EDGE_KIND_INVALID",
    "GRAPH_DEPENDENCY_CYCLE",
    "GRAPH_DECISION_REASON_REQUIRED",
    "GRAPH_DECISION_DATE_INVALID",
    "GRAPH_PROPOSAL_INVALID",
    "GRAPH_PROPOSAL_PATH_UNSAFE",
    "GRAPH_SELECTOR_REQUIRED",
)
NODE_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
#: Every character ``str.splitlines()`` treats as a break. A frontmatter scalar
#: holding one of these would render a note this module's own parser refuses, so
#: the guard has to use the parser's own definition of a line, not just \n/\r.
LINE_BREAKS = "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"
DEFAULT_DEPTH = 2
MAX_DEPTH = 8
MAX_NOTE_BYTES = 256 * 1024
#: A finding's detail is embedded in a dispatch manifest, so a cyclic group of
#: any size must still produce a short, readable line.
MAX_NAMED_CYCLE_MEMBERS = 10
#: Same rule for a quoted field value in a refusal: caller-supplied values may
#: be arbitrarily long, so they are excerpted before reaching a finding.
MAX_DETAIL_EXCERPT = 120
DEFAULT_GRAPH_SUBPATH = "context-graph"
PROPOSAL_OPERATIONS = ("CREATE", "APPEND")
OBSIDIAN_WRITE_ACTION = "OBSIDIAN_WRITE"
OBSIDIAN_WRITE_APPROVAL = "AUTO_SAFE"


class GraphError(ValueError):
    """The graph source or a proposal cannot be read."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _finding(code: str, detail: str, *, note: str | None = None) -> dict[str, str]:
    found = {"code": code, "detail": detail}
    if note is not None:
        found["note"] = note
    return found


# --------------------------------------------------------------------- frontmatter


def parse_frontmatter(text: str) -> dict[str, Any]:
    """Parse the strict subset of YAML frontmatter the graph declares.

    Accepted: ``key: scalar``, ``key: [a, b]`` and a block list of ``  - item``
    lines. Anything else — nesting, anchors, quoted keys, duplicate keys — is a
    refusal, because a graph that is read differently by two readers is worse
    than no graph.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise GraphError("GRAPH_FRONTMATTER_INVALID", "note does not open with a '---' frontmatter block")
    fields: dict[str, Any] = {}
    current: str | None = None
    closed = False
    for line in lines[1:]:
        if line.strip() == "---":
            closed = True
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.fullmatch(r"\s+-\s+\S.*", line):
            if current is None or not isinstance(fields.get(current), list):
                raise GraphError("GRAPH_FRONTMATTER_INVALID", "a list item appears outside a list field")
            fields[current].append(line.split("-", 1)[1].strip())
            continue
        if line[:1].isspace() or ":" not in line:
            raise GraphError("GRAPH_FRONTMATTER_INVALID", f"unsupported frontmatter line: {line.strip()[:60]!r}")
        key, raw = line.split(":", 1)
        key, raw = key.strip(), raw.strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            raise GraphError("GRAPH_FRONTMATTER_INVALID", f"unsupported frontmatter key: {key[:60]!r}")
        if key in fields:
            raise GraphError("GRAPH_FRONTMATTER_INVALID", f"duplicate frontmatter key: {key}")
        if raw.startswith("[") and raw.endswith("]"):
            inner = raw[1:-1].strip()
            fields[key] = [item.strip() for item in inner.split(",") if item.strip()] if inner else []
            current = key
        elif raw:
            fields[key] = raw
            current = None
        else:
            fields[key] = []
            current = key
    if not closed:
        raise GraphError("GRAPH_FRONTMATTER_INVALID", "frontmatter block is not closed")
    return fields


# ------------------------------------------------------------------------- sources


def _read_repo_note(root: Path, relative: PurePosixPath) -> str:
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise GraphError("GRAPH_SOURCE_UNAVAILABLE", f"{relative.as_posix()} traverses a symlink")
    if not cursor.is_file():
        raise GraphError("GRAPH_SOURCE_UNAVAILABLE", f"{relative.as_posix()} is not a regular file")
    try:
        raw = cursor.read_bytes()
    except OSError as error:
        raise GraphError("GRAPH_SOURCE_UNAVAILABLE", f"{relative.as_posix()}: {error.strerror or 'unreadable'}") from None
    if len(raw) > MAX_NOTE_BYTES:
        raise GraphError("GRAPH_SOURCE_UNAVAILABLE", f"{relative.as_posix()} exceeds {MAX_NOTE_BYTES} bytes")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise GraphError("GRAPH_SOURCE_UNAVAILABLE", f"{relative.as_posix()} is not UTF-8") from None


def _repo_notes(repo_root: Path, subpath: str) -> list[tuple[str, str]]:
    """Walk a repository-local graph root, contained inside the repository.

    The root is caller-supplied, so it goes through the same path rule the rest
    of the orchestration applies to editable and declared paths: canonical,
    repository-relative, literal first segment. Without this, an absolute
    `--root` would silently replace the repository root and a `../` root would
    read notes from outside the project.
    """
    if not isinstance(subpath, str) or not editable_pattern_is_safe(subpath):
        raise GraphError(
            "GRAPH_ROOT_UNSAFE",
            f"{subpath!r} must be a canonical repository-relative directory that does not escape the repository",
        )
    base = Path(repo_root) / Path(*PurePosixPath(subpath).parts)
    # Containment first: a symlinked root (or a symlinked ancestor of it) is a
    # containment failure, not a missing directory, and must report as one.
    for cursor in (base, *base.parents):
        if cursor == Path(repo_root):
            break
        if cursor.is_symlink():
            raise GraphError("GRAPH_ROOT_UNSAFE", f"{subpath} traverses a symlink")
    if not base.is_dir():
        raise GraphError("GRAPH_SOURCE_UNAVAILABLE", f"{subpath} is not a real directory")
    notes: list[tuple[str, str]] = []
    for current_root, dirnames, filenames in os.walk(base, followlinks=False):
        current = Path(current_root)
        dirnames[:] = sorted(name for name in dirnames if not (current / name).is_symlink())
        for filename in sorted(filenames):
            if not filename.endswith(".md"):
                continue
            relative = PurePosixPath((current / filename).relative_to(base).as_posix())
            notes.append((relative.as_posix(), _read_repo_note(base, relative)))
    return notes


def _vault_notes(repo_root: Path) -> list[tuple[str, str]]:
    try:
        import obsidian_connector
    except ImportError as error:  # pragma: no cover - runtime ships both modules
        raise GraphError("GRAPH_SOURCE_UNAVAILABLE", "the Obsidian connector is unavailable") from error
    try:
        paths = obsidian_connector.project_notes(Path(repo_root))
        return [(path, obsidian_connector.read_note(Path(repo_root), path)["content"]) for path in paths]
    except obsidian_connector.ConnectorError as error:
        raise GraphError("GRAPH_SOURCE_UNAVAILABLE", f"{error.code}: {error.message}") from None
    except Exception as error:  # binding errors carry a stable code attribute
        code = getattr(error, "code", None)
        if isinstance(code, str):
            raise GraphError("GRAPH_SOURCE_UNAVAILABLE", code) from None
        raise


def load_notes(repo_root: Path, subpath: str | None) -> list[tuple[str, str]]:
    """Return ``(note path, text)`` for every candidate note, read-only.

    With ``subpath`` the graph is repository-local; without it the graph is the
    bound Obsidian project container.
    """
    return _repo_notes(repo_root, subpath) if subpath else _vault_notes(repo_root)


# --------------------------------------------------------------------------- graph


#: Note roots that are never graph notes: the installed controller and its
#: project-local skills (whose SKILL.md frontmatter nests `metadata: hermes:`),
#: the Obsidian app folder and the per-worktree runtime.
SKIPPED_NOTE_ROOTS = (".hermes", ".obsidian", ".trash", ".hermes-runtime")
_GRAPH_KEY_LINE = re.compile(r"^graph_node\s*:", re.M)


def skipped_note(note_path: str) -> bool:
    """Whether a note path lies under a root the graph never scans."""
    parts = PurePosixPath(note_path).parts
    return bool(parts) and parts[0] in SKIPPED_NOTE_ROOTS


def _declares_graph_node(text: str) -> bool:
    """Cheap pre-check: only a frontmatter with a top-level ``graph_node`` key is a graph note."""
    head = text.split("\n---", 1)[0]
    return bool(_GRAPH_KEY_LINE.search(head))


def build(notes: list[tuple[str, str]]) -> dict[str, Any]:
    """Build the graph from notes and report every structural finding.

    A note without ``graph_node`` is not part of the graph and is ignored, so an
    ordinary project note never becomes a finding — including notes whose
    frontmatter uses nesting the strict graph parser refuses (installed skills).
    A note that does declare ``graph_node`` is parsed strictly. Notes under
    ``SKIPPED_NOTE_ROOTS`` are never read as graph notes.
    """
    nodes: dict[str, dict[str, Any]] = {}
    findings: list[dict[str, str]] = []
    for note_path, text in notes:
        if not text.startswith("---") or skipped_note(note_path) or not _declares_graph_node(text):
            continue  # an ordinary note is not part of the graph
        try:
            fields = parse_frontmatter(text)
        except GraphError as error:
            findings.append(_finding(error.code, error.detail, note=note_path))
            continue
        if "graph_node" not in fields:
            continue
        node = _node_from_fields(note_path, fields, findings)
        if node is None:
            continue
        # The note's own size is part of the graph: an APPEND proposal has to
        # know how much room is left before the read limit refuses the note.
        node["note_bytes"] = len(text.encode("utf-8"))
        if node["id"] in nodes:
            findings.append(_finding(
                "GRAPH_NODE_DUPLICATED",
                f"{node['id']} is declared by {nodes[node['id']]['note']} and {note_path}",
                note=note_path,
            ))
            continue
        nodes[node["id"]] = node
    findings.extend(_check_edges(nodes))
    findings.extend(_check_cycles(nodes))
    return {
        "nodes": nodes,
        "findings": sorted(findings, key=lambda item: (item["code"], item.get("note", ""), item["detail"])),
    }


def _node_from_fields(
    note_path: str,
    fields: dict[str, Any],
    findings: list[dict[str, str]],
) -> dict[str, Any] | None:
    node_id = fields.get("graph_node")
    if not isinstance(node_id, str) or NODE_ID.fullmatch(node_id) is None:
        findings.append(_finding("GRAPH_NODE_ID_INVALID", f"{node_id!r} is not a lowercase node id", note=note_path))
        return None
    kind = fields.get("graph_kind")
    if kind not in NODE_KINDS:
        findings.append(_finding("GRAPH_KIND_INVALID", f"{node_id} declares kind {kind!r}", note=note_path))
        return None
    node: dict[str, Any] = {"id": node_id, "kind": kind, "note": note_path, "code_paths": []}
    for field in SCALAR_FIELDS:
        value = fields.get(field)
        if value is not None and not isinstance(value, str):
            findings.append(_finding("GRAPH_FIELD_INVALID", f"{node_id}.{field} must be a scalar", note=note_path))
            return None
    for field in LIST_FIELDS:
        value = fields.get(field, [])
        if isinstance(value, str):
            findings.append(_finding("GRAPH_FIELD_INVALID", f"{node_id}.{field} must be a list", note=note_path))
            return None
        node[field] = list(value)
    for pattern in node["code_paths"]:
        if not editable_pattern_is_safe(pattern):
            findings.append(_finding(
                "GRAPH_CODE_PATH_UNSAFE",
                f"{node_id} declares {pattern!r}, which is absolute, escapes the repository or matches everything",
                note=note_path,
            ))
            return None
    for field in RELATIONS:
        for target in node[field]:
            if not isinstance(target, str) or NODE_ID.fullmatch(target) is None:
                findings.append(_finding(
                    "GRAPH_FIELD_INVALID",
                    f"{node_id}.{field} names {target!r}, which is not a node id",
                    note=note_path,
                ))
                return None
    if kind == "DECISION":
        reason = fields.get("decision_reason")
        if not isinstance(reason, str) or not reason.strip():
            findings.append(_finding(
                "GRAPH_DECISION_REASON_REQUIRED",
                f"{node_id} is a decision and must record why it was taken",
                note=note_path,
            ))
            return None
        date = fields.get("decision_date")
        if not isinstance(date, str) or ISO_DATE.fullmatch(date) is None:
            findings.append(_finding(
                "GRAPH_DECISION_DATE_INVALID",
                f"{node_id} needs an ISO 8601 decision_date (YYYY-MM-DD)",
                note=note_path,
            ))
            return None
        node["decision_reason"] = reason.strip()
        node["decision_date"] = date
    return node


def _check_edges(nodes: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for node in nodes.values():
        for relation, (left_kinds, right_kinds) in RELATIONS.items():
            targets = node.get(relation, [])
            if targets and node["kind"] not in left_kinds:
                findings.append(_finding(
                    "GRAPH_EDGE_KIND_INVALID",
                    f"a {node['kind']} node cannot declare {relation}",
                    note=node["note"],
                ))
                continue
            for target in targets:
                if target not in nodes:
                    findings.append(_finding(
                        "GRAPH_EDGE_DANGLING",
                        f"{node['id']}.{relation} points at unknown node {target}",
                        note=node["note"],
                    ))
                elif nodes[target]["kind"] not in right_kinds:
                    findings.append(_finding(
                        "GRAPH_EDGE_KIND_INVALID",
                        f"{node['id']}.{relation} points at {target}, which is a {nodes[target]['kind']}",
                        note=node["note"],
                    ))
    return findings


def _check_cycles(nodes: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    """Report every `depends_on` cycle, once per cyclic group.

    Cycles are found as strongly connected components with an iterative Tarjan
    pass. A DFS that only reports back edges misses a cycle reachable through an
    already-finished node, and recursion would turn a long dependency chain into
    a crash instead of a finding. Every cycle lives in exactly one component, so
    one finding per component names each cyclic group exactly once.
    """
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    counter = 0
    components: list[list[str]] = []

    for root in sorted(nodes):
        if root in index:
            continue
        work: list[tuple[str, list[str]]] = [(root, sorted(
            target for target in nodes[root].get("depends_on", []) if target in nodes
        ))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node_id, pending = work[-1]
            if pending:
                target = pending.pop(0)
                if target not in index:
                    index[target] = low[target] = counter
                    counter += 1
                    stack.append(target)
                    on_stack.add(target)
                    work.append((target, sorted(
                        child for child in nodes[target].get("depends_on", []) if child in nodes
                    )))
                elif target in on_stack:
                    low[node_id] = min(low[node_id], index[target])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node_id])
            if low[node_id] == index[node_id]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node_id:
                        break
                if len(component) > 1 or node_id in nodes[node_id].get("depends_on", []):
                    components.append(sorted(component))

    return [
        _finding("GRAPH_DEPENDENCY_CYCLE", _cycle_detail(component), note=nodes[component[0]]["note"])
        for component in sorted(components)
    ]


def _cycle_detail(component: list[str]) -> str:
    """Name a cyclic group in one bounded line, whatever its size."""
    named = component[:MAX_NAMED_CYCLE_MEMBERS]
    listed = ", ".join(named)
    if len(component) > len(named):
        listed += f", and {len(component) - len(named)} more"
    return f"{len(component)} node(s) depend on each other: {listed}"


# --------------------------------------------------------------------------- query


def _selected(graph: dict[str, Any], node_ids: list[str], paths: list[str]) -> tuple[list[str], list[dict[str, str]]]:
    nodes = graph["nodes"]
    selected: list[str] = []
    unresolved: list[dict[str, str]] = []
    for node_id in node_ids:
        if node_id in nodes:
            selected.append(node_id)
        else:
            unresolved.append({"selector": node_id, "reason": "no node declares this id"})
    for path in paths:
        matches = [
            node["id"]
            for node in nodes.values()
            if any(path_matches(path, pattern) for pattern in node["code_paths"])
        ]
        if matches:
            selected.extend(matches)
        else:
            unresolved.append({"selector": path, "reason": "no node declares a code path covering it"})
    return sorted(set(selected)), unresolved


def query(graph: dict[str, Any], *, node_ids: list[str], paths: list[str], depth: int) -> dict[str, Any]:
    """Return the context connected to the selected nodes, bounded by ``depth``.

    Depth 0 is the selection itself. Traversal follows the declared relations
    outward and reports the edges it used, so the controller can cite why a rule
    or decision is in scope instead of trusting an opaque bundle.
    """
    if not isinstance(depth, int) or isinstance(depth, bool) or not 0 <= depth <= MAX_DEPTH:
        raise GraphError("GRAPH_SELECTOR_REQUIRED", f"depth must be an integer between 0 and {MAX_DEPTH}")
    if not node_ids and not paths:
        raise GraphError("GRAPH_SELECTOR_REQUIRED", "name at least one node or repository path")
    nodes = graph["nodes"]
    selected, unresolved = _selected(graph, node_ids, paths)
    reached = {node_id: 0 for node_id in selected}
    edges: list[dict[str, str]] = []
    frontier = list(selected)
    for distance in range(1, depth + 1):
        next_frontier: list[str] = []
        for node_id in frontier:
            for relation in RELATIONS:
                for target in nodes[node_id].get(relation, []):
                    if target not in nodes:
                        continue
                    edges.append({"from": node_id, "relation": relation, "to": target})
                    if target not in reached:
                        reached[target] = distance
                        next_frontier.append(target)
        frontier = next_frontier
        if not frontier:
            break
    context: dict[str, list[dict[str, Any]]] = {kind: [] for kind in NODE_KINDS}
    for node_id, distance in sorted(reached.items()):
        node = nodes[node_id]
        record = {
            "id": node_id,
            "note": node["note"],
            "distance": distance,
            "selected": node_id in selected,
            "code_paths": sorted(node["code_paths"]),
        }
        if node["kind"] == "DECISION":
            record["reason"] = node["decision_reason"]
            record["date"] = node["decision_date"]
        context[node["kind"]].append(record)
    unverified = sorted(
        node_id for node_id in selected
        if nodes[node_id]["kind"] == "MODULE" and not nodes[node_id].get("verified_by")
    )
    return {
        "selected": selected,
        "depth": depth,
        "context": context,
        "edges": sorted(edges, key=lambda item: (item["from"], item["relation"], item["to"])),
        "unresolved": unresolved,
        "unverified_modules": unverified,
        "findings": graph["findings"],
    }


# ------------------------------------------------------------------------ proposal


def propose(graph: dict[str, Any], record: Any) -> dict[str, Any]:
    """Turn a recorded decision/outcome into note content for the controller to write.

    Nothing is written here. The result is the exact note text plus the
    ``OBSIDIAN_WRITE`` action, which the controller performs without approval.
    """
    if not isinstance(record, dict) or set(record) - {"note", "operation", "node", "kind", "reason", "date", "body", "relations"}:
        raise GraphError("GRAPH_PROPOSAL_INVALID", "a proposal holds only note, operation, node, kind, reason, date, body and relations")
    operation = record.get("operation")
    if operation not in PROPOSAL_OPERATIONS:
        raise GraphError("GRAPH_PROPOSAL_INVALID", f"operation must be one of {', '.join(PROPOSAL_OPERATIONS)}")
    note = record.get("note")
    if not isinstance(note, str) or not note.endswith(".md"):
        raise GraphError("GRAPH_PROPOSAL_PATH_UNSAFE", "note must be a Markdown path relative to the graph root")
    if not editable_pattern_is_safe(note) or "\x00" in note or set(note) & {"*", "?", "[", "]"}:
        raise GraphError(
            "GRAPH_PROPOSAL_PATH_UNSAFE",
            "note path must be a canonical, literal path that does not escape the graph root",
        )
    node_id = record.get("node")
    if not isinstance(node_id, str) or NODE_ID.fullmatch(node_id) is None:
        raise GraphError("GRAPH_PROPOSAL_INVALID", "node must be a lowercase node id")
    kind = record.get("kind")
    if kind not in NODE_KINDS:
        raise GraphError("GRAPH_PROPOSAL_INVALID", f"kind must be one of {', '.join(NODE_KINDS)}")
    body = record.get("body", "")
    if not isinstance(body, str) or not body.strip():
        raise GraphError("GRAPH_PROPOSAL_INVALID", "body must describe what happened")
    relations = record.get("relations", {})
    if not isinstance(relations, dict) or set(relations) - set(RELATIONS):
        raise GraphError("GRAPH_PROPOSAL_INVALID", "relations may only use the declared relation names")
    findings: list[dict[str, str]] = []
    for relation, targets in sorted(relations.items()):
        if not isinstance(targets, list) or any(
            not isinstance(target, str) or NODE_ID.fullmatch(target) is None for target in targets
        ):
            raise GraphError("GRAPH_PROPOSAL_INVALID", f"relations.{relation} must list node ids")
        left_kinds, right_kinds = RELATIONS[relation]
        if targets and kind not in left_kinds:
            findings.append(_finding("GRAPH_EDGE_KIND_INVALID", f"a {kind} node cannot declare {relation}", note=note))
        for target in targets:
            if target not in graph["nodes"]:
                findings.append(_finding("GRAPH_EDGE_DANGLING", f"{relation} points at unknown node {target}", note=note))
            elif graph["nodes"][target]["kind"] not in right_kinds:
                findings.append(_finding(
                    "GRAPH_EDGE_KIND_INVALID",
                    f"{relation} points at {target}, which is a {graph['nodes'][target]['kind']}",
                    note=note,
                ))
    reason, date = record.get("reason"), record.get("date")
    if kind == "DECISION":
        if not isinstance(reason, str) or not reason.strip():
            raise GraphError("GRAPH_DECISION_REASON_REQUIRED", "a decision proposal must record why it was taken")
        if reason != reason.strip() or any(character in reason for character in LINE_BREAKS + "\x00"):
            # The reason is rendered as a frontmatter scalar. Any character this
            # module's parser treats as a line break would produce a note it then
            # refuses, so the guard uses the parser's own line definition.
            raise GraphError(
                "GRAPH_DECISION_REASON_REQUIRED",
                "the reason must be a single trimmed line; put detail in the body",
            )
        if not isinstance(date, str) or ISO_DATE.fullmatch(date) is None:
            raise GraphError("GRAPH_DECISION_DATE_INVALID", "a decision proposal needs an ISO 8601 date")
    elif reason is not None or date is not None:
        raise GraphError("GRAPH_PROPOSAL_INVALID", "reason and date belong to a DECISION node")
    existing = graph["nodes"].get(node_id)
    if operation == "CREATE" and existing is not None:
        findings.append(_finding("GRAPH_NODE_DUPLICATED", f"{node_id} already exists in {existing['note']}", note=note))
    if operation == "APPEND" and (existing is None or existing["note"] != note):
        findings.append(_finding(
            "GRAPH_PROPOSAL_INVALID",
            f"APPEND requires {node_id} to already be declared by {note}",
            note=note,
        ))
    content = _render(node_id, kind, reason, date, relations, body, operation)
    if operation == "CREATE":
        # The invariant is "the approved content loads as the node described",
        # not a list of forbidden spellings. Enumerating those already missed a
        # newline class twice (plain \n, then U+2028) and a value shape once
        # (`[a, b]` reparsing as a list), so the rendered note is checked the way
        # a reader would check it: parse it back, and keep it loadable at all.
        mismatch = _render_mismatch(content, node_id, kind, reason, date, relations)
        if mismatch is not None:
            raise GraphError("GRAPH_PROPOSAL_INVALID", mismatch)
    encoded = len(content.encode("utf-8"))
    # An APPEND is concatenated onto a note that already has a size, so the
    # limit applies to the result, not to the fragment: a small fragment can
    # still push the note past the read limit and make the node disappear.
    already = (
        int(existing.get("note_bytes", 0))
        if operation == "APPEND" and existing is not None
        else 0
    )
    if already + encoded > MAX_NOTE_BYTES:
        # A note this size is refused on the read path, so approving it would
        # produce a note that loads as nothing.
        detail = (
            f"the rendered note is {encoded} bytes and would be refused above {MAX_NOTE_BYTES}; shorten the body"
            if not already
            else (
                f"appending {encoded} bytes to a {already}-byte note would exceed {MAX_NOTE_BYTES}; "
                "shorten the body or start a new note"
            )
        )
        raise GraphError("GRAPH_PROPOSAL_INVALID", detail)
    return {
        "status": "PROPOSED" if not findings else "BLOCKED",
        "action": OBSIDIAN_WRITE_ACTION,
        "approval": OBSIDIAN_WRITE_APPROVAL,
        "written": False,
        "note": note,
        "operation": operation,
        "content": content,
        "findings": sorted(findings, key=lambda item: (item["code"], item["detail"])),
    }


def _render_mismatch(
    content: str,
    node_id: str,
    kind: str,
    reason: str | None,
    date: str | None,
    relations: dict[str, list[str]],
) -> str | None:
    """Return why the rendered note would not read back as declared, or None.

    A proposal a human approves must load as the node it describes. Anything the
    parser would read differently is refused here, so the guarantee holds by
    construction rather than by keeping a blacklist in sync with the parser.
    """
    expected: dict[str, Any] = {"graph_node": node_id, "graph_kind": kind}
    if kind == "DECISION":
        expected["decision_reason"] = reason
        expected["decision_date"] = date
    for relation, targets in relations.items():
        if targets:
            expected[relation] = list(targets)
    try:
        parsed = parse_frontmatter(content)
    except GraphError as error:
        return f"the rendered note would not parse: {error.detail}"
    for field, value in expected.items():
        if parsed.get(field) != value:
            return (
                f"{field} would read back as {_excerpt(parsed.get(field))} "
                f"instead of {_excerpt(value)}; use plain text and put structure in the body"
            )
    return None


def _excerpt(value: Any) -> str:
    """Quote a value for a finding without letting its size into the manifest.

    A finding's detail is embedded in a dispatch manifest, so the same bound the
    cycle detail respects applies here: field values are caller-supplied and may
    be arbitrarily long.
    """
    text = repr(value)
    if len(text) <= MAX_DETAIL_EXCERPT:
        return text
    return f"{text[:MAX_DETAIL_EXCERPT]}… ({len(text)} characters)"


def _render(
    node_id: str,
    kind: str,
    reason: str | None,
    date: str | None,
    relations: dict[str, list[str]],
    body: str,
    operation: str,
) -> str:
    text = body.strip() + "\n"
    if operation == "APPEND":
        return f"\n## {date or 'Update'} — {node_id}\n\n{text}"
    lines = ["---", f"graph_node: {node_id}", f"graph_kind: {kind}"]
    if kind == "DECISION":
        lines.append(f"decision_reason: {reason}")
        lines.append(f"decision_date: {date}")
    for relation in RELATIONS:
        targets = relations.get(relation, [])
        if targets:
            lines.append(f"{relation}: [{', '.join(targets)}]")
    lines.append("---")
    return "\n".join(lines) + f"\n\n# {node_id}\n\n{text}"


# ------------------------------------------------------------------------------ CLI


def _graph(args: argparse.Namespace) -> dict[str, Any]:
    return build(load_notes(Path(args.repo), args.root))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read the project's context graph of modules, rules, tests and decisions.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "query", "propose"):
        command = commands.add_parser(name)
        command.add_argument("--repo", default=".", help="repository root (default: current directory)")
        command.add_argument(
            "--root",
            help=f"repository-relative graph directory (for example .hermes/orchestration/{DEFAULT_GRAPH_SUBPATH}); omit to read the bound Obsidian project container",
        )
        command.add_argument("--json", action="store_true", help="emit structured JSON")
        if name == "query":
            command.add_argument("--node", action="append", default=[], help="node id to start from (repeatable)")
            command.add_argument("--path", action="append", default=[], help="repository path to start from (repeatable)")
            command.add_argument("--depth", type=int, default=DEFAULT_DEPTH, help=f"relations to follow (0-{MAX_DEPTH}, default {DEFAULT_DEPTH})")
        if name == "propose":
            command.add_argument("--record", required=True, help="JSON file describing the decision or outcome to record")
    args = parser.parse_args(argv)

    try:
        graph = _graph(args)
        if args.command == "validate":
            result = {
                "valid": not graph["findings"],
                "nodes": len(graph["nodes"]),
                "kinds": {kind: sum(1 for node in graph["nodes"].values() if node["kind"] == kind) for kind in NODE_KINDS},
                "findings": graph["findings"],
            }
            code = 0 if result["valid"] else 2
        elif args.command == "query":
            result = query(graph, node_ids=args.node, paths=args.path, depth=args.depth)
            code = 2 if result["findings"] or result["unresolved"] else 0
        else:
            record = json.loads(Path(args.record).read_text(encoding="utf-8"))
            result = propose(graph, record)
            code = 0 if result["status"] == "PROPOSED" else 2
    except (GraphError, OSError, RecursionError, json.JSONDecodeError) as error:
        code_name = getattr(error, "code", None)
        if not isinstance(code_name, str):
            code_name = "GRAPH_SOURCE_UNAVAILABLE"
        result = {"valid": False, "findings": [_finding(code_name, str(error))]}
        code = 2
    print(json.dumps(result, sort_keys=True, ensure_ascii=False) if args.json else result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
