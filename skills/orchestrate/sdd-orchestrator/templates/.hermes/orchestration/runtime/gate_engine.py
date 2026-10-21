"""GateEngine: evaluate one SDD stage gate and record an auditable GateDecision.

``GateEngine(store, repo, commands=..., reviewers=..., mode="OBSERVE")`` combines the
GateRegistry, the DeterministicValidator (``gate_stages``), the AgentReviewer and the
EvidenceCollector/DecisionLog into ``evaluate(stage, task_id, artifact, context)``.

Verdict precedence: any FAIL → FAIL; else any BLOCKED → BLOCKED; else any INCONCLUSIVE
→ INCONCLUSIVE; else PASS, and a PASS with no evidence reference is downgraded to
INCONCLUSIVE. AGENT criteria run only when no deterministic criterion failed, so a model
never overrules a failing test and is not consulted on a known defect.

Idempotency: the decision key hashes the registry, gate, task, artifact, workspace
state, configured commands, reviewer roster and the relevant context. The same key
returns the stored decision (``reused: true``) without re-running anything or asking a
reviewer again, after the log's hash chain is verified and every evidence reference of
that decision still resolves. A ``retryable`` decision (INCONCLUSIVE because of a
timeout, an unrunnable command, a git failure or a reviewer error, or any decision made
while the workspace fingerprint was incomplete: git failed, no HEAD, submodules, too
many files, or a file too large, unreadable or special) is not reused: the same inputs are evaluated again as a new generation. A new evaluation appends a decision
with ``generation`` + 1 and ``previous_decision_id``; nothing is ever overwritten.

Context is controller-owned: ``check_commands`` (bound verifiers), ``editable_paths``,
``implementer``, ``waivers`` (human waivers the controller recorded),
``expected_check_ids`` and ``base_commit`` (the commit the slice started from) must come
from the controller's own state, never from a worker artifact. The store must be outside
the repository (``GATE_STORE_INSIDE_REPO``). The implementer is also read from the artifact's ``executor.name``; a reviewer
of either family does not count, and an unknown implementer blocks AGENT criteria.

Mode ``OBSERVE`` (default) records decisions with ``blocking: false`` and never changes
the workflow. ``ENFORCE`` marks any non-PASS ``blocking: true``. Nothing in the
controller calls this module; ``gated_handler`` adapts it to the loop engine.
"""
from __future__ import annotations

import copy
import datetime as _dt
import hashlib
from pathlib import Path
from typing import Any, Callable

import gate_corrections
import gate_reviewer
import gate_stages
from gate_evidence import DecisionLog, EvidenceCollector, canonical
from gate_registry import GateRegistry
from gate_validator import BLOCKED, FAIL, INCONCLUSIVE, PASS, DeterministicValidator

MODES = ("OBSERVE", "ENFORCE")
DECISION_VERSION = 1
NOT_EVALUATED = "NOT_EVALUATED"
#: Controller-owned inputs; a worker must never supply any of them (see the module docstring).
CONTEXT_KEYS = ("check_commands", "editable_paths", "implementer", "waivers", "expected_check_ids", "base_commit")
EVALUATOR = "gate_engine/deterministic"


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def aggregate(criteria: list[dict[str, Any]]) -> str:
    statuses = {item["status"] for item in criteria}
    for verdict in (FAIL, BLOCKED, INCONCLUSIVE):
        if verdict in statuses:
            return verdict
    return PASS


def is_success(decision: dict[str, Any]) -> bool:
    """Only a PASS is success; INCONCLUSIVE, BLOCKED and FAIL never are."""
    return decision.get("verdict") == PASS


class GateEngine:
    def __init__(self, store: Path | str, repo: Path | str, *, commands: dict[str, str | None],
                 reviewers: list[gate_reviewer.Reviewer] | None = None, mode: str = "OBSERVE",
                 timeouts: dict[str, int] | None = None, registry: GateRegistry | None = None) -> None:
        if mode not in MODES:
            raise ValueError(f"GATE_MODE_INVALID: {mode!r}")
        self.store, self.repo = Path(store), Path(repo)
        resolved_store, resolved_repo = self.store.resolve(), self.repo.resolve()
        if resolved_store == resolved_repo or resolved_repo in resolved_store.parents:
            raise ValueError("GATE_STORE_INSIDE_REPO: keep the evidence store outside the repository gate commands run in")
        self.commands, self.timeouts = dict(commands), dict(timeouts or {})
        self.reviewers = list(reviewers or [])
        self.mode = mode
        self.registry = registry or GateRegistry()
        self.evidence = EvidenceCollector(self.store)
        self.log = DecisionLog(self.store)

    def evaluate(self, stage: str, task_id: str, artifact: Any, *, context: dict[str, Any] | None = None) -> dict[str, Any]:
        gate = self.registry.for_stage(stage)
        context = {key: copy.deepcopy(value) for key, value in (context or {}).items() if key in CONTEXT_KEYS}
        validator = DeterministicValidator(self.repo, self.evidence, commands=self.commands, timeouts=self.timeouts,
                                           base=context.get("base_commit"))
        artifact_sha = _sha(artifact)
        workspace = validator.workspace_fingerprint()
        key = _sha({"registry": self.registry.digest(), "gate": gate["gate_id"], "task": task_id, "artifact": artifact_sha,
                    "workspace": workspace, "commands": self.commands, "timeouts": self.timeouts,
                    "reviewers": sorted((item.name, item.family) for item in self.reviewers), "context": context, "mode": self.mode})
        existing = self.log.find(key)
        if existing is not None and not existing.get("retryable") and self._evidence_intact(existing):
            return {**existing, "reused": True}
        artifact_ref = self.evidence.put({"kind": "artifact", "stage": stage, "sha256": artifact_sha, "summary": _artifact_summary(stage, artifact)})
        results = gate_stages.evaluate_stage(validator, stage, artifact, context)
        evaluator = EVALUATOR
        agent = [item for item in gate["criteria"] if item["evaluator"] == "AGENT"]
        if agent and aggregate(results) != FAIL and len(results) > 1:
            refs = sorted({ref for item in results for ref in item["refs"]} | {artifact_ref})
            evidence = [(ref, self.evidence.get(ref) or {}) for ref in refs]
            implementers = [context.get("implementer"), _executor_name(artifact)]
            reviewed, independence, names = gate_reviewer.review(gate, task_id, agent, evidence, self.reviewers, implementers)
            results.extend(reviewed)
            if independence is not None:
                results = [item for item in results if item["id"] != "INDEPENDENT_REVIEWER"] + [independence]
            if names:
                evaluator += "+agent:" + ",".join(names)
        evaluated = {item["id"] for item in results}
        results.extend({"id": item["id"], "status": NOT_EVALUATED, "refs": [], "detail": "skipped after an earlier failure"}
                       for item in gate["criteria"] if item["id"] not in evaluated)
        counted = [item for item in results if item["status"] != NOT_EVALUATED]
        verdict = aggregate(counted)
        evidence_refs = sorted({ref for item in counted for ref in item["refs"]} | {artifact_ref})
        failed = [item for item in counted if item["status"] != PASS]
        if verdict == PASS and len(evidence_refs) <= 1:
            verdict = INCONCLUSIVE
            failed.append({"id": "EVIDENCE_PRESENT", "status": INCONCLUSIVE, "refs": [], "detail": "a PASS needs evidence beyond the artifact"})
        history = self.log.history(gate["gate_id"], task_id)
        # An unreadable or partly hashed workspace cannot prove "no new evidence", so such a decision is never reused.
        retryable = not workspace["complete"] or (verdict == INCONCLUSIVE and any(item.get("transient") for item in failed))
        decision = {
            "schema_version": DECISION_VERSION, "decision_id": "gd-" + _sha({"key": key, "generation": len(history) + 1})[:24],
            "gate_id": gate["gate_id"], "task_id": task_id, "retryable": retryable,
            "stage": stage, "verdict": verdict, "evidence_refs": evidence_refs, "failed_criteria": failed,
            "suggested_actions": gate_corrections.suggested_actions(failed), "evaluator": evaluator, "timestamp": _now(),
            "criteria": results, "generation": len(history) + 1,
            "previous_decision_id": history[-1]["decision_id"] if history else None, "idempotency_key": key,
            "registry_sha256": self.registry.digest(), "artifact_sha256": artifact_sha, "mode": self.mode,
            "blocking": self.mode == "ENFORCE" and verdict != PASS,
            "corrective_task": gate_corrections.corrective_task(stage, failed) if verdict == FAIL else None,
        }
        return {**self.log.append(decision), "reused": False}

    def _evidence_intact(self, decision: dict[str, Any]) -> bool:
        """Every reference of a reusable decision still resolves; an edited record raises EVIDENCE_TAMPERED."""
        return all(self.evidence.get(ref) is not None for ref in decision["evidence_refs"])


def _executor_name(artifact: Any) -> str | None:
    body = artifact.get("executor_result") if isinstance(artifact, dict) else None
    executor = body.get("executor") if isinstance(body, dict) else None
    return executor.get("name") if isinstance(executor, dict) else None


def _artifact_summary(stage: str, artifact: Any) -> dict[str, Any]:
    """Short, reviewer-safe facts about the artifact (no prose beyond the summary line)."""
    if not isinstance(artifact, dict):
        return {"valid": False}
    if stage == "REVIEW":
        body = artifact.get("review_result") or {}
        return {"status": body.get("status"), "findings": (body.get("findings") or [])[:10],
                "checks": [{key: item.get(key) for key in ("id", "status", "criterion")} for item in (body.get("acceptance") or {}).get("checks") or []],
                "gate_status": body.get("gate_status")}
    body = artifact.get("executor_result") or {}
    payload = body.get("stage_payload") or {}
    return {"status": (body.get("stage") or {}).get("status"), "summary": str(payload.get("summary") or "")[:300],
            "decisions": (payload.get("decisions") or [])[:10], "impact_files": (payload.get("impact_files") or [])[:20],
            "checks": [{key: item.get(key) for key in ("id", "status", "criterion")} for item in body.get("acceptance_checks") or []]}


def gated_handler(gates: GateEngine, handler: Callable[[Any], Any], *, task_id: str,
                  context: dict[str, Any] | None = None) -> Callable[[Any], Any]:
    """Wrap a loop-engine stage handler: evaluate the gate on ``output.artifact`` and attach ``output.gate``.

    OBSERVE never changes the handler's result. ENFORCE turns a non-PASS into a FAILED
    result: a FAIL with a corrective task becomes a FUNCTIONAL failure the loop engine
    corrects (``failed_checks``, ``hypothesis``, ``evidence``); BLOCKED and INCONCLUSIVE
    become failures the loop engine escalates to a human instead of retrying.
    """
    def run(ctx: Any) -> Any:
        value = handler(ctx)
        if not isinstance(value, dict) or value.get("status") != "SUCCEEDED" or not isinstance(value.get("output"), dict):
            return value
        stage = ctx.node.get("params", {}).get("stage") or ctx.node["id"]
        decision = gates.evaluate(stage, task_id, value["output"].get("artifact"), context=context)
        summary = {key: decision[key] for key in ("decision_id", "gate_id", "verdict", "blocking", "generation", "failed_criteria")}
        output = {**value["output"], "gate": summary}
        if not decision["blocking"]:
            return {**value, "output": output}
        task = decision["corrective_task"]
        if decision["verdict"] == FAIL and task:
            return {"status": "FAILED", "output": output, "failure": {
                "class": "FUNCTIONAL", "code": "GATE_FAILED", "message": f"{decision['gate_id']} {decision['verdict']}",
                "failed_checks": task["failed_criteria"], "evidence": task["evidence"], "hypothesis": task["hypothesis"],
                "cause": task["failure_class"], "effect_applied": None}}
        failure_class = "UNKNOWN" if decision["verdict"] == INCONCLUSIVE else "FUNCTIONAL"
        return {"status": "FAILED", "output": output, "failure": {
            "class": failure_class, "code": f"GATE_{decision['verdict']}", "message": "; ".join(decision["suggested_actions"])[:500],
            "effect_applied": None}}

    return run
