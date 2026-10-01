#!/usr/bin/env python3
"""Explicit, repository-local connector for System One APIs (TypeSafe or the Jev AI compatible endpoint)."""
from __future__ import annotations

import argparse
import errno
import http.client
import json
import math
import os
import re
import stat
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

API_KEY_NAME = "TYPESAFE_API_KEY"
DEFAULT_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
MAX_TIMEOUT_SECONDS = 300.0
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_JSON_DEPTH = 64
# Each provider pairs exactly one credential with exactly one fixed destination.
# A key is never sent to another provider and there is no automatic fallback.
PROVIDERS: dict[str, dict[str, Any]] = {
    "typesafe": {
        "key_name": API_KEY_NAME,
        "base_url": "https://api.typesafe.ai",
        "models": False,
        "max_body_bytes": None,
        "max_questions": None,
        "max_question_id": None,
    },
    "jev-ai": {
        "key_name": "JEV_AI_API_KEY",
        "base_url": "https://jev-ai.pro/api",
        "models": True,
        "max_body_bytes": 256000,
        "max_questions": 64,
        "max_question_id": 64,
    },
}
DEFAULT_PROVIDER = "typesafe"
SYSTEM_ONE_PATH = "/v1/systemone"
MODELS_PATH = "/v1/models"
JEV_BILLING_HEADERS = {
    "X-Jev-Run-Id": "run_id",
    "X-Jev-Billing": "billing",
    "X-Jev-Paid-Input-Tokens-Used": "paid_input_tokens_used",
    "X-Jev-Credits-Charged": "credits_charged",
    "X-Jev-Tokens-Remaining": "tokens_remaining",
    "X-Jev-Credits-Remaining": "credits_remaining",
}
HTTP_REASONS = {
    401: "TYPESAFE_AUTHENTICATION_FAILED",
    422: "TYPESAFE_REQUEST_REJECTED",
    429: "TYPESAFE_RATE_LIMITED",
    529: "TYPESAFE_OVERLOADED",
}
# Jev AI documents additional statuses; the TypeSafe mapping above is unchanged.
JEV_HTTP_REASONS = {
    **HTTP_REASONS,
    402: "TYPESAFE_PAYMENT_REQUIRED",
    404: "TYPESAFE_ENDPOINT_NOT_FOUND",
    502: "TYPESAFE_UNAVAILABLE",
    503: "TYPESAFE_UNAVAILABLE",
    504: "TYPESAFE_UPSTREAM_TIMEOUT",
}
PROVIDERS["typesafe"]["http_reasons"] = HTTP_REASONS
PROVIDERS["jev-ai"]["http_reasons"] = JEV_HTTP_REASONS
RETRY_AFTER_PATTERN = re.compile(r"[0-9]{1,6}", re.ASCII)
# Failures after which a POST may or may not have run (and been billed).
# Check usage before sending the request again; never replay automatically.
UNCERTAIN_OUTCOME_STATUSES = {504}


def provider_urls(provider: str) -> dict[str, str]:
    base = PROVIDERS[provider]["base_url"]
    return {"base_url": base, "systemone": base + SYSTEM_ONE_PATH, "models": base + MODELS_PATH}


class TypeSafeResponseError(RuntimeError):
    pass


class TypeSafeConfigError(RuntimeError):
    pass


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def _open_request(request: urllib.request.Request, timeout: float):
    return urllib.request.build_opener(NoRedirectHandler()).open(request, timeout=timeout)


def _open_env_descriptor(path: Path) -> int | None:
    if os.name != "posix":
        if path.is_symlink():
            raise TypeSafeConfigError("TYPESAFE_ENV_INVALID")
        try:
            return os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        except FileNotFoundError:
            return None
        except OSError as error:
            raise TypeSafeConfigError("TYPESAFE_ENV_INVALID") from error

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    anchor = path.anchor or "."
    parts = path.parts[1:] if path.anchor else path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise TypeSafeConfigError("TYPESAFE_ENV_INVALID")
    descriptor: int | None = None
    try:
        descriptor = os.open(anchor, directory_flags)
        for part in parts[:-1]:
            next_descriptor = os.open(part, directory_flags, dir_fd=descriptor)
            previous_descriptor = descriptor
            try:
                os.close(previous_descriptor)
            except OSError as error:
                try:
                    os.close(next_descriptor)
                except OSError:
                    pass
                descriptor = None
                raise TypeSafeConfigError("TYPESAFE_ENV_INVALID") from error
            descriptor = next_descriptor
        file_descriptor = os.open(parts[-1], file_flags, dir_fd=descriptor)
        try:
            os.close(descriptor)
        except OSError as error:
            try:
                os.close(file_descriptor)
            except OSError:
                pass
            descriptor = None
            raise TypeSafeConfigError("TYPESAFE_ENV_INVALID") from error
        descriptor = None
        return file_descriptor
    except FileNotFoundError:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        return None
    except OSError as error:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if error.errno == errno.ENOENT:
            return None
        raise TypeSafeConfigError("TYPESAFE_ENV_INVALID") from error


def _env_api_key(path: Path, key_name: str = API_KEY_NAME) -> str | None:
    value = os.environ.get(key_name, "")
    if value:
        return value
    descriptor = _open_env_descriptor(path)
    if descriptor is None:
        return None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise TypeSafeConfigError("TYPESAFE_ENV_INVALID")
        if os.name == "posix" and (
            stat.S_IMODE(metadata.st_mode) & 0o077
            or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
        ):
            raise TypeSafeConfigError("TYPESAFE_ENV_INVALID")
        stream = os.fdopen(descriptor, "r", encoding="utf-8")
        descriptor = -1
        with stream:
            lines = stream.read().splitlines()
    except TypeSafeConfigError:
        raise
    except (OSError, UnicodeError) as error:
        raise TypeSafeConfigError("TYPESAFE_ENV_INVALID") from error
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
    matches: list[str] = []
    for line in lines:
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        name, candidate = line.split("=", 1)
        if name.strip() == key_name:
            matches.append(candidate)
    if len(matches) > 1:
        raise TypeSafeConfigError("TYPESAFE_ENV_INVALID")
    return matches[0] or None if matches else None


def _valid_api_key(value: str) -> bool:
    return bool(value) and all(not character.isspace() and 32 < ord(character) < 127 for character in value)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _load_json(value: str) -> Any:
    return json.loads(value, parse_constant=_reject_json_constant, object_pairs_hook=_unique_json_object)


def _validate_json_depth(value: Any) -> None:
    stack = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if depth > MAX_JSON_DEPTH:
            raise ValueError("JSON nesting exceeds limit")
        if isinstance(item, dict):
            stack.extend((nested, depth + 1) for nested in item.values())
        elif isinstance(item, list):
            stack.extend((nested, depth + 1) for nested in item)


def _encoded_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")


def _request_payload(path: Path, provider: str = DEFAULT_PROVIDER) -> dict[str, Any]:
    descriptor: int | None = None
    try:
        descriptor = _open_env_descriptor(path)
        if descriptor is None:
            raise ValueError("input missing")
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("input is not a regular file")
        stream = os.fdopen(descriptor, "rb")
        descriptor = None
        with stream:
            raw = stream.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("input too large")
        payload = _load_json(raw.decode("utf-8"))
        _validate_json_depth(payload)
    except (OSError, UnicodeError, ValueError, RecursionError, TypeSafeConfigError) as error:
        raise ValueError("TYPESAFE_INPUT_INVALID") from error
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    if not isinstance(payload, dict) or set(payload) - {"state", "questions", "model"}:
        raise ValueError("TYPESAFE_INPUT_INVALID")
    if "state" not in payload or not isinstance(payload.get("questions"), dict) or not payload["questions"]:
        raise ValueError("TYPESAFE_INPUT_INVALID")
    model = payload.get("model", DEFAULT_MODEL)
    if not isinstance(model, str) or not model.strip():
        raise ValueError("TYPESAFE_INPUT_INVALID")
    normalized = {"state": payload["state"], "model": model, "questions": payload["questions"]}
    try:
        encoded = _encoded_json(normalized)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ValueError("TYPESAFE_INPUT_INVALID") from error
    limits = PROVIDERS[provider]
    if limits["max_body_bytes"] is not None and len(encoded) > limits["max_body_bytes"]:
        raise ValueError("TYPESAFE_INPUT_OVER_LIMIT")
    if limits["max_questions"] is not None and len(normalized["questions"]) > limits["max_questions"]:
        raise ValueError("TYPESAFE_INPUT_OVER_LIMIT")
    if limits["max_question_id"] is not None and any(
        len(question_id) > limits["max_question_id"] for question_id in normalized["questions"]
    ):
        raise ValueError("TYPESAFE_INPUT_OVER_LIMIT")
    return normalized


def _read_json_response(response: Any) -> Any:
    try:
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("response too large")
        decoded = _load_json(body.decode("utf-8"))
        _validate_json_depth(decoded)
        _encoded_json(decoded)
    except (UnicodeError, ValueError, RecursionError, http.client.HTTPException) as error:
        raise TypeSafeResponseError("TYPESAFE_RESPONSE_INVALID") from error
    return decoded


def _billing_headers(response: Any) -> dict[str, str]:
    headers = getattr(response, "headers", None)
    if headers is None:
        return {}
    billing: dict[str, str] = {}
    for header, field in JEV_BILLING_HEADERS.items():
        value = headers.get(header)
        if isinstance(value, str) and value and len(value) <= 128 and value.isprintable():
            billing[field] = value
    return billing


def _evaluate(
    endpoint: str,
    key: str,
    payload: dict[str, Any],
    timeout: float,
    billing: dict[str, str] | None = None,
    accept_json: bool = False,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if accept_json:
        headers["Accept"] = "application/json"
    request = urllib.request.Request(
        endpoint,
        data=_encoded_json(payload),
        headers=headers,
        method="POST",
    )
    with _open_request(request, timeout=timeout) as response:
        # Capture billing before parsing: a malformed 200 may still have been billed.
        if billing is not None:
            billing.update(_billing_headers(response))
        decoded = _read_json_response(response)
    if (
        not isinstance(decoded, dict)
        or not isinstance(decoded.get("model"), str)
        or not isinstance(decoded.get("answers"), dict)
        or not isinstance(decoded.get("usage"), dict)
    ):
        raise TypeSafeResponseError("TYPESAFE_RESPONSE_INVALID")
    return decoded


def _list_models(endpoint: str, key: str, timeout: float) -> list[dict[str, Any]]:
    """Authenticated GET of the exposed models; never runs inference."""
    request = urllib.request.Request(
        endpoint,
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        method="GET",
    )
    with _open_request(request, timeout=timeout) as response:
        decoded = _read_json_response(response)
    models = decoded.get("models") if isinstance(decoded, dict) else None
    if not isinstance(models, list) or not all(
        isinstance(item, dict) and isinstance(item.get("name"), str) for item in models
    ):
        raise TypeSafeResponseError("TYPESAFE_RESPONSE_INVALID")
    return [
        {"name": item["name"], "description": item["description"]}
        if isinstance(item.get("description"), str)
        else {"name": item["name"]}
        for item in models
    ]


def _emit(report: dict[str, Any], as_json: bool, text: str) -> None:
    print(json.dumps(report, ensure_ascii=False) if as_json else text)


def _http_error_report(error: urllib.error.HTTPError, provider: str) -> dict[str, Any]:
    try:
        report: dict[str, Any] = {
            "status": "ERROR",
            "reason": PROVIDERS[provider]["http_reasons"].get(error.code, "TYPESAFE_HTTP_ERROR"),
            "http_status": error.code,
        }
        if provider != DEFAULT_PROVIDER:
            retry_after = error.headers.get("Retry-After") if error.headers is not None else None
            if isinstance(retry_after, str) and RETRY_AFTER_PATTERN.fullmatch(retry_after.strip()):
                report["retry_after_seconds"] = int(retry_after.strip())
    finally:
        error.close()
    if provider != DEFAULT_PROVIDER:
        report["provider"] = provider
    return report


def _transport_report(error: BaseException, provider: str) -> dict[str, Any]:
    if isinstance(error, TimeoutError) or (
        isinstance(error, urllib.error.URLError) and isinstance(error.reason, TimeoutError)
    ):
        reason = "TYPESAFE_TIMEOUT"
    else:
        reason = "TYPESAFE_TRANSPORT_ERROR"
    report: dict[str, Any] = {"status": "ERROR", "reason": reason}
    if provider != DEFAULT_PROVIDER:
        report["provider"] = provider
    return report


def _key_reason(provider: str, suffix: str) -> str:
    return f"{PROVIDERS[provider]['key_name']}_{suffix}"


def _timeout(value: str) -> float | None:
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        return None
    return timeout if math.isfinite(timeout) and 0 < timeout <= MAX_TIMEOUT_SECONDS else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "--provider",
            choices=sorted(PROVIDERS),
            default=DEFAULT_PROVIDER,
            help="credential/destination pair; jev-ai uses JEV_AI_API_KEY with https://jev-ai.pro/api",
        )
        command.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH)
        command.add_argument("--json", action="store_true")

    preflight = subparsers.add_parser("preflight", help="check local configuration and destination without network access")
    add_common(preflight)
    models = subparsers.add_parser("models", help="authenticated model lookup without inference (jev-ai only)")
    add_common(models)
    models.add_argument("--timeout", default="30")
    evaluate = subparsers.add_parser("evaluate", help="send one explicit typed evaluation; this is billed")
    add_common(evaluate)
    evaluate.add_argument("--input", type=Path, required=True, help="JSON file with state, questions, and optional model")
    evaluate.add_argument("--timeout", default="30")
    args = parser.parse_args()
    provider = args.provider
    key_name = PROVIDERS[provider]["key_name"]
    urls = provider_urls(provider)

    try:
        key = _env_api_key(args.env_file, key_name)
    except TypeSafeConfigError as error:
        _emit({"status": "BLOCKED", "reason": str(error)}, args.json, f"BLOCKED: {error}")
        return 2
    if key is None:
        reason = _key_reason(provider, "MISSING")
        _emit({"status": "BLOCKED", "reason": reason}, args.json, f"BLOCKED: {reason}")
        return 2
    if not _valid_api_key(key):
        reason = _key_reason(provider, "INVALID")
        _emit({"status": "BLOCKED", "reason": reason}, args.json, f"BLOCKED: {reason}")
        return 2

    if args.command in {"evaluate", "models"}:
        timeout = _timeout(args.timeout)
        if timeout is None:
            _emit({"status": "BLOCKED", "reason": "TYPESAFE_TIMEOUT_INVALID"}, args.json, "BLOCKED: TYPESAFE_TIMEOUT_INVALID")
            return 2
        if args.command == "models" and not PROVIDERS[provider]["models"]:
            _emit({"status": "BLOCKED", "reason": "MODELS_UNSUPPORTED"}, args.json, "BLOCKED: MODELS_UNSUPPORTED")
            return 2
        billing: dict[str, str] = {}
        try:
            if args.command == "models":
                listed = _list_models(urls["models"], key, timeout)
            else:
                payload = _request_payload(args.input, provider)
                result = _evaluate(
                    urls["systemone"], key, payload, timeout, billing, accept_json=provider != DEFAULT_PROVIDER
                )
        except urllib.error.HTTPError as error:
            report = _http_error_report(error, provider)
            if args.command == "evaluate" and provider != DEFAULT_PROVIDER and error.code in UNCERTAIN_OUTCOME_STATUSES:
                report["outcome"] = "UNCERTAIN"
            _emit(report, args.json, f"ERROR: {report['reason']}")
            return 3
        except TypeSafeResponseError as error:
            report: dict[str, Any] = {"status": "ERROR", "reason": str(error)}
            if args.command == "evaluate" and provider != DEFAULT_PROVIDER:
                # A 2xx body that cannot be parsed may still have run and been billed.
                report.update({"provider": provider, "outcome": "UNCERTAIN", "billing": billing})
            _emit(report, args.json, f"ERROR: {error}")
            return 3
        except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
            report = _transport_report(error, provider)
            if args.command == "evaluate" and provider != DEFAULT_PROVIDER:
                # The POST may have run and been billed; check usage before resending.
                report["outcome"] = "UNCERTAIN"
            _emit(report, args.json, f"ERROR: {report['reason']}")
            return 3
        except ValueError as error:
            _emit({"status": "BLOCKED", "reason": str(error)}, args.json, f"BLOCKED: {error}")
            return 2
        if args.command == "models":
            report = {"status": "OK", "provider": provider, "endpoint": urls["models"], "models": listed}
            _emit(report, args.json, "\n".join(model["name"] for model in listed))
            return 0
        report = {"status": "OK", "result": result}
        if provider != DEFAULT_PROVIDER:
            report.update({"provider": provider, "endpoint": urls["systemone"], "billing": billing})
        _emit(report, args.json, "OK")
        return 0

    report = {"status": "READY", "source": "environment" if os.environ.get(key_name) else "env_file"}
    if provider != DEFAULT_PROVIDER:
        report.update({"provider": provider, "key_name": key_name, **urls})
    _emit(report, args.json, "READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
