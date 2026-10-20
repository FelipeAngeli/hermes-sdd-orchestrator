#!/usr/bin/env python3
"""Deterministic worker-prompt rendering: acceptance rules, retry recipes, self-check, compact profile.

Prompt code moved out of ``sdd.py`` and imported back; ``sdd`` is imported lazily to avoid a cycle.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

RUNTIME = Path(__file__).resolve().parent
ORCHESTRATION = RUNTIME.parent

ACCEPTANCE_RULES_HEADING = "## Acceptance rules (the validator applies exactly these to your result)"
SELFCHECK_HEADING = "## Self-check before you answer"
REJECTION_HEADING = "## Most common rejections (re-check now)"
FIX_HEADING = "## Fix these first (previous attempt {parent_action_id} was rejected)"

_CITE_RE = re.compile(r"^AGENT PASS evidence for (.+) must cite")
_REQUIRED_RE = re.compile(r"^required verification `([^`]+)` was not recorded as passing$")
_OUTSIDE_RE = re.compile(r"^(.+) is outside the slice editable_paths$")
_SCHEMA_MESSAGE = re.compile(r"is not one of|is a required property|is not of type|must equal requested action|expected version")
_ONLY_JSON = "Your final message must be only the JSON object, no prose, no code fence."


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


def compact_match_tokens(context: dict[str, Any], ticket: str) -> list[str]:
    tokens: set[str] = set()
    if ticket:
        tokens.add(ticket)
    for path in context.get("editable_paths") or []:
        tokens.add(Path(path).name)
    for check_id in context.get("expected_acceptance") or {}:
        tokens.add(check_id)
    tokens.discard("")
    return sorted(token for token in tokens if token)


def _merge_windows(windows: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not windows:
        return []
    merged = [list(sorted(windows)[0])]
    for low, high in sorted(windows)[1:]:
        if low <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], high)
        else:
            merged.append([low, high])
    return [(low, high) for low, high in merged]


def compact_source_excerpts(repo: Path, sources: list[dict[str, Any]], tokens: list[str]) -> list[str]:
    """Compact sources: ±15 lines around any line mentioning a token, else the first 40 lines."""
    parts: list[str] = []
    for source in sources:
        try:
            lines = (repo / source["path"]).read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        start = max(0, source["lines"][0] - 1)
        end = min(len(lines), source["lines"][1])
        matches = [index for index in range(start, end) if any(token and token in lines[index] for token in tokens)]
        if not matches:
            windows = [(0, min(40, len(lines)))]
        else:
            windows = _merge_windows([(max(0, index - 15), min(len(lines), index + 16)) for index in matches])
        for low, high in windows:
            body = "\n".join(f"{low + 1 + offset}: {line}" for offset, line in enumerate(lines[low:high]))
            parts.append(f"### {source['path']} lines {low + 1}-{high}\n{body}")
    return parts


def acceptance_example(acceptance: dict[str, Any], required_ids: set[str], check_verifiers: dict[str, Any]) -> str:
    for check_id in sorted(required_ids):
        check = acceptance.get(check_id)
        if not check or check.get("verifier") != "AGENT":
            continue
        commands = check_verifiers.get(check_id) or []
        evidence = f"`{commands[0]}` passed" if commands else "`<bound command>` passed"
        return json.dumps({"id": check_id, "criterion": check.get("criterion"), "verification_method": check.get("verification_method"),
                           "verifier": check.get("verifier"), "slice_id": check.get("slice_id"), "status": "PASS",
                           "evidence": evidence, "waiver": None}, ensure_ascii=False)
    return "{}"


def tdd_example(current_slice_ids: list[str]) -> str:
    slice_id = (current_slice_ids or ["S1"])[0]
    return json.dumps({"id": slice_id, "red_command": "<red command>", "red_exit_code": 1,
                       "expected_failure": "<expected functional failure>", "red_failure_kind": "EXPECTED_FUNCTIONAL",
                       "green_command": "<green command>", "green_exit_code": 0, "green_result": "<green result>"},
                      ensure_ascii=False)


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
        if check.get("verifier") != "AGENT":
            continue
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
                     + ", ".join(f"`{command}`" for command in required_commands) + ".")
    if stage == "IMPLEMENT" and editable:
        lines.append(f"- Write only paths matching editable_paths: {json.dumps(editable, ensure_ascii=False)}.")
    lines.append("- If `stage.status` is SUCCESS, `blockers` must be [].")
    for check_id in sorted(waivers):
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


def _recipe_for(error: dict[str, str], context: dict[str, Any]) -> str:
    reason = error.get("reason") or ""
    path = error.get("path") or "$"
    if path == "$" or reason == "OUTPUT_INVALID" or _SCHEMA_MESSAGE.search(reason):
        return _ONLY_JSON
    match = _CITE_RE.search(reason)
    if match:
        commands = (context.get("check_verifiers") or {}).get(match.group(1)) or []
        joined = "`" + "`, `".join(commands) + "`" if commands else "`<bound command>`"
        return (f"Rewrite {match.group(1)} evidence citing literally, in backticks, {joined}, and record that command "
                "in commands[] with exit_code 0 and result PASS.")
    match = _REQUIRED_RE.search(reason)
    if match:
        return (f"Run `{match.group(1)}` and record it in commands[] with exit 0/PASS; if it cannot pass in your "
                "sandbox, return BLOCKED with a blocker instead of SUCCESS.")
    if reason == "SUCCESS cannot include blockers":
        return "Either fix and empty blockers, or set stage.status to BLOCKED."
    if reason in {"IMPLEMENT SUCCESS requires at least one TDD slice", "reported TDD slice ids must exactly match current_slice_ids"}:
        ids = context.get("current_slice_ids") or ["<slice id>"]
        return (f"tdd_slices must be exactly [{', '.join(ids)}]; each needs red_exit_code != 0, red_failure_kind "
                "EXPECTED_FUNCTIONAL, non-empty expected_failure, green_command and green_exit_code 0.")
    if reason in {"accepted RED requires a non-zero exit code", "accepted RED requires an expected functional failure",
                  "infrastructure failure is not valid RED evidence"}:
        verifier = (context.get("required_commands") or [None])[0]
        return (f"Use as RED the slice's verifier against the pre-change content, e.g. `git stash`-free: "
                f"`git show HEAD:<path> | {verifier or '<verifier>'}` or the focused test before your change; it must "
                "exit ≠ 0 for the functional reason; set red_failure_kind EXPECTED_FUNCTIONAL.")
    if reason == "accepted GREEN requires exit code 0":
        return "Run the GREEN command and record it in commands[] with exit_code 0 and a non-empty green_result."
    match = _OUTSIDE_RE.search(reason)
    if match:
        allowed = ", ".join(context.get("editable_paths") or []) or "the declared editable_paths"
        return f"Remove changes to {match.group(1)}; allowed: {allowed}."
    if reason in {"future slice checks must remain PLANNED", "future slice checks cannot carry evidence"}:
        return "Keep future slice checks at status PLANNED with evidence null."
    if reason in {"current and completed slice checks require PASS or WAIVED", "current and completed slice checks require evidence"}:
        return "Set each current and completed slice check to PASS or WAIVED with evidence, or return BLOCKED with a blocker."
    return f"Fix: {reason} at {path}."


def retry_recipe(errors: list[dict[str, str]], context: dict[str, Any]) -> list[str]:
    recipe: list[str] = []
    for error in errors:
        instruction = _recipe_for(error, context)
        if instruction and instruction not in recipe:
            recipe.append(instruction)
    return recipe


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
            "`\"valid\": true`; then answer with exactly that JSON. The controller re-validates everything.")


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
            parts.extend(compact_source_excerpts(ctx.repo, manifest["sources"], tokens))
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
