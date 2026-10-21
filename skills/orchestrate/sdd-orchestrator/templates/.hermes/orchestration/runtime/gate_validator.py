"""DeterministicValidator: real commands and artifact checks for each SDD gate.

Commands come only from the caller's configuration (the GATES.md rows) or from
verifiers that ``acceptance_verifiers.allowed_check_command`` accepts. They run
without a shell (``shlex.split``), in the repository, with a timeout, and their exit
code and bounded, redacted output are stored as evidence. A worker's own report is
never trusted: a command it claims passed is re-run, and its file lists are compared
with ``git status``.

Each check returns ``{"id", "status", "refs", "detail"}`` with status PASS, FAIL,
BLOCKED or INCONCLUSIVE (a timeout or an unrunnable command is INCONCLUSIVE, never PASS).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

import jsonschema

import acceptance_verifiers
import redaction
import validate_protocol
from gate_evidence import EvidenceCollector

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
OUTPUT_LIMIT = 4000
DEFAULT_TIMEOUT = 300
CITED = re.compile(r"`([^`]+)`")
#: A command argument that names a repository file: path characters only, with an extension or a directory part.
FILE_TOKEN = re.compile(r"(?:[\w.-]+/)*[\w-][\w.-]*\.[A-Za-z0-9]+|(?:[\w.-]+/)+[\w.-]+")
PASS, FAIL, BLOCKED, INCONCLUSIVE = "PASS", "FAIL", "BLOCKED", "INCONCLUSIVE"
#: Environment variables passed to gate commands; nothing else from the caller's environment.
SAFE_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT", "PYTHONPATH", "VIRTUAL_ENV")


def result(identifier: str, status: str, refs: list[str] | None = None, detail: str = "") -> dict[str, Any]:
    return {"id": identifier, "status": status, "refs": list(refs or []), "detail": detail}


def _tail(text: str) -> str:
    return redaction.redact(text[-OUTPUT_LIMIT:])


class DeterministicValidator:
    def __init__(self, repo: Path, evidence: EvidenceCollector, *, commands: dict[str, str | None],
                 timeouts: dict[str, int] | None = None, clock: Callable[[], float] = time.time) -> None:
        self.repo = Path(repo).resolve()
        self.evidence = evidence
        self.commands = dict(commands)
        self.timeouts = dict(timeouts or {})
        self.clock = clock
        self._runs: dict[str, dict[str, Any]] = {}

    # -- command execution ----------------------------------------------------------
    def run(self, command: str, *, purpose: str, timeout: int | None = None) -> dict[str, Any]:
        """Run once per evaluation (memoized), store the evidence and return it with its ``ref``."""
        if command in self._runs:
            return self._runs[command]
        record: dict[str, Any] = {"kind": "command", "command": command, "purpose": purpose, "cwd": ".",
                                  "workspace": self.workspace_fingerprint()}
        limit = int(timeout or DEFAULT_TIMEOUT)
        try:
            argv = shlex.split(command)
            if not argv:
                raise ValueError("empty command")
            env = {key: os.environ[key] for key in SAFE_ENV if key in os.environ}
            started = self.clock()
            completed = subprocess.run(argv, cwd=self.repo, capture_output=True, text=True, timeout=limit, check=False, env=env)
            record.update(exit_code=completed.returncode, outcome=PASS if completed.returncode == 0 else FAIL,
                          stdout_tail=_tail(completed.stdout), stderr_tail=_tail(completed.stderr),
                          duration_seconds=round(max(0.0, self.clock() - started), 3))
        except subprocess.TimeoutExpired:
            record.update(exit_code=None, outcome=INCONCLUSIVE, detail=f"timed out after {limit} s")
        except (OSError, ValueError) as error:
            record.update(exit_code=None, outcome=INCONCLUSIVE, detail=f"not runnable: {error}")
        record["ref"] = self.evidence.put(record)
        self._runs[command] = record
        return record

    def workspace_fingerprint(self) -> dict[str, Any]:
        """HEAD plus the content hash of every changed file: the exact state a command ran against."""
        head = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
        changed = self.changed_files()
        hashes = {}
        for path in changed:
            target = self.repo / path
            hashes[path] = (hashlib.sha256(target.read_bytes()).hexdigest()
                            if target.is_file() and not target.is_symlink() else None)
        return {"head": head.stdout.strip() or None, "changed": hashes}

    def changed_files(self) -> list[str]:
        completed = subprocess.run(["git", "-C", str(self.repo), "status", "--porcelain", "-z", "--untracked-files=all"],
                                   capture_output=True, check=False)
        entries = completed.stdout.decode("utf-8", "replace").split("\0")
        files: list[str] = []
        skip = False
        for entry in entries:
            if skip:
                skip = False
                continue
            if len(entry) < 4:
                continue
            if entry[0] in "RC":
                skip = True
            files.append(entry[3:])
        return sorted(set(files))

    # -- generic checks ------------------------------------------------------------
    def schema(self, action: str, value: Any, identifier: str = "SCHEMA_VALID") -> dict[str, Any]:
        """Structural validity only (JSON Schema, envelope, stage); semantics are separate criteria."""
        envelope, name = ("review_result", "REVIEW_RESULT_SCHEMA.json") if action == "REVIEW" else ("executor_result", "EXECUTOR_RESULT_SCHEMA.json")
        schema = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
        errors = [f"{'/'.join(str(part) for part in error.absolute_path) or '$'}: {error.message[:200]}"
                  for error in jsonschema.Draft202012Validator(schema).iter_errors(value)][:20]
        if not errors and action != "REVIEW" and value[envelope]["stage"]["value"] != action:
            errors.append(f"stage.value must be {action}")
        ref = self.evidence.put({"kind": "schema", "action": action, "errors": errors})
        return result(identifier, FAIL if errors else PASS, [ref], "; ".join(errors[:3]))

    def configured(self, gate: str, identifier: str) -> dict[str, Any]:
        command = self.commands.get(gate)
        ref = self.evidence.put({"kind": "configuration", "gate": gate, "command": command})
        if not command:
            return result(identifier, BLOCKED, [ref], f"no {gate} command is configured in policies/GATES.md")
        return result(identifier, PASS, [ref])

    def command_check(self, gate: str, identifier: str) -> dict[str, Any]:
        command = self.commands.get(gate)
        if not command:
            return result(identifier, BLOCKED, [], f"no {gate} command is configured")
        run = self.run(command, purpose=gate, timeout=self.timeouts.get(gate))
        detail = run.get("detail") or f"exit {run['exit_code']}"
        return result(identifier, run["outcome"], [run["ref"]], f"`{command}`: {detail}")

    def files_named_exist(self, gate: str, identifier: str) -> dict[str, Any]:
        """Every argument of the test command that looks like a repository file must exist (a missing test is not a pass)."""
        command = self.commands.get(gate)
        if not command:
            return result(identifier, BLOCKED, [], f"no {gate} command is configured")
        try:
            tokens = shlex.split(command)[1:]
        except ValueError:
            return result(identifier, FAIL, [], "the command cannot be parsed")
        named, skip_next = [], False
        for token in tokens:
            if skip_next:
                skip_next = False
                continue
            if token in ("-c", "-e", "--eval"):  # inline code, not a file
                skip_next = True
                continue
            if not token.startswith("-") and FILE_TOKEN.fullmatch(token):
                named.append(token)
        missing = [token for token in named if acceptance_verifiers.repo_relative_path_ok(token) and not (self.repo / token).exists()]
        ref = self.evidence.put({"kind": "files_exist", "command": command, "named": named, "missing": missing})
        return result(identifier, FAIL if missing else PASS, [ref], f"missing: {', '.join(missing)}" if missing else "")

    def reproduce_claims(self, artifact: dict[str, Any], identifier: str = "EVIDENCE_REPRODUCED") -> dict[str, Any]:
        """Re-run every command the worker reports as PASS that the gate is allowed to run; any mismatch is false evidence."""
        refs, mismatches, skipped = [], [], []
        allowed = {value for value in self.commands.values() if value}
        for entry in artifact.get("commands") or []:
            command = entry.get("command")
            if entry.get("result") != PASS or not command:
                continue
            if command not in allowed and not acceptance_verifiers.allowed_check_command(command, gate_commands=allowed):
                skipped.append(command)
                continue
            run = self.run(command, purpose="reproduce worker claim")
            refs.append(run["ref"])
            if run["outcome"] != PASS:
                mismatches.append(command)
        ref = self.evidence.put({"kind": "claims", "mismatches": mismatches, "not_reproducible": skipped})
        refs.append(ref)
        if mismatches:
            return result(identifier, FAIL, refs, "claimed PASS but failed when re-run: " + ", ".join(mismatches))
        return result(identifier, PASS, refs, f"{len(skipped)} claim(s) not re-runnable (not allowlisted)" if skipped else "")

    def workspace_claims(self, artifact: dict[str, Any], editable: list[str] | None) -> list[dict[str, Any]]:
        claimed = sorted({item["path"] for key in ("modified_paths", "created_paths") for item in artifact.get(key) or []})
        actual = self.changed_files()
        ref = self.evidence.put({"kind": "workspace", "claimed": claimed, "actual": actual, "editable": editable})
        mismatch = sorted(set(claimed) ^ set(actual))
        checks = [result("CLAIMS_MATCH_WORKSPACE", FAIL if mismatch else PASS, [ref],
                         f"claimed and actual differ: {', '.join(mismatch)}" if mismatch else "")]
        if editable is None:
            checks.append(result("WITHIN_EDITABLE_PATHS", BLOCKED, [ref], "no editable_paths given for this slice"))
        else:
            outside = [path for path in actual if not any(validate_protocol.path_matches(path, pattern) for pattern in editable)]
            checks.append(result("WITHIN_EDITABLE_PATHS", FAIL if outside else PASS, [ref],
                                 f"outside the slice: {', '.join(outside)}" if outside else ""))
        return checks

    def acceptance(self, checks: list[dict[str, Any]], bound: dict[str, str]) -> list[dict[str, Any]]:
        """One criterion per acceptance check: PASS only when its bound verifier passes when the gate runs it."""
        found: list[dict[str, Any]] = []
        allowed = {value for value in self.commands.values() if value}
        for check in checks:
            identifier = f"ACCEPTANCE:{check['id']}"
            if check.get("status") == "WAIVED" and check.get("waiver"):
                found.append(result(identifier, PASS, [self.evidence.put({"kind": "waiver", "check": check["id"], "waiver": check["waiver"]})],
                                    "waived by a recorded human decision"))
                continue
            if check.get("verifier") == "HUMAN":
                found.append(result(identifier, BLOCKED, [], "a HUMAN check needs a recorded human decision"))
                continue
            command = bound.get(check["id"])
            if not command or not (command in allowed or acceptance_verifiers.allowed_check_command(command, gate_commands=allowed)):
                found.append(result(identifier, FAIL, [], "no allowlisted verifier is bound; a textual claim is not proof"))
                continue
            run = self.run(command, purpose=f"acceptance {check['id']}")
            found.append(result(identifier, run["outcome"], [run["ref"]], f"`{command}`: exit {run['exit_code']}"))
        return found
