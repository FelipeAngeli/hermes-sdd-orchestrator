#!/usr/bin/env python3
"""Apply typed Jev recommendations under deterministic SDD precedence."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

RUNTIME = Path(__file__).resolve().parent
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from redaction import redact  # noqa: E402

Evaluator = Callable[[dict[str, Any]], dict[str, Any]]


class DecisionError(ValueError):
    """The typed decision request or governor report violates its contract."""


INSTRUCTIONS = {
    "TASK_CLASSIFICATION": "Select the bounded task class that best matches the supplied signals.",
    "SKILL_SELECTION": "Select the smallest sufficient project skill set from the closed candidates.",
    "AGENT_SELECTION": "Select the smallest sufficient agent route from the closed candidates.",
    "MODEL_SELECTION": "Select a compatible model route from the closed candidates.",
    "CONTEXT_RELEVANCE": "Select the context handling strategy that preserves required evidence.",
    "RISK_ASSESSMENT": "Select the risk class justified by the supplied signals.",
    "COMPLETION_ASSESSMENT": "Assess completion without overriding deterministic gates.",
    "ESCALATION": "Select only among the deterministically eligible escalation routes.",
}
MODES = {"OFF", "SHADOW", "ACTIVE", "FALLBACK"}
STAGES = {"SPECIFY", "CLARIFY", "PLAN", "TASKS", "IMPLEMENT", "TEST", "REVIEW"}


def _text(value: Any, limit: int) -> bool:
    return isinstance(value, str) and 0 < len(value) <= limit and value.isprintable()


def validate_request(request: Any) -> dict[str, Any]:
    required = {"schema_version", "decision_id", "ticket", "kind", "mode", "binding", "baseline", "candidates", "state"}
    if not isinstance(request, dict) or set(request) != required or request.get("schema_version") != 1:
        raise DecisionError("DECISION_REQUEST_INVALID")
    if not _text(request.get("decision_id"), 128) or not _text(request.get("ticket"), 64):
        raise DecisionError("DECISION_REQUEST_INVALID")
    if request.get("kind") not in INSTRUCTIONS or request.get("mode") not in MODES:
        raise DecisionError("DECISION_REQUEST_INVALID")
    binding = request.get("binding")
    if (
        not isinstance(binding, dict)
        or set(binding) != {"stage", "state_sha256"}
        or binding.get("stage") not in STAGES
        or not isinstance(binding.get("state_sha256"), str)
        or len(binding["state_sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in binding["state_sha256"])
    ):
        raise DecisionError("DECISION_REQUEST_INVALID")
    baseline = request.get("baseline")
    if (
        not isinstance(baseline, dict)
        or set(baseline) != {"value", "user_locked", "deterministic_ready"}
        or not isinstance(baseline.get("user_locked"), bool)
        or not isinstance(baseline.get("deterministic_ready"), bool)
    ):
        raise DecisionError("DECISION_REQUEST_INVALID")
    candidates = request.get("candidates")
    if (
        not isinstance(candidates, dict)
        or not 2 <= len(candidates) <= 32
        or baseline.get("value") not in candidates
        or any(not _text(key, 64) or not _text(description, 512) for key, description in candidates.items())
    ):
        raise DecisionError("DECISION_REQUEST_INVALID")
    state = request.get("state")
    if not isinstance(state, dict) or set(state) != {"summary", "signals", "evidence_ids"} or not _text(state.get("summary"), 4000):
        raise DecisionError("DECISION_REQUEST_INVALID")
    signals, evidence = state.get("signals"), state.get("evidence_ids")
    if (
        not isinstance(signals, dict)
        or len(signals) > 32
        or any(not _text(key, 64) or isinstance(value, (dict, list)) or value is None for key, value in signals.items())
        or not isinstance(evidence, list)
        or len(evidence) > 64
        or any(not _text(item, 256) for item in evidence)
    ):
        raise DecisionError("DECISION_REQUEST_INVALID")
    try:
        encoded = json.dumps(request, ensure_ascii=False, allow_nan=False, sort_keys=True).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise DecisionError("DECISION_REQUEST_INVALID") from error
    if len(encoded) > 65536:
        raise DecisionError("DECISION_REQUEST_INVALID")
    return request


def governor_request(request: dict[str, Any]) -> dict[str, Any]:
    """Build the governor payload whose fingerprint binds decision control data."""
    return {
        "schema_version": 1,
        "ticket": request["ticket"],
        "state": {
            "decision": {
                "decision_id": request["decision_id"],
                "kind": request["kind"],
                "mode": request["mode"],
                "binding": request["binding"],
                "baseline": request["baseline"],
            },
            "evidence": request["state"],
        },
        "questions": {
            "selection": {
                "type": "choice",
                "instructions": INSTRUCTIONS[request["kind"]],
                "criteria": request["candidates"],
            }
        },
    }


def _base_receipt(request: dict[str, Any]) -> dict[str, Any]:
    baseline = request["baseline"]["value"]
    return {
        "schema_version": 1,
        "decision_id": request["decision_id"],
        "ticket": request["ticket"],
        "kind": request["kind"],
        "binding": request["binding"],
        "configured_mode": request["mode"],
        "effective_mode": request["mode"],
        "baseline": baseline,
        "recommendation": {
            "value": None,
            "confidence": None,
            "disposition": "NOT_CALLED",
            "provenance": "NOT_CALLED",
            "fingerprint": None,
        },
        "outcome": {
            "selected": baseline,
            "applied": False,
            "agreement": "NOT_EVALUATED",
            "requires_review": False,
            "reason": "JEV_OFF",
        },
        "metrics": {
            "jev_calls": 0,
            "elapsed_ms": 0,
            "request_bytes": len(json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8")),
            "input_tokens": None,
            "output_tokens": None,
            "billing": {},
        },
    }


def _validate_report(report: Any, candidates: dict[str, str]) -> dict[str, Any]:
    if not isinstance(report, dict) or report.get("status") not in {"DECIDED", "REVIEW"}:
        raise DecisionError("DECISION_REPORT_INVALID")
    if report.get("provenance") not in {"LIVE_JEV", "CACHE"}:
        raise DecisionError("DECISION_REPORT_INVALID")
    fingerprint = report.get("fingerprint")
    decisions = report.get("decisions")
    decision = decisions.get("selection") if isinstance(decisions, dict) else None
    if (
        not isinstance(fingerprint, str)
        or len(fingerprint) != 64
        or any(character not in "0123456789abcdef" for character in fingerprint)
        or not isinstance(decision, dict)
        or set(decision) != {"value", "confidence", "disposition", "reason"}
        or decision.get("disposition") not in {"DECIDED", "REVIEW"}
        or not _text(decision.get("reason"), 128)
    ):
        raise DecisionError("DECISION_REPORT_INVALID")
    value, confidence = decision["value"], decision["confidence"]
    if decision["disposition"] == "DECIDED":
        if value not in candidates or not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0.70 <= confidence <= 1.0:
            raise DecisionError("DECISION_REPORT_INVALID")
    elif value is not None or (confidence is not None and (not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0.0 <= confidence < 0.70)):
        raise DecisionError("DECISION_REPORT_INVALID")
    if "usage" in report and not isinstance(report["usage"], dict):
        raise DecisionError("DECISION_REPORT_INVALID")
    if "billing" in report and not isinstance(report["billing"], dict):
        raise DecisionError("DECISION_REPORT_INVALID")
    return report


def decide(request: dict[str, Any], evaluator: Evaluator) -> dict[str, Any]:
    """Return one auditable decision receipt without performing side effects."""
    request = validate_request(request)
    state_text = json.dumps(request["state"], ensure_ascii=False, sort_keys=True)
    if redact(state_text) != state_text:
        raise DecisionError("DECISION_STATE_SENSITIVE")
    receipt = _base_receipt(request)
    if request["mode"] == "OFF":
        return receipt

    payload = governor_request(request)
    payload_text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if redact(payload_text) != payload_text:
        raise DecisionError("DECISION_STATE_SENSITIVE")
    started = time.monotonic()
    report = _validate_report(evaluator(payload), request["candidates"])
    elapsed_ms = max(0, round((time.monotonic() - started) * 1000))
    decision = report["decisions"]["selection"]
    recommendation = decision["value"]
    if decision["disposition"] == "DECIDED" and recommendation not in request["candidates"]:
        raise DecisionError("DECISION_REPORT_INVALID")
    receipt["recommendation"] = {
        "value": recommendation,
        "confidence": decision["confidence"],
        "disposition": decision["disposition"],
        "provenance": report["provenance"],
        "fingerprint": report["fingerprint"],
    }
    receipt["outcome"].update({
        "agreement": "AGREE" if recommendation == receipt["baseline"] else "DISAGREE",
        "reason": "SHADOW_OBSERVATION",
    })
    usage = report.get("usage") or {}
    receipt["metrics"].update({
        "jev_calls": 0 if report["provenance"] == "CACHE" else 1,
        "elapsed_ms": elapsed_ms,
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "billing": report.get("billing") or {},
    })
    completion_veto = (
        request["kind"] == "COMPLETION_ASSESSMENT"
        and recommendation == "COMPLETE"
        and request["baseline"].get("deterministic_ready") is not True
    )
    if request["baseline"].get("user_locked") is True:
        receipt["effective_mode"] = "FALLBACK"
        receipt["outcome"]["reason"] = "EXPLICIT_CHOICE_PRESERVED"
    elif completion_veto:
        receipt["effective_mode"] = "FALLBACK"
        receipt["outcome"]["reason"] = "DETERMINISTIC_COMPLETION_VETO"
    elif request["mode"] == "ACTIVE" and decision["disposition"] == "DECIDED" and recommendation in request["candidates"]:
        receipt["outcome"].update({
            "selected": recommendation,
            "applied": recommendation != receipt["baseline"],
            "requires_review": False,
            "reason": "JEV_DECISION_APPLIED",
        })
    elif request["mode"] == "ACTIVE":
        receipt["effective_mode"] = "FALLBACK"
        receipt["outcome"].update({
            "requires_review": True,
            "reason": "JEV_REVIEW_REQUIRED",
        })
    elif request["mode"] == "FALLBACK":
        if decision["disposition"] == "DECIDED" and recommendation in request["candidates"]:
            receipt["outcome"].update({
                "selected": recommendation,
                "applied": recommendation != receipt["baseline"],
                "reason": "JEV_DECISION_APPLIED",
            })
        else:
            receipt["outcome"]["reason"] = "JEV_FALLBACK_USED"
    return receipt
