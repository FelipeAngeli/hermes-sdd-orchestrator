"""Shared builders for the validation-gate tests: a temporary Git repository and executor_result artifacts."""
from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

PY = sys.executable
TEST_CMD = f"{PY} check_total.py"
ANALYZE_CMD = f"{PY} -m py_compile app.py"


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def make_repo(root: Path, *, correct: bool = True) -> Path:
    """A tiny project: app.py with total(), and check_total.py that exits 0 only when total is right."""
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@example.invalid")
    git(repo, "config", "user.name", "t")
    (repo / "app.py").write_text("def total(values):\n    return sum(values)\n", encoding="utf-8")
    (repo / "check_total.py").write_text(
        "import sys\nfrom app import total\nsys.exit(0 if total([1, 2, 3]) == 6 else 1)\n", encoding="utf-8")
    (repo / "README.md").write_text("# demo\nTotals.\n", encoding="utf-8")
    (repo / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    write_app(repo, correct=correct)
    return repo


def write_app(repo: Path, *, correct: bool) -> None:
    body = "sum(values)" if correct else "sum(values) - 1"
    (repo / "app.py").write_text(f"def total(values):\n    return {body}\n\n\ndef label():\n    return 'total'\n", encoding="utf-8")


def check(check_id: str, status: str = "PASS", *, command: str = TEST_CMD, evidence: str | None = None, slice_id: str | None = "S1"):
    return {
        "id": check_id, "criterion": f"{check_id} criterion", "verification_method": f"run `{command}`",
        "verifier": "AGENT", "slice_id": slice_id, "status": status,
        "evidence": evidence if evidence is not None else (f"`{command}` exit 0" if status == "PASS" else None), "waiver": None,
    }


BASE = {
    "executor_result": {
        "schema_version": 3,
        "stage": {"value": "TEST", "status": "SUCCESS"},
        "executor": {"name": "CODEX", "invocation_type": "EXTERNAL_CLI"},
        "consulted_paths": [{"path": "app.py"}],
        "modified_paths": [],
        "created_paths": [],
        "validated_symbols": [{"symbol": "total", "path": "app.py", "exists": True}],
        "commands": [{"command": TEST_CMD, "purpose": "focused tests", "timeout_seconds": 60, "exit_code": 0, "result": "PASS"}],
        "blockers": [],
        "context_assessment": {"facts": [], "assumptions": [], "unresolved_questions": []},
        "acceptance_checks": [check("AC-1")],
        "stage_payload": {"summary": "Totals are correct.", "tasks": ["S1: fix total() in app.py, verified by check_total.py"],
                          "impact_files": ["app.py"], "decisions": ["Keep total() pure; risk: none beyond app.py."]},
        "tdd_slices": [],
        "next_step": {"stage": "REVIEW", "action": "review"},
    }
}


def artifact(stage: str = "TEST", **changes):
    value = copy.deepcopy(BASE)
    result = value["executor_result"]
    result["stage"]["value"] = stage
    for key, item in changes.items():
        if key in ("modified_paths", "created_paths", "consulted_paths"):
            item = [entry if isinstance(entry, dict) else {"path": entry} for entry in item]
        if key in result["stage_payload"]:
            result["stage_payload"][key] = item
        else:
            result[key] = item
    return value


def review_artifact(**changes):
    value = {"review_result": {
        "schema_version": 3, "status": "APPROVED", "reviewed_paths": [{"path": "app.py"}], "findings": [],
        "baseline": {"preserved": True, "violations": []}, "ownership": {"valid": True, "violations": []},
        "acceptance": {"verified": True, "expected_check_ids": ["AC-1"], "checks": [
            {"id": "AC-1", "criterion": "c", "verification_method": f"run `{TEST_CMD}`", "verifier": "AGENT",
             "slice_id": "S1", "status": "PASS", "evidence": f"`{TEST_CMD}` exit 0", "waiver": None}]},
        "e2e": {"files_modified": False, "execution_performed": False, "violation": False},
        "forbidden_actions": {"violations": []},
        "gate_status": {"focused_tests": "PASS", "format": "PASS", "analyze": "PASS", "ci": "DISABLED_BY_PROJECT_POLICY"},
        "next_step": {"action": "done"},
    }}
    value["review_result"].update(changes)
    return value


class FakeReviewer:
    """Scripted reviewer: returns a verdict per criterion and records the context it was given."""

    def __init__(self, verdict: str = "PASS", *, cite: bool = True, fail_on=()):
        self.verdict, self.cite, self.fail_on = verdict, cite, set(fail_on)
        self.contexts: list[dict] = []

    def __call__(self, context: dict) -> dict:
        self.contexts.append(context)
        refs = [item["ref"] for item in context["evidence"]][:1] if self.cite else []
        return {"criteria": [{"id": item["id"], "verdict": "FAIL" if item["id"] in self.fail_on else self.verdict,
                              "evidence_refs": refs, "rationale": "checked"} for item in context["criteria"]]}
