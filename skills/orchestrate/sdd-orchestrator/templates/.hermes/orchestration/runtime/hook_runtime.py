#!/usr/bin/env python3
"""Shared protocol and policy adapters for repository-local Hermes shell hooks."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

RUNTIME = Path(__file__).resolve().parent
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

import action_journal
import obsidian_binding
import stage_context
import state_format
import vault_guard
from validate_protocol import path_matches

CONTEXT_RELATIVE = Path(".hermes/orchestration/STAGE_CONTEXT.json")
BINDING_RELATIVE = Path(".hermes/orchestration/HOOK_BINDING.json")
EVIDENCE_RELATIVE = Path(".hermes/orchestration/VERIFICATION_EVIDENCE.json")
STATE_RELATIVE = Path(".hermes/orchestration/STATE.md")
JOURNAL_RELATIVE = Path(".hermes/orchestration/ACTION_JOURNAL.json")
SUBAGENT_HISTORY_RELATIVE = Path(".hermes/orchestration/action-journal-history/subagent-events")
DIRECT_WRITE_TOOLS = {"write_file", "patch"}
HOOK_ENVIRONMENT_KEYS = (
    "SDD_STAGE_CONTEXT",
    "SDD_HOOK_BINDING",
    "SDD_VERIFICATION_EVIDENCE",
    "SDD_STATE",
)
MAX_STATE_FIELD_CHARS = 48
MAX_STATE_SUMMARY_CHARS = 240
_V4A_FILE_RE = re.compile(r"^(\*\*\*\s*(?:Update|Add|Delete)\s+File:\s*)(.+)$", re.MULTILINE)
_V4A_MOVE_RE = re.compile(r"^(\*\*\*\s*Move\s+File:\s*)(.+?)(\s+->\s+)(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class RuntimeLocations:
    state: Path
    journal: Path
    history_workspace: Path
    history_relative: Path


def runtime_locations(root: Path) -> RuntimeLocations:
    """Resolve controller state locally or in the bound per-worktree vault runtime."""
    root = root.resolve()
    try:
        binding = obsidian_binding.load(root)
    except obsidian_binding.BindingError as exc:
        if exc.code != "BINDING_MISSING":
            raise
        return RuntimeLocations(
            state=root / STATE_RELATIVE,
            journal=root / JOURNAL_RELATIVE,
            history_workspace=root,
            history_relative=SUBAGENT_HISTORY_RELATIVE,
        )
    runtime = obsidian_binding.runtime_dir(binding, root)
    return RuntimeLocations(
        state=obsidian_binding.state_path(binding, root),
        journal=obsidian_binding.journal_path(binding, root),
        history_workspace=runtime,
        history_relative=Path("action-journal-history/subagent-events"),
    )


class HookInputError(ValueError):
    """A hook cannot safely interpret its event or controller state."""


def _block(message: str) -> dict[str, str]:
    return {"action": "block", "message": message}


def _continue(message: str) -> dict[str, str]:
    return {"action": "continue", "message": message}


def hook_environment(environ: Mapping[str, str] | None = None) -> Mapping[str, str]:
    """Return explicit hook overrides without exposing unrelated process state."""
    if environ is not None:
        return environ
    return {
        key: value
        for key in HOOK_ENVIRONMENT_KEYS
        if (value := os.getenv(key)) is not None
    }


def _root(payload: Mapping[str, Any]) -> Path:
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        raise HookInputError("hook payload has no cwd")
    root = Path(cwd).expanduser().resolve()
    if not (root / ".hermes" / "orchestration").is_dir():
        raise HookInputError(f"{root} has no installed .hermes/orchestration directory")
    return root


def _configured_path(root: Path, environ: Mapping[str, str], key: str, default: Path) -> Path:
    configured = environ.get(key)
    return Path(configured).expanduser().resolve() if configured else root / default


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HookInputError(f"{label} is unavailable or invalid at {path}: {exc}") from exc


def _validated_context(value: Any) -> dict[str, Any]:
    result = stage_context.check(value)
    if not result["valid"]:
        details = "; ".join(f"{item['code']}: {item['detail']}" for item in result["errors"])
        raise HookInputError(f"STAGE_CONTEXT is invalid: {details}")
    return value


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HookInputError(f"{label} must be an object")
    return value


def _live_head(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True, capture_output=True,
            timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HookInputError(f"cannot resolve live Git HEAD: {exc}") from exc
    if result.returncode or not result.stdout.strip():
        raise HookInputError("cannot resolve live Git HEAD")
    return result.stdout.strip()


def validate_live_binding(root: Path, context: dict[str, Any], binding: Any) -> None:
    """Bind context to live Git, STATE, and the active journal action."""
    root = root.resolve()
    binding = _mapping(binding, "HOOK_BINDING")
    required = {
        "schema_version", "context_sha256", "workspace", "head", "ticket", "stage",
        "current_slice_ids", "action_id", "attempt",
    }
    if set(binding) != required or binding["schema_version"] != 1:
        raise HookInputError("HOOK_BINDING has an unsupported shape or schema")
    live_head = _live_head(root)
    current_slices = sorted((context.get("slice") or {}).get("current_slice_ids", []))
    expected = {
        "context_sha256": _canonical_sha256(context),
        "workspace": str(root),
        "head": live_head,
        "ticket": context.get("ticket"),
        "stage": context.get("stage"),
        "current_slice_ids": current_slices,
    }
    for key, value in expected.items():
        actual = sorted(binding[key]) if key == "current_slice_ids" and isinstance(binding[key], list) else binding[key]
        if actual != value:
            raise HookInputError(f"HOOK_BINDING {key} does not match live context")
    project = _mapping(context.get("project_context"), "STAGE_CONTEXT.project_context")
    if project.get("checked_head") != live_head:
        raise HookInputError("STAGE_CONTEXT checked_head is stale")

    locations = runtime_locations(root)
    journal = action_journal.load_journal(locations.journal)
    action = journal["action"]
    if Path(journal["workspace"]["path"]).resolve() != root:
        raise HookInputError("ACTION_JOURNAL workspace does not match hook cwd")
    if action["status"] in {"IDLE", "RELEASED", "BLOCKED", "INTERRUPTED"}:
        raise HookInputError("ACTION_JOURNAL has no active action")
    for key, value in (
        ("action_id", action["id"]), ("attempt", action["attempt"]),
        ("ticket", action["ticket"]), ("stage", action["stage"]),
    ):
        if binding[key] != value:
            raise HookInputError(f"HOOK_BINDING {key} does not match active action")

    state = _mapping(state_format.parse(locations.state.read_text(encoding="utf-8")), "STATE")
    state_ticket = _mapping(state.get("ticket"), "STATE.ticket").get("id")
    state_stage = _mapping(state.get("stage"), "STATE.stage").get("current")
    loop = _mapping(state.get("loop"), "STATE.loop")
    progress = _mapping(loop.get("progress"), "STATE.loop.progress")
    state_slice = progress.get("current_slice")
    if state_ticket != binding["ticket"] or state_stage != binding["stage"]:
        raise HookInputError("STATE ticket or stage does not match HOOK_BINDING")
    expected_slice = binding["current_slice_ids"][0] if len(binding["current_slice_ids"]) == 1 else None
    if state_slice != expected_slice:
        raise HookInputError("STATE current slice does not match HOOK_BINDING")


def _inside(path: Path, ancestor: Path) -> bool:
    try:
        path.relative_to(ancestor)
        return True
    except ValueError:
        return False


def _tool_input(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = payload.get("tool_input")
    if not isinstance(value, dict):
        raise HookInputError("direct write call has no tool_input object")
    return value


def _v4a_targets(body: Any) -> list[str]:
    if not isinstance(body, str) or not body:
        raise HookInputError("V4A patch has no patch content")
    targets = [match.group(2).strip() for match in _V4A_FILE_RE.finditer(body)]
    for match in _V4A_MOVE_RE.finditer(body):
        targets.extend((match.group(2).strip(), match.group(4).strip()))
    if not targets:
        raise HookInputError("V4A patch has no file paths")
    return targets


def _write_targets(tool_name: str, tool_input: dict[str, Any]) -> list[str]:
    if tool_name == "patch" and tool_input.get("mode", "replace") == "patch":
        return _v4a_targets(tool_input.get("patch"))
    target = tool_input.get("path") or tool_input.get("file_path")
    if not isinstance(target, str) or not target:
        raise HookInputError("direct write call has no path")
    return [target]


def _allowed_target(raw_target: str, root: Path, context: dict[str, Any]) -> tuple[Path | None, dict[str, str] | None]:
    candidate = Path(raw_target).expanduser()
    candidate = candidate if candidate.is_absolute() else root / candidate
    resolved = candidate.resolve(strict=False)
    if _inside(resolved, root):
        relative = resolved.relative_to(root).as_posix()
        editable = (context.get("slice") or {}).get("editable_paths", [])
        if context.get("stage") != "IMPLEMENT" or not any(path_matches(relative, pattern) for pattern in editable):
            return None, _block(f"Write refused: {relative} is outside the current slice editable_paths.")
        return resolved, None
    if context.get("stage") != "IMPLEMENT":
        return None, _block("Vault write refused: only IMPLEMENT may write inside the bound project container.")
    try:
        binding = obsidian_binding.load(root)
        return vault_guard.assert_writable(binding, resolved), None
    except (obsidian_binding.BindingError, vault_guard.VaultWriteRefused) as exc:
        return None, _block(f"Write refused outside the repository and bound project container: {exc}")


def _canonical_v4a(body: str, canonical: dict[str, str]) -> str:
    body = _V4A_FILE_RE.sub(lambda match: match.group(1) + canonical[match.group(2).strip()], body)
    return _V4A_MOVE_RE.sub(
        lambda match: match.group(1) + canonical[match.group(2).strip()] + match.group(3) + canonical[match.group(4).strip()],
        body,
    )


def scope_tool_call(payload: Mapping[str, Any], root: Path, context: dict[str, Any]) -> dict[str, Any]:
    """Allow direct file tools only for canonically resolved slice-scoped targets."""
    root = root.resolve()
    context = _validated_context(context)
    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name:
        return _block("Write-scope hook payload has no tool_name.")
    if tool_name not in DIRECT_WRITE_TOOLS:
        return {}
    tool_input = _tool_input(payload)
    targets = _write_targets(tool_name, tool_input)
    canonical: dict[str, str] = {}
    for target in targets:
        resolved, refusal = _allowed_target(target, root, context)
        if refusal is not None:
            return refusal
        assert resolved is not None
        canonical[target] = str(resolved)
    modified = dict(tool_input)
    if tool_name == "patch" and tool_input.get("mode", "replace") == "patch":
        modified["patch"] = _canonical_v4a(str(tool_input["patch"]), canonical)
    elif "path" in modified:
        modified["path"] = canonical[targets[0]]
    else:
        modified["file_path"] = canonical[targets[0]]
    return {"action": "modify", "args": modified}


def run_scope_hook(payload: Mapping[str, Any], environ: Mapping[str, str] | None = None) -> dict[str, str]:
    environment = hook_environment(environ)
    try:
        root = _root(payload)
        path = _configured_path(root, environment, "SDD_STAGE_CONTEXT", CONTEXT_RELATIVE)
        context = _validated_context(_load_json(path, "STAGE_CONTEXT"))
        binding_path = _configured_path(root, environment, "SDD_HOOK_BINDING", BINDING_RELATIVE)
        validate_live_binding(root, context, _load_json(binding_path, "HOOK_BINDING"))
        return scope_tool_call(payload, root, context)
    except (HookInputError, OSError, action_journal.JournalError, obsidian_binding.BindingError, state_format.StateFormatError):
        return _block("SDD hook state is unavailable or inconsistent.")


def verify_completion(payload: Mapping[str, Any], context: dict[str, Any], evidence: Any) -> dict[str, str]:
    """Keep the coding turn open until every in-scope acceptance check has evidence."""
    extra = payload.get("extra")
    if not isinstance(extra, dict) or not extra.get("coding", False):
        return {}
    context = _validated_context(context)
    checks = evidence.get("checks") if isinstance(evidence, dict) else None
    if not isinstance(checks, dict):
        return _continue("Record VERIFICATION_EVIDENCE.json before finishing this coding turn.")
    acceptance = (context.get("slice") or {}).get("acceptance", {})
    contract_verifiers = (context.get("slice") or {}).get("required_verification", [])
    current = set((context.get("slice") or {}).get("current_slice_ids", []))
    required = {
        check_id for check_id, check in acceptance.items()
        if not current or check.get("slice_id") in current
    }

    def check_passes(check_id: str) -> bool:
        item = checks.get(check_id)
        if not isinstance(item, dict):
            return False
        if item.get("status") != "PASS" or not isinstance(item.get("evidence"), str) or not item["evidence"].strip():
            return False
        observed = {
            verifier.get("command")
            for verifier in item.get("verifiers", [])
            if isinstance(verifier, dict) and verifier.get("exit_code") == 0
        }
        expected = {
            verifier["command"]
            for verifier in contract_verifiers
            if check_id in verifier.get("check_ids", []) and verifier.get("command")
        }
        if acceptance[check_id].get("verifier") == "HUMAN":
            return not expected or expected <= observed
        return bool(expected) and expected <= observed

    missing = sorted(check_id for check_id in required if not check_passes(check_id))
    if missing:
        return _continue("Verification incomplete for acceptance checks: " + ", ".join(missing))
    return {}


def run_verify_hook(payload: Mapping[str, Any], environ: Mapping[str, str] | None = None) -> dict[str, str]:
    environment = hook_environment(environ)
    try:
        root = _root(payload)
        context_path = _configured_path(root, environment, "SDD_STAGE_CONTEXT", CONTEXT_RELATIVE)
        evidence_path = _configured_path(root, environment, "SDD_VERIFICATION_EVIDENCE", EVIDENCE_RELATIVE)
        context = _validated_context(_load_json(context_path, "STAGE_CONTEXT"))
        binding_path = _configured_path(root, environment, "SDD_HOOK_BINDING", BINDING_RELATIVE)
        binding = _load_json(binding_path, "HOOK_BINDING")
        validate_live_binding(root, context, binding)
        try:
            evidence = _load_json(evidence_path, "VERIFICATION_EVIDENCE")
        except HookInputError:
            evidence = {}
        if not isinstance(evidence, dict) or evidence.get("binding") != binding:
            return _continue("Verification evidence is not bound to the live context and action.")
        return verify_completion(payload, context, evidence)
    except (HookInputError, OSError, action_journal.JournalError, obsidian_binding.BindingError, state_format.StateFormatError):
        return _continue("Verification state is unavailable or inconsistent.")


def _bounded_scalar(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise HookInputError(f"{label} must be a string")
    compact = " ".join(value.split())
    return compact[:MAX_STATE_FIELD_CHARS] or "unknown"


def _remaining_budget(budgets: dict[str, Any], key: str) -> str:
    item = _mapping(budgets.get(key), f"STATE.loop.budgets.{key}")
    maximum, used = item.get("max"), item.get("used")
    if type(maximum) is not int or type(used) is not int:
        raise HookInputError(f"STATE.loop.budgets.{key} max/used must be integers")
    return str(maximum - used)


def summarize_state(text: str) -> str:
    """Parse STATE structurally and return bounded facts, never source prose."""
    value = _mapping(state_format.parse(text), "STATE")
    ticket = _bounded_scalar(_mapping(value.get("ticket"), "STATE.ticket").get("id"), "STATE.ticket.id")
    stage = _bounded_scalar(_mapping(value.get("stage"), "STATE.stage").get("current"), "STATE.stage.current")
    loop = _mapping(value.get("loop"), "STATE.loop")
    mode = _bounded_scalar(loop.get("mode"), "STATE.loop.mode")
    current_slice = _bounded_scalar(
        _mapping(loop.get("progress"), "STATE.loop.progress").get("current_slice"),
        "STATE.loop.progress.current_slice",
    )
    budgets = _mapping(loop.get("budgets"), "STATE.loop.budgets")
    executor_remaining = _remaining_budget(budgets, "executor_calls")
    transitions_remaining = _remaining_budget(budgets, "stage_transitions")
    summary = (
        f"SDD state: ticket {ticket}; stage {stage}; slice {current_slice}; mode {mode}; "
        f"executor remaining {executor_remaining}; transitions remaining {transitions_remaining}."
    )
    return summary[:MAX_STATE_SUMMARY_CHARS]


def run_context_hook(payload: Mapping[str, Any], environ: Mapping[str, str] | None = None) -> dict[str, str]:
    environment = hook_environment(environ)
    try:
        root = _root(payload)
        default_state = runtime_locations(root).state
        configured = environment.get("SDD_STATE")
        state_path = Path(configured).expanduser().resolve() if configured else default_state
        return {"context": summarize_state(state_path.read_text(encoding="utf-8"))}
    except (HookInputError, OSError, UnicodeError, obsidian_binding.BindingError, state_format.StateFormatError):
        return {"context": "SDD state unavailable."}


def record_subagent_event(payload: Mapping[str, Any], root: Path) -> Path:
    """Persist immutable non-sensitive leaf-worker evidence in action history."""
    root = root.resolve()
    extra = payload.get("extra")
    if not isinstance(extra, dict):
        raise HookInputError("subagent_stop payload has no extra object")
    child_id = extra.get("child_session_id")
    if not isinstance(child_id, str) or not child_id:
        raise HookInputError("subagent_stop payload has no child_session_id")
    locations = runtime_locations(root)
    journal = action_journal.load_journal(locations.journal)
    if Path(journal["workspace"]["path"]).resolve() != root:
        raise HookInputError("ACTION_JOURNAL workspace does not match hook cwd")
    action = journal["action"]
    if not action["id"] or not action["ticket"] or action["status"] in {"IDLE", "RELEASED", "BLOCKED", "INTERRUPTED"}:
        raise HookInputError("subagent_stop has no active journal action")
    summary = extra.get("child_summary")
    summary_text = summary if isinstance(summary, str) else ""
    event = {
        key: extra.get(key)
        for key in ("parent_turn_id", "child_session_id", "child_role", "child_status", "duration_ms")
    }
    event["parent_session_id"] = payload.get("session_id")
    event.update({
        "action_id": action["id"],
        "attempt": action["attempt"],
        "result_sha256": hashlib.sha256(summary_text.encode("utf-8")).hexdigest(),
    })
    content = (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    event_id = "subagent-" + hashlib.sha256(
        f"{action['id']}\0{action['attempt']}\0{child_id}".encode("utf-8")
    ).hexdigest()[:24]
    path, _ = action_journal.atomic_create_history(
        str(locations.history_workspace), locations.history_relative, str(action["ticket"]), event_id, content
    )
    return path


def run_subagent_hook(payload: Mapping[str, Any]) -> dict[str, str]:
    try:
        record_subagent_event(payload, _root(payload))
        return {}
    except (HookInputError, OSError, action_journal.JournalError, obsidian_binding.BindingError) as exc:
        return {"error": f"subagent event was not recorded: {exc}"}


def shell_main(handler: Callable[[Mapping[str, Any]], dict[str, Any]]) -> int:
    """Run one shell hook using Hermes' JSON stdin/stdout protocol."""
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise HookInputError("payload root must be an object")
        result = handler(payload)
    except (HookInputError, json.JSONDecodeError) as exc:
        result = _block(f"Malformed hook input: {exc}")
    except Exception as exc:
        result = _block(f"Hook failed closed: {exc.__class__.__name__}.")
    print(json.dumps(result, sort_keys=True))
    return 0
