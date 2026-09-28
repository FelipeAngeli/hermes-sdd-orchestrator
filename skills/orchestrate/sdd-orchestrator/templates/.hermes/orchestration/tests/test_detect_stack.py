"""Tests for detect_stack: evidence-based, language-agnostic gate suggestions."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))

import detect_stack  # noqa: E402

SCRIPT = RUNTIME / "detect_stack.py"


class DetectStackTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def write(self, relative: str, content: str = "") -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def ecosystems(self) -> dict[str, dict]:
        return {item["ecosystem"]: item for item in detect_stack.detect(self.root)["ecosystems"]}

    def test_empty_repository_detects_nothing_and_invents_no_command(self) -> None:
        report = detect_stack.detect(self.root)
        self.assertEqual([], report["ecosystems"])
        self.assertEqual("UNKNOWN", report["status"])

    def test_every_ecosystem_cites_the_manifest_that_proves_it(self) -> None:
        manifests = {
            "package.json": "node",
            "pyproject.toml": "python",
            "go.mod": "go",
            "Cargo.toml": "rust",
            "pom.xml": "jvm-maven",
            "build.gradle.kts": "jvm-gradle",
            "Gemfile": "ruby",
            "composer.json": "php",
            "mix.exs": "elixir",
            "Package.swift": "swift",
            "App.csproj": "dotnet",
            "CMakeLists.txt": "cmake",
            "pubspec.yaml": "dart",
        }
        for name in manifests:
            self.write(name, "{}" if name.endswith(".json") else "")
        found = self.ecosystems()
        self.assertEqual(set(manifests.values()), set(found))
        for manifest, ecosystem in manifests.items():
            with self.subTest(ecosystem=ecosystem):
                self.assertIn(manifest, found[ecosystem]["evidence"])
                for gate in ("focused_tests", "format", "analyze"):
                    self.assertIn(gate, found[ecosystem]["suggested_gates"])

    def test_node_package_manager_follows_the_lockfile_and_declared_scripts(self) -> None:
        self.write("package.json", json.dumps({"scripts": {"test": "vitest", "lint": "eslint ."}}))
        self.write("pnpm-lock.yaml")
        node = self.ecosystems()["node"]
        self.assertEqual("pnpm", node["package_manager"])
        self.assertIn("pnpm-lock.yaml", node["evidence"])
        self.assertEqual("pnpm run test", node["suggested_gates"]["focused_tests"])
        self.assertEqual("pnpm run lint", node["suggested_gates"]["analyze"])
        self.assertIsNone(node["suggested_gates"]["format"])

    def test_undeclared_node_script_is_left_unknown_not_guessed(self) -> None:
        self.write("package.json", json.dumps({"scripts": {}}))
        gates = self.ecosystems()["node"]["suggested_gates"]
        self.assertEqual({"focused_tests": None, "format": None, "analyze": None}, gates)

    def test_flutter_and_fvm_are_distinguished_from_plain_dart(self) -> None:
        self.write("pubspec.yaml", "dependencies:\n  flutter:\n    sdk: flutter\n")
        self.write(".fvmrc", "{}")
        dart = self.ecosystems()["dart"]
        self.assertEqual("flutter", dart["flavor"])
        self.assertEqual("fvm flutter test", dart["suggested_gates"]["focused_tests"])
        self.assertIn(".fvmrc", dart["evidence"])

    def test_python_tools_come_from_configuration_evidence(self) -> None:
        self.write("pyproject.toml", "[tool.ruff]\nline-length = 100\n[tool.pytest.ini_options]\n")
        gates = self.ecosystems()["python"]["suggested_gates"]
        self.assertEqual("python -m pytest", gates["focused_tests"])
        self.assertEqual("ruff format {files}", gates["format"])
        self.assertEqual("ruff check .", gates["analyze"])

    def test_ci_providers_are_reported(self) -> None:
        self.write(".github/workflows/ci.yml", "on: push\n")
        self.write(".gitlab-ci.yml", "")
        self.assertEqual({"github-actions", "gitlab-ci"}, set(detect_stack.detect(self.root)["ci"]))

    def test_monorepo_members_one_level_deep_are_detected_with_their_path(self) -> None:
        self.write("apps/web/package.json", json.dumps({"scripts": {"test": "jest"}}))
        self.write("services/api/go.mod", "module x\n")
        report = detect_stack.detect(self.root)
        paths = {(item["ecosystem"], item["path"]) for item in report["ecosystems"]}
        self.assertEqual({("node", "apps/web"), ("go", "services/api")}, paths)

    def test_vendored_and_dependency_directories_are_ignored(self) -> None:
        self.write("node_modules/pkg/package.json", "{}")
        self.write(".venv/lib/pyproject.toml", "")
        self.assertEqual([], detect_stack.detect(self.root)["ecosystems"])

    def test_suggestions_are_never_presented_as_verified(self) -> None:
        self.write("go.mod", "module x\n")
        report = detect_stack.detect(self.root)
        self.assertEqual("DETECTED_UNVERIFIED", report["status"])
        self.assertIn("verify", report["note"].lower())

    def test_cli_emits_json(self) -> None:
        self.write("Cargo.toml", "[package]\n")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--target", str(self.root), "--json"],
            text=True, capture_output=True, timeout=30, check=True,
        )
        self.assertEqual("rust", json.loads(result.stdout)["ecosystems"][0]["ecosystem"])


if __name__ == "__main__":
    unittest.main()
