#!/usr/bin/env python3
"""Guarded read-only connectivity for an Obsidian second brain.

The versioned repository binding remains the source of truth. The official
Obsidian CLI is an optional read-side accelerator; runtime state and every
write continue to use the filesystem and ``vault_guard``.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

import obsidian_binding

DEFAULT_TIMEOUT_SECONDS = 5.0
_EXCLUDED_DIRECTORIES = {".hermes-runtime", ".obsidian", ".trash", ".git"}


class ConnectorError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _timeout_is_valid(timeout: float) -> bool:
    return (
        not isinstance(timeout, bool)
        and isinstance(timeout, (int, float))
        and math.isfinite(timeout)
        and timeout > 0
    )


def _run(cli_path: str, arguments: list[str], *, timeout: float) -> str:
    if (
        not _timeout_is_valid(timeout)
        or "\x00" in cli_path
        or any("\x00" in argument for argument in arguments)
    ):
        raise ConnectorError("CLI_INPUT_INVALID", "Obsidian CLI input is invalid.")
    try:
        result = subprocess.run(
            [cli_path, *arguments],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConnectorError("CLI_UNAVAILABLE", "Obsidian CLI did not respond.") from exc
    except (ValueError, OverflowError) as exc:
        raise ConnectorError("CLI_INPUT_INVALID", "Obsidian CLI input is invalid.") from exc
    try:
        output = result.stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ConnectorError("CLI_OUTPUT_INVALID", "Obsidian CLI output is not UTF-8.") from exc
    if result.returncode:
        raise ConnectorError("CLI_UNAVAILABLE", "Obsidian CLI returned a nonzero status.")
    return output


def discover_vaults(cli_path: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> list[dict[str, str]]:
    """Return strict ``name/path`` records from ``obsidian vaults verbose``."""
    output = _run(cli_path, ["vaults", "verbose"], timeout=timeout)
    records: list[dict[str, str]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != 2 or not fields[0].strip():
            raise ConnectorError("CLI_OUTPUT_INVALID", "Vault discovery output is malformed.")
        path = Path(fields[1].strip())
        if not path.is_absolute():
            raise ConnectorError("CLI_OUTPUT_INVALID", "Vault discovery returned a relative path.")
        try:
            path.resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            raise ConnectorError(
                "CLI_OUTPUT_INVALID",
                "Vault discovery returned an unresolvable path.",
            ) from exc
        records.append({"name": fields[0].strip(), "path": str(path)})
    if not records:
        raise ConnectorError("CLI_OUTPUT_INVALID", "Vault discovery returned no records.")
    return records


def _cli_path_available(path: str | None) -> bool:
    if not path or "\x00" in path:
        return False
    try:
        return Path(path).is_file()
    except (OSError, ValueError):
        return False


def _filesystem_report(diagnostic: str) -> dict[str, Any]:
    return {
        "status": "READY_FILESYSTEM",
        "ready": True,
        "transport": "filesystem",
        "diagnostics": [diagnostic],
    }


def _directory_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


def _binding_path_spelling_is_canonical(binding: obsidian_binding.Binding) -> bool:
    raw_vault = os.environ.get(obsidian_binding.VAULT_ENV, binding.raw.get("vault_path"))
    raw_container = binding.raw.get("project_container")
    if not isinstance(raw_vault, str) or not isinstance(raw_container, str):
        return False
    return (
        raw_vault == os.path.abspath(raw_vault)
        and raw_vault == os.path.normpath(raw_vault)
        and raw_container == binding.project_container
        and all(part not in {"", ".", ".."} for part in raw_container.split("/"))
    )


def _open_directory_chain(path: Path) -> list[int]:
    """Open one absolute directory path lexically without following symlinks."""
    absolute = Path(os.path.abspath(str(path)))
    descriptors: list[int] = []
    try:
        parent = os.open(absolute.anchor or os.sep, _directory_flags())
        descriptors.append(parent)
        for component in absolute.parts[1:]:
            child = os.open(component, _directory_flags(), dir_fd=parent)
            descriptors.append(child)
            if not stat.S_ISDIR(os.fstat(child).st_mode):
                raise OSError("binding component is not a directory")
            parent = child
        return descriptors
    except OSError as exc:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise ConnectorError(
            "BINDING_PATH_UNSAFE",
            "Bound vault and project container must be existing symlink-free directories.",
        ) from exc


def _assert_binding_path_safe(binding: obsidian_binding.Binding) -> None:
    if not _binding_path_spelling_is_canonical(binding):
        raise ConnectorError(
            "BINDING_PATH_UNSAFE",
            "Bound vault and project container paths must use canonical spelling.",
        )
    descriptors = _open_directory_chain(binding.container_path)
    for descriptor in reversed(descriptors):
        os.close(descriptor)


def _read_project_text(binding: obsidian_binding.Binding, relative: PurePosixPath) -> str:
    """Read through no-follow descriptors anchored at the filesystem root."""
    descriptors: list[int] = []
    file_descriptor: int | None = None
    try:
        descriptors = _open_directory_chain(binding.container_path)
        parent = descriptors[-1]
        for component in relative.parts[:-1]:
            child = os.open(component, _directory_flags(), dir_fd=parent)
            descriptors.append(child)
            if not stat.S_ISDIR(os.fstat(child).st_mode):
                raise OSError("note parent is not a directory")
            parent = child
        file_descriptor = os.open(
            relative.parts[-1],
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent,
        )
        if not stat.S_ISREG(os.fstat(file_descriptor).st_mode):
            raise OSError("note is not a regular file")
        with os.fdopen(file_descriptor, "r", encoding="utf-8") as handle:
            file_descriptor = None
            return handle.read()
    except (OSError, UnicodeError) as exc:
        raise ConnectorError("NOTE_UNAVAILABLE", "Note could not be read safely.") from exc
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _is_excluded_relative(
    binding: obsidian_binding.Binding,
    relative: PurePosixPath,
) -> bool:
    """Return whether a project-relative path belongs to connector-private state."""
    runtime_parts = PurePosixPath(binding.runtime_subpath).parts
    return (
        any(part in _EXCLUDED_DIRECTORIES for part in relative.parts)
        or relative.parts[: len(runtime_parts)] == runtime_parts
    )


def _project_notes(binding: obsidian_binding.Binding):
    for current_root, dirnames, filenames in os.walk(binding.container_path, followlinks=False):
        current = Path(current_root)
        current_relative = PurePosixPath(current.relative_to(binding.container_path).as_posix())
        dirnames[:] = [
            name
            for name in dirnames
            if not _is_excluded_relative(
                binding,
                PurePosixPath(*current_relative.parts, name),
            )
            and not (current / name).is_symlink()
        ]
        for filename in sorted(filenames):
            if not filename.endswith(".md"):
                continue
            path = current / filename
            relative = PurePosixPath(path.relative_to(binding.container_path).as_posix())
            if _is_excluded_relative(binding, relative):
                continue
            try:
                yield relative, _read_project_text(binding, relative)
            except ConnectorError:
                continue


def _filesystem_search(binding: obsidian_binding.Binding, query: str, limit: int) -> list[str]:
    matches: list[str] = []
    needle = query.casefold()
    for relative, content in _project_notes(binding):
        if needle in content.casefold():
            matches.append(relative.as_posix())
            if len(matches) >= limit:
                break
    return matches


def _cli_result_paths(binding: obsidian_binding.Binding, payload: Any, limit: int) -> list[str]:
    if not isinstance(payload, list):
        raise ConnectorError("CLI_OUTPUT_INVALID", "Search output must be a JSON array.")
    paths: list[str] = []
    container_parts = PurePosixPath(binding.project_container).parts
    for item in payload:
        raw = item if isinstance(item, str) else item.get("path") if isinstance(item, dict) else None
        if not isinstance(raw, str):
            raise ConnectorError("CLI_OUTPUT_INVALID", "Search result has no path.")
        relative = PurePosixPath(raw)
        if relative.is_absolute() or ".." in relative.parts:
            continue
        if relative.parts[: len(container_parts)] != container_parts:
            continue
        project_relative = PurePosixPath(*relative.parts[len(container_parts) :])
        try:
            project_relative = _validated_note_relative(
                binding,
                project_relative.as_posix(),
            )
            _read_project_text(binding, project_relative)
        except ConnectorError:
            continue
        paths.append(project_relative.as_posix())
        if len(paths) >= limit:
            break
    return sorted(set(paths))


def _cli_search(
    binding: obsidian_binding.Binding,
    cli_path: str,
    vault_name: str,
    query: str,
    limit: int,
    timeout: float,
) -> list[str]:
    output = _run(
        cli_path,
        [
            f"vault={vault_name}",
            "search",
            f"query={query}",
            f"path={binding.project_container}",
            f"limit={limit}",
            "format=json",
        ],
        timeout=timeout,
    )
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise ConnectorError("CLI_OUTPUT_INVALID", "Search output is not valid JSON.") from exc
    return _cli_result_paths(binding, payload, limit)


def _validated_note_relative(
    binding: obsidian_binding.Binding,
    relative_path: str,
) -> PurePosixPath:
    """Apply the connector's shared project-note path policy."""
    relative = PurePosixPath(relative_path)
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise ConnectorError("NOTE_PATH_INVALID", "Note path must be project-container-relative.")
    if relative.suffix.lower() != ".md":
        raise ConnectorError("NOTE_PATH_INVALID", "Connector reads are limited to Markdown notes.")
    if _is_excluded_relative(binding, relative):
        raise ConnectorError("NOTE_PATH_INVALID", "Note path is excluded from connector reads.")
    return relative


def read_note(
    repo_root: Path,
    relative_path: str,
    *,
    cli_path: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Read one note through no-follow filesystem descriptors.

    ``cli_path`` and ``timeout`` remain accepted for API compatibility, but a
    lexical CLI read cannot preserve containment across a path replacement.
    """
    del cli_path, timeout
    binding = obsidian_binding.load(Path(repo_root))
    _assert_binding_path_safe(binding)
    relative = _validated_note_relative(binding, relative_path)
    try:
        content = _read_project_text(binding, relative)
    except ConnectorError as exc:
        raise ConnectorError("NOTE_UNAVAILABLE", "Note could not be read.") from exc
    return {
        "transport": "filesystem",
        "path": relative.as_posix(),
        "content": content,
        "diagnostics": [],
    }


def search(
    repo_root: Path,
    query: str,
    *,
    limit: int = 20,
    cli_path: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Search only notes inside the bound project container."""
    if not isinstance(query, str) or not query.strip() or "\x00" in query:
        raise ConnectorError("QUERY_INVALID", "Search query must be non-empty and contain no NUL.")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ConnectorError("LIMIT_INVALID", "Search limit must be a positive integer.")
    binding = obsidian_binding.load(Path(repo_root))
    _assert_binding_path_safe(binding)
    if not binding.container_path.is_dir():
        raise ConnectorError("CONTAINER_UNAVAILABLE", "Bound project container is unavailable.")
    executable = cli_path if cli_path is not None else shutil.which("obsidian")
    if not _timeout_is_valid(timeout):
        diagnostic = "CLI_INPUT_INVALID"
    elif _cli_path_available(executable):
        assert executable is not None
        report = preflight(repo_root, cli_path=executable, timeout=timeout)
        if report["status"] == "READY_CLI":
            try:
                paths = _cli_search(
                    binding, executable, str(report["vault"]), query, limit, timeout
                )
                return {"transport": "cli", "paths": paths, "diagnostics": []}
            except ConnectorError as exc:
                diagnostic = exc.code
        else:
            diagnostic = str(report["diagnostics"][0])
    else:
        diagnostic = "CLI_UNAVAILABLE"
    return {
        "transport": "filesystem",
        "paths": _filesystem_search(binding, query, limit),
        "diagnostics": [diagnostic],
    }


def open_note(
    repo_root: Path,
    relative_path: str,
    *,
    cli_path: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, str]:
    """Refuse lexical CLI opening because containment cannot remain atomic."""
    del cli_path, timeout
    binding = obsidian_binding.load(Path(repo_root))
    _assert_binding_path_safe(binding)
    _validated_note_relative(binding, relative_path)
    raise ConnectorError(
        "OPEN_UNSUPPORTED",
        "Opening through the CLI is disabled because it accepts only a lexical path.",
    )


def tags(repo_root: Path) -> list[dict[str, Any]]:
    """Aggregate project-scoped tags without querying the whole vault."""
    binding = obsidian_binding.load(Path(repo_root))
    _assert_binding_path_safe(binding)
    if not binding.container_path.is_dir():
        raise ConnectorError("CONTAINER_UNAVAILABLE", "Bound project container is unavailable.")
    counts: dict[str, int] = {}
    for _, content in _project_notes(binding):
        note_tags = set(re.findall(r"(?<![A-Za-z0-9_])#([A-Za-z0-9_/-]+)", content))
        frontmatter = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
        if frontmatter:
            block = frontmatter.group(1)
            tag_list = re.search(r"(?:^|\n)tags:\s*\n((?:\s+-\s+[^\n]+\n?)*)", block)
            if tag_list:
                note_tags.update(
                    value.strip()
                    for value in re.findall(r"^\s+-\s+([^\n]+)$", tag_list.group(1), re.MULTILINE)
                    if value.strip()
                )
        for tag in note_tags:
            counts[tag] = counts.get(tag, 0) + 1
    return [{"tag": tag, "count": counts[tag]} for tag in sorted(counts)]


def preflight(
    repo_root: Path,
    *,
    cli_path: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Report binding, filesystem, and optional official-CLI readiness."""
    try:
        binding = obsidian_binding.load(Path(repo_root))
    except obsidian_binding.BindingError as exc:
        return {
            "status": exc.code,
            "ready": False,
            "transport": None,
            "diagnostics": [exc.code],
        }

    if not binding.vault_path.is_dir():
        return {
            "status": "VAULT_UNREACHABLE",
            "ready": False,
            "transport": None,
            "diagnostics": ["VAULT_UNREACHABLE"],
        }
    if not binding.container_path.is_dir():
        return {
            "status": "CONTAINER_UNAVAILABLE",
            "ready": False,
            "transport": None,
            "diagnostics": ["CONTAINER_UNAVAILABLE"],
        }
    try:
        _assert_binding_path_safe(binding)
    except ConnectorError as exc:
        return {
            "status": exc.code,
            "ready": False,
            "transport": None,
            "diagnostics": [exc.code],
        }
    if not _timeout_is_valid(timeout):
        return _filesystem_report("CLI_INPUT_INVALID")

    executable = cli_path if cli_path is not None else shutil.which("obsidian")
    if not _cli_path_available(executable):
        return _filesystem_report("CLI_UNAVAILABLE")
    assert executable is not None
    try:
        vaults = discover_vaults(executable, timeout=timeout)
    except ConnectorError as exc:
        return _filesystem_report(exc.code)
    bound = os.path.normcase(os.path.abspath(str(binding.vault_path)))
    matching = [
        record
        for record in vaults
        if os.path.normcase(os.path.abspath(record["path"])) == bound
    ]
    if not matching:
        return _filesystem_report("CLI_VAULT_UNREACHABLE")
    return {
        "status": "READY_CLI",
        "ready": True,
        "transport": "cli",
        "vault": matching[0]["name"],
        "diagnostics": [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    discover_parser = commands.add_parser("discover", help="list vault candidates through the official CLI")
    discover_parser.add_argument("--cli", help="explicit Obsidian CLI executable")
    discover_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    discover_parser.add_argument("--json", action="store_true", help="emit JSON")
    preflight_parser = commands.add_parser("preflight", help="check binding and read transport readiness")
    preflight_parser.add_argument("--repo", default=".", help="repository root (default: current directory)")
    preflight_parser.add_argument("--cli", help="explicit Obsidian CLI executable")
    preflight_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    preflight_parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)

    if args.command == "discover":
        executable = args.cli if args.cli is not None else shutil.which("obsidian")
        if not _timeout_is_valid(args.timeout):
            payload: dict[str, Any] = {"status": "CLI_INPUT_INVALID", "vaults": []}
            if args.json:
                print(json.dumps(payload, sort_keys=True))
            else:
                print("CLI_INPUT_INVALID")
            return 1
        if not _cli_path_available(executable):
            payload = {"status": "CLI_UNAVAILABLE", "vaults": []}
            if args.json:
                print(json.dumps(payload, sort_keys=True))
            else:
                print("CLI_UNAVAILABLE")
            return 1
        assert executable is not None
        try:
            vaults = discover_vaults(executable, timeout=args.timeout)
        except ConnectorError as exc:
            payload = {"status": exc.code, "vaults": []}
            if args.json:
                print(json.dumps(payload, sort_keys=True))
            else:
                print(exc.code)
            return 1
        payload = {"status": "READY", "vaults": vaults}
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            for vault in vaults:
                print(f"{vault['name']}\t{vault['path']}")
        return 0

    if args.command == "preflight":
        report = preflight(Path(args.repo), cli_path=args.cli, timeout=args.timeout)
        if args.json:
            print(json.dumps(report, sort_keys=True))
        else:
            print(f"{report['status']}: transport={report['transport'] or 'none'}")
        return 0 if report["ready"] else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
