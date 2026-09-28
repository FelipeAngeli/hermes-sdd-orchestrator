"""Behavioral contracts for the repository release workflow.

The release tool is exercised only through its command-line interface against
throwaway Git repositories. Tests assert observable files, commits, tags,
output, and refusal codes rather than importing implementation helpers.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "orchestrate" / "sdd-orchestrator" / "SKILL.md"
CHANGELOG = ROOT / "CHANGELOG.md"
RELEASE = ROOT / "tools" / "release.py"
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
RELEASE_HEADING = re.compile(r"^## (\d+\.\d+\.\d+)(?: - ([^\n]+))?$", re.M)


def metadata_version(text: str) -> str:
    match = re.search(r"^version: (\S+)\s*$", text, re.M)
    if match is None:
        raise AssertionError("SKILL.md has no version metadata")
    return match.group(1)


def released_versions(text: str) -> list[str]:
    return [version for version, _ in RELEASE_HEADING.findall(text)]


def section_body(text: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}\s*\n(?P<body>.*?)(?=^## |\Z)", text, re.M | re.S)
    if match is None:
        raise AssertionError(f"missing changelog section: {heading}")
    return match.group("body").strip()


class RepositoryVersionContractTests(unittest.TestCase):
    def test_published_version_is_semver_and_matches_latest_changelog_release(self) -> None:
        version = metadata_version(SKILL.read_text(encoding="utf-8"))
        versions = released_versions(CHANGELOG.read_text(encoding="utf-8"))

        self.assertRegex(version, SEMVER)
        self.assertTrue(versions, "CHANGELOG.md must contain at least one release")
        self.assertEqual(version, versions[0])

    def test_changelog_keeps_unreleased_first_and_releases_unique_and_descending(self) -> None:
        text = CHANGELOG.read_text(encoding="utf-8")
        headings = re.findall(r"^## (.+)$", text, re.M)
        versions = released_versions(text)
        numeric = [tuple(map(int, version.split("."))) for version in versions]

        self.assertEqual("Unreleased", headings[0])
        self.assertEqual(sorted(numeric, reverse=True), numeric)
        self.assertEqual(len(set(versions)), len(versions))

    def test_contributor_documentation_states_the_observable_release_workflow(self) -> None:
        guide = (ROOT / "docs" / "maintaining-docs.md").read_text(encoding="utf-8")
        repository_rules = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

        obligations = {
            "branch workflow": ("## Branches and versions", "feat/", "fix/", "docs/"),
            "release command": ("tools/release.py", "--apply", "--tag"),
            "semantic levels": ("MAJOR", "MINOR", "PATCH"),
        }
        for obligation, markers in obligations.items():
            with self.subTest(obligation=obligation):
                for marker in markers:
                    self.assertIn(marker, guide)
        self.assertIn("One improvement, one branch", repository_rules)


class ReleaseCommandBehaviorTests(unittest.TestCase):
    def git(self, repo: Path, *args: str, check: bool = True) -> str:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        if check and result.returncode:
            self.fail(result.stdout + result.stderr)
        return result.stdout.strip()

    @contextmanager
    def release_repo(
        self,
        *,
        version: str = "1.2.3",
        unreleased: str = "### Added\n- New thing.\n",
        branch: str = "feat/new-thing",
    ) -> Iterator[Path]:
        with tempfile.TemporaryDirectory(prefix="release-behavior-") as temp:
            repo = Path(temp)
            self.git(repo, "init", "-q", "-b", "main")
            self.git(repo, "config", "user.email", "t@example.invalid")
            self.git(repo, "config", "user.name", "T")
            skill = repo / "skills" / "orchestrate" / "sdd-orchestrator" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(f"---\nname: x\nversion: {version}\n---\n", encoding="utf-8")
            (repo / "CHANGELOG.md").write_text(
                "# Changelog\n\nIntro.\n\n"
                f"## Unreleased\n\n{unreleased}\n"
                "## 1.2.3 - 2026-09-27\n\n### Fixed\n- Old.\n",
                encoding="utf-8",
            )
            self.git(repo, "add", "-A")
            self.git(repo, "commit", "-qm", "base")
            if branch != "main":
                self.git(repo, "switch", "-q", "-c", branch)
            yield repo

    def run_release(self, repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(RELEASE), "--repo", str(repo), *args],
            text=True,
            capture_output=True,
            timeout=30,
        )

    def repository_snapshot(self, repo: Path) -> dict[str, str]:
        return {
            "skill": (repo / "skills/orchestrate/sdd-orchestrator/SKILL.md").read_text(encoding="utf-8"),
            "changelog": (repo / "CHANGELOG.md").read_text(encoding="utf-8"),
            "head": self.git(repo, "rev-parse", "HEAD"),
            "tags": self.git(repo, "tag", "--list"),
            "status": self.git(repo, "status", "--porcelain"),
        }

    def assert_refused(self, result: subprocess.CompletedProcess[str], code: str) -> None:
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn(f"release: REFUSED {code}", result.stdout)

    def test_dry_run_reports_inferred_transition_without_mutating_repository(self) -> None:
        with self.release_repo() as repo:
            before = self.repository_snapshot(repo)

            result = self.run_release(repo)

            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn("DRY RUN 1.2.3 -> 1.3.0 (minor)", result.stdout)
            self.assertEqual(before, self.repository_snapshot(repo))

    def test_apply_rolls_release_and_commits_without_tagging(self) -> None:
        with self.release_repo() as repo:
            old_history = section_body((repo / "CHANGELOG.md").read_text(encoding="utf-8"), "1.2.3 - 2026-09-27")

            result = self.run_release(repo, "--apply", "--date", "2026-09-28")

            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertEqual(
                "1.3.0",
                metadata_version((repo / "skills/orchestrate/sdd-orchestrator/SKILL.md").read_text(encoding="utf-8")),
            )
            changelog = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
            self.assertEqual("", section_body(changelog, "Unreleased"))
            self.assertIn("### Added\n- New thing.", section_body(changelog, "1.3.0 - 2026-09-28"))
            self.assertEqual(old_history, section_body(changelog, "1.2.3 - 2026-09-27"))
            self.assertEqual("chore(release): v1.3.0", self.git(repo, "log", "-1", "--format=%s"))
            self.assertEqual("", self.git(repo, "tag", "--points-at", "HEAD"))
            self.assertEqual("", self.git(repo, "status", "--porcelain"))

    def test_changelog_heading_selects_semantic_version_level(self) -> None:
        cases = (
            ("Breaking", "2.0.0", "major"),
            ("Removed", "2.0.0", "major"),
            ("Added", "1.3.0", "minor"),
            ("Changed", "1.3.0", "minor"),
            ("Fixed", "1.2.4", "patch"),
            ("Docs", "1.2.4", "patch"),
        )
        for heading, expected_version, expected_level in cases:
            with self.subTest(heading=heading), self.release_repo(unreleased=f"### {heading}\n- Change.\n") as repo:
                result = self.run_release(repo)
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                self.assertIn(f"1.2.3 -> {expected_version} ({expected_level})", result.stdout)

    def test_explicit_level_overrides_changelog_inference(self) -> None:
        with self.release_repo(unreleased="### Fixed\n- Change.\n") as repo:
            result = self.run_release(repo, "--level", "major", "--apply", "--date", "2026-09-28")

            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            skill = (repo / "skills/orchestrate/sdd-orchestrator/SKILL.md").read_text(encoding="utf-8")
            changelog = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
            self.assertEqual("2.0.0", metadata_version(skill))
            self.assertIn("### Fixed\n- Change.", section_body(changelog, "2.0.0 - 2026-09-28"))
            self.assertEqual("chore(release): v2.0.0", self.git(repo, "log", "-1", "--format=%s"))
            self.assertEqual("", self.git(repo, "status", "--porcelain"))

    def test_tag_is_annotated_and_points_to_exact_reviewed_release_commit(self) -> None:
        with self.release_repo() as repo:
            self.assertEqual(0, self.run_release(repo, "--apply", "--date", "2026-09-28").returncode)
            reviewed_head = self.git(repo, "rev-parse", "HEAD")

            result = self.run_release(repo, "--tag")

            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertEqual("tag", self.git(repo, "cat-file", "-t", "v1.3.0"))
            self.assertEqual(reviewed_head, self.git(repo, "rev-parse", "v1.3.0^{}"))

    def test_release_refusals_use_stable_codes_and_leave_repository_unchanged(self) -> None:
        scenarios = (
            ("arbitrary branch", {"branch": "arbitrary"}, (), "BRANCH_NAME_INVALID"),
            ("protected branch", {"branch": "main"}, (), "BRANCH_NOT_ALLOWED"),
            ("invalid version", {"version": "1.2"}, (), "VERSION_INVALID"),
            ("empty unreleased", {"unreleased": ""}, (), "UNRELEASED_EMPTY"),
            ("invalid date", {}, ("--apply", "--date", "28-09-2026"), "DATE_INVALID"),
        )
        for name, fixture, arguments, code in scenarios:
            with self.subTest(scenario=name), self.release_repo(**fixture) as repo:
                before = self.repository_snapshot(repo)
                result = self.run_release(repo, *arguments)
                self.assert_refused(result, code)
                self.assertEqual(before, self.repository_snapshot(repo))

    def test_apply_refuses_dirty_worktree(self) -> None:
        with self.release_repo() as repo:
            (repo / "stray.txt").write_text("x", encoding="utf-8")

            result = self.run_release(repo, "--apply")

            self.assert_refused(result, "WORKTREE_DIRTY")
            self.assertEqual("?? stray.txt", self.git(repo, "status", "--porcelain"))

    def test_apply_refuses_when_target_tag_already_exists(self) -> None:
        with self.release_repo() as repo:
            self.git(repo, "tag", "v1.3.0")

            result = self.run_release(repo, "--apply")

            self.assert_refused(result, "TAG_EXISTS")
            skill = repo / "skills/orchestrate/sdd-orchestrator/SKILL.md"
            self.assertEqual("1.2.3", metadata_version(skill.read_text(encoding="utf-8")))

    def test_tag_refuses_when_head_changed_after_release_commit(self) -> None:
        with self.release_repo() as repo:
            self.assertEqual(0, self.run_release(repo, "--apply", "--date", "2026-09-28").returncode)
            self.git(repo, "commit", "--allow-empty", "-qm", "fix: after review")

            result = self.run_release(repo, "--tag")

            self.assert_refused(result, "HEAD_NOT_RELEASE")
            self.assertEqual("", self.git(repo, "tag", "--list", "v1.3.0"))


if __name__ == "__main__":
    unittest.main()
