"""Per-stage deterministic checks: which validator call answers each registry criterion.

``evaluate_stage`` returns one result per DETERMINISTIC criterion of the stage (plus
the dynamic ``ACCEPTANCE:<id>`` criteria on TEST and REVIEW). AGENT criteria are left
to ``gate_reviewer``. Every check reads the artifact or the real workspace and stores
what it looked at as evidence; none trusts the worker's prose.
"""
from __future__ import annotations

from typing import Any

import acceptance_verifiers
from gate_evidence import canonical
from gate_validator import BLOCKED, FAIL, PASS, DeterministicValidator, result

#: Largest artifact (canonical JSON bytes) a gate evaluates; a larger one fails SCHEMA_VALID.
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
#: Review finding severities that do not block; any other value (case and spaces ignored) blocks.
NON_BLOCKING_SEVERITIES = frozenset({"LOW", "INFO", "NOTE", "MINOR", "NIT", "NON_BLOCKING", "NONE", "NO_FINDINGS"})


def _expected_set(validator, context):
    """The controller must state which acceptance checks exist; with no expected set, zero checks proves nothing."""
    stated = any(key in context for key in ("expected_check_ids", "check_commands", "waivers"))
    return _check(validator, "EXPECTED_CHECKS_STATED", stated, {"context_keys": sorted(context)},
                  "the controller gave no expected_check_ids, check_commands or waivers", BLOCKED)


def _format_check(validator):
    """Format is re-run when configured; an unconfigured format gate is recorded, not trusted from the report."""
    if validator.commands.get("format"):
        return validator.command_check("format", "FORMAT")
    return _check(validator, "FORMAT", True, {"format": None}, "")


def _ref(validator: DeterministicValidator, identifier: str, payload: dict[str, Any]) -> str:
    return validator.evidence.put({"kind": "artifact_check", "criterion": identifier, **payload})


def _check(validator: DeterministicValidator, identifier: str, ok: bool, payload: dict[str, Any], detail: str,
           failing: str = FAIL) -> dict[str, Any]:
    return result(identifier, PASS if ok else failing, [_ref(validator, identifier, payload)], "" if ok else detail)


def _blockers(validator: DeterministicValidator, body: dict[str, Any]) -> dict[str, Any]:
    blockers = body.get("blockers") or []
    return _check(validator, "NO_BLOCKERS", not blockers, {"blockers": blockers},
                  "; ".join(str(item.get("description")) for item in blockers)[:300], BLOCKED)


def _specify(validator, body, context):
    payload, checks = body["stage_payload"], body["acceptance_checks"]
    defined = [item for item in checks if (item.get("criterion") or "").strip() and (item.get("verification_method") or "").strip()]
    return [
        _check(validator, "SCOPE_STATED", bool((payload.get("summary") or "").strip()), {"summary": payload.get("summary")}, "empty summary"),
        _check(validator, "ACCEPTANCE_CRITERIA_DEFINED", bool(defined), {"checks": [item["id"] for item in defined]},
               "no acceptance check with a criterion and a verification method"),
    ]


def _clarify(validator, body, context):
    assessment = body["context_assessment"]
    open_questions = [item["question"] for item in assessment["unresolved_questions"] if item["material"]]
    unvalidated = [item["statement"] for item in assessment["assumptions"] if item["material"] and not (item.get("validation") or "").strip()]
    return [
        _check(validator, "NO_OPEN_MATERIAL_QUESTIONS", not open_questions, {"open": open_questions},
               "open: " + "; ".join(open_questions)[:300], BLOCKED),
        _check(validator, "MATERIAL_ASSUMPTIONS_VALIDATED", not unvalidated, {"unvalidated": unvalidated},
               "unvalidated: " + "; ".join(unvalidated)[:300], BLOCKED),
    ]


def _plan(validator, body, context):
    payload = body["stage_payload"]
    impact = payload.get("impact_files") or []
    missing = [path for path in impact if not acceptance_verifiers.repo_relative_path_ok(path) or not (validator.repo / path).exists()]
    decisions = [item for item in payload.get("decisions") or [] if item.strip()]
    return [
        _check(validator, "IMPACT_FILES_EXIST", bool(impact) and not missing, {"impact_files": impact, "missing": missing},
               "missing or unsafe: " + ", ".join(missing) if missing else "no impact files named"),
        _check(validator, "DECISIONS_RECORDED", bool(decisions), {"decisions": decisions[:20]}, "no decision recorded"),
    ]


def _tasks(validator, body, context):
    tasks = [item for item in body["stage_payload"].get("tasks") or [] if item.strip()]
    checks = body["acceptance_checks"]
    untraced = [item["id"] for item in checks if not item.get("slice_id")]
    unverified = [item["id"] for item in checks if item["verifier"] == "AGENT"
                  and not acceptance_verifiers.extract_check_command(item.get("verification_method"))]
    return [
        _check(validator, "TASKS_DEFINED", bool(tasks), {"tasks": tasks[:30]}, "no task listed"),
        _check(validator, "CHECKS_TRACED_TO_SLICES", bool(checks) and not untraced, {"untraced": untraced},
               "checks without a slice: " + ", ".join(untraced) if untraced else "no acceptance check"),
        _check(validator, "CHECKS_HAVE_VERIFIERS", not unverified, {"unverified": unverified},
               "AGENT checks without a backticked verifier: " + ", ".join(unverified)),
    ]


def _implement(validator, body, context):
    found = validator.workspace_claims(body, context.get("editable_paths"))
    found.append(validator.command_check("analyze", "STATIC_ANALYSIS"))
    return found


def _test(validator, body, context):
    checks = body["acceptance_checks"]
    bound, waivers = context.get("check_commands") or {}, context.get("waivers") or {}
    uncovered = [item["id"] for item in checks if item["verifier"] == "AGENT" and item["id"] not in bound and item["id"] not in waivers]
    return [
        validator.configured("focused_tests", "TESTS_CONFIGURED"),
        validator.files_named_exist("focused_tests", "TESTS_EXIST"),
        validator.command_check("focused_tests", "TESTS_PASS"),
        validator.reproduce_claims(body),
        _check(validator, "CHECKS_COVERED", not uncovered, {"uncovered": uncovered}, "checks with no bound verifier: " + ", ".join(uncovered)),
        _expected_set(validator, context),
        *validator.acceptance(checks, context),
    ]


def _review(validator, body, context):
    findings = body["findings"]
    blocking = [item for item in findings if str(item.get("severity", "")).strip().upper() not in NON_BLOCKING_SEVERITIES]
    gates = body["gate_status"]
    not_passed = [name for name in ("focused_tests", "format", "analyze") if gates.get(name) != "PASS"]
    baseline_ok = body["baseline"]["preserved"] and body["ownership"]["valid"]
    return [
        _check(validator, "REVIEW_APPROVED", body["status"] == "APPROVED" and not blocking, {"status": body["status"], "findings": findings[:10]},
               f"review is {body['status']} with {len(blocking)} blocking finding(s)"),
        _check(validator, "RECORDED_GATES_PASS", not not_passed, {"gate_status": gates}, "not PASS: " + ", ".join(not_passed)),
        _check(validator, "BASELINE_AND_OWNERSHIP", baseline_ok, {"baseline": body["baseline"], "ownership": body["ownership"]},
               "baseline not preserved or ownership invalid"),
        # The reported gate_status is only a consistency check: the gate re-runs the real commands.
        validator.command_check("focused_tests", "TESTS_PASS"),
        validator.command_check("analyze", "STATIC_ANALYSIS"),
        _format_check(validator),
        _expected_set(validator, context),
        *validator.acceptance(body["acceptance"]["checks"], context, expected=body["acceptance"]["expected_check_ids"]),
    ]


HANDLERS = {"SPECIFY": _specify, "CLARIFY": _clarify, "PLAN": _plan, "TASKS": _tasks, "IMPLEMENT": _implement,
            "TEST": _test, "REVIEW": _review}


def evaluate_stage(validator: DeterministicValidator, stage: str, artifact: Any, context: dict[str, Any]) -> list[dict[str, Any]]:
    """Run the schema check, then (only on a valid artifact) the stage's deterministic checks."""
    size = len(canonical(artifact))
    if size > MAX_ARTIFACT_BYTES:
        return [_check(validator, "SCHEMA_VALID", False, {"bytes": size, "limit": MAX_ARTIFACT_BYTES},
                       f"the artifact is {size} bytes; the limit is {MAX_ARTIFACT_BYTES}")]
    schema = validator.schema(stage, artifact)
    if schema["status"] != PASS:
        return [schema]
    body = artifact["review_result" if stage == "REVIEW" else "executor_result"]
    found = [schema, *HANDLERS[stage](validator, body, context)]
    if stage != "REVIEW":
        found.append(_blockers(validator, body))
    return found
