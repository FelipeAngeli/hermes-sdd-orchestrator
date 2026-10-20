"""Pure validation for v1 delegation contracts; persistence arrives in a later PR."""
from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
from typing import Any

import jsonschema

import agent_registry
import delegation_config
import executor_launch

ROOT = Path(__file__).parents[1]


def _error(code: str, field: str, message: str, next_step: str) -> dict[str, str]:
    return {"code": code, "field": field, "message": message, "next_step": next_step}


def _schema_errors(contract: Any) -> list[dict[str, str]]:
    schema = json.loads((ROOT / "schemas" / "DELEGATION_CONTRACT_SCHEMA.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    return [_error("CONTRACT_SCHEMA_INVALID", ".".join(str(p) for p in issue.path) or "$", issue.message, "Pass a schema-v1 contract.") for issue in sorted(validator.iter_errors(contract), key=lambda item: list(item.path))]


def _permissions(contract: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    """Intersect the request with the child's registry entry; never widen either side."""
    requested = contract["allowed_tools"]
    available = child.get("tools")
    tools = [] if available is None else [tool for tool in requested if tool in available]
    ceiling = child.get("timeout_seconds")
    timeout = contract["timeout"] if ceiling is None else min(contract["timeout"], ceiling)
    return {"tools": tools, "timeout_seconds": timeout, "sandbox": child.get("sandbox"), "writes": child.get("writes", False)}


def validate_contract(contract: dict[str, Any], registry: dict[str, Any], config: dict[str, Any], *, prompt_bytes: int | None = None) -> dict[str, Any]:
    """Return structured validation results and never alter the supplied contract."""
    value = copy.deepcopy(contract)
    errors = _schema_errors(value)
    agents = {item["agent_id"]: item for item in registry.get("agents", [])}
    # Semantic checks read typed fields, so they run only on a schema-valid contract.
    well_formed = not errors and isinstance(value, dict)
    child = agents.get(value.get("child_agent_id")) if well_formed else None
    if isinstance(value, dict):
        session = value.get("session_id")
        if not isinstance(session, str) or not session.strip():
            errors.append(_error("SESSION_ID_REQUIRED", "session_id", "A session id is required and is never inferred.", "Pass --session-id <existing Hermes session id>; state.db validation is planned for PR 2."))
    if well_formed:
        if child is None:
            errors.append(_error("CHILD_AGENT_UNKNOWN", "child_agent_id", "Child agent is not in the registry.", "Choose an agent from the registry."))
        parent = value.get("parent_agent_id")
        if parent != "controller" and parent not in agents:
            errors.append(_error("PARENT_AGENT_UNKNOWN", "parent_agent_id", "Parent agent is not in the registry.", "Use controller or a registry agent."))
        if child:
            if value.get("stage") not in child["stages"]:
                errors.append(_error("STAGE_NOT_ALLOWED", "stage", "Stage is not allowed for this child.", "Choose one of the child stages."))
            requested = value.get("allowed_tools", [])
            available = child.get("tools")
            causes: list[str] = []
            # tools null: a Codex child (capability from the sandbox) or Claude "default" (unrestricted); only [] is accepted.
            if (available is None and requested) or (available is not None and any(tool not in available for tool in requested)):
                causes.append("allowed_tools")
            # A writing Claude child given only read tools would report writes:true with no write tool: refuse the contradiction.
            elif child.get("writes") and child.get("executor") == "claude" and all(tool in executor_launch.READ_ONLY_CLAUDE_TOOLS for tool in requested):
                causes.append("allowed_tools")
            timeout = value.get("timeout")
            if child.get("timeout_seconds") is not None and timeout > child["timeout_seconds"]:
                causes.append("timeout")
            budget, default = value.get("token_budget"), config.get("default_token_budget")
            if budget is not None and default is not None and budget > default:
                causes.append("token_budget")
            for field in causes:
                errors.append(_error("PERMISSION_WIDENING", field, f"Requested {field} exceeds or cannot express the child's registry permissions.", "Request only the child registry permissions."))
            output = value.get("output_schema", {})
            if isinstance(output, dict) and Path(str(output.get("path", ""))).name != Path(str(child.get("result_schema", ""))).name:
                errors.append(_error("OUTPUT_SCHEMA_MISMATCH", "output_schema.path", "Output schema does not match the child.", "Use the child result schema basename."))
        deps = value.get("dependencies", [])
        if isinstance(deps, list) and (len(set(deps)) != len(deps) or value.get("task_id") in deps):
            errors.append(_error("DEPENDENCY_INVALID", "dependencies", "Dependencies may not duplicate or include this task.", "Remove duplicate and self dependencies."))
    budget = value.get("token_budget") if well_formed else None
    if budget is None:
        budget = config.get("default_token_budget")
    if prompt_bytes is not None and (not isinstance(prompt_bytes, int) or isinstance(prompt_bytes, bool) or prompt_bytes < 0):
        raise ValueError("prompt_bytes must be a non-negative integer")
    estimate = math.ceil(prompt_bytes / config["bytes_per_token"]) if prompt_bytes is not None else None
    token = {"estimated_tokens": estimate, "method": "BYTES_DIV_N", "bytes_per_token": config["bytes_per_token"], "measured_tokens": None, "usage_status": "UNMEASURED", "covers_provider_internal_context": False, "budget": budget}
    if well_formed and estimate is not None and budget is not None and estimate > budget:
        errors.append(_error("TOKEN_BUDGET_EXCEEDED", "token_budget", "Prompt estimate exceeds budget.", "Request explicit review before dispatch."))
    effective = _permissions(value, child) if child else {"tools": [], "timeout_seconds": 0, "sandbox": None, "writes": False}
    codes = {item["code"] for item in errors}
    status = "VALID" if not errors else "REVIEW_REQUIRED" if codes == {"TOKEN_BUDGET_EXCEEDED"} else "REJECTED"
    return {"status": status, "errors": errors, "token_estimate": token, "effective_permissions": effective}


def _usage_error(status: str, next_step: str, as_json: bool) -> int:
    print(json.dumps({"status": status, "next_step": next_step}) if as_json else status)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--contract", type=Path, required=True)
    validate.add_argument("--prompt-bytes", type=int)
    validate.add_argument("--root", type=Path, default=ROOT)
    validate.add_argument("--config", type=Path, default=delegation_config.DEFAULT_CONFIG_PATH)
    validate.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.prompt_bytes is not None and args.prompt_bytes < 0:
        return _usage_error("PROMPT_BYTES_INVALID", "Pass --prompt-bytes as a non-negative integer.", args.json)
    try:
        contract = json.loads(args.contract.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _usage_error("CONTRACT_UNREADABLE", "Pass a readable JSON contract file with --contract.", args.json)
    try:
        registry = agent_registry.build_registry(args.root)
    except (agent_registry.RegistryError, OSError) as exc:
        return _usage_error(getattr(exc, "status", "REGISTRY_SOURCE_INVALID"), "Fix the agent sources under --root.", args.json)
    config, reason = delegation_config.load_config(args.config)
    if config is None:
        return _usage_error(reason, "Pass a valid delegation policy with --config.", args.json)
    result = validate_contract(contract, registry, config, prompt_bytes=args.prompt_bytes)
    print(json.dumps(result, sort_keys=True) if args.json else result["status"])
    return 0 if result["status"] == "VALID" else 2


if __name__ == "__main__":
    raise SystemExit(main())
