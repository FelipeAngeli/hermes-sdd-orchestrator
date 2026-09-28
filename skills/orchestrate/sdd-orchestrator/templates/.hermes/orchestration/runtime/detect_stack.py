#!/usr/bin/env python3
"""Detect a repository's ecosystems and suggest gate commands from evidence.

Read-only and stdlib-only. Every ecosystem is reported with the files that
prove it, and a gate command is suggested only when a manifest, lockfile or
tool configuration supports it; otherwise it is ``None``. Suggestions are
never verified here: the controller must run each command once in the target
repository before writing it into ``policies/GATES.md``.

``{files}`` in a format command is a placeholder for the agent-owned changed
files, keeping the formatter restricted as ``GATES.md`` requires.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

MAX_DEPTH = 2
IGNORED_DIRS = frozenset({
    ".git", ".hg", ".svn", ".hermes", ".idea", ".vscode", "node_modules", ".venv", "venv", "env",
    "__pycache__", ".tox", ".nox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "vendor",
    "build", "dist", "out", "target", "bin", "obj", ".dart_tool", ".fvm", "Pods", ".gradle",
    ".next", ".nuxt", "coverage", ".terraform", "deps", "_build",
})
CI_MARKERS = (
    (".github/workflows", "github-actions"),
    (".gitlab-ci.yml", "gitlab-ci"),
    (".circleci/config.yml", "circleci"),
    ("azure-pipelines.yml", "azure-pipelines"),
    ("bitbucket-pipelines.yml", "bitbucket-pipelines"),
    ("Jenkinsfile", "jenkins"),
    (".buildkite", "buildkite"),
    (".travis.yml", "travis-ci"),
)
INSTRUCTION_FILES = (
    "AGENTS.md", "CLAUDE.md", ".cursorrules", ".github/copilot-instructions.md",
    "CONTRIBUTING.md", ".editorconfig", "docs/adr", "docs/ARCHITECTURE.md", "ARCHITECTURE.md",
)
NOTE = (
    "Suggestions are inferred from files, not executed. Verify each command in this repository "
    "before copying it into policies/GATES.md; a None gate has no evidence and must be configured by a human."
)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _gates(focused_tests: str | None = None, format_: str | None = None, analyze: str | None = None) -> dict[str, str | None]:
    return {"focused_tests": focused_tests, "format": format_, "analyze": analyze}


def _present(directory: Path, *names: str) -> list[str]:
    return [name for name in names if (directory / name).exists()]


def _node(directory: Path, evidence: list[str]) -> dict[str, Any]:
    lockfiles = (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("bun.lockb", "bun"), ("bun.lock", "bun"), ("package-lock.json", "npm"))
    manager = "npm"
    for lockfile, name in lockfiles:
        if (directory / lockfile).exists():
            manager = name
            evidence.append(lockfile)
            break
    try:
        scripts = json.loads(_read(directory / "package.json") or "{}").get("scripts") or {}
    except (json.JSONDecodeError, AttributeError):
        scripts = {}
    run = lambda *names: next((f"{manager} run {name}" for name in names if name in scripts), None)
    return {
        "package_manager": manager,
        "suggested_gates": _gates(run("test"), run("format", "fmt"), run("lint", "typecheck", "check")),
    }


def _python(directory: Path, evidence: list[str]) -> dict[str, Any]:
    config = "\n".join(_read(directory / name) for name in ("pyproject.toml", "setup.cfg", "tox.ini"))
    extra = _present(directory, "pytest.ini", "conftest.py", "ruff.toml", ".ruff.toml", "mypy.ini", ".flake8")
    evidence.extend(extra)
    has = lambda tool, *files: f"[tool.{tool}" in config or f"[{tool}" in config or any(name in extra for name in files)
    tests = "python -m pytest" if has("pytest", "pytest.ini", "conftest.py") or "[tool:pytest]" in config else None
    if has("ruff", "ruff.toml", ".ruff.toml"):
        fmt, analyze = "ruff format {files}", "ruff check ."
    else:
        fmt = "black {files}" if has("black") else None
        analyze = "mypy ." if has("mypy", "mypy.ini") else ("flake8" if has("flake8", ".flake8") else None)
    return {"suggested_gates": _gates(tests, fmt, analyze)}


def _dart(directory: Path, evidence: list[str]) -> dict[str, Any]:
    flutter = "sdk: flutter" in _read(directory / "pubspec.yaml")
    fvm = _present(directory, ".fvmrc", ".fvm")
    evidence.extend(fvm)
    prefix = "fvm " if fvm else ""
    tool = "flutter" if flutter else "dart"
    return {
        "flavor": tool,
        "suggested_gates": _gates(f"{prefix}{tool} test", f"{prefix}dart format {{files}}", f"{prefix}{tool} analyze"),
    }


def _gradle(directory: Path, evidence: list[str]) -> dict[str, Any]:
    wrapper = "./gradlew" if (directory / "gradlew").exists() else "gradle"
    if wrapper == "./gradlew":
        evidence.append("gradlew")
    build = _read(directory / "build.gradle.kts") + _read(directory / "build.gradle")
    fmt = f"{wrapper} spotlessApply" if "spotless" in build else (f"{wrapper} ktlintFormat" if "ktlint" in build else None)
    analyze = f"{wrapper} detekt" if "detekt" in build else None
    return {"suggested_gates": _gates(f"{wrapper} test", fmt, analyze)}


def _maven(directory: Path, evidence: list[str]) -> dict[str, Any]:
    wrapper = "./mvnw" if (directory / "mvnw").exists() else "mvn"
    if wrapper == "./mvnw":
        evidence.append("mvnw")
    pom = _read(directory / "pom.xml")
    return {"suggested_gates": _gates(
        f"{wrapper} test",
        f"{wrapper} spotless:apply" if "spotless" in pom else None,
        f"{wrapper} checkstyle:check" if "checkstyle" in pom else None,
    )}


def _ruby(directory: Path, evidence: list[str]) -> dict[str, Any]:
    extra = _present(directory, ".rspec", "spec", "Rakefile", ".rubocop.yml")
    evidence.extend(extra)
    tests = "bundle exec rspec" if {".rspec", "spec"} & set(extra) else ("bundle exec rake test" if "Rakefile" in extra else None)
    rubocop = ".rubocop.yml" in extra
    return {"suggested_gates": _gates(tests, "bundle exec rubocop -a {files}" if rubocop else None, "bundle exec rubocop" if rubocop else None)}


def _php(directory: Path, evidence: list[str]) -> dict[str, Any]:
    extra = _present(directory, "phpunit.xml", "phpunit.xml.dist", "phpstan.neon", "phpstan.neon.dist", ".php-cs-fixer.php", ".php-cs-fixer.dist.php")
    evidence.extend(extra)
    try:
        scripts = json.loads(_read(directory / "composer.json") or "{}").get("scripts") or {}
    except (json.JSONDecodeError, AttributeError):
        scripts = {}
    tests = "composer run test" if "test" in scripts else ("vendor/bin/phpunit" if any(n.startswith("phpunit") for n in extra) else None)
    fmt = "vendor/bin/php-cs-fixer fix {files}" if any(n.startswith(".php-cs-fixer") for n in extra) else None
    analyze = "vendor/bin/phpstan analyse" if any(n.startswith("phpstan") for n in extra) else None
    return {"suggested_gates": _gates(tests, fmt, analyze)}


def _elixir(directory: Path, evidence: list[str]) -> dict[str, Any]:
    credo = ":credo" in _read(directory / "mix.exs")
    return {"suggested_gates": _gates("mix test", "mix format {files}", "mix credo" if credo else "mix compile --warnings-as-errors")}


def _swift(directory: Path, evidence: list[str]) -> dict[str, Any]:
    extra = _present(directory, ".swiftlint.yml", ".swift-format", ".swiftformat")
    evidence.extend(extra)
    fmt = "swift-format -i {files}" if ".swift-format" in extra else ("swiftformat {files}" if ".swiftformat" in extra else None)
    return {"suggested_gates": _gates("swift test", fmt, "swiftlint" if ".swiftlint.yml" in extra else None)}


def _cmake(directory: Path, evidence: list[str]) -> dict[str, Any]:
    extra = _present(directory, ".clang-format", ".clang-tidy")
    evidence.extend(extra)
    return {"suggested_gates": _gates(
        "ctest --test-dir build --output-on-failure",
        "clang-format -i {files}" if ".clang-format" in extra else None,
        "clang-tidy {files}" if ".clang-tidy" in extra else None,
    )}


def _fixed(tests: str, fmt: str | None, analyze: str | None) -> Callable[[Path, list[str]], dict[str, Any]]:
    return lambda directory, evidence: {"suggested_gates": _gates(tests, fmt, analyze)}


# (ecosystem, manifest names or glob patterns, analyzer)
ECOSYSTEMS: tuple[tuple[str, tuple[str, ...], Callable[[Path, list[str]], dict[str, Any]]], ...] = (
    ("node", ("package.json",), _node),
    ("python", ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile"), _python),
    ("go", ("go.mod",), _fixed("go test ./...", "gofmt -w {files}", "go vet ./...")),
    ("rust", ("Cargo.toml",), _fixed("cargo test", "rustfmt {files}", "cargo clippy --all-targets")),
    ("jvm-maven", ("pom.xml",), _maven),
    ("jvm-gradle", ("build.gradle.kts", "build.gradle"), _gradle),
    ("ruby", ("Gemfile",), _ruby),
    ("php", ("composer.json",), _php),
    ("elixir", ("mix.exs",), _elixir),
    ("swift", ("Package.swift",), _swift),
    ("dotnet", ("*.csproj", "*.fsproj", "*.sln"), _fixed("dotnet test", "dotnet format --include {files}", "dotnet build -warnaserror")),
    ("cmake", ("CMakeLists.txt",), _cmake),
    ("dart", ("pubspec.yaml",), _dart),
)


def _directories(root: Path) -> list[Path]:
    found, frontier = [root], [root]
    for _ in range(MAX_DEPTH):
        nxt: list[Path] = []
        for directory in frontier:
            try:
                children = sorted(p for p in directory.iterdir() if p.is_dir() and not p.is_symlink())
            except OSError:
                continue
            nxt.extend(p for p in children if p.name not in IGNORED_DIRS and not p.name.startswith("."))
        found.extend(nxt)
        frontier = nxt
    return found


def _manifests(directory: Path, patterns: tuple[str, ...]) -> list[str]:
    hits: list[str] = []
    for pattern in patterns:
        if any(ch in pattern for ch in "*?["):
            hits.extend(sorted(p.name for p in directory.glob(pattern) if p.is_file()))
        elif (directory / pattern).is_file():
            hits.append(pattern)
    return hits


def detect(root: Path) -> dict[str, Any]:
    root = root.resolve()
    ecosystems: list[dict[str, Any]] = []
    for directory in _directories(root):
        relative = directory.relative_to(root).as_posix() or "."
        for name, patterns, analyze in ECOSYSTEMS:
            manifests = _manifests(directory, patterns)
            if not manifests:
                continue
            evidence = list(manifests)
            details = analyze(directory, evidence)
            ecosystems.append({"ecosystem": name, "path": relative, "evidence": evidence, **details})
    ci = sorted({provider for marker, provider in CI_MARKERS if (root / marker).exists()})
    instructions = [name for name in INSTRUCTION_FILES if (root / name).exists()]
    return {
        "status": "DETECTED_UNVERIFIED" if ecosystems else "UNKNOWN",
        "root": str(root),
        "ecosystems": ecosystems,
        "ci": ci,
        "instruction_files": instructions,
        "note": NOTE,
    }


def _text(report: dict[str, Any]) -> str:
    lines = [f"status: {report['status']}", f"ci: {', '.join(report['ci']) or 'none detected'}"]
    if report["instruction_files"]:
        lines.append(f"instructions: {', '.join(report['instruction_files'])}")
    for item in report["ecosystems"]:
        lines.append(f"\n[{item['ecosystem']}] {item['path']}  (evidence: {', '.join(item['evidence'])})")
        for gate, command in item["suggested_gates"].items():
            lines.append(f"  {gate:<14} {command or 'UNKNOWN — configure manually'}")
    lines.append(f"\n{report['note']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--target", default=".", help="repository root to inspect (default: current directory)")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable report")
    args = parser.parse_args(argv)
    target = Path(args.target).expanduser()
    if not target.is_dir():
        print(json.dumps({"status": "BLOCKED", "reason": "TARGET_NOT_DIRECTORY"}) if args.json else "BLOCKED: TARGET_NOT_DIRECTORY", file=sys.stderr)
        return 2
    report = detect(target)
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else _text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
