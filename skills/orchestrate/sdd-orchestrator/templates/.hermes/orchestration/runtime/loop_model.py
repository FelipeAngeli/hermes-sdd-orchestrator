"""Loop-engine data model: definitions, states, Hermes mappings and graph checks.

A LoopDefinition is a closed JSON document (schemas/LOOP_DEFINITION_SCHEMA.json)
with nodes, dependencies, limits and policy. This module validates it, fills the
experimental defaults, enforces hard ceilings and orders the dependency graph.
It never performs I/O beyond reading its schemas.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
DEFINITION_SCHEMA_PATH = SCHEMAS / "LOOP_DEFINITION_SCHEMA.json"
EXECUTION_SCHEMA_PATH = SCHEMAS / "LOOP_EXECUTION_SCHEMA.json"

#: Node kinds implemented now: the ones the current SDD flow needs.
NODE_KINDS = ("run-agent", "run-tool", "gate", "wait")
#: Kinds accepted by the schema vocabulary but refused until they are implemented.
PLANNED_NODE_KINDS = ("run-loop", "fan-out", "collect", "branch")
EXECUTION_STATES = ("PENDING", "RUNNING", "WAITING_INPUT", "WAITING_APPROVAL", "RETRYING", "BLOCKED", "COMPLETED", "FAILED", "CANCELLED")
TERMINAL_STATES = ("COMPLETED", "FAILED", "CANCELLED")
APPROVAL_CATEGORIES = ("SECURITY", "EXTERNAL_MUTATION", "PROTECTED_FILE", "SCOPE", "HUMAN")
EFFECTS = ("NONE", "LOCAL", "EXTERNAL")

#: Each loop state expressed in the states Hermes already persists: the action
#: journal status(es) of the node's action and the STATE stage status.
HERMES_STATE_MAP = {
    "PENDING": {"journal": ["IDLE"], "stage_status": "RUNNING"},
    "RUNNING": {"journal": ["PREPARED", "DISPATCHED", "PROCESS_FINISHED", "ARTIFACT_READY", "VALIDATED", "STATE_COMMITTED"], "stage_status": "RUNNING"},
    "WAITING_INPUT": {"journal": ["IDLE"], "stage_status": "PAUSED"},
    "WAITING_APPROVAL": {"journal": ["IDLE"], "stage_status": "PAUSED"},
    "RETRYING": {"journal": ["INTERRUPTED", "IDLE"], "stage_status": "RUNNING"},
    "BLOCKED": {"journal": ["BLOCKED"], "stage_status": "BLOCKED"},
    "COMPLETED": {"journal": ["RELEASED", "IDLE"], "stage_status": "DONE"},
    "FAILED": {"journal": ["BLOCKED"], "stage_status": "BLOCKED"},
    "CANCELLED": {"journal": ["IDLE"], "stage_status": "PAUSED"},
}

#: Engine halt/outcome reasons and the registered Hermes stop reason each one maps to.
HERMES_STOP_REASON = {
    "COMPLETED": "DONE",
    "CANCELLED": "ABANDON_REQUESTED",
    "TRANSIENT_RETRIES_EXHAUSTED": "EXECUTOR_FAILED",
    "TIMEOUT_RETRIES_EXHAUSTED": "EXECUTOR_TIMEOUT",
    "UNKNOWN_FAILURE": "EXECUTOR_FAILED",
    "HANDLER_MISSING": "EXECUTOR_UNAVAILABLE",
    "FUNCTIONAL_FAILURE": "WORKER_BLOCKED",
    "EFFECT_AMBIGUOUS": "ACTION_RECOVERY_REQUIRED",
    "RUN_BUDGET_EXHAUSTED": "BUDGET_REACHED",
    "TOKEN_BUDGET_EXHAUSTED": "BUDGET_REACHED",
    "TIME_BUDGET_EXHAUSTED": "BUDGET_REACHED",
    "CORRECTION_BUDGET_EXHAUSTED": "RETRY_BUDGET_REACHED",
    "NO_PROGRESS": "NO_PROGRESS",
    "NO_NEW_HYPOTHESIS": "NO_NEW_HYPOTHESIS",
    "APPROVAL_REQUIRED": "HUMAN_APPROVAL_REQUIRED",
    "EXTERNAL_APPROVAL_REQUIRED": "EXTERNAL_MUTATION_REQUIRED",
    "APPROVAL_REJECTED": "HUMAN_DECISION_REQUIRED",
    "INPUT_REQUIRED": "CLARIFICATION_REQUIRED",
    "NO_RUNNABLE_NODE": "BLOCKED",
    "NODE_STILL_RUNNING": "EXECUTOR_TIMEOUT",
    "POLICY_DENIED": "ACTION_BLOCKED",
    "POLICY_APPROVAL_REQUIRED": "HUMAN_APPROVAL_REQUIRED",
}

#: Experimental defaults (owner request) and the hard ceilings a definition or raise can never exceed.
DEFAULT_LIMITS = {
    "max_correction_cycles": 3,
    "max_transient_retries": 2,
    "max_timeout_retries": 1,
    "max_node_runs": 100,
    "max_tokens": None,
    "max_wall_clock_seconds": None,
    "backoff_base_seconds": 2,
    "backoff_max_seconds": 60,
    "timeouts_by_kind": {"run-agent": 900, "run-tool": 300, "gate": 600, "wait": None},
}
LIMIT_CEILINGS = {
    "max_correction_cycles": 10,
    "max_transient_retries": 5,
    "max_timeout_retries": 3,
    "max_node_runs": 1000,
    "max_tokens": 1_000_000_000,
    "max_wall_clock_seconds": 86_400,
    "backoff_base_seconds": 300,
    "backoff_max_seconds": 600,
}
#: People allowed to approve a SECURITY gate unless the definition names others.
DEFAULT_SECURITY_APPROVERS = ("requester",)
RAISABLE_LIMITS = ("max_correction_cycles", "max_transient_retries", "max_timeout_retries", "max_node_runs", "max_tokens", "max_wall_clock_seconds")
TIMEOUT_CEILING_SECONDS = 7200


class LoopError(Exception):
    """A structured, actionable refusal; ``code`` is stable and documented."""

    def __init__(self, code: str, message: str, next_step: str = "") -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.next_step = next_step

    def payload(self) -> dict[str, str]:
        return {"status": "ERROR", "code": self.code, "message": self.message, "next_step": self.next_step}


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _schema(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def schema_errors(value: Any, path: Path) -> list[str]:
    validator = jsonschema.Draft202012Validator(_schema(path))
    errors = sorted(validator.iter_errors(value), key=lambda error: list(error.absolute_path))
    return [f"{'/'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}" for error in errors[:5]]


def _limits(raw: dict[str, Any]) -> dict[str, Any]:
    limits = copy.deepcopy(DEFAULT_LIMITS)
    timeouts = raw.get("timeouts_by_kind") or {}
    limits.update({key: value for key, value in raw.items() if key != "timeouts_by_kind"})
    limits["timeouts_by_kind"].update(timeouts)
    for key, ceiling in LIMIT_CEILINGS.items():
        if limits[key] is not None and limits[key] > ceiling:
            raise LoopError("LIMIT_ABOVE_CEILING", f"{key}={limits[key]} exceeds the hard ceiling {ceiling}.",
                            f"Use at most {ceiling} for {key}; ceilings are fixed in loop_model.LIMIT_CEILINGS.")
    for kind, seconds in limits["timeouts_by_kind"].items():
        if seconds is not None and seconds > TIMEOUT_CEILING_SECONDS:
            raise LoopError("LIMIT_ABOVE_CEILING", f"timeouts_by_kind.{kind}={seconds} exceeds {TIMEOUT_CEILING_SECONDS}.",
                            f"Use at most {TIMEOUT_CEILING_SECONDS} seconds.")
    if limits["backoff_max_seconds"] < limits["backoff_base_seconds"]:
        raise LoopError("DEFINITION_INVALID", "backoff_max_seconds is below backoff_base_seconds.", "Raise backoff_max_seconds.")
    return limits


def _find_cycle(nodes: dict[str, dict[str, Any]]) -> list[str] | None:
    colour: dict[str, int] = {}
    stack: list[str] = []

    def visit(name: str) -> list[str] | None:
        colour[name] = 1
        stack.append(name)
        for dependency in nodes[name]["depends_on"]:
            if colour.get(dependency) == 1:
                return stack[stack.index(dependency):] + [dependency]
            if colour.get(dependency) is None:
                found = visit(dependency)
                if found:
                    return found
        stack.pop()
        colour[name] = 2
        return None

    for name in sorted(nodes):
        if colour.get(name) is None:
            found = visit(name)
            if found:
                return found
    return None


def topological_order(nodes: dict[str, dict[str, Any]]) -> list[str]:
    """Kahn's order with ties broken by declaration order (stable, deterministic)."""
    declared = list(nodes)
    remaining = {name: set(node["depends_on"]) for name, node in nodes.items()}
    order: list[str] = []
    while remaining:
        ready = [name for name in declared if name in remaining and not remaining[name]]
        if not ready:
            raise LoopError("DEPENDENCY_CYCLE", "the dependency graph has a cycle.", "Remove the cycle.")
        for name in ready:
            order.append(name)
            del remaining[name]
            for dependencies in remaining.values():
                dependencies.discard(name)
    return order


def downstream(nodes: dict[str, dict[str, Any]], start: str) -> set[str]:
    """``start`` plus every node that depends on it, transitively."""
    found = {start}
    changed = True
    while changed:
        changed = False
        for name, node in nodes.items():
            if name not in found and found.intersection(node["depends_on"]):
                found.add(name)
                changed = True
    return found


def validate_definition(value: Any) -> dict[str, Any]:
    """Return the normalized definition or raise ``LoopError`` with a stable code."""
    errors = schema_errors(value, DEFINITION_SCHEMA_PATH)
    if errors:
        raise LoopError("DEFINITION_INVALID", "; ".join(errors), "Fix the definition so it matches schemas/LOOP_DEFINITION_SCHEMA.json.")
    spec = copy.deepcopy(value)
    spec.setdefault("loop_type", "GENERIC")
    spec.setdefault("policy", {})
    spec["policy"].setdefault("on_unrecoverable_failure", "ESCALATE")
    spec["policy"].setdefault("security_approvers", list(DEFAULT_SECURITY_APPROVERS))
    spec["limits"] = _limits(spec.get("limits") or {})
    nodes: dict[str, dict[str, Any]] = {}
    for raw in spec["nodes"]:
        if raw["id"] in nodes:
            raise LoopError("DUPLICATE_NODE_ID", f"node id {raw['id']!r} is declared twice.", "Give every node a unique id.")
        if raw["kind"] in PLANNED_NODE_KINDS:
            raise LoopError("UNSUPPORTED_NODE_KIND", f"{raw['id']}: kind {raw['kind']!r} is planned but not implemented.",
                            f"Use one of {', '.join(NODE_KINDS)}.")
        node = dict(raw)
        node.setdefault("depends_on", [])
        node.setdefault("idempotent", False)
        node.setdefault("effect", "LOCAL")
        node.setdefault("params", {})
        node.setdefault("approval", None)
        node.setdefault("on_failure", None)
        if "timeout_seconds" not in node:
            node["timeout_seconds"] = spec["limits"]["timeouts_by_kind"].get(node["kind"])
        if node["kind"] != "wait" and node["timeout_seconds"] is None:
            raise LoopError("TIMEOUT_REQUIRED", f"{node['id']}: a {node['kind']} node needs a finite timeout.",
                            "Set timeout_seconds or limits.timeouts_by_kind for its kind; only wait nodes may have none.")
        if node["kind"] == "wait" and node["effect"] == "EXTERNAL":
            raise LoopError("DEFINITION_INVALID", f"{node['id']}: a wait node has no effect.", "Remove effect from the wait node.")
        nodes[node["id"]] = node
    for name, node in nodes.items():
        if len(set(node["depends_on"])) != len(node["depends_on"]):
            raise LoopError("DEPENDENCY_INVALID", f"{name} lists a dependency twice.", "List each dependency once.")
        for dependency in node["depends_on"]:
            if dependency not in nodes:
                raise LoopError("DEPENDENCY_UNKNOWN", f"{name} depends on unknown node {dependency!r}.", "Declare the node or drop the dependency.")
    cycle = _find_cycle(nodes)
    if cycle:
        raise LoopError("DEPENDENCY_CYCLE", "cycle " + " -> ".join(cycle), "Remove one dependency of the cycle; loops repeat through on_failure.reopen, never through edges.")
    for name, node in nodes.items():
        target = (node["on_failure"] or {}).get("reopen")
        if target is not None and (target == name or name not in downstream(nodes, target)):
            raise LoopError("CORRECTION_TARGET_INVALID", f"{name}: reopen target {target!r} is not upstream of it.",
                            "A verifier may reopen only a node it depends on, directly or transitively.")
    spec["nodes"] = [nodes[name] for name in nodes]
    return spec


def node_map(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {node["id"]: node for node in spec["nodes"]}


def hermes_stop_reason(reason: str) -> str:
    return HERMES_STOP_REASON[reason]
