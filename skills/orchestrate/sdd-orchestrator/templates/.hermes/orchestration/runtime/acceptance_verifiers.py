"""Allowlist and binding for per-AC acceptance verifiers, plus doc-slice RED hints.

A PLAN/TASKS acceptance check may propose exactly one command in backticks in its
``verification_method``. The controller binds it only when it passes the allowlist
here; otherwise the check keeps the focused project gate as its verifier. The RED
hint for a document slice turns the bound ``rg``/``grep`` verifier into a command
that runs against ``git show HEAD:<path>`` and therefore fails before the change.
"""
from __future__ import annotations

import re
import shlex
from pathlib import PurePosixPath
from typing import Any, Callable, Iterable

#: File suffixes a document slice may edit; a slice whose editable paths are all
#: these gets a ready RED command per bound ``rg``/``grep`` verifier.
DOC_EXTENSIONS = (".md", ".mdx", ".rst", ".adoc", ".txt", ".yaml", ".yml")

#: Flags accepted on the ``rg``/``grep`` verifier shapes.
GREP_FLAGS = frozenset({
    "-n", "-q", "-F", "-i", "-c", "-w", "-x", "-e", "-E",
    "--fixed-strings", "--count", "--quiet",
})

#: Shell-composition characters refused in every built-in shape.
DANGEROUS_CHARACTERS = set(';&|<>$`\\\n')
#: Characters refused inside the free text of ``rg``/``grep`` patterns and path arguments.
ARGUMENT_FORBIDDEN_CHARACTERS = set('(){}*?')

_CITED_COMMAND = re.compile(r"`([^`]+)`")


def repo_relative_path_ok(path: str) -> bool:
    """A canonical repository-relative path: no absolute, home, empty, `.`, `..` or `-`-prefixed segment."""
    if not isinstance(path, str) or not path:
        return False
    if path.startswith(("/", "~")) or "\\" in path:
        return False
    return all(part not in {"", ".", ".."} and not part.startswith("-") for part in PurePosixPath(path).parts)


def extract_check_command(verification_method: str | None) -> str | None:
    """Return the single backtick command of a verification_method, or None."""
    matches = _CITED_COMMAND.findall(verification_method or "")
    if len(matches) != 1:
        return None
    return matches[0].strip()


def allowed_check_command(
    command: str,
    repo_paths_ok: Callable[[str], bool] = repo_relative_path_ok,
    *,
    gate_commands: Iterable[str] = (),
) -> bool:
    """True when ``command`` is one of the bound verifier shapes and never shell-composable."""
    if not isinstance(command, str) or not command.strip():
        return False
    if command.strip() in set(gate_commands):
        return True
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if not tokens:
        return False
    program, arguments = tokens[0], tokens[1:]

    if any(character in command for character in DANGEROUS_CHARACTERS):
        return False

    def clean_argument(value: str) -> bool:
        return not any(character in value for character in ARGUMENT_FORBIDDEN_CHARACTERS)

    if program in {"rg", "grep"}:
        flags = [item for item in arguments if item.startswith("-")]
        rest = [item for item in arguments if not item.startswith("-")]
        if any(item not in GREP_FLAGS for item in flags):
            return False
        if len(rest) < 2 or not clean_argument(rest[0]):
            return False
        return all(repo_paths_ok(item) and clean_argument(item) for item in rest[1:])
    if program == "test":
        return len(arguments) == 2 and arguments[0] in {"-f", "-s", "-d"} and repo_paths_ok(arguments[1]) and clean_argument(arguments[1])
    return False


def bind_check_commands(
    acceptance: dict[str, dict[str, Any]],
    *,
    repo_paths_ok: Callable[[str], bool] = repo_relative_path_ok,
    gate_commands: Iterable[str] = (),
) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Return (bound check commands, refused proposals) for a folded acceptance mapping."""
    gate_set = set(gate_commands)
    check_commands: dict[str, str] = {}
    refused: list[dict[str, str]] = []
    for check_id in sorted(acceptance):
        proposed = extract_check_command(acceptance[check_id].get("verification_method"))
        if proposed is None:
            continue
        if allowed_check_command(proposed, repo_paths_ok=repo_paths_ok, gate_commands=gate_set):
            check_commands[check_id] = proposed
        else:
            refused.append({"id": check_id, "command": proposed, "reason": "not allowlisted"})
    return check_commands, refused


def fold_plan_tasks(
    delivery: dict[str, Any],
    *,
    stage: str,
    profile: str,
    impact_files: Iterable[str],
    approve_slices: Callable[[list[str]], list[str]],
    gate_commands: Iterable[str] = (),
    doc_slice: str = "DOC",
    slice_order: Iterable[str] = (),
) -> None:
    """Bind PLAN/TASKS acceptance commands and replace slice state from impact files.

    ``slice_order`` is the TASKS ``tdd_slices`` id order. IMPLEMENT runs slices in that
    order; acceptance checks only map criteria to slices, and their list order (often
    string-sorted: AC1, AC10, AC2...) must never decide which slice runs first.
    """
    if stage in {"PLAN", "TASKS"}:
        check_commands, refused = bind_check_commands(delivery.get("acceptance") or {}, gate_commands=gate_commands)
        delivery["check_commands"] = check_commands
        delivery["check_commands_refused"] = refused
    if stage == "PLAN" and profile == "DECISION_DOC":
        for check in delivery.get("acceptance", {}).values():
            check["slice_id"] = doc_slice
    if stage == "TASKS" or (stage == "PLAN" and profile == "DECISION_DOC"):
        import plan_checks

        editable_paths, normalizations = plan_checks.normalize_editable_paths(impact_files)
        assigned = list(dict.fromkeys(check["slice_id"] for check in delivery.get("acceptance", {}).values() if check.get("slice_id")))
        declared = [item for item in dict.fromkeys(slice_order) if item in assigned] if stage == "TASKS" else []
        planned = [*declared, *(item for item in assigned if item not in declared)]
        delivery["slices"] = {"planned": planned, "completed": [], "editable_paths": editable_paths}
        delivery["editable_path_normalizations"] = normalizations
        delivery["approved_slice_sha256s"] = approve_slices(planned)


def is_doc_path(pattern: str) -> bool:
    """True when a glob pattern names only document files (by its final segment suffix)."""
    if not isinstance(pattern, str) or not pattern:
        return False
    name = pattern.rsplit("/", 1)[-1]
    if not name or name == "**":
        return False
    return any(name.endswith(extension) for extension in DOC_EXTENSIONS)


def is_doc_only_slice(editable_paths: Iterable[str]) -> bool:
    """True when every editable path of the slice is a document file."""
    paths = list(editable_paths)
    return bool(paths) and all(is_doc_path(path) for path in paths)


def doc_red_command(command: str) -> str | None:
    """Turn a bound ``rg``/``grep`` verifier into ``git show HEAD:<path> | <verifier>``."""
    if not isinstance(command, str):
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if len(tokens) < 3 or tokens[0] not in {"rg", "grep"}:
        return None
    if len(tokens) < 3 or tokens[-1].startswith("-") or sum(not token.startswith("-") for token in tokens[1:]) < 2:
        return None
    path = tokens[-1]
    return f"git show {shlex.quote(f'HEAD:{path}')} | {shlex.join(tokens[:-1])}"


def doc_red_commands(
    check_commands: dict[str, str],
    editable_paths: Iterable[str],
    profile: str,
) -> dict[str, str]:
    """Per-AC RED commands for a DECISION_DOC demand or a document-only slice."""
    if profile != "DECISION_DOC" and not is_doc_only_slice(editable_paths):
        return {}
    red: dict[str, str] = {}
    for check_id in sorted(check_commands):
        command = doc_red_command(check_commands[check_id])
        if command:
            red[check_id] = command
    return red
