"""Shared fixtures for the loop-engine acceptance tests (controller-owned)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))


class FakeClock:
    """Deterministic clock: ``sleep`` advances time and records each delay."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def node(node_id: str, kind: str = "run-tool", **extra):
    value = {"id": node_id, "kind": kind}
    value.update(extra)
    return value


def definition(nodes, **extra):
    value = {
        "schema_version": 1,
        "loop_id": extra.pop("loop_id", "demo"),
        "objective": "Exercise the loop engine.",
        "nodes": nodes,
    }
    limits = {"backoff_base_seconds": 1, "backoff_max_seconds": 30}
    limits.update(extra.pop("limits", {}))
    value["limits"] = limits
    value.update(extra)
    return value


def ok(output=None, **extra):
    result = {"status": "SUCCEEDED", "output": output if output is not None else {}}
    result.update(extra)
    return result


def failed(failure_class: str, code: str, message: str, **extra):
    failure = {"class": failure_class, "code": code, "message": message}
    failure.update(extra)
    return {"status": "FAILED", "output": {}, "failure": failure}


class Recorder:
    """Handler factory that records every call and replays scripted results."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.contexts: dict[str, list] = {}

    def handler(self, name: str, results=None):
        script = list(results or [])

        def run(ctx):
            self.calls.append(name)
            self.contexts.setdefault(name, []).append(ctx)
            if script:
                item = script.pop(0)
                if isinstance(item, BaseException):
                    raise item
                if callable(item):
                    return item(ctx)
                return item
            return ok({"node": ctx.node["id"]})

        return run


def write_json(path: Path, value) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path
