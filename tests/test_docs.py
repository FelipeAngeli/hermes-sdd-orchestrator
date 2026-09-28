"""Documentation must describe the orchestration that actually ships.

These tests make stale documentation a test failure instead of a review
comment. `docs/doc-map.json` maps every orchestration file to the component
page that documents it; the tests check that the map is complete, that every
page names what it covers, that every page is linked into the documentation
graph, and that CLI commands, flags and enums extracted from the code appear
on the page that owns them.
"""
from __future__ import annotations

import fnmatch
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SKILL_ROOT = ROOT / "skills" / "orchestrate" / "sdd-orchestrator"
ORCHESTRATION = SKILL_ROOT / "templates" / ".hermes" / "orchestration"
RUNTIME = ORCHESTRATION / "runtime"
DOC_MAP = DOCS / "doc-map.json"
CHECKER = ROOT / "tools" / "check_docs_sync.py"
LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")


def load_map() -> dict[str, list[str]]:
    return json.loads(DOC_MAP.read_text(encoding="utf-8"))["docs"]


def tracked_sources() -> list[str]:
    """Every file the documentation must cover, repo-relative."""
    roots = [SKILL_ROOT, ROOT / "tests", ROOT / "tools", ROOT / ".github" / "workflows", ROOT / ".githooks"]
    files: list[str] = []
    for root in roots:
        files.extend(
            path.relative_to(ROOT).as_posix()
            for path in root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )
    return sorted(files)


def owners(relative: str, doc_map: dict[str, list[str]]) -> list[str]:
    return [doc for doc, globs in doc_map.items() if any(fnmatch.fnmatch(relative, pattern) for pattern in globs)]


def mention(relative: str) -> str:
    """The shortest unambiguous name a page must use for a covered file."""
    path = ROOT / relative
    for base in (ORCHESTRATION, SKILL_ROOT, ROOT):
        try:
            return path.relative_to(base).as_posix()
        except ValueError:
            continue
    return relative


def page(doc: str) -> str:
    return (ROOT / doc).read_text(encoding="utf-8")


def owning_page(relative: str) -> str:
    found = owners(relative, load_map())
    assert len(found) == 1, (relative, found)
    return page(found[0])


class DocumentationCoverageTests(unittest.TestCase):
    def test_every_orchestration_file_has_exactly_one_documentation_owner(self) -> None:
        doc_map = load_map()
        for relative in tracked_sources():
            with self.subTest(file=relative):
                self.assertEqual(1, len(owners(relative, doc_map)), f"owners: {owners(relative, doc_map)}")

    def test_every_mapped_page_exists_and_every_glob_matches_something(self) -> None:
        sources = tracked_sources()
        for doc, globs in load_map().items():
            with self.subTest(doc=doc):
                self.assertTrue((ROOT / doc).is_file())
                for pattern in globs:
                    self.assertTrue(any(fnmatch.fnmatch(s, pattern) for s in sources), f"dead glob {pattern}")

    def test_every_page_names_each_file_it_covers(self) -> None:
        doc_map = load_map()
        for relative in tracked_sources():
            (doc,) = owners(relative, doc_map)
            with self.subTest(file=relative, doc=doc):
                self.assertTrue(mention(relative) in page(doc), f"{doc} does not name {mention(relative)}")


class DocumentationGraphTests(unittest.TestCase):
    def all_pages(self) -> list[Path]:
        return sorted(DOCS.rglob("*.md"))

    def test_relative_links_resolve(self) -> None:
        for path in [*self.all_pages(), ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / "AGENTS.md"]:
            for target in LINK.findall(path.read_text(encoding="utf-8")):
                if re.match(r"^[a-z]+:", target):
                    continue
                with self.subTest(page=path.relative_to(ROOT).as_posix(), link=target):
                    self.assertTrue((path.parent / target).resolve().exists())

    def test_every_page_is_reachable_from_the_index(self) -> None:
        index = DOCS / "README.md"
        seen, frontier = {index.resolve()}, [index]
        while frontier:
            current = frontier.pop()
            for target in LINK.findall(current.read_text(encoding="utf-8")):
                resolved = (current.parent / target).resolve()
                if resolved.suffix == ".md" and resolved.is_relative_to(DOCS.resolve()) and resolved not in seen:
                    seen.add(resolved)
                    frontier.append(resolved)
        for path in self.all_pages():
            with self.subTest(page=path.relative_to(ROOT).as_posix()):
                self.assertIn(path.resolve(), seen)

    def test_every_component_page_links_back_to_the_index_and_to_a_sibling(self) -> None:
        for path in sorted((DOCS / "components").glob("*.md")):
            links = {(path.parent / t).resolve() for t in LINK.findall(path.read_text(encoding="utf-8"))}
            siblings = {p.resolve() for p in (DOCS / "components").glob("*.md")} - {path.resolve()}
            with self.subTest(page=path.name):
                self.assertIn((DOCS / "README.md").resolve(), links)
                self.assertTrue(links & siblings, "no link to another component page")

    def test_readme_and_agents_point_to_the_documentation_index(self) -> None:
        for name in ("README.md", "AGENTS.md", "CONTRIBUTING.md"):
            with self.subTest(file=name):
                self.assertIn("docs/README.md", (ROOT / name).read_text(encoding="utf-8"))


class DocumentationDriftTests(unittest.TestCase):
    """Names extracted from code must appear on the page that owns the code."""

    def cli_flags(self, source: str) -> set[str]:
        return set(re.findall(r'add_argument\(\s*"(--[a-z0-9-]+)"', source))

    def test_every_cli_flag_is_documented_on_its_page(self) -> None:
        sources = [*RUNTIME.glob("*.py"), SKILL_ROOT / "scripts" / "install_project.py", CHECKER]
        for path in sources:
            relative = path.relative_to(ROOT).as_posix()
            text = owning_page(relative)
            for flag in sorted(self.cli_flags(path.read_text(encoding="utf-8"))):
                with self.subTest(file=relative, flag=flag):
                    self.assertTrue(flag in text, f"{flag} undocumented")

    def test_every_subcommand_is_documented_with_its_tool(self) -> None:
        for path in RUNTIME.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            names = set(re.findall(r'add_parser\(\s*"([a-z-]+)"', source))
            for group in re.findall(r'for name in \(([^)]*)\):\s*\n\s*\w+ = commands\.add_parser', source):
                names |= set(re.findall(r'"([a-z-]+)"', group))
            choices = re.search(r'"command", choices=\(([^)]*)\)', source)
            if choices:
                names |= set(re.findall(r'"([a-z-]+)"', choices.group(1)))
            text = owning_page(path.relative_to(ROOT).as_posix())
            for name in sorted(names):
                with self.subTest(tool=path.name, command=name):
                    self.assertTrue(f"`{name}`" in text, f"`{name}` undocumented")

    def test_journal_statuses_and_driver_decisions_are_documented(self) -> None:
        sys.path.insert(0, str(RUNTIME))
        import action_journal
        import bounded_loop_driver
        import bounded_run_driver

        journal_page = owning_page((RUNTIME / "action_journal.py").relative_to(ROOT).as_posix())
        for status in sorted(action_journal.STATUSES):
            with self.subTest(status=status):
                self.assertTrue(f"`{status}`" in journal_page, status)
        loop_page = owning_page((RUNTIME / "bounded_run_driver.py").relative_to(ROOT).as_posix())
        decisions = {
            value for module in (bounded_run_driver, bounded_loop_driver)
            for name, value in vars(module).items()
            if name.isupper() and isinstance(value, str) and value == name
        }
        for decision in sorted(decisions):
            with self.subTest(decision=decision):
                self.assertTrue(f"`{decision}`" in loop_page, decision)

    def test_planner_actions_are_documented(self) -> None:
        sys.path.insert(0, str(RUNTIME))
        import bounded_run_planner as planner

        text = owning_page((RUNTIME / "bounded_run_planner.py").relative_to(ROOT).as_posix())
        for action in sorted(planner.AUTO_SAFE | planner.AUTO_WITH_BUDGET | planner.HUMAN_REQUIRED):
            with self.subTest(action=action):
                self.assertTrue(f"`{action}`" in text, action)

    def test_every_detected_ecosystem_is_documented(self) -> None:
        sys.path.insert(0, str(RUNTIME))
        import detect_stack

        text = owning_page((RUNTIME / "detect_stack.py").relative_to(ROOT).as_posix())
        for name, _, _ in detect_stack.ECOSYSTEMS:
            with self.subTest(ecosystem=name):
                self.assertTrue(f"`{name}`" in text, name)

    def frontmatter(self, path: Path) -> dict[str, str]:
        block = path.read_text(encoding="utf-8").split("---")[1]
        return dict(line.split(": ", 1) for line in block.strip().splitlines() if ": " in line)

    def table_row(self, text: str, name: str) -> str:
        rows = [line for line in text.splitlines() if line.startswith("|") and f"`{name}`" in line]
        self.assertEqual(1, len(rows), f"expected one table row for {name}")
        return rows[0]

    def test_sub_agent_table_matches_each_brief(self) -> None:
        text = owning_page((ORCHESTRATION / "sub-agents" / "investigator.md").relative_to(ROOT).as_posix())
        for path in sorted((ORCHESTRATION / "sub-agents").glob("*.md")):
            meta = self.frontmatter(path)
            stages = meta["allowed_stages"].strip("[]").replace(" ", "").split(",")
            with self.subTest(sub_agent=path.stem):
                row = self.table_row(text, f"sub-agents/{path.name}")
                self.assertIn(meta["role"], row)
                self.assertEqual(stages, re.findall(r"\b[A-Z]+\b", row.split("|")[3]))
                self.assertIn(Path(meta["result_schema"]).name, row)

    def test_stage_agent_table_matches_each_brief(self) -> None:
        text = owning_page((ORCHESTRATION / "agents" / "plan.md").relative_to(ROOT).as_posix())
        for path in sorted((ORCHESTRATION / "agents").glob("*.md")):
            meta = self.frontmatter(path)
            with self.subTest(agent=path.name):
                row = self.table_row(text, f"agents/{path.name}")
                self.assertIn(f"`{meta['stage']}`", row)
                self.assertIn(Path(meta["result_schema"]).name, row)


class DocsSyncCheckerTests(unittest.TestCase):
    """tools/check_docs_sync.py blocks orchestration changes without docs."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "T")
        self.write("docs/doc-map.json", json.dumps({"docs": {
            "docs/components/runtime.md": ["skills/o/runtime/*.py"],
            "docs/components/testing.md": ["skills/o/tests/*.py"],
        }}))
        self.write("docs/components/runtime.md", "runtime\n")
        self.write("docs/components/testing.md", "tests\n")
        self.write("CHANGELOG.md", "# Changelog\n")
        self.write("skills/o/runtime/tool.py", "x = 1\n")
        self.write("skills/o/tests/test_tool.py", "y = 1\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.repo), *args], text=True, capture_output=True, check=True).stdout

    def write(self, relative: str, text: str) -> None:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def check(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CHECKER), "--repo", str(self.repo), "--source-prefix", "skills/", *args],
            text=True, capture_output=True, timeout=30,
        )

    def test_orchestration_change_without_docs_is_rejected(self) -> None:
        self.write("skills/o/runtime/tool.py", "x = 2\n")
        self.git("add", "-A")
        result = self.check("--staged")
        self.assertEqual(1, result.returncode)
        self.assertIn("docs/components/runtime.md", result.stdout)
        self.assertIn("CHANGELOG.md", result.stdout)

    def test_orchestration_change_with_owning_page_and_changelog_passes(self) -> None:
        self.write("skills/o/runtime/tool.py", "x = 2\n")
        self.write("docs/components/runtime.md", "runtime v2\n")
        self.write("CHANGELOG.md", "# Changelog\n- tool\n")
        self.git("add", "-A")
        self.assertEqual(0, self.check("--staged").returncode)

    def test_updating_the_wrong_page_is_not_enough(self) -> None:
        self.write("skills/o/runtime/tool.py", "x = 2\n")
        self.write("docs/components/testing.md", "tests v2\n")
        self.write("CHANGELOG.md", "# Changelog\n- tool\n")
        self.git("add", "-A")
        result = self.check("--staged")
        self.assertEqual(1, result.returncode)
        self.assertIn("docs/components/runtime.md", result.stdout)

    def test_editing_an_existing_test_needs_no_documentation(self) -> None:
        self.write("skills/o/tests/test_tool.py", "y = 2\n")
        self.git("add", "-A")
        self.assertEqual(0, self.check("--staged").returncode)

    def test_explicit_docs_impact_none_trailer_waives_the_check(self) -> None:
        self.write("skills/o/runtime/tool.py", "x = 2  # typo fix\n")
        self.git("add", "-A")
        message = self.repo / "MSG"
        message.write_text("fix: typo\n\nDocs-Impact: none - comment typo only\n", encoding="utf-8")
        self.assertEqual(0, self.check("--staged", "--message-file", str(message)).returncode)
        message.write_text("fix: typo\n\nDocs-Impact: none\n", encoding="utf-8")
        self.assertEqual(1, self.check("--staged", "--message-file", str(message)).returncode, "a reason is required")

    def test_commit_range_mode_reads_trailers_from_the_range(self) -> None:
        base = self.git("rev-parse", "HEAD").strip()
        self.write("skills/o/runtime/tool.py", "x = 3\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "feat: change tool")
        self.assertEqual(1, self.check("--base", base).returncode)
        self.write("docs/components/runtime.md", "runtime v3\n")
        self.write("CHANGELOG.md", "# Changelog\n- v3\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "docs: runtime v3")
        self.assertEqual(0, self.check("--base", base).returncode)


if __name__ == "__main__":
    unittest.main()
