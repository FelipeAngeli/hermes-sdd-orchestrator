"""Gate commands run with a fresh, gate-owned bytecode cache (``runtime/gate_env.py``).

``sdd.py gate`` and the opt-in ``gate_validator`` both build the gate's environment here:
the caller's environment plus a bytecode-cache prefix that is a fresh temporary
directory per gate run, deleted afterwards, and the "do not write bytecode" switch. A
child interpreter then neither loads a ``.pyc`` planted in the repository's cache nor
writes one there. The variable names live in ``detect_stack.py``.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from os import environ as process_environment
from pathlib import Path
from unittest import mock

ORCHESTRATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ORCHESTRATION / "runtime"))

import detect_stack  # noqa: E402
import gate_env  # noqa: E402

PROBE = ("import json, os, sys; print(json.dumps({'prefix': sys.pycache_prefix, 'dont_write': sys.dont_write_bytecode,"
         " 'inherited': os.getenv('SDD_GATE_ENV_PROBE'), 'exists': os.path.isdir(sys.pycache_prefix or '')}))")


class GateEnvTests(unittest.TestCase):
    def test_the_names_live_in_stack_detection(self) -> None:
        self.assertEqual("PYTHONPYCACHEPREFIX", detect_stack.BYTECODE_PREFIX_ENV)
        self.assertEqual({"PYTHONDONTWRITEBYTECODE": "1"}, detect_stack.BYTECODE_ISOLATION_ENV)

    def test_isolated_adds_both_variables_and_a_fresh_prefix_that_is_removed(self) -> None:
        base = {"PATH": "/usr/bin", "KEEP": "1", "PYTHONPYCACHEPREFIX": "/repo/shared-cache"}
        with gate_env.isolated(base) as first:
            prefix = Path(first["PYTHONPYCACHEPREFIX"])
            self.assertTrue(prefix.is_dir())
            self.assertEqual([], list(prefix.iterdir()), "the prefix starts empty")
            self.assertNotEqual("/repo/shared-cache", str(prefix), "an inherited prefix is replaced, never reused")
            self.assertEqual("1", first["PYTHONDONTWRITEBYTECODE"])
            self.assertEqual(("/usr/bin", "1"), (first["PATH"], first["KEEP"]))
            with gate_env.isolated(base) as second:
                self.assertNotEqual(first["PYTHONPYCACHEPREFIX"], second["PYTHONPYCACHEPREFIX"], "one prefix per gate run")
        self.assertFalse(prefix.exists(), "the prefix is deleted after the gate run")
        self.assertEqual("/repo/shared-cache", base["PYTHONPYCACHEPREFIX"], "the caller's mapping is not modified")

    def test_run_inherits_the_environment_and_redirects_bytecode_in_the_child(self) -> None:
        inherited = {key: value for key, value in process_environment.items()
                     if key not in {"PYTHONPYCACHEPREFIX", "PYTHONDONTWRITEBYTECODE"}}
        inherited["SDD_GATE_ENV_PROBE"] = "kept"
        with tempfile.TemporaryDirectory() as repo, mock.patch.dict(process_environment, inherited, clear=True):
            completed = gate_env.run([sys.executable, "-c", PROBE], cwd=Path(repo), timeout=60)
            self.assertNotIn("PYTHONPYCACHEPREFIX", process_environment, "the controller's own environment is not changed")
        self.assertEqual(0, completed.returncode, completed.stderr)
        seen = json.loads(completed.stdout)
        self.assertEqual("kept", seen["inherited"])
        self.assertTrue(seen["dont_write"])
        self.assertTrue(seen["exists"], "the prefix exists while the gate runs")
        self.assertFalse(str(Path(seen["prefix"])).startswith(str(Path(repo).resolve())), "the prefix is outside the repository")
        self.assertFalse(Path(seen["prefix"]).exists(), "the prefix is removed after the gate run")

    def test_the_gate_cache_variable_names_live_in_stack_detection(self) -> None:
        self.assertEqual(("MYPY_CACHE_DIR", "RUFF_CACHE_DIR"), detect_stack.GATE_CACHE_DIR_ENVS)

    def test_isolated_gives_each_gate_run_fresh_tool_caches_that_are_removed(self) -> None:
        base = {"PATH": "/usr/bin", "MYPY_CACHE_DIR": "/repo/.mypy_cache", "RUFF_CACHE_DIR": "/repo/.ruff_cache"}
        with gate_env.isolated(base) as first, gate_env.isolated(base) as second:
            caches = [Path(first[name]) for name in detect_stack.GATE_CACHE_DIR_ENVS]
            for name, cache in zip(detect_stack.GATE_CACHE_DIR_ENVS, caches):
                with self.subTest(variable=name):
                    self.assertTrue(cache.is_dir())
                    self.assertEqual([], list(cache.iterdir()), "the cache starts empty")
                    self.assertNotEqual(base[name], str(cache), "an inherited cache directory is replaced, never reused")
                    self.assertNotEqual(first[name], second[name], "one cache per gate run")
            self.assertEqual(len(set(caches) | {Path(first["PYTHONPYCACHEPREFIX"])}), 3, "each tool gets its own directory")
        for cache in caches:
            self.assertFalse(cache.exists(), "the cache is deleted after the gate run")
        self.assertEqual("/repo/.mypy_cache", base["MYPY_CACHE_DIR"], "the caller's mapping is not modified")

    def test_run_points_the_child_at_the_fresh_tool_caches(self) -> None:
        probe = "import json, os; print(json.dumps({n: os.getenv(n) for n in ('MYPY_CACHE_DIR', 'RUFF_CACHE_DIR')}))"
        with tempfile.TemporaryDirectory() as repo:
            completed = gate_env.run([sys.executable, "-c", probe], cwd=Path(repo), timeout=60)
        self.assertEqual(0, completed.returncode, completed.stderr)
        for name, value in json.loads(completed.stdout).items():
            with self.subTest(variable=name):
                self.assertTrue(value)
                self.assertFalse(str(Path(value)).startswith(str(Path(repo).resolve())), "the cache is outside the repository")
                self.assertFalse(Path(value).exists(), "the cache is removed after the gate run")

    def test_run_keeps_the_exit_code_output_and_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as repo:
            completed = gate_env.run([sys.executable, "-c", "import sys; print('out'); sys.stderr.write('err'); sys.exit(3)"],
                                     cwd=Path(repo), timeout=60)
            self.assertEqual((3, "out\n", "err"), (completed.returncode, completed.stdout, completed.stderr))
            with self.assertRaises(subprocess.TimeoutExpired):
                gate_env.run([sys.executable, "-c", "import time; time.sleep(5)"], cwd=Path(repo), timeout=0.5)


if __name__ == "__main__":
    unittest.main()
