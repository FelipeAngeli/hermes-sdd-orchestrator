"""Corrective tasks and suggested actions for failed gate criteria.

When a gate FAILs, ``corrective_task`` classifies the failure, picks the smallest
affected unit (one acceptance check, the files outside scope, the failing command, or
the stage) and returns a bounded task the loop engine can dispatch: the stage to
re-run, the failed criteria and one hypothesis-shaped action. BLOCKED and INCONCLUSIVE
never produce a corrective task: they need a human or a re-run, not a code change.
"""
from __future__ import annotations

from typing import Any

#: (criterion id or prefix, failure class, suggested action) in priority order.
CLASSIFICATION = (
    ("TESTS_EXIST", "MISSING_TEST", "Create the missing test file the focused-tests command names, then make it pass."),
    ("TESTS_PASS", "TEST_FAILURE", "Fix the code so the focused tests pass; do not change the tests to hide the failure."),
    ("EVIDENCE_REPRODUCED", "FALSE_EVIDENCE", "Report only commands that really pass; re-run them and record their actual exit codes."),
    ("ACCEPTANCE:", "ACCEPTANCE_UNMET", "Make the bound verifier of the failing acceptance check pass."),
    ("STATIC_ANALYSIS", "STATIC_ANALYSIS", "Fix the static-analysis errors in the changed files."),
    ("WITHIN_EDITABLE_PATHS", "SCOPE_VIOLATION", "Revert the changes outside the slice's editable paths."),
    ("CLAIMS_MATCH_WORKSPACE", "FALSE_EVIDENCE", "Report exactly the files you changed; the gate compares them with the workspace."),
    ("REVIEW_APPROVED", "REVIEW_FINDINGS", "Address the blocking review findings."),
    ("RECORDED_GATES_PASS", "GATE_FAILURE", "Make the recorded focused-tests, format and analysis gates pass."),
    ("BASELINE_AND_OWNERSHIP", "SCOPE_VIOLATION", "Restore the protected baseline and keep writes inside agent-owned paths."),
    ("SCHEMA_VALID", "CONTRACT_INVALID", "Return a result that matches the stage's JSON Schema."),
    ("CHECKS_COVERED", "ACCEPTANCE_UNMET", "Bind an allowlisted verifier to every AGENT acceptance check."),
)
#: Failures of these stages are fixed by re-running IMPLEMENT; earlier stages redo themselves.
FIXED_BY_IMPLEMENT = ("IMPLEMENT", "TEST", "REVIEW")
GENERIC_ACTION = "Complete the missing part of the stage artifact named by the failed criterion."


def _classify(identifier: str) -> tuple[str, str]:
    for key, failure_class, action in CLASSIFICATION:
        if identifier == key or (key.endswith(":") and identifier.startswith(key)):
            return failure_class, action
    return "ARTIFACT_INCOMPLETE", GENERIC_ACTION


def suggested_actions(failed: list[dict[str, Any]]) -> list[str]:
    actions: list[str] = []
    for item in failed:
        if item["status"] == "FAIL":
            text = f"{item['id']}: {_classify(item['id'])[1]}" + (f" ({item['detail']})" if item.get("detail") else "")
        elif item["status"] == "BLOCKED":
            text = f"{item['id']}: needs a human decision or configuration ({item.get('detail') or 'see criterion'})."
        else:
            text = f"{item['id']}: inconclusive; gather new evidence (re-run, raise a timeout, or add a reviewer)."
        if text not in actions:
            actions.append(text)
    return actions


def _unit(primary: dict[str, Any], failure_class: str, stage: str) -> str:
    if primary["id"].startswith("ACCEPTANCE:"):
        return primary["id"].split(":", 1)[1]
    if failure_class == "SCOPE_VIOLATION" and primary.get("detail"):
        return primary["detail"].split(":", 1)[-1].strip()
    if primary.get("detail", "").startswith("`"):
        return primary["detail"].split("`")[1]
    return stage


def corrective_task(stage: str, failed: list[dict[str, Any]]) -> dict[str, Any] | None:
    defects = [item for item in failed if item["status"] == "FAIL"]
    if not defects:
        return None
    ranked = sorted(defects, key=lambda item: next((index for index, (key, *_rest) in enumerate(CLASSIFICATION)
                                                    if item["id"] == key or (key.endswith(":") and item["id"].startswith(key))),
                                                   len(CLASSIFICATION)))
    primary = ranked[0]
    failure_class, action = _classify(primary["id"])
    return {
        "stage": "IMPLEMENT" if stage in FIXED_BY_IMPLEMENT else stage,
        "failure_class": failure_class,
        "unit": _unit(primary, failure_class, stage),
        "failed_criteria": [item["id"] for item in defects],
        "hypothesis": f"{action} [{primary['id']}]",
        "evidence": "; ".join(f"{item['id']}: {item.get('detail') or 'FAIL'}" for item in defects)[:1000],
        "scope": f"Change only what makes {', '.join(item['id'] for item in defects)} pass; do not touch unrelated behavior.",
    }
