"""Read-only executor-result checks extracted from the validator entry module."""
from __future__ import annotations


def validate(result: dict, completed_slice_ids: set[str] | None, *, satisfied, has_text, error) -> list[dict[str, str]]:
    """Return semantic errors for a role that may inspect but never change a delivery."""
    errors: list[dict[str, str]] = []
    if result["tdd_slices"]:
        errors.append(error("$.executor_result.tdd_slices", "a read-only role cannot report TDD slices"))
    completed = completed_slice_ids or set()
    for index, check in enumerate(result["acceptance_checks"]):
        prefix = f"$.executor_result.acceptance_checks[{index}]"
        if check["slice_id"] in completed:
            if check["status"] not in satisfied or not has_text(check["evidence"]):
                errors.append(error(
                    f"{prefix}.status",
                    "a read-only role must carry completed-slice checks forward unchanged as PASS or WAIVED with evidence",
                ))
            continue
        if check["status"] != "PLANNED":
            errors.append(error(f"{prefix}.status", "a read-only role keeps unverified checks PLANNED"))
        if check["evidence"] is not None:
            errors.append(error(f"{prefix}.evidence", "a read-only role cannot add acceptance evidence"))
    return errors
