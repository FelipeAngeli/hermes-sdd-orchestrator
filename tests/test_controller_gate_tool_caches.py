"""`sdd.py gate` never reads a type-checker or linter cache planted in the repository.

``sdd.py gate`` runs ``mypy``/``ruff`` on the host, and both tools trust their incremental
cache: a file whose recorded mtime and size match is not re-checked. A writing worker can
plant (or keep) a ``.mypy_cache/``/``.ruff_cache/`` built from a clean version of a file,
then write an erroneous version with the same size and mtime; a tool that reads that cache
reports success. These tests do exactly that with the **real** tools in an installed
fixture and run the gate through the controller: the gate must still fail on the error,
and the repository cache must be neither read into a pass nor written.

The tools are looked up in ``SDD_GATE_TOOL_BIN`` (a directory) and then on ``PATH``; a
missing tool skips its test. No network, no paid call.
"""
from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import subprocess
import sys
import unittest
from os import environ as process_environment
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_controller_sdd_guards as guards  # noqa: E402

CACHE_ENVS = ("MYPY_CACHE_DIR", "RUFF_CACHE_DIR")
#: Clean and erroneous versions of the gated file, with the same byte size.
CASES = {
    "ruff": (".ruff_cache", "y = 12345\n", "import os\n", ("check", "--isolated", "--no-fix")),
    "mypy": (".mypy_cache", "x: int = 12345\n", 'x: int = "abc"\n', ("--no-error-summary",)),
}


def tool(name: str) -> str | None:
    folder = process_environment.get("SDD_GATE_TOOL_BIN")
    candidate = Path(folder) / name if folder else None
    if candidate is not None and candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    return shutil.which(name)


class GateToolCacheTests(guards.SddGuardTests):
    """Reuses the installed guard fixture; runs only the cache-poisoning scenarios."""

    def tree(self, root: Path) -> dict[str, tuple[str, int]]:
        if not root.exists():
            return {}
        return {path.relative_to(root).as_posix(): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
                for path in sorted(root.rglob("*")) if path.is_file()}

    def poison(self, name: str) -> tuple[list[str], Path]:
        """Build the repository cache from the clean file, then swap in the error with the same size and mtime."""
        binary = tool(name)
        if binary is None:
            self.skipTest(f"{name} is not installed (set SDD_GATE_TOOL_BIN)")
        cache, clean, broken, options = CASES[name]
        target = self.repo / "src" / "feature.py"
        argv = [binary, *options, "src/feature.py"]
        env = {key: value for key, value in self.env.items() if key not in CACHE_ENVS}
        target.write_text(clean, encoding="utf-8")
        stamp = target.stat().st_mtime_ns
        warm = subprocess.run(argv, cwd=self.repo, env=env, capture_output=True, text=True, check=False)
        self.assertEqual(0, warm.returncode, warm.stdout + warm.stderr)
        self.assertTrue((self.repo / cache).is_dir(), f"{name} did not create {cache}")
        target.write_text(broken, encoding="utf-8")
        self.assertEqual(len(clean.encode()), target.stat().st_size)
        os.utime(target, ns=(stamp, stamp))
        poisoned = subprocess.run(argv, cwd=self.repo, env=env, capture_output=True, text=True, check=False)
        self.assertEqual(0, poisoned.returncode, f"the planted {cache} must hide the error, or this test proves nothing")
        return argv, self.repo / cache

    def gate_ignores_the_planted_cache(self, name: str) -> None:
        self.craft(stage="TEST")
        for variable in CACHE_ENVS:
            self.env.pop(variable, None)  # the controller's own environment carries neither
        argv, cache = self.poison(name)
        before = self.tree(cache)
        self.set_gate_row("Analyze", " ".join(shlex.quote(part) for part in argv), 120)
        code, result = self.call("gate", "--name", "analyze")
        self.assertNotEqual(0, code, result)
        self.assertEqual("ANALYZE_FAILED", result["status"], result)
        self.assertEqual(before, self.tree(cache), f"sdd.py gate wrote the repository's {cache.name}")
        recorded = self.state()["gates"]["analyze"]
        self.assertEqual(("FAIL", 1), (recorded["status"], recorded["exit_code"]))

    def test_a_planted_ruff_cache_does_not_hide_a_lint_error_from_the_gate(self) -> None:
        self.gate_ignores_the_planted_cache("ruff")

    def test_a_planted_mypy_cache_does_not_hide_a_type_error_from_the_gate(self) -> None:
        self.gate_ignores_the_planted_cache("mypy")


def load_tests(loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: str | None) -> unittest.TestSuite:
    """Only this module's scenarios; the inherited guard tests already run in their own module."""
    return unittest.TestSuite(GateToolCacheTests(name) for name in (
        "test_a_planted_ruff_cache_does_not_hide_a_lint_error_from_the_gate",
        "test_a_planted_mypy_cache_does_not_hide_a_type_error_from_the_gate",
    ))


if __name__ == "__main__":
    unittest.main()
