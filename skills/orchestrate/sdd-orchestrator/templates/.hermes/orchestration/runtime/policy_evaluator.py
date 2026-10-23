"""PolicyEvaluator: deterministic ALLOW / DENY / REQUIRE_APPROVAL for one runtime action.

``evaluate(policy, action, workspace)`` reads only the action's allowlisted fields
for its kind; an unknown kind, an unknown field or a wrong type is ``DENY`` with
``action_malformed`` (a malformed hook or payload never reaches ALLOW). The verdict
names every rule that fired (``P1``…``P12``) and the policy identity, so the audit
can show why.

Two inputs are never consulted, by construction:

* agent instructions (prompt text, "the user said I may", tool descriptions), so a
  security rule always wins over what the agent asks for (P11);
* Jev output (``jev``/``semantic``/``recommendation`` fields), so no semantic
  classification can turn a DENY into ALLOW or skip an approval (P12).

Both are rejected as unknown fields rather than ignored, so a caller cannot even
pass them.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

import policy_budget
import policy_commands
import policy_registry

ALLOW, DENY, REQUIRE_APPROVAL = "ALLOW", "DENY", "REQUIRE_APPROVAL"
WRITE_TOOLS = {"write_file", "patch"}
CODE_TOOLS = {"execute_code"}
READ_TOOLS = {"read_file", "search_files"}
#: Fields each action kind may carry; anything else is a malformed action.
FIELDS = {
    "agent_start": {"kind", "agent_id", "stage", "tools", "allowed_tools", "sandbox", "permission_mode", "workspace", "attempts"},
    "subagent_spawn": {"kind", "parent_agent_id", "parent_tools", "child_agent_id", "child_tools", "depth", "children", "concurrent_agents", "workspace"},
    "tool_execution": {"kind", "agent_id", "tool", "command", "paths", "workdir", "workspace", "recent_denials"},
    "loop_retry": {"kind", "node", "attempts", "limits", "usage"},
    "budget_raise": {"kind", "limit", "current", "requested", "by", "quote"},
    "gate_decision": {"kind", "stage", "mode", "verdict"},
    "task_completion": {"kind", "task_id", "verdict", "evidence_refs"},
    "policy_change": {"kind", "path", "by"},
}
#: Commands that would let an agent approve itself, edit the policy state or rewrite the audit (P3, P4).
_PERMISSION_FLAGS = re.compile(r"--(yolo|dangerously-skip-permissions|dangerously-bypass[a-z-]*|full-auto)\b|bypassPermissions|approve[-_]all", re.I)


def _verdict(policy: dict[str, Any], decision: str, rules: list[str], reasons: list[str]) -> dict[str, Any]:
    return {"decision": decision, "rules": sorted(set(rules)), "reasons": sorted(set(reasons)), **policy_registry.identity(policy)}


def _strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _inside(path: str, workspace: Path, extra_roots: tuple[Path, ...] = ()) -> bool:
    resolved = Path(os.path.realpath(os.path.join(workspace, os.path.expanduser(path))))
    return any(resolved == root or root in resolved.parents for root in (workspace, *extra_roots))


def _base(action: dict[str, Any], workspace: Path) -> Path:
    """Directory relative paths resolve against: the tool's ``workdir`` when given, else the workspace."""
    workdir = action.get("workdir")
    return Path(os.path.realpath(os.path.join(workspace, os.path.expanduser(workdir)))) if workdir else workspace


def _protected(path: str, workspace: Path, policy: dict[str, Any]) -> bool:
    return policy_commands.matches_protected(path, workspace, policy["protected_paths"])


def command_sha256(command: str) -> str:
    return hashlib.sha256(command.encode("utf-8", "surrogatepass")).hexdigest()


def _malformed(action: Any) -> str | None:
    if not isinstance(action, dict) or action.get("kind") not in FIELDS:
        return "unknown action kind"
    extra = set(action) - FIELDS[action["kind"]]
    return f"unknown fields: {', '.join(sorted(extra))}" if extra else None


def _tool_execution(policy: dict[str, Any], action: dict[str, Any], workspace: Path,
                    extra_roots: tuple[Path, ...]) -> tuple[list[str], list[str], list[str]]:
    rules: list[str] = []
    deny: list[str] = []
    approval: list[str] = []
    tool, command, paths = action.get("tool"), action.get("command"), action.get("paths", [])
    workdir = action.get("workdir")
    if (not isinstance(tool, str) or not _strings(paths) or (command is not None and not isinstance(command, str))
            or (workdir is not None and (not isinstance(workdir, str) or not workdir))):
        return ["P10"], ["action_malformed"], []
    base = _base(action, workspace)
    if workdir is not None and not _inside(str(base), workspace, extra_roots):
        rules.append("P10")
        approval.append("workdir_outside_workspace")
    if tool in CODE_TOOLS:
        rules.append("P10")
        approval.append("unverifiable")
    elif tool == "terminal":
        if not isinstance(command, str):
            return ["P10"], ["action_malformed"], []
        found = policy_commands.classify(command, workspace, policy["protected_paths"], policy["extra_deny_patterns"],
                                         extra_roots, cwd=base)
        if _PERMISSION_FLAGS.search(command):
            found["deny"].append("permission_bypass_flag")
        deny += found["deny"]
        approval += found["approval"]
        rules += ["P2"] if set(found["deny"]) & {"destructive", "privileged", "permission_widening", "network_exfil", "policy_pattern", "empty"} else []
        rules += ["P8"] if "credentials" in found["deny"] else []
        rules += ["P4"] if {"protected_path", "self_approval"} & set(found["deny"]) else []
        rules += ["P3"] if "self_approval" in found["deny"] else []
        rules += ["P1"] if "permission_bypass_flag" in found["deny"] else []
        rules += ["P10"] if found["approval"] else []
    for path in paths:
        if not _inside(os.path.join(base, os.path.expanduser(path)), workspace, extra_roots):
            rules.append("P9" if tool in WRITE_TOOLS else "P10")
            (deny if tool in WRITE_TOOLS else approval).append("path_outside_workspace")
        if tool in WRITE_TOOLS and _protected(os.path.join(base, os.path.expanduser(path)), workspace, policy):
            rules.append("P4")
            deny.append("protected_path")
    repeats = action.get("recent_denials")
    if repeats is not None and (not _int(repeats) or repeats >= policy["repetition"]["max_denied_repeats"]) and (deny or approval):
        rules.append("P7")
        deny.append("dangerous_repetition")
    return rules, deny, approval


def _subagent_spawn(policy: dict[str, Any], action: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    parent, child = action.get("parent_tools"), action.get("child_tools")
    depth, children, concurrent = action.get("depth"), action.get("children"), action.get("concurrent_agents")
    # depth is None when the host enforces it (Hermes delegate_task: delegation.max_spawn_depth).
    if not _strings(parent) or not _strings(child) or (depth is not None and not _int(depth)) or not _int(children):
        return ["P1"], ["action_malformed"], []
    rules, deny = [], []
    if set(child or []) - set(parent or []):
        rules.append("P1")
        deny.append("permission_widening")
    limits = policy["delegation"]
    if (depth is not None and depth > limits["max_depth"]) or children >= limits["max_children_per_parent"]:
        rules.append("P6")
        deny.append("delegation_limit")
    if concurrent is not None and policy_budget.check(policy, concurrent_agents=concurrent):
        rules.append("P5")
        deny.append("budget_concurrency_exhausted")
    return rules, deny, []


def _agent_start(policy: dict[str, Any], action: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    tools, allowed = action.get("tools", []), action.get("allowed_tools")
    mode, sandbox = action.get("permission_mode"), action.get("sandbox")
    if not _strings(tools) or (allowed is not None and not _strings(allowed)):
        return ["P1"], ["action_malformed"], []
    rules, deny = [], []
    if allowed is not None and set(tools) - set(allowed):
        rules.append("P1")
        deny.append("permission_widening")
    if (isinstance(mode, str) and _PERMISSION_FLAGS.search(mode)) or sandbox == "danger-full-access":
        rules.append("P1")
        deny.append("permission_bypass_flag")
    attempts = action.get("attempts")
    if attempts is not None and policy_budget.check(policy, attempts=attempts):
        rules.append("P5")
        deny.append("budget_attempts_exhausted")
    return rules, deny, []


def evaluate(policy: dict[str, Any], action: Any, workspace: str | Path | None = None,
             extra_roots: tuple[str | Path, ...] = ()) -> dict[str, Any]:
    """The verdict for one action; never raises on bad input (that is DENY).

    ``workspace`` is the authorised root (the repository); ``extra_roots`` are other
    roots the caller authorises, such as the bound Obsidian project container.
    """
    problem = _malformed(action)
    if problem:
        return _verdict(policy, DENY, ["P10"], ["action_malformed"])
    kind = action["kind"]
    root = Path(os.path.realpath(os.path.expanduser(str(workspace or action.get("workspace") or "."))))
    roots = tuple(Path(os.path.realpath(os.path.expanduser(str(item)))) for item in extra_roots)
    if kind == "tool_execution":
        rules, deny, approval = _tool_execution(policy, action, root, roots)
    elif kind == "subagent_spawn":
        rules, deny, approval = _subagent_spawn(policy, action)
    elif kind == "agent_start":
        rules, deny, approval = _agent_start(policy, action)
    elif kind == "loop_retry":
        reasons = policy_budget.check(policy, limits=action.get("limits") or {}, usage=action.get("usage") or {},
                                      attempts=action.get("attempts"))
        rules, deny, approval = (["P5"] if reasons else []), reasons, []
    elif kind == "budget_raise":
        reasons = policy_budget.raise_budget(policy, limit=action.get("limit"), current=action.get("current"),
                                             requested=action.get("requested"), by=action.get("by"), quote=action.get("quote"))
        rules, deny, approval = (["P5"] if reasons else []), reasons, []
    elif kind == "gate_decision":
        weaker = action.get("mode") not in ("OBSERVE", "ENFORCE") or (
            policy["gates"]["min_mode"] == "ENFORCE" and action.get("mode") != "ENFORCE")
        rules, deny, approval = (["P11"] if weaker else []), (["gate_mode_below_policy"] if weaker else []), []
    elif kind == "task_completion":
        evidence = action.get("evidence_refs")
        missing = action.get("verdict") != "PASS" or not _strings(evidence) or not evidence
        rules, deny, approval = (["P11"] if missing else []), (["completion_without_evidence"] if missing else []), []
    else:  # policy_change: no agent may change the policy; only the owner edits the file outside a session
        rules, deny, approval = ["P4"], ["policy_change_by_agent"], []
    if deny:
        return _verdict(policy, DENY, rules, deny)
    if approval:
        return _verdict(policy, REQUIRE_APPROVAL, rules, approval)
    return _verdict(policy, ALLOW, rules, [])
