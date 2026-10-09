#!/usr/bin/env python3
"""Check the per-stage context manifest and slice contract before a dispatch.

The controller builds one manifest per dispatch: scoped excerpts (never whole
documents or transcripts), the project-context-guardian outcome, recorded
code/documentation divergences and, for IMPLEMENT/TEST/REVIEW, the slice
contract — editable paths, project-local playbooks, the authoritative acceptance
mapping and the observable verifiers each check needs. It reads only the
controller-owned JSON and the exact project-local playbook files named there;
it never reads other repository files, the vault or STATE and never mutates state
(the optional Jev gate below reads the governor cache under its existing lock).

When the project recorded explicit automatic Jev consent, PLAN and IMPLEMENT
also need the `semantic_governance` record: the fingerprint of a live
`semantic_governor.py decide` report for this ticket, read back and
structurally validated from the governor cache. A `REVIEW` outcome must carry
the human resolution. Only an explicit non-consent answer disables the gate;
a missing or unreadable setup fails closed.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import re
import sys
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

import jsonschema

from validate_protocol import READ_ONLY_ROLES, editable_pattern_is_safe

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "STAGE_CONTEXT_SCHEMA.json"
PROJECT_CONTEXT_STAGES = ("PLAN", "IMPLEMENT")
FORBIDDEN_SOURCE_NAMES = ("STATE.md", "ACTION_JOURNAL.json", "INCIDENTS.md", "action-journal-history")
SLICE_STAGES = ("IMPLEMENT", "TEST", "REVIEW")
GOVERNANCE_STAGES = ("PLAN", "IMPLEMENT")
JEV_PROJECT_SETUP_PATH = Path(__file__).resolve().parents[1] / "PROJECT_SETUP.md"
JEV_CACHE_PATH = Path(__file__).resolve().parents[1] / "JEV_CACHE.json"
APPROVAL_REUSED = "APPROVAL_REUSED"
APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
APPROVAL_NOT_REQUESTED = "APPROVAL_NOT_REQUESTED"
APPROVAL_NOT_APPLICABLE = "APPROVAL_NOT_APPLICABLE"
#: Default worker-prompt ceiling when the manifest does not set limits.max_prompt_bytes.
DEFAULT_MAX_PROMPT_BYTES = 48 * 1024
CONTEXT_ERROR_CODES = (
    "CONTEXT_BUDGET_EXCEEDED",
    "CONTEXT_EXCERPT_INVALID",
    "CONTEXT_EXCERPT_TOO_LARGE",
    "CONTEXT_SOURCE_DUPLICATED",
    "CONTEXT_SOURCE_FORBIDDEN",
    "PROMPT_TOO_LARGE",
    "PROJECT_CONTEXT_REQUIRED",
    "PROJECT_CONTEXT_GAPS_REQUIRED",
    "CONTEXT_GRAPH_FINDINGS_PRESENT",
    "CONTEXT_GRAPH_SELECTION_REQUIRED",
    "CONTEXT_GRAPH_GAPS_REQUIRED",
    "CONTEXT_GRAPH_STATUS_INCONSISTENT",
    "CONTEXT_GRAPH_DECISION_UNRECORDED",
    "SLICE_REQUIRED",
    "SLICE_CURRENT_INVALID",
    "SLICE_EDITABLE_PATHS_REQUIRED",
    "SLICE_EDITABLE_PATH_UNSAFE",
    "ANALYSIS_STAGE_EDITABLE_PATHS",
    "PLAYBOOK_CONTENT_MISMATCH",
    "PLAYBOOK_DUPLICATED",
    "PLAYBOOK_PATH_INVALID",
    "PLAYBOOK_REQUIRED",
    "PLAYBOOK_ROOT_INVALID",
    "PLAYBOOK_UNDECLARED",
    "PLAYBOOK_UNKNOWN_SLICE",
    "ACCEPTANCE_CHECK_UNVERIFIED",
    "INDEPENDENT_VERIFIER_REQUIRED",
    "VERIFIER_COMMAND_REQUIRED",
    "VERIFIER_UNKNOWN_CHECK",
    "SCOPE_CHANGE_REQUIRED",
    "JEV_GOVERNANCE_RECORD_REQUIRED",
    "JEV_GOVERNANCE_RECORD_UNVERIFIED",
    "JEV_GOVERNANCE_REVIEW_UNRESOLVED",
    "JEV_GOVERNANCE_SETUP_INVALID",
    "JEV_GOVERNANCE_PLATFORM_UNSUPPORTED",
    "SCHEMA_INVALID",
)


class ContextError(ValueError):
    """The manifest cannot be read."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def slice_contract(value: dict[str, Any], slice_id: str) -> dict[str, Any]:
    """Return the order-independent contract of one slice: what an approval authorizes.

    It holds only the ticket, the slice ID, its editable paths, its acceptance
    checks and the verifiers bound to them. The stage and the completed-slice
    cursor are excluded, so a hash computed when TASKS is approved matches the
    later IMPLEMENT dispatch of the same slice, and finishing S1 does not change
    the hash of S2.
    """
    contract = value["slice"]
    acceptance = {key: check for key, check in contract["acceptance"].items() if check["slice_id"] == slice_id}
    verifiers = []
    for item in contract["required_verification"]:
        bound = sorted(set(item["check_ids"]) & set(acceptance))
        if bound:
            verifiers.append({**item, "check_ids": bound})
    required_names = {
        item["name"]
        for item in contract["required_playbooks"]
        if slice_id in item["slice_ids"]
    }
    playbooks = [
        {
            **item,
            "references": sorted(
                item["references"],
                key=lambda reference: (reference["path"], reference["sha256"]),
            ),
        }
        for item in value["playbooks"]
        if item["name"] in required_names
    ]
    return {
        "ticket": value["ticket"],
        "slice_id": slice_id,
        "editable_paths": sorted(contract["editable_paths"]),
        "required_playbooks": sorted(playbooks, key=lambda item: item["name"]),
        "acceptance": acceptance,
        "required_verification": sorted(verifiers, key=lambda item: item["id"]),
    }


def slice_sha256(value: dict[str, Any]) -> str | None:
    """Hash the current IMPLEMENT slice contract; other stages authorize no writes."""
    contract = value.get("slice")
    if value["stage"] != "IMPLEMENT" or contract is None or len(set(contract["current_slice_ids"])) != 1:
        return None
    (slice_id,) = set(contract["current_slice_ids"])
    return hashlib.sha256(_canonical(slice_contract(value, slice_id))).hexdigest()


def _finding(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def _check_sources(value: dict[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    limits, sources = value["limits"], value["sources"]
    if len(sources) > limits["max_sources"]:
        errors.append(_finding("CONTEXT_BUDGET_EXCEEDED", f"{len(sources)} sources exceed max_sources {limits['max_sources']}"))
    seen: set[tuple[str, int, int]] = set()
    for source in sources:
        start, end = source["lines"]
        key = (source["path"], start, end)
        if key in seen:
            errors.append(_finding("CONTEXT_SOURCE_DUPLICATED", f"{source['path']}:{start}-{end} is listed twice"))
        seen.add(key)
        if set(PurePosixPath(source["path"]).parts) & set(FORBIDDEN_SOURCE_NAMES):
            errors.append(_finding(
                "CONTEXT_SOURCE_FORBIDDEN",
                f"{source['path']} is controller-owned runtime state; send the relevant facts, not a STATE or journal dump",
            ))
        if end < start:
            errors.append(_finding("CONTEXT_EXCERPT_INVALID", f"{source['path']} ends before it starts"))
        elif end - start + 1 > limits["max_lines_per_source"]:
            errors.append(_finding(
                "CONTEXT_EXCERPT_TOO_LARGE",
                f"{source['path']}:{start}-{end} exceeds {limits['max_lines_per_source']} lines; send an excerpt",
            ))
    return errors


def _check_project_context(value: dict[str, Any]) -> list[dict[str, str]]:
    project = value["project_context"]
    if value["stage"] not in PROJECT_CONTEXT_STAGES:
        return []
    if project["status"] == "MISSING" or not (project["evidence"] or "").strip() or not project["checked_head"]:
        return [_finding(
            "PROJECT_CONTEXT_REQUIRED",
            f"{value['stage']} needs a project-context-guardian result with evidence and the checked HEAD",
        )]
    if project["status"] == "PARTIAL" and not project["gaps"]:
        return [_finding("PROJECT_CONTEXT_GAPS_REQUIRED", "PARTIAL project context must name what was not examined")]
    return []


def _check_context_graph(value: dict[str, Any]) -> list[dict[str, str]]:
    """Check the optional context-graph record attached to this dispatch.

    The graph is never a condition for a dispatch: a project without one omits
    the key or declares `NOT_CONFIGURED`. But a graph that *is* declared must be
    structurally sound, must say which work it was queried for, and must carry
    the reason behind every prior decision it brings into scope. A graph with
    findings is worse than no graph, because it looks like evidence.
    """
    graph = value.get("context_graph")
    if graph is None:
        return []
    errors: list[dict[str, str]] = []
    status = graph["status"]
    if status == "NOT_CONFIGURED":
        if graph["selectors"] or graph["nodes"] or graph["decisions"] or graph["unresolved"] or graph["findings"]:
            errors.append(_finding(
                "CONTEXT_GRAPH_STATUS_INCONSISTENT",
                "NOT_CONFIGURED cannot carry selectors, nodes, decisions, gaps or findings",
            ))
        return errors
    if graph["findings"]:
        errors.append(_finding(
            "CONTEXT_GRAPH_FINDINGS_PRESENT",
            f"the graph reports {len(graph['findings'])} unresolved finding(s); repair the notes before citing them",
        ))
    if graph["source"] == "OBSIDIAN" and value["project_context"]["obsidian"] != "BOUND":
        errors.append(_finding(
            "CONTEXT_GRAPH_STATUS_INCONSISTENT",
            "an Obsidian-sourced graph requires a BOUND vault in project_context",
        ))
    if status == "PARTIAL" and not graph["unresolved"]:
        errors.append(_finding(
            "CONTEXT_GRAPH_GAPS_REQUIRED",
            "a PARTIAL graph must name the selectors it could not resolve",
        ))
    if status != "PARTIAL" and graph["unresolved"]:
        errors.append(_finding(
            "CONTEXT_GRAPH_STATUS_INCONSISTENT",
            f"{status} cannot leave selectors unresolved; report PARTIAL instead",
        ))
    if status != "MISSING" and value["stage"] in PROJECT_CONTEXT_STAGES and not (graph["selectors"] and graph["nodes"]):
        errors.append(_finding(
            "CONTEXT_GRAPH_SELECTION_REQUIRED",
            f"{value['stage']} must record which modules or paths the graph was queried for, and what it returned",
        ))
    recorded = {decision["id"] for decision in graph["decisions"]}
    for node in graph["nodes"]:
        if node["kind"] == "DECISION" and node["id"] not in recorded:
            errors.append(_finding(
                "CONTEXT_GRAPH_DECISION_UNRECORDED",
                f"{node['id']} is in scope but its reason and date are not carried",
            ))
    for decision_id in sorted(recorded - {node["id"] for node in graph["nodes"] if node["kind"] == "DECISION"}):
        errors.append(_finding(
            "CONTEXT_GRAPH_STATUS_INCONSISTENT",
            f"{decision_id} is recorded as a decision but is not a DECISION node in scope",
        ))
    return errors


def _frontmatter_identity(content: bytes) -> tuple[str, str] | None:
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None
    if not lines or lines[0].strip() != "---":
        return None
    identity: dict[str, str] = {}
    closed = False
    for line in lines[1:]:
        if line.strip() == "---":
            closed = True
            break
        if line[:1].isspace() or ":" not in line:
            continue
        key, raw = line.split(":", 1)
        if key in {"name", "version"}:
            if key in identity:
                return None
            value = raw.strip()
            pattern = (
                r"[a-z][a-z0-9-]{0,63}"
                if key == "name"
                else r"[0-9]+\.[0-9]+\.[0-9]+"
            )
            if re.fullmatch(pattern, value) is None:
                return None
            identity[key] = value
    if not closed or set(identity) != {"name", "version"}:
        return None
    return identity["name"], identity["version"]


def _verified_file(root: Path, relative: str, expected_hash: str, base: Path) -> bytes | None:
    try:
        unresolved = root / Path(*PurePosixPath(relative).parts)
        cursor = unresolved
        while cursor != root:
            if cursor.is_symlink():
                return None
            if root not in cursor.parents:
                return None
            cursor = cursor.parent
        candidate = unresolved.resolve(strict=True)
        boundary = base.resolve(strict=True)
        candidate.relative_to(boundary)
        if not candidate.is_file():
            return None
        content = candidate.read_bytes()
    except (OSError, ValueError):
        return None
    return content if hashlib.sha256(content).hexdigest() == expected_hash else None


def _live_project_root() -> Path | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(Path.cwd()), "rev-parse", "--show-toplevel"],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if result.returncode:
            return None
        return Path(result.stdout.strip()).resolve(strict=True)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _installed_playbook_root() -> Path | None:
    """Obsidian project container holding this controller, when vault-resident.

    runtime/ -> orchestration/ -> .hermes/ -> <container>. The container is
    authoritative only when it carries the Obsidian binding written by the
    installer; a repository-local installation returns ``None``.
    """
    container = Path(__file__).resolve().parents[3]
    binding = container / ".hermes" / "obsidian.json"
    if binding.is_file() and not binding.is_symlink() and (container / ".hermes" / "skills").is_dir():
        return container
    return None


def _check_playbooks(value: dict[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    loaded: dict[str, dict[str, Any]] = {}
    root = Path(value["project_root"])
    canonical_root: Path | None = None
    if root.is_absolute() and root.is_dir() and not root.is_symlink():
        try:
            canonical_root = root.resolve(strict=True)
        except OSError:
            canonical_root = None
    trusted_root = _live_project_root()
    root_valid = canonical_root is not None and root == canonical_root and canonical_root == trusted_root
    if value["playbooks"] and not root_valid:
        errors.append(_finding(
            "PLAYBOOK_ROOT_INVALID",
            "project_root must be the canonical non-symlinked live Git workspace when playbooks are loaded",
        ))
    # Playbook bytes come from wherever the controller is installed: the
    # repository (legacy) or the Obsidian project container (default).
    asset_root = root
    if root_valid and not (root / ".hermes" / "skills").is_dir():
        installed = _installed_playbook_root()
        if installed is not None:
            asset_root = installed

    for item in value["playbooks"]:
        name = item["name"]
        if name in loaded:
            errors.append(_finding("PLAYBOOK_DUPLICATED", f"{name} is loaded more than once"))
        loaded[name] = item
        expected = f".hermes/skills/{name}/SKILL.md"
        path_valid = item["path"] == expected
        if not path_valid:
            errors.append(_finding("PLAYBOOK_PATH_INVALID", f"{name} must load from {expected}"))

        reference_paths: set[str] = set()
        reference_prefix = f".hermes/skills/{name}/references/"
        for reference in item["references"]:
            relative = reference["path"]
            if relative in reference_paths:
                errors.append(_finding("PLAYBOOK_DUPLICATED", f"{name} reference {relative} is loaded more than once"))
            reference_paths.add(relative)
            reference_path = PurePosixPath(relative)
            reference_canonical = (
                not reference_path.is_absolute()
                and all(part not in {"", ".", ".."} for part in relative.split("/"))
                and "//" not in relative
            )
            if (
                not reference_canonical
                or not relative.startswith(reference_prefix)
                or not relative.endswith(".md")
            ):
                errors.append(_finding(
                    "PLAYBOOK_PATH_INVALID",
                    f"{name} reference must be a Markdown file under {reference_prefix}",
                ))
                continue
            if root_valid:
                base = asset_root / ".hermes" / "skills" / name / "references"
                if _verified_file(asset_root, relative, reference["sha256"], base) is None:
                    errors.append(_finding(
                        "PLAYBOOK_CONTENT_MISMATCH",
                        f"{name} reference content does not match its project-local descriptor",
                    ))

        if root_valid and path_valid:
            base = asset_root / ".hermes" / "skills" / name
            content = _verified_file(asset_root, item["path"], item["sha256"], base)
            identity = _frontmatter_identity(content) if content is not None else None
            if identity != (name, item["version"]):
                errors.append(_finding(
                    "PLAYBOOK_CONTENT_MISMATCH",
                    f"{name} content/frontmatter does not match its project-local descriptor",
                ))

    contract = value.get("slice")
    if contract is None:
        return errors
    known_slices = {check["slice_id"] for check in contract["acceptance"].values()}
    seen_requirements: set[str] = set()
    required_now: set[str] = set()
    current = set(contract["current_slice_ids"]) if value["stage"] == "IMPLEMENT" else set()
    for requirement in contract["required_playbooks"]:
        name = requirement["name"]
        if name in seen_requirements:
            errors.append(_finding("PLAYBOOK_DUPLICATED", f"{name} is required more than once"))
        seen_requirements.add(name)
        unknown = sorted(set(requirement["slice_ids"]) - known_slices)
        if unknown:
            errors.append(_finding(
                "PLAYBOOK_UNKNOWN_SLICE",
                f"{name} names unknown slices: {', '.join(unknown)}",
            ))
        if current & set(requirement["slice_ids"]):
            required_now.add(name)
    for name in sorted(required_now - set(loaded)):
        errors.append(_finding("PLAYBOOK_REQUIRED", f"{name} is required by the current slice but was not loaded"))
    if value["stage"] == "IMPLEMENT":
        for name in sorted(set(loaded) - required_now):
            errors.append(_finding("PLAYBOOK_UNDECLARED", f"{name} is loaded but not required by the current slice"))
    return errors


def _check_slice(value: dict[str, Any]) -> list[dict[str, str]]:
    stage, contract = value["stage"], value["slice"]
    if stage not in SLICE_STAGES:
        return []
    if contract is None:
        return [_finding("SLICE_REQUIRED", f"{stage} requires a slice contract")]
    errors: list[dict[str, str]] = []
    if stage == "IMPLEMENT":
        if len(set(contract["current_slice_ids"])) != 1:
            errors.append(_finding("SLICE_CURRENT_INVALID", "IMPLEMENT handles exactly one current slice"))
        if set(contract["current_slice_ids"]) & set(contract["completed_slice_ids"]):
            errors.append(_finding("SLICE_CURRENT_INVALID", "current and completed slices must be disjoint"))
        if not contract["editable_paths"]:
            errors.append(_finding("SLICE_EDITABLE_PATHS_REQUIRED", "IMPLEMENT must declare the slice's editable paths"))
    elif contract["editable_paths"]:
        errors.append(_finding("ANALYSIS_STAGE_EDITABLE_PATHS", f"{stage} is read-only and cannot declare editable paths"))
    for pattern in contract["editable_paths"]:
        if not editable_pattern_is_safe(pattern):
            errors.append(_finding(
                "SLICE_EDITABLE_PATH_UNSAFE",
                f"{pattern} is absolute, escapes the repository, is unanchored or matches everything",
            ))

    acceptance = contract["acceptance"]
    in_scope = (
        set(contract["current_slice_ids"]) if stage == "IMPLEMENT"
        else set(contract["current_slice_ids"]) | set(contract["completed_slice_ids"]) or {item["slice_id"] for item in acceptance.values()}
    )
    covered: dict[str, list[dict[str, Any]]] = {}
    for verifier in contract["required_verification"]:
        if verifier["kind"] != "HUMAN" and not verifier["command"]:
            errors.append(_finding("VERIFIER_COMMAND_REQUIRED", f"{verifier['id']} is machine-checkable and needs a command"))
        for check_id in verifier["check_ids"]:
            if check_id not in acceptance:
                errors.append(_finding("VERIFIER_UNKNOWN_CHECK", f"{verifier['id']} names unknown check {check_id}"))
            covered.setdefault(check_id, []).append(verifier)
    for check_id, check in sorted(acceptance.items()):
        if check["slice_id"] not in in_scope:
            continue
        verifiers = covered.get(check_id, [])
        wanted_kind = {"HUMAN"} if check["verifier"] == "HUMAN" else set(_MACHINE_KINDS)
        bound = [item for item in verifiers if item["kind"] in wanted_kind]
        if not bound:
            errors.append(_finding(
                "ACCEPTANCE_CHECK_UNVERIFIED",
                f"{check_id} has no {'human' if check['verifier'] == 'HUMAN' else 'observable'} verifier bound to it",
            ))
        elif check["verifier"] == "AGENT" and all(item["introduced_by_slice"] for item in bound):
            errors.append(_finding(
                "INDEPENDENT_VERIFIER_REQUIRED",
                f"{check_id} is bound only to verifiers this slice introduced; bind one that predates the slice",
            ))
    return errors


def _semantic_governor() -> Any:
    """Load the sibling governor by path, independent of sys.path and cwd."""
    path = Path(__file__).resolve().with_name("semantic_governor.py")
    name = "sdd_stage_context_semantic_governor"
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError(str(path))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


def _check_semantic_governance(value: dict[str, Any]) -> list[dict[str, str]]:
    """Fail closed: only an explicit non-consent record disables the gate."""
    if value["stage"] not in GOVERNANCE_STAGES:
        return []
    governor = _semantic_governor()
    state = governor.consent_state(JEV_PROJECT_SETUP_PATH)
    if state == governor.CONSENT_DISABLED:
        return []
    if state != governor.CONSENT_ENABLED:
        return [_finding(
            "JEV_GOVERNANCE_SETUP_INVALID",
            f"{JEV_PROJECT_SETUP_PATH} is missing or its typesafe_ai answer is unreadable; "
            "record none, an explicit false, or the automatic consent",
        )]
    if governor._platform_name() != "posix":
        return [_finding(
            "JEV_GOVERNANCE_PLATFORM_UNSUPPORTED",
            "automatic Jev governance is enabled but the governor cannot run on this platform",
        )]
    record = value.get("semantic_governance")
    if record is None:
        return [_finding(
            "JEV_GOVERNANCE_RECORD_REQUIRED",
            f"automatic Jev governance is enabled: run semantic_governor.py decide for {value['ticket']} "
            f"before {value['stage']} and record its fingerprint",
        )]
    fingerprint = record["fingerprint"]
    try:
        report = governor.verify_cached_decision(JEV_CACHE_PATH, fingerprint, value["ticket"])
    except (governor.GovernanceError, OSError):
        return [_finding(
            "JEV_GOVERNANCE_RECORD_UNVERIFIED",
            f"fingerprint {fingerprint[:12]} is not a valid governor decision for {value['ticket']} in {JEV_CACHE_PATH}",
        )]
    if report["status"] == "REVIEW" and not record["review_resolution"]:
        return [_finding(
            "JEV_GOVERNANCE_REVIEW_UNRESOLVED",
            "Jev returned REVIEW; record the human resolution instead of inventing a route",
        )]
    return []


_MACHINE_KINDS = ("TEST", "STATIC_ANALYSIS", "SCHEMA_VALIDATION", "STATE_INSPECTION", "LOG_INSPECTION")

#: One actionable next step per finding code; ``check`` attaches it to every finding.
FINDING_NEXT_STEPS = {
    "DISPATCH_QUESTION_REQUIRED": "Record the dispatch question in the manifest's `dispatch` block, or do not dispatch the sub-agent (default = do not dispatch).",
    "CONTEXT_BUDGET_EXCEEDED": "Drop sources until max_sources holds, or ask the user for one investigation expansion (`sdd.py budget --raise investigation_expansions`).",
    "CONTEXT_EXCERPT_TOO_LARGE": "Send a narrower line range for that source.",
    "PROMPT_TOO_LARGE": "Regenerate the prompt with `sdd.py prepare` (it drops excerpts first), narrow the sources, or raise limits.max_prompt_bytes in the manifest.",
    "PROJECT_CONTEXT_REQUIRED": "Dispatch the project-context-guardian first; `sdd.py next` schedules it before PLAN and IMPLEMENT.",
    "SCOPE_CHANGE_REQUIRED": "The slice differs from the approved one; ask the user to approve the new scope.",
    "SCHEMA_INVALID": "Regenerate the manifest with `sdd.py manifest` instead of editing it.",
}
DEFAULT_FINDING_NEXT_STEP = "Fix the named field in the manifest source data, regenerate it with `sdd.py manifest`, and check again."


def _check_prompt(value: dict[str, Any], prompt_bytes: int | None) -> list[dict[str, str]]:
    if prompt_bytes is None:
        return []
    limit = value["limits"].get("max_prompt_bytes") or DEFAULT_MAX_PROMPT_BYTES
    if prompt_bytes > limit:
        return [_finding("PROMPT_TOO_LARGE", f"the worker prompt is {prompt_bytes} bytes; max_prompt_bytes is {limit}")]
    return []


def check(value: Any, *, prompt_bytes: int | None = None, role: str | None = None) -> dict[str, Any]:
    """Return {valid, errors, approval, slice_sha256, next_step} for one manifest (and optional prompt size).

    A read-only ``role`` (the project-context-guardian runs before the project
    context exists) tolerates a missing project context, as ``verifier_context`` does.
    """
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    schema_errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(value), key=lambda error: list(error.path))
    if schema_errors:
        return {
            "valid": False,
            "errors": [_finding("SCHEMA_INVALID", error.message) for error in schema_errors[:5]],
            "approval": APPROVAL_REQUIRED,
            "slice_sha256": None,
            "next_step": FINDING_NEXT_STEPS["SCHEMA_INVALID"],
        }
    errors = [
        *_check_sources(value),
        *_check_prompt(value, prompt_bytes),
        *_check_project_context(value),
        *_check_context_graph(value),
        *_check_playbooks(value),
        *_check_slice(value),
        *_check_semantic_governance(value),
    ]
    if role is not None and role in READ_ONLY_ROLES and value["stage"] in READ_ONLY_ROLES[role]:
        errors = [item for item in errors if item["code"] not in {"PROJECT_CONTEXT_REQUIRED", "PROJECT_CONTEXT_GAPS_REQUIRED"}]
    if role is not None and not value.get("dispatch"):
        errors.append(_finding("DISPATCH_QUESTION_REQUIRED",
                               "a sub-agent dispatch must record dispatch.pending_decision, deterministic_attempt and if_empty (DISPATCH_POLICY.md)"))
    digest = slice_sha256(value)
    approval = value["approval"]
    if approval is None:
        approval_state = APPROVAL_NOT_REQUESTED
    elif digest is None:
        approval_state = APPROVAL_NOT_APPLICABLE
    elif digest in approval["approved_slice_sha256s"]:
        approval_state = APPROVAL_REUSED
    else:
        approval_state = APPROVAL_REQUIRED
        errors.append(_finding(
            "SCOPE_CHANGE_REQUIRED",
            "the slice contract differs from the approved one; this is a new scope and needs its own approval",
        ))
    if errors and approval_state == APPROVAL_REUSED:
        approval_state = APPROVAL_REQUIRED
    if errors:
        next_step = FINDING_NEXT_STEPS.get(errors[0]["code"], DEFAULT_FINDING_NEXT_STEP)
    else:
        next_step = "Dispatch is allowed for this manifest; continue with the printed batch (`sdd.py next`)."
    return {"valid": not errors, "errors": errors, "approval": approval_state, "slice_sha256": digest, "next_step": next_step}


def recorded_waivers_from_state(state_path: Path, acceptance: dict[str, Any]) -> dict[str, Any]:
    """Waivers the controller recorded in STATE (`sdd.py waive`), limited to this manifest's checks."""
    import state_format

    data = state_format.parse(state_path.read_text(encoding="utf-8"))
    waivers = ((data.get("delivery") or {}).get("waivers") or {})
    return {key: value for key, value in waivers.items() if key in acceptance}


def verifier_context(value: dict[str, Any], role: str | None = None) -> dict[str, Any]:
    """Project the slice contract into validate_protocol.py keyword arguments.

    IMPLEMENT requires only the verifiers bound to the current slice's checks:
    a later slice's verifier may be a test that does not exist yet. TEST and
    REVIEW require every verifier. With a read-only `role` (dispatched during
    IMPLEMENT before the project context exists), the missing project context
    is tolerated and no editable paths are emitted; every other rule applies.
    """
    if role is not None and (role not in READ_ONLY_ROLES or value["stage"] not in READ_ONLY_ROLES[role]):
        raise ContextError(f"role {role} is not a read-only role allowed in {value['stage']}")
    result = check(value)
    errors = [
        item for item in result["errors"]
        if not (role is not None and item["code"] in {"PROJECT_CONTEXT_REQUIRED", "PROJECT_CONTEXT_GAPS_REQUIRED"})
    ]
    if errors:
        raise ContextError("; ".join(f"{item['code']}: {item['detail']}" for item in errors))
    contract = value["slice"]
    if contract is None:
        return {"role": role} if role else {}
    acceptance = contract["acceptance"]
    if value["stage"] == "IMPLEMENT":
        current = set(contract["current_slice_ids"])
        required_checks = {key for key, check in acceptance.items() if check["slice_id"] in current}
    else:
        required_checks = set(acceptance)
    context: dict[str, Any] = {
        "expected_acceptance": copy.deepcopy(acceptance),
        "required_commands": sorted({
            item["command"] for item in contract["required_verification"]
            if item["command"] and set(item["check_ids"]) & required_checks
        }),
        "check_verifiers": {
            check_id: sorted(
                item["command"] for item in contract["required_verification"]
                if item["command"] and check_id in item["check_ids"]
            )
            for check_id in sorted(acceptance)
        },
    }
    if value["stage"] == "IMPLEMENT":
        context.update(
            current_slice_ids=sorted(contract["current_slice_ids"]),
            completed_slice_ids=sorted(contract["completed_slice_ids"]),
        )
        if role is None:
            context["editable_paths"] = sorted(contract["editable_paths"])
    if role is not None:
        context["role"] = role
    return context


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check a per-stage context manifest and its declared project-local playbooks.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "hash", "verifier-context"):
        command = commands.add_parser(name)
        command.add_argument("--context", required=True, help="Controller-owned stage context manifest JSON.")
        command.add_argument("--json", action="store_true", help="Emit structured JSON.")
        command.add_argument("--project-setup", type=Path, help="PROJECT_SETUP.md holding the Jev consent record.")
        command.add_argument("--jev-cache", type=Path, help="Governor cache that holds semantic_governance fingerprints.")
        if name == "check":
            command.add_argument("--prompt-file", type=Path, help="Worker prompt whose size must not exceed limits.max_prompt_bytes.")
        if name in {"check", "verifier-context"}:
            command.add_argument("--role", choices=sorted(READ_ONLY_ROLES), help="Read-only sub-agent role being dispatched.")
        if name == "verifier-context":
            command.add_argument("--state", type=Path, help="STATE.md whose recorded waivers (sdd.py waive) are emitted as recorded_waivers.")
            command.add_argument("--output", type=Path, help="Write the context JSON to this file instead of only printing it.")
    args = parser.parse_args(argv)
    global JEV_PROJECT_SETUP_PATH, JEV_CACHE_PATH
    if args.project_setup is not None:
        JEV_PROJECT_SETUP_PATH = args.project_setup.absolute()
    if args.jev_cache is not None:
        JEV_CACHE_PATH = args.jev_cache.absolute()
    try:
        value = json.loads(Path(args.context).read_text(encoding="utf-8"))
        if args.command == "check":
            prompt_bytes = args.prompt_file.stat().st_size if args.prompt_file is not None else None
            result = check(value, prompt_bytes=prompt_bytes, role=args.role)
            code = 0 if result["valid"] else 2
        elif args.command == "hash":
            checked = check(value)
            if not checked["valid"]:
                raise ContextError("; ".join(f"{item['code']}: {item['detail']}" for item in checked["errors"]))
            digest = checked["slice_sha256"]
            result, code = {"slice_sha256": digest}, 0 if digest else 2
        else:
            result, code = verifier_context(value, role=args.role), 0
            if args.state is not None and value.get("slice"):
                waivers = recorded_waivers_from_state(args.state, value["slice"]["acceptance"])
                if waivers:
                    result["recorded_waivers"] = waivers
            if args.output is not None:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(result, sort_keys=True, indent=1) + "\n", encoding="utf-8")
                result = {"status": "WRITTEN", "path": str(args.output), "recorded_waivers": sorted(result.get("recorded_waivers", {})),
                          "next_step": "Pass this file to validate_protocol.py --context."}
    except (OSError, json.JSONDecodeError, KeyError, ContextError) as exc:
        result, code = {"valid": False, "errors": [_finding("SCHEMA_INVALID", str(exc))], "next_step": DEFAULT_FINDING_NEXT_STEP}, 2
    print(json.dumps(result, sort_keys=True) if args.json else result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
