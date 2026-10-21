#!/usr/bin/env python3
"""Command line for the opt-in SDD validation gates: evaluate, history, verify, registry.

``evaluate`` runs one stage gate against a worker artifact in OBSERVE mode by default:
it records an auditable GateDecision and exits 0 whatever the verdict, so it never
blocks a workflow. ``--enforce`` makes a non-PASS verdict exit 3. Gate commands come
from ``policies/GATES.md`` (``--gates``); no AgentReviewer is wired from the command
line, so AGENT criteria are BLOCKED (``INDEPENDENT_REVIEWER``) unless a host program
supplies reviewers through ``gate_engine.GateEngine``.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import gate_engine
from gate_evidence import DecisionLog, EvidenceError
from gate_registry import GATE_STAGES, GateRegistry

ROWS = {"focused_tests": "Focused tests", "format": "Format", "analyze": "Analyze", "ci": "CI"}
DEFAULT_GATES = Path(__file__).resolve().parents[1] / "policies" / "GATES.md"


def gate_commands(path: Path) -> tuple[dict[str, str | None], dict[str, int]]:
    """Read the command and timeout of each gate row; UNCONFIGURED and NOT_APPLICABLE are None."""
    commands: dict[str, str | None] = dict.fromkeys(ROWS)
    timeouts: dict[str, int] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return commands, timeouts
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        for gate, label in ROWS.items():
            if cells[0] == label and commands[gate] is None and gate not in timeouts:
                match = re.match(r"^`([^`]+)`", cells[1])
                value = match.group(1).strip() if match else cells[1]
                commands[gate] = None if value.startswith(("UNCONFIGURED", "NOT_APPLICABLE")) else value
                seconds = re.match(r"^(\d+)\s*s", cells[3])
                if seconds:
                    timeouts[gate] = int(seconds.group(1))
    return commands, timeouts


def _read_json(path: str) -> object:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Evaluate SDD validation gates (observation mode by default).")
    commands = root.add_subparsers(dest="command", required=True)
    evaluate = commands.add_parser("evaluate", help="evaluate one stage gate and record the decision")
    evaluate.add_argument("--stage", required=True, choices=GATE_STAGES)
    evaluate.add_argument("--task", required=True, help="task or ticket id the decision belongs to")
    evaluate.add_argument("--artifact", required=True, help="worker result JSON (executor_result or review_result)")
    evaluate.add_argument("--store", required=True, help="private evidence and decision store")
    evaluate.add_argument("--repo", default=".", help="repository the commands run in")
    evaluate.add_argument("--gates", default=str(DEFAULT_GATES), help="policies/GATES.md with the gate commands")
    evaluate.add_argument("--context", help="JSON with check_commands, editable_paths and implementer")
    evaluate.add_argument("--enforce", action="store_true", help="exit 3 when the verdict is not PASS")
    history = commands.add_parser("history", help="list the recorded decisions of one gate and task")
    history.add_argument("--store", required=True)
    history.add_argument("--gate", required=True, help="gate id, e.g. sdd.test")
    history.add_argument("--task", required=True)
    verify = commands.add_parser("verify", help="check the hash chain of the decision log")
    verify.add_argument("--store", required=True)
    registry = commands.add_parser("registry", help="print the gate catalogue and its hash")
    for command in (evaluate, history, verify, registry):
        command.add_argument("--json", action="store_true", help="emit one JSON object")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "registry":
            registry = GateRegistry()
            payload = {"registry_sha256": registry.digest(), "gates": [registry.for_stage(stage) for stage in registry.stages()]}
            code = 0
        elif args.command == "verify":
            payload, code = {"status": "OK", "decisions": DecisionLog(args.store).verify()}, 0
        elif args.command == "history":
            payload = {"decisions": [{key: item[key] for key in ("decision_id", "generation", "verdict", "timestamp", "evaluator")}
                                     for item in DecisionLog(args.store).history(args.gate, args.task)]}
            code = 0
        else:
            commands, timeouts = gate_commands(Path(args.gates))
            context = _read_json(args.context) if args.context else {}
            engine = gate_engine.GateEngine(args.store, args.repo, commands=commands, timeouts=timeouts, reviewers=[],
                                            mode="ENFORCE" if args.enforce else "OBSERVE")
            payload = engine.evaluate(args.stage, args.task, _read_json(args.artifact), context=context if isinstance(context, dict) else {})
            code = 3 if payload["blocking"] else 0
    except (OSError, ValueError, KeyError, EvidenceError) as error:
        payload, code = {"status": "ERROR", "message": str(error)}, 2
    print(json.dumps(payload, sort_keys=True, ensure_ascii=False) if args.json else
          "\n".join(f"{key}: {payload[key]}" for key in sorted(payload)))
    return code


if __name__ == "__main__":
    sys.exit(main())
