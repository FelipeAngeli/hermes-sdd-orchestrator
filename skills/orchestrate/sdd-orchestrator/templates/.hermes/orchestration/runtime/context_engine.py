"""Opt-in Jev context engine: the optimization pipeline.

collect -> trigger gate -> segment -> deterministic protection -> evaluator gate
(consent + preflight) -> cache -> Jev judge -> decision -> rebuild + integrity ->
audit -> metrics. Every failure returns the original request (fail-open for the
optimization only); protections are never relaxed. Nothing in the controller
calls this module; it runs only when an operator invokes ``context_cli.py``.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

from context_audit import ContextAudit
from context_cache import ContextCache
from context_collector import canonical_json, collect, estimate_tokens, to_native
from context_config import default_state_dir, policy_sha256, stage_rules
from context_judge import JevJudge, connector_evaluate, preflight
from context_metrics import Metrics
from context_policy import decide, protection, requirement_markers
from context_rebuilder import rebuild
from context_segmenter import segment

ENGINE_VERSION = "3"
STATUSES = ("DISABLED", "SKIPPED_BELOW_TRIGGER", "DRY_RUN", "APPLIED", "FAIL_OPEN")
REAL_EVALUATORS = ("typesafe", "jev-ai")
# Screening reasons keep one segment without sending it; they are not evaluator failures.
SCREEN_REASONS = ("EVALUATOR_NONE", "REDACTION_UNAVAILABLE", "CODE_PROTECTED", "SECRET_PRESENT", "SEGMENT_TOO_LARGE")


def original_request(raw: bytes, fmt: str) -> Any:
    """Decode the caller's input for the report without failing; undecodable input is base64."""
    try:
        text = raw.decode("utf-8")
        return text if fmt == "sdd-prompt" else json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"raw_base64": base64.b64encode(raw).decode("ascii")}


def _git_root(directory: Path) -> Path | None:
    current = directory.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def inside_repository(path: str | Path) -> bool:
    """Whether ``path`` lies in a Git work tree: the current one or any enclosing it (errors count as inside)."""
    try:
        root = _git_root(Path.cwd())
        target = Path(path).resolve()
        if root is not None and (target == root or root in target.parents):
            return True
        return _git_root(target) is not None
    except OSError:
        return True


def _constraints(request: Any) -> str:
    texts = [block.text for message in request.messages if message.role == "user" for block in message.blocks if block.type == "text"]
    return "\n".join(texts)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _result(status: str, request: Any, metrics: Metrics, **extra: Any) -> dict[str, Any]:
    report = {"status": status, "request": request, "metrics": metrics.as_dict()}
    report.update(extra)
    return report


def _keep_all(metrics: Metrics, candidates: list[Any], reason: str) -> None:
    for _ in candidates:
        metrics.count("evaluation_errors", reason)
        metrics.values["segments_preserved"] += 1


def optimize(
    raw: bytes,
    fmt: str,
    stage: str,
    task: str,
    config: dict[str, Any] | None,
    *,
    evaluate: Callable[[dict[str, Any]], Any] | None = None,
    apply: bool = False,
    audit_dir: str | Path | None = None,
    clock: Callable[[], float] | None = None,
    env_file: str | Path = ".env",
    evaluator: str | None = None,
    project_setup: str | Path | None = None,
) -> dict[str, Any]:
    """Return a report with ``status``, ``request`` (original unless ``APPLIED``) and ``metrics``."""
    original = original_request(raw, fmt)
    if not config or config.get("enabled") is not True:
        return _result("DISABLED", original, Metrics())
    metrics = Metrics()
    started = time.monotonic()
    try:
        return _optimize(raw, fmt, stage, task, dict(config), metrics, started, original, evaluate, apply, audit_dir, clock, env_file, evaluator, project_setup)
    except Exception as error:
        metrics.values["latency_ms"] = int((time.monotonic() - started) * 1000)
        return _result("FAIL_OPEN", original, metrics, reason=type(error).__name__)


def _optimize(raw, fmt, stage, task, config, metrics, started, original, evaluate, apply, audit_dir, clock, env_file, evaluator, project_setup):
    request = collect(raw, fmt)
    # Measure with the serializer used for the forwarded request, so the saving is
    # the content removed and never a whitespace difference from re-serialization.
    before = estimate_tokens(serialized(to_native(request)), config["bytes_per_token"])
    metrics.values["input_tokens_before"] = before
    metrics.values["input_tokens_after"] = before
    if before < config["trigger_estimated_tokens"]:
        return _result("SKIPPED_BELOW_TRIGGER", original, metrics)
    root = Path(audit_dir or config.get("state_dir") or default_state_dir(Path.cwd()))
    if inside_repository(root):
        return _result("FAIL_OPEN", original, metrics, reason="AUDIT_DIR_INSIDE_REPOSITORY")
    effective = evaluator or config["evaluator"]
    config["evaluator"] = effective
    thresholds = stage_rules(config, stage)["thresholds"]
    candidates, protected = [], []
    for item in segment(request, config, stage):
        rule = protection(item, request, config)
        metrics.values["segments_total"] += 1
        if rule:
            protected.append(item)
            metrics.values["segments_protected"] += 1
            metrics.values["segments_preserved"] += 1
            metrics.count("protection_rules", rule)
        else:
            candidates.append(item)
    if not candidates:
        return _finish_unchanged(metrics, started, original, "DRY_RUN")
    if effective == "none":
        _keep_all(metrics, candidates, "EVALUATOR_NONE")
        return _finish_unchanged(metrics, started, original, "DRY_RUN")
    if effective in REAL_EVALUATORS:
        if not preflight(effective, env_file, project_setup):
            _keep_all(metrics, candidates, "EVALUATOR_UNAVAILABLE")
            return _finish_unchanged(metrics, started, original, "DRY_RUN", reason="EVALUATOR_UNAVAILABLE")
        sender = evaluate or connector_evaluate(effective, env_file, config["evaluator_timeout_seconds"])
    elif evaluate is None:
        raise ValueError("EVALUATOR_INVALID")
    else:
        sender = evaluate
    audit = ContextAudit(root)
    run_id = audit.start(raw)
    for item in protected:
        audit.record({"segment_id": item.id, "sha256": item.sha256, "kind": item.kind, "disposition": "PRESERVE", "reason": "PROTECTED", "protection_rule": protection(item, request, config), "cached": False, "probabilities": {}})
    constraints = _constraints(request)
    identity = (_sha(task), _sha(constraints), policy_sha256(config), effective, ENGINE_VERSION)
    cache = ContextCache(root / "cache.json", config["cache"]["ttl_seconds"], config["cache"]["max_entries"], clock or time.time)
    answers, misses, keys = {}, [], {}
    for item in candidates:
        keys[item.id] = cache.key(item.sha256, *identity)
        cached = cache.get(keys[item.id])
        if cached is not None and isinstance(cached.get("probabilities"), dict):
            answers[item.id] = cached["probabilities"]
            metrics.values["cache_hits"] += 1
        else:
            misses.append(item)
            metrics.values["cache_misses"] += 1
    report = JevJudge(config, sender).judge(misses, task, constraints)
    metrics.values["evaluator_calls"] = report.calls
    metrics.values["evaluator_input_tokens"] = report.usage["input_tokens"]
    metrics.values["evaluator_output_tokens"] = report.usage["output_tokens"]
    metrics.values["evaluator_calls_without_usage"] = report.calls - report.usage_reported
    for reason in report.errors.values():
        metrics.count("evaluation_errors", reason)
    for item in misses:
        if item.id in report.answers and item.id not in report.errors:
            answers[item.id] = report.answers[item.id]
            cache.put(keys[item.id], decide(item, answers[item.id], thresholds).disposition, answers[item.id])
    cache.save()
    omissions = []
    for item in candidates:
        outcome = decide(item, answers.get(item.id), thresholds)
        if item.id in report.errors:
            outcome.reason = report.errors[item.id]
        if outcome.disposition == "OMIT":
            omissions.append(item)
        audit.record({"segment_id": item.id, "sha256": item.sha256, "kind": item.kind, "disposition": outcome.disposition, "reason": outcome.reason, "protection_rule": None, "cached": item not in misses, "probabilities": answers.get(item.id, {})})
    failures = [reason for reason in report.errors.values() if reason not in SCREEN_REASONS]
    if failures:
        metrics.values["segments_preserved"] += len(candidates)
        return _finish_unchanged(metrics, started, original, "FAIL_OPEN", audit=audit, run_id=run_id, reason="EVALUATOR_FAILED")
    rebuilt, errors = rebuild(request, omissions, run_id, protected) if omissions else (request, [])
    before, after = requirement_markers(request), requirement_markers(rebuilt)
    metrics.values["requirement_impact"] = {"before": before, "after": after, "preserved": before == after}
    if errors or before != after:
        metrics.values["segments_rejected"] = len(omissions)
        metrics.values["segments_preserved"] += len(candidates)
        reason = errors[0] if errors else "REQUIREMENT_MARKER_LOST"
        return _finish_unchanged(metrics, started, original, "FAIL_OPEN", audit=audit, run_id=run_id, reason=reason)
    after_tokens = estimate_tokens(serialized(to_native(rebuilt)), config["bytes_per_token"]) if omissions else metrics.values["input_tokens_before"]
    metrics.values["estimated_saving"] = max(0, metrics.values["input_tokens_before"] - after_tokens)
    metrics.values["segments_preserved"] += len(candidates) - len(omissions)
    real_apply = apply and config["apply_enabled"] is True and config["dry_run"] is False
    if not real_apply or not omissions:
        return _finish_unchanged(metrics, started, original, "DRY_RUN", audit=audit, run_id=run_id, would_omit=[item.id for item in omissions])
    metrics.values["segments_omitted"] = len(omissions)
    metrics.values["input_tokens_after"] = after_tokens
    metrics.values["effective_saving"] = metrics.values["estimated_saving"]
    metrics.values["latency_ms"] = int((time.monotonic() - started) * 1000)
    audit.finish(metrics.as_dict())
    return _result("APPLIED", to_native(rebuilt), metrics, run_id=run_id)


def serialized(native: Any) -> bytes:
    """Return the bytes a caller forwards for an ``APPLIED`` request."""
    if isinstance(native, str):
        return native.encode("utf-8")
    return json.dumps(native, ensure_ascii=False).encode("utf-8")


def compare(corpus_dir: str | Path, config: dict[str, Any], audit_dir: str | Path | None = None) -> dict[str, Any]:
    """Offline baseline / dry-run / apply comparison (see ``context_cli.compare``)."""
    from context_cli import compare as run_compare

    return run_compare(corpus_dir, config, audit_dir)


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point (see ``context_cli.main``)."""
    from context_cli import main as run_main

    return run_main(argv)


def _finish_unchanged(metrics: Metrics, started: float, original: Any, status: str, *, audit: ContextAudit | None = None, run_id: str | None = None, **extra: Any) -> dict[str, Any]:
    metrics.values["latency_ms"] = int((time.monotonic() - started) * 1000)
    if audit is not None:
        audit.finish(metrics.as_dict())
        extra["run_id"] = run_id
    extra.setdefault("would_omit", [])
    return _result(status, original, metrics, **extra)


if __name__ == "__main__":
    raise SystemExit(main())
