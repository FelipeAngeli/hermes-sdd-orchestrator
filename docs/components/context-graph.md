# Context graph

[Docs index](../README.md) · Related: [Obsidian vault](obsidian-vault.md), [Harness: stage context and bounded correction](harness.md), [Sub-agents and dispatch](sub-agents.md), [Action journal](action-journal.md)

**Files:** `runtime/context_graph.py`.

The context graph is a map of connected information about the project: which modules exist, what they depend on, which rules govern them, which tests cover them, which prior decisions apply and **why** those decisions were taken. Before touching `payments`, the controller can ask the graph what else is attached to `payments` instead of guessing from file names.

It is **optional** and needs no graph database. The graph *is* a set of Markdown notes with declarative frontmatter, kept either in the bound [Obsidian](obsidian-vault.md) project container or in a repository-local directory. `context_graph.py` is read-only: it never creates, edits or moves a note. Recording a new decision produces a **proposal**; writing it stays the `OBSIDIAN_WRITE` [`HUMAN_REQUIRED` action](fsm-and-loop.md#actions).

## A node is a note

A note joins the graph by declaring `graph_node`. A note without that key is an ordinary project note and is ignored — never a finding. Frontmatter is parsed with a deliberately narrow stdlib parser: `key: scalar`, `key: [a, b]` and block lists of `  - item`. Nesting, duplicate keys, an unclosed block or an unsupported key fail closed as `GRAPH_FRONTMATTER_INVALID`, because a graph two readers interpret differently is worse than no graph.

```markdown
---
graph_node: payments
graph_kind: MODULE
code_paths: [src/payments/**]
depends_on: [ride]
governed_by: [refund-window]
verified_by: [payments-suite]
decided_by: [use-bloc-for-checkout]
---

# payments
```

```markdown
---
graph_node: use-bloc-for-checkout
graph_kind: DECISION
decision_reason: Checkout needs replayable state transitions for audit
decision_date: 2026-09-14
documented_by: [checkout-doc]
---
```

| Field | Meaning |
| --- | --- |
| `graph_node` | The node ID: lowercase, `^[a-z][a-z0-9-]{0,63}$`, unique across the graph. |
| `graph_kind` | One of `NODE_KINDS`: `MODULE`, `RULE`, `TEST`, `DECISION`, `DOC`. |
| `code_paths` | Repository paths this node covers. Each pattern uses the same safety rule as a slice's editable paths: canonical, repository-relative, literal first segment, no match-all spelling. |
| `decision_reason`, `decision_date` | `DECISION_FIELDS`. Required on a `DECISION`: the reason it was taken and an ISO 8601 `YYYY-MM-DD` date. A decision without a recorded reason is not a decision, it is a leftover. |

`SCALAR_FIELDS` are the fields read as single values; `LIST_FIELDS` are `code_paths` plus every relation.

## Relations

Each relation is directed and typed, so a graph cannot quietly claim that a test governs a module:

| Relation | From | To |
| --- | --- | --- |
| `depends_on` | `MODULE` | `MODULE` |
| `governed_by` | `MODULE`, `TEST` | `RULE` |
| `verified_by` | `MODULE`, `RULE` | `TEST` |
| `decided_by` | `MODULE`, `RULE`, `TEST` | `DECISION` |
| `documented_by` | `MODULE`, `RULE`, `DECISION` | `DOC` |
| `supersedes` | `DECISION` | `DECISION` |

## Findings

Every refusal carries a stable code from `GRAPH_ERROR_CODES`:

| Code | Rule |
| --- | --- |
| `GRAPH_SOURCE_UNAVAILABLE` | The graph root does not exist or is not a directory, or a note is symlinked, non-UTF-8, not a regular file or larger than 256 KiB. Symlinked notes and subdirectories are never followed. |
| `GRAPH_ROOT_UNSAFE` | `--root` is absolute, escapes the repository, is not canonical, or the root itself or any ancestor up to the repository root is a symlink. The root goes through the same path rule as a slice's editable paths, so a caller cannot point the graph at notes outside the project. Containment is checked before existence, so a symlinked root reports as a containment failure rather than a missing directory. |
| `GRAPH_FRONTMATTER_INVALID` | The frontmatter is outside the supported subset, has a duplicate key or is unclosed. |
| `GRAPH_NODE_ID_INVALID`, `GRAPH_KIND_INVALID`, `GRAPH_FIELD_INVALID` | The node ID is not lowercase-dashed, the kind is not a `NODE_KINDS` member, or a field has the wrong shape (a scalar where a list is required, or a relation target that is not a node ID). |
| `GRAPH_NODE_DUPLICATED` | Two notes declare the same node ID; the finding names both. |
| `GRAPH_CODE_PATH_UNSAFE` | A `code_paths` pattern is absolute, escapes the repository or matches everything. |
| `GRAPH_EDGE_DANGLING`, `GRAPH_EDGE_KIND_INVALID` | A relation points at an unknown node, or the relation is not allowed between those two kinds. |
| `GRAPH_DEPENDENCY_CYCLE` | `depends_on` forms a cycle. Cycles are found as strongly connected components with an iterative pass, so a cycle reachable only through an already-finished node is still reported, a long dependency chain cannot exhaust the stack, and each cyclic group is named once. A group larger than `MAX_NAMED_CYCLE_MEMBERS` names its first members plus a count, so the finding stays short enough to embed in a manifest. |
| `GRAPH_DECISION_REASON_REQUIRED`, `GRAPH_DECISION_DATE_INVALID` | A `DECISION` lacks a reason or a valid ISO date. A proposal's reason must also be a single trimmed line, because it is rendered as a frontmatter scalar; every character in `LINE_BREAKS` (the parser's own line definition, which includes U+2028, U+2029 and U+0085, not only `\n`) is refused. Detail belongs in the body. |
| `GRAPH_SELECTOR_REQUIRED` | A query names no node and no path, or its depth is outside `0`–`MAX_DEPTH`. |
| `GRAPH_PROPOSAL_INVALID`, `GRAPH_PROPOSAL_PATH_UNSAFE` | A proposal has an unsupported shape/operation, or its note path is absolute, non-canonical, backslash-bearing, glob-bearing or not Markdown. The note path uses the same path rule as the graph root and a slice's editable paths, and additionally must be literal: a note path is one file, never a pattern. |

## Querying before acting

```text
context_graph.py validate --repo . [--root <dir>] --json
context_graph.py query --repo . [--root <dir>] --node payments --path src/payments/checkout --depth 2 --json
context_graph.py propose --repo . [--root <dir>] --record record.json --json
```

Subcommands `validate`, `query` and `propose`. Flags: `--repo`, `--root`, `--json`, plus `--node`, `--path`, `--depth` for `query` and `--record` for `propose`. With `--root` the graph is repository-local (for example `.hermes/orchestration/context-graph`); the root must be a canonical repository-relative directory with no symlinked component, or it is refused as `GRAPH_ROOT_UNSAFE` before any note is read. Without `--root`, the bound Obsidian project container is read through the connector's guarded no-follow reads. `validate` exits `2` on any finding; `query` exits `2` when a finding or an unresolved selector remains.

`query` starts from the named nodes plus every node whose `code_paths` cover a named repository path, then follows relations outward up to `--depth` (default `DEFAULT_DEPTH` = 2, maximum `MAX_DEPTH`; depth `0` is the selection itself). The result groups nodes by kind with their `distance`, lists the `edges` it traversed, returns each in-scope decision with its reason and date, reports `unresolved` selectors instead of silently returning less, and names `unverified_modules`: selected modules with no `verified_by` test. The edges are part of the answer so the controller can cite *why* a rule or decision is in scope rather than trust an opaque bundle.

## Recording the outcome

`propose` turns a decision or outcome into the exact note content a human would approve. It writes nothing:

```json
{"operation": "CREATE", "note": "decisions/idempotent-refunds.md", "node": "idempotent-refunds",
 "kind": "DECISION", "reason": "A duplicated webhook must not refund twice", "date": "2026-10-02",
 "body": "Refund handlers key on the provider event id.", "relations": {"documented_by": ["checkout-doc"]}}
```

`PROPOSAL_OPERATIONS` are `CREATE` (renders frontmatter plus body; blocked when the node already exists) and `APPEND` (renders only a dated section; blocked unless that node is already declared by that exact note). The report always carries `written: false`, `action: OBSIDIAN_WRITE` and `approval: HUMAN_REQUIRED`, and a blocked proposal still creates no file. Dangling or wrongly typed relations and a duplicate node become findings; a malformed proposal is refused outright.

A `CREATE` proposal is only returned if its rendered content **parses back as the node it declares**: `propose` runs the content through this module's own `parse_frontmatter` and compares every declared field, refusing a mismatch as `GRAPH_PROPOSAL_INVALID` naming the field and both readings (each excerpted to `MAX_DETAIL_EXCERPT`, because a finding ends up in a dispatch manifest). The **resulting note** must also stay within `MAX_NOTE_BYTES`, the bound the read path enforces, since a larger note would load as nothing: for `CREATE` that is the rendered content measured in UTF-8 bytes, and for `APPEND` it is the existing note's size plus the fragment, so a small fragment cannot push an already-large note over the limit. The guarantee is therefore what a reader would check — the content loads as the node described — not a list of forbidden spellings: enumerating those missed `\n`, then the rest of `LINE_BREAKS`, then a reason like `[deferred]` that a human reads as text and the parser reads as a one-item list. The `LINE_BREAKS` and single-trimmed-line rules stay because they give a precise reason for the common mistake.

## In the dispatch manifest

The [stage context manifest](harness.md#stage-context-manifest-schema-2-stage_contextpy) carries an optional `context_graph` record: the `status` (`CURRENT`, `REFRESHED`, `PARTIAL`, `MISSING`, `NOT_CONFIGURED`), the `source` (`OBSIDIAN` or `REPOSITORY`), the `selectors` queried, the `nodes` with kind/note/distance, the in-scope `decisions` with reason and date, the `unresolved` selectors and any `findings`. Omitting the key, or `null`, is valid: a project without a graph is never blocked. The rules `stage_context.py check` applies to a declared record are listed with the other [harness rules](harness.md#rules-enforced-by-check). The record is not part of the approved slice hash, so refreshing the graph never invalidates an approval.
