#!/usr/bin/env python3
"""Batch, threshold, and cache semantic SDD decisions made by Jev."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import importlib.util
import json
import math
import os
import secrets
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

if os.name == "posix":
    import fcntl

THRESHOLD = 0.70
POLICY_VERSION = 1
MAX_CACHE_ENTRIES = 128
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_TERMINAL_TEXT = 240
DEFAULT_PROVIDER = "typesafe"
PROVIDERS = ("typesafe", "jev-ai")
DEFAULT_CACHE_PATH = Path(__file__).resolve().parents[1] / "JEV_CACHE.json"
DEFAULT_PROGRESS_PATH = Path(__file__).resolve().parents[1] / "TERMINAL_PROGRESS.json"
DEFAULT_PROJECT_SETUP_PATH = Path(__file__).resolve().parents[1] / "PROJECT_SETUP.md"
CONNECTOR_PATH = Path(__file__).resolve().with_name("typesafe_connector.py")


class GovernanceError(ValueError):
    """The governance request or persisted cache is unsafe or malformed."""


class EvaluationNotStarted(RuntimeError):
    """The connector process provably did not start, so no request was sent."""


def _platform_name() -> str:
    return os.name


def _require_supported_platform() -> None:
    if _platform_name() != "posix":
        raise GovernanceError("JEV_GOVERNANCE_PLATFORM_UNSUPPORTED")


def _open_parent_descriptor(path: Path, reason: str, *, create: bool = False) -> tuple[int | None, str]:
    """Open every parent component without following links."""
    _require_supported_platform()
    if not path.name or path.name in {".", ".."}:
        raise GovernanceError(reason)

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


def _lock_descriptor(
    descriptor: int,
    *,
    platform: str | None = None,
    windows_module: Any | None = None,
) -> Callable[[], None]:
    """Lock one cache descriptor and return its matching unlock operation."""
    selected = platform or os.name
    if selected == "posix":
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError as error:
            raise GovernanceError("JEV_GOVERNANCE_CACHE_LOCK_FAILED") from error

        def unlock_posix() -> None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)

        return unlock_posix
    if selected == "nt":
        module = windows_module or importlib.import_module("msvcrt")
        try:
            if os.fstat(descriptor).st_size == 0:
                os.lseek(descriptor, 0, os.SEEK_SET)
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            module.locking(descriptor, module.LK_LOCK, 1)
        except (OSError, AttributeError) as error:
            raise GovernanceError("JEV_GOVERNANCE_CACHE_LOCK_FAILED") from error

        def unlock_windows() -> None:
            os.lseek(descriptor, 0, os.SEEK_SET)
            module.locking(descriptor, module.LK_UNLCK, 1)

        return unlock_windows
    raise GovernanceError("JEV_GOVERNANCE_CACHE_LOCK_FAILED")


@contextlib.contextmanager
def _cache_lock(path: Path):
    """Keep a private lock inode so unlinking cannot split lock domains."""
    lock_path = path.with_name(f".{path.name}.lock")
    parent_descriptor, name = _open_parent_descriptor(
        lock_path, "JEV_GOVERNANCE_CACHE_INVALID", create=True
    )
    assert parent_descriptor is not None
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
        yield parent_descriptor, path.name
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


def _cache_encoded(value: dict[str, Any]) -> bytes:
    """Encode cache without sorting entries so persisted age order survives reload."""
    try:
        return json.dumps(
            value,
            sort_keys=False,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise GovernanceError("JEV_GOVERNANCE_CACHE_INVALID") from error


def _finite_probability(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1


def _validate_request(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {"schema_version", "ticket", "state", "questions", "model"}:
        raise GovernanceError("JEV_GOVERNANCE_INPUT_INVALID")
    if "state" not in value:
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


def _valid_remote_model(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and len(value) <= MAX_TERMINAL_TEXT
        and value.isprintable()
    )


def _parse_remote(value: dict[str, Any], remote: Any, digest: str, *, provider: str) -> dict[str, Any]:
    if not isinstance(remote, dict) or remote.get("status") != "OK":
        reason = remote.get("reason", "JEV_GOVERNANCE_REMOTE_FAILED") if isinstance(remote, dict) else "JEV_GOVERNANCE_REMOTE_FAILED"
        return _review_all(value, digest, str(reason), provider=provider)
    result = remote.get("result")
    if (
        not isinstance(result, dict)
        or not _valid_remote_model(result.get("model"))
        or not isinstance(result.get("answers"), dict)
    ):
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


def _read_cache_at(parent_descriptor: int, name: str) -> dict[str, Any]:
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | os.O_NOFOLLOW
        try:
            descriptor = os.open(name, flags, dir_fd=parent_descriptor)
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
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("entries"), dict):
        raise GovernanceError("JEV_GOVERNANCE_CACHE_INVALID")
    return value


def _read_cache(path: Path) -> dict[str, Any]:
    parent_descriptor, name = _open_parent_descriptor(
        path, "JEV_GOVERNANCE_CACHE_INVALID", create=True
    )
    assert parent_descriptor is not None
    try:
        return _read_cache_at(parent_descriptor, name)
    finally:
        os.close(parent_descriptor)


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
        if not _valid_remote_model(report.get("model")) or not isinstance(report.get("usage"), dict):
            raise GovernanceError("JEV_GOVERNANCE_CACHE_INVALID")
        if "billing" in report and not isinstance(report["billing"], dict):
            raise GovernanceError("JEV_GOVERNANCE_CACHE_INVALID")
    return report


def _write_cache_at(
    parent_descriptor: int,
    name: str,
    cache: dict[str, Any],
    *,
    prior_tombstone_is_safe: bool = False,
) -> None:
    entries = cache["entries"]
    while len(entries) > MAX_CACHE_ENTRIES:
        entries.pop(next(iter(entries)))
    encoded = _cache_encoded(cache)
    while len(encoded) > MAX_INPUT_BYTES and len(entries) > 1:
        entries.pop(next(iter(entries)))
        encoded = _cache_encoded(cache)
    if len(encoded) > MAX_INPUT_BYTES:
        raise GovernanceError("JEV_GOVERNANCE_CACHE_INVALID")
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
        raise GovernanceError("JEV_GOVERNANCE_CACHE_INVALID")
    try:
        try:
            existing = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | os.O_NOFOLLOW,
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
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=parent_descriptor, dst_dir_fd=parent_descriptor)
        try:
            os.fsync(parent_descriptor)
        except OSError:
            if not prior_tombstone_is_safe:
                raise
            # The file data and rename are already visible. After a crash, either
            # this final report or the prior durable tombstone survives; both
            # suppress an automatic retry for the billed fingerprint.
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=parent_descriptor)
        except FileNotFoundError:
            pass


def _write_cache(path: Path, cache: dict[str, Any]) -> None:
    parent_descriptor, name = _open_parent_descriptor(
        path, "JEV_GOVERNANCE_CACHE_INVALID", create=True
    )
    assert parent_descriptor is not None
    try:
        _write_cache_at(parent_descriptor, name, cache)
    finally:
        os.close(parent_descriptor)


def decide(
    value: dict[str, Any],
    evaluator: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    provider: str = DEFAULT_PROVIDER,
    cache_path: Path | None = None,
    prepare: Callable[[], None] | None = None,
) -> dict[str, Any]:
    _require_supported_platform()
    value = _validate_request(value)
    digest = fingerprint(value, provider=provider)
    lock = _cache_lock(cache_path) if cache_path is not None else contextlib.nullcontext(None)
    with lock as cache_location:
        try:
            cache = (
                _read_cache_at(*cache_location)
                if cache_location is not None
                else None
            )
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

        if prepare is not None:
            prepare()

        if cache is not None:
            assert cache_path is not None
            cache["entries"][digest] = _review_all(
                value,
                digest,
                "JEV_GOVERNANCE_IN_FLIGHT_UNCERTAIN",
                provider=provider,
            )
            try:
                assert cache_location is not None
                _write_cache_at(*cache_location, cache)
            except Exception as error:
                raise GovernanceError("JEV_GOVERNANCE_CACHE_WRITE_FAILED") from error

        payload = {"state": value["state"], "questions": value["questions"]}
        if "model" in value:
            payload["model"] = value["model"]
        try:
            remote = evaluator(payload)
        except EvaluationNotStarted as error:
            if cache is not None:
                cache["entries"].pop(digest, None)
                try:
                    assert cache_location is not None
                    _write_cache_at(*cache_location, cache)
                except Exception as persistence_error:
                    raise GovernanceError(
                        "JEV_GOVERNANCE_CACHE_WRITE_FAILED"
                    ) from persistence_error
            raise GovernanceError("JEV_GOVERNANCE_EVALUATION_NOT_STARTED") from error
        except GovernanceError:
            raise
        except Exception:
            remote = {"status": "ERROR", "reason": "JEV_GOVERNANCE_EVALUATOR_FAILED"}
        report = _parse_remote(value, remote, digest, provider=provider)
        if cache is not None:
            assert cache_path is not None
            cache["entries"][digest] = report
            try:
                assert cache_location is not None
                _write_cache_at(
                    *cache_location,
                    cache,
                    prior_tombstone_is_safe=True,
                )
            except Exception:
                failed_report = _review_all(
                    value,
                    digest,
                    "JEV_GOVERNANCE_FINAL_PERSISTENCE_FAILED",
                    provider=provider,
                )
                failed_report["receipt"] = {
                    "network_call": "COMPLETED",
                    "result_status": report["status"],
                    "reason": "JEV_GOVERNANCE_CACHE_WRITE_FAILED",
                }
                return failed_report
        return report


def _load_request(path: Path) -> dict[str, Any]:
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


def _load_project_setup_consent(path: Path) -> None:
    parent_descriptor, name = _open_parent_descriptor(
        path, "JEV_GOVERNANCE_PROJECT_SETUP_INVALID"
    )
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
        if os.name == "posix":
            flags |= os.O_NOFOLLOW
        descriptor = os.open(
            path if parent_descriptor is None else name,
            flags,
            dir_fd=parent_descriptor,
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_INPUT_BYTES:
            raise GovernanceError("JEV_GOVERNANCE_PROJECT_SETUP_INVALID")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            encoded = stream.read(MAX_INPUT_BYTES + 1)
        if len(encoded) > MAX_INPUT_BYTES:
            raise GovernanceError("JEV_GOVERNANCE_PROJECT_SETUP_INVALID")
        lines = encoded.decode("utf-8").splitlines()
    except FileNotFoundError as error:
        raise GovernanceError("JEV_GOVERNANCE_CONSENT_REQUIRED") from error
    except GovernanceError:
        raise
    except (OSError, UnicodeError) as error:
        raise GovernanceError("JEV_GOVERNANCE_PROJECT_SETUP_INVALID") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)

    yaml_starts = [index for index, line in enumerate(lines) if line == "```yaml"]
    if len(yaml_starts) != 1:
        raise GovernanceError("JEV_GOVERNANCE_PROJECT_SETUP_INVALID")
    start = yaml_starts[0] + 1
    try:
        end = lines.index("```", start)
    except ValueError:
        raise GovernanceError("JEV_GOVERNANCE_PROJECT_SETUP_INVALID") from None
    record = lines[start:end]
    if record.count("schema_version: 1") != 1 or record.count("answers:") != 1:
        raise GovernanceError("JEV_GOVERNANCE_PROJECT_SETUP_INVALID")
    answers_start = record.index("answers:") + 1
    answer_lines: list[str] = []
    for line in record[answers_start:]:
        if not line.strip():
            continue
        if not line.startswith("  "):
            break
        answer_lines.append(line)
    matches = [
        line.split(":", 1)[1].strip()
        for line in answer_lines
        if line.startswith("  typesafe_ai:") and not line.startswith("    ")
    ]
    if len(matches) != 1:
        raise GovernanceError("JEV_GOVERNANCE_CONSENT_REQUIRED")
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        consent = json.loads(matches[0], object_pairs_hook=unique_object)
    except (json.JSONDecodeError, RecursionError, ValueError):
        raise GovernanceError("JEV_GOVERNANCE_CONSENT_REQUIRED") from None
    if (
        not isinstance(consent, dict)
        or set(consent) != {"install", "automatic_semantic_governance"}
        or consent["install"] is not True
        or consent["automatic_semantic_governance"] is not True
    ):
        raise GovernanceError("JEV_GOVERNANCE_CONSENT_REQUIRED")


def _connector_preflight(provider: str, env_file: Path) -> None:
    command = [
        sys.executable,
        str(CONNECTOR_PATH),
        "preflight",
        "--provider",
        provider,
        "--env-file",
        str(env_file),
        "--json",
    ]
    try:
        completed = subprocess.run(command, text=True, capture_output=True, timeout=10)
        report = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        raise GovernanceError("JEV_GOVERNANCE_PREFLIGHT_FAILED") from error
    if not isinstance(report, dict) or completed.returncode != 0 or report.get("status") != "READY":
        raise GovernanceError("JEV_GOVERNANCE_PREFLIGHT_FAILED")


def _connector_evaluator(
    provider: str,
    env_file: Path,
    timeout: str,
    _workspace: Path,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def evaluate(payload: dict[str, Any]) -> dict[str, Any]:
        command = [
            sys.executable,
            str(CONNECTOR_PATH),
            "evaluate",
            "--provider",
            provider,
            "--input-stdin",
            "--env-file",
            str(env_file),
            "--timeout",
            timeout,
            "--json",
        ]
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as error:
            raise EvaluationNotStarted from error
        try:
            stdout, stderr = process.communicate(
                input=_canonical(payload), timeout=float(timeout) + 5
            )
        except BaseException:
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait()
            except (OSError, subprocess.SubprocessError):
                pass
            raise
        if stderr:
            print(stderr.decode("utf-8", errors="replace"), file=sys.stderr, end="")
        try:
            return json.loads(stdout.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            return {"status": "ERROR", "reason": "JEV_GOVERNANCE_CONNECTOR_INVALID"}
    return evaluate


def _connector_prepare(provider: str, env_file: Path, project_setup: Path) -> None:
    """Complete every provably pre-network check before a tombstone is needed."""
    _load_project_setup_consent(project_setup)
    _connector_preflight(provider, env_file)


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
    def safe_phase(value: Any) -> str:
        if (
            isinstance(value, str)
            and value
            and len(value) <= 64
            and value.isprintable()
        ):
            return value
        return "não informada"

    if not isinstance(state, dict):
        return "não informada"
    stage = state.get("stage")
    if isinstance(stage, dict):
        return safe_phase(stage.get("current"))
    if isinstance(stage, str):
        return safe_phase(stage)
    return "não informada"


def _start_progress(path: Path, value: dict[str, Any], provider: str) -> tuple[Any, str] | None:
    if not path.exists():
        return None
    try:
        module = _progress_module()
        current: dict[str, str] = {}

        def begin(progress: dict[str, Any]) -> dict[str, Any]:
            current["phase"] = progress["stage"]["current"]
            return module.start_jev(
                progress,
                provider=provider,
                area="classificação semântica",
                questions=list(value["questions"]),
            )

        progress = module.update_progress(path, begin)
        print(module.render(progress, color=False), file=sys.stderr)
        return module, current["phase"]
    except Exception as error:
        print(f"aviso de progresso: {type(error).__name__}", file=sys.stderr)
        return None


def _finish_progress(path: Path, context: tuple[Any, str] | None, report: dict[str, Any]) -> None:
    if context is None:
        return
    module, _ = context
    try:
        progress = module.update_progress(
            path,
            lambda value: module.finish_jev(
                value,
                report["status"],
                model=report.get("model"),
            ),
        )
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
    decide_parser.add_argument("--project-setup", type=Path, default=DEFAULT_PROJECT_SETUP_PATH)
    decide_parser.add_argument("--timeout", default="30")
    decide_parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    live: dict[str, Any] = {}
    try:
        _require_supported_platform()
        _load_project_setup_consent(args.project_setup)
        value = _load_request(args.input)
        try:
            timeout = float(args.timeout)
        except (TypeError, ValueError) as error:
            raise GovernanceError("JEV_GOVERNANCE_TIMEOUT_INVALID") from error
        if not math.isfinite(timeout) or not 0 < timeout <= 300:
            raise GovernanceError("JEV_GOVERNANCE_TIMEOUT_INVALID")
        def prepare() -> None:
            _connector_prepare(args.provider, args.env_file, args.project_setup)

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
        report = decide(
            value,
            evaluator,
            provider=args.provider,
            cache_path=args.cache,
            prepare=prepare,
        )
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
        _finish_progress(args.progress, live.get("progress"), report)
        print(json.dumps(report) if args.json else f"BLOCKED: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
