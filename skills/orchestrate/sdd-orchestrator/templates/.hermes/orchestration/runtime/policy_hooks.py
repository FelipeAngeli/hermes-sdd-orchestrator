"""HookDispatcher: the seven proposed policy hooks mapped onto events that already exist.

| Proposed hook          | Existing event it rides on                                      |
|------------------------|-----------------------------------------------------------------|
| before_tool_execution  | Hermes ``pre_tool_call`` (shell hook, ``fail_closed: true``)    |
| before_subagent_spawn  | Hermes ``pre_tool_call`` for ``delegate_task``                  |
| before_agent_start     | caller of ``executor_launch`` (``LoopEngine`` policy hook)      |
| before_loop_retry      | ``LoopEngine._after_failure`` RETRY branch (opt-in policy)      |
| before_gate_decision   | caller of ``GateEngine.evaluate`` (mode check)                  |
| after_task_completion  | ``LoopEngine`` node completion / Hermes ``subagent_stop``        |
| on_policy_violation    | emitted by the dispatcher itself for every DENY                 |

``dispatch(hook, action)`` evaluates, appends the verdict to the SecurityAudit, adds
an ``on_policy_violation`` record for every DENY, and for ``REQUIRE_APPROVAL``
consumes a matching single-use grant from the ApprovalBridge when one exists (the
only path from REQUIRE_APPROVAL to ALLOW). If the audit cannot be written the result
is DENY: a decision that cannot be recorded is not made.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

import policy_approval
import policy_audit
import policy_evaluator
import policy_registry

HOOKS = {
    "before_agent_start": "agent_start",
    "before_subagent_spawn": "subagent_spawn",
    "before_tool_execution": "tool_execution",
    "before_loop_retry": "loop_retry",
    "before_gate_decision": "gate_decision",
    "after_task_completion": "task_completion",
}
VIOLATION = "on_policy_violation"
STATE_DIR_NAME = "policy-state"
#: Tools that run arbitrary code: nothing static can bound them, so each call needs a human approval.
CODE_TOOLS = {"execute_code"}
#: Tools whose arguments are paths, and the argument names that hold them.
PATH_ARGS = {"write_file": ("path", "file_path"), "patch": ("path", "file_path"), "read_file": ("path", "file_path"),
             "search_files": ("path",)}
# Same markers as Hermes tools/patch_parser.py (_OP_MARKERS), so every file the patch touches is checked.
_V4A_PATH = re.compile(r"^\*\*\*\s*(?:Update|Add|Delete)\s+File:\s*(.+)$|^\*\*\*\s*Move\s+File:\s*(.+?)\s*->\s*(.+)$", re.MULTILINE)


class PolicyDispatcher:
    def __init__(self, policy: dict[str, Any], state_dir: str | Path, workspace: str | Path,
                 extra_roots: tuple[str | Path, ...] = (), clock: Any = None) -> None:
        self.policy, self.workspace, self.extra_roots = policy, Path(workspace), tuple(extra_roots)
        self.audit = policy_audit.SecurityAudit(state_dir)
        self.clock = clock or time.time
        kwargs = {"clock": clock} if clock else {}
        self.approvals = policy_approval.ApprovalBridge(state_dir, ttl_seconds=policy["approvals"]["ttl_seconds"], **kwargs)

    def recent_denials(self, action_sha: str) -> int:
        """DENY / REQUIRE_APPROVAL records for this exact action inside the repetition window (from the audit)."""
        window = self.policy["repetition"]["window_seconds"]
        records = self.audit.records()
        now = self.clock()
        return sum(1 for record in records if record.get("action_sha256") == action_sha
                   and record.get("decision") in (policy_evaluator.DENY, policy_evaluator.REQUIRE_APPROVAL)
                   and isinstance(record.get("at"), (int, float)) and now - record["at"] <= window)

    def dispatch(self, hook: str, action: Any) -> dict[str, Any]:
        """The verdict for one hook call; any internal failure (unreadable audit, bad state) is a DENY."""
        try:
            return self._dispatch(hook, action)
        except (OSError, ValueError, KeyError, TypeError) as error:
            verdict = policy_evaluator.evaluate(self.policy, {"kind": "malformed"})
            return {**verdict, "reasons": ["policy_state_unavailable"], "audit_error": error.__class__.__name__}

    def _dispatch(self, hook: str, action: Any) -> dict[str, Any]:
        at = self.clock()
        kind = HOOKS.get(hook)
        if kind is None or not isinstance(action, dict) or action.get("kind", kind) != kind:
            verdict = policy_evaluator.evaluate(self.policy, {"kind": "malformed"})
        else:
            action = {**action, "kind": kind}
            sha = policy_approval.action_sha256({key: value for key, value in action.items() if key != "recent_denials"})
            if kind == "tool_execution":
                action["recent_denials"] = self.recent_denials(sha)
            verdict = policy_evaluator.evaluate(self.policy, action, self.workspace, self.extra_roots)
        bare = {key: value for key, value in (action if isinstance(action, dict) else {}).items() if key != "recent_denials"}
        sha = policy_approval.action_sha256(bare) if isinstance(action, dict) else None
        if verdict["decision"] == policy_evaluator.REQUIRE_APPROVAL:
            try:
                grant = self.approvals.consume(bare)
                verdict = {**verdict, "decision": policy_evaluator.ALLOW, "approval": grant["request_id"], "approved_by": grant["by"]}
            except policy_approval.ApprovalError as error:
                try:
                    identifier = self.approvals.request(bare)
                except (OSError, policy_approval.ApprovalError):
                    identifier = policy_approval.request_id(bare)
                verdict = {**verdict, "approval_status": error.code, "request_id": identifier}
        command = bare.get("command") if isinstance(bare.get("command"), str) else None
        fields = {"hook": hook, "kind": kind, "decision": verdict["decision"], "rules": verdict["rules"],
                  "reasons": verdict["reasons"], "action_sha256": sha, "at": at, "policy_version": verdict["policy_version"],
                  "policy_sha256": verdict["policy_sha256"], "agent_id": bare.get("agent_id") or bare.get("child_agent_id"),
                  "tool": bare.get("tool"), "command_sha256": policy_evaluator.command_sha256(command) if command else None,
                  "approval": verdict.get("approval")}
        try:
            self.audit.append("POLICY_DECISION", **fields)
            if verdict["decision"] == policy_evaluator.DENY:
                self.audit.append(VIOLATION.upper(), **{key: fields[key] for key in ("hook", "rules", "reasons", "action_sha256", "at")})
        except (OSError, ValueError) as error:
            return {**verdict, "decision": policy_evaluator.DENY, "reasons": [*verdict["reasons"], "audit_unavailable"],
                    "audit_error": error.__class__.__name__}
        return verdict


def tool_action(tool_name: Any, tool_input: Any) -> tuple[str, dict[str, Any]]:
    """Translate a Hermes ``pre_tool_call`` payload into a hook name and action (unknown shapes stay malformed)."""
    if not isinstance(tool_name, str) or not isinstance(tool_input, dict):
        return "before_tool_execution", {"kind": "tool_execution", "tool": None}
    if tool_name == "delegate_task":
        # Hermes children inherit the parent's toolsets (no model-facing toolsets argument; acp_command is stripped)
        # and Hermes enforces delegation.max_spawn_depth, so the hook checks the batch size: one call may not
        # spawn more than max_children_per_parent children. Control actions (list/steer/stop) spawn nothing.
        if tool_input.get("action") in ("list", "steer", "stop"):
            return "before_subagent_spawn", {"kind": "subagent_spawn", "parent_tools": [], "child_tools": [], "depth": None,
                                             "children": 0}
        tasks = tool_input.get("tasks")
        count = len(tasks) if isinstance(tasks, list) else (1 if tool_input.get("goal") else 0)
        return "before_subagent_spawn", {"kind": "subagent_spawn", "parent_tools": [], "child_tools": [], "depth": None,
                                         "children": max(0, count - 1)}
    if tool_name in ("terminal",) or tool_name in CODE_TOOLS:
        field = "command" if tool_name == "terminal" else "code"
        action = {"kind": "tool_execution", "tool": tool_name, "command": tool_input.get(field)}
        if tool_input.get("workdir") is not None:
            action["workdir"] = tool_input["workdir"]  # relative paths resolve there, so the check must too
        return "before_tool_execution", action
    paths: list[str] = []
    if tool_name == "patch" and tool_input.get("mode") == "patch" and isinstance(tool_input.get("patch"), str):
        for match in _V4A_PATH.finditer(tool_input["patch"]):
            paths.extend(group.strip() for group in match.groups() if group)
    for key in PATH_ARGS.get(tool_name, ()):
        if isinstance(tool_input.get(key), str):
            paths.append(tool_input[key])
    return "before_tool_execution", {"kind": "tool_execution", "tool": tool_name, "paths": paths}


def state_dir_for(controller_root: Path) -> Path:
    return controller_root / STATE_DIR_NAME


def load_dispatcher(controller_root: Path, workspace: Path, extra_roots: tuple[Path, ...] = ()) -> PolicyDispatcher:
    policy = policy_registry.load(controller_root / "policies" / "SECURITY_POLICY.json")
    return PolicyDispatcher(policy, state_dir_for(controller_root), workspace, extra_roots)


def workspace_from(payload: dict[str, Any]) -> Path:
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        raise ValueError("hook payload has no cwd")
    return Path(os.path.realpath(os.path.expanduser(cwd)))
