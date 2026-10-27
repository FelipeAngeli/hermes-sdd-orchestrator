"""Whether one controller runtime is idle enough for ``--upgrade``.

An upgrade replaces runtime files only; it never writes STATE, the action journal or a
workspace-audit baseline. It still refuses a busy runtime (``UPGRADE_CONTROLLER_BUSY``): an
``ACTION_JOURNAL.json`` that is not ``IDLE``/``RELEASED``, a STATE whose
``loop.control.loop_active`` is true or whose ``stage.status`` is ``RUNNING``, or a STATE that
cannot be parsed.

One open action is exempt: an IMPLEMENT/TEST result held by the opt-in workspace audit. A
stopped audit leaves the journal ``ARTIFACT_READY`` and STATE ``RUNNING`` in that stage, so a
controller fix for the audit could never be installed over the action it has to re-audit.
The runtime counts as audit-held only when **all** of these hold, read from the controller's
own files (the Obsidian container or the local controller, never the worker's output):

* the journal is ``ARTIFACT_READY`` for an ``IMPLEMENT``/``TEST`` action: its process
  finished and its artifact was recorded, so no worker is running;
* that artifact is not validated (``validation_status`` ``PENDING`` or ``INVALID``) and no
  STATE commit was prepared or made, so nothing of it reached STATE;
* STATE is ``RUNNING`` in that same stage for that same ticket, records
  ``delivery.workspace_audit`` with ``mode: enforce`` and ``action_id`` equal to the journal
  action, and has no active bounded loop.

Whether the audit currently reports findings is not evaluated here: that would run the
installed runtime's code from the installer, and ``sdd.py next`` re-audits the action with
the new runtime anyway, either accepting it or stopping again. Standard library only.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .errors import _load_unique_json

BUSY_FREE_STATUSES = frozenset({"IDLE", "RELEASED"})
AUDITED_STAGES = frozenset({"IMPLEMENT", "TEST"})
UNVALIDATED = frozenset({"PENDING", "INVALID"})
_COMMIT_FIELDS = ("expected_after_hash", "committed_after_hash", "committed_at")


def _mapping(value: Any, key: str) -> dict:
    found = value.get(key) if isinstance(value, dict) else None
    return found if isinstance(found, dict) else {}


def state_busy_reasons(data: Any) -> list[str]:
    """Why a parsed STATE payload blocks an upgrade."""
    if not isinstance(data, dict):
        return ["STATE unreadable (payload is not a mapping)"]
    reasons: list[str] = []
    loop = _mapping(data, "loop")
    if _mapping(loop, "control").get("loop_active") is True or loop.get("loop_active") is True:
        reasons.append("loop_active true")
    stage = _mapping(data, "stage")
    if stage.get("status") == "RUNNING":
        reasons.append(f"stage RUNNING ({stage.get('current')})")
    return reasons


def audit_held(journal: Any, state: Any) -> bool:
    """True when the only open thing is an IMPLEMENT/TEST result held by the workspace audit."""
    action, artifact, commit = (_mapping(journal, key) for key in ("action", "artifact", "state_commit"))
    stage, record, loop = _mapping(state, "stage"), _mapping(_mapping(state, "delivery"), "workspace_audit"), _mapping(state, "loop")
    return (
        action.get("status") == "ARTIFACT_READY"
        and action.get("stage") in AUDITED_STAGES
        and isinstance(action.get("id"), str) and bool(action["id"])
        and artifact.get("exists") is True
        and artifact.get("validation_status") in UNVALIDATED
        and commit.get("verified") is False
        and all(commit.get(field) is None for field in _COMMIT_FIELDS)
        and stage.get("status") == "RUNNING"
        and stage.get("current") == action["stage"]
        and _mapping(state, "ticket").get("id") == action.get("ticket")
        and record.get("mode") == "enforce"
        and record.get("action_id") == action["id"]
        and _mapping(loop, "control").get("loop_active") is not True
        and loop.get("loop_active") is not True
    )


def runtime_busy_reasons(directory: Path, parse: Callable[[str], Any]) -> list[str]:
    """Every reason one runtime directory blocks an upgrade; ``parse`` reads a STATE payload."""
    busy: list[str] = []
    journal_path, state_path = directory / "ACTION_JOURNAL.json", directory / "STATE.md"
    journal: Any = None
    status = "IDLE"
    if journal_path.is_file() and not journal_path.is_symlink():
        try:
            journal = _load_unique_json(journal_path.read_text(encoding="utf-8"))
            status = journal["action"]["status"]
        except (OSError, UnicodeError, ValueError, KeyError, TypeError):
            journal, status = None, "UNREADABLE"
    state: Any = None
    state_reasons: list[str] = []
    if state_path.is_symlink():
        state_reasons.append("STATE unreadable (symlink)")
    elif state_path.is_file():
        try:
            state = parse(state_path.read_text(encoding="utf-8"))
        except Exception as error:  # noqa: BLE001 - any read or parse failure must block, never pass
            state_reasons.append(f"STATE unreadable ({error})")
        else:
            state_reasons.extend(state_busy_reasons(state))
    if status not in BUSY_FREE_STATUSES and audit_held(journal, state):
        return []
    if status not in BUSY_FREE_STATUSES:
        busy.append(f"{journal_path}: action {status}")
    busy.extend(f"{state_path}: {reason}" for reason in state_reasons)
    return busy
