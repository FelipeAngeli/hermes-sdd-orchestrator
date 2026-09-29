"""Documentation must describe the orchestration that actually ships.

These tests make stale documentation a test failure instead of a review
comment. `docs/doc-map.json` maps every orchestration file to the component
page that documents it; the tests check that the map is complete, that every
page names what it covers, that every page is linked into the documentation
graph, and that CLI commands, flags and enums extracted from the code appear
on the page that owns them.
"""
from __future__ import annotations

import ast
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
LINK = re.compile(r"\]\(([^)\s]+)\)")
EXPLICIT_ID = re.compile(r'<a\s+(?:name|id)=["\']([^"\']+)["\']\s*></a>', re.I)


def markdown_links(text: str) -> list[tuple[str, str]]:
    """Return (relative path, anchor) for non-external Markdown links."""
    result: list[tuple[str, str]] = []
    for target in LINK.findall(text):
        if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
            continue
        path, _, anchor = target.partition("#")
        result.append((path, anchor))
    return result


def markdown_anchors(text: str) -> set[str]:
    """Approximate GitHub heading anchors plus explicit HTML ids."""
    anchors = set(EXPLICIT_ID.findall(text))
    seen: dict[str, int] = {}
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, re.M):
        plain = re.sub(r"<[^>]+>|[`*~]", "", heading).strip().lower()
        slug = re.sub(r"[^\w\- ]", "", plain, flags=re.UNICODE).replace(" ", "-")
        count = seen.get(slug, 0)
        anchors.add(slug if count == 0 else f"{slug}-{count}")
        seen[slug] = count + 1
    return anchors


def public_help(path: Path, *arguments: str) -> str:
    """Return the public help text exposed by a shipped command."""
    result = subprocess.run(
        [sys.executable, str(path), *arguments, "--help"],
        text=True,
        capture_output=True,
        timeout=30,
    )
    if result.returncode:
        raise AssertionError(f"{path} {' '.join(arguments)} --help failed:\n{result.stdout}{result.stderr}")
    return result.stdout


def public_options(help_text: str) -> set[str]:
    """Read long options from observable argparse help, not Python syntax."""
    return set(re.findall(r"(?<!\w)(--[a-z][a-z0-9-]*)", help_text)) - {"--help"}


def argparse_clis() -> list[Path]:
    """Discover every shipped Python module that constructs an argparse CLI."""
    candidates = {
        path
        for directory in (RUNTIME, SKILL_ROOT / "scripts", ROOT / "tools")
        for path in directory.rglob("*.py")
    }
    result: list[Path] = []
    for path in sorted(candidates):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            isinstance(node, ast.Call)
            and (
                isinstance(node.func, ast.Attribute) and node.func.attr == "ArgumentParser"
                or isinstance(node.func, ast.Name) and node.func.id == "ArgumentParser"
            )
            for node in ast.walk(tree)
        ):
            result.append(path)
    return result


def public_commands(help_text: str) -> set[str]:
    """Read argparse positional choices, including subcommands, from help."""
    section = re.search(
        r"^positional arguments:\s*\n(?P<body>.*?)(?=^[a-z][^\n]*:\s*$|\Z)",
        help_text,
        re.M | re.S,
    )
    if section is None:
        return set()
    choices = re.findall(r"^\s+\{([a-z0-9,-]+)\}(?:\s+.*)?$", section.group("body"), re.M)
    return {command for group in choices for command in group.split(",")}


def public_help_tree(path: Path, *, max_depth: int = 8) -> dict[tuple[str, ...], str]:
    """Traverse distinct nested command help without following cycles forever."""
    root_help = public_help(path)
    discovered: dict[tuple[str, ...], str] = {(): root_help}
    frontier: list[tuple[str, ...]] = [()]
    expanded_help = {root_help}
    while frontier:
        arguments = frontier.pop(0)
        parent_help = discovered[arguments]
        if len(arguments) >= max_depth:
            continue
        for command in sorted(public_commands(parent_help)):
            child_arguments = (*arguments, command)
            if child_arguments in discovered:
                continue
            child_help = public_help(path, *child_arguments)
            discovered[child_arguments] = child_help
            if child_help != parent_help and child_help not in expanded_help:
                expanded_help.add(child_help)
                frontier.append(child_arguments)
    return discovered


def published_vocabulary(path: Path) -> set[str]:
    """Collect public domain terms from conventional module-level constants."""
    terms: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            names = [target.id for target in statement.targets if isinstance(target, ast.Name)]
            value = statement.value
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            names = [statement.target.id]
            value = statement.value
        else:
            continue
        if value is None:
            continue
        for name in (candidate for candidate in names if candidate.isupper() and not candidate.startswith("_")):
            if isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value == name:
                terms.add(value.value)
                continue
            literal_node = value
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id in {"frozenset", "list", "set", "tuple"}
                and len(value.args) == 1
                and not value.keywords
            ):
                literal_node = value.args[0]
            try:
                literal = ast.literal_eval(literal_node)
            except (ValueError, TypeError):
                literal = None
            if (
                isinstance(literal, (list, tuple, set, frozenset))
                and literal
                and all(isinstance(term, str) and re.fullmatch(r"[A-Z][A-Z0-9_]*", term) for term in literal)
            ):
                terms.update(literal)
                continue
            if not isinstance(value, (ast.List, ast.Tuple, ast.Set)) or not value.elts:
                continue
            identifiers: list[str] = []
            for row in value.elts:
                if not isinstance(row, (ast.List, ast.Tuple)) or not row.elts:
                    identifiers = []
                    break
                identifier = row.elts[0]
                if not (
                    isinstance(identifier, ast.Constant)
                    and isinstance(identifier.value, str)
                    and re.fullmatch(r"[a-z][a-z0-9-]*", identifier.value)
                ):
                    identifiers = []
                    break
                identifiers.append(identifier.value)
            terms.update(identifiers)
    return terms


def load_map() -> dict[str, list[str]]:
    return json.loads(DOC_MAP.read_text(encoding="utf-8"))["docs"]


def tracked_sources() -> list[str]:
    """Every file the documentation must cover, repo-relative."""
    roots = [SKILL_ROOT, ROOT / "tests", ROOT / "tools", ROOT / ".github" / "workflows", ROOT / ".githooks"]
    files = ["hermes-pack.yaml"] if (ROOT / "hermes-pack.yaml").is_file() else []
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

    def test_relative_links_and_anchors_resolve(self) -> None:
        for path in [*self.all_pages(), ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / "AGENTS.md"]:
            for target, anchor in markdown_links(path.read_text(encoding="utf-8")):
                destination = (path.parent / target).resolve() if target else path.resolve()
                with self.subTest(page=path.relative_to(ROOT).as_posix(), link=target, anchor=anchor):
                    self.assertTrue(destination.exists())
                    if anchor and destination.suffix == ".md":
                        self.assertIn(anchor, markdown_anchors(destination.read_text(encoding="utf-8")))

    def test_ci_checks_out_the_real_pull_request_head(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        checkout_step = re.search(
            r"- uses: actions/checkout@[^\n]+\n(?P<body>(?:\s{8,}.*\n)+)",
            workflow,
        )
        self.assertIsNotNone(checkout_step, "CI must contain an actions/checkout step")
        assert checkout_step is not None
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            checkout_step.group("body"),
        )

    def test_every_page_is_reachable_from_the_index(self) -> None:
        index = DOCS / "README.md"
        seen, frontier = {index.resolve()}, [index]
        while frontier:
            current = frontier.pop()
            for target, _ in markdown_links(current.read_text(encoding="utf-8")):
                if not target:
                    continue
                resolved = (current.parent / target).resolve()
                if resolved.suffix == ".md" and resolved.is_relative_to(DOCS.resolve()) and resolved not in seen:
                    seen.add(resolved)
                    frontier.append(resolved)
        for path in self.all_pages():
            with self.subTest(page=path.relative_to(ROOT).as_posix()):
                self.assertIn(path.resolve(), seen)

    def test_every_component_page_links_back_to_the_index_and_to_a_sibling(self) -> None:
        for path in sorted((DOCS / "components").glob("*.md")):
            links = {(path.parent / t).resolve() for t, _ in markdown_links(path.read_text(encoding="utf-8")) if t}
            siblings = {p.resolve() for p in (DOCS / "components").glob("*.md")} - {path.resolve()}
            with self.subTest(page=path.name):
                self.assertIn((DOCS / "README.md").resolve(), links)
                self.assertTrue(links & siblings, "no link to another component page")

    def test_readme_and_agents_point_to_the_documentation_index(self) -> None:
        for name in ("README.md", "AGENTS.md", "CONTRIBUTING.md"):
            with self.subTest(file=name):
                self.assertIn("docs/README.md", (ROOT / name).read_text(encoding="utf-8"))


class PublicDocumentationContractTests(unittest.TestCase):
    """Published commands and domain terms must be discoverable in their docs."""

    def test_public_command_discovery_handles_descriptions_and_nested_help(self) -> None:
        described = "positional arguments:\n  {alpha,beta}  command to execute\n"
        self.assertEqual({"alpha", "beta"}, public_commands(described))

        with tempfile.TemporaryDirectory(prefix="nested-help-") as temp:
            command = Path(temp) / "command.py"
            command.write_text(
                "import argparse\n"
                "parser = argparse.ArgumentParser()\n"
                "commands = parser.add_subparsers(dest='command')\n"
                "parent = commands.add_parser('parent', help='parent command')\n"
                "children = parent.add_subparsers(dest='child')\n"
                "child = children.add_parser('child', help='child command')\n"
                "child.add_argument('--deep-option')\n"
                "parser.parse_args()\n",
                encoding="utf-8",
            )

            help_tree = public_help_tree(command)

            self.assertIn(("parent", "child"), help_tree)
            self.assertIn("--deep-option", public_options("\n".join(help_tree.values())))
            self.assertNotIn(("parent", "child"), public_help_tree(command, max_depth=1))

    def test_runtime_vocabulary_conventions_exclude_path_and_config_constants(self) -> None:
        with tempfile.TemporaryDirectory(prefix="runtime-vocabulary-") as temp:
            module = Path(temp) / "module.py"
            module.write_text(
                "READY = 'READY'\n"
                "TERMS = {'ONE', 'TWO'}\n"
                "FROZEN = frozenset({'THREE'})\n"
                "ECOSYSTEMS = (('node', ('package.json',), detector), ('python', ('pyproject.toml',), detector))\n"
                "CONFIG_FILES = ('STATE.md', 'GATES.md')\n"
                "ROOT_PATH = '.hermes/orchestration'\n",
                encoding="utf-8",
            )

            self.assertEqual(
                {"READY", "ONE", "TWO", "THREE", "node", "python"},
                published_vocabulary(module),
            )

    def test_every_public_cli_option_and_subcommand_is_documented(self) -> None:
        for path in argparse_clis():
            help_tree = public_help_tree(path)
            commands = {command for arguments in help_tree for command in arguments}
            text = owning_page(path.relative_to(ROOT).as_posix())

            for option in sorted(public_options("\n".join(help_tree.values()))):
                with self.subTest(tool=path.name, option=option):
                    self.assertIn(option, text)
            for command in sorted(commands):
                with self.subTest(tool=path.name, command=command):
                    self.assertIn(f"`{command}`", text)

    def test_published_runtime_vocabulary_is_documented(self) -> None:
        for path in sorted(RUNTIME.rglob("*.py")):
            text = owning_page(path.relative_to(ROOT).as_posix())
            for term in sorted(published_vocabulary(path)):
                with self.subTest(tool=path.name, term=term):
                    self.assertRegex(text, rf"(?<![A-Za-z0-9_]){re.escape(term)}(?![A-Za-z0-9_])")

    def frontmatter(self, path: Path) -> dict[str, str]:
        block = path.read_text(encoding="utf-8").split("---")[1]
        return dict(line.split(": ", 1) for line in block.strip().splitlines() if ": " in line)

    def table_record(self, text: str, name: str) -> dict[str, str]:
        lines = [line for line in text.splitlines() if line.startswith("|")]
        for index, header_line in enumerate(lines[:-1]):
            headers = [cell.strip() for cell in header_line.strip("|").split("|")]
            if index + 1 >= len(lines) or not all(set(cell.strip()) <= {"-", ":"} for cell in lines[index + 1].strip("|").split("|")):
                continue
            for row in lines[index + 2:]:
                cells = [cell.strip() for cell in row.strip("|").split("|")]
                if len(cells) != len(headers):
                    break
                record = dict(zip(headers, cells))
                if f"`{name}`" in cells:
                    return record
        self.fail(f"expected one documentation record for {name}")

    def test_sub_agent_catalogue_publishes_each_briefs_contract(self) -> None:
        text = owning_page((ORCHESTRATION / "sub-agents" / "investigator.md").relative_to(ROOT).as_posix())
        for path in sorted((ORCHESTRATION / "sub-agents").glob("*.md")):
            meta = self.frontmatter(path)
            stages = meta["allowed_stages"].strip("[]").replace(" ", "").split(",")
            with self.subTest(sub_agent=path.stem):
                record = self.table_record(text, f"sub-agents/{path.name}")
                self.assertEqual(f"`{meta['role']}`", record["Role"])
                self.assertEqual(stages, re.findall(r"\b[A-Z]+\b", record["Stages"]))
                self.assertEqual(f"`{Path(meta['result_schema']).name}`", record["Result schema"])

    def test_stage_agent_catalogue_publishes_each_briefs_contract(self) -> None:
        text = owning_page((ORCHESTRATION / "agents" / "plan.md").relative_to(ROOT).as_posix())
        for path in sorted((ORCHESTRATION / "agents").glob("*.md")):
            meta = self.frontmatter(path)
            with self.subTest(agent=path.name):
                record = self.table_record(text, f"agents/{path.name}")
                self.assertEqual(f"`{meta['stage']}`", record["Stage"])
                self.assertEqual(f"`{Path(meta['result_schema']).name}`", record["Result schema"])

    def test_hook_catalogue_publishes_each_installed_hook_file_exactly_once(self) -> None:
        hooks = ORCHESTRATION / "hooks"
        text = owning_page((hooks / "enforce-slice-scope.py").relative_to(ROOT).as_posix())
        installed = {f"hooks/{path.name}" for path in hooks.iterdir() if path.is_file()}
        documented = {
            match.group(1)
            for line in text.splitlines()
            if (match := re.match(r"^\| `(?P<path>hooks/[^`]+)` \|", line))
        }
        self.assertEqual(installed, documented)
        for name in sorted(installed):
            with self.subTest(hook=name):
                self.table_record(text, name)

    def test_project_skill_catalogue_publishes_each_installed_file_exactly_once(self) -> None:
        project_skills = ORCHESTRATION.parent / "skills"
        first = project_skills / "sdd-backend-engineering" / "SKILL.md"
        text = owning_page(first.relative_to(ROOT).as_posix())
        installed = {
            path.relative_to(SKILL_ROOT).as_posix()
            for path in project_skills.rglob("*") if path.is_file()
        }
        documented = {
            match.group(1)
            for line in text.splitlines()
            if (match := re.match(r"^\| `(?P<path>templates/\.hermes/skills/[^`]+)` \|", line))
        }
        self.assertEqual(installed, documented)
        for name in sorted(installed):
            with self.subTest(skill_file=name):
                self.table_record(text, name)


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
            "docs/components/obsidian.md": ["hermes-pack.yaml"],
        }}))
        self.write("docs/components/runtime.md", "runtime\n")
        self.write("docs/components/testing.md", "tests\n")
        self.write("docs/components/obsidian.md", "pack\n")
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

    def check_default(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CHECKER), "--repo", str(self.repo), *args],
            text=True, capture_output=True, timeout=30,
        )

    def test_default_sources_include_repository_plugin_pack(self) -> None:
        self.write("hermes-pack.yaml", "name: test-pack\n")
        self.git("add", "-A")

        result = self.check_default("--staged")

        self.assertEqual(1, result.returncode)
        self.assertIn("docs/components/obsidian.md", result.stdout)
        self.assertIn("CHANGELOG.md", result.stdout)

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

    def test_a_waiver_on_one_commit_does_not_cover_other_commits_in_the_range(self) -> None:
        """A release commit carries Docs-Impact: none; it must not launder the branch."""
        base = self.git("rev-parse", "HEAD").strip()
        self.write("skills/o/runtime/tool.py", "x = 4\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "feat: undocumented change")
        self.write("CHANGELOG.md", "# Changelog\n## 1.0.0\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "chore(release): v1.0.0", "-m", "Docs-Impact: none - version bump only")
        result = self.check("--base", base)
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("docs/components/runtime.md", result.stdout)

    def test_a_waived_commit_exempts_only_its_own_files(self) -> None:
        base = self.git("rev-parse", "HEAD").strip()
        self.write("skills/o/runtime/tool.py", "x = 5  # typo\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "fix: typo", "-m", "Docs-Impact: none - comment typo")
        self.assertEqual(0, self.check("--base", base).returncode)

    def test_waiver_does_not_launder_a_merge_resolution_change(self) -> None:
        base = self.git("rev-parse", "HEAD").strip()
        self.git("switch", "-q", "-c", "side")
        self.write("side.txt", "side\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "side")
        self.git("switch", "-q", "main")
        self.write("main.txt", "main\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "main")
        self.git("merge", "--no-ff", "--no-commit", "side")
        self.write("skills/o/runtime/tool.py", "x = 7  # merge-only change\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "merge side with resolution")
        self.write("skills/o/runtime/tool.py", "x = 8  # waived metadata\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "chore: metadata", "-m", "Docs-Impact: none - metadata only")
        result = self.check("--base", base)
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("docs/components/runtime.md", result.stdout)

    def test_waiver_covers_both_paths_of_its_own_rename(self) -> None:
        base = self.git("rev-parse", "HEAD").strip()
        self.git("mv", "skills/o/runtime/tool.py", "skills/o/tests/test_moved.py")
        self.git("commit", "-qm", "chore: move fixture", "-m", "Docs-Impact: none - test fixture move only")
        result = self.check("--base", base)
        self.assertEqual(0, result.returncode, result.stdout)

    def test_renaming_a_source_into_a_test_path_is_not_exempt(self) -> None:
        self.git("mv", "skills/o/runtime/tool.py", "skills/o/tests/test_moved.py")
        result = self.check("--staged")
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("skills/o/runtime/tool.py", result.stdout)

    def test_initial_introduction_of_doc_map_has_no_old_map(self) -> None:
        self.git("rm", "docs/doc-map.json")
        self.git("commit", "-qm", "remove preexisting map")
        base = self.git("rev-parse", "HEAD").strip()
        self.write("docs/doc-map.json", json.dumps({"docs": {
            "docs/components/runtime.md": ["skills/o/runtime/*.py"],
            "docs/components/testing.md": ["skills/o/tests/*.py"],
        }}))
        self.write("skills/o/runtime/tool.py", "x = 6\n")
        self.write("docs/components/runtime.md", "runtime documented\n")
        self.write("CHANGELOG.md", "# Changelog\n- introduce docs map\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "docs: introduce map")
        result = self.check("--base", base)
        self.assertEqual(0, result.returncode, result.stdout)

    def test_documented_deletion_uses_the_owner_from_the_base_map(self) -> None:
        self.git("rm", "skills/o/runtime/tool.py")
        self.write("docs/doc-map.json", json.dumps({"docs": {
            "docs/components/testing.md": ["skills/o/tests/*.py"],
        }}))
        self.write("docs/components/runtime.md", "runtime removed\n")
        self.write("CHANGELOG.md", "# Changelog\n- remove runtime tool\n")
        self.git("add", "-A")
        result = self.check("--staged")
        self.assertEqual(0, result.returncode, result.stdout)


if __name__ == "__main__":
    unittest.main()
