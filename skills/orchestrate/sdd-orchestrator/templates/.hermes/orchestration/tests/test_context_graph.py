"""Tests for the read-only project context graph."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))

import context_graph  # noqa: E402


def note(node: str, kind: str, **fields: object) -> str:
    lines = ["---", f"graph_node: {node}", f"graph_kind: {kind}"]
    for key, value in fields.items():
        if isinstance(value, list):
            lines.append(f"{key}: [{', '.join(str(item) for item in value)}]")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + f"\n\n# {node}\n\nBody.\n"


class GraphTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name).resolve()
        self.root = "graph"
        (self.repo / self.root).mkdir()

    def write(self, relative: str, text: str) -> None:
        path = self.repo / self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def graph(self) -> dict[str, object]:
        return context_graph.build(context_graph.load_notes(self.repo, self.root))

    def seed(self) -> None:
        self.write("payments.md", note(
            "payments", "MODULE",
            code_paths=["src/payments/**"],
            depends_on=["ride"],
            governed_by=["refund-window"],
            verified_by=["payments-suite"],
            decided_by=["use-bloc-for-checkout"],
        ))
        self.write("ride.md", note("ride", "MODULE", code_paths=["src/ride/**"]))
        self.write("refund-window.md", note("refund-window", "RULE", verified_by=["payments-suite"]))
        self.write("payments-suite.md", note("payments-suite", "TEST", code_paths=["test/payments/**"]))
        self.write("decisions/bloc.md", note(
            "use-bloc-for-checkout", "DECISION",
            decision_reason="Checkout needs replayable state transitions for audit",
            decision_date="2026-09-14",
            documented_by=["checkout-doc"],
        ))
        self.write("docs/checkout.md", note("checkout-doc", "DOC"))


class FrontmatterTests(GraphTestCase):
    def test_scalar_inline_list_and_block_list_are_parsed(self) -> None:
        fields = context_graph.parse_frontmatter(
            "---\n"
            "graph_node: payments\n"
            "code_paths: [src/a/**, src/b/**]\n"
            "depends_on:\n"
            "  - ride\n"
            "  - billing\n"
            "---\n\nbody\n"
        )

        self.assertEqual("payments", fields["graph_node"])
        self.assertEqual(["src/a/**", "src/b/**"], fields["code_paths"])
        self.assertEqual(["ride", "billing"], fields["depends_on"])

    def test_duplicate_key_unclosed_block_and_nesting_are_refused(self) -> None:
        for text in (
            "---\ngraph_node: a\ngraph_node: b\n---\n",
            "---\ngraph_node: a\n",
            "---\ngraph_node: a\nnested:\n  child: value\n---\n",
            "---\n  - orphan\n---\n",
        ):
            with self.subTest(text=text):
                with self.assertRaises(context_graph.GraphError) as raised:
                    context_graph.parse_frontmatter(text)
                self.assertEqual("GRAPH_FRONTMATTER_INVALID", raised.exception.code)

    def test_a_note_without_frontmatter_is_not_part_of_the_graph(self) -> None:
        self.write("journal.md", "# Meeting notes\n\nNothing structured here.\n")

        graph = self.graph()

        self.assertEqual({}, graph["nodes"])
        self.assertEqual([], graph["findings"])

    def test_a_note_with_frontmatter_but_no_node_is_ignored_without_a_finding(self) -> None:
        self.write("page.md", "---\ntags: [reference]\n---\n\nText.\n")

        self.assertEqual([], self.graph()["findings"])


class StructureTests(GraphTestCase):
    def test_a_valid_graph_reports_every_kind_without_findings(self) -> None:
        self.seed()

        graph = self.graph()

        self.assertEqual([], graph["findings"])
        self.assertEqual(
            {"payments", "ride", "refund-window", "payments-suite", "use-bloc-for-checkout", "checkout-doc"},
            set(graph["nodes"]),
        )
        self.assertEqual("DECISION", graph["nodes"]["use-bloc-for-checkout"]["kind"])

    def test_a_decision_without_a_reason_or_a_valid_date_is_refused(self) -> None:
        self.write("d1.md", note("no-reason", "DECISION", decision_date="2026-09-14"))
        self.write("d2.md", note("bad-date", "DECISION", decision_reason="because", decision_date="14/09/2026"))

        codes = {finding["code"] for finding in self.graph()["findings"]}

        self.assertEqual({"GRAPH_DECISION_REASON_REQUIRED", "GRAPH_DECISION_DATE_INVALID"}, codes)

    def test_dangling_and_wrongly_typed_edges_are_named(self) -> None:
        self.seed()
        self.write("broken.md", note("broken", "MODULE", depends_on=["ghost"], governed_by=["payments-suite"]))

        findings = self.graph()["findings"]

        self.assertIn("GRAPH_EDGE_DANGLING", {finding["code"] for finding in findings})
        self.assertIn("GRAPH_EDGE_KIND_INVALID", {finding["code"] for finding in findings})
        self.assertTrue(any("ghost" in finding["detail"] for finding in findings))

    def test_a_relation_declared_by_the_wrong_kind_is_refused(self) -> None:
        self.write("r.md", note("some-rule", "RULE", depends_on=["other-rule"]))
        self.write("r2.md", note("other-rule", "RULE"))

        findings = self.graph()["findings"]

        self.assertEqual(["GRAPH_EDGE_KIND_INVALID"], [finding["code"] for finding in findings])

    def test_a_duplicate_node_id_names_both_notes(self) -> None:
        self.write("a.md", note("payments", "MODULE"))
        self.write("b.md", note("payments", "MODULE"))

        (finding,) = self.graph()["findings"]

        self.assertEqual("GRAPH_NODE_DUPLICATED", finding["code"])
        self.assertIn("a.md", finding["detail"])
        self.assertIn("b.md", finding["detail"])

    def test_an_invalid_id_kind_or_field_shape_is_refused(self) -> None:
        self.write("a.md", "---\ngraph_node: Payments\ngraph_kind: MODULE\n---\n\nx\n")
        self.write("b.md", note("ok-id", "SERVICE"))
        self.write("c.md", "---\ngraph_node: scalar-list\ngraph_kind: MODULE\ndepends_on: ride\n---\n\nx\n")

        codes = {finding["code"] for finding in self.graph()["findings"]}

        self.assertEqual({"GRAPH_NODE_ID_INVALID", "GRAPH_KIND_INVALID", "GRAPH_FIELD_INVALID"}, codes)

    def test_an_unsafe_code_path_is_refused_like_an_editable_path(self) -> None:
        for pattern in ("/absolute/elsewhere", "../outside/**", "**", "*", "src/../outside", "~/elsewhere"):
            with self.subTest(pattern=pattern):
                self.write("m.md", note("m", "MODULE", code_paths=[pattern]))
                findings = self.graph()["findings"]
                self.assertEqual(["GRAPH_CODE_PATH_UNSAFE"], [finding["code"] for finding in findings])

    def test_a_dependency_cycle_is_reported_once_with_its_members(self) -> None:
        self.write("a.md", note("alpha", "MODULE", depends_on=["beta"]))
        self.write("b.md", note("beta", "MODULE", depends_on=["gamma"]))
        self.write("c.md", note("gamma", "MODULE", depends_on=["alpha"]))

        findings = [finding for finding in self.graph()["findings"] if finding["code"] == "GRAPH_DEPENDENCY_CYCLE"]

        self.assertEqual(1, len(findings))
        self.assertIn("alpha, beta, gamma", findings[0]["detail"])

    def test_overlapping_cycles_in_one_group_are_reported_once_naming_every_member(self) -> None:
        """A cycle reachable only through a finished node must not be lost."""
        self.write("a.md", note("alpha", "MODULE", depends_on=["beta", "gamma"]))
        self.write("b.md", note("beta", "MODULE", depends_on=["gamma"]))
        self.write("c.md", note("gamma", "MODULE", depends_on=["alpha"]))

        findings = [finding for finding in self.graph()["findings"] if finding["code"] == "GRAPH_DEPENDENCY_CYCLE"]

        self.assertEqual(1, len(findings))
        for member in ("alpha", "beta", "gamma"):
            self.assertIn(member, findings[0]["detail"])

    def test_two_independent_cycles_are_reported_separately(self) -> None:
        self.write("a.md", note("alpha", "MODULE", depends_on=["beta"]))
        self.write("b.md", note("beta", "MODULE", depends_on=["alpha"]))
        self.write("c.md", note("gamma", "MODULE", depends_on=["delta"]))
        self.write("d.md", note("delta", "MODULE", depends_on=["gamma"]))

        findings = [finding for finding in self.graph()["findings"] if finding["code"] == "GRAPH_DEPENDENCY_CYCLE"]

        self.assertEqual(2, len(findings))

    def test_an_acyclic_diamond_is_not_a_cycle(self) -> None:
        self.write("a.md", note("alpha", "MODULE", depends_on=["beta", "gamma"]))
        self.write("b.md", note("beta", "MODULE", depends_on=["delta"]))
        self.write("c.md", note("gamma", "MODULE", depends_on=["delta"]))
        self.write("d.md", note("delta", "MODULE"))

        self.assertEqual([], self.graph()["findings"])

    def test_a_long_dependency_chain_does_not_exhaust_the_stack(self) -> None:
        length = 3000
        for position in range(length):
            depends = [f"node-{position + 1}"] if position + 1 < length else []
            self.write(f"n{position}.md", note(f"node-{position}", "MODULE", depends_on=depends))

        graph = self.graph()

        self.assertEqual(length, len(graph["nodes"]))
        self.assertEqual([], graph["findings"])

    def test_a_large_cyclic_group_stays_one_bounded_finding(self) -> None:
        """The detail is embedded in a dispatch manifest; it must stay readable."""
        length = 400
        for position in range(length):
            self.write(f"n{position}.md", note(
                f"node-{position}", "MODULE", depends_on=[f"node-{(position + 1) % length}"]
            ))

        findings = [finding for finding in self.graph()["findings"] if finding["code"] == "GRAPH_DEPENDENCY_CYCLE"]

        self.assertEqual(1, len(findings))
        self.assertLess(len(findings[0]["detail"]), 400)
        self.assertIn(f"{length} node(s)", findings[0]["detail"])
        self.assertIn("more", findings[0]["detail"])

    def test_a_self_dependency_is_a_cycle(self) -> None:
        self.write("a.md", note("alpha", "MODULE", depends_on=["alpha"]))

        self.assertEqual(
            ["GRAPH_DEPENDENCY_CYCLE"],
            [finding["code"] for finding in self.graph()["findings"]],
        )


class SourceTests(GraphTestCase):
    def test_a_symlinked_note_fails_closed_instead_of_being_followed(self) -> None:
        outside = self.repo / "outside"
        outside.mkdir()
        (outside / "payments.md").write_text(note("payments", "MODULE"), encoding="utf-8")
        os.symlink(outside / "payments.md", self.repo / self.root / "linked.md")

        with self.assertRaises(context_graph.GraphError) as raised:
            context_graph.load_notes(self.repo, self.root)

        self.assertEqual("GRAPH_SOURCE_UNAVAILABLE", raised.exception.code)
        self.assertIn("symlink", raised.exception.detail)

    def test_an_absent_graph_root_is_refused(self) -> None:
        with self.assertRaises(context_graph.GraphError) as raised:
            context_graph.load_notes(self.repo, "missing-dir")

        self.assertEqual("GRAPH_SOURCE_UNAVAILABLE", raised.exception.code)

    def test_an_absolute_graph_root_cannot_replace_the_repository_root(self) -> None:
        outside = self.repo.parent / "outside-graph"
        outside.mkdir(exist_ok=True)
        (outside / "ghost.md").write_text(note("ghost", "MODULE"), encoding="utf-8")

        with self.assertRaises(context_graph.GraphError) as raised:
            context_graph.load_notes(self.repo, str(outside))

        self.assertEqual("GRAPH_ROOT_UNSAFE", raised.exception.code)

    def test_a_graph_root_that_escapes_the_repository_is_refused(self) -> None:
        outside = self.repo.parent / "outside-graph"
        outside.mkdir(exist_ok=True)
        (outside / "ghost.md").write_text(note("ghost", "MODULE"), encoding="utf-8")

        for subpath in ("../outside-graph", "graph/../../outside-graph", "~/graph", "graph/./nested"):
            with self.subTest(subpath=subpath):
                with self.assertRaises(context_graph.GraphError) as raised:
                    context_graph.load_notes(self.repo, subpath)
                self.assertEqual("GRAPH_ROOT_UNSAFE", raised.exception.code)

    def test_a_symlinked_graph_root_is_refused_as_a_containment_failure(self) -> None:
        outside = self.repo.parent / "outside-graph"
        outside.mkdir(exist_ok=True)
        (outside / "ghost.md").write_text(note("ghost", "MODULE"), encoding="utf-8")
        os.symlink(outside, self.repo / "linked-graph")

        with self.assertRaises(context_graph.GraphError) as raised:
            context_graph.load_notes(self.repo, "linked-graph")

        self.assertEqual("GRAPH_ROOT_UNSAFE", raised.exception.code)

    def test_a_graph_root_under_a_symlinked_parent_is_refused(self) -> None:
        outside = self.repo.parent / "outside-tree"
        (outside / "inner").mkdir(parents=True, exist_ok=True)
        (outside / "inner" / "ghost.md").write_text(note("ghost", "MODULE"), encoding="utf-8")
        os.symlink(outside, self.repo / "linked-parent")

        with self.assertRaises(context_graph.GraphError) as raised:
            context_graph.load_notes(self.repo, "linked-parent/inner")

        self.assertEqual("GRAPH_ROOT_UNSAFE", raised.exception.code)

    def test_a_symlinked_subdirectory_is_not_walked(self) -> None:
        outside = self.repo / "outside"
        outside.mkdir()
        (outside / "ghost.md").write_text(note("ghost", "MODULE"), encoding="utf-8")
        os.symlink(outside, self.repo / self.root / "linked")

        self.assertEqual({}, self.graph()["nodes"])

    def test_non_utf8_and_oversized_notes_are_refused(self) -> None:
        (self.repo / self.root / "bad.md").write_bytes(b"---\ngraph_node: \xff\n---\n")

        with self.assertRaises(context_graph.GraphError) as raised:
            context_graph.load_notes(self.repo, self.root)

        self.assertEqual("GRAPH_SOURCE_UNAVAILABLE", raised.exception.code)


class QueryTests(GraphTestCase):
    def test_a_code_path_resolves_to_the_module_that_declares_it(self) -> None:
        self.seed()

        result = context_graph.query(self.graph(), node_ids=[], paths=["src/payments/checkout"], depth=1)

        self.assertEqual(["payments"], result["selected"])
        self.assertEqual(
            {"refund-window"},
            {record["id"] for record in result["context"]["RULE"]},
        )
        self.assertEqual(
            {"payments-suite"},
            {record["id"] for record in result["context"]["TEST"]},
        )
        self.assertEqual(
            {"use-bloc-for-checkout"},
            {record["id"] for record in result["context"]["DECISION"]},
        )

    def test_a_decision_is_returned_with_its_reason_and_date(self) -> None:
        self.seed()

        result = context_graph.query(self.graph(), node_ids=["payments"], paths=[], depth=1)

        (decision,) = result["context"]["DECISION"]
        self.assertEqual("Checkout needs replayable state transitions for audit", decision["reason"])
        self.assertEqual("2026-09-14", decision["date"])

    def test_depth_bounds_the_traversal_and_zero_returns_only_the_selection(self) -> None:
        self.seed()
        graph = self.graph()

        selection = context_graph.query(graph, node_ids=["payments"], paths=[], depth=0)
        shallow = context_graph.query(graph, node_ids=["payments"], paths=[], depth=1)
        deep = context_graph.query(graph, node_ids=["payments"], paths=[], depth=2)

        self.assertEqual([], selection["context"]["DOC"])
        self.assertEqual([], shallow["context"]["DOC"])
        self.assertEqual(["checkout-doc"], [record["id"] for record in deep["context"]["DOC"]])

    def test_every_returned_node_carries_the_edges_that_justify_it(self) -> None:
        self.seed()

        result = context_graph.query(self.graph(), node_ids=["payments"], paths=[], depth=1)

        self.assertIn({"from": "payments", "relation": "governed_by", "to": "refund-window"}, result["edges"])
        self.assertIn({"from": "payments", "relation": "decided_by", "to": "use-bloc-for-checkout"}, result["edges"])

    def test_an_unmatched_selector_is_reported_instead_of_returning_nothing(self) -> None:
        self.seed()

        result = context_graph.query(
            self.graph(), node_ids=["ghost"], paths=["lib/unmapped/entry"], depth=1
        )

        self.assertEqual(
            {"ghost", "lib/unmapped/entry"},
            {item["selector"] for item in result["unresolved"]},
        )

    def test_a_selected_module_without_a_test_is_reported(self) -> None:
        self.seed()

        result = context_graph.query(self.graph(), node_ids=["ride"], paths=[], depth=1)

        self.assertEqual(["ride"], result["unverified_modules"])

    def test_a_query_without_a_selector_or_with_an_invalid_depth_is_refused(self) -> None:
        self.seed()
        graph = self.graph()

        for kwargs in (
            {"node_ids": [], "paths": [], "depth": 1},
            {"node_ids": ["payments"], "paths": [], "depth": -1},
            {"node_ids": ["payments"], "paths": [], "depth": context_graph.MAX_DEPTH + 1},
            {"node_ids": ["payments"], "paths": [], "depth": True},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(context_graph.GraphError) as raised:
                    context_graph.query(graph, **kwargs)
                self.assertEqual("GRAPH_SELECTOR_REQUIRED", raised.exception.code)


class ProposalTests(GraphTestCase):
    def record(self, **overrides: object) -> dict[str, object]:
        record = {
            "note": "decisions/idempotent-refunds.md",
            "operation": "CREATE",
            "node": "idempotent-refunds",
            "kind": "DECISION",
            "reason": "A duplicated webhook must not refund twice",
            "date": "2026-10-02",
            "body": "Refund handlers key on the provider event id.",
            "relations": {"documented_by": ["checkout-doc"]},
        }
        record.update(overrides)
        return record

    def test_a_valid_decision_proposal_renders_a_note_without_writing_it(self) -> None:
        self.seed()

        result = context_graph.propose(self.graph(), self.record())

        self.assertEqual("PROPOSED", result["status"])
        self.assertFalse(result["written"])
        self.assertEqual("OBSIDIAN_WRITE", result["action"])
        self.assertEqual("HUMAN_REQUIRED", result["approval"])
        self.assertIn("graph_node: idempotent-refunds", result["content"])
        self.assertIn("decision_reason: A duplicated webhook must not refund twice", result["content"])
        self.assertIn("documented_by: [checkout-doc]", result["content"])
        self.assertFalse((self.repo / self.root / "decisions" / "idempotent-refunds.md").exists())

    def test_a_proposal_never_creates_a_file_even_when_blocked(self) -> None:
        self.seed()

        result = context_graph.propose(self.graph(), self.record(relations={"documented_by": ["ghost-doc"]}))

        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual(["GRAPH_EDGE_DANGLING"], [finding["code"] for finding in result["findings"]])
        self.assertFalse((self.repo / self.root / "decisions" / "idempotent-refunds.md").exists())

    def test_creating_an_existing_node_or_appending_to_an_absent_one_is_blocked(self) -> None:
        self.seed()

        duplicate = context_graph.propose(self.graph(), self.record(
            note="payments.md", node="payments", kind="MODULE", reason=None, date=None, relations={},
        ))
        absent = context_graph.propose(self.graph(), self.record(
            operation="APPEND", note="decisions/absent.md", node="absent", kind="MODULE",
            reason=None, date=None, relations={},
        ))

        self.assertEqual("BLOCKED", duplicate["status"])
        self.assertEqual(["GRAPH_NODE_DUPLICATED"], [finding["code"] for finding in duplicate["findings"]])
        self.assertEqual("BLOCKED", absent["status"])
        self.assertEqual(["GRAPH_PROPOSAL_INVALID"], [finding["code"] for finding in absent["findings"]])

    def test_appending_to_an_existing_node_renders_a_dated_section_only(self) -> None:
        self.seed()

        result = context_graph.propose(self.graph(), self.record(
            operation="APPEND",
            note="decisions/bloc.md",
            node="use-bloc-for-checkout",
            body="Outcome: checkout replay verified in staging.",
        ))

        self.assertEqual("PROPOSED", result["status"])
        self.assertNotIn("graph_node:", result["content"])
        self.assertIn("## 2026-10-02 — use-bloc-for-checkout", result["content"])

    def test_a_rendered_proposal_reparses_as_the_node_it_declares(self) -> None:
        """A proposal this module's own parser would refuse is not a proposal."""
        self.seed()

        result = context_graph.propose(self.graph(), self.record())
        fields = context_graph.parse_frontmatter(result["content"])

        self.assertEqual("idempotent-refunds", fields["graph_node"])
        self.assertEqual("DECISION", fields["graph_kind"])
        self.assertEqual("A duplicated webhook must not refund twice", fields["decision_reason"])
        self.assertEqual("2026-10-02", fields["decision_date"])
        self.assertEqual(["checkout-doc"], fields["documented_by"])

    def test_an_append_that_would_push_the_note_over_the_limit_is_refused(self) -> None:
        """A small fragment still makes the node disappear if the note overflows."""
        self.seed()
        graph = self.graph()
        existing = context_graph.propose(graph, self.record(
            body="x" * (context_graph.MAX_NOTE_BYTES - 300)
        ))
        self.write("decisions/idempotent-refunds.md", existing["content"])
        graph = self.graph()
        self.assertIn("idempotent-refunds", graph["nodes"])

        with self.assertRaises(context_graph.GraphError) as raised:
            context_graph.propose(graph, self.record(
                operation="APPEND",
                note="decisions/idempotent-refunds.md",
                node="idempotent-refunds",
                body="y" * 1000,
            ))

        self.assertEqual("GRAPH_PROPOSAL_INVALID", raised.exception.code)
        self.assertIn("appending", raised.exception.detail)
        self.assertIn(str(context_graph.MAX_NOTE_BYTES), raised.exception.detail)

    def test_an_append_that_fits_is_accepted_and_the_note_still_loads(self) -> None:
        self.seed()
        created = context_graph.propose(self.graph(), self.record())
        self.write("decisions/idempotent-refunds.md", created["content"])

        fragment = context_graph.propose(self.graph(), self.record(
            operation="APPEND",
            note="decisions/idempotent-refunds.md",
            node="idempotent-refunds",
            body="Outcome: replay verified in staging.",
        ))
        self.write("decisions/idempotent-refunds.md", created["content"] + fragment["content"])
        graph = self.graph()

        self.assertEqual("PROPOSED", fragment["status"])
        self.assertEqual([], graph["findings"])
        self.assertIn("idempotent-refunds", graph["nodes"])

    def test_the_size_gate_counts_bytes_not_characters(self) -> None:
        """A multi-byte body must not slip past a character-based limit."""
        self.seed()

        with self.assertRaises(context_graph.GraphError) as raised:
            context_graph.propose(self.graph(), self.record(
                body="\U0001f9e9" * (context_graph.MAX_NOTE_BYTES // 4)
            ))

        self.assertEqual("GRAPH_PROPOSAL_INVALID", raised.exception.code)

    def test_an_oversized_proposal_is_refused_because_the_note_would_not_load(self) -> None:
        """An accepted proposal must be loadable, not merely reparseable."""
        self.seed()

        with self.assertRaises(context_graph.GraphError) as raised:
            context_graph.propose(self.graph(), self.record(
                body="x" * (context_graph.MAX_NOTE_BYTES + 10)
            ))

        self.assertEqual("GRAPH_PROPOSAL_INVALID", raised.exception.code)
        self.assertIn(str(context_graph.MAX_NOTE_BYTES), raised.exception.detail)

    def test_a_proposal_just_inside_the_note_limit_is_accepted_and_loads(self) -> None:
        self.seed()
        graph = self.graph()
        overhead = len(context_graph.propose(graph, self.record(body="x"))["content"].encode("utf-8")) - 1

        result = context_graph.propose(graph, self.record(
            body="x" * (context_graph.MAX_NOTE_BYTES - overhead)
        ))
        self.write("decisions/idempotent-refunds.md", result["content"])

        self.assertEqual("PROPOSED", result["status"])
        self.assertEqual(context_graph.MAX_NOTE_BYTES, len(result["content"].encode("utf-8")))
        self.assertEqual([], self.graph()["findings"])
        self.assertIn("idempotent-refunds", self.graph()["nodes"])

    def test_a_refusal_never_embeds_an_unbounded_field_value(self) -> None:
        """A finding's detail goes into a dispatch manifest; it must stay short."""
        self.seed()

        with self.assertRaises(context_graph.GraphError) as raised:
            context_graph.propose(self.graph(), self.record(reason="[" + "a" * 200_000 + "]"))

        self.assertEqual("GRAPH_PROPOSAL_INVALID", raised.exception.code)
        self.assertLess(len(raised.exception.detail), 600)
        self.assertIn("characters", raised.exception.detail)

    def test_a_reason_that_would_read_back_as_a_list_is_refused(self) -> None:
        """A bracket-delimited scalar reparses as a list, so the note would lie."""
        self.seed()
        graph = self.graph()

        for reason in ("[deferred]", "[a, b]", "[ADR-7 superseded]"):
            with self.subTest(reason=reason):
                with self.assertRaises(context_graph.GraphError) as raised:
                    context_graph.propose(graph, self.record(reason=reason))
                self.assertEqual("GRAPH_PROPOSAL_INVALID", raised.exception.code)
                self.assertIn("decision_reason", raised.exception.detail)

    def test_no_accepted_create_proposal_can_read_back_as_a_different_node(self) -> None:
        """The round trip is the invariant: parse the rendered note and compare."""
        self.seed()
        graph = self.graph()
        candidates = [
            "short",
            "with: a colon",
            "with #hash and [brackets] inside",
            "- leading dash",
            "[bracketed]",
            "  padded  ",
            "multi\nline",
            "u2028\u2028separated",
            "--- fence like",
            "trailing colon:",
            "a" * 300,
            "tab\tseparated",
        ]

        accepted = 0
        for reason in candidates:
            with self.subTest(reason=reason[:24]):
                try:
                    result = context_graph.propose(graph, self.record(reason=reason))
                except context_graph.GraphError:
                    continue
                accepted += 1
                fields = context_graph.parse_frontmatter(result["content"])
                self.assertEqual(reason, fields["decision_reason"])
                self.assertEqual("idempotent-refunds", fields["graph_node"])
                self.assertEqual(["checkout-doc"], fields["documented_by"])
        self.assertGreater(accepted, 0, "the fixture must accept at least one reason")

    def test_an_accepted_proposal_loads_as_the_node_it_declares(self) -> None:
        """End to end: the approved content, written as a note, must be that node."""
        self.seed()

        result = context_graph.propose(self.graph(), self.record())
        self.write("decisions/idempotent-refunds.md", result["content"])
        graph = self.graph()

        self.assertEqual([], graph["findings"])
        self.assertIn("idempotent-refunds", graph["nodes"])
        self.assertEqual("DECISION", graph["nodes"]["idempotent-refunds"]["kind"])
        self.assertEqual(
            "A duplicated webhook must not refund twice",
            graph["nodes"]["idempotent-refunds"]["decision_reason"],
        )

    def test_every_line_break_the_parser_knows_is_refused_in_a_reason(self) -> None:
        """The guard must use the parser's own definition of a line, not just \\n."""
        self.seed()
        graph = self.graph()

        for separator in context_graph.LINE_BREAKS:
            with self.subTest(separator=repr(separator)):
                with self.assertRaises(context_graph.GraphError) as raised:
                    context_graph.propose(graph, self.record(reason=f"left{separator}right"))
                self.assertEqual("GRAPH_DECISION_REASON_REQUIRED", raised.exception.code)

    def test_a_rendered_proposal_reparses_for_every_accepted_reason(self) -> None:
        self.seed()
        graph = self.graph()

        for reason in ("short", "with: a colon", "with #hash and [brackets]", "a" * 200):
            with self.subTest(reason=reason[:20]):
                result = context_graph.propose(graph, self.record(reason=reason))
                fields = context_graph.parse_frontmatter(result["content"])
                self.assertEqual(reason, fields["decision_reason"])

    def test_a_glob_bearing_note_path_is_refused_because_a_note_path_is_literal(self) -> None:
        self.seed()
        graph = self.graph()

        for path in ("decisions/*.md", "decisions/d?.md", "decisions/[ab].md"):
            with self.subTest(path=path):
                with self.assertRaises(context_graph.GraphError) as raised:
                    context_graph.propose(graph, self.record(note=path))
                self.assertEqual("GRAPH_PROPOSAL_PATH_UNSAFE", raised.exception.code)

    def test_an_ordinary_nested_note_path_is_still_accepted(self) -> None:
        self.seed()
        graph = self.graph()

        for path in ("d.md", "decisions/foo.md", "a/b/c/decision-1.md"):
            with self.subTest(path=path):
                self.assertEqual("PROPOSED", context_graph.propose(graph, self.record(note=path))["status"])

    def test_an_unsafe_note_path_or_malformed_proposal_is_refused(self) -> None:
        self.seed()
        graph = self.graph()

        for overrides, code in (
            ({"note": "/abs/decision.md"}, "GRAPH_PROPOSAL_PATH_UNSAFE"),
            ({"note": "../escape.md"}, "GRAPH_PROPOSAL_PATH_UNSAFE"),
            ({"note": "decision.txt"}, "GRAPH_PROPOSAL_PATH_UNSAFE"),
            ({"operation": "DELETE"}, "GRAPH_PROPOSAL_INVALID"),
            ({"node": "Bad Id"}, "GRAPH_PROPOSAL_INVALID"),
            ({"kind": "SERVICE"}, "GRAPH_PROPOSAL_INVALID"),
            ({"body": "   "}, "GRAPH_PROPOSAL_INVALID"),
            ({"relations": {"unknown_relation": ["checkout-doc"]}}, "GRAPH_PROPOSAL_INVALID"),
            ({"reason": ""}, "GRAPH_DECISION_REASON_REQUIRED"),
            ({"reason": "A duplicated webhook\nmust not refund twice"}, "GRAPH_DECISION_REASON_REQUIRED"),
            ({"reason": "  padded reason  "}, "GRAPH_DECISION_REASON_REQUIRED"),
            ({"note": "decisions\\..\\escape.md"}, "GRAPH_PROPOSAL_PATH_UNSAFE"),
            ({"note": "decisions//double.md"}, "GRAPH_PROPOSAL_PATH_UNSAFE"),
            ({"note": "~/decision.md"}, "GRAPH_PROPOSAL_PATH_UNSAFE"),
            ({"date": "2026/10/02"}, "GRAPH_DECISION_DATE_INVALID"),
            ({"kind": "MODULE"}, "GRAPH_PROPOSAL_INVALID"),
        ):
            with self.subTest(**overrides):
                with self.assertRaises(context_graph.GraphError) as raised:
                    context_graph.propose(graph, self.record(**overrides))
                self.assertEqual(code, raised.exception.code)


class CommandTests(GraphTestCase):
    def run_cli(self, *arguments: str) -> tuple[int, dict[str, object]]:
        result = subprocess.run(
            [sys.executable, str(RUNTIME / "context_graph.py"), *arguments, "--json"],
            text=True,
            capture_output=True,
            timeout=60,
        )
        return result.returncode, json.loads(result.stdout)

    def test_validate_reports_counts_and_exits_zero_for_a_valid_graph(self) -> None:
        self.seed()

        code, report = self.run_cli("validate", "--repo", str(self.repo), "--root", self.root)

        self.assertEqual(0, code)
        self.assertTrue(report["valid"])
        self.assertEqual(6, report["nodes"])
        self.assertEqual(2, report["kinds"]["MODULE"])

    def test_validate_exits_two_with_findings(self) -> None:
        self.write("a.md", note("alpha", "MODULE", depends_on=["ghost"]))

        code, report = self.run_cli("validate", "--repo", str(self.repo), "--root", self.root)

        self.assertEqual(2, code)
        self.assertFalse(report["valid"])
        self.assertEqual(["GRAPH_EDGE_DANGLING"], [finding["code"] for finding in report["findings"]])

    def test_query_returns_the_connected_context_for_a_path(self) -> None:
        self.seed()

        code, report = self.run_cli(
            "query", "--repo", str(self.repo), "--root", self.root,
            "--path", "src/payments/checkout", "--depth", "2",
        )

        self.assertEqual(0, code)
        self.assertEqual(["payments"], report["selected"])
        self.assertEqual(["checkout-doc"], [record["id"] for record in report["context"]["DOC"]])

    def test_query_exits_two_when_a_selector_does_not_resolve(self) -> None:
        self.seed()

        code, report = self.run_cli("query", "--repo", str(self.repo), "--root", self.root, "--node", "ghost")

        self.assertEqual(2, code)
        self.assertEqual(["ghost"], [item["selector"] for item in report["unresolved"]])

    def test_propose_prints_the_note_and_never_writes_it(self) -> None:
        self.seed()
        record = self.repo / "record.json"
        record.write_text(json.dumps({
            "note": "decisions/idempotent-refunds.md",
            "operation": "CREATE",
            "node": "idempotent-refunds",
            "kind": "DECISION",
            "reason": "A duplicated webhook must not refund twice",
            "date": "2026-10-02",
            "body": "Refund handlers key on the provider event id.",
        }), encoding="utf-8")

        code, report = self.run_cli(
            "propose", "--repo", str(self.repo), "--root", self.root, "--record", str(record)
        )

        self.assertEqual(0, code)
        self.assertEqual("PROPOSED", report["status"])
        self.assertFalse(report["written"])
        self.assertFalse((self.repo / self.root / "decisions" / "idempotent-refunds.md").exists())

    def test_an_unreadable_graph_root_exits_two_with_a_stable_code(self) -> None:
        code, report = self.run_cli("validate", "--repo", str(self.repo), "--root", "absent")

        self.assertEqual(2, code)
        self.assertEqual(["GRAPH_SOURCE_UNAVAILABLE"], [finding["code"] for finding in report["findings"]])

    def test_a_graph_root_outside_the_repository_exits_two_without_reading_it(self) -> None:
        outside = self.repo.parent / "outside-graph"
        outside.mkdir(exist_ok=True)
        (outside / "ghost.md").write_text(note("ghost", "MODULE"), encoding="utf-8")

        for root in (str(outside), "../outside-graph"):
            with self.subTest(root=root):
                code, report = self.run_cli("validate", "--repo", str(self.repo), "--root", root)
                self.assertEqual(2, code)
                self.assertEqual(["GRAPH_ROOT_UNSAFE"], [finding["code"] for finding in report["findings"]])
                self.assertNotIn("ghost", json.dumps(report))

    def test_an_absent_obsidian_binding_exits_two_without_a_traceback(self) -> None:
        code, report = self.run_cli("validate", "--repo", str(self.repo))

        self.assertEqual(2, code)
        self.assertEqual(["GRAPH_SOURCE_UNAVAILABLE"], [finding["code"] for finding in report["findings"]])


if __name__ == "__main__":
    unittest.main()
