"""Shared fixtures for the observability-engine tests."""
from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from loop_engine_fixtures import FakeClock, Recorder  # noqa: E402

import loop_engine  # noqa: E402

SECRET = "sk-ant-api03-" + "A" * 40


def snapshot(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file()}


class LoopFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = self.root / "loops"
        self.clock = FakeClock()
        self.rec = Recorder()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_loop(self, spec, handlers):
        engine = loop_engine.LoopEngine(self.store, handlers=handlers, clock=self.clock.time, sleep=self.clock.sleep)
        identifier = engine.start(spec)["execution_id"]
        engine.run(identifier)
        return identifier
