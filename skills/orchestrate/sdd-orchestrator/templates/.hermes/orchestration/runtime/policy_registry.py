"""PolicyRegistry: load the versioned security policy and refuse any file that loosens a fixed floor.

The policy file can only TIGHTEN the floors below. Floors live in code, not in the
file, so an agent (or a careless edit) that rewrites ``SECURITY_POLICY.json`` can
make the engine stricter but never weaker: a weakening file raises
``POLICY_WEAKENS_FLOOR`` and every caller fails closed. A loaded policy carries its
``version`` and the SHA-256 of the exact bytes read, so each decision names the
policy that produced it.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = ROOT / "policies" / "SECURITY_POLICY.json"
SCHEMA_PATH = ROOT / "schemas" / "SECURITY_POLICY_SCHEMA.json"
MAX_POLICY_BYTES = 64 * 1024

#: Hard floors: a policy may lower a maximum or add a protected path, never the reverse.
FLOORS = {
    "delegation.max_depth": 2,
    "delegation.max_children_per_parent": 3,
    "delegation.max_concurrent_agents": 3,
    "approvals.ttl_seconds": 900,
    "repetition.max_denied_repeats": 5,
    "budget_raise.max_factor": 4,
    "budgets.max_attempts_per_node": 10,
}
#: The repetition window may grow (stricter) but never shrink below this.
MIN_REPETITION_WINDOW_SECONDS = 300
#: Paths every policy protects; a file may add more, never drop one of these.
FLOOR_PROTECTED_PATHS = (
    ".hermes/orchestration/policies/**",
    ".hermes/orchestration/runtime/**",
    ".hermes/orchestration/hooks/**",
    ".hermes/orchestration/schemas/**",
    "Hermes/orchestration/policies/**",
    "Hermes/orchestration/runtime/**",
    "Hermes/orchestration/hooks/**",
    "Hermes/orchestration/schemas/**",
    ".hermes/orchestration/policy-state/**",
    "Hermes/orchestration/policy-state/**",
)
#: Limits a budget raise may ever touch, whatever the file says.
RAISABLE_LIMITS = ("max_tokens", "max_node_runs", "max_wall_clock_seconds", "max_correction_cycles")


class PolicyError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def _get(value: dict[str, Any], dotted: str) -> Any:
    for part in dotted.split("."):
        value = value[part]
    return value


def _read(path: Path) -> bytes:
    """Bytes of a regular, owner-only-writable policy file, read through one descriptor."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise PolicyError("POLICY_UNREADABLE", f"{path.name}: {error.strerror or error}") from error
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise PolicyError("POLICY_UNREADABLE", f"{path.name} is not a regular file")
        if info.st_mode & 0o022:
            raise PolicyError("POLICY_FILE_UNSAFE", f"{path.name} is writable by group or others")
        data = handle.read(MAX_POLICY_BYTES + 1)
    if len(data) > MAX_POLICY_BYTES:
        raise PolicyError("POLICY_UNREADABLE", f"{path.name} is larger than {MAX_POLICY_BYTES} bytes")
    return data


def check_floors(policy: dict[str, Any]) -> list[str]:
    """Names of every floor the policy loosens (empty when it only tightens)."""
    weakened = [name for name, ceiling in FLOORS.items() if _get(policy, name) > ceiling]
    if policy["repetition"]["window_seconds"] < MIN_REPETITION_WINDOW_SECONDS:
        weakened.append("repetition.window_seconds")
    missing = [path for path in FLOOR_PROTECTED_PATHS if path not in policy["protected_paths"]]
    if missing:
        weakened.append("protected_paths")
    if any(limit not in RAISABLE_LIMITS for limit in policy["budget_raise"]["allowed_limits"]):
        weakened.append("budget_raise.allowed_limits")
    return weakened


def validate(policy: Any) -> dict[str, Any]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(policy), key=lambda item: list(item.path))
    if errors:
        where = ".".join(str(part) for part in errors[0].path) or "$"
        raise PolicyError("POLICY_SCHEMA_INVALID", f"{where}: {errors[0].message}")
    weakened = check_floors(policy)
    if weakened:
        raise PolicyError("POLICY_WEAKENS_FLOOR", ", ".join(weakened))
    for pattern in policy["extra_deny_patterns"]:
        if _backtracks(pattern) or _BACKREFERENCE.search(pattern):
            raise PolicyError("POLICY_SCHEMA_INVALID", f"extra_deny_patterns: may backtrack catastrophically: {pattern!r}")
        try:
            re.compile(pattern)
        except re.error as error:
            raise PolicyError("POLICY_SCHEMA_INVALID", f"extra_deny_patterns: {error}") from error
    return policy


_BACKREFERENCE = re.compile(r"\\[1-9]|\(\?P=")


def _backtracks(pattern: str) -> bool:
    """True when a group holding a quantifier or an alternation is itself quantified, at any depth.

    ``(a+)+``, ``((a)+)+`` and ``(a|aa)*`` can take exponential time; a deny pattern runs
    inside the pre-execution hook, so such a pattern is refused instead of compiled.
    """
    stack: list[bool] = []  # per open group: does it contain a quantifier or "|"?
    index, escaped, in_class = 0, False, False
    while index < len(pattern):
        char = pattern[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif in_class:
            in_class = char != "]"
        elif char == "[":
            in_class = True
        elif char == "(":
            stack.append(False)
        elif char == ")" and stack:
            risky = stack.pop()
            following = pattern[index + 1:index + 2]
            if risky and following in ("+", "*", "{", "?") and not (following == "?" and pattern[index + 2:index + 3] != "+"):
                return True
            if stack:
                stack[-1] = stack[-1] or risky
        elif char in "+*{|" and stack:
            stack[-1] = True
        index += 1
    return False


def load(path: str | Path = DEFAULT_POLICY_PATH) -> dict[str, Any]:
    """The validated policy plus ``sha256`` of its bytes; raises ``PolicyError`` (callers fail closed)."""
    data = _read(Path(path))
    try:
        policy = json.loads(data.decode("utf-8"))
    except (UnicodeError, ValueError) as error:
        raise PolicyError("POLICY_SCHEMA_INVALID", f"not JSON: {error}") from error
    policy = copy.deepcopy(validate(policy))
    policy["sha256"] = hashlib.sha256(data).hexdigest()
    return policy


def identity(policy: dict[str, Any]) -> dict[str, str]:
    return {"policy_id": policy["policy_id"], "policy_version": policy["version"], "policy_sha256": policy["sha256"]}
