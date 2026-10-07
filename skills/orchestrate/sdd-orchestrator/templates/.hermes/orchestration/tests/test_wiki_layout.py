"""Behavior of the LLM Wiki layout for an Obsidian project container."""
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

import wiki_layout  # noqa: E402

SCRIPT = RUNTIME / "wiki_layout.py"
TODAY = "2026-10-06"


def tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


class WikiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="sdd-wiki-")
        self.addCleanup(temp.cleanup)
        self.base = Path(os.path.realpath(temp.name))
        self.vault = self.base / "vault"
        (self.vault / ".obsidian").mkdir(parents=True)
        self.container = self.vault / "Projects" / "App"
        self.container.mkdir(parents=True)

    def write(self, relative: str, content: str | bytes = "x\n") -> Path:
        path = self.container / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
        return path

    def initialized(self) -> None:
        wiki_layout.init(self.container, project="App", today=TODAY)

    def migrate(self, today: str = TODAY) -> dict:
        return wiki_layout.migrate(self.container, project="App", today=today)

    def assert_blocked(self, code: str) -> wiki_layout.WikiError:
        with self.assertRaises(wiki_layout.WikiError) as raised:
            self.migrate()
        self.assertEqual(code, raised.exception.code, raised.exception.detail)
        return raised.exception


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
        self.initialized()
        (self.container / "index.md").write_text("# My index\n", encoding="utf-8")
        before = tree(self.container)

        created = wiki_layout.init(self.container, project="App", today="2027-01-01")

        self.assertEqual([], created)
        self.assertEqual(before, tree(self.container))

    def test_skeleton_files_match_what_init_writes(self) -> None:
        skeleton = wiki_layout.skeleton_files(project="App", today=TODAY)
        self.initialized()
        for relative, content in skeleton.items():
            self.assertEqual(content, (self.container / relative).read_bytes(), relative)

    def test_init_refuses_a_symlinked_wiki_directory(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        (self.container / "raw").symlink_to(outside, target_is_directory=True)

        with self.assertRaises(wiki_layout.WikiError) as raised:
            self.initialized()

        self.assertEqual("WIKI_PATH_UNSAFE", raised.exception.code)
        self.assertEqual([], list(outside.iterdir()))


class ContainerTests(WikiTestCase):
    def assert_refused(self, container: Path, code: str) -> None:
        for call in (
            lambda: wiki_layout.init(container, project="App", today=TODAY),
            lambda: wiki_layout.plan(container),
            lambda: wiki_layout.migrate(container, project="App", today=TODAY),
        ):
            with self.assertRaises(wiki_layout.WikiError) as raised:
                call()
            self.assertEqual(code, raised.exception.code)

    def test_a_container_outside_a_vault_is_refused(self) -> None:
        outside = self.base / "repo"
        outside.mkdir()
        self.assert_refused(outside, "WIKI_VAULT_REQUIRED")
        self.assertEqual([], list(outside.iterdir()))

    def test_the_vault_root_is_refused(self) -> None:
        self.write("sessions/a.md")
        before = tree(self.vault)
        self.assert_refused(self.vault, "WIKI_CONTAINER_IS_VAULT")
        self.assertEqual(before, tree(self.vault))

    def test_a_folder_holding_other_project_containers_is_refused(self) -> None:
        (self.container / ".hermes").mkdir()
        (self.container / ".hermes" / "obsidian.json").write_text("{}", encoding="utf-8")
        self.write("sessions/a.md")
        self.assert_refused(self.vault / "Projects", "WIKI_CONTAINER_NESTED")

    def test_a_git_work_tree_is_refused(self) -> None:
        (self.container / ".git").mkdir()
        self.assert_refused(self.container, "WIKI_CONTAINER_IS_REPOSITORY")

    def test_a_planted_binding_outside_a_vault_is_not_a_vault(self) -> None:
        repo = self.base / "repo"
        (repo / ".hermes").mkdir(parents=True)
        (repo / ".hermes" / "obsidian.json").write_text("{}", encoding="utf-8")
        (repo / "src").mkdir()
        (repo / "src" / "main.py").write_text("print()\n", encoding="utf-8")
        self.assert_refused(repo, "WIKI_VAULT_REQUIRED")

    def test_a_symlinked_container_is_refused(self) -> None:
        link = self.vault / "Projects" / "Link"
        link.symlink_to(self.container, target_is_directory=True)
        self.assert_refused(link, "WIKI_CONTAINER_INVALID")

    def test_migrate_requires_an_initialized_container(self) -> None:
        self.write("sessions/a.md")
        before = tree(self.container)
        self.assert_blocked("WIKI_INIT_REQUIRED")
        self.assertEqual(before, tree(self.container))


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
            "decisions/sub/README.md": "raw/articles/decisions/sub/README.md",
            "boards/README.md": "raw/articles/boards/README.md",
            "sessions/README.md": "raw/transcripts/README.md",
            "decisions/foo.pdf": "raw/papers/decisions/foo.pdf",
            "sessions/img.png": "raw/assets/sessions/img.png",
            "decisions/notes.txt": "raw/articles/decisions/notes.txt",
        }
        for source, destination in cases.items():
            with self.subTest(source=source):
                self.assertEqual(destination, wiki_layout.classify(source))

    def test_wiki_files_and_hidden_entries_stay_in_place(self) -> None:
        for source in (
            "SCHEMA.md", "index.md", "log.md", "log-2025.md", "raw/articles/a.md", "entities/x.md",
            "concepts/y.md", "comparisons/z.md", "queries/q.md", "_archive/old.md", "_meta/topic-map.md",
            "Concepts/payments.md", "Raw/a.md",
            ".hermes/orchestration/STATE.md", ".hermes.md", ".hermes-runtime/x/STATE.md", ".obsidian/app.json",
            "decisions/.DS_Store", "Demand/.obsidian/x", "demand/.orca-sdd/state.json",
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
        self.initialized()
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

        report = self.migrate()

        self.assertEqual("APPLIED", report["status"])
        for source, content in original.items():
            destination = wiki_layout.classify(source) or source
            self.assertEqual(content, (self.container / destination).read_bytes(), destination)
            if destination != source:
                self.assertFalse((self.container / source).exists(), source)
        for emptied in ("sessions", "decisions", "boards", "app-1-feature"):
            self.assertFalse((self.container / emptied).exists(), emptied)
        index = (self.container / "index.md").read_text(encoding="utf-8")
        self.assertIn("[[concepts/2026-05-07 - Use Bloc|2026-05-07 - Use Bloc]]", index)
        self.assertIn("## Concepts", index)
        log = (self.container / "log.md").read_text(encoding="utf-8")
        self.assertIn(f"## [{TODAY}] migrate | Legacy project folders moved into the LLM Wiki layout", log)
        self.assertIn("sessions/2026-05-07-setup.md -> raw/transcripts/2026-05-07-setup.md", log)
        self.assertEqual([], wiki_layout.check(self.container)["findings"])

    def test_migrate_preserves_modification_times(self) -> None:
        self.legacy()
        source = self.container / "sessions/2026-05-07-setup.md"
        os.utime(source, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
        self.migrate()
        moved = self.container / "raw/transcripts/2026-05-07-setup.md"
        self.assertEqual(1_700_000_000_000_000_000, moved.stat().st_mtime_ns)

    def test_migrate_is_idempotent(self) -> None:
        self.legacy()
        self.migrate()
        before = tree(self.container)

        report = self.migrate("2027-01-01")

        self.assertEqual("ALREADY_MIGRATED", report["status"])
        self.assertEqual(before, tree(self.container))

    def test_a_conflicting_destination_blocks_the_whole_migration(self) -> None:
        self.legacy()
        self.write("raw/transcripts/2026-05-07-setup.md", "# Different\n")
        before = tree(self.container)
        self.assert_blocked("WIKI_MIGRATION_CONFLICT")
        self.assertEqual(before, tree(self.container))

    def test_an_identical_destination_is_deduplicated(self) -> None:
        self.legacy()
        self.write("raw/transcripts/2026-05-07-setup.md", "# Session\n")

        report = self.migrate()

        self.assertEqual("APPLIED", report["status"])
        self.assertFalse((self.container / "sessions").exists())
        self.assertIn("sessions/2026-05-07-setup.md", report["deduplicated"])
        self.assertEqual(b"# Session\n", (self.container / "raw/transcripts/2026-05-07-setup.md").read_bytes())

    def test_symlinks_are_never_followed_or_moved(self) -> None:
        outside = self.base / "outside.md"
        outside.write_text("secret\n", encoding="utf-8")
        self.initialized()
        (self.container / "sessions").mkdir()
        (self.container / "sessions" / "link.md").symlink_to(outside)

        plan = wiki_layout.plan(self.container)

        self.assertIn("sessions/link.md", plan["skipped_symlinks"])
        self.assertNotIn("sessions/link.md", {move["source"] for move in plan["moves"]})
        self.migrate()
        self.assertTrue((self.container / "sessions" / "link.md").is_symlink())
        self.assertEqual("secret\n", outside.read_text(encoding="utf-8"))

    def test_hidden_entries_inside_legacy_folders_stay_and_are_reported(self) -> None:
        self.initialized()
        self.write("demand-1/prd.md", "# PRD\n")
        self.write("demand-1/.orca-sdd/state.json", "{}\n")
        self.write("decisions/a.md", "# A\n")
        self.write("decisions/.DS_Store", b"\x00")

        plan = wiki_layout.plan(self.container)
        self.assertIn("demand-1/.orca-sdd/", plan["kept_hidden"])
        self.migrate()

        self.assertEqual(b"{}\n", (self.container / "demand-1/.orca-sdd/state.json").read_bytes())
        self.assertFalse((self.container / "decisions").exists())
        self.assertFalse((self.container / "concepts/.DS_Store").exists())

    def test_a_failed_copy_leaves_the_source_in_place(self) -> None:
        self.legacy()
        real = wiki_layout._move_verified
        calls = {"count": 0}

        def failing(root_fd, source, record):
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("disk full")
            return real(root_fd, source, record)

        wiki_layout._move_verified = failing
        self.addCleanup(setattr, wiki_layout, "_move_verified", real)
        self.assert_blocked("WIKI_MIGRATION_INCOMPLETE")
        for source in ("decisions/2026-05-07 - Use Bloc.md", "app-1-feature/prd.md"):
            destination = wiki_layout.classify(source)
            self.assertTrue((self.container / source).exists() or (self.container / destination).exists())
        self.assertIn("Legacy migration interrupted", (self.container / "log.md").read_text(encoding="utf-8"))


class MigrationSafetyTests(WikiTestCase):
    """Regression tests for every way a move could lose, leak or half-apply data."""

    def test_a_symlinked_destination_ancestor_pointing_at_the_source_never_deletes_it(self) -> None:
        self.initialized()
        self.write("sessions/a.md", "only copy\n")
        (self.container / "raw" / "transcripts").rmdir()
        (self.container / "raw" / "transcripts").symlink_to("../sessions", target_is_directory=True)

        error = self.assert_blocked("WIKI_MIGRATION_CONFLICT")

        self.assertIn("raw/transcripts is a symlink", error.detail)
        self.assertEqual(b"only copy\n", (self.container / "sessions/a.md").read_bytes())

    def test_a_destination_ancestor_that_is_a_file_blocks_before_any_move(self) -> None:
        self.initialized()
        self.write("a-first/n.md", "# N\n")
        self.write("decisions/d.md", "# D\n")
        (self.container / "concepts").rmdir()
        (self.container / "concepts").write_text("not a directory\n", encoding="utf-8")
        before = tree(self.container)

        self.assert_blocked("WIKI_MIGRATION_CONFLICT")

        self.assertEqual(before, tree(self.container))

    def test_a_destination_that_is_also_a_needed_directory_blocks_before_any_move(self) -> None:
        self.initialized()
        self.write("aaa.md", "# A\n")
        self.write("discovery", "root file\n")
        self.write("_discovery/x.md", "# X\n")
        before = tree(self.container)

        self.assert_blocked("WIKI_MIGRATION_CONFLICT")

        self.assertEqual(before, tree(self.container))

    def test_destinations_that_differ_only_by_case_block_before_any_move(self) -> None:
        # raw/articles/Discovery/x.md and raw/articles/discovery/x.md are one file on APFS.
        self.initialized()
        self.write("Discovery/x.md", "one\n")
        self.write("_Discovery/x.md", "two\n")
        before = tree(self.container)

        error = self.assert_blocked("WIKI_MIGRATION_CONFLICT")

        self.assertIn("also claimed by", error.detail)
        self.assertEqual(before, tree(self.container))

    def test_a_destination_differing_only_by_case_from_an_existing_page_blocks(self) -> None:
        self.initialized()
        self.write("concepts/ADR.md", "existing page\n")
        self.write("decisions/adr.md", "legacy decision\n")
        before = tree(self.container)

        self.assert_blocked("WIKI_MIGRATION_CONFLICT")

        self.assertEqual(before, tree(self.container))

    def test_a_source_swapped_for_a_symlink_after_the_plan_is_never_followed(self) -> None:
        self.initialized()
        self.write("sessions/a.md", "first\n")
        self.write("sessions/b.md", "mine\n")
        secret = self.base / "secret.txt"
        secret.write_text("OUTSIDE-SECRET\n", encoding="utf-8")
        real_plan = wiki_layout._plan

        def swapping(container):
            result = real_plan(container)
            (self.container / "sessions/b.md").unlink()
            (self.container / "sessions/b.md").symlink_to(secret)
            return result

        wiki_layout._plan = swapping
        self.addCleanup(setattr, wiki_layout, "_plan", real_plan)
        self.assert_blocked("WIKI_MIGRATION_INCOMPLETE")

        self.assertFalse((self.container / "raw/transcripts/b.md").exists())
        self.assertTrue((self.container / "sessions/b.md").is_symlink())
        self.assertEqual("OUTSIDE-SECRET\n", secret.read_text(encoding="utf-8"))
        self.assertNotIn("OUTSIDE-SECRET", "".join(p.read_text(errors="ignore") for p in self.container.rglob("*.md") if p.is_file() and not p.is_symlink()))

    def test_a_source_edited_after_the_plan_is_kept(self) -> None:
        self.initialized()
        self.write("sessions/a.md", "planned\n")
        real_plan = wiki_layout._plan

        def editing(container):
            result = real_plan(container)
            (self.container / "sessions/a.md").write_text("edited during migration\n", encoding="utf-8")
            return result

        wiki_layout._plan = editing
        self.addCleanup(setattr, wiki_layout, "_plan", real_plan)
        self.assert_blocked("WIKI_MIGRATION_INCOMPLETE")

        self.assertEqual("edited during migration\n", (self.container / "sessions/a.md").read_text(encoding="utf-8"))
        self.assertFalse((self.container / "raw/transcripts/a.md").exists())

    def test_a_deduplicated_source_edited_after_the_plan_is_kept(self) -> None:
        self.initialized()
        self.write("sessions/a.md", "same\n")
        self.write("raw/transcripts/a.md", "same\n")
        real_plan = wiki_layout._plan

        def editing(container):
            result = real_plan(container)
            self.assertIn("sessions/a.md", result[0]["deduplicated"])
            (self.container / "sessions/a.md").write_text("same\nplus an edit\n", encoding="utf-8")
            return result

        wiki_layout._plan = editing
        self.addCleanup(setattr, wiki_layout, "_plan", real_plan)
        self.assert_blocked("WIKI_MIGRATION_INCOMPLETE")

        self.assertEqual("same\nplus an edit\n", (self.container / "sessions/a.md").read_text(encoding="utf-8"))

    def test_a_hard_linked_destination_is_never_treated_as_a_duplicate(self) -> None:
        self.initialized()
        source = self.write("sessions/a.md", "data\n")
        (self.container / "raw/transcripts/a.md").hardlink_to(source)
        self.assert_blocked("WIKI_MIGRATION_CONFLICT")
        self.assertEqual(b"data\n", source.read_bytes())

    def test_index_and_log_must_be_single_link_regular_files(self) -> None:
        outside = self.base / "outside-index.md"
        for name, make in (
            ("index.md", lambda path: path.hardlink_to(outside)),
            ("log.md", lambda path: path.symlink_to(outside)),
        ):
            with self.subTest(name=name):
                outside.write_text("outside\n", encoding="utf-8")
                self.initialized()
                self.write("sessions/a.md", "a\n")
                (self.container / name).unlink()
                make(self.container / name)

                self.assert_blocked("WIKI_PATH_UNSAFE")

                self.assertEqual("outside\n", outside.read_text(encoding="utf-8"))
                self.assertTrue((self.container / "sessions/a.md").exists())
                (self.container / name).unlink()
                wiki_layout.init(self.container, project="App", today=TODAY)

    def test_pages_sharing_a_name_get_distinct_index_lines(self) -> None:
        self.initialized()
        self.write("decisions/a/plan.md", "# A plan\n")
        self.write("decisions/b/plan.md", "# B plan\n")

        self.migrate()

        index = (self.container / "index.md").read_text(encoding="utf-8")
        self.assertIn("[[concepts/a/plan|plan]]", index)
        self.assertIn("[[concepts/b/plan|plan]]", index)
        self.write("concepts/c/plan.md", "# C plan\n")
        unindexed = [f["path"] for f in wiki_layout.check(self.container)["findings"] if f["code"] == "WIKI_PAGE_NOT_INDEXED"]
        self.assertEqual(["concepts/c/plan.md"], unindexed)


class CheckTests(WikiTestCase):
    def test_check_reports_missing_skeleton_legacy_folders_and_unindexed_pages(self) -> None:
        self.initialized()
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
        self.assertTrue(report["init_required"])
        self.assertEqual(before, tree(self.container))

    def test_init_then_migrate_apply_and_check(self) -> None:
        self.write("sessions/a.md")
        refused = self.run_cli("migrate", "--apply")
        self.assertEqual(2, refused.returncode)
        self.assertEqual("WIKI_INIT_REQUIRED", json.loads(refused.stdout)["reason"])
        initialized = self.run_cli("init", "--project", "App", "--date", TODAY, "--apply")
        self.assertEqual(0, initialized.returncode, initialized.stderr)
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
