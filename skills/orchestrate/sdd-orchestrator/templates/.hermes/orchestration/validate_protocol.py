"""Local structural and semantic validation for SDD V2.5 result envelopes.

This module validates synthetic or final-message JSON only. It never executes
commands referenced by a payload and does not read or update STATE.md.
"""
from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent
EXECUTOR_ACTIONS = {"SPECIFY", "CLARIFY", "PLAN", "TASKS", "IMPLEMENT", "TEST"}
REVIEW_ACTION = "REVIEW"
_VALIDATOR_CACHE_MAXSIZE = 32
_VALIDATOR_CACHE: OrderedDict[str, Draft202012Validator] = OrderedDict()


def _error(path: str, reason: str) -> dict[str, str]:
    return {"path": path or "$", "reason": reason}


def _schema_for(action: str) -> tuple[Path, str]:
    if action in EXECUTOR_ACTIONS:
        return ROOT / "EXECUTOR_RESULT_SCHEMA.json", "executor_result"
    if action == REVIEW_ACTION:
        return ROOT / "REVIEW_RESULT_SCHEMA.json", "review_result"
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


def validate_payload(action: str, payload: Any, expected_version: int = 2) -> list[dict[str, str]]:
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
        errors.extend(_validate_executor_semantics(result))
    else:
        errors.extend(_validate_review_semantics(result))
    return errors


def validate_json_text(action: str, json_text: str, expected_version: int = 2) -> list[dict[str, str]]:
    """Accept JSON text only; YAML, transcripts, and Markdown are rejected."""
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as exc:
        return [_error("$", f"invalid JSON final message: {exc.msg}")]
    return validate_payload(action, payload, expected_version)


def _validate_executor_semantics(result: dict[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    stage = result["stage"]
    slices = result["tdd_slices"]
    if stage["status"] == "SUCCESS" and result["blockers"]:
        errors.append(_error("$.executor_result.blockers", "SUCCESS cannot include blockers"))
    if stage["value"] == "IMPLEMENT" and stage["status"] == "SUCCESS":
        if not slices:
            errors.append(_error("$.executor_result.tdd_slices", "IMPLEMENT SUCCESS requires at least one TDD slice"))
        for index, item in enumerate(slices):
            prefix = f"$.executor_result.tdd_slices[{index}]"
            if item["red_exit_code"] in (None, 0):
                errors.append(_error(f"{prefix}.red_exit_code", "accepted RED requires a non-zero exit code"))
            if not item["expected_failure"]:
                errors.append(_error(f"{prefix}.expected_failure", "accepted RED requires an expected functional failure"))
            if item["red_failure_kind"] != "EXPECTED_FUNCTIONAL":
                errors.append(_error(f"{prefix}.red_failure_kind", "infrastructure failure is not valid RED evidence"))
            if item["green_exit_code"] != 0:
                errors.append(_error(f"{prefix}.green_exit_code", "accepted GREEN requires exit code 0"))
            if not item["green_result"]:
                errors.append(_error(f"{prefix}.green_result", "accepted GREEN requires a result"))
    return errors


def _validate_review_semantics(result: dict[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if result["status"] == "APPROVED":
        if not result["baseline"]["preserved"]:
            errors.append(_error("$.review_result.baseline.preserved", "APPROVED requires baseline preservation"))
        if not result["ownership"]["valid"]:
            errors.append(_error("$.review_result.ownership.valid", "APPROVED requires valid ownership"))
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
