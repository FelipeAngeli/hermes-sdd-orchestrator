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

#: Shell-composition characters refused in every non-pinned shape (pinned one-liners
#: may contain ``;``, ``(``, ``)`` only inside their quoted, strictly-matched argument).
DANGEROUS_CHARACTERS = set(';&|<>$`\\\n')
#: Characters refused inside the free text of ``rg``/``grep`` patterns and path arguments.
ARGUMENT_FORBIDDEN_CHARACTERS = set('(){}*?')

#: ``npm``/``pnpm``/``yarn`` scripts must match this and end in a read-only task.
SCRIPT_RE = re.compile(r"^[a-z][a-z0-9:_-]{0,40}$")
READ_ONLY_TAILS = ("format:check", "lint", "typecheck", "test", "test:coverage")

#: Strict shapes for the three language one-liners; the path is extracted and checked.
PYTHON_RE = re.compile(r"^import yaml,sys; yaml\.safe_load\(open\('([^']+)'\)\)$")
RUBY_RE = re.compile(r"^YAML\.load_file\('([^']+)'\)$")
NODE_RE = re.compile(r"^JSON\.parse\(require\('fs'\)\.readFileSync\('([^']+)','utf8'\)\)$")

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

    # Pinned one-liner shapes first: their quoted argument may contain `;`, `(` and
    # `)` that are not shell syntax; the strict regex pins the only accepted value.
    if program == "python3":
        return len(arguments) == 2 and arguments[0] == "-c" and _strict_path(arguments[1], PYTHON_RE, repo_paths_ok)
    if program == "ruby":
        return len(arguments) == 3 and arguments[0] == "-ryaml" and arguments[1] == "-e" and _strict_path(arguments[2], RUBY_RE, repo_paths_ok)
    if program == "node":
        return len(arguments) == 2 and arguments[0] == "-e" and _strict_path(arguments[1], NODE_RE, repo_paths_ok)

    # Every other shape must be free of shell composition anywhere.
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
    if program in {"npm", "pnpm", "yarn"}:
        script = arguments[-1] if (program == "yarn" and len(arguments) == 1) else (
            arguments[1] if program in {"npm", "pnpm"} and len(arguments) == 2 and arguments[0] == "run" else None
        )
        return bool(script) and SCRIPT_RE.match(script) is not None and any(script.endswith(tail) or script == tail for tail in READ_ONLY_TAILS)
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
) -> None:
    """Bind PLAN/TASKS acceptance commands and replace slice state from impact files."""
    if stage in {"PLAN", "TASKS"}:
        check_commands, refused = bind_check_commands(delivery.get("acceptance") or {}, gate_commands=gate_commands)
        delivery["check_commands"] = check_commands
        delivery["check_commands_refused"] = refused
    if stage == "PLAN":
        for check in delivery.get("acceptance", {}).values():
            check["slice_id"] = doc_slice
    if stage == "TASKS" or (stage == "PLAN" and profile == "DECISION_DOC"):
        import plan_checks

        editable_paths, normalizations = plan_checks.normalize_editable_paths(impact_files)
        planned = list(dict.fromkeys(check["slice_id"] for check in delivery.get("acceptance", {}).values() if check.get("slice_id")))
        delivery["slices"] = {"planned": planned, "completed": [], "editable_paths": editable_paths}
        delivery["editable_path_normalizations"] = normalizations
        delivery["approved_slice_sha256s"] = approve_slices(planned)


def _strict_path(argument: str, shape: re.Pattern[str], repo_paths_ok: Callable[[str], bool]) -> bool:
    match = shape.match(argument)
    return bool(match) and repo_paths_ok(match.group(1))


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
    program, arguments = tokens[0], tokens[1:]
    flags = [item for item in arguments if item.startswith("-")]
    rest = [item for item in arguments if not item.startswith("-")]
    if len(rest) != 2:
        return None
    pattern, path = rest
    return f"git show HEAD:{path} | {' '.join([program, *flags, shlex.quote(pattern)])}"


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
