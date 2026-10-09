"""Project onboarding record (PROJECT_SETUP.md) parsing and rendering."""
from __future__ import annotations

from pathlib import Path

from .constants import CONFIG_ROOT, TYPESAFE_ENV_PATH, TYPESAFE_OFFICIAL_COMMAND, TYPESAFE_SKILL_PATH
from .errors import InstallError, _load_unique_json
from .fsops import _overwrite_project_file_nofollow, _read_project_regular_snapshot, reject_symlinks
from .mode import MODE
from .typesafe import _typesafe_env_ready, typesafe_skill_status


def _answer_state(question_id: str, value: str) -> tuple[bool, bool]:
    normalized = value.strip()
    if normalized.casefold() in {"", "unresolved", "null", "~", "{}", "[]"}:
        return True, True
    if normalized.casefold() == "none":
        return False, True
    try:
        decoded = _load_unique_json(normalized)
    except (ValueError, TypeError):
        return True, False
    if isinstance(decoded, str):
        marker = decoded.strip().casefold()
        if marker in {"unresolved", "null", "~", "{}", "[]"}:
            return True, True
        return (False, True) if marker == "none" else (True, False)

    def nonempty(item: object) -> bool:
        return isinstance(item, str) and bool(item.strip())

    if question_id == "issue_tracker":
        valid = (
            isinstance(decoded, dict)
            and set(decoded) == {"provider", "project", "read", "write"}
            and nonempty(decoded["provider"])
            and nonempty(decoded["project"])
            and isinstance(decoded["read"], bool)
            and isinstance(decoded["write"], bool)
        )
    elif question_id == "obsidian":
        valid = (
            isinstance(decoded, dict)
            and set(decoded) == {"vault", "project_container"}
            and nonempty(decoded["vault"])
            and Path(decoded["vault"]).is_absolute()
            and nonempty(decoded["project_container"])
            and not Path(decoded["project_container"]).is_absolute()
            and ".." not in Path(decoded["project_container"]).parts
        )
    elif question_id == "typesafe_ai":
        valid = (
            isinstance(decoded, dict)
            and set(decoded) == {"install", "automatic_semantic_governance"}
            and decoded["install"] is True
            and isinstance(decoded["automatic_semantic_governance"], bool)
        )
    elif question_id == "project_tools":
        valid = isinstance(decoded, list) and bool(decoded) and all(
            isinstance(tool, dict)
            and set(tool) == {"tool", "purpose", "read", "write"}
            and nonempty(tool["tool"])
            and nonempty(tool["purpose"])
            and isinstance(tool["read"], bool)
            and isinstance(tool["write"], bool)
            for tool in decoded
        )
    else:
        valid = False
    return (False, True) if valid else (True, False)


def _unresolved_answer(question_id: str, value: str) -> bool:
    unresolved, valid = _answer_state(question_id, value)
    return unresolved or not valid


def _read_onboarding_record(target: Path, question_ids: set[str]) -> tuple[dict[str, str], bool, str | None, list[str]]:
    path = target / CONFIG_ROOT / "PROJECT_SETUP.md"
    if not path.is_file():
        return {}, False, None, ["RECORD_MISSING"]
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeError:
        return {}, False, None, ["INVALID_ENCODING"]

    try:
        start = lines.index("```yaml") + 1
        end = lines.index("```", start)
    except ValueError:
        return {}, False, None, ["YAML_BLOCK_INVALID"]

    metadata: dict[str, str] = {}
    answers: dict[str, str] = {}
    issues: list[str] = []
    in_answers = False
    for line in lines[start:end]:
        if not line.strip():
            continue
        if in_answers and line.startswith("  "):
            if line.startswith("    ") or ":" not in line:
                issues.append("ANSWER_LINE_INVALID")
                continue
            key, value = line.strip().split(":", 1)
            if key not in question_ids:
                issues.append("ANSWER_KEY_UNKNOWN")
            elif key in answers:
                issues.append("ANSWER_KEY_DUPLICATE")
            else:
                answers[key] = value.strip()
            continue
        in_answers = False
        if line.startswith(" ") or ":" not in line:
            issues.append("SETUP_LINE_INVALID")
            continue
        key, value = line.split(":", 1)
        if key not in {"schema_version", "status", "answers"}:
            issues.append("SETUP_KEY_UNKNOWN")
        elif key in metadata:
            issues.append("SETUP_KEY_DUPLICATE")
        else:
            metadata[key] = value.strip()
            in_answers = key == "answers" and not value.strip()

    if metadata.get("schema_version") != "1":
        issues.append("SCHEMA_VERSION_UNSUPPORTED")
    if metadata.get("status") not in {"PENDING", "COMPLETE"}:
        issues.append("STATUS_INVALID")
    if metadata.get("answers") != "":
        issues.append("ANSWERS_SECTION_INVALID")
    missing = question_ids - set(answers)
    if missing:
        issues.append("ANSWERS_MISSING")
    for key, value in answers.items():
        _, valid = _answer_state(key, value)
        if not valid:
            issues.append("ANSWER_VALUE_INVALID")
    all_resolved = not missing and all(not _unresolved_answer(key, answers[key]) for key in question_ids)
    expected_status = "COMPLETE" if all_resolved else "PENDING"
    if metadata.get("status") in {"PENDING", "COMPLETE"} and metadata["status"] != expected_status:
        issues.append("STATUS_ANSWER_MISMATCH")

    non_structural = {"ANSWERS_MISSING", "ANSWER_VALUE_INVALID", "STATUS_ANSWER_MISMATCH"}
    structural_issues = set(issues) - non_structural
    if structural_issues:
        answers = {}
    return answers, not issues, metadata.get("status"), sorted(set(issues))


def onboarding_questions(
    target: Path | None = None,
    typesafe_choice: str | None = None,
    automatic_jev_governance: bool = False,
) -> dict[str, object]:
    """Return only unresolved project-local questions after installation."""
    # `choices` is what a controller offers the user, `none` always first so a
    # project without the integration is never pushed into a follow-up question.
    questions = [
        {
            "id": "issue_tracker",
            "prompt": "Which issue tracker should the orchestrator read, and may it create or update issues? Answer `none` if the project has no tracker.",
            "choices": ["none", "read only", "read and write"],
            "accepted_answers": ["JSON object with provider, project, read, and write", "none"],
        },
        {
            "id": "obsidian",
            "prompt": "Should the orchestrator connect this project to an Obsidian vault? Answer `none` to skip.",
            "choices": ["none", "connect a vault"],
            "accepted_answers": ["JSON object with absolute vault and relative project_container", "none"],
        },
        {
            "id": "typesafe_ai",
            "prompt": "Should the orchestrator install TypeSafe, and may it send automatic, potentially billed semantic classifications to Jev? Answer `none` to skip TypeSafe.",
            "choices": ["none", "install without automatic Jev", "install with automatic Jev"],
            "accepted_answers": [
                "JSON object with install true and automatic_semantic_governance true or false",
                "none",
            ],
        },
        {
            "id": "project_tools",
            "prompt": "Which other project-specific tools must the orchestrator use, and with what permissions? Answer `none` if there are none. (Approvers for HUMAN acceptance checks default to the requester; edit the `approvers` JSON block in PROJECT_SETUP.md only if someone else approves.)",
            "choices": ["none", "list tools"],
            "accepted_answers": ["JSON array of tool, purpose, read, and write objects", "none"],
        },
    ]
    all_questions = list(questions)
    answers: dict[str, str] = {}
    record_valid = False
    record_status: str | None = None
    record_issues = ["RECORD_MISSING"]
    if target:
        answers, record_valid, record_status, record_issues = _read_onboarding_record(
            target, {str(question["id"]) for question in questions}
        )
        questions = [
            question
            for question in questions
            if _unresolved_answer(str(question["id"]), answers.get(str(question["id"]), "UNRESOLVED"))
        ]
    typesafe = typesafe_skill_status(target) if target else {
        "status": "NOT_CHECKED",
        "skill_path": TYPESAFE_SKILL_PATH,
        "lock_path": MODE.typesafe_lock_path,
        "lock_entry": "UNKNOWN",
        "issue": None,
        "official_command": list(TYPESAFE_OFFICIAL_COMMAND),
        "env_path": TYPESAFE_ENV_PATH,
        "env_status": "NOT_CHECKED",
        "env_issue": None,
    }
    recorded_typesafe = answers.get("typesafe_ai", "")
    try:
        recorded_value = _load_unique_json(recorded_typesafe)
    except (ValueError, TypeError):
        recorded_value = None
    recorded_install = (
        isinstance(recorded_value, dict)
        and set(recorded_value) == {"install", "automatic_semantic_governance"}
        and recorded_value.get("install") is True
        and isinstance(recorded_value.get("automatic_semantic_governance"), bool)
    )
    recorded_automatic = bool(
        isinstance(recorded_value, dict)
        and recorded_install
        and recorded_value["automatic_semantic_governance"] is True
    )
    recorded_none = recorded_typesafe.strip().casefold() == "none"
    integration_issues: list[str] = []
    if recorded_install:
        typesafe_healthy = typesafe["status"] == "INSTALLED" and _typesafe_env_ready(typesafe)
    elif recorded_none:
        typesafe_healthy = typesafe["status"] == "NOT_INSTALLED"
    else:
        typesafe_healthy = True
    if not typesafe_healthy:
        integration_issues.append("TYPESAFE_INTEGRATION_STATE_MISMATCH")
        unresolved = {str(question["id"]) for question in questions}
        unresolved.add("typesafe_ai")
        questions = [question for question in all_questions if str(question["id"]) in unresolved]
    complete = record_valid and record_status == "COMPLETE" and not questions and not integration_issues
    if typesafe_choice == "install" and typesafe["env_status"] == "CONFLICT":
        typesafe["planned_action"] = "BLOCKED"
    elif typesafe_choice == "install":
        if typesafe["status"] == "INSTALLED":
            typesafe["planned_action"] = (
                "NONE"
                if (
                    recorded_install
                    and recorded_automatic == automatic_jev_governance
                    and _typesafe_env_ready(typesafe)
                )
                else "RECORD"
            )
        else:
            typesafe["planned_action"] = "INSTALL"
    elif typesafe_choice == "none":
        if typesafe["status"] == "NOT_INSTALLED":
            typesafe["planned_action"] = "NONE" if recorded_none else "RECORD_NONE"
        else:
            typesafe["planned_action"] = "BLOCKED"
    else:
        typesafe["planned_action"] = "NONE"
    typesafe["planned_env_action"] = (
        "CREATE"
        if MODE.credential_file_managed and typesafe_choice == "install" and typesafe["env_status"] == "ABSENT"
        else "NONE"
    )
    typesafe["automatic_semantic_governance"] = recorded_automatic
    typesafe["planned_automatic_semantic_governance"] = (
        automatic_jev_governance if typesafe_choice == "install" else recorded_automatic
    )
    return {
        "status": "COMPLETE" if complete else "REQUIRED",
        "scope": "ORCHESTRATOR_ONLY",
        "ask_only_unresolved": True,
        "record_valid": record_valid,
        "record_status": record_status,
        "record_issues": record_issues,
        "integration_issues": integration_issues,
        "questions": questions,
        "integrations": {"typesafe_ai": typesafe},
    }


def _require_typesafe_record_target(target: Path) -> None:
    question_ids = {"issue_tracker", "obsidian", "typesafe_ai", "project_tools"}
    answers, valid, _, issues = _read_onboarding_record(target, question_ids)
    if valid:
        return
    legacy_keys = question_ids - {"typesafe_ai"}
    legacy_issues = {"ANSWERS_MISSING", "STATUS_ANSWER_MISMATCH"}
    if set(answers) == legacy_keys and set(issues).issubset(legacy_issues):
        return
    legacy_value = answers.get("typesafe_ai", "")
    try:
        legacy_typesafe = _load_unique_json(legacy_value) == {"install": True}
    except (ValueError, TypeError):
        legacy_typesafe = False
    if (
        set(answers) == question_ids
        and legacy_typesafe
        and set(issues).issubset({"ANSWER_VALUE_INVALID", "STATUS_ANSWER_MISMATCH"})
    ):
        return
    reason = issues[0] if issues else "UNKNOWN"
    raise InstallError(f"ONBOARDING_RECORD_INVALID: {reason}")


def _render_onboarding_answer(
    target: Path,
    question_id: str,
    value: str,
    source: bytes | None = None,
) -> bytes:
    path = target / CONFIG_ROOT / "PROJECT_SETUP.md"
    reject_symlinks(target, f"{CONFIG_ROOT}/PROJECT_SETUP.md")
    raw = path.read_bytes() if source is None else source
    lines = raw.decode("utf-8").splitlines()
    prefix = f"  {question_id}:"
    matches = [index for index, line in enumerate(lines) if line.startswith(prefix)]
    if len(matches) > 1:
        raise InstallError(f"ONBOARDING_RECORD_INVALID: {question_id}")
    if matches:
        lines[matches[0]] = f"  {question_id}: {value}"
    elif question_id == "typesafe_ai":
        insertion_points = [index for index, line in enumerate(lines) if line.startswith("  project_tools:")]
        if len(insertion_points) != 1:
            raise InstallError(f"ONBOARDING_RECORD_INVALID: {question_id}")
        lines.insert(insertion_points[0], f"  {question_id}: {value}")
    else:
        raise InstallError(f"ONBOARDING_RECORD_INVALID: {question_id}")

    question_ids = {"issue_tracker", "obsidian", "typesafe_ai", "project_tools"}
    answers: dict[str, str] = {}
    for line in lines:
        if line.startswith("  ") and not line.startswith("    ") and ":" in line:
            key, answer = line.strip().split(":", 1)
            if key in question_ids:
                answers[key] = answer.strip()
    resolved = set(answers) == question_ids and all(
        not _unresolved_answer(key, answers[key]) for key in question_ids
    )
    status_lines = [index for index, line in enumerate(lines) if line.startswith("status:")]
    if len(status_lines) != 1:
        raise InstallError("ONBOARDING_RECORD_INVALID: status")
    lines[status_lines[0]] = f"status: {'COMPLETE' if resolved else 'PENDING'}"
    return ("\n".join(lines) + "\n").encode("utf-8")


def _write_onboarding_answer(target: Path, question_id: str, value: str) -> None:
    relative = f"{CONFIG_ROOT}/PROJECT_SETUP.md"
    snapshot = _read_project_regular_snapshot(target, relative)
    if snapshot is None:
        raise InstallError("ONBOARDING_RECORD_MISSING")
    before, owned = snapshot
    content = _render_onboarding_answer(target, question_id, value, source=before)
    _overwrite_project_file_nofollow(target, relative, owned, content, expected=before)
