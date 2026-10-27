"""DeterministicValidator: real commands and artifact checks for each SDD gate.

Commands come only from the caller's configuration (the GATES.md rows) or from
verifiers that ``acceptance_verifiers.allowed_check_command`` accepts. They run
without a shell (``shlex.split``), in the repository, with a timeout and a minimal
environment; stdout and stderr go to temporary files, and only a bounded window of
their end is redacted and stored as evidence. A worker's own report is never trusted:
a command it claims passed is re-run, its file lists are compared with ``git status``
(``gate_workspace``), and its acceptance list and waivers are checked against the
controller-owned context.

Each check returns ``{"id", "status", "refs", "detail"}`` with status PASS, FAIL,
BLOCKED or INCONCLUSIVE (a timeout or an unrunnable command is INCONCLUSIVE, never PASS).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import jsonschema

import acceptance_verifiers
import gate_env
import gate_workspace
import redaction
import validate_protocol
from gate_evidence import EvidenceCollector

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
OUTPUT_LIMIT = 4000
#: Bytes read back from the end of an output file: the tail plus room for redaction to see a whole secret.
OUTPUT_WINDOW = OUTPUT_LIMIT * 4 + 4096
DEFAULT_TIMEOUT = 300
#: At most this many distinct worker-claimed commands are re-run per evaluation.
MAX_REPRODUCED = 10
#: A command argument that names a repository file: path characters only, with an extension or a directory part.
FILE_TOKEN = re.compile(r"(?:[\w.-]+/)*[\w-][\w.-]*\.[A-Za-z0-9]+|(?:[\w.-]+/)+[\w.-]+")
PASS, FAIL, BLOCKED, INCONCLUSIVE = "PASS", "FAIL", "BLOCKED", "INCONCLUSIVE"
#: Environment variables passed to gate commands; nothing else from the caller's environment.
SAFE_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT", "PYTHONPATH", "VIRTUAL_ENV")


def result(identifier: str, status: str, refs: list[str] | None = None, detail: str = "", *, transient: bool = False) -> dict[str, Any]:
    """A criterion result; ``transient`` marks an INCONCLUSIVE caused by a timeout or an environment failure."""
    found = {"id": identifier, "status": status, "refs": list(refs or []), "detail": detail}
    if transient:
        found["transient"] = True
    return found


def _argv_key(command: str) -> tuple[str, ...]:
    """A command's identity for de-duplication: its argv, with ``./`` path prefixes removed."""
    try:
        return tuple(token[2:] if token.startswith("./") else token for token in shlex.split(command))
    except ValueError:
        return (command,)


def _run_result(identifier: str, run: dict[str, Any], command: str) -> dict[str, Any]:
    detail = run.get("detail") or f"exit {run['exit_code']}"
    return result(identifier, run["outcome"], [run["ref"]], f"`{command}`: {detail}", transient=run["outcome"] == INCONCLUSIVE)


def _tail(handle: Any) -> str:
    """Redact the end window first, then cut, so a secret straddling the cut is never stored half-masked."""
    handle.seek(0, os.SEEK_END)
    size = handle.tell()
    handle.seek(max(0, size - OUTPUT_WINDOW))
    text = handle.read().decode("utf-8", "replace")
    if size > OUTPUT_WINDOW and "\n" in text:
        text = text.split("\n", 1)[1]
    return redaction.redact(text)[-OUTPUT_LIMIT:]


class DeterministicValidator:
    def __init__(self, repo: Path, evidence: EvidenceCollector, *, commands: dict[str, str | None],
                 timeouts: dict[str, int] | None = None, clock: Callable[[], float] = time.time, base: str | None = None) -> None:
        self.repo = Path(repo).resolve()
        self.base = base
        self.evidence = evidence
        self.commands = dict(commands)
        self.timeouts = dict(timeouts or {})
        self.clock = clock
        self._runs: dict[str, dict[str, Any]] = {}

    @property
    def allowed(self) -> set[str]:
        return {value for value in self.commands.values() if value}

    def permitted(self, command: Any) -> bool:
        return isinstance(command, str) and bool(command) and (
            command in self.allowed or acceptance_verifiers.allowed_check_command(command, gate_commands=self.allowed))

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
            env = {key: value for key in SAFE_ENV if (value := os.environ.get(key)) is not None}
            # A fresh, gate-owned bytecode cache (``gate_env``): the command neither loads bytecode planted in the
            # repository's caches nor writes any there, so it cannot change the state it is evaluated on.
            with gate_env.isolated(env) as env, tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
                started = self.clock()
                completed = subprocess.run(argv, cwd=self.repo, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                           timeout=limit, check=False, env=env)
                record.update(exit_code=completed.returncode, outcome=PASS if completed.returncode == 0 else FAIL,
                              stdout_tail=_tail(out), stderr_tail=_tail(err),
                              duration_seconds=round(max(0.0, self.clock() - started), 3))
        except subprocess.TimeoutExpired:
            record.update(exit_code=None, outcome=INCONCLUSIVE, detail=f"timed out after {limit} s")
        except (OSError, ValueError) as error:
            record.update(exit_code=None, outcome=INCONCLUSIVE, detail=f"not runnable: {error}")
        record["ref"] = self.evidence.put(record)
        self._runs[command] = record
        return record

    def workspace_fingerprint(self) -> dict[str, Any]:
        return gate_workspace.fingerprint(self.repo, self.base)

    def changed_files(self) -> list[str] | None:
        return gate_workspace.changed_files(self.repo, self.base)

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
        return _run_result(identifier, self.run(command, purpose=gate, timeout=self.timeouts.get(gate)), command)

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
        """Re-run claimed passing commands the gate may run (at most ``MAX_REPRODUCED``); a mismatch is false evidence."""
        refs, mismatches, unsettled, skipped, capped, seen = [], [], [], [], [], set()
        entries = [entry for entry in artifact.get("commands") or [] if isinstance(entry, dict)
                   and isinstance(entry.get("command"), str) and entry.get("result") == PASS]
        # A claim whose argv matches a configured gate command is that command: it is re-run first, under the
        # configured spelling, so neither cheap claims nor a non-allowlisted spelling can hide a false claim about it.
        configured = {_argv_key(value): value for value in sorted(self.allowed)}
        entries.sort(key=lambda entry: _argv_key(entry["command"]) not in configured)
        for entry in entries:
            key = _argv_key(entry["command"])
            command = configured.get(key, entry["command"])
            if key in seen:
                continue
            if not self.permitted(command):
                skipped.append(command)
                continue
            seen.add(key)
            if len(refs) >= MAX_REPRODUCED:
                capped.append(command)
            else:
                run = self.run(command, purpose="reproduce worker claim")
                refs.append(run["ref"])
                if run["outcome"] == FAIL:
                    mismatches.append(command)
                elif run["outcome"] != PASS:
                    unsettled.append(command)
        refs.append(self.evidence.put({"kind": "claims", "mismatches": mismatches, "not_reproducible": skipped[:50],
                                       "over_cap": capped[:50]}))
        if mismatches:
            return result(identifier, FAIL, refs, "claimed PASS but failed when re-run: " + ", ".join(mismatches))
        if unsettled:
            return result(identifier, INCONCLUSIVE, refs, "re-run did not finish: " + ", ".join(unsettled), transient=True)
        if capped:
            return result(identifier, INCONCLUSIVE, refs, f"{len(capped)} claimed passing command(s) over the re-run cap of {MAX_REPRODUCED}")
        return result(identifier, PASS, refs, f"{len(skipped)} claim(s) not re-runnable (not allowlisted)" if skipped else "")

    def workspace_claims(self, artifact: dict[str, Any], editable: list[str] | None) -> list[dict[str, Any]]:
        claimed = sorted({item["path"] for key in ("modified_paths", "created_paths") for item in artifact.get(key) or []})
        found = gate_workspace.scan(self.repo, self.base)
        actual = found["changed"]
        ref = self.evidence.put({"kind": "workspace", "claimed": claimed, "actual": actual, "editable": editable,
                                 "base": self.base, "reason": found.get("reason")})
        if found.get("reason") in ("invalid base", "submodules are not supported"):
            detail = ("base_commit must be the full object id of an ancestor of HEAD" if found["reason"] == "invalid base"
                      else "the repository has submodules, whose content the gate cannot compare")
            return [result("CLAIMS_MATCH_WORKSPACE", BLOCKED, [ref], detail), result("WITHIN_EDITABLE_PATHS", BLOCKED, [ref], detail)]
        if actual is None:
            detail = f"the workspace cannot be read ({found.get('reason', 'unknown')})"
            return [result("CLAIMS_MATCH_WORKSPACE", INCONCLUSIVE, [ref], detail, transient=True),
                    result("WITHIN_EDITABLE_PATHS", INCONCLUSIVE, [ref], detail, transient=True)]
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

    def acceptance(self, checks: list[dict[str, Any]], context: dict[str, Any], expected: list[str] | None = None) -> list[dict[str, Any]]:
        """One criterion per acceptance check the artifact lists or the controller expects.

        PASS only when the check's controller-bound verifier (``context.check_commands``)
        passes when the gate runs it, or when the controller recorded a waiver for it
        (``context.waivers``). A waiver written only in the artifact is BLOCKED, and a check
        the controller expects (``expected``, ``context.expected_check_ids``, bound or waived
        ids) that the artifact leaves out is a FAIL.
        """
        bound = context.get("check_commands") or {}
        waivers = context.get("waivers") or {}
        listed = {check["id"]: check for check in checks}
        wanted = set(expected or ()) | set(context.get("expected_check_ids") or ()) | set(bound) | set(waivers)
        found: list[dict[str, Any]] = []
        for check_id in sorted(wanted - set(listed)):
            ref = self.evidence.put({"kind": "acceptance_missing", "check": check_id, "listed": sorted(listed)})
            found.append(result(f"ACCEPTANCE:{check_id}", FAIL, [ref], "expected by the controller but missing from the artifact"))
        for check_id, check in listed.items():
            identifier = f"ACCEPTANCE:{check_id}"
            if check_id in waivers:
                ref = self.evidence.put({"kind": "waiver", "check": check_id, "waiver": waivers[check_id], "source": "controller"})
                found.append(result(identifier, PASS, [ref], "waived by a controller-recorded decision"))
            elif check.get("status") == "WAIVED":
                found.append(result(identifier, BLOCKED, [], "the artifact claims a waiver the controller did not record"))
            elif check.get("verifier") == "HUMAN":
                found.append(result(identifier, BLOCKED, [], "a HUMAN check needs a recorded human decision"))
            elif not self.permitted(bound.get(check_id)):
                found.append(result(identifier, FAIL, [], "no allowlisted verifier is bound; a textual claim is not proof"))
            else:
                found.append(_run_result(identifier, self.run(bound[check_id], purpose=f"acceptance {check_id}"), bound[check_id]))
        return found
