#!/usr/bin/env python3
"""Single deterministic entry point for the SDD controller.

The controller (an LLM) runs ``sdd.py next``, executes exactly the printed
``commands`` in order (stopping at the first non-zero exit), and runs
``sdd.py next`` again. When ``end_turn`` is true it stops and either reports
DONE or asks the user what ``next_step`` names, recording the answer with the
printed ``next_command``. It never reads policies to decide the next step,
never hand-builds a manifest, snapshot, journal payload or prompt, and never
edits STATE or the journal.

Every printed batch is pre-validated here (manifest, prompt size, result
validity), so a non-zero exit happens only where the outcome is genuinely
unknown in advance (an executor run or a gate command); its effect is recorded
in the journal or STATE and the next ``next`` differs. Standard library plus the
sibling runtime modules only; STATE is written only while the journal is a
pristine IDLE journal, or through the journal's own state commit.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RUNTIME = Path(__file__).resolve().parent
ORCHESTRATION = RUNTIME.parent
CONTROLLER_ROOT = ORCHESTRATION.parent.parent
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

import action_journal as aj  # noqa: E402
import state_format as sf  # noqa: E402
import stop_reasons as sr  # noqa: E402

PROJECT_SETUP = ORCHESTRATION / "PROJECT_SETUP.md"
GATES_POLICY = ORCHESTRATION / "policies" / "GATES.md"
STAGES = ("SPECIFY", "CLARIFY", "PLAN", "TASKS", "IMPLEMENT", "TEST", "REVIEW")
DELIVERABLE_KINDS = ("CODE", "DECISION_DOC", "BOTH")
GUARDIAN_ROLE = "PROJECT_CONTEXT_GUARDIAN"
GATE_NAMES = ("focused_tests", "format", "analyze", "ci")
GATE_ROWS = {"focused_tests": "Focused tests", "format": "Format", "analyze": "Analyze", "ci": "CI"}
BUDGET_NAMES = (
    "stage_transitions", "executor_calls", "corrective_retries", "tdd_slices",
    "investigation_expansions", "review_cycles", "ci_runs",
)
DEFAULT_MAX_PROMPT_BYTES = 48 * 1024
DEFAULT_GATE_TIMEOUT = 300
MAX_SOURCE_LINES = 120
MAX_OUTPUT_TAIL = 600
TICKET_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md")
#: Playbooks the stage worker loads itself (paths and hashes go in the manifest).
STAGE_PLAYBOOKS = {
    "SPECIFY": ("sdd-product-owner",),
    "CLARIFY": ("sdd-product-owner",),
    "PLAN": ("sdd-tech-lead",),
    "TASKS": ("sdd-product-owner", "sdd-tech-lead", "sdd-tdd"),
    "TEST": ("sdd-tdd",),
    "REVIEW": ("sdd-product-owner", "sdd-tech-lead", "sdd-tdd", "sdd-release-readiness"),
}
IMPLEMENT_PLAYBOOK = {"CODE": "sdd-tdd", "DECISION_DOC": "sdd-release-readiness"}
DOC_SLICE = "DOC"
#: Step names printed in ``step``; each maps to a fixed command batch.
STEPS = ("RECOVER", "PREPARE", "DISPATCH", "VALIDATE", "CLASSIFY_INVALID", "ACCEPT", "ROLLOVER", "TRANSITION", "GATES", "STOP")
EMITTED_STOP_REASONS = (
    "IDLE_NO_DEMAND", "STATE_INCONSISTENT", "BASELINE_DRIFT_EXTERNAL", "PREEXISTING_FILE_MODIFIED",
    "EXECUTOR_TIMEOUT", "EXECUTOR_FAILED", "RETRY_BUDGET_REACHED", "EXECUTOR_CALL_BUDGET_REACHED",
    "STAGE_TRANSITION_BUDGET_REACHED", "TDD_SLICE_BUDGET_REACHED", "REVIEW_CYCLE_BUDGET_REACHED",
    "CI_RUN_BUDGET_REACHED", "CONTRACT_INVALID", "WORKER_BLOCKED", "CLARIFICATION_REQUIRED",
    "HUMAN_DECISION_REQUIRED", "MANIFEST_INVALID", "PROMPT_BUDGET_EXCEEDED", "INVESTIGATION_BUDGET_EXCEEDED",
    "SCOPE_CHANGE_REQUIRED", "JEV_GOVERNANCE_REQUIRED", "GATE_COMMAND_UNCONFIGURED", "GATE_CONFIRMATION_REQUIRED",
    "FOCUSED_TESTS_FAILED", "FORMAT_FAILED", "ANALYZE_FAILED", "CI_FAILED", "GATE_TIMEOUT",
    "REVIEW_CHANGES_REQUIRED", "REVIEW_BLOCKED", "OWNERSHIP_VIOLATION", "DONE_GATES_NOT_PASSED",
    "ACTION_RECOVERY_REQUIRED", "DONE",
)
GATE_FAILURE_STOP_REASONS = {
    "focused_tests": "FOCUSED_TESTS_FAILED", "format": "FORMAT_FAILED", "analyze": "ANALYZE_FAILED", "ci": "CI_FAILED",
}


class SddError(Exception):
    def __init__(self, code: str, message: str, *, next_step: str | None = None, next_command: str | None = None, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        described = sr.describe(code) if code in sr.STOP_REASONS else {}
        self.next_step = next_step or described.get("next_step") or "Run `sdd.py next`."
        self.next_command = next_command if next_command is not None else described.get("next_command") or sdd("next")
        self.extra = extra

    def payload(self) -> dict[str, Any]:
        return {"status": self.code, "message": str(self), "next_step": self.next_step, "next_command": self.next_command, **self.extra}


# ----------------------------------------------------------------------------- helpers

def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str | None:
    try:
        return sha256_bytes(path.read_bytes()) if path.is_file() and not path.is_symlink() else None
    except OSError:
        return None


def command(script: Path, *arguments: str) -> str:
    return shlex.join([sys.executable, str(script), *arguments])


def sdd(*arguments: str) -> str:
    return command(Path(__file__).resolve(), *arguments)


def journal_cli(journal: Path, *arguments: str) -> str:
    return command(RUNTIME / "action_journal.py", "--journal", str(journal), "--json", *arguments)


def git(repo: Path, *arguments: str) -> str:
    completed = subprocess.run(["git", "-C", str(repo), *arguments], capture_output=True, text=True, timeout=30, check=False)
    if completed.returncode:
        raise SddError("STATE_INCONSISTENT", f"git {' '.join(arguments)} failed: {completed.stderr.strip()[:200]}")
    return completed.stdout


def dirty_files(repo: Path) -> list[str]:
    raw = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "-z", "--untracked-files=all"],
                         capture_output=True, timeout=30, check=False).stdout.decode("utf-8", "replace")
    entries = [item for item in raw.split("\0") if item]
    files: list[str] = []
    skip = False
    for entry in entries:
        if skip:
            skip = False
            continue
        status, path = entry[:2], entry[3:]
        files.append(path)
        skip = "R" in status or "C" in status
    return sorted(set(files))


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = ""
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def frontmatter_version(path: Path) -> str | None:
    match = re.search(r"^version:\s*([0-9]+\.[0-9]+\.[0-9]+)\s*$", path.read_text(encoding="utf-8"), re.M)
    return match.group(1) if match else None


# ----------------------------------------------------------------------------- project files

def gate_table() -> dict[str, dict[str, Any]]:
    """Read the gate rows of policies/GATES.md: {gate: {command, not_applicable, timeout}}."""
    rows: dict[str, dict[str, Any]] = {}
    try:
        text = GATES_POLICY.read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        for gate, label in GATE_ROWS.items():
            if cells[0] != label or gate in rows:
                continue
            raw = cells[1]
            match = re.match(r"^`([^`]+)`", raw)
            value = match.group(1).strip() if match else raw
            timeout = re.match(r"^(\d+)\s*s", cells[3])
            rows[gate] = {
                "command": None if value.startswith(("UNCONFIGURED", "NOT_APPLICABLE")) else value,
                "unconfigured": value.startswith("UNCONFIGURED"),
                "not_applicable": raw.lstrip("`").startswith("NOT_APPLICABLE"),
                "timeout": int(timeout.group(1)) if timeout else DEFAULT_GATE_TIMEOUT,
            }
    return rows


def ci_enabled() -> bool:
    try:
        block = re.search(r"project_ci_policy:\s*\n\s+enabled:\s*(true|false)", GATES_POLICY.read_text(encoding="utf-8"))
    except OSError:
        return False
    return bool(block and block.group(1) == "true")


def approvers() -> dict[str, str]:
    """PROJECT_SETUP.md `approvers` (json block); every role defaults to the requester."""
    default = {"product_owner": "requester", "tech_lead": "requester"}
    try:
        text = PROJECT_SETUP.read_text(encoding="utf-8")
    except OSError:
        return default
    for block in re.findall(r"^```json\s*\n(.*?)^```", text, re.M | re.S):
        try:
            value = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and isinstance(value.get("approvers"), dict):
            return {**default, **{str(key): str(item) for key, item in value["approvers"].items()}}
    return default


def executor_for(stage: str) -> str:
    import executor_launch

    return executor_launch.stage_settings(executor_launch.load_policy(), stage, executor=None, model=None, timeout=None)["executor"]


# ----------------------------------------------------------------------------- context

class Ctx:
    """Everything one invocation needs, read once: canonical paths, STATE, journal, recovery."""

    def __init__(self, repo: Path | None) -> None:
        try:
            self.paths = aj.canonical_paths(repo)
        except aj.JournalError as exc:
            raise SddError(exc.code, str(exc), next_step=exc.next_step, next_command=exc.next_command or aj.paths_command()) from exc
        self.repo = Path(self.paths["workspace"]).resolve()
        self.storage = self.paths["storage"]
        self.state_path = Path(self.paths["state"])
        self.journal_path = Path(self.paths["journal"])
        self.history_dir = Path(self.paths["history_dir"])
        self.runtime_dir = Path(self.paths["runtime_dir"])
        self.asset_root = CONTROLLER_ROOT if self.storage == "OBSIDIAN" else self.repo
        os.chdir(self.repo)
        self.reload()

    def reload(self) -> None:
        try:
            self.text = self.state_path.read_text(encoding="utf-8")
            self.state = sf.parse(self.text)
        except (OSError, sf.StateFormatError) as exc:
            raise SddError("STATE_INCONSISTENT", f"STATE.md cannot be read: {exc}", next_command=None) from exc
        try:
            self.journal = aj.load_journal(self.journal_path)
        except aj.JournalError as exc:
            raise SddError("ACTION_RECOVERY_REQUIRED", str(exc), next_step=exc.next_step, next_command=None) from exc
        self.recovery = aj.recovery_decision(self.journal, journal_path=self.journal_path)

    # -- STATE accessors
    @property
    def stage(self) -> str:
        return (self.state.get("stage") or {}).get("current") or "IDLE"

    @property
    def delivery(self) -> dict[str, Any]:
        return self.state.get("delivery") or {}

    @property
    def ticket(self) -> str:
        return str((self.state.get("ticket") or {}).get("id") or "IDLE")

    @property
    def profile(self) -> str:
        return self.delivery.get("profile") or "CODE"

    def run_dir(self) -> Path:
        return self.runtime_dir / "runs" / self.ticket

    def pristine(self) -> bool:
        return aj.is_pristine_idle(self.journal)

    def require_pristine(self) -> None:
        if not self.pristine():
            raise SddError("ACTION_RECOVERY_REQUIRED", f"The journal holds action {self.journal['action']['id']} ({self.journal['action']['status']}); STATE may change only through it.",
                           next_command=sdd("next"))

    def write_state(self, data: dict[str, Any], log: str) -> None:
        """Direct STATE write: only while no action is in flight (pristine IDLE journal)."""
        self.require_pristine()
        atomic_write_text(self.state_path, sf.dump(self.text, data, log=f"{now()} {log}"))
        self.reload()


def budget(data: dict[str, Any], name: str) -> tuple[int, int]:
    entry = ((data.get("loop") or {}).get("budgets") or {}).get(name) or {}
    if name == "corrective_retries":
        return int(entry.get("used_current_action", 0)), int(entry.get("max_per_action", 1))
    if name == "investigation_expansions":
        return int(entry.get("used_current_stage", 0)), int(entry.get("max_per_stage", 1))
    return int(entry.get("used", 0)), int(entry.get("max", 0))


def set_budget(data: dict[str, Any], name: str, *, used: int | None = None, maximum: int | None = None) -> None:
    entry = data.setdefault("loop", {}).setdefault("budgets", {}).setdefault(name, {})
    used_key, max_key = {"corrective_retries": ("used_current_action", "max_per_action"),
                         "investigation_expansions": ("used_current_stage", "max_per_stage")}.get(name, ("used", "max"))
    if used is not None:
        entry[used_key] = used
    if maximum is not None:
        entry[max_key] = maximum


# ----------------------------------------------------------------------------- action planning

def action_key(stage: str, role: str | None, slice_id: str | None) -> str:
    if role == GUARDIAN_ROLE:
        return "guardian"
    return f"{stage.lower()}-{slice_id.lower()}" if stage == "IMPLEMENT" and slice_id else stage.lower()


def history_attempts(ctx: Ctx, key: str) -> list[dict[str, Any]]:
    """Archived actions of this ticket for ``key``, oldest first (from the append-only history)."""
    folder = ctx.history_dir / ctx.ticket
    prefix = f"{ctx.ticket}-{key}-"
    found: list[tuple[int, dict[str, Any]]] = []
    if folder.is_dir():
        for path in folder.glob(f"{prefix}*.json"):
            suffix = path.stem[len(prefix):]
            if not suffix.isdigit():
                continue
            try:
                found.append((int(suffix), json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, json.JSONDecodeError):
                continue
    return [value for _, value in sorted(found, key=lambda item: item[0])]


def human_checks_due(ctx: Ctx, stage: str, slice_id: str | None) -> list[str]:
    """HUMAN checks whose recorded decision the next validation requires."""
    acceptance = ctx.delivery.get("acceptance") or {}
    waivers = ctx.delivery.get("waivers") or {}
    slices = ctx.delivery.get("slices") or {}
    if stage == "IMPLEMENT":
        scope = set(slices.get("completed") or []) | ({slice_id} if slice_id else set())
        due = [key for key, check in acceptance.items() if check.get("slice_id") in scope]
    elif stage in {"TEST", "REVIEW"}:
        due = list(acceptance)
    else:
        return []
    return sorted(key for key in due if acceptance[key].get("verifier") == "HUMAN" and key not in waivers)


def current_slice(ctx: Ctx) -> str | None:
    slices = ctx.delivery.get("slices") or {}
    remaining = [item for item in slices.get("planned") or [] if item not in (slices.get("completed") or [])]
    return remaining[0] if remaining else None


def plan_dispatch(ctx: Ctx) -> dict[str, Any]:
    """The single action to prepare for the current stage, or a stop. Pure."""
    stage = ctx.stage
    data = ctx.state
    head = git(ctx.repo, "rev-parse", "HEAD").strip()
    role = None
    slice_id = current_slice(ctx) if stage == "IMPLEMENT" else None
    if stage in {"PLAN", "IMPLEMENT"} and (ctx.delivery.get("project_context") or {}).get("checked_head") != head:
        role = GUARDIAN_ROLE
    for check_stage in ((stage,) if role is None else ()):
        due = human_checks_due(ctx, check_stage, slice_id)
        if due:
            return stop("HUMAN_DECISION_REQUIRED", checks=due,
                        next_command=sdd("waive", "--check", due[0], "--by", "requester", "--quote", "<user words>", "--reason", "<why>"))
    key = action_key(stage, role, slice_id)
    attempts = history_attempts(ctx, key)
    released = [index for index, item in enumerate(attempts) if item["action"]["status"] == "RELEASED"]
    series = attempts[released[-1] + 1:] if released else attempts
    n = (int(attempts[-1]["action"]["id"].rsplit("-", 1)[-1]) if attempts else 0) + 1
    parent = series[-1] if series and series[-1]["action"]["status"] in {"INTERRUPTED", "BLOCKED"} else None
    retries_used = len([item for item in series if item["action"]["status"] in {"INTERRUPTED", "BLOCKED"}])
    used, maximum = budget(data, "corrective_retries")
    if parent is not None and retries_used > maximum:
        exit_code = parent["process"]["exit_code"]
        code = "EXECUTOR_TIMEOUT" if exit_code == aj_timeout() else ("RETRY_BUDGET_REACHED" if parent["action"]["status"] == "BLOCKED" else "EXECUTOR_FAILED")
        return stop(code, parent_action_id=parent["action"]["id"], retries_used=retries_used)
    calls_used, calls_max = budget(data, "executor_calls")
    if calls_used >= calls_max:
        return stop("EXECUTOR_CALL_BUDGET_REACHED")
    if stage == "IMPLEMENT" and role is None:
        slices_used, slices_max = budget(data, "tdd_slices")
        if slices_used >= slices_max:
            return stop("TDD_SLICE_BUDGET_REACHED")
    if stage == "REVIEW":
        cycles_used, cycles_max = budget(data, "review_cycles")
        if cycles_used >= cycles_max:
            return stop("REVIEW_CYCLE_BUDGET_REACHED")
    action_stage = stage
    action_id = f"{ctx.ticket}-{key}-{n:02d}"
    run = ctx.run_dir()
    reduced = parent is not None and parent["process"]["exit_code"] == aj_timeout()
    return {
        "status": "PREPARE", "stage": action_stage, "role": role, "slice_id": slice_id, "key": key,
        "action_id": action_id, "attempt": 1 + retries_used, "reduced": reduced,
        "parent": None if parent is None else {
            "action_id": parent["action"]["id"], "status": parent["action"]["status"],
            "exit_code": parent["process"]["exit_code"],
            "artifact_path": parent["action"]["final_message_path"] if parent["artifact"]["sha256"] else None,
            "artifact_sha256": parent["artifact"]["sha256"],
            "errors_file": str(run / f"{parent['action']['id']}.errors.json"),
        },
        "manifest": str(run / f"{action_id}.context.json"),
        "prompt": str(run / f"{action_id}.prompt.md"),
        "final": str(run / f"{action_id}.result.json"),
        "verifier": str(run / f"{action_id}.verifier.json"),
        "head": head,
    }


def aj_timeout() -> int:
    return 124


def stop(code: str, *, next_command: str | None = None, **extra: Any) -> dict[str, Any]:
    described = sr.describe(code)
    if next_command is not None:
        described["next_command"] = next_command
    return {"status": "STOP", **described, **extra}


# ----------------------------------------------------------------------------- manifest

def playbook_entry(ctx: Ctx, name: str, reason: str) -> dict[str, Any] | None:
    path = ctx.asset_root / ".hermes" / "skills" / name / "SKILL.md"
    digest = sha256_file(path)
    version = frontmatter_version(path) if digest else None
    if not digest or not version:
        return None
    return {"name": name, "version": version, "path": f".hermes/skills/{name}/SKILL.md", "sha256": digest, "references": [], "reason": reason}


def slice_block(ctx: Ctx, data: dict[str, Any], stage: str, slice_id: str | None) -> dict[str, Any] | None:
    if stage not in {"IMPLEMENT", "TEST", "REVIEW"}:
        return None
    delivery = data.get("delivery") or {}
    slices = delivery.get("slices") or {}
    acceptance = copy.deepcopy(delivery.get("acceptance") or {})
    focused = (gate_table().get("focused_tests") or {}).get("command")
    agent_checks = sorted(key for key, check in acceptance.items() if check.get("verifier") == "AGENT")
    verification = []
    if agent_checks:
        if not focused:
            raise SddError("GATE_COMMAND_UNCONFIGURED", "Focused tests have no verified command in policies/GATES.md; AGENT checks need it as their verifier.")
        verification.append({"id": "focused-tests", "kind": "TEST", "command": focused, "check_ids": agent_checks, "introduced_by_slice": False})
    for key in sorted(key for key, check in acceptance.items() if check.get("verifier") == "HUMAN"):
        verification.append({"id": f"human-{key.lower()}", "kind": "HUMAN", "command": None, "check_ids": [key], "introduced_by_slice": False})
    planned = list(slices.get("planned") or [])
    known = sorted({check["slice_id"] for check in acceptance.values() if check.get("slice_id")})
    playbook = IMPLEMENT_PLAYBOOK[delivery.get("profile") or "CODE"]
    return {
        "current_slice_ids": [slice_id] if stage == "IMPLEMENT" and slice_id else [],
        "completed_slice_ids": [item for item in slices.get("completed") or [] if item != slice_id and item in known],
        "editable_paths": sorted(slices.get("editable_paths") or []) if stage == "IMPLEMENT" else [],
        "required_playbooks": [{"name": playbook, "slice_ids": known}] if planned and known and playbook_entry(ctx, playbook, "-") else [],
        "acceptance": acceptance,
        "required_verification": verification,
    }


def build_manifest(ctx: Ctx, data: dict[str, Any], stage: str, *, role: str | None = None, slice_id: str | None = None, head: str | None = None) -> dict[str, Any]:
    """Complete stage-context manifest (schema 2) from STATE: hashes computed, nothing to hand-fill."""
    delivery = data.get("delivery") or {}
    profile = delivery.get("profile") or "CODE"
    head = head or git(ctx.repo, "rev-parse", "HEAD").strip()
    sources: list[dict[str, Any]] = []
    candidates = [(name, "RULES") for name in INSTRUCTION_FILES]
    if stage in {"PLAN", "TASKS", "IMPLEMENT", "TEST", "REVIEW"}:
        candidates += [(path, "CODE") for path in (delivery.get("slices") or {}).get("editable_paths") or []]
    for relative, kind in candidates:
        path = ctx.repo / relative
        digest = sha256_file(path)
        if digest is None or len(sources) >= 12:
            continue
        try:
            count = max(1, len(path.read_text(encoding="utf-8").splitlines()))
        except (OSError, UnicodeDecodeError):
            continue
        sources.append({"kind": kind, "path": relative, "lines": [1, min(count, MAX_SOURCE_LINES)], "sha256": digest})
    if stage == "IMPLEMENT":
        names = [IMPLEMENT_PLAYBOOK[profile]] if role is None else []
    else:
        names = list(STAGE_PLAYBOOKS.get(stage, ()))
        if stage == "PLAN" and profile == "DECISION_DOC":
            names.append("sdd-architecture-decisions")
    playbooks = [entry for entry in (playbook_entry(ctx, name, f"{stage} brief playbook") for name in names) if entry]
    if stage == "IMPLEMENT" and role is None and playbooks:
        playbooks[0]["reason"] = "required by the slice contract"
    project = copy.deepcopy(delivery.get("project_context") or {})
    status = project.get("status") if project.get("checked_head") == head else "MISSING"
    manifest: dict[str, Any] = {
        "schema_version": 2,
        "project_root": str(ctx.repo),
        "ticket": str((data.get("ticket") or {}).get("id")),
        "stage": stage,
        "limits": {"max_sources": 12, "max_lines_per_source": 250, "max_prompt_bytes": DEFAULT_MAX_PROMPT_BYTES},
        "project_context": {
            "status": status or "MISSING",
            "checked_head": project.get("checked_head") if status != "MISSING" else None,
            "obsidian": "BOUND" if ctx.storage == "OBSIDIAN" else "NOT_CONFIGURED",
            "evidence": project.get("evidence") if status != "MISSING" else None,
            "gaps": list(project.get("gaps") or []) if status != "MISSING" else [],
        },
        "sources": sources,
        "playbooks": playbooks,
        "divergences": [],
        "slice": slice_block(ctx, data, stage, slice_id) if role is None else None,
        "approval": None,
    }
    approved = delivery.get("approved_slice_sha256s") or []
    if stage == "IMPLEMENT" and role is None and approved:
        manifest["approval"] = {"approved_slice_sha256s": list(approved), "evidence": (delivery.get("authorization") or {}).get("quote") or "demand request"}
    if role == "PROJECT_CONTEXT_GUARDIAN":
        manifest["dispatch"] = {
            "pending_decision": f"whether the stored project context is current enough to {stage} without re-reading the repository",
            "deterministic_attempt": "STATE project_context status and checked_head compared with git rev-parse HEAD",
            "if_empty": "NO_FINDINGS keeps the stored context and the stage proceeds; findings refresh the context first",
        }
    governance = delivery.get("semantic_governance")
    if governance:
        manifest["semantic_governance"] = governance
    return manifest


def check_manifest(manifest: dict[str, Any], role: str | None) -> dict[str, Any] | None:
    """Run stage_context.check in-process; return a stop payload or None."""
    import stage_context

    errors = stage_context.check(manifest, role=role)["errors"]
    if not errors:
        return None
    codes = {item["code"] for item in errors}
    if codes & {"CONTEXT_BUDGET_EXCEEDED", "CONTEXT_EXCERPT_TOO_LARGE"}:
        code = "INVESTIGATION_BUDGET_EXCEEDED"
    elif any(item.startswith("JEV_GOVERNANCE") for item in codes):
        code = "JEV_GOVERNANCE_REQUIRED"
    elif "SCOPE_CHANGE_REQUIRED" in codes:
        code = "SCOPE_CHANGE_REQUIRED"
    else:
        code = "MANIFEST_INVALID"
    return stop(code, findings=errors[:5])


# ----------------------------------------------------------------------------- prompt

def _excerpt(repo: Path, source: dict[str, Any]) -> str:
    start, end = source["lines"]
    lines = (repo / source["path"]).read_text(encoding="utf-8").splitlines()[start - 1:end]
    return "\n".join(f"{start + index}: {line}" for index, line in enumerate(lines))


def controller_data(ctx: Ctx, plan: dict[str, Any], manifest: dict[str, Any], *, brief: bool) -> dict[str, Any]:
    delivery = ctx.delivery
    stage, role = plan["stage"], plan["role"]
    acceptance = delivery.get("acceptance") or {}
    waivers = delivery.get("waivers") or {}
    data: dict[str, Any] = {
        "ticket": ctx.ticket, "stage": stage, "role": role, "profile": ctx.profile,
        "deliverable_kind": delivery.get("deliverable_kind"),
        "request": {"title": (ctx.state.get("ticket") or {}).get("title"), "objective": (ctx.state.get("ticket") or {}).get("objective"),
                    "quote": (delivery.get("authorization") or {}).get("quote")},
        "executor": executor_for(stage).upper(),
        "acceptance": [{"id": key, **check} for key, check in sorted(acceptance.items())],
        "recorded_waivers": {key: value for key, value in waivers.items() if key in acceptance},
        "answers": delivery.get("answers") or [],
        "protected_preexisting": (ctx.state.get("baseline") or {}).get("protected_preexisting") or [],
    }
    if manifest.get("slice"):
        import stage_context

        verifier = stage_context.verifier_context(manifest, role=role) if role is None else {}
        data["slice"] = {key: verifier.get(key) for key in ("current_slice_ids", "completed_slice_ids", "editable_paths", "required_commands", "check_verifiers") if key in verifier}
    if stage == "REVIEW":
        data["gates"] = {name: (ctx.state.get("gates") or {}).get(name, {}).get("status", "PENDING") for name in GATE_NAMES}
        data["agent_owned"] = (ctx.state.get("ownership") or {}).get("agent_owned") or []
    previous = delivery.get("summaries") or {}
    data["previous"] = {name: ({"summary": item.get("summary")} if brief else item) for name, item in previous.items()}
    if plan["parent"]:
        retry: dict[str, Any] = {"parent_action_id": plan["parent"]["action_id"],
                                 "reason": "timeout" if plan["parent"]["exit_code"] == aj_timeout() else plan["parent"]["status"].lower()}
        errors_file = Path(plan["parent"]["errors_file"])
        if errors_file.is_file():
            retry["errors"] = json.loads(errors_file.read_text(encoding="utf-8"))[:10]
        data["retry"] = retry
    return data


def build_prompt(ctx: Ctx, plan: dict[str, Any], manifest: dict[str, Any]) -> str:
    stage, role = plan["stage"], plan["role"]
    limit = int(manifest["limits"].get("max_prompt_bytes") or DEFAULT_MAX_PROMPT_BYTES)
    if plan["reduced"]:
        limit //= 2
    brief_path = ORCHESTRATION / ("sub-agents/project-context-guardian.md" if role == GUARDIAN_ROLE else f"agents/{stage.lower()}.md")
    envelope = "review_result" if stage == "REVIEW" else "executor_result"
    playbooks = [str(ctx.asset_root / item["path"]) for item in manifest["playbooks"]]

    def render(with_excerpts: bool, brief: bool) -> str:
        parts = [
            f"# SDD worker: {role or stage} for ticket {ctx.ticket}",
            f"Read your brief first: {brief_path}",
            ("Load these playbooks yourself: " + ", ".join(playbooks)) if playbooks else "No playbook is required.",
            (f"Return exactly one JSON object `{{\"{envelope}\": {{...}}}}` matching the schema the CLI enforces "
             f"(schema_version 3{'' if stage == 'REVIEW' else f', stage.value {stage}'}). Copy every acceptance check below with its id, "
             "criterion, verification_method, verifier and slice_id unchanged; change only status, evidence and waiver. A check in "
             "recorded_waivers is WAIVED with exactly that waiver object and evidence quoting it. Never write STATE or the journal, "
             "never commit, push or call external systems."),
            "## Controller data (authoritative)",
            "```json\n" + json.dumps(controller_data(ctx, plan, manifest, brief=brief), ensure_ascii=False, indent=1) + "\n```",
            "## Sources",
        ]
        for source in manifest["sources"]:
            parts.append(f"### {source['path']} lines {source['lines'][0]}-{source['lines'][1]}")
            if with_excerpts:
                parts.append(_excerpt(ctx.repo, source))
        return "\n\n".join(parts) + "\n"

    for with_excerpts, brief in ((not plan["reduced"], plan["reduced"]), (False, False), (False, True)):
        text = render(with_excerpts, brief)
        if len(text.encode("utf-8")) <= limit:
            return text
    raise SddError("PROMPT_BUDGET_EXCEEDED", f"The {stage} prompt needs {len(text.encode('utf-8'))} bytes even without excerpts; max_prompt_bytes allows {limit}.")


# ----------------------------------------------------------------------------- validation

def verifier_context(ctx: Ctx, manifest: dict[str, Any], role: str | None) -> dict[str, Any]:
    import stage_context

    context = stage_context.verifier_context(manifest, role=role)
    waivers = {key: value for key, value in (ctx.delivery.get("waivers") or {}).items() if key in (ctx.delivery.get("acceptance") or {})}
    if waivers:
        context["recorded_waivers"] = waivers
    return context


def validate_result(ctx: Ctx, stage: str, role: str | None, manifest: dict[str, Any], final: Path) -> list[dict[str, str]]:
    import validate_protocol

    context = verifier_context(ctx, manifest, role)
    as_set = lambda key: None if context.get(key) is None else set(context[key])  # noqa: E731
    return validate_protocol.validate_json_text(
        stage, final.read_text(encoding="utf-8"),
        expected_acceptance=context.get("expected_acceptance"), current_slice_ids=as_set("current_slice_ids"),
        completed_slice_ids=as_set("completed_slice_ids"), editable_paths=as_set("editable_paths"),
        required_commands=context.get("required_commands"), check_verifiers=context.get("check_verifiers"),
        role=context.get("role"), recorded_waivers=context.get("recorded_waivers"),
    )


def journal_action(ctx: Ctx) -> dict[str, Any]:
    action = ctx.journal["action"]
    run = ctx.run_dir()
    role = action["name"] if action["name"] not in STAGES else None
    return {"id": action["id"], "stage": action["stage"], "role": role, "final": Path(action["final_message_path"]),
            "manifest": run / f"{action['id']}.context.json", "verifier": run / f"{action['id']}.verifier.json",
            "prompt": run / f"{action['id']}.prompt.md", "errors": run / f"{action['id']}.errors.json"}


# ----------------------------------------------------------------------------- next

def step(name: str, commands: list[str], next_step: str, **extra: Any) -> dict[str, Any]:
    return {"status": "OK", "step": name, "end_turn": False, "commands": commands, "next_step": next_step,
            "next_command": sdd("next"), **extra}


def stopped(ctx: Ctx | None, payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(payload)
    payload.setdefault("status", "STOP")
    payload["end_turn"] = True
    payload["commands"] = []
    if ctx is not None:
        payload.setdefault("stage", ctx.stage)
    return payload


def baseline_stop(ctx: Ctx) -> dict[str, Any] | None:
    baseline = ctx.state.get("baseline") or {}
    if not baseline.get("captured"):
        return None
    head = git(ctx.repo, "rev-parse", "HEAD").strip()
    branch = git(ctx.repo, "branch", "--show-current").strip()
    if head != baseline.get("head") or branch != baseline.get("branch"):
        return stop("BASELINE_DRIFT_EXTERNAL", expected={"head": baseline.get("head"), "branch": baseline.get("branch")}, actual={"head": head, "branch": branch})
    for path, digest in sorted((baseline.get("protected_sha256") or {}).items()):
        if sha256_file(ctx.repo / path) != digest:
            return stop("PREEXISTING_FILE_MODIFIED", path=path)
    return None


def recovery_step(ctx: Ctx) -> dict[str, Any] | None:
    """Map the journal's recover decision to one step; None when dispatch is allowed with a pristine journal."""
    decision = ctx.recovery
    status = ctx.journal["action"]["status"]
    if decision["decision"] == "DISPATCH_ALLOWED" and ctx.pristine():
        return None
    if decision["decision"] == "DISPATCH_ALLOWED" and status == "PREPARED":
        action = journal_action(ctx)
        arguments = ["run", "--stage", action["stage"], "--journal", str(ctx.journal_path), "--prompt-file", str(action["prompt"]), "--final", str(action["final"])]
        if action["role"]:
            arguments += ["--role", action["role"]]
        if ctx.storage == "OBSIDIAN":
            arguments += ["--add-dir", str(CONTROLLER_ROOT)]
        size_check = ["check", "--context", str(action["manifest"]), "--prompt-file", str(action["prompt"]), "--json"]
        if action["role"]:
            size_check += ["--role", action["role"]]
        return step("DISPATCH", [command(RUNTIME / "stage_context.py", *size_check), command(RUNTIME / "executor_launch.py", *arguments)],
                    "Check the prompt against max_prompt_bytes, run the worker in the foreground (the launcher records start and finish in the journal), then `sdd.py next`.",
                    action_id=action["id"])
    if decision["decision"] == "RELEASED":
        return step("ROLLOVER", [journal_cli(ctx.journal_path, "rollover", "--history-dir", str(ctx.history_dir))],
                    "Archive the released action and open a pristine journal, then `sdd.py next`.")
    if decision["decision"] == "ARCHIVE_INTERRUPTED_REQUIRED":
        return step("RECOVER", [decision["next_command"]],
                    "Archive the interrupted action; `sdd.py next` then prepares its single retry (timeout: reduced context).",
                    exit_code=ctx.journal["process"]["exit_code"])
    if decision["decision"] == "CORRECTIVE_RETRY_AVAILABLE" or (decision["decision"] == "BLOCKED" and decision.get("stop_reason") == "RETRY_BUDGET_REACHED"):
        # Archive first in both cases: the retry budget is then decided from history by
        # plan_dispatch (RETRY_BUDGET_REACHED stop), with a pristine journal so the user's
        # `sdd.py budget --raise` can be recorded.
        return step("RECOVER", [decision["next_command"]], "Archive the invalid result; `sdd.py next` then prepares the corrective retry with its errors, or stops when the retry budget is spent.")
    if status == "ARTIFACT_READY" or decision["decision"] in {"STATE_COMMIT_REQUIRED", "ALREADY_COMMITTED"} or status == "VALIDATED" or status == "STATE_COMMITTED":
        if status == "ARTIFACT_READY":
            return validation_step(ctx)
        return step("ACCEPT", [sdd("accept")], "Finish the STATE commit of the validated result, then `sdd.py next`.")
    if decision["decision"] == "RECONCILE_ARTIFACT" and decision["next_command"] and "<" not in decision["next_command"]:
        return step("RECOVER", [decision["next_command"]], decision["next_step"])
    code = decision.get("stop_reason") or "ACTION_RECOVERY_REQUIRED"
    payload = stop(code if code in sr.STOP_REASONS else "ACTION_RECOVERY_REQUIRED", next_command=decision["next_command"],
                   recovery=decision["decision"], reason=decision["reason"])
    payload["next_step"] = decision["next_step"]
    return stopped(ctx, payload)


def validation_step(ctx: Ctx) -> dict[str, Any]:
    action = journal_action(ctx)
    manifest = json.loads(action["manifest"].read_text(encoding="utf-8"))
    errors = validate_result(ctx, action["stage"], action["role"], manifest, action["final"])
    if errors:
        fields = sorted({item["path"] for item in errors})[:8]
        return step("CLASSIFY_INVALID", [sdd("reject")],
                    "The result is invalid; record the errors and classify it so the single corrective retry gets them, then `sdd.py next`.",
                    errors=errors[:5], invalid_fields=fields)
    verifier = ["verifier-context", "--context", str(action["manifest"]), "--state", str(ctx.state_path), "--output", str(action["verifier"]), "--json"]
    if action["role"]:
        verifier += ["--role", action["role"]]
    return step("VALIDATE", [
        command(RUNTIME / "stage_context.py", *verifier),
        command(RUNTIME / "validate_protocol.py", "--action", action["stage"], "--result", str(action["final"]), "--context", str(action["verifier"]), "--json"),
        sdd("accept"),
    ], "Validate the result against its contract and commit it to STATE through the journal, then `sdd.py next`.", action_id=action["id"])


def stage_complete(ctx: Ctx) -> bool:
    stage = ctx.stage
    accepted = ctx.delivery.get("accepted") or {}
    if stage == "IMPLEMENT":
        return current_slice(ctx) is None and bool((ctx.delivery.get("slices") or {}).get("planned"))
    return stage.lower() in accepted


def gates_step(ctx: Ctx, names: tuple[str, ...]) -> dict[str, Any] | None:
    table = gate_table()
    gates = ctx.state.get("gates") or {}
    pending = [name for name in names if (gates.get(name) or {}).get("status") not in {"PASS", "DISABLED_BY_PROJECT_POLICY"}]
    if not pending:
        return None
    commands = []
    for name in pending:
        status = (gates.get(name) or {}).get("status")
        row = table.get(name) or {"unconfigured": True}
        if status in {"FAIL", "TIMEOUT"}:
            return stopped(ctx, stop("GATE_TIMEOUT" if status == "TIMEOUT" else GATE_FAILURE_STOP_REASONS[name], gate=name))
        if row.get("not_applicable"):
            return stopped(ctx, stop("GATE_CONFIRMATION_REQUIRED", gate=name,
                                     next_command=sdd("gate", "--name", name, "--not-applicable", "--by", "requester", "--quote", "<user words>")))
        if row.get("unconfigured") or not row.get("command"):
            return stopped(ctx, stop("GATE_COMMAND_UNCONFIGURED", gate=name, next_command=None))
        commands.append(sdd("gate", "--name", name))
    return step("GATES", commands, "Run the gates in order (each records its result in STATE and the wiki), then `sdd.py next`.")


def done_gate_failure(ctx: Ctx) -> str | None:
    gates = ctx.state.get("gates") or {}
    for name in ("focused_tests", "format", "analyze"):
        if (gates.get(name) or {}).get("status") != "PASS":
            return name
    if (gates.get("review") or {}).get("status") != "APPROVED":
        return "review"
    expected = "PASS" if ci_enabled() else "DISABLED_BY_PROJECT_POLICY"
    if (gates.get("ci") or {}).get("status") != expected:
        return "ci"
    return None


def transition_step(ctx: Ctx) -> dict[str, Any]:
    stage = ctx.stage
    target = sf.next_stage(ctx.profile, stage)
    arguments = ["transition", "--to", target]
    accepted = (ctx.delivery.get("accepted") or {}).get(stage.lower() if stage != "IMPLEMENT" else f"implement-{(ctx.delivery.get('slices') or {}).get('planned', ['?'])[-1].lower()}")
    if accepted:
        arguments += ["--artifact", accepted["artifact"]]
    if target == "CLARIFY" and not (ctx.delivery.get("open_questions") or []):
        target = sf.next_stage(ctx.profile, "CLARIFY")
        arguments[2] = target
        arguments += ["--skip-reason", "SPECIFY reported no material unresolved question"]
    used, maximum = budget(ctx.state, "stage_transitions")
    if used >= maximum:
        return stopped(ctx, stop("STAGE_TRANSITION_BUDGET_REACHED"))
    if target == "DONE":
        missing = done_gate_failure(ctx)
        if missing:
            return stopped(ctx, stop("DONE_GATES_NOT_PASSED", gate=missing))
    return step("TRANSITION", [sdd(*arguments)], f"Move STATE from {stage} to {target} with provenance, then `sdd.py next`.", target=target)


def decide_next(ctx: Ctx) -> dict[str, Any]:
    if ctx.stage == "IDLE":
        if not ctx.pristine():
            return recovery_step(ctx) or stopped(ctx, stop("ACTION_RECOVERY_REQUIRED"))
        return stopped(ctx, stop("IDLE_NO_DEMAND"))
    recovered = recovery_step(ctx)
    if recovered is not None:
        return recovered
    if ctx.stage == "DONE":
        return stopped(ctx, stop("DONE"))
    drift = baseline_stop(ctx)
    if drift:
        return stopped(ctx, drift)
    blockers = ctx.delivery.get("worker_blockers") or []
    if blockers:
        return stopped(ctx, stop("WORKER_BLOCKED", blockers=blockers[:5], next_command=sdd("unblock", "--quote", "<user words resolving the blocker>")))
    open_questions = [item for item in ctx.delivery.get("open_questions") or [] if not item.get("answer")]
    if ctx.stage == "CLARIFY" and open_questions:
        first = open_questions[0]
        return stopped(ctx, stop("CLARIFICATION_REQUIRED", questions=[item["question"] for item in open_questions],
                                 next_command=sdd("answer", "--index", str(first["index"]), "--quote", "<user words>")))
    gates = ctx.state.get("gates") or {}
    review = (gates.get("review") or {}).get("status")
    if ctx.stage == "REVIEW" and review in {"CHANGES_REQUIRED", "BLOCKED"}:
        return stopped(ctx, stop("REVIEW_CHANGES_REQUIRED" if review == "CHANGES_REQUIRED" else "REVIEW_BLOCKED",
                                 next_command=sdd("transition", "--to", "IMPLEMENT", "--reason", "<review findings>", "--quote", "<user words>")))
    if stage_complete(ctx):
        gate_stage = "TEST" if ctx.profile == "CODE" else "IMPLEMENT"
        if ctx.stage == gate_stage:
            pending = gates_step(ctx, ("focused_tests", "format", "analyze"))
            if pending:
                return pending
        if ctx.stage == "REVIEW" and ci_enabled():
            used, maximum = budget(ctx.state, "ci_runs")
            if (gates.get("ci") or {}).get("status") != "PASS" and used >= maximum:
                return stopped(ctx, stop("CI_RUN_BUDGET_REACHED"))
            pending = gates_step(ctx, ("ci",))
            if pending:
                return pending
        return transition_step(ctx)
    acceptance = ctx.delivery.get("acceptance") or {}
    if ctx.stage in {"TASKS", "IMPLEMENT", "TEST", "REVIEW"} and any(check.get("verifier") == "AGENT" for check in acceptance.values()) \
            and not (gate_table().get("focused_tests") or {}).get("command"):
        return stopped(ctx, stop("GATE_COMMAND_UNCONFIGURED", gate="focused_tests", next_command=None))
    plan = plan_dispatch(ctx)
    if plan["status"] == "STOP":
        return stopped(ctx, plan)
    try:
        manifest = build_manifest(ctx, ctx.state, plan["stage"], role=plan["role"], slice_id=plan["slice_id"], head=plan["head"])
    except SddError as exc:
        return stopped(ctx, stop(exc.code, next_command=exc.next_command))
    refused = check_manifest(manifest, plan["role"])
    if refused:
        return stopped(ctx, refused)
    try:
        build_prompt(ctx, plan, manifest)
    except SddError as exc:
        return stopped(ctx, stop(exc.code, detail=str(exc)))
    manifest_cmd = ["manifest", "--stage", plan["stage"], "--output", plan["manifest"]]
    check_arguments = ["check", "--context", plan["manifest"], "--json"]
    if plan["role"]:
        manifest_cmd += ["--role", plan["role"]]
        check_arguments += ["--role", plan["role"]]
    check_cmd = command(RUNTIME / "stage_context.py", *check_arguments)
    prepare_cmd = ["prepare", "--stage", plan["stage"], "--manifest", plan["manifest"]]
    if plan["role"]:
        prepare_cmd += ["--role", plan["role"]]
    label = plan["role"] or (f"{plan['stage']} slice {plan['slice_id']}" if plan["slice_id"] else plan["stage"])
    note = f" Retry {plan['attempt']} of {plan['parent']['action_id']}" + (" with reduced context." if plan["reduced"] else ".") if plan["parent"] else ""
    return step("PREPARE", [sdd(*manifest_cmd), check_cmd, sdd(*prepare_cmd)],
                f"Build and check the {label} manifest, then prepare the journaled action and its prompt.{note}",
                action_id=plan["action_id"])


# ----------------------------------------------------------------------------- commands

def cmd_status(ctx: Ctx, _args: argparse.Namespace) -> dict[str, Any]:
    decision = decide_next(ctx)
    budgets = {}
    for name in BUDGET_NAMES:
        used, maximum = budget(ctx.state, name)
        budgets[name] = maximum - used
    return {
        "status": "OK", "storage": ctx.storage, "ticket": ctx.ticket, "stage": ctx.stage,
        "stage_status": (ctx.state.get("stage") or {}).get("status"), "profile": ctx.profile if ctx.stage != "IDLE" else None,
        "mode": (ctx.state.get("loop") or {}).get("mode"),
        "paths": {key: ctx.paths[key] for key in ("state", "journal", "history_dir", "runtime_dir")},
        "recovery": {"decision": ctx.recovery["decision"], "journal_status": ctx.journal["action"]["status"]},
        "budgets_left": budgets,
        "next_action": decision.get("step") or decision.get("stop_reason"),
        "stop": None if not decision.get("end_turn") else {key: decision.get(key) for key in ("stop_reason", "kind", "next_step")},
        "next_step": "Run `sdd.py next` and execute exactly the printed commands.", "next_command": sdd("next"),
    }


def cmd_next(ctx: Ctx, _args: argparse.Namespace) -> dict[str, Any]:
    result = decide_next(ctx)
    result.setdefault("stage", ctx.stage)
    return result


def cmd_start(ctx: Ctx, args: argparse.Namespace) -> dict[str, Any]:
    if ctx.stage != "IDLE":
        raise SddError("DEMAND_ACTIVE", f"Ticket {ctx.ticket} is active at {ctx.stage}; finish or resume it first.", next_command=sdd("next"))
    if not ctx.pristine():
        raise SddError("ACTION_RECOVERY_REQUIRED", "The journal is not pristine; recover it before starting a demand.", next_command=sdd("next"))
    if not TICKET_PATTERN.match(args.ticket):
        raise SddError("TICKET_INVALID", "Ticket ids are 1-64 letters, digits, '.', '_' or '-' (one path component).",
                       next_command=sdd("start", "--ticket", "<ticket-id>", "--title", args.title, "--objective", args.objective))
    data = copy.deepcopy(ctx.state)
    head = git(ctx.repo, "rev-parse", "HEAD").strip()
    branch = git(ctx.repo, "branch", "--show-current").strip()
    protected = dirty_files(ctx.repo)
    previous = data.get("repository") or {}
    data["repository"] = {**previous, "branch": branch, "head": head}
    data["baseline"] = {"captured": True, "captured_at": now(), "head": head, "branch": branch, "protected_preexisting": protected,
                        "protected_sha256": {path: digest for path in protected if (digest := sha256_file(ctx.repo / path))}}
    data.setdefault("ownership", {})["protected_preexisting"] = protected
    data["ownership"]["agent_owned"] = []
    profile = "DECISION_DOC" if args.deliverable_kind == "DECISION_DOC" else "CODE"
    path = sf.profile_path(profile)
    data["ticket"] = {"id": args.ticket, "title": args.title, "objective": args.objective, "scope_confirmed": []}
    limits = {"stage_transitions": len(path) - 1, "executor_calls": args.executor_calls, "tdd_slices": args.tdd_slices,
              "review_cycles": 2, "ci_runs": 1}
    loop = data.setdefault("loop", {})
    loop["budgets"] = {
        **{name: {"max": limits[name], "used": 0} for name in limits},
        "corrective_retries": {"max_per_action": 1, "used_current_action": 0},
        "investigation_expansions": {"max_per_stage": 1, "used_current_stage": 0},
        "external_mutations": {"max": 0, "used": 0},
    }
    loop.setdefault("control", {})["stop_reason"] = "NONE"
    loop["mode"] = loop.get("mode") or "MANUAL"
    data["gates"] = {name: {"status": "PENDING"} for name in ("focused_tests", "format", "analyze", "review", "ci")}
    data["blockers"] = []
    quote = args.request or args.objective
    data["delivery"] = {
        "profile": profile, "deliverable_kind": args.deliverable_kind, "started_at": now(),
        "authorization": {"quote": quote, "limits": limits, "scope": "LOCAL_DELIVERY up to the next HUMAN checkpoint"},
        "acceptance": {}, "open_questions": [], "answers": [], "waivers": {}, "accepted": {}, "summaries": {},
        "slices": {"planned": [], "completed": [], "editable_paths": []}, "approved_slice_sha256s": [],
        "project_context": None, "worker_blockers": [],
    }
    data["stage"] = {"current": "IDLE", "status": "WAITING", "completed": [], "skipped": []}
    data = sf.apply_transition(data, "SPECIFY", profile=profile)
    data["resume"] = {"last_gate": None, "next_action": "SPECIFY", "next_command": sdd("next")}
    drift = previous.get("branch") not in (None, branch) or previous.get("head") not in (None, head)
    ctx.write_state(data, f"Demand {args.ticket} started ({profile}); baseline captured at {branch}@{head[:12]}"
                    + (" (IDLE + pristine journal: new baseline, not drift)" if drift else "")
                    + (f"; protected pre-existing: {', '.join(protected)}" if protected else ""))
    record_wiki(ctx, "stage", f"{args.ticket} started", f"Request: {quote}\n\nProfile: {profile} ({' -> '.join(path)})\nLimits: {json.dumps(limits)}", stage="SPECIFY")
    return {"status": "STARTED", "ticket": args.ticket, "profile": profile, "stages": list(path), "limits": limits,
            "protected_preexisting": protected,
            "authorization": "This request authorizes progress up to the next HUMAN checkpoint within these limits; show this preview once, then run the loop.",
            "next_step": "Run `sdd.py next` and execute exactly the printed commands until it ends the turn.", "next_command": sdd("next")}


def cmd_manifest(ctx: Ctx, args: argparse.Namespace) -> dict[str, Any]:
    slice_id = current_slice(ctx) if args.stage == "IMPLEMENT" else None
    manifest = build_manifest(ctx, ctx.state, args.stage, role=args.role, slice_id=slice_id)
    if not args.output:
        return {"status": "OK", "manifest": manifest, "next_step": "Pass it to stage_context.py check.", "next_command": sdd("next")}
    output = Path(args.output)
    atomic_write_text(output, json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    return {"status": "WRITTEN", "path": str(output), "sources": len(manifest["sources"]), "playbooks": [item["name"] for item in manifest["playbooks"]],
            "next_step": "Check it with stage_context.py.", "next_command": command(RUNTIME / "stage_context.py", "check", "--context", str(output), "--json")}


def cmd_snapshot(ctx: Ctx, args: argparse.Namespace) -> dict[str, Any]:
    import bounded_run_planner as planner

    if ctx.stage == "IDLE":
        raise SddError("IDLE_NO_DEMAND", "STATE is IDLE: there is nothing to plan.")
    workspace = ctx.journal["workspace"]
    gates = ctx.state.get("gates") or {}
    table = gate_table()

    def gate_value(name: str) -> str:
        entry = gates.get(name) or {}
        if entry.get("not_applicable") and entry.get("status") == "PASS" and (table.get(name) or {}).get("not_applicable"):
            return "NOT_APPLICABLE"
        return entry.get("status") or "PENDING"

    slices = ctx.delivery.get("slices") or {}
    loop = ctx.state.get("loop") or {}
    budgets = {}
    for name in planner.BUDGET_KEYS:
        used, maximum = budget(ctx.state, name)
        budgets[name] = {"corrective_retries": {"max_per_action": maximum, "used_current_action": used},
                         "investigation_expansions": {"max_per_stage": maximum, "used_current_stage": used}}.get(name, {"max": maximum, "used": used})
    snapshot = {
        "schema_version": 1,
        "workspace": {key: workspace[key] for key in ("path", "branch", "head", "git_common_dir")},
        "state": {
            "sha256": sha256_bytes(ctx.text.encode("utf-8")), "ticket": ctx.ticket, "stage": ctx.stage,
            "status": (ctx.state.get("stage") or {}).get("status") or "RUNNING",
            "completed": list((ctx.state.get("stage") or {}).get("completed") or []), "skipped": sf.skipped_stage_names(ctx.state),
            "next_action": (ctx.state.get("resume") or {}).get("next_action"),
            "blockers": [str(item) for item in (ctx.state.get("blockers") or [])] + [str(item) for item in ctx.delivery.get("worker_blockers") or []],
            "protected_preexisting": list((ctx.state.get("baseline") or {}).get("protected_preexisting") or []),
            "agent_owned": list((ctx.state.get("ownership") or {}).get("agent_owned") or []),
        },
        "loop": {"mode": loop.get("mode") or "MANUAL", "loop_active": bool((loop.get("control") or {}).get("loop_active")),
                 "budgets": budgets, "stop_reason": (loop.get("control") or {}).get("stop_reason") or "NONE",
                 "human_approval_required": bool((loop.get("control") or {}).get("human_approval_required"))},
        "gates": {**{name: gate_value(name) for name in ("focused_tests", "format", "analyze", "ci")},
                  "review": (gates.get("review") or {}).get("status") or "PENDING", "project_ci_enabled": ci_enabled()},
        "implementation": {"planned_slices": list(slices.get("planned") or []), "completed_slices": list(slices.get("completed") or []),
                           "next_slice": current_slice(ctx), "all_slices_green": bool(slices.get("planned")) and current_slice(ctx) is None,
                           "canonical_focused_test_command_available": bool((table.get("focused_tests") or {}).get("command")),
                           "changed_files_available": bool((ctx.state.get("ownership") or {}).get("agent_owned"))},
        "recovery": {"decision": ctx.recovery["decision"], "journal_status": ctx.journal["action"]["status"],
                     "current_action_id": ctx.journal["action"]["id"], "artifact_present": aj.artifact_present(ctx.journal)},
        "restrictions": {"external_mutation_requested": False, "protected_file_required": False, "scope_change_required": False, "architecture_decision_required": False},
    }
    planner.validate_snapshot(snapshot)
    output = Path(args.output) if args.output else ctx.run_dir() / "snapshot.json"
    atomic_write_text(output, json.dumps(snapshot, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    return {"status": "WRITTEN", "path": str(output), "state_sha256": snapshot["state"]["sha256"],
            "next_step": "Plan the bounded run from it (never hand-edit the snapshot).",
            "next_command": command(RUNTIME / "bounded_run_planner.py", "plan", "--snapshot", str(output), "--target", "NEXT_HUMAN_CHECKPOINT", "--json")}


def cmd_prepare(ctx: Ctx, args: argparse.Namespace) -> dict[str, Any]:
    ctx.require_pristine()
    plan = plan_dispatch(ctx)
    if plan["status"] == "STOP":
        raise SddError(plan["stop_reason"], plan["next_step"], next_command=plan.get("next_command"))
    if plan["stage"] != args.stage or plan["role"] != args.role or str(Path(args.manifest)) != plan["manifest"]:
        raise SddError("STEP_MISMATCH", f"The next action is {plan['role'] or plan['stage']} with manifest {plan['manifest']}, not what was passed.",
                       next_step="Run exactly the commands `sdd.py next` printed.", next_command=sdd("next"))
    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = build_manifest(ctx, ctx.state, plan["stage"], role=plan["role"], slice_id=plan["slice_id"], head=plan["head"])
    if manifest != expected:
        raise SddError("MANIFEST_INVALID", "The manifest file differs from the one STATE produces; regenerate it, never edit it.",
                       next_command=sdd("manifest", "--stage", plan["stage"], "--output", plan["manifest"], *(["--role", plan["role"]] if plan["role"] else [])))
    refused = check_manifest(manifest, plan["role"])
    if refused:
        raise SddError(refused["stop_reason"], refused["next_step"], next_command=refused.get("next_command"), findings=refused.get("findings"))
    prompt = build_prompt(ctx, plan, manifest)
    prompt_path = Path(plan["prompt"])
    atomic_write_text(prompt_path, prompt)
    data = copy.deepcopy(ctx.state)
    used, _ = budget(data, "executor_calls")
    set_budget(data, "executor_calls", used=used + 1)
    set_budget(data, "corrective_retries", used=plan["attempt"] - 1)
    data.setdefault("resume", {})["next_action"] = plan["action_id"]
    ctx.write_state(data, f"Prepared {plan['action_id']} ({plan['role'] or plan['stage']}, attempt {plan['attempt']}, prompt {len(prompt.encode('utf-8'))} bytes)")
    executor = executor_for(plan["stage"]).upper()
    schema = ORCHESTRATION / "schemas" / ("REVIEW_RESULT_SCHEMA.json" if plan["stage"] == "REVIEW" else "EXECUTOR_RESULT_SCHEMA.json")
    parent = plan["parent"] or {}
    payload = aj.empty_journal(dict(ctx.journal["workspace"]))
    payload["action"].update({
        "id": plan["action_id"], "ticket": ctx.ticket, "stage": plan["stage"], "name": plan["role"] or plan["stage"], "status": "PREPARED",
        "executor": executor, "schema_path": str(schema), "protocol_version": 3, "prompt_hash": sha256_bytes(prompt.encode("utf-8")),
        "final_message_path": plan["final"], "attempt": plan["attempt"],
        "retry_mode": "FULL_REPLACEMENT" if parent else None, "parent_action_id": parent.get("action_id"),
        "parent_artifact_path": parent.get("artifact_path"), "parent_artifact_sha256": parent.get("artifact_sha256"),
    })
    payload["fingerprints"] = {
        "baseline": sha256_bytes(json.dumps(ctx.state.get("baseline"), sort_keys=True).encode()),
        "ownership": sha256_bytes(json.dumps(ctx.state.get("ownership"), sort_keys=True).encode()),
        "state_before": sha256_bytes(ctx.text.encode("utf-8")),
    }
    try:
        aj.prepare_action(ctx.journal_path, payload, history_dir=ctx.history_dir)
    except aj.JournalError as exc:
        raise SddError(exc.code if exc.code in sr.STOP_REASONS else "ACTION_RECOVERY_REQUIRED", str(exc), next_step=exc.next_step, next_command=sdd("next")) from exc
    return {"status": "PREPARED", "action_id": plan["action_id"], "executor": executor, "prompt": str(prompt_path),
            "prompt_bytes": len(prompt.encode("utf-8")), "next_step": "Dispatch it with the command `sdd.py next` prints.", "next_command": sdd("next")}


def cmd_reject(ctx: Ctx, _args: argparse.Namespace) -> dict[str, Any]:
    if ctx.journal["action"]["status"] != "ARTIFACT_READY":
        raise SddError("ACTION_RECOVERY_REQUIRED", "reject applies to an ARTIFACT_READY action.", next_command=sdd("next"))
    action = journal_action(ctx)
    manifest = json.loads(action["manifest"].read_text(encoding="utf-8"))
    errors = validate_result(ctx, action["stage"], action["role"], manifest, action["final"])
    if not errors:
        raise SddError("STEP_MISMATCH", "The result is valid; accept it instead.", next_command=sdd("next"))
    atomic_write_text(action["errors"], json.dumps(errors[:20], ensure_ascii=False, indent=1) + "\n")
    fields = sorted({item["path"] for item in errors})[:8]
    aj.classify_invalid_artifact(ctx.journal_path, fields)
    record_wiki(ctx, "incident", f"{action['id']} invalid result", json.dumps(errors[:10], ensure_ascii=False, indent=1), stage=action["stage"])
    return {**stop("CONTRACT_INVALID"), "status": "CLASSIFIED_INVALID", "action_id": action["id"], "invalid_fields": fields}


def apply_result(ctx: Ctx, data: dict[str, Any], action: dict[str, Any], result: dict[str, Any], digest: str) -> str:
    """Fold one validated worker result into STATE; return a one-line log."""
    stage, role = action["stage"], action["role"]
    delivery = data.setdefault("delivery", {})
    key = action_key(stage, role, current_slice(ctx) if stage == "IMPLEMENT" else None)
    delivery.setdefault("accepted", {})[key] = {"action_id": action["id"], "artifact": str(action["final"]), "artifact_sha256": digest}
    set_budget(data, "corrective_retries", used=0)
    if stage == "REVIEW":
        review = result["review_result"]
        data.setdefault("gates", {})["review"] = {"status": review["status"], "action_id": action["id"]}
        used, _ = budget(data, "review_cycles")
        set_budget(data, "review_cycles", used=used + 1)
        if review["status"] == "APPROVED" and not ci_enabled():
            data["gates"]["ci"] = {"status": "DISABLED_BY_PROJECT_POLICY"}
        delivery.setdefault("summaries", {})["REVIEW"] = {"summary": review["next_step"]["action"], "findings": len(review["findings"])}
        return f"REVIEW {review['status']} ({action['id']})"
    worker = result["executor_result"]
    payload = worker["stage_payload"]
    if worker["stage"]["status"] != "SUCCESS" or worker["blockers"]:
        delivery["worker_blockers"] = [f"{item['type']}: {item['description']}" for item in worker["blockers"]] or [f"{stage} returned {worker['stage']['status']}"]
        return f"{role or stage} returned {worker['stage']['status']} with blockers ({action['id']})"
    if role == GUARDIAN_ROLE:
        delivery["project_context"] = {"status": "CURRENT", "checked_head": git(ctx.repo, "rev-parse", "HEAD").strip(),
                                       "evidence": f"{action['final']} sha256 {digest}", "gaps": []}
        delivery.setdefault("summaries", {})["PROJECT_CONTEXT"] = {"summary": payload["summary"]}
        return f"project context recorded at HEAD ({action['id']})"
    delivery.setdefault("summaries", {})[stage] = {"summary": payload["summary"], "decisions": payload["decisions"][:10], "tasks": payload["tasks"][:20]}
    checks = worker["acceptance_checks"]
    if stage in {"SPECIFY", "CLARIFY", "PLAN", "TASKS"}:
        delivery["acceptance"] = {item["id"]: {"criterion": item["criterion"], "verification_method": item["verification_method"],
                                               "verifier": item["verifier"], "slice_id": item["slice_id"]} for item in checks}
    if stage in {"SPECIFY", "CLARIFY"}:
        questions = [item for item in worker["context_assessment"]["unresolved_questions"] if item["material"]]
        answered = {item["question"]: item for item in delivery.get("open_questions") or [] if item.get("answer")}
        delivery["open_questions"] = [answered.get(item["question"], {"index": index + 1, "question": item["question"], "impact": item["impact"]})
                                      for index, item in enumerate(questions)]
    profile = delivery.get("profile") or "CODE"
    if (stage == "TASKS") or (stage == "PLAN" and profile == "DECISION_DOC"):
        if stage == "PLAN":
            for check in delivery["acceptance"].values():
                check["slice_id"] = DOC_SLICE
        planned = list(dict.fromkeys(check["slice_id"] for check in delivery["acceptance"].values() if check["slice_id"]))
        delivery["slices"] = {"planned": planned, "completed": [], "editable_paths": sorted(set(payload["impact_files"]))}
        import stage_context

        hashes = []
        for slice_id in planned:
            manifest = build_manifest(ctx, data, "IMPLEMENT", slice_id=slice_id)
            hashes.append(hashlib.sha256(stage_context._canonical(stage_context.slice_contract(manifest, slice_id))).hexdigest())
        delivery["approved_slice_sha256s"] = hashes
    if stage == "IMPLEMENT":
        slice_id = current_slice(ctx)
        delivery["slices"]["completed"] = [*delivery["slices"].get("completed", []), slice_id]
        used, _ = budget(data, "tdd_slices")
        set_budget(data, "tdd_slices", used=used + 1)
        owned = set((data.get("ownership") or {}).get("agent_owned") or [])
        owned |= {item["path"] for item in worker["modified_paths"] + worker["created_paths"]}
        data.setdefault("ownership", {})["agent_owned"] = sorted(owned)
        data.setdefault("evidence", {}).setdefault("tdd_slices", []).append({"slice": slice_id, "action_id": action["id"], "artifact_sha256": digest})
        return f"IMPLEMENT slice {slice_id} GREEN ({action['id']})"
    return f"{stage} accepted ({action['id']})"


def cmd_accept(ctx: Ctx, _args: argparse.Namespace) -> dict[str, Any]:
    status = ctx.journal["action"]["status"]
    if status not in {"ARTIFACT_READY", "VALIDATED", "STATE_COMMITTED"}:
        raise SddError("ACTION_RECOVERY_REQUIRED", f"accept applies to a recorded result, journal is {status}.", next_command=sdd("next"))
    action = journal_action(ctx)
    if status == "STATE_COMMITTED":
        aj.release_action(ctx.journal_path)
        return {"status": "ACCEPTED", "action_id": action["id"], "next_step": "Roll over with the command `sdd.py next` prints.", "next_command": sdd("next")}
    manifest = json.loads(action["manifest"].read_text(encoding="utf-8"))
    if status == "ARTIFACT_READY":
        errors = validate_result(ctx, action["stage"], action["role"], manifest, action["final"])
        if errors:
            raise SddError("CONTRACT_INVALID", "The result is invalid; it cannot be accepted.", next_command=sdd("next"), errors=errors[:5])
        aj.mark_validated(ctx.journal_path)
        ctx.reload()
    commit = ctx.journal["state_commit"]
    current_hash = sha256_bytes(ctx.state_path.read_bytes())
    if commit["expected_after_hash"] and current_hash == commit["expected_after_hash"]:
        aj.mark_state_committed(ctx.journal_path)
    else:
        result = json.loads(action["final"].read_text(encoding="utf-8"))
        digest = sha256_bytes(action["final"].read_bytes())
        data = copy.deepcopy(ctx.state)
        log = apply_result(ctx, data, action, result, digest)
        new_text = sf.dump(ctx.text, data, log=f"{now()} {log}")
        new_hash = sha256_bytes(new_text.encode("utf-8"))
        aj.prepare_state_commit(ctx.journal_path, ctx.state_path, current_hash, new_hash)
        atomic_write_text(ctx.state_path, new_text)
        aj.mark_state_committed(ctx.journal_path)
        body = result.get("executor_result", result.get("review_result", {}))
        summary = body.get("stage_payload", {}).get("summary") if "stage_payload" in body else body.get("status")
        record_wiki(ctx, "stage", f"{action['role'] or action['stage']} {action['id']}", f"{log}\n\nSummary: {summary}\n\nArtifact: {action['final']}",
                    stage=action["stage"])
    aj.release_action(ctx.journal_path)
    ctx.reload()
    if action["stage"] == "REVIEW":
        review = json.loads(action["final"].read_text(encoding="utf-8"))["review_result"]
        if review["ownership"]["violations"]:
            return {**stop("OWNERSHIP_VIOLATION"), "status": "ACCEPTED", "action_id": action["id"]}
    return {"status": "ACCEPTED", "action_id": action["id"], "next_step": "Roll over with the command `sdd.py next` prints.", "next_command": sdd("next")}


def cmd_transition(ctx: Ctx, args: argparse.Namespace) -> dict[str, Any]:
    ctx.require_pristine()
    current = ctx.stage
    data = copy.deepcopy(ctx.state)
    reopen = sf.FSM_ORDER.index(args.to) < sf.FSM_ORDER.index(current) if args.to in sf.FSM_ORDER and current in sf.FSM_ORDER else False
    provenance = None
    if not reopen:
        if not stage_complete(ctx):
            raise SddError("STEP_MISMATCH", f"{current} is not complete yet.", next_command=sdd("next"))
        used, maximum = budget(data, "stage_transitions")
        if used >= maximum:
            raise SddError("STAGE_TRANSITION_BUDGET_REACHED", "The stage-transition budget is spent.")
        key = current.lower() if current != "IMPLEMENT" else f"implement-{(ctx.delivery.get('slices') or {}).get('planned', ['?'])[-1].lower()}"
        accepted = (ctx.delivery.get("accepted") or {}).get(key)
        if accepted:
            if args.artifact and Path(args.artifact) != Path(accepted["artifact"]):
                raise SddError("STEP_MISMATCH", "--artifact is not the accepted artifact of this stage.", next_command=sdd("next"))
            if sha256_file(Path(accepted["artifact"])) != accepted["artifact_sha256"]:
                raise SddError("STATE_INCONSISTENT", f"The accepted {current} artifact changed after acceptance.", next_command=None)
            provenance = {"action_id": accepted["action_id"], "artifact": accepted["artifact"], "artifact_sha256": accepted["artifact_sha256"], "at": now()}
        if args.to == "DONE":
            missing = done_gate_failure(ctx)
            if missing:
                raise SddError("DONE_GATES_NOT_PASSED", f"Gate {missing} does not allow DONE.")
        set_budget(data, "stage_transitions", used=used + 1)
    elif not (args.reason and args.quote):
        raise SddError("STEP_MISMATCH", "Reopening a stage needs --reason and the user's --quote.",
                       next_command=sdd("transition", "--to", args.to, "--reason", "<why>", "--quote", "<user words>"))
    try:
        data = sf.apply_transition(data, args.to, profile=ctx.profile, provenance=provenance,
                                   clarify_skip_reason=args.skip_reason, reopen_reason=f"{args.reason} (user: {args.quote})" if reopen else None)
    except sf.StateFormatError as exc:
        raise SddError("STEP_MISMATCH", str(exc), next_command=sdd("next")) from exc
    set_budget(data, "investigation_expansions", used=0)
    if reopen:
        slices = data.setdefault("delivery", {}).setdefault("slices", {})
        last = (slices.get("planned") or ["FIX"])[-1]
        fix = f"FIX{len([item for item in slices.get('planned', []) if item.startswith('FIX')]) + 1}"
        slices["planned"] = [*slices.get("planned", []), fix]
        for check in data["delivery"].get("acceptance", {}).values():
            if check.get("slice_id") == last:
                check["slice_id"] = fix
        data["delivery"]["approved_slice_sha256s"] = []
        accepted = data["delivery"].setdefault("accepted", {})
        for stage_key in ("test", "review"):
            accepted.pop(stage_key, None)
        data["gates"] = {name: {"status": "PENDING"} for name in ("focused_tests", "format", "analyze", "review", "ci")}
    data.setdefault("resume", {})["next_action"] = args.to
    ctx.write_state(data, f"{current} -> {args.to}" + (f" (reopened: {args.reason})" if reopen else "") + (f" (CLARIFY skipped: {args.skip_reason})" if args.skip_reason else ""))
    if args.to == "DONE":
        record_wiki(ctx, "stage", f"{ctx.ticket} DONE", f"Engineering validated. Gates: {json.dumps(data.get('gates'))}\nNo commit or push was performed.", stage="DONE")
        record_wiki(ctx, "decision", f"{ctx.ticket} delivered", f"Delivered {ctx.state['ticket'].get('title')} under the {ctx.profile} profile; review APPROVED, gates passed.", stage="DONE")
    return {"status": "TRANSITIONED", "from": current, "to": args.to, "next_step": "Run `sdd.py next`.", "next_command": sdd("next")}


def cmd_waive(ctx: Ctx, args: argparse.Namespace) -> dict[str, Any]:
    ctx.require_pristine()
    acceptance = ctx.delivery.get("acceptance") or {}
    if args.check not in acceptance:
        raise SddError("STEP_MISMATCH", f"{args.check} is not an acceptance check of {ctx.ticket} ({', '.join(sorted(acceptance)) or 'none yet'}).", next_command=sdd("status"))
    allowed = {"requester", *approvers().values()}
    if args.by not in allowed:
        raise SddError("STEP_MISMATCH", f"--by must be the requester or an approver from PROJECT_SETUP.md ({', '.join(sorted(allowed))}).",
                       next_command=sdd("waive", "--check", args.check, "--by", "requester", "--quote", args.quote, "--reason", args.reason))
    if not args.quote.strip() or not args.reason.strip():
        raise SddError("STEP_MISMATCH", "--quote (the user's literal words) and --reason are required.", next_command=sdd("next"))
    data = copy.deepcopy(ctx.state)
    waiver = {"by": args.by, "reason": args.reason, "quote": args.quote, "recorded_at": now()}
    data.setdefault("delivery", {}).setdefault("waivers", {})[args.check] = waiver
    ctx.write_state(data, f"{args.check} decided by {args.by}: \"{args.quote}\" ({args.reason})")
    record_wiki(ctx, "decision", f"{ctx.ticket} {args.check} waived", f"Check: {acceptance[args.check]['criterion']}\nBy: {args.by}\nQuote: {args.quote}\nReason: {args.reason}", stage=ctx.stage)
    return {"status": "WAIVED", "check": args.check, "waiver": waiver, "next_step": "Run `sdd.py next`; validation receives it as recorded_waivers.", "next_command": sdd("next")}


def cmd_answer(ctx: Ctx, args: argparse.Namespace) -> dict[str, Any]:
    ctx.require_pristine()
    data = copy.deepcopy(ctx.state)
    questions = data.setdefault("delivery", {}).get("open_questions") or []
    match = next((item for item in questions if item.get("index") == args.index), None)
    if match is None:
        raise SddError("STEP_MISMATCH", f"No open question {args.index}.", next_command=sdd("status"))
    match["answer"] = args.quote
    data["delivery"].setdefault("answers", []).append({"question": match["question"], "answer": args.quote, "recorded_at": now()})
    if ctx.stage == "CLARIFY":
        data["delivery"].setdefault("accepted", {}).pop("clarify", None)
    ctx.write_state(data, f"Answer to question {args.index}: \"{args.quote}\"")
    return {"status": "ANSWERED", "index": args.index, "next_step": "Run `sdd.py next`.", "next_command": sdd("next")}


def cmd_unblock(ctx: Ctx, args: argparse.Namespace) -> dict[str, Any]:
    ctx.require_pristine()
    data = copy.deepcopy(ctx.state)
    blockers = data.setdefault("delivery", {}).get("worker_blockers") or []
    data["delivery"]["worker_blockers"] = []
    data["delivery"].setdefault("answers", []).append({"question": "; ".join(blockers), "answer": args.quote, "recorded_at": now()})
    key = ctx.stage.lower()
    data["delivery"].get("accepted", {}).pop(key, None)
    ctx.write_state(data, f"Blockers resolved by the user: \"{args.quote}\"")
    return {"status": "UNBLOCKED", "next_step": "Run `sdd.py next`; the stage is redispatched with the user's answer.", "next_command": sdd("next")}


def cmd_budget(ctx: Ctx, args: argparse.Namespace) -> dict[str, Any]:
    ctx.require_pristine()
    if not args.quote.strip():
        raise SddError("STEP_MISMATCH", "--quote with the user's words authorizing the raise is required.", next_command=sdd("next"))
    data = copy.deepcopy(ctx.state)
    _, maximum = budget(data, args.raise_name)
    set_budget(data, args.raise_name, maximum=maximum + args.by)
    data.setdefault("delivery", {}).setdefault("authorization", {}).setdefault("raises", []).append({"budget": args.raise_name, "by": args.by, "quote": args.quote, "at": now()})
    ctx.write_state(data, f"Budget {args.raise_name} raised by {args.by}: \"{args.quote}\"")
    return {"status": "RAISED", "budget": args.raise_name, "max": maximum + args.by, "next_step": "Run `sdd.py next`.", "next_command": sdd("next")}


def cmd_gate(ctx: Ctx, args: argparse.Namespace) -> dict[str, Any]:
    ctx.require_pristine()
    name = args.name
    row = gate_table().get(name) or {"unconfigured": True}
    data = copy.deepcopy(ctx.state)
    gates = data.setdefault("gates", {})
    order = {"format": ("focused_tests",), "analyze": ("focused_tests", "format"), "ci": ("focused_tests", "format", "analyze")}
    missing = [item for item in order.get(name, ()) if (gates.get(item) or {}).get("status") != "PASS"]
    if missing:
        raise SddError({"format": "FOCUSED_TESTS_REQUIRED", "analyze": "TEST_AND_FORMAT_REQUIRED"}.get(name, "REVIEW_PREREQUISITES_REQUIRED"),
                       f"{name} runs after {', '.join(missing)}.", next_command=sdd("next"))
    if args.not_applicable:
        if not row.get("not_applicable"):
            raise SddError("STEP_MISMATCH", f"GATES.md does not mark {name} NOT_APPLICABLE.", next_command=sdd("next"))
        if not (args.quote or "").strip():
            raise SddError("GATE_CONFIRMATION_REQUIRED", "The user's literal confirmation is required.")
        gates[name] = {"status": "PASS", "not_applicable": {"by": args.by, "quote": args.quote, "reason": "NOT_APPLICABLE in policies/GATES.md", "recorded_at": now()}}
        ctx.write_state(data, f"Gate {name} NOT_APPLICABLE confirmed by {args.by}: \"{args.quote}\"")
        record_wiki(ctx, "gate", f"{name} NOT_APPLICABLE", f"Confirmed by {args.by}: {args.quote}", stage=ctx.stage)
        return {"status": "PASS", "gate": name, "not_applicable": True, "next_step": "Run `sdd.py next`.", "next_command": sdd("next")}
    if row.get("not_applicable"):
        raise SddError("GATE_CONFIRMATION_REQUIRED", f"GATES.md marks {name} NOT_APPLICABLE; it needs the user's confirmation.",
                       next_command=sdd("gate", "--name", name, "--not-applicable", "--by", "requester", "--quote", "<user words>"))
    if row.get("unconfigured") or not row.get("command"):
        raise SddError("GATE_COMMAND_UNCONFIGURED", f"{name} has no verified command in policies/GATES.md.", next_command=None)
    if name == "ci":
        used, maximum = budget(data, "ci_runs")
        if used >= maximum:
            raise SddError("CI_RUN_BUDGET_REACHED", "The CI-run budget is spent.")
        set_budget(data, "ci_runs", used=used + 1)
    files = list((data.get("ownership") or {}).get("agent_owned") or [])
    argv: list[str] = []
    for token in shlex.split(row["command"]):
        argv.extend(files if token == "{files}" else [token])
    try:
        completed = subprocess.run(argv, cwd=str(ctx.repo), capture_output=True, text=True, timeout=row["timeout"], check=False)
        status, exit_code, tail = ("PASS" if completed.returncode == 0 else "FAIL"), completed.returncode, (completed.stdout + completed.stderr)[-MAX_OUTPUT_TAIL:]
    except subprocess.TimeoutExpired:
        status, exit_code, tail = "TIMEOUT", 124, ""
    except OSError as exc:
        status, exit_code, tail = "FAIL", 127, str(exc)
    gates[name] = {"status": status, "command": row["command"], "exit_code": exit_code, "at": now()}
    ctx.write_state(data, f"Gate {name} {status} (exit {exit_code})")
    record_wiki(ctx, "gate", f"{name} {status}", f"Command: `{row['command']}`\nExit: {exit_code}\n\n{tail}", stage=ctx.stage)
    if status != "PASS":
        code = "CI_TIMEOUT" if (name == "ci" and status == "TIMEOUT") else ("GATE_TIMEOUT" if status == "TIMEOUT" else GATE_FAILURE_STOP_REASONS[name])
        raise SddError(code, f"Gate {name} {status} (exit {exit_code}).", output_tail=tail)
    return {"status": "PASS", "gate": name, "exit_code": exit_code, "next_step": "Run `sdd.py next`.", "next_command": sdd("next")}


def record_wiki(ctx: Ctx, kind: str, title: str, body: str, *, stage: str | None = None) -> None:
    try:
        import wiki_journal

        wiki_journal.safe_record_for_workspace(ctx.repo, kind=kind, title=title, body=body, ticket=ctx.ticket if ctx.ticket != "IDLE" else None, stage=stage)
    except Exception:  # the wiki never blocks the loop (LOOP_POLICY wiki rule)
        pass


# ----------------------------------------------------------------------------- CLI

COMMANDS = {
    "status": cmd_status, "next": cmd_next, "start": cmd_start, "snapshot": cmd_snapshot, "manifest": cmd_manifest,
    "prepare": cmd_prepare, "reject": cmd_reject, "accept": cmd_accept, "transition": cmd_transition, "waive": cmd_waive,
    "answer": cmd_answer, "unblock": cmd_unblock, "budget": cmd_budget, "gate": cmd_gate,
}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Deterministic SDD controller: run `next`, execute exactly the printed commands, repeat.")
    result.add_argument("--repo", help="Repository worktree (default: the current directory).")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="One compact JSON with paths, stage, recovery, budgets left and the next action.")
    commands.add_parser("next", help="Print the exact command batch for the next step, or the stop and its resolution command.")
    start = commands.add_parser("start", help="IDLE -> SPECIFY: capture the baseline and print the one preview with limits.")
    start.add_argument("--ticket", required=True)
    start.add_argument("--title", required=True)
    start.add_argument("--objective", required=True)
    start.add_argument("--request", help="The user's literal request (authorization quote); defaults to --objective.")
    start.add_argument("--deliverable-kind", choices=DELIVERABLE_KINDS, default="CODE")
    start.add_argument("--executor-calls", type=int, default=12, help="Executor-call limit for the demand (default 12).")
    start.add_argument("--tdd-slices", type=int, default=6, help="TDD-slice limit for the demand (default 6).")
    snapshot = commands.add_parser("snapshot", help="Write the normalized bounded-run planner snapshot from STATE and the journal.")
    snapshot.add_argument("--output")
    manifest = commands.add_parser("manifest", help="Write the stage-context manifest for the current step, hashes included.")
    manifest.add_argument("--stage", required=True, choices=STAGES)
    manifest.add_argument("--role", choices=(GUARDIAN_ROLE,))
    manifest.add_argument("--output")
    prepare = commands.add_parser("prepare", help="Build the prompt (max_prompt_bytes enforced) and prepare the journaled action.")
    prepare.add_argument("--stage", required=True, choices=STAGES)
    prepare.add_argument("--role", choices=(GUARDIAN_ROLE,))
    prepare.add_argument("--manifest", required=True)
    commands.add_parser("reject", help="Record the errors of an invalid result and classify it invalid.")
    commands.add_parser("accept", help="Validate the result and commit it to STATE through the journal, then release.")
    transition = commands.add_parser("transition", help="Validated STATE transition with provenance (journal must be idle).")
    transition.add_argument("--to", required=True, choices=sf.FSM_ORDER[1:])
    transition.add_argument("--artifact", help="Accepted artifact of the stage being left (verified by SHA-256).")
    transition.add_argument("--skip-reason", help="Why CLARIFY is skipped.")
    transition.add_argument("--reason", help="Why a stage is reopened.")
    transition.add_argument("--quote", help="The user's literal words authorizing a reopen.")
    waive = commands.add_parser("waive", help="Record a human acceptance decision as a waiver.")
    waive.add_argument("--check", required=True)
    waive.add_argument("--by", required=True)
    waive.add_argument("--quote", required=True)
    waive.add_argument("--reason", required=True)
    answer = commands.add_parser("answer", help="Record the user's answer to an open material question.")
    answer.add_argument("--index", type=int, required=True)
    answer.add_argument("--quote", required=True)
    unblock = commands.add_parser("unblock", help="Clear worker blockers with the user's resolution; the stage is redispatched.")
    unblock.add_argument("--quote", required=True)
    budget_parser = commands.add_parser("budget", help="Raise one demand budget with the user's authorization quote.")
    budget_parser.add_argument("--raise", dest="raise_name", required=True, choices=BUDGET_NAMES)
    budget_parser.add_argument("--by", type=int, default=1)
    budget_parser.add_argument("--quote", required=True)
    gate = commands.add_parser("gate", help="Run one configured gate from policies/GATES.md, or confirm a NOT_APPLICABLE one.")
    gate.add_argument("--name", required=True, choices=GATE_NAMES)
    gate.add_argument("--not-applicable", action="store_true")
    gate.add_argument("--by", default="requester")
    gate.add_argument("--quote")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        ctx = Ctx(Path(args.repo) if args.repo else None)
        result = COMMANDS[args.command](ctx, args)
        code = 0
    except SddError as exc:
        result, code = exc.payload(), 2
    except (aj.JournalError, sf.StateFormatError) as exc:
        result, code = {"status": getattr(exc, "code", "STATE_INCONSISTENT"), "message": str(exc),
                        "next_step": getattr(exc, "next_step", "Run `sdd.py next`."), "next_command": sdd("next")}, 2
    except Exception as exc:  # planner/driver errors carry their own text
        result, code = {"status": "ERROR", "message": f"{type(exc).__name__}: {exc}", "next_step": "Run `sdd.py status`; report this message if it repeats.",
                        "next_command": sdd("status")}, 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
