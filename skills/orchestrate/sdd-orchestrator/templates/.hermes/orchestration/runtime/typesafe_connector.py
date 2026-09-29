#!/usr/bin/env python3
"""Explicit, repository-local connector for the TypeSafe System One API."""
from __future__ import annotations

import argparse
import errno
import http.client
import json
import math
import os
import stat
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

API_KEY_NAME = "TYPESAFE_API_KEY"
DEFAULT_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
MAX_TIMEOUT_SECONDS = 300.0
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_JSON_DEPTH = 64
HTTP_REASONS = {
    401: "TYPESAFE_AUTHENTICATION_FAILED",
    422: "TYPESAFE_REQUEST_REJECTED",
    429: "TYPESAFE_RATE_LIMITED",
    529: "TYPESAFE_OVERLOADED",
}


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


def _env_api_key(path: Path) -> str | None:
    value = os.environ.get(API_KEY_NAME, "")
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
        if name.strip() == API_KEY_NAME:
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


def _request_payload(path: Path) -> dict[str, Any]:
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
        _encoded_json(normalized)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ValueError("TYPESAFE_INPUT_INVALID") from error
    return normalized


def _evaluate(endpoint: str, key: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        endpoint,
        data=_encoded_json(payload),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with _open_request(request, timeout=timeout) as response:
        try:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError("response too large")
            decoded = _load_json(body.decode("utf-8"))
            _validate_json_depth(decoded)
        except (UnicodeError, ValueError, RecursionError, http.client.HTTPException) as error:
            raise TypeSafeResponseError("TYPESAFE_RESPONSE_INVALID") from error
    if (
        not isinstance(decoded, dict)
        or not isinstance(decoded.get("model"), str)
        or not isinstance(decoded.get("answers"), dict)
        or not isinstance(decoded.get("usage"), dict)
    ):
        raise TypeSafeResponseError("TYPESAFE_RESPONSE_INVALID")
    try:
        _encoded_json(decoded)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise TypeSafeResponseError("TYPESAFE_RESPONSE_INVALID") from error
    return decoded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    preflight = subparsers.add_parser("preflight", help="check local TypeSafe configuration without network access")
    preflight.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH)
    preflight.add_argument("--json", action="store_true")
    evaluate = subparsers.add_parser("evaluate", help="send one explicit typed evaluation to TypeSafe")
    evaluate.add_argument("--input", type=Path, required=True, help="JSON file with state, questions, and optional model")
    evaluate.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH)
    evaluate.add_argument("--timeout", default="30")
    evaluate.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        key = _env_api_key(args.env_file)
    except TypeSafeConfigError as error:
        report = {"status": "BLOCKED", "reason": str(error)}
        print(json.dumps(report, ensure_ascii=False) if args.json else f"BLOCKED: {error}")
        return 2
    if key is None:
        report = {"status": "BLOCKED", "reason": "TYPESAFE_API_KEY_MISSING"}
        print(json.dumps(report, ensure_ascii=False) if args.json else "BLOCKED: TYPESAFE_API_KEY_MISSING")
        return 2
    if not _valid_api_key(key):
        report = {"status": "BLOCKED", "reason": "TYPESAFE_API_KEY_INVALID"}
        print(json.dumps(report, ensure_ascii=False) if args.json else "BLOCKED: TYPESAFE_API_KEY_INVALID")
        return 2

    if args.command == "evaluate":
        try:
            timeout = float(args.timeout)
        except (TypeError, ValueError):
            timeout = math.nan
        if not math.isfinite(timeout) or not 0 < timeout <= MAX_TIMEOUT_SECONDS:
            report = {"status": "BLOCKED", "reason": "TYPESAFE_TIMEOUT_INVALID"}
            print(json.dumps(report, ensure_ascii=False) if args.json else "BLOCKED: TYPESAFE_TIMEOUT_INVALID")
            return 2
        try:
            result = _evaluate(DEFAULT_ENDPOINT, key, _request_payload(args.input), timeout)
        except urllib.error.HTTPError as error:
            try:
                report = {
                    "status": "ERROR",
                    "reason": HTTP_REASONS.get(error.code, "TYPESAFE_HTTP_ERROR"),
                    "http_status": error.code,
                }
            finally:
                error.close()
            print(json.dumps(report, ensure_ascii=False) if args.json else f"ERROR: {report['reason']}")
            return 3
        except TimeoutError:
            report = {"status": "ERROR", "reason": "TYPESAFE_TIMEOUT"}
            print(json.dumps(report, ensure_ascii=False) if args.json else "ERROR: TYPESAFE_TIMEOUT")
            return 3
        except urllib.error.URLError as error:
            reason = "TYPESAFE_TIMEOUT" if isinstance(error.reason, TimeoutError) else "TYPESAFE_TRANSPORT_ERROR"
            report = {"status": "ERROR", "reason": reason}
            print(json.dumps(report, ensure_ascii=False) if args.json else f"ERROR: {reason}")
            return 3
        except OSError:
            report = {"status": "ERROR", "reason": "TYPESAFE_TRANSPORT_ERROR"}
            print(json.dumps(report, ensure_ascii=False) if args.json else "ERROR: TYPESAFE_TRANSPORT_ERROR")
            return 3
        except http.client.HTTPException:
            report = {"status": "ERROR", "reason": "TYPESAFE_TRANSPORT_ERROR"}
            print(json.dumps(report, ensure_ascii=False) if args.json else "ERROR: TYPESAFE_TRANSPORT_ERROR")
            return 3
        except TypeSafeResponseError as error:
            report = {"status": "ERROR", "reason": str(error)}
            print(json.dumps(report, ensure_ascii=False) if args.json else f"ERROR: {error}")
            return 3
        except ValueError as error:
            report = {"status": "BLOCKED", "reason": str(error)}
            print(json.dumps(report, ensure_ascii=False) if args.json else f"BLOCKED: {error}")
            return 2
        report = {"status": "OK", "result": result}
        print(json.dumps(report, ensure_ascii=False) if args.json else "OK")
        return 0

    report = {"status": "READY", "source": "environment" if os.environ.get(API_KEY_NAME) else "env_file"}
    print(json.dumps(report, ensure_ascii=False) if args.json else "READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
