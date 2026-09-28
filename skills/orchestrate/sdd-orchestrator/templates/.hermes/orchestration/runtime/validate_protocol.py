#!/usr/bin/env python3
"""Local structural and semantic validation for SDD V3 result envelopes.

This module validates synthetic or final-message JSON only. It never executes
commands referenced by a payload and does not read or update STATE.md.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from collections import OrderedDict
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator

ORCHESTRATION_ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_ROOT = ORCHESTRATION_ROOT / "schemas"
EXECUTOR_ACTIONS = {"SPECIFY", "CLARIFY", "PLAN", "TASKS", "IMPLEMENT", "TEST"}
ANALYSIS_ONLY_ACTIONS = {"SPECIFY", "CLARIFY", "PLAN", "TASKS", "TEST"}
REVIEW_ACTION = "REVIEW"
_CITED_COMMAND = re.compile(r"`([^`]+)`")
_VALIDATOR_CACHE_MAXSIZE = 32
_VALIDATOR_CACHE: OrderedDict[str, Draft202012Validator] = OrderedDict()


def _error(path: str, reason: str) -> dict[str, str]:
    return {"path": path or "$", "reason": reason}


def _schema_for(action: str) -> tuple[Path, str]:
    if action in EXECUTOR_ACTIONS:
        return SCHEMAS_ROOT / "EXECUTOR_RESULT_SCHEMA.json", "executor_result"
    if action == REVIEW_ACTION:
        return SCHEMAS_ROOT / "REVIEW_RESULT_SCHEMA.json", "review_result"
    raise ValueError(f"unsupported action: {action}")


def _load_schema(action: str) -> tuple[dict[str, Any], str]:
    schema_path, envelope = _schema_for(action)
    with schema_path.open(encoding="utf-8") as schema_file:
        schema = json.load(schema_file)
    return schema, envelope


def _validator_for_schema(schema: dict[str, Any]) -> Draft202012Validator:
    schema_key = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    validator = _VALIDATOR_CACHE.get(schema_key)
    if validator is not None:
        _VALIDATOR_CACHE.move_to_end(schema_key)
        return validator

    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    _VALIDATOR_CACHE[schema_key] = validator
    if len(_VALIDATOR_CACHE) > _VALIDATOR_CACHE_MAXSIZE:
        _VALIDATOR_CACHE.popitem(last=False)
    return validator


def validate_payload(
    action: str,
    payload: Any,
    expected_version: int = 3,
    expected_acceptance: dict[str, dict[str, str | None]] | None = None,
    current_slice_ids: set[str] | None = None,
    completed_slice_ids: set[str] | None = None,
    editable_paths: set[str] | None = None,
    required_commands: list[str] | None = None,
) -> list[dict[str, str]]:
    """Return structural and semantic errors; an empty list means acceptance."""
    try:
        schema, envelope = _load_schema(action)
        validator = _validator_for_schema(schema)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return [_error("$", str(exc))]
    if not isinstance(payload, dict):
        return [_error("$", "final message must be a JSON object")]
    if envelope not in payload:
        return [_error("$", f"expected {envelope} envelope for {action}")]

    errors = []
    for error in sorted(validator.iter_errors(payload), key=lambda item: list(item.absolute_path)):
        path = "$" + "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.absolute_path)
        errors.append(_error(path, error.message))
    if errors:
        return errors

    result = payload[envelope]
    if result["schema_version"] != expected_version:
        return [_error(f"$.{envelope}.schema_version", f"expected version {expected_version}")]
    if action in EXECUTOR_ACTIONS:
        if result["stage"]["value"] != action:
            return [_error("$.executor_result.stage.value", f"must equal requested action {action}")]
        errors.extend(_validate_executor_semantics(
            result,
            expected_acceptance,
            current_slice_ids,
            completed_slice_ids,
            editable_paths,
            required_commands,
        ))
    else:
        errors.extend(_validate_review_semantics(result, expected_acceptance))
    return errors


def validate_json_text(
    action: str,
    json_text: str,
    expected_version: int = 3,
    expected_acceptance: dict[str, dict[str, str | None]] | None = None,
    current_slice_ids: set[str] | None = None,
    completed_slice_ids: set[str] | None = None,
    editable_paths: set[str] | None = None,
    required_commands: list[str] | None = None,
) -> list[dict[str, str]]:
    """Accept JSON text only; YAML, transcripts, and Markdown are rejected."""
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as exc:
        return [_error("$", f"invalid JSON final message: {exc.msg}")]
    return validate_payload(
        action,
        payload,
        expected_version,
        expected_acceptance,
        current_slice_ids,
        completed_slice_ids,
        editable_paths,
        required_commands,
    )


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _duplicate_values(values: list[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


def _validate_authoritative_acceptance(
    checks: list[dict[str, Any]],
    expected_acceptance: dict[str, dict[str, str | None]] | None,
    path: str,
    *,
    compare_slice: bool,
) -> list[dict[str, str]]:
    if expected_acceptance is None:
        return [_error(path, "authoritative acceptance mapping is required")]
    errors: list[dict[str, str]] = []
    if not expected_acceptance:
        errors.append(_error(path, "authoritative acceptance mapping must not be empty"))
    if not checks:
        errors.append(_error(path, "acceptance checks must not be empty"))
    actual = {check["id"]: check for check in checks}
    if set(actual) != set(expected_acceptance):
        errors.append(_error(path, "acceptance ids must exactly match the authoritative mapping"))
    for check_id in sorted(set(actual) & set(expected_acceptance)):
        check = actual[check_id]
        expected = expected_acceptance[check_id]
        for field in ("criterion", "verification_method", "verifier"):
            if field not in expected:
                errors.append(_error(
                    path,
                    f"authoritative acceptance {check_id} is missing {field}",
                ))
            elif check[field] != expected[field]:
                errors.append(_error(
                    path,
                    f"acceptance {field} {check_id} differs from the authoritative mapping",
                ))
        if compare_slice:
            expected_slice = expected.get("slice_id")
            if not _has_text(expected_slice):
                errors.append(_error(
                    path,
                    f"authoritative acceptance {check_id} requires a non-whitespace slice_id",
                ))
            if not _has_text(check["slice_id"]):
                errors.append(_error(
                    path,
                    f"acceptance {check_id} requires a non-whitespace slice_id",
                ))
            elif check["slice_id"] != expected_slice:
                errors.append(_error(
                    path,
                    f"acceptance slice {check_id} differs from the authoritative mapping",
                ))
    return errors


def _is_safe_relative(path: str) -> bool:
    candidate = PurePosixPath(path)
    return not candidate.is_absolute() and ".." not in candidate.parts and not path.startswith("~")


def _validate_write_scope(
    result: dict[str, Any], editable_paths: set[str] | None,
) -> list[dict[str, str]]:
    """Analysis stages write nothing; IMPLEMENT writes only controller-declared paths."""
    stage = result["stage"]["value"]
    errors: list[dict[str, str]] = []
    written = [
        (field, index, item["path"])
        for field in ("modified_paths", "created_paths")
        for index, item in enumerate(result[field])
    ]
    if stage in ANALYSIS_ONLY_ACTIONS:
        for field, index, _ in written:
            errors.append(_error(
                f"$.executor_result.{field}[{index}]",
                f"{stage} is analysis-only and cannot report file changes",
            ))
        return errors
    if stage != "IMPLEMENT":
        return errors
    if not editable_paths:
        errors.append(_error(
            "$.executor_result.modified_paths",
            "IMPLEMENT requires non-empty controller-declared editable_paths for the slice",
        ))
        return errors
    for field, index, path in written:
        prefix = f"$.executor_result.{field}[{index}]"
        if not _is_safe_relative(path):
            errors.append(_error(prefix, "written paths must be repository-relative without '..'"))
        elif not any(fnmatch.fnmatchcase(path, pattern) for pattern in editable_paths):
            errors.append(_error(prefix, f"{path} is outside the slice editable_paths"))
    return errors


def _passing_commands(result: dict[str, Any]) -> set[str]:
    commands = {
        item["command"]
        for item in result["commands"]
        if item["command"] and item["exit_code"] == 0 and item["result"] == "PASS"
    }
    commands.update(
        item["green_command"]
        for item in result["tdd_slices"]
        if item["green_command"] and item["green_exit_code"] == 0
    )
    return commands


def _validate_evidence_citations(
    result: dict[str, Any], current_slice_ids: set[str] | None,
) -> list[dict[str, str]]:
    """An AGENT PASS verified in this result must cite a recorded passing command.

    TEST verifies every check now. IMPLEMENT verifies only the current slice;
    completed-slice checks carry evidence already accepted by an earlier action.
    """
    stage = result["stage"]["value"]
    if stage not in {"IMPLEMENT", "TEST"}:
        return []
    passing = _passing_commands(result)
    errors: list[dict[str, str]] = []
    for index, check in enumerate(result["acceptance_checks"]):
        if check["status"] != "PASS" or check["verifier"] != "AGENT" or not _has_text(check["evidence"]):
            continue
        if stage == "IMPLEMENT" and current_slice_ids is not None and check["slice_id"] not in current_slice_ids:
            continue
        cited = set(_CITED_COMMAND.findall(check["evidence"]))
        if not cited & passing:
            errors.append(_error(
                f"$.executor_result.acceptance_checks[{index}].evidence",
                "AGENT PASS evidence must cite, in backticks, a recorded command that exited 0 with PASS",
            ))
    return errors


def _validate_required_commands(
    result: dict[str, Any], required_commands: list[str] | None,
) -> list[dict[str, str]]:
    """A successful IMPLEMENT/TEST must record every controller-required verifier as passing."""
    stage = result["stage"]
    if not required_commands or stage["value"] not in {"IMPLEMENT", "TEST"} or stage["status"] != "SUCCESS":
        return []
    passing = _passing_commands(result)
    return [
        _error("$.executor_result.commands", f"required verification `{command}` was not recorded as passing")
        for command in required_commands
        if command not in passing
    ]


def _validate_executor_semantics(
    result: dict[str, Any],
    expected_acceptance: dict[str, dict[str, str | None]] | None,
    current_slice_ids: set[str] | None,
    completed_slice_ids: set[str] | None,
    editable_paths: set[str] | None = None,
    required_commands: list[str] | None = None,
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = _validate_write_scope(result, editable_paths)
    errors.extend(_validate_evidence_citations(result, current_slice_ids))
    errors.extend(_validate_required_commands(result, required_commands))
    stage = result["stage"]
    slices = result["tdd_slices"]
    checks = result["acceptance_checks"]
    check_ids = [check["id"] for check in checks]
    if duplicates := _duplicate_values(check_ids):
        errors.append(_error(
            "$.executor_result.acceptance_checks",
            f"acceptance check ids must be unique: {sorted(duplicates)}",
        ))
    if stage["status"] == "SUCCESS" and result["blockers"]:
        errors.append(_error("$.executor_result.blockers", "SUCCESS cannot include blockers"))
    if stage["status"] == "SUCCESS":
        context = result["context_assessment"]
        material_assumptions = [
            (index, assumption)
            for index, assumption in enumerate(context["assumptions"])
            if assumption["material"] and not _has_text(assumption["validation"])
        ]
        material_questions = [
            (index, question)
            for index, question in enumerate(context["unresolved_questions"])
            if question["material"]
        ]
        if stage["value"] == "SPECIFY" and (material_assumptions or material_questions):
            if result["next_step"]["stage"] != "CLARIFY":
                errors.append(_error(
                    "$.executor_result.next_step.stage",
                    "SPECIFY with unresolved material context must route to CLARIFY",
                ))
        elif stage["value"] != "SPECIFY":
            for index, _ in material_assumptions:
                errors.append(_error(
                    f"$.executor_result.context_assessment.assumptions[{index}].validation",
                    "SUCCESS after SPECIFY requires validation for every material assumption",
                ))
            for index, _ in material_questions:
                errors.append(_error(
                    f"$.executor_result.context_assessment.unresolved_questions[{index}]",
                    "SUCCESS after SPECIFY cannot include a material unresolved question",
                ))
    if stage["value"] in {"SPECIFY", "CLARIFY", "PLAN", "TASKS"}:
        for index, check in enumerate(checks):
            prefix = f"$.executor_result.acceptance_checks[{index}]"
            if check["status"] != "PLANNED":
                errors.append(_error(f"{prefix}.status", f"{stage['value']} acceptance checks must remain PLANNED"))
            if check["evidence"] is not None:
                errors.append(_error(f"{prefix}.evidence", f"{stage['value']} cannot claim executed acceptance evidence"))
    if stage["value"] == "TASKS" and stage["status"] == "SUCCESS":
        if not checks:
            errors.append(_error(
                "$.executor_result.acceptance_checks",
                "TASKS SUCCESS requires at least one acceptance check",
            ))
        for index, check in enumerate(checks):
            if not _has_text(check["slice_id"]):
                errors.append(_error(
                    f"$.executor_result.acceptance_checks[{index}].slice_id",
                    "TASKS SUCCESS requires every acceptance check to be assigned to a slice",
                ))
    if stage["value"] in {"IMPLEMENT", "TEST"}:
        errors.extend(_validate_authoritative_acceptance(
            checks,
            expected_acceptance,
            "$.executor_result.acceptance_checks",
            compare_slice=True,
        ))
        if stage["status"] == "SUCCESS" and not checks:
            errors.append(_error(
                "$.executor_result.acceptance_checks",
                f"{stage['value']} SUCCESS requires at least one acceptance check",
            ))
    if stage["value"] == "IMPLEMENT" and stage["status"] == "SUCCESS":
        reported_slice_ids = [item["id"] for item in slices]
        if current_slice_ids is None:
            errors.append(_error(
                "$.executor_result.tdd_slices",
                "IMPLEMENT SUCCESS requires controller-owned current_slice_ids",
            ))
            authoritative_current: set[str] = set()
        else:
            authoritative_current = current_slice_ids
            if len(authoritative_current) != 1:
                errors.append(_error(
                    "$.executor_result.tdd_slices",
                    "IMPLEMENT handles exactly one controller-selected current slice",
                ))
        if completed_slice_ids is None:
            errors.append(_error(
                "$.executor_result.acceptance_checks",
                "IMPLEMENT SUCCESS requires controller-owned completed_slice_ids, including an explicit empty set",
            ))
            authoritative_completed: set[str] = set()
        else:
            authoritative_completed = completed_slice_ids
        if duplicates := _duplicate_values(reported_slice_ids):
            errors.append(_error(
                "$.executor_result.tdd_slices",
                f"reported TDD slice ids must be unique: {sorted(duplicates)}",
            ))
        if set(reported_slice_ids) != authoritative_current:
            errors.append(_error(
                "$.executor_result.tdd_slices",
                "reported TDD slice ids must exactly match current_slice_ids",
            ))
        if authoritative_current & authoritative_completed:
            errors.append(_error(
                "$.executor_result.tdd_slices",
                "current_slice_ids and completed_slice_ids must be disjoint",
            ))
        for slice_id in sorted(authoritative_current):
            assigned = [check for check in checks if check["slice_id"] == slice_id]
            if not assigned:
                errors.append(_error(
                    "$.executor_result.acceptance_checks",
                    f"IMPLEMENT slice {slice_id} requires an assigned acceptance check",
                ))
        verified_slice_ids = authoritative_current | authoritative_completed
        for index, check in enumerate(checks):
            prefix = f"$.executor_result.acceptance_checks[{index}]"
            if check["slice_id"] in verified_slice_ids:
                if check["status"] != "PASS":
                    errors.append(_error(f"{prefix}.status", "current and completed slice checks require PASS"))
                if not _has_text(check["evidence"]):
                    errors.append(_error(f"{prefix}.evidence", "current and completed slice checks require evidence"))
            else:
                if check["status"] != "PLANNED":
                    errors.append(_error(f"{prefix}.status", "future slice checks must remain PLANNED"))
                if check["evidence"] is not None:
                    errors.append(_error(f"{prefix}.evidence", "future slice checks cannot carry evidence"))
    if stage["value"] == "TEST" and stage["status"] == "SUCCESS":
        for index, check in enumerate(checks):
            prefix = f"$.executor_result.acceptance_checks[{index}]"
            if check["status"] != "PASS":
                errors.append(_error(f"{prefix}.status", "TEST SUCCESS requires every acceptance check to PASS"))
            if not _has_text(check["evidence"]):
                errors.append(_error(f"{prefix}.evidence", "TEST SUCCESS requires evidence"))
    if stage["value"] == "IMPLEMENT" and stage["status"] == "SUCCESS":
        if not slices:
            errors.append(_error("$.executor_result.tdd_slices", "IMPLEMENT SUCCESS requires at least one TDD slice"))
        for index, item in enumerate(slices):
            prefix = f"$.executor_result.tdd_slices[{index}]"
            if item["red_exit_code"] in (None, 0):
                errors.append(_error(f"{prefix}.red_exit_code", "accepted RED requires a non-zero exit code"))
            if not _has_text(item["expected_failure"]):
                errors.append(_error(f"{prefix}.expected_failure", "accepted RED requires an expected functional failure"))
            if item["red_failure_kind"] != "EXPECTED_FUNCTIONAL":
                errors.append(_error(f"{prefix}.red_failure_kind", "infrastructure failure is not valid RED evidence"))
            if item["green_exit_code"] != 0:
                errors.append(_error(f"{prefix}.green_exit_code", "accepted GREEN requires exit code 0"))
            if not _has_text(item["green_result"]):
                errors.append(_error(f"{prefix}.green_result", "accepted GREEN requires a result"))
    return errors


def _validate_review_semantics(
    result: dict[str, Any],
    expected_acceptance: dict[str, dict[str, str | None]] | None,
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    baseline = result["baseline"]
    ownership = result["ownership"]
    if baseline["preserved"] == bool(baseline["violations"]):
        errors.append(_error(
            "$.review_result.baseline",
            "baseline.preserved must be true exactly when violations is empty",
        ))
    if ownership["valid"] == bool(ownership["violations"]):
        errors.append(_error(
            "$.review_result.ownership",
            "ownership.valid must be true exactly when violations is empty",
        ))
    acceptance = result["acceptance"]
    errors.extend(_validate_authoritative_acceptance(
        acceptance["checks"],
        expected_acceptance,
        "$.review_result.acceptance.checks",
        compare_slice=True,
    ))
    if expected_acceptance is not None and set(acceptance["expected_check_ids"]) != set(expected_acceptance):
        errors.append(_error(
            "$.review_result.acceptance.expected_check_ids",
            "expected_check_ids must match the controller's authoritative acceptance mapping",
        ))
    expected_ids = acceptance["expected_check_ids"]
    actual_ids = [check["id"] for check in acceptance["checks"]]
    if not expected_ids:
        errors.append(_error(
            "$.review_result.acceptance.expected_check_ids",
            "review requires expected acceptance ids",
        ))
    if duplicates := _duplicate_values(expected_ids):
        errors.append(_error(
            "$.review_result.acceptance.expected_check_ids",
            f"expected acceptance ids must be unique: {sorted(duplicates)}",
        ))
    if duplicates := _duplicate_values(actual_ids):
        errors.append(_error(
            "$.review_result.acceptance.checks",
            f"reviewed acceptance ids must be unique: {sorted(duplicates)}",
        ))
    if set(expected_ids) != set(actual_ids):
        errors.append(_error(
            "$.review_result.acceptance.checks",
            "review acceptance checks must exactly cover expected_check_ids",
        ))
    if not acceptance["checks"]:
        errors.append(_error(
            "$.review_result.acceptance.checks",
            "review requires at least one acceptance check",
        ))
    if result["status"] == "APPROVED":
        if not result["baseline"]["preserved"]:
            errors.append(_error("$.review_result.baseline.preserved", "APPROVED requires baseline preservation"))
        if result["baseline"]["violations"]:
            errors.append(_error("$.review_result.baseline.violations", "APPROVED requires no baseline violations"))
        if not result["ownership"]["valid"]:
            errors.append(_error("$.review_result.ownership.valid", "APPROVED requires valid ownership"))
        if result["ownership"]["violations"]:
            errors.append(_error("$.review_result.ownership.violations", "APPROVED requires no ownership violations"))
        if not acceptance["verified"]:
            errors.append(_error("$.review_result.acceptance.verified", "APPROVED requires independent acceptance verification"))
        for index, check in enumerate(acceptance["checks"]):
            prefix = f"$.review_result.acceptance.checks[{index}]"
            if check["status"] != "PASS":
                errors.append(_error(f"{prefix}.status", "APPROVED requires every acceptance check to PASS"))
            if not _has_text(check["evidence"]):
                errors.append(_error(f"{prefix}.evidence", "APPROVED requires evidence for every acceptance check"))
        if result["findings"]:
            errors.append(_error("$.review_result.findings", "APPROVED cannot include unresolved findings"))
        if result["forbidden_actions"]["violations"]:
            errors.append(_error("$.review_result.forbidden_actions.violations", "APPROVED cannot include forbidden-action violations"))
        if result["e2e"]["violation"]:
            errors.append(_error("$.review_result.e2e.violation", "APPROVED cannot include an E2E violation"))
        gates = result["gate_status"]
        if gates["focused_tests"] != "PASS" or gates["format"] != "PASS" or gates["analyze"] != "PASS":
            errors.append(_error("$.review_result.gate_status", "APPROVED requires focused_tests, format, and analyze PASS"))
    return errors


def _load_context(path: str | None) -> dict[str, Any]:
    if path is None:
        return {}
    context = json.loads(Path(path).read_text(encoding="utf-8"))
    allowed = {"expected_acceptance", "current_slice_ids", "completed_slice_ids", "editable_paths", "required_commands"}
    if not isinstance(context, dict) or set(context) - allowed:
        raise ValueError(f"context must be an object with only {sorted(allowed)}")
    return context


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate one worker final-message JSON against its contract.")
    parser.add_argument("--action", required=True, choices=sorted(EXECUTOR_ACTIONS | {REVIEW_ACTION}))
    parser.add_argument("--result", required=True, help="Final-message JSON file written by the worker.")
    parser.add_argument("--context", help="Controller verification context JSON, e.g. from stage_context.py verifier-context.")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON.")
    args = parser.parse_args(argv)
    try:
        context = _load_context(args.context)
        as_set = lambda key: None if context.get(key) is None else set(context[key])  # noqa: E731
        errors = validate_json_text(
            args.action,
            Path(args.result).read_text(encoding="utf-8"),
            expected_acceptance=context.get("expected_acceptance"),
            current_slice_ids=as_set("current_slice_ids"),
            completed_slice_ids=as_set("completed_slice_ids"),
            editable_paths=as_set("editable_paths"),
            required_commands=context.get("required_commands"),
        )
    except (OSError, ValueError) as exc:
        errors = [_error("$", str(exc))]
    payload = {"valid": not errors, "errors": errors}
    print(json.dumps(payload, sort_keys=True) if args.json else ("VALID" if not errors else json.dumps(errors)))
    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
