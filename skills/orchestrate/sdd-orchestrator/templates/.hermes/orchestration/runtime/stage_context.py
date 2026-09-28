#!/usr/bin/env python3
"""Check the per-stage context manifest and slice contract before a dispatch.

The controller builds one manifest per dispatch: scoped excerpts (never whole
documents or transcripts), the project-context-guardian outcome, recorded
code/documentation divergences and, for IMPLEMENT/TEST/REVIEW, the slice
contract — editable paths, the authoritative acceptance mapping and the
observable verifiers each check needs. This module is pure: it reads JSON and
returns findings. It never reads the repository, the vault or STATE.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

import jsonschema

from validate_protocol import editable_pattern_is_safe

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "STAGE_CONTEXT_SCHEMA.json"
PROJECT_CONTEXT_STAGES = ("PLAN", "IMPLEMENT")
FORBIDDEN_SOURCE_NAMES = ("STATE.md", "ACTION_JOURNAL.json", "INCIDENTS.md", "action-journal-history")
SLICE_STAGES = ("IMPLEMENT", "TEST", "REVIEW")
APPROVAL_REUSED = "APPROVAL_REUSED"
APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
APPROVAL_NOT_REQUESTED = "APPROVAL_NOT_REQUESTED"
APPROVAL_NOT_APPLICABLE = "APPROVAL_NOT_APPLICABLE"
CONTEXT_ERROR_CODES = (
    "CONTEXT_BUDGET_EXCEEDED",
    "CONTEXT_EXCERPT_INVALID",
    "CONTEXT_EXCERPT_TOO_LARGE",
    "CONTEXT_SOURCE_DUPLICATED",
    "CONTEXT_SOURCE_FORBIDDEN",
    "PROJECT_CONTEXT_REQUIRED",
    "PROJECT_CONTEXT_GAPS_REQUIRED",
    "SLICE_REQUIRED",
    "SLICE_CURRENT_INVALID",
    "SLICE_EDITABLE_PATHS_REQUIRED",
    "SLICE_EDITABLE_PATH_UNSAFE",
    "ANALYSIS_STAGE_EDITABLE_PATHS",
    "ACCEPTANCE_CHECK_UNVERIFIED",
    "INDEPENDENT_VERIFIER_REQUIRED",
    "VERIFIER_COMMAND_REQUIRED",
    "VERIFIER_UNKNOWN_CHECK",
    "SCOPE_CHANGE_REQUIRED",
    "SCHEMA_INVALID",
)


class ContextError(ValueError):
    """The manifest cannot be read."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def slice_contract(value: dict[str, Any], slice_id: str) -> dict[str, Any]:
    """Return the order-independent contract of one slice: what an approval authorizes.

    It holds only the ticket, the slice ID, its editable paths, its acceptance
    checks and the verifiers bound to them. The stage and the completed-slice
    cursor are excluded, so a hash computed when TASKS is approved matches the
    later IMPLEMENT dispatch of the same slice, and finishing S1 does not change
    the hash of S2.
    """
    contract = value["slice"]
    acceptance = {key: check for key, check in contract["acceptance"].items() if check["slice_id"] == slice_id}
    verifiers = []
    for item in contract["required_verification"]:
        bound = sorted(set(item["check_ids"]) & set(acceptance))
        if bound:
            verifiers.append({**item, "check_ids": bound})
    return {
        "ticket": value["ticket"],
        "slice_id": slice_id,
        "editable_paths": sorted(contract["editable_paths"]),
        "acceptance": acceptance,
        "required_verification": sorted(verifiers, key=lambda item: item["id"]),
    }


def slice_sha256(value: dict[str, Any]) -> str | None:
    """Hash the current IMPLEMENT slice contract; other stages authorize no writes."""
    contract = value.get("slice")
    if value["stage"] != "IMPLEMENT" or contract is None or len(set(contract["current_slice_ids"])) != 1:
        return None
    (slice_id,) = set(contract["current_slice_ids"])
    return hashlib.sha256(_canonical(slice_contract(value, slice_id))).hexdigest()


def _finding(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def _check_sources(value: dict[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    limits, sources = value["limits"], value["sources"]
    if len(sources) > limits["max_sources"]:
        errors.append(_finding("CONTEXT_BUDGET_EXCEEDED", f"{len(sources)} sources exceed max_sources {limits['max_sources']}"))
    seen: set[tuple[str, int, int]] = set()
    for source in sources:
        start, end = source["lines"]
        key = (source["path"], start, end)
        if key in seen:
            errors.append(_finding("CONTEXT_SOURCE_DUPLICATED", f"{source['path']}:{start}-{end} is listed twice"))
        seen.add(key)
        if set(PurePosixPath(source["path"]).parts) & set(FORBIDDEN_SOURCE_NAMES):
            errors.append(_finding(
                "CONTEXT_SOURCE_FORBIDDEN",
                f"{source['path']} is controller-owned runtime state; send the relevant facts, not a STATE or journal dump",
            ))
        if end < start:
            errors.append(_finding("CONTEXT_EXCERPT_INVALID", f"{source['path']} ends before it starts"))
        elif end - start + 1 > limits["max_lines_per_source"]:
            errors.append(_finding(
                "CONTEXT_EXCERPT_TOO_LARGE",
                f"{source['path']}:{start}-{end} exceeds {limits['max_lines_per_source']} lines; send an excerpt",
            ))
    return errors


def _check_project_context(value: dict[str, Any]) -> list[dict[str, str]]:
    project = value["project_context"]
    if value["stage"] not in PROJECT_CONTEXT_STAGES:
        return []
    if project["status"] == "MISSING" or not (project["evidence"] or "").strip() or not project["checked_head"]:
        return [_finding(
            "PROJECT_CONTEXT_REQUIRED",
            f"{value['stage']} needs a project-context-guardian result with evidence and the checked HEAD",
        )]
    if project["status"] == "PARTIAL" and not project["gaps"]:
        return [_finding("PROJECT_CONTEXT_GAPS_REQUIRED", "PARTIAL project context must name what was not examined")]
    return []


def _check_slice(value: dict[str, Any]) -> list[dict[str, str]]:
    stage, contract = value["stage"], value["slice"]
    if stage not in SLICE_STAGES:
        return []
    if contract is None:
        return [_finding("SLICE_REQUIRED", f"{stage} requires a slice contract")]
    errors: list[dict[str, str]] = []
    if stage == "IMPLEMENT":
        if len(set(contract["current_slice_ids"])) != 1:
            errors.append(_finding("SLICE_CURRENT_INVALID", "IMPLEMENT handles exactly one current slice"))
        if set(contract["current_slice_ids"]) & set(contract["completed_slice_ids"]):
            errors.append(_finding("SLICE_CURRENT_INVALID", "current and completed slices must be disjoint"))
        if not contract["editable_paths"]:
            errors.append(_finding("SLICE_EDITABLE_PATHS_REQUIRED", "IMPLEMENT must declare the slice's editable paths"))
    elif contract["editable_paths"]:
        errors.append(_finding("ANALYSIS_STAGE_EDITABLE_PATHS", f"{stage} is read-only and cannot declare editable paths"))
    for pattern in contract["editable_paths"]:
        if not editable_pattern_is_safe(pattern):
            errors.append(_finding(
                "SLICE_EDITABLE_PATH_UNSAFE",
                f"{pattern} is absolute, escapes the repository, is unanchored or matches everything",
            ))

    acceptance = contract["acceptance"]
    in_scope = (
        set(contract["current_slice_ids"]) if stage == "IMPLEMENT"
        else set(contract["current_slice_ids"]) | set(contract["completed_slice_ids"]) or {item["slice_id"] for item in acceptance.values()}
    )
    covered: dict[str, list[dict[str, Any]]] = {}
    for verifier in contract["required_verification"]:
        if verifier["kind"] != "HUMAN" and not verifier["command"]:
            errors.append(_finding("VERIFIER_COMMAND_REQUIRED", f"{verifier['id']} is machine-checkable and needs a command"))
        for check_id in verifier["check_ids"]:
            if check_id not in acceptance:
                errors.append(_finding("VERIFIER_UNKNOWN_CHECK", f"{verifier['id']} names unknown check {check_id}"))
            covered.setdefault(check_id, []).append(verifier)
    for check_id, check in sorted(acceptance.items()):
        if check["slice_id"] not in in_scope:
            continue
        verifiers = covered.get(check_id, [])
        wanted_kind = {"HUMAN"} if check["verifier"] == "HUMAN" else set(_MACHINE_KINDS)
        bound = [item for item in verifiers if item["kind"] in wanted_kind]
        if not bound:
            errors.append(_finding(
                "ACCEPTANCE_CHECK_UNVERIFIED",
                f"{check_id} has no {'human' if check['verifier'] == 'HUMAN' else 'observable'} verifier bound to it",
            ))
        elif check["verifier"] == "AGENT" and all(item["introduced_by_slice"] for item in bound):
            errors.append(_finding(
                "INDEPENDENT_VERIFIER_REQUIRED",
                f"{check_id} is bound only to verifiers this slice introduced; bind one that predates the slice",
            ))
    return errors


_MACHINE_KINDS = ("TEST", "STATIC_ANALYSIS", "SCHEMA_VALIDATION", "STATE_INSPECTION", "LOG_INSPECTION")


def check(value: Any) -> dict[str, Any]:
    """Return {valid, errors, approval, slice_sha256} for one manifest."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    schema_errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(value), key=lambda error: list(error.path))
    if schema_errors:
        return {
            "valid": False,
            "errors": [_finding("SCHEMA_INVALID", error.message) for error in schema_errors[:5]],
            "approval": APPROVAL_REQUIRED,
            "slice_sha256": None,
        }
    errors = [*_check_sources(value), *_check_project_context(value), *_check_slice(value)]
    digest = slice_sha256(value)
    approval = value["approval"]
    if approval is None:
        approval_state = APPROVAL_NOT_REQUESTED
    elif digest is None:
        approval_state = APPROVAL_NOT_APPLICABLE
    elif digest in approval["approved_slice_sha256s"]:
        approval_state = APPROVAL_REUSED
    else:
        approval_state = APPROVAL_REQUIRED
        errors.append(_finding(
            "SCOPE_CHANGE_REQUIRED",
            "the slice contract differs from the approved one; this is a new scope and needs its own approval",
        ))
    return {"valid": not errors, "errors": errors, "approval": approval_state, "slice_sha256": digest}


def verifier_context(value: dict[str, Any]) -> dict[str, Any]:
    """Project the slice contract into validate_protocol.py keyword arguments."""
    result = check(value)
    if not result["valid"]:
        raise ContextError("; ".join(f"{item['code']}: {item['detail']}" for item in result["errors"]))
    contract = value["slice"]
    if contract is None:
        return {}
    context: dict[str, Any] = {
        "expected_acceptance": copy.deepcopy(contract["acceptance"]),
        "required_commands": [item["command"] for item in contract["required_verification"] if item["command"]],
        "check_verifiers": {
            check_id: sorted(
                item["command"] for item in contract["required_verification"]
                if item["command"] and check_id in item["check_ids"]
            )
            for check_id in sorted(contract["acceptance"])
        },
    }
    if value["stage"] == "IMPLEMENT":
        context.update(
            current_slice_ids=sorted(contract["current_slice_ids"]),
            completed_slice_ids=sorted(contract["completed_slice_ids"]),
            editable_paths=sorted(contract["editable_paths"]),
        )
    return context


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check a per-stage context manifest and slice contract without reading the repository.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "hash", "verifier-context"):
        command = commands.add_parser(name)
        command.add_argument("--context", required=True, help="Controller-owned stage context manifest JSON.")
        command.add_argument("--json", action="store_true", help="Emit structured JSON.")
    args = parser.parse_args(argv)
    try:
        value = json.loads(Path(args.context).read_text(encoding="utf-8"))
        if args.command == "check":
            result = check(value)
            code = 0 if result["valid"] else 2
        elif args.command == "hash":
            digest = slice_sha256(value)
            result, code = {"slice_sha256": digest}, 0 if digest else 2
        else:
            result, code = verifier_context(value), 0
    except (OSError, json.JSONDecodeError, KeyError, ContextError) as exc:
        result, code = {"valid": False, "errors": [_finding("SCHEMA_INVALID", str(exc))]}, 2
    print(json.dumps(result, sort_keys=True) if args.json else result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
