#!/usr/bin/env python3
"""Deterministic worker-prompt rendering: acceptance rules, self-check, compact profile.

Prompt code moved out of ``sdd.py`` and imported back; ``sdd`` is imported lazily to avoid a cycle.
Retry recipes live in ``retry_recipes.py`` and compact windowing in ``prompt_compact.py``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import prompt_compact
import retry_recipes

RUNTIME = Path(__file__).resolve().parent
ORCHESTRATION = RUNTIME.parent

ACCEPTANCE_RULES_HEADING = "## Acceptance rules (the validator applies exactly these to your result)"
SELFCHECK_HEADING = "## Self-check before you answer"
REJECTION_HEADING = "## Most common rejections (re-check now)"
FIX_HEADING = "## Fix these first (previous attempt {parent_action_id} was rejected)"

compact_match_tokens = prompt_compact.compact_match_tokens
compact_source_excerpts = prompt_compact.compact_source_excerpts
retry_recipe = retry_recipes.retry_recipe


def _excerpt(repo: Path, source: dict[str, Any]) -> str:
    start, end = source["lines"]
    lines = (repo / source["path"]).read_text(encoding="utf-8").splitlines()[start - 1:end]
    return "\n".join(f"{start + index}: {line}" for index, line in enumerate(lines))


def prompt_profile(stage: str) -> str:
    """The stage's EXECUTORS.md ``prompt_profile``; any policy error means standard."""
    import executor_launch

    try:
        policy = executor_launch.load_policy()
    except executor_launch.LaunchError:
        return "standard"
    settings = (policy.get("stages") or {}).get(stage) or {}
    return settings.get("prompt_profile") or "standard"


def _check_example(check_id: str, check: dict[str, Any], *, status: str, evidence: str | None, waiver: Any = None) -> str:
    return json.dumps({
        "id": check_id,
        "criterion": check.get("criterion") or "<criterion>",
        "verification_method": check.get("verification_method") or "<verification method>",
        "verifier": check.get("verifier") or "HUMAN",
        "slice_id": check.get("slice_id"),
        "status": status,
        "evidence": evidence,
        "waiver": waiver,
    }, ensure_ascii=False)


def acceptance_example(acceptance: dict[str, Any], required_ids: set[str], check_verifiers: dict[str, Any]) -> str:
    if required_ids:
        for check_id in sorted(required_ids):
            check = acceptance.get(check_id)
            if not check or check.get("verifier") != "AGENT":
                continue
            commands = check_verifiers.get(check_id) or []
            evidence = f"`{commands[0]}` passed" if commands else "`<bound command>` passed"
            return _check_example(check_id, check, status="PASS", evidence=evidence)
        for check_id in sorted(required_ids):
            check = acceptance.get(check_id)
            if not check or check.get("verifier") != "HUMAN":
                continue
            return _check_example(check_id, check, status="PASS",
                                  evidence=f"`<recorded human decision for {check_id}>`")
    check_id = next(iter(sorted(acceptance)), "<check id>")
    return _check_example(check_id, acceptance.get(check_id) or {}, status="PLANNED", evidence=None)


def tdd_example(current_slice_ids: list[str]) -> str:
    slice_id = (current_slice_ids or ["S1"])[0]
    return json.dumps({
        "id": slice_id,
        "objective": "<objective>",
        "test_file": "<test file>",
        "red_command": "<red command>",
        "red_exit_code": 1,
        "expected_failure": "<expected functional failure>",
        "red_failure_kind": "EXPECTED_FUNCTIONAL",
        "minimal_implementation": "<minimal implementation>",
        "green_command": "<green command>",
        "green_exit_code": 0,
        "green_result": "<green result>",
    }, ensure_ascii=False)


def acceptance_rules_block(context: dict[str, Any], stage: str) -> str:
    acceptance = context.get("expected_acceptance") or {}
    current = context.get("current_slice_ids") or []
    completed = context.get("completed_slice_ids") or []
    verified = set(current) | set(completed)
    check_verifiers = context.get("check_verifiers") or {}
    required_commands = context.get("required_commands") or []
    waivers = context.get("recorded_waivers") or {}
    editable = context.get("editable_paths") or []
    required_ids = set(acceptance) if stage == "TEST" else {
        check_id for check_id, check in acceptance.items() if check.get("slice_id") in verified
    }
    future_ids = set() if stage == "TEST" else {
        check_id for check_id, check in acceptance.items() if check.get("slice_id") not in verified
    }
    lines = [ACCEPTANCE_RULES_HEADING, ""]
    for check_id in sorted(required_ids):
        check = acceptance[check_id]
        verifier = check.get("verifier")
        if verifier == "HUMAN":
            lines.append(f"- {check_id} is HUMAN-verified: report PASS with the recorded human decision as evidence, "
                         "or WAIVED with its recorded waiver object; never leave it PLANNED.")
        elif verifier == "AGENT":
            commands = check_verifiers.get(check_id) or []
            joined = ", ".join(f"`{command}`" for command in commands)
            lines.append(f"- {check_id} → evidence must contain, in backticks, one of: {joined}; that command must appear in "
                         f"`commands[]` with `exit_code: 0` and `result: \"PASS\"`.")
    for check_id in sorted(future_ids):
        lines.append(f"- {check_id} stays PLANNED with evidence null.")
    if stage == "IMPLEMENT":
        lines.append(f"- `tdd_slices` must contain exactly the ids [{', '.join(current)}]; each needs `red_command`, "
                     f"`red_exit_code` ≠ 0, `red_failure_kind: \"EXPECTED_FUNCTIONAL\"`, non-empty `expected_failure`, "
                     f"`green_command`, `green_exit_code: 0`.")
    if required_commands:
        lines.append(f"- Every `required_commands` entry must be recorded in `commands[]` as exit 0 / PASS: "
                     + ", ".join(f"`{command}`" for command in required_commands)
                     + "; unless every check bound to it is WAIVED.")
    if stage == "IMPLEMENT" and editable:
        lines.append(f"- Write only paths matching editable_paths: {json.dumps(editable, ensure_ascii=False)}.")
    lines.append("- If `stage.status` is SUCCESS, `blockers` must be [].")
    waiver_eligible = set(acceptance) if stage == "TEST" else required_ids
    for check_id in sorted(waivers):
        if check_id in waiver_eligible:
            lines.append(f"- {check_id} is WAIVED with exactly this waiver object "
                         f"{json.dumps(waivers[check_id], ensure_ascii=False, sort_keys=True)}.")
    lines.extend(["", "Minimal valid example:",
                  f"- `acceptance_checks[0]`: {acceptance_example(acceptance, required_ids, check_verifiers)}",
                  f"- `tdd_slices[0]`: {tdd_example(current)}"])
    return "\n".join(lines)


def rejection_checklist(context: dict[str, Any], stage: str) -> str:
    current = sorted(context.get("current_slice_ids") or [])
    lines = [REJECTION_HEADING, "",
             "- Evidence must cite a bound verifier command in backticks.",
             "- Record each required command in `commands[]` with `exit_code: 0` and `result: \"PASS\"`."]
    if stage == "IMPLEMENT":
        lines.append(f"- `tdd_slices` ids exactly [{', '.join(current)}]; RED exit_code ≠ 0 with red_failure_kind "
                     "EXPECTED_FUNCTIONAL, GREEN exit_code 0.")
    lines += ["- If `stage.status` is SUCCESS, `blockers` must be [].",
              "- The final message is only the JSON object."]
    return "\n".join(lines)


def controller_data(ctx: Any, plan: dict[str, Any], manifest: dict[str, Any], *, brief: bool) -> dict[str, Any]:
    import sdd as _sdd

    delivery = ctx.delivery
    stage, role = plan["stage"], plan["role"]
    acceptance = delivery.get("acceptance") or {}
    waivers = delivery.get("waivers") or {}
    data: dict[str, Any] = {
        "ticket": ctx.ticket, "stage": stage, "role": role, "profile": ctx.profile,
        "deliverable_kind": delivery.get("deliverable_kind"),
        "request": {"title": (ctx.state.get("ticket") or {}).get("title"), "objective": (ctx.state.get("ticket") or {}).get("objective"),
                    "quote": (delivery.get("authorization") or {}).get("quote")},
        "executor": _sdd.executor_for(stage).upper(),
        "acceptance": [{"id": key, **check} for key, check in sorted(acceptance.items())],
        "recorded_waivers": {key: value for key, value in waivers.items() if key in acceptance},
        "answers": delivery.get("answers") or [],
        "protected_preexisting": (ctx.state.get("baseline") or {}).get("protected_preexisting") or [],
    }
    if manifest.get("slice"):
        import stage_context

        verifier = stage_context.verifier_context(manifest, role=role) if role is None else {}
        data["slice"] = {key: verifier.get(key) for key in
                         ("current_slice_ids", "completed_slice_ids", "editable_paths", "required_commands", "check_verifiers")
                         if key in verifier}
    if stage == "REVIEW":
        data["gates"] = {name: (ctx.state.get("gates") or {}).get(name, {}).get("status", "PENDING") for name in _sdd.GATE_NAMES}
        data["agent_owned"] = (ctx.state.get("ownership") or {}).get("agent_owned") or []
        data["human_accepted_paths"] = (ctx.state.get("ownership") or {}).get("human_accepted") or []
    previous = delivery.get("summaries") or {}
    data["previous"] = {name: ({"summary": item.get("summary")} if brief else item) for name, item in previous.items()}
    if plan["parent"]:
        retry: dict[str, Any] = {"parent_action_id": plan["parent"]["action_id"],
                                 "reason": "timeout" if plan["parent"]["exit_code"] == 124 else plan["parent"]["status"].lower()}
        errors_file = Path(plan["parent"]["errors_file"])
        if errors_file.is_file():
            retry["errors"] = json.loads(errors_file.read_text(encoding="utf-8"))[:10]
        context = _sdd.verifier_context(ctx, manifest, role) if stage in {"IMPLEMENT", "TEST"} and role is None else {}
        retry["recipe"] = retry_recipe(retry.get("errors") or [], context)
        data["retry"] = retry
    return data


def _slice_line(ctx: Any, plan: dict[str, Any], context: dict[str, Any]) -> str | None:
    slice_id = plan.get("slice_id") or ((context.get("current_slice_ids") or [None])[0])
    if not slice_id:
        return None
    planned = (ctx.delivery.get("slices") or {}).get("planned") or list(context.get("current_slice_ids") or [])
    line = f"Only slice {slice_id}. Do not create or change files for any other slice (planned: {json.dumps(planned)}); stop after its GREEN."
    import executor_launch

    try:
        policy = executor_launch.load_policy()
        timeout = (policy.get("stages") or {}).get(plan["stage"], {}).get("timeout_seconds")
        if isinstance(timeout, int) and not isinstance(timeout, bool):
            line += f" Finish within {int(timeout * 0.8 // 60)} minutes."
    except executor_launch.LaunchError:
        pass
    return line


def selfcheck_context(ctx: Any, manifest: dict[str, Any], role: str | None) -> dict[str, Any]:
    import sdd as _sdd

    return _sdd.verifier_context(ctx, manifest, role)


def selfcheck_section(ctx: Any, plan: dict[str, Any]) -> str:
    action_id = plan["action_id"]
    context_path = ctx.run_dir() / f"{action_id}.selfcheck-context.json"
    result_file = f"$TMPDIR/sdd-selfcheck-{action_id}.json"
    command = f"{sys.executable} {RUNTIME / 'validate_protocol.py'} --action {plan['stage']} --result {result_file} --context {context_path} --json"
    return (SELFCHECK_HEADING + "\n\n"
            f"Write your final JSON to `{result_file}` (outside the repository), run `{command}` and fix until it prints "
            "`\"valid\": true`; then answer with exactly that JSON. The controller re-validates everything. "
            f"If `$TMPDIR` is unset or not writable, use `/tmp/sdd-selfcheck-{action_id}.json` instead.")


def _fix_section(parent_action_id: str, recipe: list[str]) -> str:
    heading = FIX_HEADING.format(parent_action_id=parent_action_id)
    if not recipe:
        return heading
    return heading + "\n\n" + "\n".join(f"- {instruction}" for instruction in recipe)


def build_prompt(ctx: Any, plan: dict[str, Any], manifest: dict[str, Any]) -> str:
    import sdd as _sdd

    stage, role = plan["stage"], plan["role"]
    limit = int(manifest["limits"].get("max_prompt_bytes") or _sdd.DEFAULT_MAX_PROMPT_BYTES)
    if plan["reduced"]:
        limit //= 2
    compact = prompt_profile(stage) == "compact"
    brief_path = ORCHESTRATION / ("sub-agents/project-context-guardian.md" if role == _sdd.GUARDIAN_ROLE else f"agents/{stage.lower()}.md")
    envelope = "review_result" if stage == "REVIEW" else "executor_result"
    playbooks = [str(ORCHESTRATION.parent / item["path"].split("/", 1)[1]) for item in manifest["playbooks"]]
    context: dict[str, Any] = {}
    if stage in {"IMPLEMENT", "TEST"} and role is None:
        context = _sdd.verifier_context(ctx, manifest, role)
    tokens = compact_match_tokens(context, ctx.ticket) if compact else []

    def render(with_excerpts: bool, brief: bool) -> str:
        brief = True if compact else brief
        data = controller_data(ctx, plan, manifest, brief=brief)
        parts = [f"# SDD worker: {role or stage} for ticket {ctx.ticket}"]
        if stage == "IMPLEMENT" and role is None:
            slice_line = _slice_line(ctx, plan, context)
            if slice_line:
                parts.append(slice_line)
        if plan["parent"]:
            recipe = (data.get("retry") or {}).get("recipe") or []
            parts.append(_fix_section(plan["parent"]["action_id"], recipe))
        parts.append(f"Read your brief first: {brief_path}")
        parts.append(("Load these playbooks yourself: " + ", ".join(playbooks)) if playbooks else "No playbook is required.")
        parts.append(f"Return exactly one JSON object `{{\"{envelope}\": {{...}}}}` matching the schema the CLI enforces "
                     f"(schema_version 3{'' if stage == 'REVIEW' else f', stage.value {stage}'}). Copy every acceptance check below with its id, "
                     "criterion, verification_method, verifier and slice_id unchanged; change only status, evidence and waiver. A check in "
                     "recorded_waivers is WAIVED with exactly that waiver object and evidence quoting it. Never write STATE or the journal, "
                     "never commit, push or call external systems.")
        if stage in {"IMPLEMENT", "TEST"} and role is None:
            parts.append(acceptance_rules_block(context, stage))
        parts.append("## Controller data (authoritative)")
        parts.append("```json\n" + json.dumps(data, ensure_ascii=False, indent=None if compact else 1) + "\n```")
        parts.append("## Sources")
        if compact:
            parts.extend(compact_source_excerpts(ctx.repo, manifest["sources"], tokens, with_excerpts=with_excerpts))
        else:
            for source in manifest["sources"]:
                parts.append(f"### {source['path']} lines {source['lines'][0]}-{source['lines'][1]}")
                if with_excerpts:
                    parts.append(_excerpt(ctx.repo, source))
        if stage in {"IMPLEMENT", "TEST"} and role is None:
            parts.append(selfcheck_section(ctx, plan))
        if compact:
            parts.append(rejection_checklist(context, stage))
        return "\n\n".join(parts) + "\n"

    for with_excerpts, brief in ((not plan["reduced"], plan["reduced"]), (False, False), (False, True)):
        text = render(with_excerpts, brief)
        if len(text.encode("utf-8")) <= limit:
            return text
    raise _sdd.SddError("PROMPT_BUDGET_EXCEEDED",
                        f"The {stage} prompt needs {len(text.encode('utf-8'))} bytes even without excerpts; max_prompt_bytes allows {limit}.")
