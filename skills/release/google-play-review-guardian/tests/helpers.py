"""Shared test helpers: puts scripts/ and tests/ on sys.path, builds the finding factory."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TESTS_DIR.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
for p in (str(SCRIPTS_DIR), str(TESTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import common  # noqa: E402

RULES = common.load_rules()
F = common.FindingFactory(RULES)


def no_tools():
    names = ("java", "aapt2", "zipalign", "apksigner", "apkanalyzer", "bundletool", "llvm-readelf", "adb")
    return {n: common.ToolInfo(n, None, False, "mock: ausente") for n in names}


def by_rule(findings, rule_id):
    return [f for f in findings if f.rule_id == rule_id]


def sev(findings, rule_id):
    return {f.severity for f in findings if f.rule_id == rule_id}


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gprg-test-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def load_schema():
    return json.loads((SKILL_DIR / "schemas/audit-report.schema.json").read_text(encoding="utf-8"))
