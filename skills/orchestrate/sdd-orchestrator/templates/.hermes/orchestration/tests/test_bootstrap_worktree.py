"""Isolated tests for the local Hermes worktree bootstrap utility."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))

import bootstrap_worktree as bootstrap  # noqa: E402
import obsidian_binding  # noqa: E402
from jsonschema import Draft202012Validator


ALLOWLIST = (
    ".hermes.md",
    ".hermes/orchestration/policies/LOOP_POLICY.md",
    ".hermes/orchestration/policies/GATES.md",
    ".hermes/orchestration/contracts/EXECUTOR_CONTRACT.md",
    ".hermes/orchestration/schemas/EXECUTOR_RESULT_SCHEMA.json",
    ".hermes/orchestration/contracts/REVIEW_CONTRACT.md",
    ".hermes/orchestration/schemas/REVIEW_RESULT_SCHEMA.json",
    ".hermes/orchestration/runtime/validate_protocol.py",
    ".hermes/orchestration/tests/test_protocol.py",
    ".hermes/orchestration/policies/ACTION_RECOVERY.md",
    ".hermes/orchestration/schemas/ACTION_JOURNAL_SCHEMA.json",
    ".hermes/orchestration/runtime/action_journal.py",
    ".hermes/orchestration/tests/test_action_journal.py",
    ".hermes/orchestration/policies/BOUNDED_AUTOMATION.md",
    ".hermes/orchestration/schemas/BOUNDED_RUN_PLAN_SCHEMA.json",
    ".hermes/orchestration/runtime/bounded_run_planner.py",
    ".hermes/orchestration/tests/test_bounded_run_planner.py",
    ".hermes/orchestration/policies/BOUNDED_RUN_DRIVER.md",
    ".hermes/orchestration/runtime/bounded_run_driver.py",
    ".hermes/orchestration/tests/test_bounded_run_driver.py",
    ".hermes/orchestration/runtime/bounded_loop_driver.py",
    ".hermes/orchestration/tests/test_bounded_loop_driver.py",
)
SCRIPT = Path(__file__).resolve().parents[1] / "runtime" / "bootstrap_worktree.py"
ORCHESTRATION_DIR = Path(__file__).resolve().parents[1]


def run(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(args),
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    if check and result.returncode:
        raise AssertionError(f"command failed: {args}\nstdout={result.stdout}\nstderr={result.stderr}")
    return result


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run("git", *args, cwd=repo, check=check)


def write_source_config(root: Path) -> None:
    contents = {
        ".hermes.md": "# Canonical Hermes configuration\n",
        ".hermes/orchestration/policies/LOOP_POLICY.md": "# Policy\nMANUAL is the default.\n",
        ".hermes/orchestration/policies/GATES.md": "project_ci_policy:\n  enabled: false\n  status_when_skipped: DISABLED_BY_PROJECT_POLICY\n",
        ".hermes/orchestration/contracts/EXECUTOR_CONTRACT.md": "executor_result schema_version: 2\n",
        ".hermes/orchestration/schemas/EXECUTOR_RESULT_SCHEMA.json": '{"type":"object","required":["executor_result"]}\n',
        ".hermes/orchestration/contracts/REVIEW_CONTRACT.md": "review_result schema_version: 2\n",
        ".hermes/orchestration/schemas/REVIEW_RESULT_SCHEMA.json": '{"type":"object","required":["review_result"]}\n',
        ".hermes/orchestration/runtime/validate_protocol.py": "ROOT = __file__\n",
        ".hermes/orchestration/tests/test_protocol.py": "# synthetic fixtures only\n",
        ".hermes/orchestration/policies/ACTION_RECOVERY.md": "# Action recovery\n",
        ".hermes/orchestration/schemas/ACTION_JOURNAL_SCHEMA.json": '{"type":"object","required":["journal_version"]}\n',
        ".hermes/orchestration/runtime/action_journal.py": "JOURNAL_VERSION = 1\n",
        ".hermes/orchestration/tests/test_action_journal.py": "# isolated action journal tests\n",
        ".hermes/orchestration/policies/BOUNDED_AUTOMATION.md": "# Bounded automation\n",
        ".hermes/orchestration/schemas/BOUNDED_RUN_PLAN_SCHEMA.json": '{"type":"object","required":["plan_version"]}\n',
        ".hermes/orchestration/runtime/bounded_run_planner.py": "PLAN_VERSION = 1\n",
        ".hermes/orchestration/tests/test_bounded_run_planner.py": "# isolated bounded planner tests\n",
        ".hermes/orchestration/policies/BOUNDED_RUN_DRIVER.md": "# Bounded run driver\n",
        ".hermes/orchestration/runtime/bounded_run_driver.py": "EXECUTE_NEXT = \"EXECUTE_NEXT\"\n",
        ".hermes/orchestration/tests/test_bounded_run_driver.py": "# isolated bounded runtime driver tests\n",
        ".hermes/orchestration/runtime/bounded_loop_driver.py": "CONTINUE = \"CONTINUE\"\n",
        ".hermes/orchestration/tests/test_bounded_loop_driver.py": "# isolated bounded loop driver tests\n",
    }
    for relative, content in contents.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


class BootstrapWorktreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="hermes-bootstrap-test-")
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
        # Runtime now lives in the vault, keyed per worktree.
        self.vault = self.base / "vault"
        self.container = self.vault / "Projects" / "Fixture"
        self.container.mkdir(parents=True)

    def runtime(self) -> Path:
        return (
            self.container
            / obsidian_binding._DEFAULT_RUNTIME_SUBPATH
            / obsidian_binding.worktree_slug(self.target)
        )

    def invoke(self, *extra: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        result = run(
            sys.executable,
            str(SCRIPT),
            "--source",
            str(self.source),
            "--target",
            str(self.target),
            "--json",
            "--obsidian-vault",
            str(self.vault),
            "--obsidian-project",
            "Projects/Fixture",
            *extra,
            cwd=self.source,
            check=False,
        )
        return result, json.loads(result.stdout)

    def test_dry_run_does_not_write(self) -> None:
        result, report = self.invoke()
        self.assertEqual(0, result.returncode)
        self.assertEqual("READY", report["status"])
        self.assertFalse((self.target / ".hermes.md").exists())
        self.assertFalse((self.runtime() / "STATE.md").exists())
        exclude = Path(git(self.target, "rev-parse", "--git-path", "info/exclude").stdout.strip())
        self.assertNotIn("bootstrap_worktree.py", exclude.read_text(encoding="utf-8"))

    def test_dry_run_reports_unrelated_prunable_worktree_without_writing(self) -> None:
        prunable = self.base / "unrelated prunable worktree"
        git(self.source, "worktree", "add", "-b", "fixture-prunable", str(prunable))
        shutil.rmtree(prunable)

        result, report = self.invoke()

        self.assertEqual(0, result.returncode)
        self.assertEqual("READY", report["status"])
        self.assertIn(
            {"code": "UNRELATED_PRUNABLE_WORKTREE", "path": os.path.realpath(str(prunable)), "action": "NOT_TOUCHED"},
            report["warnings"],
        )
        self.assertFalse((self.target / ".hermes.md").exists())
        self.assertFalse((self.runtime() / "STATE.md").exists())
        self.assertIn(str(prunable), git(self.source, "worktree", "list", "--porcelain").stdout)

    def test_missing_source_and_target_are_blocked(self) -> None:
        missing_source = self.base / "missing source"
        with self.assertRaises(bootstrap.BootstrapError) as source_error:
            bootstrap.build_plan(str(missing_source), str(self.target), str(self.vault), "Projects/Fixture")
        self.assertEqual("PATH_NOT_DIRECTORY", source_error.exception.status)

        missing_target = self.base / "missing target"
        with self.assertRaises(bootstrap.BootstrapError) as target_error:
            bootstrap.build_plan(str(self.source), str(missing_target), str(self.vault), "Projects/Fixture")
        self.assertEqual("PATH_NOT_DIRECTORY", target_error.exception.status)

    def test_unregistered_target_is_blocked(self) -> None:
        target = bootstrap.discover_worktree(str(self.target), "target")
        with self.assertRaises(bootstrap.BootstrapError) as error:
            bootstrap.registered_participant((), target, "target")
        self.assertEqual("TARGET_NOT_REGISTERED", error.exception.status)

    def test_apply_initializes_empty_target_and_records_identity(self) -> None:
        result, report = self.invoke("--apply")
        self.assertEqual(0, result.returncode)
        self.assertEqual("READY", report["status"])
        for relative in ALLOWLIST:
            self.assertEqual((self.source / relative).read_bytes(), (self.target / relative).read_bytes())
        state = (self.runtime() / "STATE.md").read_text(encoding="utf-8")
        self.assertIn("ticket:\n  id: IDLE", state)
        self.assertIn("current: IDLE", state)
        self.assertIn("status: WAITING", state)
        self.assertIn("mode: MANUAL", state)
        self.assertIn("loop_active: false", state)
        self.assertIn("bounded_run_plan: null", state)
        self.assertIn("captured: false", state)
        self.assertIn(str(self.target), state)
        self.assertIn("git_common_dir", state)
        self.assertNotIn("APP-436", state)
        self.assertTrue((self.runtime() / "INCIDENTS.md").is_file())
        journal = json.loads((self.runtime() / "ACTION_JOURNAL.json").read_text(encoding="utf-8"))
        self.assertEqual(1, journal["journal_version"])
        self.assertEqual(str(self.target.resolve()), journal["workspace"]["path"])
        self.assertIsNone(journal["action"]["id"])
        self.assertEqual("IDLE", journal["action"]["status"])

    def test_apply_creates_journal_matching_distributed_schema_and_canonical_init(self) -> None:
        for folder, filename in (("schemas", "ACTION_JOURNAL_SCHEMA.json"), ("runtime", "action_journal.py")):
            destination = self.source / ".hermes/orchestration" / folder / filename
            destination.write_bytes((ORCHESTRATION_DIR / folder / filename).read_bytes())

        result, report = self.invoke("--apply")

        self.assertEqual(0, result.returncode)
        self.assertEqual("READY", report["status"])
        journal_path = self.runtime() / "ACTION_JOURNAL.json"
        schema_path = self.target / ".hermes/orchestration/schemas/ACTION_JOURNAL_SCHEMA.json"
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual([], list(Draft202012Validator(schema).iter_errors(journal)))
        self.assertEqual(
            {
                "state_path": None,
                "expected_before_hash": None,
                "expected_after_hash": None,
                "committed_after_hash": None,
                "committed_at": None,
                "verified": False,
            },
            journal["state_commit"],
        )
        self.assertEqual("IDLE", journal["action"]["status"])
        self.assertIsNone(journal["action"]["ticket"])
        self.assertEqual({"exists": False, "sha256": None, "validation_status": "PENDING"}, journal["artifact"])
        self.assertEqual([], journal["incidents"])

        canonical_journal_path = self.target / "canonical-action-journal.json"
        workspace = journal["workspace"]
        init = run(
            sys.executable,
            str(self.target / ".hermes/orchestration/runtime/action_journal.py"),
            "--journal",
            str(canonical_journal_path),
            "--payload",
            json.dumps(workspace),
            "init",
            cwd=self.target,
            check=False,
        )
        self.assertEqual(0, init.returncode, init.stderr)
        canonical_journal = json.loads(canonical_journal_path.read_text(encoding="utf-8"))
        self.assertEqual([], list(Draft202012Validator(schema).iter_errors(canonical_journal)))
        self.assertEqual(set(journal), set(canonical_journal))
        for section in ("workspace", "action", "fingerprints", "process", "artifact", "state_commit"):
            self.assertEqual(set(journal[section]), set(canonical_journal[section]), section)

    def test_fresh_bootstrap_journal_recovery_allows_first_dispatch(self) -> None:
        for folder, filename in (("schemas", "ACTION_JOURNAL_SCHEMA.json"), ("runtime", "action_journal.py")):
            destination = self.source / ".hermes/orchestration" / folder / filename
            destination.write_bytes((ORCHESTRATION_DIR / folder / filename).read_bytes())

        result, report = self.invoke("--apply")

        self.assertEqual(0, result.returncode)
        self.assertEqual("READY", report["status"])
        journal_path = self.runtime() / "ACTION_JOURNAL.json"
        recovery = run(
            sys.executable,
            str(self.target / ".hermes/orchestration/runtime/action_journal.py"),
            "--journal",
            str(journal_path),
            "--json",
            "recover",
            cwd=self.target,
            check=False,
        )
        self.assertEqual(0, recovery.returncode, recovery.stderr)
        self.assertEqual("DISPATCH_ALLOWED", json.loads(recovery.stdout)["decision"])

    def test_second_apply_preserves_bytes_and_reports_already_initialized(self) -> None:
        self.assertEqual(0, self.invoke("--apply")[0].returncode)
        before = {self.target / path: (self.target / path).read_bytes() for path in ALLOWLIST}
        before.update(
            {
                self.runtime() / name: (self.runtime() / name).read_bytes()
                for name in ("STATE.md", "ACTION_JOURNAL.json")
            }
        )
        result, report = self.invoke("--apply")
        self.assertEqual(0, result.returncode)
        self.assertEqual("ALREADY_INITIALIZED", report["status"])
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_existing_state_is_never_overwritten_when_work_is_needed(self) -> None:
        state = self.runtime() / "STATE.md"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text("existing state bytes\n", encoding="utf-8")
        result, report = self.invoke("--apply")
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("EXISTING_STATE_REQUIRES_REVIEW", report["status"])
        self.assertEqual("existing state bytes\n", state.read_text(encoding="utf-8"))
        self.assertFalse((self.target / ".hermes.md").exists())

    def test_conflict_blocks_before_writes(self) -> None:
        conflict = self.target / ".hermes.md"
        conflict.write_text("different destination config\n", encoding="utf-8")
        result, report = self.invoke("--apply")
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("CONFIG_CONFLICT", report["status"])
        self.assertEqual("different destination config\n", conflict.read_text(encoding="utf-8"))
        self.assertFalse((self.runtime() / "STATE.md").exists())

    def test_same_root_and_different_repositories_are_rejected(self) -> None:
        same = run(sys.executable, str(SCRIPT), "--source", str(self.source), "--target", str(self.source), "--json", cwd=self.source, check=False)
        self.assertNotEqual(0, same.returncode)
        self.assertEqual("SAME_WORKTREE", json.loads(same.stdout)["status"])
        other = self.base / "other repository"
        other.mkdir()
        git(other, "init")
        git(other, "config", "user.email", "bootstrap@example.test")
        git(other, "config", "user.name", "Bootstrap Test")
        (other / "README.md").write_text("other fixture\n", encoding="utf-8")
        git(other, "add", "README.md")
        git(other, "commit", "-m", "fixture")
        different = run(sys.executable, str(SCRIPT), "--source", str(self.source), "--target", str(other), "--json", cwd=self.source, check=False)
        self.assertNotEqual(0, different.returncode)
        self.assertEqual("DIFFERENT_REPOSITORY", json.loads(different.stdout)["status"])

    def test_incomplete_source_and_destination_symlink_are_rejected(self) -> None:
        (self.source / ".hermes/orchestration/policies/GATES.md").unlink()
        result, report = self.invoke()
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("SOURCE_CONFIG_MISSING", report["status"])
        write_source_config(self.source)
        outside = self.base / "outside"
        outside.mkdir()
        os.symlink(outside, self.target / ".hermes")
        result, report = self.invoke()
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("SYMLINK_REJECTED", report["status"])

    def test_tracked_destination_path_is_not_overwritten_or_untracked(self) -> None:
        tracked = self.target / ".hermes.md"
        tracked.write_text("tracked conflict\n", encoding="utf-8")
        git(self.target, "add", ".hermes.md")
        result, report = self.invoke("--apply")
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("TRACKED_DESTINATION_PATH", report["status"])
        self.assertEqual("tracked conflict\n", tracked.read_text(encoding="utf-8"))
        self.assertEqual(".hermes.md\n", git(self.target, "diff", "--cached", "--name-only").stdout)

    def test_ignores_are_exact_and_not_duplicated(self) -> None:
        self.assertEqual(0, self.invoke("--apply")[0].returncode)
        self.assertEqual(0, self.invoke("--apply")[0].returncode)
        exclude = Path(git(self.target, "rev-parse", "--git-path", "info/exclude").stdout.strip()).read_text(encoding="utf-8")
        for relative in (*ALLOWLIST, ".hermes/orchestration/action-journal-history/"):
            self.assertEqual(1, exclude.splitlines().count(relative))
        # Runtime no longer lives in the repository, so it needs no exclude entry.
        for absent in (".hermes/orchestration/STATE.md", ".hermes/orchestration/ACTION_JOURNAL.json"):
            self.assertEqual(0, exclude.splitlines().count(absent))
        # The binding is versioned: excluding it would break clones.
        self.assertEqual(0, exclude.splitlines().count(".hermes/obsidian.json"))

    def test_existing_journal_is_never_overwritten(self) -> None:
        journal = self.runtime() / "ACTION_JOURNAL.json"
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text('{"preserve":"these bytes"}\n', encoding="utf-8")
        result, report = self.invoke("--apply")
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("EXISTING_ACTION_JOURNAL_REQUIRES_REVIEW", report["status"])
        self.assertEqual('{"preserve":"these bytes"}\n', journal.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
