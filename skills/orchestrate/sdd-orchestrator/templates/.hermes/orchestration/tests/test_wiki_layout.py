"""Behavior of the LLM Wiki layout for an Obsidian project container."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))

import wiki_layout  # noqa: E402

SCRIPT = RUNTIME / "wiki_layout.py"
TODAY = "2026-10-06"


def tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class WikiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="sdd-wiki-")
        self.addCleanup(temp.cleanup)
        self.vault = Path(temp.name).resolve() / "vault"
        (self.vault / ".obsidian").mkdir(parents=True)
        self.container = self.vault / "Projects" / "App"
        self.container.mkdir(parents=True)

    def write(self, relative: str, content: str | bytes = "x\n") -> Path:
        path = self.container / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
        return path


class SkeletonTests(WikiTestCase):
    def test_init_creates_the_llm_wiki_skeleton(self) -> None:
        created = wiki_layout.init(self.container, project="App", today=TODAY)

        for name in ("SCHEMA.md", "index.md", "log.md"):
            self.assertTrue((self.container / name).is_file(), name)
        for directory in wiki_layout.DIRECTORIES:
            self.assertTrue((self.container / directory).is_dir(), directory)
        self.assertIn("SCHEMA.md", created)
        schema = (self.container / "SCHEMA.md").read_text(encoding="utf-8")
        self.assertIn(f"layout_version: {wiki_layout.LAYOUT_VERSION}", schema)
        self.assertIn("App", schema)
        log = (self.container / "log.md").read_text(encoding="utf-8")
        self.assertIn(f"## [{TODAY}] create | Wiki initialized", log)
        self.assertEqual([], wiki_layout.check(self.container)["findings"])

    def test_init_never_overwrites_existing_files(self) -> None:
        wiki_layout.init(self.container, project="App", today=TODAY)
        (self.container / "index.md").write_text("# My index\n", encoding="utf-8")
        before = tree(self.container)

        created = wiki_layout.init(self.container, project="App", today="2027-01-01")

        self.assertEqual([], created)
        self.assertEqual(before, tree(self.container))

    def test_skeleton_files_match_what_init_writes(self) -> None:
        skeleton = wiki_layout.skeleton_files(project="App", today=TODAY)
        wiki_layout.init(self.container, project="App", today=TODAY)
        for relative, content in skeleton.items():
            self.assertEqual(content, (self.container / relative).read_bytes(), relative)

    def test_a_container_outside_a_vault_is_refused(self) -> None:
        outside = self.vault.parent / "repo"
        outside.mkdir()
        with self.assertRaises(wiki_layout.WikiError) as raised:
            wiki_layout.init(outside, project="App", today=TODAY)
        self.assertEqual("WIKI_VAULT_REQUIRED", raised.exception.code)
        self.assertEqual([], list(outside.iterdir()))


class ClassificationTests(unittest.TestCase):
    def test_legacy_project_folders_map_onto_the_wiki_layers(self) -> None:
        cases = {
            "sessions/2026-05-07-1740-setup.md": "raw/transcripts/2026-05-07-1740-setup.md",
            "_Meetings/kickoff.md": "raw/transcripts/meetings/kickoff.md",
            "decisions/2026-05-07 - Adotar Clean Architecture.md": "concepts/2026-05-07 - Adotar Clean Architecture.md",
            "boards/Tarefas.base": "queries/boards/Tarefas.base",
            "_Discovery/2026-06-15-followup.md": "raw/articles/discovery/2026-06-15-followup.md",
            "_References/Docs do repositório.md": "raw/articles/references/Docs do repositório.md",
            "_Reviews/round-1.md": "raw/articles/reviews/round-1.md",
            "app-348-resiliencia/01_task.md": "raw/articles/app-348-resiliencia/01_task.md",
            "app-348-resiliencia/run.yaml": "raw/articles/app-348-resiliencia/run.yaml",
            "Meu segundo cérebro assets/a.png": "raw/assets/Meu segundo cérebro assets/a.png",
            "README.md": "raw/articles/README.md",
            "_Overview.md": "raw/articles/_Overview.md",
            "spec.pdf": "raw/papers/spec.pdf",
            "diagram.png": "raw/assets/diagram.png",
            "Sem título.base": "queries/Sem título.base",
            "board.canvas": "raw/articles/board.canvas",
            "skills-lock.json": ".hermes/skills-lock.json",
            "Decisões/README.md": "raw/articles/decisões/README.md",
            "decisions/_INDEX.md": "raw/articles/decisions/_INDEX.md",
            "boards/README.md": "raw/articles/boards/README.md",
            "sessions/README.md": "raw/transcripts/README.md",
        }
        for source, destination in cases.items():
            with self.subTest(source=source):
                self.assertEqual(destination, wiki_layout.classify(source))

    def test_wiki_files_and_hidden_state_stay_in_place(self) -> None:
        for source in (
            "SCHEMA.md", "index.md", "log.md", "log-2025.md", "raw/articles/a.md", "entities/x.md",
            "concepts/y.md", "comparisons/z.md", "queries/q.md", "_archive/old.md", "_meta/topic-map.md",
            ".hermes/orchestration/STATE.md", ".hermes.md", ".hermes-runtime/x/STATE.md", ".obsidian/app.json",
        ):
            with self.subTest(source=source):
                self.assertIsNone(wiki_layout.classify(source))


class MigrationTests(WikiTestCase):
    def legacy(self) -> dict[str, bytes]:
        files = {
            "README.md": "# App\n",
            "sessions/2026-05-07-setup.md": "# Session\n",
            "decisions/2026-05-07 - Use Bloc.md": "# Use Bloc\n\nBecause replay.\n",
            "boards/Tarefas.base": "filters: []\n",
            "app-1-feature/prd.md": "# PRD\n",
            "app-1-feature/tasks.md": "# Tasks\n",
            ".hermes-runtime/wt-1/STATE.md": "state\n",
        }
        for relative, content in files.items():
            self.write(relative, content)
        return {relative: content.encode("utf-8") for relative, content in files.items()}

    def test_plan_is_a_dry_run(self) -> None:
        self.legacy()
        before = tree(self.container)

        plan = wiki_layout.plan(self.container)

        self.assertEqual(before, tree(self.container))
        moves = {move["source"]: move["destination"] for move in plan["moves"]}
        self.assertEqual("raw/transcripts/2026-05-07-setup.md", moves["sessions/2026-05-07-setup.md"])
        self.assertNotIn(".hermes-runtime/wt-1/STATE.md", moves)
        self.assertEqual([], plan["conflicts"])

    def test_migrate_moves_every_byte_and_records_the_action(self) -> None:
        original = self.legacy()

        report = wiki_layout.migrate(self.container, project="App", today=TODAY)

        self.assertEqual("APPLIED", report["status"])
        for source, content in original.items():
            destination = wiki_layout.classify(source) or source
            self.assertEqual(content, (self.container / destination).read_bytes(), destination)
            if destination != source:
                self.assertFalse((self.container / source).exists(), source)
        for emptied in ("sessions", "decisions", "boards", "app-1-feature"):
            self.assertFalse((self.container / emptied).exists(), emptied)
        index = (self.container / "index.md").read_text(encoding="utf-8")
        self.assertIn("[[2026-05-07 - Use Bloc]]", index)
        self.assertIn("## Concepts", index)
        log = (self.container / "log.md").read_text(encoding="utf-8")
        self.assertIn(f"## [{TODAY}] migrate | Legacy project folders moved into the LLM Wiki layout", log)
        self.assertIn("sessions/2026-05-07-setup.md -> raw/transcripts/2026-05-07-setup.md", log)
        self.assertEqual([], wiki_layout.check(self.container)["findings"])

    def test_migrate_is_idempotent(self) -> None:
        self.legacy()
        wiki_layout.migrate(self.container, project="App", today=TODAY)
        before = tree(self.container)

        report = wiki_layout.migrate(self.container, project="App", today="2027-01-01")

        self.assertEqual("ALREADY_MIGRATED", report["status"])
        self.assertEqual(before, tree(self.container))

    def test_a_conflicting_destination_blocks_the_whole_migration(self) -> None:
        self.legacy()
        self.write("raw/transcripts/2026-05-07-setup.md", "# Different\n")
        before = tree(self.container)

        with self.assertRaises(wiki_layout.WikiError) as raised:
            wiki_layout.migrate(self.container, project="App", today=TODAY)

        self.assertEqual("WIKI_MIGRATION_CONFLICT", raised.exception.code)
        self.assertEqual(before, tree(self.container))

    def test_an_identical_destination_is_deduplicated(self) -> None:
        self.legacy()
        self.write("raw/transcripts/2026-05-07-setup.md", "# Session\n")

        report = wiki_layout.migrate(self.container, project="App", today=TODAY)

        self.assertEqual("APPLIED", report["status"])
        self.assertFalse((self.container / "sessions").exists())
        self.assertIn("sessions/2026-05-07-setup.md", report["deduplicated"])

    def test_symlinks_are_never_followed_or_moved(self) -> None:
        outside = self.vault.parent / "outside.md"
        outside.write_text("secret\n", encoding="utf-8")
        (self.container / "sessions").mkdir()
        (self.container / "sessions" / "link.md").symlink_to(outside)

        plan = wiki_layout.plan(self.container)

        self.assertIn("sessions/link.md", plan["skipped_symlinks"])
        self.assertNotIn("sessions/link.md", {move["source"] for move in plan["moves"]})
        wiki_layout.migrate(self.container, project="App", today=TODAY)
        self.assertTrue((self.container / "sessions" / "link.md").is_symlink())
        self.assertEqual("secret\n", outside.read_text(encoding="utf-8"))

    def test_a_failed_copy_leaves_the_source_in_place(self) -> None:
        self.legacy()
        real = wiki_layout._copy_verified
        calls = {"count": 0}

        def failing(container, source, destination):
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("disk full")
            return real(container, source, destination)

        wiki_layout._copy_verified = failing
        self.addCleanup(setattr, wiki_layout, "_copy_verified", real)
        with self.assertRaises(wiki_layout.WikiError) as raised:
            wiki_layout.migrate(self.container, project="App", today=TODAY)
        self.assertEqual("WIKI_MIGRATION_INCOMPLETE", raised.exception.code)
        moved = sum(1 for path in self.container.rglob("*") if path.is_file())
        self.assertGreaterEqual(moved, 7)
        for source in ("decisions/2026-05-07 - Use Bloc.md", "app-1-feature/prd.md"):
            destination = wiki_layout.classify(source)
            self.assertTrue((self.container / source).exists() or (self.container / destination).exists())


class CheckTests(WikiTestCase):
    def test_check_reports_missing_skeleton_legacy_folders_and_unindexed_pages(self) -> None:
        wiki_layout.init(self.container, project="App", today=TODAY)
        (self.container / "log.md").unlink()
        self.write("sessions/a.md")
        self.write("concepts/orphan.md", "# Orphan\n")

        codes = {finding["code"] for finding in wiki_layout.check(self.container)["findings"]}

        self.assertEqual({"WIKI_SKELETON_MISSING", "WIKI_LEGACY_ENTRY", "WIKI_PAGE_NOT_INDEXED"}, codes)


class CliTests(WikiTestCase):
    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), *arguments, "--container", str(self.container), "--json"],
            text=True, capture_output=True, timeout=60,
        )

    def test_migrate_without_apply_is_a_dry_run(self) -> None:
        self.write("sessions/a.md")
        before = tree(self.container)

        result = self.run_cli("migrate", "--project", "App", "--date", TODAY)

        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual("READY", report["status"])
        self.assertEqual(before, tree(self.container))

    def test_migrate_apply_and_check(self) -> None:
        self.write("sessions/a.md")
        applied = self.run_cli("migrate", "--project", "App", "--date", TODAY, "--apply")
        self.assertEqual(0, applied.returncode, applied.stderr)
        self.assertEqual("APPLIED", json.loads(applied.stdout)["status"])
        checked = self.run_cli("check")
        self.assertEqual(0, checked.returncode, checked.stdout)

    def test_check_exits_two_with_findings(self) -> None:
        self.write("sessions/a.md")
        result = self.run_cli("check")
        self.assertEqual(2, result.returncode)
        self.assertTrue(json.loads(result.stdout)["findings"])


if __name__ == "__main__":
    unittest.main()
