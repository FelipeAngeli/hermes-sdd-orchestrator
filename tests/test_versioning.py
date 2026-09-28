"""Versioning rules: one SemVer, one changelog section per release, one tag per version.

`tools/release.py` is the only supported way to cut a version. These tests pin
its behavior and the invariants between SKILL.md, CHANGELOG.md and the docs.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "orchestrate" / "sdd-orchestrator" / "SKILL.md"
CHANGELOG = ROOT / "CHANGELOG.md"
RELEASE = ROOT / "tools" / "release.py"
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")

sys.path.insert(0, str(ROOT / "tools"))
import release  # noqa: E402


def skill_version(text: str) -> str:
    return re.search(r"^version: (.+)$", text, re.M).group(1).strip()


class VersionInvariantTests(unittest.TestCase):
    def test_skill_version_is_semver(self) -> None:
        self.assertRegex(skill_version(SKILL.read_text(encoding="utf-8")), SEMVER)

    def test_latest_changelog_release_matches_skill_version(self) -> None:
        versions = release.changelog_versions(CHANGELOG.read_text(encoding="utf-8"))
        self.assertEqual(skill_version(SKILL.read_text(encoding="utf-8")), versions[0])

    def test_changelog_has_unreleased_section_on_top(self) -> None:
        headings = re.findall(r"^## (.+)$", CHANGELOG.read_text(encoding="utf-8"), re.M)
        self.assertEqual("Unreleased", headings[0])

    def test_changelog_versions_strictly_descend(self) -> None:
        versions = release.changelog_versions(CHANGELOG.read_text(encoding="utf-8"))
        keys = [tuple(map(int, v.split("."))) for v in versions]
        self.assertEqual(sorted(keys, reverse=True), keys)
        self.assertEqual(len(set(keys)), len(keys))

    def test_workflow_is_documented(self) -> None:
        text = (ROOT / "docs" / "maintaining-docs.md").read_text(encoding="utf-8")
        for marker in ("## Branches and versions", "tools/release.py", "feat/", "fix/", "docs/", "MAJOR", "MINOR", "PATCH"):
            with self.subTest(marker=marker):
                self.assertIn(marker, text)
        self.assertIn("One improvement, one branch", (ROOT / "AGENTS.md").read_text(encoding="utf-8"))


class BumpTests(unittest.TestCase):
    def test_bump_levels(self) -> None:
        self.assertEqual("4.0.0", release.bump("3.3.1", "major"))
        self.assertEqual("3.4.0", release.bump("3.3.1", "minor"))
        self.assertEqual("3.3.2", release.bump("3.3.1", "patch"))
        with self.assertRaises(release.ReleaseError):
            release.bump("3.3", "patch")

    def test_level_is_inferred_from_the_unreleased_section(self) -> None:
        self.assertEqual("major", release.infer_level("### Breaking\n- x\n### Added\n- y\n"))
        self.assertEqual("minor", release.infer_level("### Added\n- y\n### Fixed\n- z\n"))
        self.assertEqual("minor", release.infer_level("### Changed\n- y\n"))
        self.assertEqual("patch", release.infer_level("### Fixed\n- z\n"))
        self.assertEqual("patch", release.infer_level("### Docs\n- z\n"))
        with self.assertRaises(release.ReleaseError):
            release.infer_level("\n")


class ReleaseCliTests(unittest.TestCase):
    """End-to-end on a throwaway repository."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "T")
        skill = self.repo / "skills" / "orchestrate" / "sdd-orchestrator" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: x\nversion: 1.2.3\n---\n", encoding="utf-8")
        (self.repo / "CHANGELOG.md").write_text(
            "# Changelog\n\nIntro.\n\n## Unreleased\n\n### Added\n- New thing.\n\n## 1.2.3\n\n### Fixed\n- Old.\n",
            encoding="utf-8",
        )
        self.git("add", "-A")
        self.git("commit", "-qm", "base")
        self.git("switch", "-q", "-c", "feat/new-thing")

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.repo), *args], text=True, capture_output=True, check=True).stdout

    def run_release(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(RELEASE), "--repo", str(self.repo), *args],
            text=True, capture_output=True, timeout=30,
        )

    def test_dry_run_reports_without_writing(self) -> None:
        result = self.run_release()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("1.2.3 -> 1.3.0", result.stdout)
        self.assertIn("version: 1.2.3", (self.repo / "skills/orchestrate/sdd-orchestrator/SKILL.md").read_text())

    def test_apply_bumps_skill_rolls_changelog_commits_and_tags(self) -> None:
        result = self.run_release("--apply", "--date", "2026-09-28")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("version: 1.3.0", (self.repo / "skills/orchestrate/sdd-orchestrator/SKILL.md").read_text())
        changelog = (self.repo / "CHANGELOG.md").read_text()
        self.assertIn("## Unreleased\n\n## 1.3.0 - 2026-09-28\n\n### Added\n- New thing.\n\n## 1.2.3", changelog)
        self.assertEqual("chore(release): v1.3.0", self.git("log", "-1", "--format=%s").strip())
        self.assertEqual("v1.3.0", self.git("tag", "--points-at", "HEAD").strip())
        self.assertEqual("", self.git("status", "--porcelain"))

    def test_explicit_level_overrides_inference(self) -> None:
        result = self.run_release("--level", "major", "--apply", "--date", "2026-09-28")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("v2.0.0", self.git("tag", "--points-at", "HEAD").strip())

    def test_refuses_to_release_from_main(self) -> None:
        self.git("switch", "-q", "main")
        result = self.run_release("--apply")
        self.assertEqual(1, result.returncode)
        self.assertIn("BRANCH", result.stdout)

    def test_refuses_empty_unreleased_section(self) -> None:
        (self.repo / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n\n## 1.2.3\n\n- Old.\n", encoding="utf-8")
        self.git("commit", "-qam", "empty")
        result = self.run_release("--apply")
        self.assertEqual(1, result.returncode)
        self.assertIn("UNRELEASED_EMPTY", result.stdout)

    def test_refuses_dirty_worktree(self) -> None:
        (self.repo / "stray.txt").write_text("x", encoding="utf-8")
        result = self.run_release("--apply")
        self.assertEqual(1, result.returncode)
        self.assertIn("WORKTREE_DIRTY", result.stdout)

    def test_refuses_existing_tag(self) -> None:
        self.git("tag", "v1.3.0")
        result = self.run_release("--apply")
        self.assertEqual(1, result.returncode)
        self.assertIn("TAG_EXISTS", result.stdout)


if __name__ == "__main__":
    unittest.main()
