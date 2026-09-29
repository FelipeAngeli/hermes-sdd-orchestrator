"""Tests for the guarded read-only Obsidian CLI connector."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import sys

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))

import obsidian_connector  # noqa: E402


class ConnectorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)
        self.repo = root / "repo"
        self.repo.mkdir()
        self.vault = root / "Knowledge"
        self.container = self.vault / "Projects" / "Demo"
        self.container.mkdir(parents=True)
        binding = {
            "schema_version": 1,
            "vault_path": str(self.vault),
            "project_container": "Projects/Demo",
            "runtime_subpath": ".hermes-runtime",
        }
        path = self.repo / ".hermes" / "obsidian.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(binding), encoding="utf-8")

    def fake_cli(self, body: str) -> str:
        path = self.repo / "fake-obsidian"
        path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
        os.chmod(path, 0o755)
        return str(path)


class PreflightTests(ConnectorTestCase):
    def test_missing_cli_uses_ready_filesystem_fallback(self) -> None:
        report = obsidian_connector.preflight(
            self.repo,
            cli_path=str(self.repo / "missing-obsidian"),
        )

        self.assertEqual("READY_FILESYSTEM", report["status"])
        self.assertEqual("filesystem", report["transport"])
        self.assertTrue(report["ready"])
        self.assertIn("CLI_UNAVAILABLE", report["diagnostics"])

    def test_malformed_cli_discovery_falls_back_without_trusting_output(self) -> None:
        cli = self.fake_cli("print('not a vault record')\n")

        report = obsidian_connector.preflight(self.repo, cli_path=cli)

        self.assertEqual("READY_FILESYSTEM", report["status"])
        self.assertEqual("filesystem", report["transport"])
        self.assertIn("CLI_OUTPUT_INVALID", report["diagnostics"])

    def test_invalid_utf8_cli_output_uses_filesystem_fallback(self) -> None:
        cli = self.fake_cli(
            "import sys\n"
            "sys.stdout.buffer.write(b'\\xff')\n"
        )

        report = obsidian_connector.preflight(self.repo, cli_path=cli)

        self.assertEqual("READY_FILESYSTEM", report["status"])
        self.assertEqual("filesystem", report["transport"])
        self.assertIn("CLI_OUTPUT_INVALID", report["diagnostics"])

    def test_non_finite_timeout_falls_back_with_structured_diagnostic(self) -> None:
        cli = self.fake_cli(f"print('Knowledge\\t{self.vault}')\n")

        report = obsidian_connector.preflight(self.repo, cli_path=cli, timeout=float("nan"))

        self.assertEqual("READY_FILESYSTEM", report["status"])
        self.assertIn("CLI_INPUT_INVALID", report["diagnostics"])

    def test_non_finite_timeout_is_invalid_even_when_cli_is_missing(self) -> None:
        report = obsidian_connector.preflight(
            self.repo,
            cli_path=str(self.repo / "missing-obsidian"),
            timeout=float("nan"),
        )

        self.assertEqual("READY_FILESYSTEM", report["status"])
        self.assertIn("CLI_INPUT_INVALID", report["diagnostics"])

    def test_noncanonical_bound_vault_spelling_is_not_ready(self) -> None:
        binding_path = self.repo / ".hermes" / "obsidian.json"
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        binding["vault_path"] = f"{self.vault}/../{self.vault.name}"
        binding_path.write_text(json.dumps(binding), encoding="utf-8")

        report = obsidian_connector.preflight(self.repo)

        self.assertEqual("BINDING_PATH_UNSAFE", report["status"])
        self.assertFalse(report["ready"])

    def test_symlinked_bound_vault_is_not_ready(self) -> None:
        real_vault = self.vault.with_name("Knowledge-real")
        self.vault.rename(real_vault)
        os.symlink(real_vault, self.vault)

        report = obsidian_connector.preflight(self.repo)

        self.assertEqual("BINDING_PATH_UNSAFE", report["status"])
        self.assertFalse(report["ready"])

    def test_nul_in_discovered_vault_path_uses_filesystem_fallback(self) -> None:
        cli = self.fake_cli(
            "import sys\n"
            "sys.stdout.buffer.write(b'Knowledge\\t/tmp/bad\\x00path\\n')\n"
        )

        report = obsidian_connector.preflight(self.repo, cli_path=cli)

        self.assertEqual("READY_FILESYSTEM", report["status"])
        self.assertEqual("filesystem", report["transport"])
        self.assertIn("CLI_OUTPUT_INVALID", report["diagnostics"])

    def test_multiple_vaults_selects_exact_bound_path(self) -> None:
        other = self.vault.parent / "Other"
        other.mkdir()
        cli = self.fake_cli(
            f"print('Other\\t{other}')\nprint('Knowledge\\t{self.vault}')\n"
        )

        report = obsidian_connector.preflight(self.repo, cli_path=cli)

        self.assertEqual("READY_CLI", report["status"])
        self.assertEqual("Knowledge", report["vault"])

    def test_cli_without_bound_vault_uses_filesystem_fallback(self) -> None:
        other = self.vault.parent / "Other"
        other.mkdir()
        cli = self.fake_cli(f"print('Other\\t{other}')\n")

        report = obsidian_connector.preflight(self.repo, cli_path=cli)

        self.assertEqual("READY_FILESYSTEM", report["status"])
        self.assertIn("CLI_VAULT_UNREACHABLE", report["diagnostics"])

    def test_cli_timeout_uses_filesystem_fallback(self) -> None:
        cli = self.fake_cli("import time\ntime.sleep(1)\n")

        report = obsidian_connector.preflight(self.repo, cli_path=cli, timeout=0.01)

        self.assertEqual("READY_FILESYSTEM", report["status"])
        self.assertIn("CLI_UNAVAILABLE", report["diagnostics"])

    def test_missing_vault_and_container_have_distinct_statuses(self) -> None:
        self.vault.rename(self.vault.with_name("gone"))
        missing_vault = obsidian_connector.preflight(self.repo)
        self.assertEqual("VAULT_UNREACHABLE", missing_vault["status"])

        self.vault.mkdir()
        missing_container = obsidian_connector.preflight(self.repo)
        self.assertEqual("CONTAINER_UNAVAILABLE", missing_container["status"])

    def test_invalid_binding_reports_binding_status(self) -> None:
        binding = self.repo / ".hermes" / "obsidian.json"
        binding.write_text("{}", encoding="utf-8")

        report = obsidian_connector.preflight(self.repo)

        self.assertEqual("BINDING_INVALID", report["status"])
        self.assertFalse(report["ready"])


class SearchTests(ConnectorTestCase):
    def test_filesystem_search_returns_only_project_notes(self) -> None:
        (self.container / "inside.md").write_text("Alpha decision", encoding="utf-8")
        outside = self.vault / "Personal"
        outside.mkdir()
        (outside / "private.md").write_text("Alpha private", encoding="utf-8")
        runtime = self.container / ".hermes-runtime" / "worktree"
        runtime.mkdir(parents=True)
        (runtime / "STATE.md").write_text("Alpha runtime", encoding="utf-8")

        result = obsidian_connector.search(
            self.repo,
            "alpha",
            cli_path=str(self.repo / "missing-obsidian"),
        )

        self.assertEqual("filesystem", result["transport"])
        self.assertEqual(["inside.md"], result["paths"])

    def test_filesystem_search_excludes_nested_runtime_subpath(self) -> None:
        binding_path = self.repo / ".hermes" / "obsidian.json"
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        binding["runtime_subpath"] = ".custom/runtime"
        binding_path.write_text(json.dumps(binding), encoding="utf-8")
        (self.container / "allowed.md").write_text("nested needle", encoding="utf-8")
        runtime_note = self.container / ".custom" / "runtime" / "STATE.md"
        runtime_note.parent.mkdir(parents=True)
        runtime_note.write_text("nested needle secret", encoding="utf-8")

        result = obsidian_connector.search(
            self.repo,
            "nested needle",
            cli_path=str(self.repo / "missing-obsidian"),
        )

        self.assertEqual(["allowed.md"], result["paths"])

    def test_cli_search_filters_results_to_project_container(self) -> None:
        (self.container / "cli.md").write_text("CLI result", encoding="utf-8")
        private = self.vault / "Personal"
        private.mkdir()
        (private / "private.md").write_text("Private", encoding="utf-8")
        cli = self.fake_cli(
            "import json, sys\n"
            f"vault = {str(self.vault)!r}\n"
            "args = sys.argv[1:]\n"
            "if args == ['vaults', 'verbose']:\n"
            "    print('Knowledge\\t' + vault)\n"
            "elif 'search' in args:\n"
            "    print(json.dumps(['Projects/Demo/cli.md', 'Personal/private.md']))\n"
            "else:\n"
            "    raise SystemExit(2)\n"
        )

        result = obsidian_connector.search(self.repo, "result", cli_path=cli)

        self.assertEqual("cli", result["transport"])
        self.assertEqual(["cli.md"], result["paths"])
        self.assertEqual([], result["diagnostics"])

    def test_empty_search_query_is_refused(self) -> None:
        with self.assertRaises(obsidian_connector.ConnectorError) as raised:
            obsidian_connector.search(self.repo, "   ")

        self.assertEqual("QUERY_INVALID", raised.exception.code)

    def test_nul_search_query_is_refused_before_subprocess(self) -> None:
        cli = self.fake_cli(f"print('Knowledge\\t{self.vault}')\n")

        with self.assertRaises(obsidian_connector.ConnectorError) as raised:
            obsidian_connector.search(self.repo, "bad\x00query", cli_path=cli)

        self.assertEqual("QUERY_INVALID", raised.exception.code)

    def test_cli_search_malformed_json_falls_back_to_filesystem(self) -> None:
        (self.container / "fallback.md").write_text("fallback needle", encoding="utf-8")
        cli = self.fake_cli(
            "import sys\n"
            f"vault = {str(self.vault)!r}\n"
            "args = sys.argv[1:]\n"
            "print('Knowledge\\t' + vault if args == ['vaults', 'verbose'] else 'not-json')\n"
        )

        result = obsidian_connector.search(self.repo, "needle", cli_path=cli)

        self.assertEqual("filesystem", result["transport"])
        self.assertEqual(["fallback.md"], result["paths"])
        self.assertIn("CLI_OUTPUT_INVALID", result["diagnostics"])

    def test_cli_search_skips_symlinked_result(self) -> None:
        outside = self.vault / "Personal"
        outside.mkdir()
        secret = outside / "secret.md"
        secret.write_text("secret", encoding="utf-8")
        os.symlink(secret, self.container / "linked.md")
        cli = self.fake_cli(
            "import json, sys\n"
            f"vault = {str(self.vault)!r}\n"
            "args = sys.argv[1:]\n"
            "print('Knowledge\\t' + vault if args == ['vaults', 'verbose'] else json.dumps(['Projects/Demo/linked.md']))\n"
        )

        result = obsidian_connector.search(self.repo, "secret", cli_path=cli)

        self.assertEqual([], result["paths"])

    def test_cli_search_rejects_all_excluded_note_paths(self) -> None:
        binding_path = self.repo / ".hermes" / "obsidian.json"
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        binding["runtime_subpath"] = ".custom-runtime"
        binding_path.write_text(json.dumps(binding), encoding="utf-8")
        relative_paths = [
            ".hermes-runtime/STATE.md",
            ".custom-runtime/STATE.md",
            ".obsidian/private.md",
            ".trash/private.md",
            ".git/private.md",
            ".env",
            "allowed.md",
        ]
        for relative_path in relative_paths:
            note = self.container / relative_path
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_text("needle", encoding="utf-8")
        cli_paths = [f"Projects/Demo/{path}" for path in relative_paths]
        cli = self.fake_cli(
            "import json, sys\n"
            f"vault = {str(self.vault)!r}\n"
            f"paths = {cli_paths!r}\n"
            "args = sys.argv[1:]\n"
            "print('Knowledge\\t' + vault if args == ['vaults', 'verbose'] else json.dumps(paths))\n"
        )

        result = obsidian_connector.search(self.repo, "needle", cli_path=cli)

        self.assertEqual("cli", result["transport"])
        self.assertEqual(["allowed.md"], result["paths"])


class ReadTests(ConnectorTestCase):
    def test_filesystem_read_refuses_symlink_escape(self) -> None:
        outside = self.vault / "Personal"
        outside.mkdir()
        secret = outside / "secret.md"
        secret.write_text("private", encoding="utf-8")
        link = self.container / "escape.md"
        os.symlink(secret, link)

        with self.assertRaises(obsidian_connector.ConnectorError) as raised:
            obsidian_connector.read_note(
                self.repo,
                "escape.md",
                cli_path=str(self.repo / "missing-obsidian"),
            )

        self.assertEqual("NOTE_UNAVAILABLE", raised.exception.code)

    def test_direct_read_rejects_runtime_note(self) -> None:
        runtime = self.container / ".hermes-runtime" / "worktree"
        runtime.mkdir(parents=True)
        (runtime / "STATE.md").write_text("runtime secret", encoding="utf-8")

        with self.assertRaises(obsidian_connector.ConnectorError) as raised:
            obsidian_connector.read_note(
                self.repo,
                ".hermes-runtime/worktree/STATE.md",
                cli_path=str(self.repo / "missing-obsidian"),
            )

        self.assertEqual("NOTE_PATH_INVALID", raised.exception.code)

    def test_direct_read_rejects_non_markdown_file(self) -> None:
        (self.container / ".env").write_text("TOKEN=secret", encoding="utf-8")

        with self.assertRaises(obsidian_connector.ConnectorError) as raised:
            obsidian_connector.read_note(self.repo, ".env")

        self.assertEqual("NOTE_PATH_INVALID", raised.exception.code)

    def test_read_returns_canonical_relative_path(self) -> None:
        (self.container / "note.md").write_text("body", encoding="utf-8")

        result = obsidian_connector.read_note(self.repo, "./note.md")

        self.assertEqual("note.md", result["path"])

    def test_filesystem_read_succeeds_with_obsidian_closed(self) -> None:
        (self.container / "note.md").write_text("offline body", encoding="utf-8")

        result = obsidian_connector.read_note(
            self.repo,
            "note.md",
            cli_path=str(self.repo / "missing-obsidian"),
        )

        self.assertEqual("filesystem", result["transport"])
        self.assertEqual("offline body", result["content"])
        self.assertNotIn(str(self.vault), result["path"])

    def test_read_uses_no_follow_filesystem_even_when_cli_is_available(self) -> None:
        (self.container / "note.md").write_text("filesystem body", encoding="utf-8")
        calls = self.repo / "calls.jsonl"
        cli = self.fake_cli(
            "import json, sys\n"
            f"calls = {str(calls)!r}\n"
            "with open(calls, 'a', encoding='utf-8') as handle:\n"
            "    handle.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "print('unexpected cli content', end='')\n"
        )

        result = obsidian_connector.read_note(self.repo, "note.md", cli_path=cli)

        self.assertEqual("filesystem", result["transport"])
        self.assertEqual("filesystem body", result["content"])
        self.assertFalse(calls.exists(), "read_note must not dispatch a lexical CLI read")

    def test_read_does_not_dispatch_cli_that_could_replace_parent(self) -> None:
        safe = self.container / "safe"
        safe.mkdir()
        (safe / "note.md").write_text("safe body", encoding="utf-8")
        outside = self.vault / "Personal"
        outside.mkdir()
        (outside / "note.md").write_text("outside secret", encoding="utf-8")
        cli = self.fake_cli(
            "import os, pathlib, shutil\n"
            f"safe = pathlib.Path({str(safe)!r})\n"
            f"outside = pathlib.Path({str(outside)!r})\n"
            "shutil.rmtree(safe)\n"
            "os.symlink(outside, safe)\n"
            "print('unexpected cli content', end='')\n"
        )

        result = obsidian_connector.read_note(self.repo, "safe/note.md", cli_path=cli)

        self.assertEqual("filesystem", result["transport"])
        self.assertEqual("safe body", result["content"])
        self.assertFalse(safe.is_symlink())

    def test_read_closes_directory_descriptors_when_validation_fails(self) -> None:
        (self.container / "note.md").write_text("trusted body", encoding="utf-8")
        real_open = os.open
        real_fstat = os.fstat
        real_close = os.close
        opened: list[int] = []
        closed: list[int] = []
        fstat_calls = 0

        def tracking_open(path, flags, mode=0o777, *, dir_fd=None):
            if dir_fd is None:
                descriptor = real_open(path, flags, mode)
            else:
                descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
            opened.append(descriptor)
            return descriptor

        def fail_second_validation(descriptor):
            nonlocal fstat_calls
            fstat_calls += 1
            if fstat_calls == 2:
                raise OSError("injected directory validation failure")
            return real_fstat(descriptor)

        def tracking_close(descriptor):
            closed.append(descriptor)
            return real_close(descriptor)

        try:
            with mock.patch.object(obsidian_connector.os, "open", tracking_open):
                with mock.patch.object(obsidian_connector.os, "fstat", fail_second_validation):
                    with mock.patch.object(obsidian_connector.os, "close", tracking_close):
                        with self.assertRaises(obsidian_connector.ConnectorError):
                            obsidian_connector.read_note(self.repo, "note.md")

            self.assertEqual(set(opened), set(closed))
        finally:
            for descriptor in set(opened) - set(closed):
                real_close(descriptor)

    def test_read_does_not_resolve_container_after_validation(self) -> None:
        (self.container / "note.md").write_text("trusted body", encoding="utf-8")
        moved_vault = self.vault.with_name("Knowledge-original")
        outside_vault = self.vault.with_name("Outside")
        outside_note = outside_vault / "Projects" / "Demo" / "note.md"
        outside_note.parent.mkdir(parents=True)
        outside_note.write_text("outside secret", encoding="utf-8")
        real_resolve = Path.resolve
        replaced = False

        def replace_if_container_is_resolved(path, strict=False):
            nonlocal replaced
            if not replaced and path == self.container:
                replaced = True
                self.vault.rename(moved_vault)
                os.symlink(outside_vault, self.vault)
            return real_resolve(path, strict=strict)

        with mock.patch.object(Path, "resolve", replace_if_container_is_resolved):
            result = obsidian_connector.read_note(self.repo, "note.md")

        self.assertFalse(replaced, "secure reads must not resolve the container after validation")
        self.assertEqual("trusted body", result["content"])

    def test_read_rejects_intermediate_vault_replacement_after_validation(self) -> None:
        (self.container / "note.md").write_text("trusted body", encoding="utf-8")
        moved_vault = self.vault.with_name("Knowledge-original")
        outside_vault = self.vault.with_name("Outside")
        outside_note = outside_vault / "Projects" / "Demo" / "note.md"
        outside_note.parent.mkdir(parents=True)
        outside_note.write_text("outside secret", encoding="utf-8")
        real_open = os.open
        replaced = False

        def replace_before_first_descriptor_open(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal replaced
            if not replaced:
                replaced = True
                self.vault.rename(moved_vault)
                os.symlink(outside_vault, self.vault)
            if dir_fd is None:
                return real_open(path, flags, mode)
            return real_open(path, flags, mode, dir_fd=dir_fd)

        with mock.patch.object(obsidian_connector.os, "open", replace_before_first_descriptor_open):
            with self.assertRaises(obsidian_connector.ConnectorError) as raised:
                obsidian_connector.read_note(self.repo, "note.md")

        self.assertTrue(replaced)
        self.assertEqual("BINDING_PATH_UNSAFE", raised.exception.code)

    def test_read_does_not_dispatch_cli_that_could_replace_identity(self) -> None:
        safe = self.container / "safe"
        safe.mkdir()
        (safe / "note.md").write_text("original body", encoding="utf-8")
        moved = self.container / "moved"
        cli = self.fake_cli(
            "import pathlib\n"
            f"safe = pathlib.Path({str(safe)!r})\n"
            f"moved = pathlib.Path({str(moved)!r})\n"
            "safe.rename(moved)\n"
            "safe.mkdir()\n"
            "(safe / 'note.md').write_text('replacement body', encoding='utf-8')\n"
            "print('unexpected cli content', end='')\n"
        )

        result = obsidian_connector.read_note(self.repo, "safe/note.md", cli_path=cli)

        self.assertEqual("original body", result["content"])
        self.assertFalse(moved.exists())


class TagTests(ConnectorTestCase):
    def test_tags_are_aggregated_only_from_project_notes(self) -> None:
        (self.container / "note.md").write_text(
            "---\ntags:\n  - architecture\n---\nBody #decision #architecture\n",
            encoding="utf-8",
        )
        outside = self.vault / "Personal"
        outside.mkdir()
        (outside / "private.md").write_text("#private", encoding="utf-8")

        result = obsidian_connector.tags(self.repo)

        self.assertEqual(
            [{"tag": "architecture", "count": 1}, {"tag": "decision", "count": 1}],
            result,
        )

    def test_tags_exclude_nested_runtime_subpath(self) -> None:
        binding_path = self.repo / ".hermes" / "obsidian.json"
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        binding["runtime_subpath"] = ".custom/runtime"
        binding_path.write_text(json.dumps(binding), encoding="utf-8")
        (self.container / "allowed.md").write_text("#public", encoding="utf-8")
        runtime_note = self.container / ".custom" / "runtime" / "STATE.md"
        runtime_note.parent.mkdir(parents=True)
        runtime_note.write_text("#runtime-secret", encoding="utf-8")

        result = obsidian_connector.tags(self.repo)

        self.assertEqual([{"tag": "public", "count": 1}], result)


class OpenTests(ConnectorTestCase):
    def test_open_is_disabled_because_cli_accepts_only_racy_lexical_paths(self) -> None:
        (self.container / "note.md").write_text("body", encoding="utf-8")
        calls = self.repo / "calls.jsonl"
        cli = self.fake_cli(
            "import pathlib\n"
            f"calls = pathlib.Path({str(calls)!r})\n"
            "calls.write_text('called', encoding='utf-8')\n"
        )

        with self.assertRaises(obsidian_connector.ConnectorError) as raised:
            obsidian_connector.open_note(self.repo, "note.md", cli_path=cli)

        self.assertEqual("OPEN_UNSUPPORTED", raised.exception.code)
        self.assertFalse(calls.exists())


class CliTests(ConnectorTestCase):
    def test_preflight_command_emits_machine_readable_status(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(RUNTIME / "obsidian_connector.py"),
                "preflight",
                "--repo",
                str(self.repo),
                "--cli",
                str(self.repo / "missing-obsidian"),
                "--json",
            ],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("READY_FILESYSTEM", json.loads(result.stdout)["status"])

    def test_discover_command_lists_cli_vault_candidates(self) -> None:
        cli = self.fake_cli(
            f"print('Knowledge\\t{self.vault}')\n"
        )
        result = subprocess.run(
            [
                sys.executable,
                str(RUNTIME / "obsidian_connector.py"),
                "discover",
                "--cli",
                cli,
                "--json",
            ],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            [{"name": "Knowledge", "path": str(self.vault)}],
            json.loads(result.stdout)["vaults"],
        )


if __name__ == "__main__":
    unittest.main()
