"""Shell-hook runner and owner CLI for the security policy engine.

``run_policy_hook(payload)`` is what ``hooks/enforce-policy.py`` calls for Hermes
``pre_tool_call``: it maps the tool call onto a policy hook, dispatches it and
answers in Hermes' shell-hook protocol. ALLOW is ``{}``; DENY and an unapproved
REQUIRE_APPROVAL are ``{"action": "block", ...}``. Any error (bad payload, missing
or weakened policy, unwritable audit) blocks: the hook is declared
``fail_closed: true`` and fails closed itself too.

The CLI is for the owner, outside an agent session:

* ``check`` validates the policy file (``POLICY_WEAKENS_FLOOR`` when it loosens a floor);
* ``evaluate --action FILE`` prints the verdict for one action JSON, without auditing;
* ``pending`` lists requests waiting for approval; ``grant --request ID --by NAME
  --quote TEXT`` approves one exactly once; ``revoke --request ID`` cancels it;
* ``audit-verify`` checks the hash chain and prints the head hash;
* ``matrix`` prints the permission matrix (action kind × rule → outcome).

An agent cannot run ``grant``/``revoke`` through the hook-protected ``terminal``:
that command is a ``self_approval`` DENY (P3).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

RUNTIME = Path(__file__).resolve().parent
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

import obsidian_binding  # noqa: E402
import policy_approval  # noqa: E402
import policy_audit  # noqa: E402
import policy_evaluator  # noqa: E402
import policy_hooks  # noqa: E402
import policy_registry  # noqa: E402

CONTROLLER = RUNTIME.parent
MATRIX = [
    ("tool_execution", "terminal: destructive, privileged, permission widening, network exfiltration", "DENY", "P2"),
    ("tool_execution", "terminal: credential stores and environment dumps", "DENY", "P8"),
    ("tool_execution", "terminal: --yolo, --dangerously-*, bypassPermissions, approve-all", "DENY", "P1"),
    ("tool_execution", "terminal: policy_cli grant/revoke, policy-state, security audit", "DENY", "P3, P4"),
    ("tool_execution", "terminal: unverifiable construct ($(), eval, inline interpreter)", "REQUIRE_APPROVAL", "P10"),
    ("tool_execution", "terminal/read: path outside the workspace", "REQUIRE_APPROVAL", "P10"),
    ("tool_execution", "write_file/patch: path outside the workspace", "DENY", "P9"),
    ("tool_execution", "write_file/patch/terminal write: protected controller path", "DENY", "P4"),
    ("tool_execution", "execute_code (arbitrary code)", "REQUIRE_APPROVAL", "P10"),
    ("tool_execution", "same denied action repeated max_denied_repeats times in the window", "DENY", "P7"),
    ("subagent_spawn", "child tools not a subset of the parent's", "DENY", "P1"),
    ("subagent_spawn", "depth above max_depth or batch above max_children_per_parent", "DENY", "P6"),
    ("agent_start", "tools beyond allowed_tools, bypass mode, danger-full-access", "DENY", "P1"),
    ("loop_retry", "attempts, tokens, node runs or time at their limit", "DENY", "P5"),
    ("budget_raise", "limit not allowed, above max_factor, or not a named human with words", "DENY", "P5"),
    ("gate_decision", "mode below gates.min_mode", "DENY", "P11"),
    ("task_completion", "verdict not PASS or no evidence", "DENY", "P11"),
    ("policy_change", "any change requested by an agent", "DENY", "P4"),
    ("any", "unknown kind or field (agent instructions, Jev output)", "DENY", "P10, P11, P12"),
]


def _extra_roots(controller: Path) -> tuple[Path, ...]:
    container = controller.parents[1]
    return (container,) if obsidian_binding.controller_binding_file(container) is not None else ()


def run_policy_hook(payload: Any, controller: Path = CONTROLLER) -> dict[str, Any]:
    try:
        if not isinstance(payload, dict):
            raise ValueError("payload root must be an object")
        workspace = policy_hooks.workspace_from(payload)
        dispatcher = policy_hooks.load_dispatcher(controller, workspace, _extra_roots(controller))
        hook, action = policy_hooks.tool_action(payload.get("tool_name"), payload.get("tool_input"))
        verdict = dispatcher.dispatch(hook, action)
    except Exception as error:  # every failure blocks; the class name never carries payload text
        return {"action": "block", "message": f"Security policy failed closed: {error.__class__.__name__}."}
    if verdict["decision"] == policy_evaluator.ALLOW:
        return {}
    if verdict["decision"] == policy_evaluator.REQUIRE_APPROVAL:
        return {"action": "block", "message": (
            f"Security policy needs a human approval ({', '.join(verdict['reasons'])}; rules {', '.join(verdict['rules'])}). "
            f"Ask the user; only they can run `policy_cli.py grant --request {verdict.get('request_id')}` outside this session.")}
    return {"action": "block", "message": (
        f"Security policy denied this call ({', '.join(verdict['reasons'])}; rules {', '.join(verdict['rules'])}; "
        f"policy {verdict['policy_version']}). Do not retry it in another form.")}


def _state(args: argparse.Namespace) -> Path:
    return Path(args.state_dir) if args.state_dir else policy_hooks.state_dir_for(CONTROLLER)


def _bridge(args: argparse.Namespace) -> policy_approval.ApprovalBridge:
    policy = policy_registry.load(args.policy)
    return policy_approval.ApprovalBridge(_state(args), ttl_seconds=policy["approvals"]["ttl_seconds"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Security policy engine: check, evaluate, approvals, audit.")
    parser.add_argument("--policy", type=Path, default=policy_registry.DEFAULT_POLICY_PATH, help="policy file")
    parser.add_argument("--state-dir", help="approvals and audit directory (default: <controller>/policy-state)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="validate the policy file")
    evaluate = sub.add_parser("evaluate", help="verdict for one action JSON (not audited)")
    evaluate.add_argument("--action", type=Path, required=True)
    evaluate.add_argument("--workspace", default=".")
    sub.add_parser("pending", help="requests waiting for a human approval")
    grant = sub.add_parser("grant", help="approve one pending request once")
    grant.add_argument("--request", required=True)
    grant.add_argument("--by", required=True)
    grant.add_argument("--quote", required=True)
    revoke = sub.add_parser("revoke", help="cancel a granted approval before use")
    revoke.add_argument("--request", required=True)
    sub.add_parser("audit-verify", help="verify the audit hash chain")
    sub.add_parser("matrix", help="print the permission matrix")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            result: Any = {"status": "VALID", **policy_registry.identity(policy_registry.load(args.policy))}
        elif args.command == "evaluate":
            action = json.loads(args.action.read_text(encoding="utf-8"))
            result = policy_evaluator.evaluate(policy_registry.load(args.policy), action, args.workspace)
        elif args.command == "pending":
            directory = _state(args) / policy_approval.GRANTS
            result = {"pending": sorted(path.name[:-len(".request")] for path in directory.glob("ap-*.request"))}
        elif args.command == "grant":
            bridge = _bridge(args)
            grant = bridge.grant(bridge.pending_action(args.request), by=args.by, quote=args.quote)
            result = {"status": "GRANTED", **{key: grant[key] for key in ("request_id", "by", "expires_at")}}
        elif args.command == "revoke":
            result = {"status": "REVOKED" if _bridge(args).revoke(args.request) else "NOT_PENDING", "request_id": args.request}
        elif args.command == "audit-verify":
            result = policy_audit.SecurityAudit(_state(args)).verify()
        else:
            result = {"matrix": [dict(zip(("kind", "condition", "outcome", "rules"), row)) for row in MATRIX]}
    except (policy_registry.PolicyError, policy_approval.ApprovalError) as error:
        print(json.dumps({"status": error.code, "error": str(error)}, sort_keys=True))
        return 2
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "ERROR", "error": error.__class__.__name__}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, indent=1))
    return 1 if args.command == "audit-verify" and not result["valid"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
