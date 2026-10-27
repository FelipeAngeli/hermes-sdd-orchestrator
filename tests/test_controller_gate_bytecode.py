"""`sdd.py gate` never loads bytecode from the repository's ``__pycache__``.

A writing worker can forge a timestamp-based ``.pyc`` whose header copies its source's
mtime and size: the workspace audit leaves it out as a test-run artifact
(``runtime/audit_scope.py``), and an interpreter that reads the repository's cache would
run it instead of the source. These tests forge exactly that file with the real
interpreter in the installed fixture and run the gate through the printed controller
command; no network, no paid call.
"""
from __future__ import annotations

import importlib.util
import marshal
import shlex
import struct
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_controller_sdd_guards as guards  # noqa: E402

sys.path.insert(0, str(guards.e2e.SKILL_ROOT / "templates" / ".hermes" / "orchestration" / "runtime"))
import audit_scope  # noqa: E402

GATE = "import sys; sys.path.insert(0, 'src'); import feature"


class GateBytecodeTests(guards.SddGuardTests):
    """Reuses the installed guard fixture; runs only the bytecode scenarios."""

    def forge(self) -> Path:
        """A valid timestamp-based ``.pyc`` for ``src/feature.py`` whose code writes MARKER."""
        source = self.repo / "src" / "feature.py"
        info = source.stat()
        code = compile("open('MARKER', 'w').write('forged')\n", str(source), "exec")
        header = importlib.util.MAGIC_NUMBER + struct.pack("<III", 0, int(info.st_mtime) & 0xFFFFFFFF, info.st_size & 0xFFFFFFFF)
        target = self.repo / "src" / "__pycache__" / f"feature.{sys.implementation.cache_tag}.pyc"
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(header + marshal.dumps(code))
        return target

    def test_a_forged_pyc_the_audit_leaves_out_is_not_executed_by_the_gate(self) -> None:
        self.craft(stage="TEST")
        for name in ("PYTHONPYCACHEPREFIX", "PYTHONDONTWRITEBYTECODE"):
            self.env.pop(name, None)  # the controller's own environment carries neither
        self.set_gate_row("Focused tests", f"{shlex.quote(sys.executable)} -c {shlex.quote(GATE)}", 60)
        forged = self.forge()
        marker = self.repo / "MARKER"
        # The forged file is what the audit leaves out: a regular file matching its source.
        self.assertTrue(audit_scope.eligible(self.repo, forged.relative_to(self.repo).as_posix(), present=True))
        # Control: a Python with the controller's own environment runs the forged bytecode.
        subprocess.run([sys.executable, "-c", GATE], cwd=self.repo, env=self.env, check=True, capture_output=True)
        self.assertTrue(marker.exists(), "the forged .pyc must be loadable, or this test proves nothing")
        marker.unlink()
        code, result = self.call("gate", "--name", "focused_tests")
        self.assertEqual(0, code, result)
        self.assertEqual("PASS", result["status"])
        self.assertEqual(0, result["exit_code"])
        self.assertFalse(marker.exists(), "sdd.py gate loaded bytecode from the repository's __pycache__")
        self.assertEqual([forged], sorted((self.repo / "src" / "__pycache__").iterdir()), "the gate wrote bytecode into the repository")
        recorded = self.state()["gates"]["focused_tests"]
        self.assertEqual(("PASS", 0), (recorded["status"], recorded["exit_code"]))

    def test_a_failing_gate_still_fails_with_its_exit_code(self) -> None:
        self.craft(stage="TEST")
        self.set_gate_row("Focused tests", f"{shlex.quote(sys.executable)} -c {shlex.quote('raise SystemExit(3)')}", 60)
        code, result = self.call("gate", "--name", "focused_tests")
        self.assertNotEqual(0, code)
        self.assertEqual("FOCUSED_TESTS_FAILED", result["status"], result)
        self.assertEqual(("FAIL", 3), (self.state()["gates"]["focused_tests"]["status"], self.state()["gates"]["focused_tests"]["exit_code"]))


def load_tests(loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: str | None) -> unittest.TestSuite:
    """Only this module's scenarios; the inherited guard tests already run in their own module."""
    return unittest.TestSuite(GateBytecodeTests(name) for name in (
        "test_a_forged_pyc_the_audit_leaves_out_is_not_executed_by_the_gate",
        "test_a_failing_gate_still_fails_with_its_exit_code",
    ))


if __name__ == "__main__":
    unittest.main()
