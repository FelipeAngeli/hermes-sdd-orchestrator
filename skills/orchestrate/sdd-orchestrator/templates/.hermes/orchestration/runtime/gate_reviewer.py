"""AgentReviewer: judge the AGENT criteria of a gate with minimal context and independent reviewers.

A reviewer is ``Reviewer(name, family, call)``; ``call(context) -> {"criteria": [{"id",
"verdict", "evidence_refs", "rationale"}]}`` is supplied by the host (an executor launch,
a sub-agent, a test double). The context holds only the gate id, stage, task id, the
criteria texts and short evidence summaries with their references, capped at
``MAX_CONTEXT_BYTES``: never the conversation, the repository or the worker's prose.

Rules applied here, never by the model:
- a reviewer of the implementer's family does not count; with no independent reviewer
  the criteria are BLOCKED (``INDEPENDENT_REVIEWER``);
- a PASS that cites no evidence reference from the context counts as INCONCLUSIVE;
- independent reviewers that disagree make the criterion INCONCLUSIVE;
- a reviewer error, malformed answer or missing criterion is INCONCLUSIVE.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

PASS, FAIL, BLOCKED, INCONCLUSIVE = "PASS", "FAIL", "BLOCKED", "INCONCLUSIVE"
VERDICTS = (PASS, FAIL, INCONCLUSIVE)
MAX_CONTEXT_BYTES = 16_384
MAX_EVIDENCE_ITEMS = 24
SUMMARY_CHARS = 400


@dataclass(frozen=True)
class Reviewer:
    name: str
    family: str
    call: Callable[[dict[str, Any]], Any]


def _summary(record: dict[str, Any]) -> str:
    keep = {key: record[key] for key in ("kind", "command", "exit_code", "outcome", "detail", "status", "findings",
                                           "checks", "gate_status", "mismatches", "claimed", "actual") if key in record}
    return json.dumps(keep, sort_keys=True, ensure_ascii=False)[:SUMMARY_CHARS]


def build_context(gate: dict[str, Any], task_id: str, criteria: list[dict[str, Any]],
                  evidence: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    """The only data a reviewer sees; trimmed until it fits ``MAX_CONTEXT_BYTES``."""
    items = [{"ref": ref, "summary": _summary(record)} for ref, record in evidence[:MAX_EVIDENCE_ITEMS]]
    context = {"gate_id": gate["gate_id"], "stage": gate["stage"], "task_id": task_id,
               "criteria": [{"id": item["id"], "description": item["description"]} for item in criteria],
               "evidence": items, "limits": {"cite": "Cite evidence refs for every PASS; a PASS without a ref is not counted."}}
    while len(json.dumps(context).encode("utf-8")) > MAX_CONTEXT_BYTES and context["evidence"]:
        context["evidence"].pop()
    return context


def _answers(reviewer: Reviewer, context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    valid_refs = {item["ref"] for item in context["evidence"]}
    try:
        reply = reviewer.call(context)
        entries = reply.get("criteria") if isinstance(reply, dict) else None
    except Exception as error:  # noqa: BLE001 - a failing reviewer is inconclusive, never a pass
        return {item["id"]: {"verdict": INCONCLUSIVE, "refs": [], "note": f"{reviewer.name} failed: {type(error).__name__}"}
                for item in context["criteria"]}
    found: dict[str, dict[str, Any]] = {}
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or entry.get("id") not in {item["id"] for item in context["criteria"]}:
            continue
        verdict = entry.get("verdict") if entry.get("verdict") in VERDICTS else INCONCLUSIVE
        refs = [ref for ref in entry.get("evidence_refs") or [] if ref in valid_refs]
        note = str(entry.get("rationale") or "")[:300]
        if verdict == PASS and not refs:
            verdict, note = INCONCLUSIVE, f"{reviewer.name}: PASS without cited evidence"
        found[entry["id"]] = {"verdict": verdict, "refs": refs, "note": note}
    for item in context["criteria"]:
        found.setdefault(item["id"], {"verdict": INCONCLUSIVE, "refs": [], "note": f"{reviewer.name} gave no verdict"})
    return found


def review(gate: dict[str, Any], task_id: str, criteria: list[dict[str, Any]], evidence: list[tuple[str, dict[str, Any]]],
           reviewers: list[Reviewer], implementer: str | None) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[str]]:
    """Return (criterion results, independence result or None, names of reviewers consulted)."""
    if not criteria:
        return [], None, []
    independent = [item for item in reviewers if not implementer or item.family.upper() != implementer.upper()]
    if not independent:
        detail = "no reviewer independent of the implementer is configured"
        blocked = [{"id": item["id"], "status": BLOCKED, "refs": [], "detail": detail} for item in criteria]
        return blocked, {"id": "INDEPENDENT_REVIEWER", "status": BLOCKED, "refs": [], "detail": detail}, []
    context = build_context(gate, task_id, criteria, evidence)
    answers = {item.name: _answers(item, context) for item in independent}
    results = []
    for item in criteria:
        verdicts = {answers[name][item["id"]]["verdict"] for name in answers}
        refs = sorted({ref for name in answers for ref in answers[name][item["id"]]["refs"]})
        notes = "; ".join(f"{name}: {answers[name][item['id']]['verdict']}" for name in sorted(answers))
        if len(verdicts) > 1:
            status, notes = INCONCLUSIVE, "reviewers disagree (" + notes + ")"
        else:
            status = verdicts.pop()
        results.append({"id": item["id"], "status": status, "refs": refs, "detail": notes})
    independence = {"id": "INDEPENDENT_REVIEWER", "status": PASS, "refs": [], "detail": ", ".join(sorted(answers))}
    return results, independence, sorted(answers)
