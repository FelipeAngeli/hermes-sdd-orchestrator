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
        for pattern in ("/etc/passwd", "../outside/**", "**", "*", "src/../etc"):
            with self.subTest(pattern=pattern):
                self.write("m.md", note("m", "MODULE", code_paths=[pattern]))
                findings = self.graph()["findings"]
                self.assertEqual(["GRAPH_CODE_PATH_UNSAFE"], [finding["code"] for finding in findings])

    def test_a_dependency_cycle_is_reported_once_with_its_route(self) -> None:
        self.write("a.md", note("alpha", "MODULE", depends_on=["beta"]))
        self.write("b.md", note("beta", "MODULE", depends_on=["gamma"]))
        self.write("c.md", note("gamma", "MODULE", depends_on=["alpha"]))

        findings = [finding for finding in self.graph()["findings"] if finding["code"] == "GRAPH_DEPENDENCY_CYCLE"]

        self.assertEqual(1, len(findings))
        self.assertEqual("alpha -> beta -> gamma -> alpha", findings[0]["detail"])

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

    def test_an_absent_obsidian_binding_exits_two_without_a_traceback(self) -> None:
        code, report = self.run_cli("validate", "--repo", str(self.repo))

        self.assertEqual(2, code)
        self.assertEqual(["GRAPH_SOURCE_UNAVAILABLE"], [finding["code"] for finding in report["findings"]])


if __name__ == "__main__":
    unittest.main()
