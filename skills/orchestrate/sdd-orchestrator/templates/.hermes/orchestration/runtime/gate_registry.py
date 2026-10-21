"""GateRegistry: the catalogue of validation gates, one per SDD stage, with explicit criteria.

Each criterion has a stable ``id``, a ``description`` (the only text an AgentReviewer
sees), an ``evaluator`` (``DETERMINISTIC`` runs code or real commands; ``AGENT`` asks
an independent reviewer) and ``on_fail`` (``FAIL`` for a defect, ``BLOCKED`` for a
condition only a human or missing configuration can resolve). Automated tests are
always DETERMINISTIC: no model judgement replaces them.

The registry is data, not policy: it never blocks anything by itself. ``gate_engine``
reads it; its canonical hash (``digest``) is part of every decision's idempotency key,
so changing a criterion produces a new decision instead of reusing an old one.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

REGISTRY_VERSION = 2
EVALUATORS = ("DETERMINISTIC", "AGENT")
GATE_STAGES = ("SPECIFY", "CLARIFY", "PLAN", "TASKS", "IMPLEMENT", "TEST", "REVIEW")


def _criterion(identifier: str, description: str, evaluator: str = "DETERMINISTIC", on_fail: str = "FAIL") -> dict[str, str]:
    return {"id": identifier, "description": description, "evaluator": evaluator, "on_fail": on_fail}


_SCHEMA = _criterion("SCHEMA_VALID", "The stage artifact matches its JSON Schema.")
_NO_BLOCKERS = _criterion("NO_BLOCKERS", "The artifact reports no blockers.", on_fail="BLOCKED")
_INDEPENDENT = _criterion("INDEPENDENT_REVIEWER", "At least one reviewer is not the implementer.", on_fail="BLOCKED")

GATES: dict[str, list[dict[str, str]]] = {
    "SPECIFY": [
        _SCHEMA,
        _criterion("SCOPE_STATED", "The artifact states the scope in a non-empty summary."),
        _criterion("ACCEPTANCE_CRITERIA_DEFINED", "At least one acceptance check has a criterion and a verification method."),
        _NO_BLOCKERS,
    ],
    "CLARIFY": [
        _SCHEMA,
        _criterion("NO_OPEN_MATERIAL_QUESTIONS", "No material question is left unresolved.", on_fail="BLOCKED"),
        _criterion("MATERIAL_ASSUMPTIONS_VALIDATED", "Every material assumption records how it was validated.", on_fail="BLOCKED"),
        _NO_BLOCKERS,
    ],
    "PLAN": [
        _SCHEMA,
        _criterion("IMPACT_FILES_EXIST", "Every impact file named by the plan exists in the repository."),
        _criterion("DECISIONS_RECORDED", "The plan records its decisions, dependencies and risks."),
        _INDEPENDENT,
        _criterion("FEASIBILITY", "Given the recorded decisions and impact files, the plan is feasible and its "
                                  "dependencies and risks are addressed.", evaluator="AGENT"),
        _NO_BLOCKERS,
    ],
    "TASKS": [
        _SCHEMA,
        _criterion("TASKS_DEFINED", "The artifact lists at least one task."),
        _criterion("CHECKS_TRACED_TO_SLICES", "Every acceptance check is assigned to a slice."),
        _criterion("CHECKS_HAVE_VERIFIERS", "Every AGENT check names a verifier command in backticks."),
        _NO_BLOCKERS,
    ],
    "IMPLEMENT": [
        _SCHEMA,
        _criterion("CLAIMS_MATCH_WORKSPACE", "The paths the worker reports as modified or created are exactly the files changed in the workspace."),
        _criterion("WITHIN_EDITABLE_PATHS", "Every changed file is inside the slice's editable paths."),
        _criterion("STATIC_ANALYSIS", "The configured static-analysis command passes."),
        _NO_BLOCKERS,
    ],
    "TEST": [
        _SCHEMA,
        _criterion("TESTS_CONFIGURED", "A focused-tests command is configured.", on_fail="BLOCKED"),
        _criterion("TESTS_EXIST", "Every file the test command names exists."),
        _criterion("TESTS_PASS", "The focused-tests command, run by the gate, exits 0."),
        _criterion("EVIDENCE_REPRODUCED", "Every command the worker reports as passing passes when the gate re-runs it."),
        _criterion("CHECKS_COVERED", "Every AGENT acceptance check has a controller-bound verifier or a controller-recorded waiver."),
        _NO_BLOCKERS,
    ],
    "REVIEW": [
        _criterion("SCHEMA_VALID", "The review artifact matches its JSON Schema."),
        _criterion("REVIEW_APPROVED", "The review is APPROVED with no blocking finding."),
        _criterion("RECORDED_GATES_PASS", "Focused tests, format and analysis are recorded as PASS."),
        _criterion("BASELINE_AND_OWNERSHIP", "The baseline is preserved and ownership is valid."),
        _criterion("TESTS_PASS", "The focused-tests command, re-run by the gate at review, exits 0."),
        _criterion("STATIC_ANALYSIS", "The configured static-analysis command, re-run by the gate at review, passes."),
        _INDEPENDENT,
        _criterion("FUNCTIONAL_ADHERENCE", "The change does what the acceptance criteria require, per the cited evidence.", evaluator="AGENT"),
        _criterion("SECURITY", "The change introduces no security weakness visible in the cited evidence.", evaluator="AGENT"),
        _criterion("INTEGRATION", "The change integrates with its callers and contracts, per the cited evidence.", evaluator="AGENT"),
    ],
}
#: Acceptance checks become dynamic criteria ``ACCEPTANCE:<check id>`` on these gates.
ACCEPTANCE_STAGES = ("TEST", "REVIEW")


class GateRegistry:
    """Read-only access to the gate catalogue."""

    def __init__(self, gates: dict[str, list[dict[str, str]]] | None = None) -> None:
        self._gates = copy.deepcopy(gates if gates is not None else GATES)
        for stage, criteria in self._gates.items():
            ids = [item["id"] for item in criteria]
            if len(ids) != len(set(ids)) or any(item["evaluator"] not in EVALUATORS for item in criteria):
                raise ValueError(f"GATE_REGISTRY_INVALID: {stage}")

    def stages(self) -> tuple[str, ...]:
        return tuple(self._gates)

    def for_stage(self, stage: str) -> dict[str, Any]:
        if stage not in self._gates:
            raise KeyError(f"GATE_UNKNOWN: no gate for stage {stage!r}")
        return {"gate_id": f"sdd.{stage.lower()}", "stage": stage, "criteria": copy.deepcopy(self._gates[stage]),
                "acceptance": stage in ACCEPTANCE_STAGES}

    def digest(self) -> str:
        data = json.dumps({"version": REGISTRY_VERSION, "gates": self._gates}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(data.encode("utf-8")).hexdigest()
