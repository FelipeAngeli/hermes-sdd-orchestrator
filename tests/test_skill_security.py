"""Public-CLI tests for the pinned Hermes skill-security gate."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "tools" / "check_skill_security.py"


class SkillSecurityGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.scanner = self.root / "scanner"
        (self.scanner / "tools").mkdir(parents=True)
        (self.scanner / "tools" / "skills_guard.py").write_text(textwrap.dedent('''\
            from dataclasses import dataclass
            SCANNER_VERSION = "test-guard"

            @dataclass
            class Result:
                verdict: str

            def scan_skill(path, source="community"):
                return Result((path / "VERDICT").read_text(encoding="utf-8").strip())

            def format_scan_report(result):
                return f"Verdict: {result.verdict.upper()}"
        '''), encoding="utf-8")
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.scanner)], check=True)
        subprocess.run(["git", "-C", str(self.scanner), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(self.scanner), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(self.scanner), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.scanner), "commit", "-qm", "scanner"], check=True)
        self.scanner_commit = subprocess.run(
            ["git", "-C", str(self.scanner), "rev-parse", "HEAD"],
            text=True, capture_output=True, check=True,
        ).stdout.strip()
        self.skill = self.root / "skill"
        self.skill.mkdir()

    def run_checker(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run([
            sys.executable, str(CHECKER),
            "--repo", str(self.root),
            "--skill", str(self.skill),
            "--hermes-root", str(self.scanner),
            "--expected-commit", self.scanner_commit,
            "--expected-version", "test-guard",
        ], text=True, capture_output=True, check=False)

    def test_non_safe_verdict_fails_closed(self) -> None:
        (self.skill / "VERDICT").write_text("caution", encoding="utf-8")

        result = self.run_checker()

        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("Verdict: CAUTION", result.stdout)
        self.assertIn("SKILL_SECURITY_BLOCKED", result.stderr)

    def test_modified_scanner_checkout_fails_before_import(self) -> None:
        (self.skill / "VERDICT").write_text("safe", encoding="utf-8")
        scanner_file = self.scanner / "tools" / "skills_guard.py"
        scanner_file.write_text(
            scanner_file.read_text(encoding="utf-8") + "\n# modified after the pinned commit\n",
            encoding="utf-8",
        )

        result = self.run_checker()

        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("scanner checkout is not clean", result.stderr)

    def test_assume_unchanged_cannot_hide_modified_scanner_bytes(self) -> None:
        (self.skill / "VERDICT").write_text("safe", encoding="utf-8")
        scanner_file = self.scanner / "tools" / "skills_guard.py"
        scanner_file.write_text(
            scanner_file.read_text(encoding="utf-8") + "\n# hidden modified bytes\n",
            encoding="utf-8",
        )
        subprocess.run([
            "git", "-C", str(self.scanner), "update-index", "--assume-unchanged",
            "tools/skills_guard.py",
        ], check=True)

        result = self.run_checker()

        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("scanner source differs from pinned commit", result.stderr)


if __name__ == "__main__":
    unittest.main()
