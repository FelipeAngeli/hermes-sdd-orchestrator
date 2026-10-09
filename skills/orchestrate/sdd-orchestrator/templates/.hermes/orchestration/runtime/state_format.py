"""Read the three STATE.md dialects and normalise them to one.

Why this exists: the STATE payload inside the ```yaml fence is written three
different ways across this repository's worktrees (JSON, indented YAML, and
YAML with inline collections). Migration has to read all of them, and the
chosen target format is JSON.

Why a hand-written parser: no Python interpreter on this machine ships PyYAML,
and bootstrap-class tooling must not acquire a new runtime dependency. The
parser therefore covers ONLY the subset these files actually use, and every
normalisation is verified by re-parsing its own output before it is returned.
A file that cannot be round-tripped raises instead of being rewritten — losing
orchestration state silently is far worse than refusing to convert it.

Supported subset: nested block mappings, block sequences (``- item``), inline
mappings ``{a: 1}``, inline sequences ``[a, b]``, inline sequences of mappings,
double/single quoted scalars, integers, booleans, ``null`` and trailing
comments.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

FENCE_RE = re.compile(r"^(?P<before>.*?)```yaml\s*\n(?P<body>.*?)```(?P<after>.*)$", re.S)

#: A state payload must have these to be recognisable as orchestration state.
REQUIRED_KEYS = ("schema_version", "stage")

#: Delivery profiles: the ordered stages a demand passes through. ``DECISION_DOC``
#: delivers a document (ADR, spike report) through IMPLEMENT; TASKS and TEST are
#: recorded as skipped with the profile as reason.
FSM_ORDER = ("IDLE", "SPECIFY", "CLARIFY", "PLAN", "TASKS", "IMPLEMENT", "TEST", "REVIEW", "DONE")
PROFILES = {
    "CODE": ("SPECIFY", "CLARIFY", "PLAN", "TASKS", "IMPLEMENT", "TEST", "REVIEW", "DONE"),
    "DECISION_DOC": ("SPECIFY", "CLARIFY", "PLAN", "IMPLEMENT", "REVIEW", "DONE"),
}
#: Backward transitions allowed with a recorded human reason (a reopened stage).
#: A stage never transitions to itself: a failure inside IMPLEMENT (the DECISION_DOC
#: gate stage) reopens in place with ``sdd.py reopen`` (a FIX slice), not a transition.
REOPEN_TARGETS = {"TEST": ("IMPLEMENT",), "REVIEW": ("IMPLEMENT",)}


class StateFormatError(Exception):
    def __init__(self, code: str, message: str, *, detail: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail

    def __str__(self) -> str:  # pragma: no cover - trivial formatting
        return f"{self.code}: {self.message}" + (f" | {self.detail}" if self.detail else "")


def split_fence(text: str) -> Tuple[str, str, str]:
    match = FENCE_RE.match(text)
    if not match:
        raise StateFormatError(
            "STATE_NO_YAML_FENCE", "STATE.md has no ```yaml payload fence."
        )
    return match.group("before"), match.group("body"), match.group("after")


def detect(text: str) -> str:
    """Classify the payload dialect. Informational only: ``parse`` handles all.

    ``yaml-inline`` means the file uses inline collections with CONTENT; an
    empty ``[]`` or ``{}`` is spelled the same way in every dialect and must not
    reclassify an otherwise plain block file.
    """
    body = split_fence(text)[1]
    if body.lstrip().startswith("{"):
        return "json"
    for line in body.splitlines():
        content = _strip_comment(line)
        match = re.search(r":\s*([\{\[])", content)
        if match and content[match.end() :].strip(" \t")[:1] not in ("", "}", "]"):
            return "yaml-inline"
    return "yaml-block"


def parse(text: str) -> Dict[str, Any]:
    body = split_fence(text)[1]
    stripped = body.lstrip()
    if stripped.startswith("{"):
        try:
            value = json.loads(body)
        except json.JSONDecodeError as exc:
            raise StateFormatError(
                "STATE_JSON_INVALID", "STATE payload is not valid JSON.", detail=str(exc)
            ) from None
        if not isinstance(value, dict):
            raise StateFormatError("STATE_SHAPE_UNRECOGNISED", "STATE payload is not a mapping.")
        return value
    return _parse_block(body)


def normalise(text: str) -> str:
    """Rewrite STATE.md with a JSON payload, preserving all surrounding prose."""
    before, _, after = split_fence(text)
    data = parse(text)

    missing = [key for key in REQUIRED_KEYS if key not in data]
    if missing:
        raise StateFormatError(
            "STATE_SHAPE_UNRECOGNISED",
            "STATE payload lacks required orchestration keys.",
            detail=f"missing: {', '.join(missing)}",
        )

    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    result = f"{before}```yaml\n{payload}\n```{after}"

    # Refuse to emit anything this module cannot read back identically.
    if parse(result) != data:
        raise StateFormatError(
            "STATE_ROUND_TRIP_FAILED",
            "Normalised STATE did not re-parse to the same value; refusing to convert.",
        )
    return result


def dump(text: str, data: Dict[str, Any], *, log: str | None = None) -> str:
    """Return STATE.md with ``data`` as its JSON payload, prose preserved, plus one log line.

    Like ``normalise`` the result is re-parsed and refused unless it reads back
    identically, so a transition can never silently lose state.
    """
    before, _, after = split_fence(text)
    missing = [key for key in REQUIRED_KEYS if key not in data]
    if missing:
        raise StateFormatError("STATE_SHAPE_UNRECOGNISED", "STATE payload lacks required orchestration keys.", detail=", ".join(missing))
    if log:
        line = " ".join(str(log).replace("```", "'''").split())
        after = after.rstrip("\n") + f"\n- {line}\n"
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    result = f"{before}```yaml\n{payload}\n```{after}"
    if parse(result) != data:
        raise StateFormatError("STATE_ROUND_TRIP_FAILED", "Serialised STATE did not re-parse to the same value; refusing to write it.")
    return result


def profile_path(profile: str) -> Tuple[str, ...]:
    if profile not in PROFILES:
        raise StateFormatError("STATE_PROFILE_UNKNOWN", f"Unknown delivery profile {profile!r}.", detail=", ".join(PROFILES))
    return PROFILES[profile]


def next_stage(profile: str, current: str) -> str:
    path = profile_path(profile)
    if current == "IDLE":
        return path[0]
    if current not in path or current == "DONE":
        raise StateFormatError("STATE_TRANSITION_INVALID", f"{current} has no next stage in the {profile} profile.")
    return path[path.index(current) + 1]


def apply_transition(
    data: Dict[str, Any],
    target: str,
    *,
    profile: str,
    provenance: Dict[str, Any] | None = None,
    clarify_skip_reason: str | None = None,
    reopen_reason: str | None = None,
) -> Dict[str, Any]:
    """Return a copy of ``data`` moved to ``target``; refuse any transition the profile forbids.

    Forward: every stage strictly between current and target must be absent from
    the profile (recorded as skipped with the profile as reason) or be CLARIFY
    with an explicit ``clarify_skip_reason``. Backward: only ``REOPEN_TARGETS``
    with a ``reopen_reason``. Same stage: always refused. Provenance of the left
    stage is recorded.
    """
    import copy as _copy

    path = profile_path(profile)
    stage = data.get("stage") or {}
    current = stage.get("current")
    if current not in FSM_ORDER or target not in FSM_ORDER:
        raise StateFormatError("STATE_TRANSITION_INVALID", f"Unknown stage in transition {current!r} -> {target!r}.")
    if target == current:
        raise StateFormatError("STATE_TRANSITION_INVALID", f"{current} -> {current} is not a transition (it would spend stage_transitions without progress); "
                               "reopen the work in place with `sdd.py reopen`.")
    result = _copy.deepcopy(data)
    moved = result["stage"]
    moved.setdefault("completed", [])
    moved.setdefault("skipped", [])
    if FSM_ORDER.index(target) < FSM_ORDER.index(current):
        if target not in REOPEN_TARGETS.get(current, ()) or not (reopen_reason or "").strip():
            raise StateFormatError("STATE_TRANSITION_INVALID", f"{current} -> {target} is not a permitted reopen (needs a recorded reason).")
        reopened = FSM_ORDER[FSM_ORDER.index(target):FSM_ORDER.index(current) + 1]
        moved["completed"] = [item for item in moved["completed"] if item not in reopened]
        moved["current"], moved["status"] = target, "RUNNING"
        result.setdefault("stage_provenance", {})[f"{current}->REOPEN"] = {"reason": reopen_reason}
        return result
    if target not in path:
        raise StateFormatError("STATE_TRANSITION_INVALID", f"{target} is not part of the {profile} profile ({' -> '.join(path)}).")
    if current != "IDLE" and current not in path:
        raise StateFormatError("STATE_TRANSITION_INVALID", f"{current} is not part of the {profile} profile.")
    between = FSM_ORDER[FSM_ORDER.index(current) + 1:FSM_ORDER.index(target)]
    for skipped in between:
        if skipped not in path:
            reason = f"not part of the {profile} delivery profile"
        elif skipped == "CLARIFY" and (clarify_skip_reason or "").strip():
            reason = clarify_skip_reason
        else:
            raise StateFormatError("STATE_TRANSITION_INVALID", f"{current} -> {target} would skip {skipped}; only CLARIFY may be skipped, with a recorded reason.")
        moved["skipped"] = [item for item in moved["skipped"] if (item.get("stage") if isinstance(item, dict) else item) != skipped]
        moved["skipped"].append({"stage": skipped, "reason": reason})
    if current != "IDLE" and current not in moved["completed"]:
        moved["completed"].append(current)
    if current != "IDLE" and provenance is not None:
        result.setdefault("stage_provenance", {})[current] = provenance
    moved["current"] = target
    moved["status"] = "DONE" if target == "DONE" else "RUNNING"
    return result


def skipped_stage_names(data: Dict[str, Any]) -> List[str]:
    """Skipped stages as names; entries may be plain names (legacy) or {stage, reason}."""
    return [item.get("stage") if isinstance(item, dict) else item for item in (data.get("stage") or {}).get("skipped", [])]


# ---------------------------------------------------------------------------
# Block parser
# ---------------------------------------------------------------------------


def _parse_block(body: str) -> Dict[str, Any]:
    lines: List[Tuple[int, str]] = []
    for raw in body.splitlines():
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise StateFormatError(
                "STATE_TAB_INDENT",
                "STATE payload uses tab indentation, which is ambiguous.",
                detail=raw,
            )
        stripped_comment = _strip_comment(raw).rstrip()
        if not stripped_comment.strip():
            continue
        indent = len(stripped_comment) - len(stripped_comment.lstrip())
        lines.append((indent, stripped_comment.strip()))
    if not lines:
        raise StateFormatError("STATE_SHAPE_UNRECOGNISED", "STATE payload is empty.")
    value, index = _parse_mapping(lines, 0, lines[0][0], _raw_lines(body))
    if index != len(lines):
        raise StateFormatError(
            "STATE_SHAPE_UNRECOGNISED",
            "STATE payload has content the parser could not place.",
            detail=lines[index][1],
        )
    return value


def _raw_lines(body: str) -> List[str]:
    return body.splitlines()


def _parse_mapping(
    lines: List[Tuple[int, str]], index: int, indent: int, raw: List[str]
) -> Tuple[Dict[str, Any], int]:
    result: Dict[str, Any] = {}
    while index < len(lines):
        line_indent, content = lines[index]
        if line_indent < indent:
            break
        if line_indent > indent:
            raise StateFormatError(
                "STATE_SHAPE_UNRECOGNISED", "Unexpected indentation.", detail=content
            )
        key, _, rest = _split_key(content)
        rest = rest.strip()
        index += 1
        if rest in (">", "|", ">-", "|-", ">+", "|+"):
            result[key], index = _parse_block_scalar(lines, index, indent, rest)
            continue
        if rest:
            result[key] = _parse_scalar_or_inline(rest)
            continue
        if index < len(lines) and lines[index][0] > indent:
            child_indent = lines[index][0]
            if lines[index][1].startswith("- "):
                result[key], index = _parse_sequence(lines, index, child_indent, raw)
            else:
                result[key], index = _parse_mapping(lines, index, child_indent, raw)
        elif index < len(lines) and lines[index][0] == indent and lines[index][1].startswith("- "):
            # A sequence may sit at the same indentation as its key.
            result[key], index = _parse_sequence(lines, index, indent, raw)
        else:
            result[key] = None
    return result, index


def _parse_block_scalar(
    lines: List[Tuple[int, str]], index: int, indent: int, style: str
) -> Tuple[str, int]:
    """Consume a folded (``>``) or literal (``|``) block scalar.

    Comments were already stripped, which is safe here because these blocks hold
    prose; ``#`` mid-sentence is preserved by ``_strip_comment`` since it only
    treats whitespace-preceded hashes as comments.
    """
    collected: List[str] = []
    while index < len(lines) and lines[index][0] > indent:
        collected.append(lines[index][1])
        index += 1
    joiner = "\n" if style.startswith("|") else " "
    return joiner.join(collected), index


def _parse_sequence(
    lines: List[Tuple[int, str]], index: int, indent: int, raw: List[str]
) -> Tuple[List[Any], int]:
    items: List[Any] = []
    while index < len(lines):
        line_indent, content = lines[index]
        if line_indent != indent or not content.startswith("- "):
            break
        rest = content[2:].strip()
        index += 1
        # A sequence item may itself be a mapping whose remaining keys are
        # indented to the item's content column:
        #     - action_id: "x"
        #       cost:
        #         executor_calls: 1
        if _looks_like_mapping_entry(rest) and index < len(lines) and lines[index][0] > indent:
            child_indent = lines[index][0]
            nested_lines = [(child_indent, rest)] + lines[index:]
            value, consumed = _parse_mapping(nested_lines, 0, child_indent, raw)
            items.append(value)
            index += consumed - 1
            continue
        items.append(_parse_scalar_or_inline(rest))
    return items, index


def _looks_like_mapping_entry(token: str) -> bool:
    if token.startswith(("{", "[")):
        return False
    try:
        _split_key(token)
    except StateFormatError:
        return False
    return True


def _split_key(content: str) -> Tuple[str, str, str]:
    in_single = in_double = False
    for position, char in enumerate(content):
        if char == '"' and not in_single:
            in_double = not in_double
        elif char == "'" and not in_double:
            in_single = not in_single
        elif char == ":" and not in_single and not in_double:
            after = content[position + 1 :]
            if after and not after.startswith((" ", "\t")):
                continue
            return _unquote(content[:position].strip()), ":", after
    raise StateFormatError(
        "STATE_SHAPE_UNRECOGNISED", "Mapping line without a key separator.", detail=content
    )


def _strip_comment(line: str) -> str:
    in_single = in_double = False
    for position, char in enumerate(line):
        if char == '"' and not in_single:
            in_double = not in_double
        elif char == "'" and not in_double:
            in_single = not in_single
        elif char == "#" and not in_single and not in_double:
            if position == 0 or line[position - 1] in " \t":
                return line[:position]
    return line


def _parse_scalar_or_inline(token: str) -> Any:
    token = token.strip()
    if token.startswith("{") or token.startswith("["):
        value, consumed = _parse_flow(token, 0)
        if token[consumed:].strip():
            raise StateFormatError(
                "STATE_SHAPE_UNRECOGNISED", "Trailing content after inline collection.", detail=token
            )
        return value
    return _parse_scalar(token)


def _parse_flow(text: str, index: int) -> Tuple[Any, int]:
    index = _skip_space(text, index)
    if index >= len(text):
        raise StateFormatError("STATE_SHAPE_UNRECOGNISED", "Unterminated inline collection.")
    if text[index] == "{":
        result: Dict[str, Any] = {}
        index += 1
        index = _skip_space(text, index)
        if index < len(text) and text[index] == "}":
            return result, index + 1
        while True:
            key_token, index = _read_flow_token(text, index, stop=":")
            index = _skip_space(text, index)
            if index >= len(text) or text[index] != ":":
                raise StateFormatError(
                    "STATE_SHAPE_UNRECOGNISED", "Inline mapping entry without ':'.", detail=text
                )
            index += 1
            value, index = _read_flow_value(text, index)
            result[_unquote(key_token.strip())] = value
            index = _skip_space(text, index)
            if index < len(text) and text[index] == ",":
                index += 1
                continue
            if index < len(text) and text[index] == "}":
                return result, index + 1
            raise StateFormatError(
                "STATE_SHAPE_UNRECOGNISED", "Malformed inline mapping.", detail=text
            )
    if text[index] == "[":
        items: List[Any] = []
        index += 1
        index = _skip_space(text, index)
        if index < len(text) and text[index] == "]":
            return items, index + 1
        while True:
            value, index = _read_flow_value(text, index)
            items.append(value)
            index = _skip_space(text, index)
            if index < len(text) and text[index] == ",":
                index += 1
                continue
            if index < len(text) and text[index] == "]":
                return items, index + 1
            raise StateFormatError(
                "STATE_SHAPE_UNRECOGNISED", "Malformed inline sequence.", detail=text
            )
    raise StateFormatError("STATE_SHAPE_UNRECOGNISED", "Expected inline collection.", detail=text)


def _read_flow_value(text: str, index: int) -> Tuple[Any, int]:
    index = _skip_space(text, index)
    if index < len(text) and text[index] in "{[":
        return _parse_flow(text, index)
    token, index = _read_flow_token(text, index, stop=",]}")
    return _parse_scalar(token.strip()), index


def _read_flow_token(text: str, index: int, stop: str) -> Tuple[str, int]:
    start = index
    in_single = in_double = False
    while index < len(text):
        char = text[index]
        if char == '"' and not in_single:
            in_double = not in_double
        elif char == "'" and not in_double:
            in_single = not in_single
        elif char in stop and not in_single and not in_double:
            break
        index += 1
    return text[start:index], index


def _skip_space(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t":
        index += 1
    return index


def _parse_scalar(token: str) -> Any:
    token = token.strip()
    if not token:
        return None
    if (token.startswith('"') and token.endswith('"') and len(token) >= 2) or (
        token.startswith("'") and token.endswith("'") and len(token) >= 2
    ):
        return _unquote(token)
    lowered = token.lower()
    if lowered in {"null", "~"}:
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        pass
    return token


def _unquote(token: str) -> str:
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        inner = token[1:-1]
        if token[0] == '"':
            return inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner.replace("''", "'")
    return token
