"""The controller payload stays language-neutral.

Ecosystem knowledge belongs only in ``runtime/detect_stack.py`` and the reference
table of ``policies/GATES.md`` (CONTRIBUTING.md, docs/ARCHITECTURE.md). The
toolchain-name scan in ``test_sdd_orchestrator_skill`` cannot see language
one-liners such as a YAML or JSON parse command, so this test covers them in
every runtime module.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "skills" / "orchestrate" / "sdd-orchestrator" / "templates" / ".hermes" / "orchestration" / "runtime"
ONE_LINERS = re.compile(r"\bruby\b|\bnode -e\b|require\('fs'\)|yaml\.safe_load|YAML\.load", re.IGNORECASE)


class LanguageNeutralRuntimeTests(unittest.TestCase):
    def test_runtime_embeds_no_language_one_liner_outside_stack_detection(self) -> None:
        modules = sorted(path for path in RUNTIME.glob("*.py") if path.name != "detect_stack.py")
        self.assertTrue(modules)
        for path in modules:
            with self.subTest(path=path.name):
                self.assertIsNone(ONE_LINERS.search(path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
