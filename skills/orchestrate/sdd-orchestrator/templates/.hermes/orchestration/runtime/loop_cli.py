#!/usr/bin/env python3
"""Command line for the opt-in loop engine: validate, sdd-definition, status, cancel, resolve.

The CLI never runs a node: running needs handlers, which only a host program
registers (``loop_engine.LoopEngine(store, handlers=...)``). Every command
prints one JSON object with ``--json``; errors exit 2 with ``code`` and
``next_step``, usage errors exit 1.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import loop_control
import loop_model
import loop_sdd
import loop_state
from loop_store import LoopStore


def _emit(value: dict, as_json: bool) -> None:
    print(json.dumps(value, sort_keys=True, ensure_ascii=False) if as_json else
          "\n".join(f"{key}: {value[key]}" for key in sorted(value)))


def _read_json(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise loop_model.LoopError("DEFINITION_UNREADABLE", f"{path}: {error}", "Pass a readable JSON file.") from error


def _command(args: argparse.Namespace) -> dict:
    if args.command == "validate":
        spec = loop_model.validate_definition(_read_json(args.definition))
        return {"status": "VALID", "loop_id": spec["loop_id"], "nodes": [node["id"] for node in spec["nodes"]],
                "order": loop_model.topological_order(loop_model.node_map(spec)), "limits": spec["limits"],
                "definition_sha256": loop_model.digest(spec)}
    if args.command == "sdd-definition":
        return loop_sdd.build_definition(ticket=args.ticket, objective=args.objective, profile=args.profile,
                                         policy_path=Path(args.executors) if args.executors else None)
    if args.command == "status":
        if not args.execution:
            raise loop_model.LoopError("EXECUTION_REQUIRED", "status needs --execution.", "Pass --execution <id>.")
        return loop_state.summary(LoopStore(args.store).load(args.execution))
    if args.command == "cancel":
        return loop_control.request_cancel(args.store, args.execution, by=args.by, reason=args.reason)
    return loop_control.resolve(args.store, args.execution, node=args.node, decision=args.decision, value=args.value,
                                by=args.by, quote=args.quote, actor="human")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Inspect and control loop-engine executions without running nodes.")
    commands = root.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="validate a LoopDefinition and print its order and limits")
    validate.add_argument("--definition", required=True, help="LoopDefinition JSON file")
    sdd = commands.add_parser("sdd-definition", help="print the LoopDefinition of an SDD delivery profile")
    sdd.add_argument("--ticket", required=True)
    sdd.add_argument("--objective", required=True)
    sdd.add_argument("--profile", default="CODE", choices=("CODE", "DECISION_DOC"))
    sdd.add_argument("--executors", help="policies/EXECUTORS.md to read timeouts from (defaults when absent)")
    status = commands.add_parser("status", help="print one execution's status, pending decision and outcome")
    status.add_argument("--store", required=True)
    status.add_argument("--execution")
    cancel = commands.add_parser("cancel", help="cancel an execution (or request it from the active runner)")
    cancel.add_argument("--store", required=True)
    cancel.add_argument("--execution", required=True)
    cancel.add_argument("--by", required=True)
    cancel.add_argument("--reason", required=True)
    resolve = commands.add_parser("resolve", help="record one human decision for the pending halt")
    resolve.add_argument("--store", required=True)
    resolve.add_argument("--execution", required=True)
    resolve.add_argument("--node")
    resolve.add_argument("--decision", required=True, choices=loop_control.DECISIONS)
    resolve.add_argument("--value")
    resolve.add_argument("--by", required=True, help="the person deciding")
    resolve.add_argument("--quote", required=True, help="the person's literal words")
    for command in (validate, sdd, status, cancel, resolve):
        command.add_argument("--json", action="store_true", help="emit one JSON object")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = _command(args)
    except loop_model.LoopError as error:
        payload = error.payload()
        _emit(payload, args.json)
        return 1 if error.code == "EXECUTION_REQUIRED" else 2
    _emit(result, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
