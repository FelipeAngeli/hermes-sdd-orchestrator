#!/usr/bin/env python3
"""Deterministic retry recipes derived from validator error reasons.

The recipe table lives here so ``prompt_rules.py`` stays under the token
limit; ``prompt_rules`` re-exports ``retry_recipe`` for existing imports.
"""
from __future__ import annotations

import re
from typing import Any

_ONLY_JSON = "Your final message must be only the JSON object, no prose, no code fence."
_CONTROLLER_DATA = ("This is a controller-data problem you cannot fix: return stage.status BLOCKED "
                    "with a blocker quoting this error.")

_CITE_RE = re.compile(r"^AGENT PASS evidence for (.+) must cite")
_REQUIRED_RE = re.compile(r"^required verification `([^`]+)` was not recorded as passing$")
_OUTSIDE_RE = re.compile(r"^(.+) is outside the slice editable_paths$")
_VERSION_RE = re.compile(r"expected version (\d+)")
_ACTION_RE = re.compile(r"must equal requested action (\S+)")
_ENVELOPE_RE = re.compile(r"expected (executor_result|review_result) envelope for ")


def _controller_data_problem(reason: str) -> bool:
    return (
        "editable_paths patterns are unsafe" in reason
        or "is not a read-only role allowed in" in reason
        or "requires controller-owned" in reason
        or "controller-declared editable_paths" in reason
    )


def _only_json_error(path: str, reason: str) -> bool:
    if reason == "OUTPUT_INVALID" or "Expecting value" in reason or "is not valid JSON" in reason:
        return True
    if _ENVELOPE_RE.search(reason):
        return True
    return path == "$" and reason == "final message must be a JSON object"


def _recipe_for(error: dict[str, str], context: dict[str, Any]) -> str:
    reason = error.get("reason") or ""
    path = error.get("path") or "$"
    if _controller_data_problem(reason):
        return _CONTROLLER_DATA
    match = _VERSION_RE.search(reason)
    if match:
        return f"Set executor_result.schema_version to {match.group(1)}."
    match = _ACTION_RE.search(reason)
    if match:
        return f"Set executor_result.stage.value to {match.group(1)}."
    if _only_json_error(path, reason):
        return _ONLY_JSON
    match = _CITE_RE.search(reason)
    if match:
        commands = (context.get("check_verifiers") or {}).get(match.group(1)) or []
        joined = "`" + "`, `".join(commands) + "`" if commands else "`<bound command>`"
        return (f"Rewrite {match.group(1)} evidence citing literally, in backticks, {joined}, and record that command "
                "in commands[] with exit_code 0 and result PASS.")
    match = _REQUIRED_RE.search(reason)
    if match:
        return (f"Run `{match.group(1)}` and record it in commands[] with exit 0/PASS; if it cannot pass in your "
                "sandbox, return BLOCKED with a blocker instead of SUCCESS.")
    if reason == "SUCCESS cannot include blockers":
        return "Either fix and empty blockers, or set stage.status to BLOCKED."
    if reason in {"IMPLEMENT SUCCESS requires at least one TDD slice", "reported TDD slice ids must exactly match current_slice_ids"}:
        ids = context.get("current_slice_ids") or ["<slice id>"]
        return (f"tdd_slices must be exactly [{', '.join(ids)}]; each needs red_exit_code != 0, red_failure_kind "
                "EXPECTED_FUNCTIONAL, non-empty expected_failure, green_command and green_exit_code 0.")
    if reason in {"accepted RED requires a non-zero exit code", "accepted RED requires an expected functional failure",
                  "infrastructure failure is not valid RED evidence"}:
        verifier = (context.get("required_commands") or [None])[0]
        return (f"Use as RED the slice's verifier against the pre-change content, e.g. `git stash`-free: "
                f"`git show HEAD:<path> | {verifier or '<verifier>'}` or the focused test before your change; it must "
                "exit ≠ 0 for the functional reason; set red_failure_kind EXPECTED_FUNCTIONAL.")
    if reason == "accepted GREEN requires exit code 0":
        return "Run the GREEN command and record it in commands[] with exit_code 0 and a non-empty green_result."
    match = _OUTSIDE_RE.search(reason)
    if match:
        allowed = ", ".join(context.get("editable_paths") or []) or "the declared editable_paths"
        return f"Remove changes to {match.group(1)}; allowed: {allowed}."
    if reason in {"future slice checks must remain PLANNED", "future slice checks cannot carry evidence"}:
        return "Keep future slice checks at status PLANNED with evidence null."
    if reason in {"current and completed slice checks require PASS or WAIVED", "current and completed slice checks require evidence"}:
        return "Set each current and completed slice check to PASS or WAIVED with evidence, or return BLOCKED with a blocker."
    return f"Fix: {reason} at {path}."


def retry_recipe(errors: list[dict[str, str]], context: dict[str, Any]) -> list[str]:
    recipe: list[str] = []
    for error in errors:
        instruction = _recipe_for(error, context)
        if instruction and instruction not in recipe:
            recipe.append(instruction)
    return recipe
