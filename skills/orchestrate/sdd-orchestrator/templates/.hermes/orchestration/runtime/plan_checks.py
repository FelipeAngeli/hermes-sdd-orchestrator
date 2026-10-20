"""Deterministic plan checks: editable-path normalization and budget-vs-plan sizing."""
from __future__ import annotations

from typing import Iterable


def normalize_editable_path(entry: str) -> str:
    """Normalize a TASKS impact entry: only a trailing ``/`` becomes ``<dir>/**``."""
    value = str(entry)
    if value.endswith("/"):
        return f"{value[:-1]}/**"
    return value


def normalize_editable_paths(entries: Iterable[str]) -> tuple[list[str], list[dict[str, str]]]:
    """Return (deduplicated normalized paths, from/to records) in deterministic order."""
    records = []
    normalized_entries = []
    for entry in entries:
        normalized = normalize_editable_path(entry)
        normalized_entries.append(normalized)
        if normalized != entry:
            records.append({"from": entry, "to": normalized})
    return sorted(set(normalized_entries)), records


def impact_file_errors(entries: Iterable[str], pattern_is_safe) -> list[tuple[int, str]]:
    """Return indexes and values whose normalized editable pattern is unsafe."""
    return [
        (index, entry)
        for index, entry in enumerate(entries)
        if not pattern_is_safe(normalize_editable_path(entry))
    ]


def budget_below_plan(data: dict, profile: str) -> dict[str, int] | None:
    """Return the shortfall (``tdd_slices``, ``executor_calls``) for IMPLEMENT, or None when enough.

    IMPLEMENT dispatches one executor call per planned slice, plus TEST and REVIEW.
    The ``profile`` argument is accepted for call-site clarity; the same arithmetic
    applies to CODE and DECISION_DOC.
    """
    slices = (data.get("delivery") or {}).get("slices") or {}
    planned = len(slices.get("planned") or [])
    if not planned:
        return None
    budgets = ((data.get("loop") or {}).get("budgets") or {})
    tdd = budgets.get("tdd_slices") or {}
    executor = budgets.get("executor_calls") or {}
    tdd_deficit = max(0, planned - (int(tdd.get("max", 0)) - int(tdd.get("used", 0))))
    executor_deficit = max(0, planned + 2 - (int(executor.get("max", 0)) - int(executor.get("used", 0))))
    if tdd_deficit or executor_deficit:
        return {"tdd_slices": tdd_deficit, "executor_calls": executor_deficit}
    return None


def budget_raise_commands(deficit: dict[str, int], command) -> list[str]:
    """Return the exact ``budget --raise`` commands that close a BUDGET_BELOW_PLAN shortfall."""
    return [
        command("budget", "--raise", name, "--by", str(shortfall), "--quote", "<user words>")
        for name, shortfall in (("tdd_slices", deficit["tdd_slices"]), ("executor_calls", deficit["executor_calls"]))
        if shortfall
    ]


def budget_below_plan_stop(deficit: dict[str, int], command) -> tuple[str, dict]:
    """Return the next command and stop extras for a BUDGET_BELOW_PLAN shortfall."""
    commands = budget_raise_commands(deficit, command)
    return commands[0], {"deficit": deficit, "next_commands": commands}


def reopen_to_tasks_refusal(ctx, data: dict, *, build_manifest, check_manifest, sdd_command) -> dict | None:
    """Return the refusal descriptor blocking ``reopen --to TASKS``, or None when allowed."""
    if ctx.stage != "IMPLEMENT":
        return {"code": "STEP_MISMATCH", "message": "reopen --to TASKS applies only at IMPLEMENT.", "next_command": sdd_command("next")}
    slices = (data.get("delivery") or {}).get("slices") or {}
    if slices.get("completed"):
        return {"code": "STEP_MISMATCH", "message": "IMPLEMENT accepted a slice; reopen --to TASKS needs none completed.", "next_command": sdd_command("next")}
    manifest = build_manifest(ctx, data, "IMPLEMENT")
    refused = check_manifest(manifest, None)
    if refused is None:
        return {"code": "STEP_MISMATCH", "message": "The IMPLEMENT manifest is valid now; reopen --to TASKS needs slice data refused by stage_context.", "next_command": sdd_command("next")}
    findings = refused.get("findings") or []
    if refused.get("stop_reason") != "MANIFEST_INVALID" or not any(item.get("code", "").startswith("SLICE_") for item in findings):
        return {"code": "STEP_MISMATCH", "message": "reopen --to TASKS needs MANIFEST_INVALID caused by slice data (a SLICE_* finding).", "next_command": sdd_command("next")}
    return None


def reset_for_tasks_replan(data: dict) -> None:
    """Drop IMPLEMENT slice state and the TASKS acceptance so TASKS re-plans from PLAN artifacts."""
    delivery = data.setdefault("delivery", {})
    delivery["slices"] = {"planned": [], "completed": [], "editable_paths": []}
    delivery["approved_slice_sha256s"] = []
    delivery["check_commands"] = {}
    delivery["check_commands_refused"] = []
    delivery["editable_path_normalizations"] = []
    accepted = delivery.setdefault("accepted", {})
    for key in list(accepted):
        if key == "tasks" or key.startswith("implement-"):
            accepted.pop(key, None)
    delivery.setdefault("summaries", {}).pop("TASKS", None)
