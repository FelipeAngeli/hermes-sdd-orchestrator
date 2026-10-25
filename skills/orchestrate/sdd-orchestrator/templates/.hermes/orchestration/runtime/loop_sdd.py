"""SDD adapter: the delivery profiles of ``state_format`` as a LoopDefinition.

``build_definition`` turns a ticket into a chain SPECIFY → … → REVIEW using the
profile order from ``state_format.PROFILES`` and the per-stage timeouts and
write capability from ``executor_launch`` (EXECUTORS.md or its defaults). TEST
and REVIEW are verifier ``gate`` nodes whose functional failure reopens only
IMPLEMENT and what depends on it; writing stages are not idempotent, so an
interrupted IMPLEMENT is never re-run without proof or a human decision.

The adapter only builds definitions. Handlers that actually launch executors are
supplied by the host program; this module never dispatches anything.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import executor_launch
import state_format

SDD_STAGES = ("SPECIFY", "CLARIFY", "PLAN", "TASKS", "IMPLEMENT", "TEST", "REVIEW")
VERIFIER_STAGES = ("TEST", "REVIEW")
CORRECTION_TARGET = "IMPLEMENT"


def _stage_settings(policy_path: Path | None) -> dict[str, dict[str, Any]]:
    policy = executor_launch.load_policy(policy_path)
    return {stage: executor_launch.stage_settings(policy, stage, executor=None, model=None, timeout=None) for stage in SDD_STAGES}


def build_definition(*, ticket: str, objective: str, profile: str = "CODE", limits: dict[str, Any] | None = None,
                     policy_path: Path | None = None) -> dict[str, Any]:
    stages = [stage for stage in state_format.profile_path(profile) if stage in SDD_STAGES]
    settings = _stage_settings(policy_path)
    nodes: list[dict[str, Any]] = []
    previous: str | None = None
    for stage in stages:
        stage_settings = settings[stage]
        writes = executor_launch.is_writing_worker(stage_settings) and stage not in VERIFIER_STAGES
        node: dict[str, Any] = {
            "id": stage,
            "kind": "gate" if stage in VERIFIER_STAGES else "run-agent",
            "description": f"{stage} stage of {ticket}",
            "depends_on": [previous] if previous else [],
            "idempotent": not writes,
            "effect": "LOCAL" if writes else "NONE",
            "timeout_seconds": stage_settings["timeout_seconds"],
            "params": {"stage": stage, "executor": stage_settings["executor"], "model": stage_settings.get("model")},
        }
        if stage in VERIFIER_STAGES and CORRECTION_TARGET in stages:
            node["on_failure"] = {"reopen": CORRECTION_TARGET}
        nodes.append(node)
        previous = stage
    definition: dict[str, Any] = {
        "schema_version": 1, "loop_id": f"sdd-{ticket}", "objective": objective, "loop_type": "SDD_DELIVERY", "ticket": ticket,
        "nodes": nodes, "limits": {"max_correction_cycles": 3, "max_transient_retries": 2}, "policy": {"on_unrecoverable_failure": "ESCALATE"},
    }
    definition["limits"].update(limits or {})
    return definition
