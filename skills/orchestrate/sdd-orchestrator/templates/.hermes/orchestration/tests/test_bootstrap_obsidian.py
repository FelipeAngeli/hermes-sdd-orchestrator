"""Bootstrap tests for the Obsidian binding and vault-resident runtime.

Covers the behaviour added so that installing the orchestrator in a new project
arrives with Obsidian connectivity already wired, and so that runtime state no
longer lands in the repository.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))

import obsidian_binding  # noqa: E402
from test_bootstrap_worktree import git, run, write_source_config

SCRIPT = Path(__file__).resolve().parents[1] / "runtime" / "bootstrap_worktree.py"


class BootstrapObsidianTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="hermes-obsidian-test-")
        self.addCleanup(self.tempdir.cleanup)
        self.base = Path(self.tempdir.name)

        self.source = self.base / "canonical source"
        self.target = self.base / "target worktree with spaces"
        self.source.mkdir()
        git(self.source, "init")
        git(self.source, "config", "user.email", "bootstrap@example.test")
        git(self.source, "config", "user.name", "Bootstrap Test")
        (self.source / "README.md").write_text("fixture\n", encoding="utf-8")
        git(self.source, "add", "README.md")
        git(self.source, "commit", "-m", "fixture")
        git(self.source, "worktree", "add", "-b", "fixture-target", str(self.target))
        write_source_config(self.source)

        self.vault = self.base / "Demo Vault"
        self.container = self.vault / "Projects" / "Demo Project"
        self.container.mkdir(parents=True)

    def invoke(self, *extra: str, obsidian: bool = True) -> tuple[subprocess.CompletedProcess, dict]:
        args = [
            sys.executable,
            str(SCRIPT),
            "--source",
            str(self.source),
            "--target",
            str(self.target),
            "--json",
        ]
        if obsidian:
            args += [
                "--obsidian-vault",
                str(self.vault),
                "--obsidian-project",
                "Projects/Demo Project",
            ]
        args += list(extra)
        result = run(*args, cwd=self.source, check=False)
        return result, json.loads(result.stdout)

    # ---- binding generation -------------------------------------------------

    def test_apply_without_obsidian_flags_is_refused(self) -> None:
        """Silent derivation is what produced cross-project contamination."""
        result, report = self.invoke("--apply", obsidian=False)
        self.assertEqual(2, result.returncode)
        self.assertEqual("OBSIDIAN_BINDING_REQUIRED", report["status"])
        self.assertFalse((self.target / ".hermes.md").exists())

    def test_apply_writes_binding_file(self) -> None:
        result, _ = self.invoke("--apply")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        binding_file = self.target / ".hermes" / "obsidian.json"
        self.assertTrue(binding_file.exists())
        payload = json.loads(binding_file.read_text(encoding="utf-8"))
        self.assertEqual(1, payload["schema_version"])
        self.assertEqual(str(self.vault), payload["vault_path"])
        self.assertEqual("Projects/Demo Project", payload["project_container"])

    def test_generated_binding_loads_through_the_resolver(self) -> None:
        self.invoke("--apply")
        binding = obsidian_binding.load(self.target)
        self.assertEqual(self.container, binding.container_path)

    def test_missing_vault_directory_is_refused_before_any_write(self) -> None:
        result, report = self.invoke(
            "--apply",
            obsidian=False,
            *(
                "--obsidian-vault",
                str(self.base / "no-such-vault"),
                "--obsidian-project",
                "Projects/Demo Project",
            ),
        )
        self.assertEqual(2, result.returncode)
        self.assertEqual("OBSIDIAN_VAULT_NOT_FOUND", report["status"])
        self.assertFalse((self.target / ".hermes.md").exists())

    def test_absolute_project_container_is_refused(self) -> None:
        result, report = self.invoke(
            "--apply",
            obsidian=False,
            *(
                "--obsidian-vault",
                str(self.vault),
                "--obsidian-project",
                "/etc",
            ),
        )
        self.assertEqual(2, result.returncode)
        self.assertEqual("OBSIDIAN_BINDING_INVALID", report["status"])

    # ---- runtime placement --------------------------------------------------

    def test_state_is_created_in_the_vault_not_the_repository(self) -> None:
        self.invoke("--apply")
        self.assertFalse((self.target / ".hermes/orchestration/STATE.md").exists())
        binding = obsidian_binding.load(self.target)
        state = obsidian_binding.state_path(binding, self.target)
        self.assertTrue(state.exists(), f"missing {state}")
        self.assertIn("current: IDLE", state.read_text(encoding="utf-8"))

    def test_journal_is_created_in_the_vault_not_the_repository(self) -> None:
        self.invoke("--apply")
        self.assertFalse(
            (self.target / ".hermes/orchestration/ACTION_JOURNAL.json").exists()
        )
        binding = obsidian_binding.load(self.target)
        journal = obsidian_binding.journal_path(binding, self.target)
        self.assertTrue(journal.exists(), f"missing {journal}")
        payload = json.loads(journal.read_text(encoding="utf-8"))
        # git resolves symlinked ancestors (/var -> /private/var on macOS), so
        # compare resolved paths rather than the spelling the test used.
        self.assertEqual(
            self.target.resolve(), Path(payload["workspace"]["path"]).resolve()
        )

    def test_runtime_directory_is_hidden_from_obsidian(self) -> None:
        self.invoke("--apply")
        binding = obsidian_binding.load(self.target)
        runtime = obsidian_binding.runtime_dir(binding, self.target)
        relative = runtime.relative_to(self.container)
        self.assertTrue(
            relative.parts[0].startswith("."),
            f"runtime must be dot-hidden, got {relative.parts[0]}",
        )

    def test_runtime_stays_inside_the_bound_container(self) -> None:
        self.invoke("--apply")
        binding = obsidian_binding.load(self.target)
        runtime = obsidian_binding.runtime_dir(binding, self.target)
        self.assertTrue(obsidian_binding.is_inside_container(binding, runtime))

    def test_second_apply_is_idempotent(self) -> None:
        self.invoke("--apply")
        binding = obsidian_binding.load(self.target)
        journal = obsidian_binding.journal_path(binding, self.target)
        before = journal.read_bytes()
        result, report = self.invoke("--apply")
        self.assertEqual(0, result.returncode)
        self.assertEqual("ALREADY_INITIALIZED", report["status"])
        self.assertEqual(before, journal.read_bytes())

    def test_existing_vault_state_blocks_reinitialisation(self) -> None:
        self.invoke("--apply")
        (self.target / ".hermes.md").unlink()
        result, report = self.invoke("--apply")
        self.assertEqual(2, result.returncode)
        self.assertEqual("EXISTING_STATE_REQUIRES_REVIEW", report["status"])

    def test_two_worktrees_get_separate_runtime_directories(self) -> None:
        """Risk R1 end-to-end: real worktrees must not share a journal."""
        second = self.base / "second target"
        git(self.source, "worktree", "add", "-b", "fixture-second", str(second))
        self.invoke("--apply")
        run(
            sys.executable,
            str(SCRIPT),
            "--source",
            str(self.source),
            "--target",
            str(second),
            "--json",
            "--obsidian-vault",
            str(self.vault),
            "--obsidian-project",
            "Projects/Demo Project",
            "--apply",
            cwd=self.source,
            check=True,
        )
        first_binding = obsidian_binding.load(self.target)
        second_binding = obsidian_binding.load(second)
        self.assertNotEqual(
            obsidian_binding.journal_path(first_binding, self.target),
            obsidian_binding.journal_path(second_binding, second),
        )

    # ---- repository hygiene -------------------------------------------------

    def test_binding_is_not_excluded_from_version_control(self) -> None:
        """The binding must survive a clone: it is the connectivity itself."""
        self.invoke("--apply")
        result = git(
            self.target, "check-ignore", "-v", ".hermes/obsidian.json", check=False
        )
        self.assertNotEqual(
            0, result.returncode, f"binding must not be ignored: {result.stdout}"
        )

    def test_repository_keeps_no_orchestration_runtime(self) -> None:
        self.invoke("--apply")
        orchestration = self.target / ".hermes" / "orchestration"
        leftovers = [
            name
            for name in ("STATE.md", "ACTION_JOURNAL.json", "INCIDENTS.md")
            if (orchestration / name).exists()
        ]
        self.assertEqual([], leftovers)


if __name__ == "__main__":
    unittest.main()
