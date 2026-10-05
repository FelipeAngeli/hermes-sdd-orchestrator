#!/usr/bin/env python3
"""Batch, threshold, and cache semantic SDD decisions made by Jev."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import math
import os
import secrets
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

if os.name == "posix":
    import fcntl

THRESHOLD = 0.70
POLICY_VERSION = 1
MAX_CACHE_ENTRIES = 128
MAX_INPUT_BYTES = 4 * 1024 * 1024
DEFAULT_PROVIDER = "typesafe"
PROVIDERS = ("typesafe", "jev-ai")
DEFAULT_CACHE_PATH = Path(__file__).resolve().parents[1] / "JEV_CACHE.json"
DEFAULT_PROGRESS_PATH = Path(__file__).resolve().parents[1] / "TERMINAL_PROGRESS.json"
CONNECTOR_PATH = Path(__file__).resolve().with_name("typesafe_connector.py")


class GovernanceError(ValueError):
    """The governance request or persisted cache is unsafe or malformed."""


def _require_supported_platform(platform: str | None = None) -> None:
    """Fail closed where descriptor-anchored no-follow access is unavailable."""
    if (platform or os.name) != "posix":
        raise GovernanceError("JEV_GOVERNANCE_PLATFORM_UNSUPPORTED")


def _open_parent_descriptor(
    path: Path,
    reason: str,
    *,
    create: bool = False,
    platform: str | None = None,
) -> tuple[int | None, str]:
    """Open every parent component without following links."""
    if not path.name or path.name in {".", ".."}:
        raise GovernanceError(reason)
    _require_supported_platform(platform)

    anchor = path.anchor or "."
    parts = path.parts[1:] if path.anchor else path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise GovernanceError(reason)
    descriptor: int | None = None
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        descriptor = os.open(anchor, flags)
        for part in parts[:-1]:
            try:
                next_descriptor = os.open(part, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, 0o700, dir_fd=descriptor)
                next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor, parts[-1]
    except (GovernanceError, OSError) as error:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if isinstance(error, GovernanceError):
            raise
        raise GovernanceError(reason) from error


def _private_regular(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_size <= MAX_INPUT_BYTES
        and (os.name != "posix" or not (
            stat.S_IMODE(metadata.st_mode) & ~0o600
            or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
        ))
    )


def _set_private_mode(descriptor: int) -> None:
    """Apply POSIX owner-only mode when descriptor chmod exists."""
    fchmod = getattr(os, "fchmod", None)
    if callable(fchmod):
        fchmod(descriptor, 0o600)


def _lock_descriptor(
    descriptor: int,
    *,
    platform: str | None = None,
) -> Callable[[], None]:
    """Lock one POSIX cache descriptor and return its matching unlock operation."""
    _require_supported_platform(platform)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except OSError as error:
        raise GovernanceError("JEV_GOVERNANCE_CACHE_LOCK_FAILED") from error

    def unlock_posix() -> None:
        fcntl.flock(descriptor, fcntl.LOCK_UN)

    return unlock_posix


@contextlib.contextmanager
def _cache_lock(path: Path):
    """Keep a private lock inode so unlinking cannot split lock domains."""
    lock_path = path.with_name(f".{path.name}.lock")
    parent_descriptor, name = _open_parent_descriptor(
        lock_path, "JEV_GOVERNANCE_CACHE_INVALID", create=True
    )
    descriptor = -1
    unlock: Callable[[], None] | None = None
    try:
        flags = os.O_RDWR
        if os.name == "posix":
            flags |= os.O_NOFOLLOW
        try:
            try:
                descriptor = os.open(
                    lock_path if parent_descriptor is None else name,
                    flags | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=parent_descriptor,
                )
            except FileExistsError:
                try:
                    descriptor = os.open(
                        lock_path if parent_descriptor is None else name,
                        flags,
                        dir_fd=parent_descriptor,
                    )
                except PermissionError:
                    descriptor = os.open(
                        lock_path if parent_descriptor is None else name,
                        (os.O_RDONLY | os.O_NOFOLLOW) if os.name == "posix" else os.O_RDONLY,
                        dir_fd=parent_descriptor,
                    )
        except OSError as error:
            raise GovernanceError("JEV_GOVERNANCE_CACHE_LOCK_FAILED") from error
        if not _private_regular(os.fstat(descriptor)):
            raise GovernanceError("JEV_GOVERNANCE_CACHE_LOCK_FAILED")
        unlock = _lock_descriptor(descriptor)
        yield
    finally:
        if descriptor >= 0:
            if unlock is not None:
                try:
                    unlock()
                except (OSError, AttributeError):
                    pass
            os.close(descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise GovernanceError("JEV_GOVERNANCE_INPUT_INVALID") from error


def _finite_probability(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1


def _validate_request(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {"schema_version", "ticket", "state", "questions", "model"}:
        raise GovernanceError("JEV_GOVERNANCE_INPUT_INVALID")
    if value.get("schema_version") != 1 or not isinstance(value.get("ticket"), str) or not value["ticket"].strip():
        raise GovernanceError("JEV_GOVERNANCE_INPUT_INVALID")
    questions = value.get("questions")
    if not isinstance(questions, dict) or not questions or len(questions) > 64:
        raise GovernanceError("JEV_GOVERNANCE_INPUT_INVALID")
    for question_id, question in questions.items():
        if (
            not isinstance(question_id, str)
            or not question_id
            or len(question_id) > 64
            or not question_id.isprintable()
        ):
            raise GovernanceError("JEV_GOVERNANCE_INPUT_INVALID")
        if not isinstance(question, dict) or question.get("type") not in {"choice", "noul"}:
            raise GovernanceError("JEV_GOVERNANCE_INPUT_INVALID")
        instructions = question.get("instructions")
        if not isinstance(instructions, str) or not instructions.strip():
            raise GovernanceError("JEV_GOVERNANCE_INPUT_INVALID")
        criteria = question.get("criteria")
        if question["type"] == "choice":
            if not isinstance(criteria, dict) or len(criteria) < 2:
                raise GovernanceError("JEV_GOVERNANCE_INPUT_INVALID")
            if any(not isinstance(option, str) or not option for option in criteria):
                raise GovernanceError("JEV_GOVERNANCE_INPUT_INVALID")
        elif criteria is not None and not isinstance(criteria, dict):
            raise GovernanceError("JEV_GOVERNANCE_INPUT_INVALID")
    model = value.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise GovernanceError("JEV_GOVERNANCE_INPUT_INVALID")
    _canonical(value)
    return value


def fingerprint(value: dict[str, Any], *, provider: str = DEFAULT_PROVIDER) -> str:
    _validate_request(value)
    if provider not in PROVIDERS:
        raise GovernanceError("JEV_GOVERNANCE_INPUT_INVALID")
    material = {
        "policy_version": POLICY_VERSION,
        "threshold": THRESHOLD,
        "provider": provider,
        "request": value,
    }
    return hashlib.sha256(_canonical(material)).hexdigest()


def _review_all(value: dict[str, Any], digest: str, reason: str, *, provider: str) -> dict[str, Any]:
    decisions = {
        question_id: {"value": None, "confidence": None, "disposition": "REVIEW", "reason": reason}
        for question_id in value["questions"]
    }
    return {
        "schema_version": 1,
        "status": "REVIEW",
        "provenance": "LIVE_JEV",
        "provider": provider,
        "threshold": THRESHOLD,
        "fingerprint": digest,
        "decisions": decisions,
        "summary": {"decided": 0, "review": len(decisions)},
        "reason": reason,
    }


def _choice_decision(question: dict[str, Any], answer: Any) -> tuple[Any, float] | None:
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        return None
    choice, confidence, probabilities = answer.get("choice"), answer.get("confidence"), answer.get("probabilities")
    options = set(question["criteria"])
    if choice not in options or not _finite_probability(confidence) or not isinstance(probabilities, dict):
        return None
    assert isinstance(confidence, (int, float))
    if set(probabilities) != options or any(not _finite_probability(item) for item in probabilities.values()):
        return None
    if not 0.999 <= sum(probabilities.values()) <= 1.001:
        return None
    if probabilities[choice] < max(probabilities.values()):
        return None
    return choice, float(confidence)


def _noul_decision(answer: Any) -> tuple[Any, float] | None:
    if not isinstance(answer, dict) or answer.get("type") != "noul" or not _finite_probability(answer.get("noul")):
        return None
    probability = float(answer["noul"])
    return probability >= 0.5, max(probability, 1.0 - probability)


def _parse_remote(value: dict[str, Any], remote: Any, digest: str, *, provider: str) -> dict[str, Any]:
    if not isinstance(remote, dict) or remote.get("status") != "OK":
        reason = remote.get("reason", "JEV_GOVERNANCE_REMOTE_FAILED") if isinstance(remote, dict) else "JEV_GOVERNANCE_REMOTE_FAILED"
        return _review_all(value, digest, str(reason), provider=provider)
    result = remote.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("model"), str) or not isinstance(result.get("answers"), dict):
        return _review_all(value, digest, "JEV_GOVERNANCE_RESPONSE_INVALID", provider=provider)
    if not isinstance(result.get("usage"), dict):
        return _review_all(value, digest, "JEV_GOVERNANCE_RESPONSE_INVALID", provider=provider)
    answers = result["answers"]
    if set(answers) != set(value["questions"]):
        return _review_all(value, digest, "JEV_GOVERNANCE_RESPONSE_INVALID", provider=provider)

    decisions: dict[str, dict[str, Any]] = {}
    decided = 0
    for question_id, question in value["questions"].items():
        parsed = (
            _choice_decision(question, answers[question_id])
            if question["type"] == "choice"
            else _noul_decision(answers[question_id])
        )
        if parsed is None:
            return _review_all(value, digest, "JEV_GOVERNANCE_RESPONSE_INVALID", provider=provider)
        selected, confidence = parsed
        if confidence >= THRESHOLD:
            decisions[question_id] = {
                "value": selected,
                "confidence": confidence,
                "disposition": "DECIDED",
                "reason": "AT_OR_ABOVE_THRESHOLD",
            }
            decided += 1
        else:
            decisions[question_id] = {
                "value": None,
                "confidence": confidence,
                "disposition": "REVIEW",
                "reason": "BELOW_THRESHOLD",
            }
    review = len(decisions) - decided
    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "DECIDED" if review == 0 else "REVIEW",
        "provenance": "LIVE_JEV",
        "provider": provider,
        "model": result["model"],
        "threshold": THRESHOLD,
        "fingerprint": digest,
        "decisions": decisions,
        "summary": {"decided": decided, "review": review},
        "usage": result["usage"],
    }
    if isinstance(remote.get("billing"), dict):
        report["billing"] = remote["billing"]
    return report


def _read_cache(path: Path) -> dict[str, Any]:
    parent_descriptor, name = _open_parent_descriptor(path, "JEV_GOVERNANCE_CACHE_INVALID", create=True)
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
        if os.name == "posix":
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path if parent_descriptor is None else name, flags, dir_fd=parent_descriptor)
        except FileNotFoundError:
            return {"schema_version": 1, "entries": {}}
        metadata = os.fstat(descriptor)
        if not _private_regular(metadata):
            raise GovernanceError("JEV_GOVERNANCE_CACHE_INVALID")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            encoded = stream.read(MAX_INPUT_BYTES + 1)
        if len(encoded) > MAX_INPUT_BYTES:
            raise GovernanceError("JEV_GOVERNANCE_CACHE_INVALID")
        value = json.loads(encoded.decode("utf-8"))
    except GovernanceError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise GovernanceError("JEV_GOVERNANCE_CACHE_INVALID") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("entries"), dict):
        raise GovernanceError("JEV_GOVERNANCE_CACHE_INVALID")
    return value


def _validate_cached_report(
    report: Any,
    digest: str,
    value: dict[str, Any],
    provider: str,
) -> dict[str, Any]:
    allowed = {
        "schema_version", "status", "provenance", "provider", "model",
        "threshold", "fingerprint", "decisions", "summary", "usage", "billing", "reason",
    }
    if (
        not isinstance(report, dict)
        or set(report) - allowed
        or report.get("schema_version") != 1
        or report.get("status") not in {"DECIDED", "REVIEW"}
        or report.get("provenance") != "LIVE_JEV"
        or report.get("provider") != provider
        or report.get("threshold") != THRESHOLD
        or report.get("fingerprint") != digest
        or not isinstance(report.get("decisions"), dict)
        or set(report["decisions"]) != set(value["questions"])
    ):
        raise GovernanceError("JEV_GOVERNANCE_CACHE_INVALID")
    decided = 0
    for question_id, question in value["questions"].items():
        item = report["decisions"][question_id]
        if not isinstance(item, dict) or set(item) != {"value", "confidence", "disposition", "reason"}:
            raise GovernanceError("JEV_GOVERNANCE_CACHE_INVALID")
        if item["disposition"] == "DECIDED":
            if not _finite_probability(item["confidence"]) or item["confidence"] < THRESHOLD:
                raise GovernanceError("JEV_GOVERNANCE_CACHE_INVALID")
            valid_value = (
                item["value"] in question["criteria"]
                if question["type"] == "choice"
                else isinstance(item["value"], bool)
            )
            if not valid_value or item["reason"] != "AT_OR_ABOVE_THRESHOLD":
                raise GovernanceError("JEV_GOVERNANCE_CACHE_INVALID")
            decided += 1
        elif item["disposition"] == "REVIEW":
            if item["value"] is not None or (
                item["confidence"] is not None and not _finite_probability(item["confidence"])
            ) or not isinstance(item["reason"], str):
                raise GovernanceError("JEV_GOVERNANCE_CACHE_INVALID")
        else:
            raise GovernanceError("JEV_GOVERNANCE_CACHE_INVALID")
    review = len(value["questions"]) - decided
    if report.get("summary") != {"decided": decided, "review": review}:
        raise GovernanceError("JEV_GOVERNANCE_CACHE_INVALID")
    if report["status"] != ("DECIDED" if review == 0 else "REVIEW"):
        raise GovernanceError("JEV_GOVERNANCE_CACHE_INVALID")
    if "reason" in report:
        if (
            not isinstance(report["reason"], str)
            or not report["reason"]
            or decided != 0
            or report["status"] != "REVIEW"
            or any(key in report for key in ("model", "usage", "billing"))
        ):
            raise GovernanceError("JEV_GOVERNANCE_CACHE_INVALID")
    else:
        if not isinstance(report.get("model"), str) or not isinstance(report.get("usage"), dict):
            raise GovernanceError("JEV_GOVERNANCE_CACHE_INVALID")
        if "billing" in report and not isinstance(report["billing"], dict):
            raise GovernanceError("JEV_GOVERNANCE_CACHE_INVALID")
    return report


def _write_cache(path: Path, cache: dict[str, Any]) -> None:
    entries = cache["entries"]
    while len(entries) > MAX_CACHE_ENTRIES:
        entries.pop(next(iter(entries)))
    encoded = _canonical(cache) + b"\n"
    parent_descriptor, name = _open_parent_descriptor(path, "JEV_GOVERNANCE_CACHE_INVALID", create=True)
    if parent_descriptor is None:
        descriptor, temporary_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = str(temporary_path)
    else:
        descriptor = -1
        temporary = ""
        for _ in range(128):
            temporary = f".{name}.{secrets.token_hex(8)}"
            try:
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=parent_descriptor,
                )
                break
            except FileExistsError:
                continue
        if descriptor < 0:
            os.close(parent_descriptor)
            raise GovernanceError("JEV_GOVERNANCE_CACHE_INVALID")
    try:
        try:
            existing = os.open(
                path if parent_descriptor is None else name,
                os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | (os.O_NOFOLLOW if os.name == "posix" else 0),
                dir_fd=parent_descriptor,
            )
        except FileNotFoundError:
            existing = -1
        if existing >= 0:
            try:
                if not _private_regular(os.fstat(existing)):
                    raise GovernanceError("JEV_GOVERNANCE_CACHE_INVALID")
            finally:
                os.close(existing)
        _set_private_mode(descriptor)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if parent_descriptor is None:
            os.replace(temporary, path)
        else:
            os.replace(temporary, name, src_dir_fd=parent_descriptor, dst_dir_fd=parent_descriptor)
            os.fsync(parent_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=parent_descriptor)
        except FileNotFoundError:
            pass
        finally:
            if parent_descriptor is not None:
                os.close(parent_descriptor)


def decide(
    value: dict[str, Any],
    evaluator: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    provider: str = DEFAULT_PROVIDER,
    cache_path: Path | None = None,
    platform: str | None = None,
) -> dict[str, Any]:
    value = _validate_request(value)
    if cache_path is not None:
        _require_supported_platform(platform)
    digest = fingerprint(value, provider=provider)
    lock = _cache_lock(cache_path) if cache_path is not None else contextlib.nullcontext()
    with lock:
        try:
            cache = _read_cache(cache_path) if cache_path is not None else None
        except GovernanceError:
            raise
        except Exception as error:
            raise GovernanceError("JEV_GOVERNANCE_CACHE_READ_FAILED") from error
        if cache is not None and digest in cache["entries"]:
            cached = json.loads(json.dumps(_validate_cached_report(
                cache["entries"][digest], digest, value, provider
            )))
            cached["provenance"] = "CACHE"
            return cached

        payload = {"state": value["state"], "questions": value["questions"]}
        if "model" in value:
            payload["model"] = value["model"]
        try:
            remote = evaluator(payload)
        except Exception:
            remote = {"status": "ERROR", "reason": "JEV_GOVERNANCE_EVALUATOR_FAILED"}
        report = _parse_remote(value, remote, digest, provider=provider)
        if cache is not None:
            assert cache_path is not None
            cache["entries"][digest] = report
            try:
                _write_cache(cache_path, cache)
            except Exception:
                report["cache"] = {
                    "status": "ERROR",
                    "reason": "JEV_GOVERNANCE_CACHE_WRITE_FAILED",
                }
        return report


def _load_request(path: Path) -> dict[str, Any]:
    _require_supported_platform()
    parent_descriptor, name = _open_parent_descriptor(path, "JEV_GOVERNANCE_INPUT_INVALID")
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
        if os.name == "posix":
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path if parent_descriptor is None else name, flags, dir_fd=parent_descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_INPUT_BYTES:
            raise GovernanceError("JEV_GOVERNANCE_INPUT_INVALID")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            encoded = stream.read(MAX_INPUT_BYTES + 1)
        if len(encoded) > MAX_INPUT_BYTES:
            raise GovernanceError("JEV_GOVERNANCE_INPUT_INVALID")
        return _validate_request(json.loads(encoded.decode("utf-8")))
    except GovernanceError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise GovernanceError("JEV_GOVERNANCE_INPUT_INVALID") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)


def _connector_evaluator(provider: str, env_file: Path, timeout: str, workspace: Path) -> Callable[[dict[str, Any]], dict[str, Any]]:
    _require_supported_platform()
    def evaluate(payload: dict[str, Any]) -> dict[str, Any]:
        workspace.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix="jev-request-", suffix=".json", dir=workspace)
        request_path = Path(name)
        try:
            _set_private_mode(descriptor)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(_canonical(payload))
            command = [
                sys.executable,
                str(CONNECTOR_PATH),
                "evaluate",
                "--provider",
                provider,
                "--input",
                str(request_path),
                "--env-file",
                str(env_file),
                "--timeout",
                timeout,
                "--json",
            ]
            completed = subprocess.run(command, text=True, capture_output=True, timeout=float(timeout) + 5)
            if completed.stderr:
                print(completed.stderr, file=sys.stderr, end="")
            try:
                return json.loads(completed.stdout)
            except json.JSONDecodeError:
                return {"status": "ERROR", "reason": "JEV_GOVERNANCE_CONNECTOR_INVALID"}
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            request_path.unlink(missing_ok=True)
    return evaluate


def _file_count(state: Any) -> int:
    files = state.get("files") if isinstance(state, dict) else None
    return len(files) if isinstance(files, list) else 0


def _progress_module() -> Any:
    path = Path(__file__).resolve().with_name("terminal_progress.py")
    spec = importlib.util.spec_from_file_location("sdd_terminal_progress_runtime", path)
    if spec is None or spec.loader is None:
        raise GovernanceError("JEV_PROGRESS_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _phase_from_state(state: Any) -> str:
    if not isinstance(state, dict):
        return "não informada"
    stage = state.get("stage")
    candidate = stage.get("current") if isinstance(stage, dict) else stage
    if isinstance(candidate, str) and candidate in {
        "SPECIFY", "CLARIFY", "PLAN", "TASKS", "IMPLEMENT", "TEST", "REVIEW", "DONE"
    }:
        return candidate
    return "não informada"


def _start_progress(path: Path, value: dict[str, Any], provider: str) -> tuple[Any, str] | None:
    if not path.exists():
        return None
    try:
        module = _progress_module()
        progress = module.load_progress(path)
        phase = progress["stage"]["current"]
        progress = module.start_jev(
            progress,
            provider=provider,
            area="classificação semântica",
            questions=list(value["questions"]),
        )
        module.save_progress(path, progress)
        print(module.render(progress, color=False), file=sys.stderr)
        return module, phase
    except Exception as error:
        print(f"aviso de progresso: {type(error).__name__}", file=sys.stderr)
        return None


def _finish_progress(path: Path, context: tuple[Any, str] | None, report: dict[str, Any]) -> None:
    if context is None:
        return
    module, _ = context
    try:
        progress = module.load_progress(path)
        progress = module.finish_jev(
            progress,
            report["status"],
            model=report.get("model"),
        )
        module.save_progress(path, progress)
        print(module.render(progress, color=False), file=sys.stderr)
    except Exception as error:
        print(f"aviso de progresso: {type(error).__name__}", file=sys.stderr)


def _announce_start(value: dict[str, Any], provider: str, phase: str) -> None:
    print("=== JEV EM USO ===", file=sys.stderr)
    print(f"provider: {provider}", file=sys.stderr)
    print(f"fase: {phase}", file=sys.stderr)
    print("atuação: classificação semântica", file=sys.stderr)
    print(f"decisões: {', '.join(value['questions'])}", file=sys.stderr)
    print("==================", file=sys.stderr)


def _announce(
    value: dict[str, Any],
    report: dict[str, Any],
    *,
    provider: str,
    phase: str,
    elapsed_seconds: float,
) -> None:
    print("=== JEV USADO ===", file=sys.stderr)
    print("script: runtime/semantic_governor.py decide", file=sys.stderr)
    print(f"provider: {provider}", file=sys.stderr)
    print(f"fase: {phase}", file=sys.stderr)
    print("atuação: classificação semântica", file=sys.stderr)
    print(f"decisões: {', '.join(value['questions'])}", file=sys.stderr)
    print(f"tempo: {elapsed_seconds:.3f}s", file=sys.stderr)
    print(f"arquivos avaliados: {_file_count(value['state'])}", file=sys.stderr)
    print(
        f"decididos vs revisar: {report['summary']['decided']} vs {report['summary']['review']}",
        file=sys.stderr,
    )
    print(f"artefato: fingerprint {report['fingerprint']}", file=sys.stderr)
    print("==================", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    decide_parser = subparsers.add_parser("decide", help="make one cached, batched semantic decision")
    decide_parser.add_argument("--input", type=Path, required=True)
    decide_parser.add_argument("--provider", choices=PROVIDERS, default=DEFAULT_PROVIDER)
    decide_parser.add_argument("--env-file", type=Path, default=Path(__file__).resolve().parents[2] / ".env")
    decide_parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH)
    decide_parser.add_argument("--progress", type=Path, default=DEFAULT_PROGRESS_PATH)
    decide_parser.add_argument("--timeout", default="30")
    decide_parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        value = _load_request(args.input)
        try:
            timeout = float(args.timeout)
        except (TypeError, ValueError) as error:
            raise GovernanceError("JEV_GOVERNANCE_TIMEOUT_INVALID") from error
        if not math.isfinite(timeout) or not 0 < timeout <= 300:
            raise GovernanceError("JEV_GOVERNANCE_TIMEOUT_INVALID")
        live: dict[str, Any] = {}
        def evaluator(payload: dict[str, Any]) -> dict[str, Any]:
            live["started"] = time.monotonic()
            live["progress"] = _start_progress(args.progress, value, args.provider)
            live["phase"] = (
                live["progress"][1]
                if live["progress"] is not None
                else _phase_from_state(value["state"])
            )
            _announce_start(value, args.provider, live["phase"])
            return _connector_evaluator(
                args.provider, args.env_file, args.timeout, args.cache.parent
            )(payload)
        report = decide(value, evaluator, provider=args.provider, cache_path=args.cache)
        if report["provenance"] == "LIVE_JEV":
            _finish_progress(args.progress, live.get("progress"), report)
            _announce(
                value,
                report,
                provider=args.provider,
                phase=live.get("phase", _phase_from_state(value["state"])),
                elapsed_seconds=time.monotonic() - live.get("started", time.monotonic()),
            )
        print(json.dumps(report, ensure_ascii=False) if args.json else report["status"])
        return 0 if report["status"] == "DECIDED" else 2
    except GovernanceError as error:
        report = {"status": "BLOCKED", "reason": str(error)}
        print(json.dumps(report) if args.json else f"BLOCKED: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
