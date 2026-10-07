#!/usr/bin/env python3
"""Record everything the orchestrator runs into the project's Obsidian LLM Wiki.

The project container is read **and written**: every SDD action, stage
artifact, gate result, incident, decision and Hermes conversation turn that runs
for a bound worktree lands in the wiki, so the vault is the project's memory.

Where each record goes (relative to the project container):

* ``stage``    -> ``raw/articles/<ticket>/<stamp>-<stage>-<title>.md``
* ``action``   -> ``raw/articles/<ticket>/actions/<stamp>-<title>.md``
* ``gate``     -> ``raw/articles/<ticket>/gates/<stamp>-<title>.md``
* ``incident`` -> ``raw/articles/<ticket>/incidents/<stamp>-<title>.md``
* ``turn``     -> ``raw/transcripts/sessions/<date>-<session>.md`` (append-only)
* ``decision`` -> ``concepts/<title>.md`` (``type: decision``; later records append)
* ``concept`` / ``entity`` / ``comparison`` / ``query`` -> the Layer-2 folder

Raw sources are created once and never rewritten (``O_EXCL``; a clashing name
gets a numeric suffix); a session transcript only ever grows. Layer-2 pages are
created or get a dated section appended, and are listed in ``index.md``. Every
record appends one entry to ``log.md``. All writes go through no-follow
descriptors anchored at the container, so a symlink can never redirect them,
and obvious secrets are redacted before anything is written. Stdlib only;
nothing here contacts the network.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import stat
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))

import obsidian_binding  # noqa: E402
import wiki_layout  # noqa: E402

RAW_KINDS = {
    "stage": "",
    "action": "actions",
    "gate": "gates",
    "incident": "incidents",
}
PAGE_KINDS = {
    "decision": "concepts",
    "concept": "concepts",
    "entity": "entities",
    "comparison": "comparisons",
    "query": "queries",
}
KINDS = (*RAW_KINDS, "turn", *PAGE_KINDS)
MAX_RECORD_BYTES = 512 * 1024
MAX_TITLE_CHARS = 80
NO_TICKET = "no-ticket"
_SLUG_UNSAFE = re.compile(r"[^0-9A-Za-z\u00C0-\u024F._ -]+")
_SPACES = re.compile(r"[\s_]+")
_SECRET_PATTERNS = (
    re.compile(r"\b(sk-[A-Za-z0-9_-]{16,})"),
    re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,})"),
    re.compile(r"\b(xox[abposr]-[A-Za-z0-9-]{10,})"),
    re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
    re.compile(r"(?i)\b(bearer\s+[A-Za-z0-9._~+/-]{16,}=*)"),
    re.compile(r"(?i)\b([A-Za-z0-9_-]*(?:api[_-]?key|token|secret|password|passwd)[A-Za-z0-9_-]*\s*[:=]\s*)(['\"]?)([^\s'\"]{8,})\2"),
)


class WikiJournalError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


# --------------------------------------------------------------------------- helpers


def redact(text: str) -> str:
    """Mask common credential shapes; the wiki is synced and must never hold a secret."""
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 3:
            text = pattern.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]{match.group(2)}", text)
        else:
            text = pattern.sub("[REDACTED]", text)
    return text


def slug(value: str, *, fallback: str = "record") -> str:
    cleaned = _SLUG_UNSAFE.sub(" ", value).strip().lower()
    cleaned = _SPACES.sub("-", cleaned).strip("-.")
    return (cleaned[:MAX_TITLE_CHARS].rstrip("-.") or fallback)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _bounded(text: str) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= MAX_RECORD_BYTES:
        return text
    kept = encoded[:MAX_RECORD_BYTES].decode("utf-8", errors="ignore")
    return kept + f"\n\n> Truncated: the record had {len(encoded)} bytes; the first {MAX_RECORD_BYTES} are kept.\n"


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(json.dumps(str(item), ensure_ascii=False) for item in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


def _frontmatter(fields: Mapping[str, Any]) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if value is None or value == "" or value == []:
            continue
        lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- container


def container_for(workspace: Path) -> Path:
    """Return the bound, initialized project container for a worktree.

    A repository-local binding selects its container directly. Otherwise the
    vault-resident controller's own container is used, but only for a worktree
    the installer registered there, so an unrelated directory (a test fixture,
    another repository) can never write into this project's wiki.
    """
    workspace = Path(os.path.realpath(workspace))
    local = workspace / obsidian_binding.BINDING_RELATIVE_PATH
    try:
        if local.is_file():
            binding = obsidian_binding.load_path(local)
        else:
            installed = _installed_container()
            if installed is None:
                raise WikiJournalError("WIKI_BINDING_MISSING", f"{workspace} has no Obsidian binding")
            registered = {Path(os.path.realpath(path)) for path in registered_workspaces(installed)}
            if workspace not in registered:
                raise WikiJournalError("WIKI_WORKSPACE_NOT_REGISTERED", f"{workspace} is not installed in {installed}")
            binding = obsidian_binding.load_path(installed / obsidian_binding.BINDING_RELATIVE_PATH)
    except obsidian_binding.BindingError as error:
        raise WikiJournalError("WIKI_BINDING_MISSING", str(error)) from error
    return prepare_container(binding.container_path)


def prepare_container(container: Path) -> Path:
    container = Path(os.path.realpath(container))
    try:
        container = wiki_layout._require_container(container)
        if not os.path.lexists(container / "SCHEMA.md"):
            wiki_layout.init(container, project=container.name, today=_now().date().isoformat())
    except wiki_layout.WikiError as error:
        raise WikiJournalError(error.code, error.detail) from error
    return container


# --------------------------------------------------------------------------- writing


def _create_unique(root_fd: int, relative: str, content: bytes) -> str:
    """Create a new file below the container; never overwrite, suffix on clash."""
    parts, name = wiki_layout._split(relative)
    stem, dot, suffix = name.rpartition(".")
    if not dot:
        stem, suffix = name, ""
    directory = wiki_layout._open_dir(root_fd, parts, create=True)
    try:
        for attempt in range(1, 1000):
            candidate = name if attempt == 1 else f"{stem}-{attempt}{dot}{suffix}"
            try:
                fd = os.open(
                    candidate,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | wiki_layout._NOFOLLOW | wiki_layout._CLOEXEC,
                    0o644,
                    dir_fd=directory,
                )
            except FileExistsError:
                continue
            try:
                wiki_layout._write_all(fd, content)
                os.fsync(fd)
            finally:
                os.close(fd)
            return str(PurePosixPath(*parts, candidate))
    finally:
        os.close(directory)
    raise WikiJournalError("WIKI_RECORD_NAME_EXHAUSTED", f"no free name for {relative}")


def _append_or_create(root_fd: int, relative: str, header: bytes, entry: bytes) -> bool:
    """Append ``entry`` to a single-link regular file, creating it with ``header`` first. True if created."""
    parts, name = wiki_layout._split(relative)
    directory = wiki_layout._open_dir(root_fd, parts, create=True)
    try:
        created = False
        try:
            fd = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | wiki_layout._NOFOLLOW | wiki_layout._CLOEXEC,
                0o644,
                dir_fd=directory,
            )
            created = True
        except FileExistsError:
            fd = os.open(name, os.O_WRONLY | os.O_APPEND | wiki_layout._NOFOLLOW | wiki_layout._CLOEXEC, dir_fd=directory)
        try:
            status = os.fstat(fd)
            if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
                raise WikiJournalError("WIKI_PATH_UNSAFE", f"{relative} must be a regular file with a single link")
            if created:
                wiki_layout._write_all(fd, header)
            wiki_layout._write_all(fd, entry)
            os.fsync(fd)
        finally:
            os.close(fd)
        return created
    finally:
        os.close(directory)


def _index_page(container: Path, root_fd: int, relative: str, summary: str) -> None:
    if not os.path.lexists(container / "index.md"):
        return
    text = wiki_layout._read_wiki_file(root_fd, "index.md")
    unique = wiki_layout._unique_stems(wiki_layout._layer_two_pages(container))
    if wiki_layout._is_indexed(text, relative, unique):
        return
    prefix = PurePosixPath(relative).parts[0]
    title = dict(wiki_layout.PAGE_SECTIONS)[prefix]
    heading = f"## {title}\n"
    if heading not in text:
        text = text.rstrip("\n") + f"\n\n{heading}"
    line = f"- [[{wiki_layout._link_target(relative)}|{PurePosixPath(relative).stem}]] — {summary}\n"
    position = text.index(heading) + len(heading)
    text = text[:position] + line + text[position:]
    text = re.sub(r"Total pages: \d+", f"Total pages: {len(wiki_layout._layer_two_pages(container))}", text, count=1)
    text = re.sub(r"Last updated: \S+", f"Last updated: {_now().date().isoformat()}", text, count=1)
    wiki_layout._replace_wiki_file(root_fd, "index.md", text)


def _log(root_fd: int, action: str, subject: str, lines: Iterable[str]) -> None:
    try:
        wiki_layout.append_log_entry(root_fd, _now().date().isoformat(), action, subject, list(lines))
    except FileNotFoundError:
        pass


def _summary_line(body: str) -> str:
    for line in body.splitlines():
        line = line.strip().lstrip("#>-* ").strip()
        if line:
            return redact(line)[:140]
    return "recorded by the SDD orchestrator"


def record(
    container: Path,
    *,
    kind: str,
    title: str,
    body: str,
    ticket: str | None = None,
    stage: str | None = None,
    session: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    when: dt.datetime | None = None,
) -> dict[str, Any]:
    """Write one record into the wiki and log it. Returns ``{"status": "WRITTEN", "path": ...}``."""
    if kind not in KINDS:
        raise WikiJournalError("WIKI_RECORD_KIND_INVALID", f"kind must be one of {', '.join(KINDS)}")
    if not isinstance(title, str) or not title.strip():
        raise WikiJournalError("WIKI_RECORD_INVALID", "title is required")
    if not isinstance(body, str):
        raise WikiJournalError("WIKI_RECORD_INVALID", "body must be text")
    when = when or _now()
    stamp = when.strftime("%Y%m%d-%H%M%S")
    clean_title = redact(title.strip())[:200]
    clean_body = _bounded(redact(body))
    fields: dict[str, Any] = {
        "title": clean_title,
        "kind": kind,
        "recorded": when.isoformat().replace("+00:00", "Z"),
        "ticket": ticket,
        "stage": stage,
        "session": session,
        "source": "hermes-sdd-orchestrator",
    }
    for key, value in (metadata or {}).items():
        if key not in fields and isinstance(key, str) and re.fullmatch(r"[a-z][a-z0-9_]*", key):
            fields[key] = redact(str(value)) if isinstance(value, str) else value
    container = Path(container)
    root_fd = wiki_layout._open_root(container)
    try:
        ticket_dir = slug(ticket or NO_TICKET, fallback=NO_TICKET)
        if kind in RAW_KINDS:
            name = f"{stamp}-{slug(stage)}-{slug(clean_title)}.md" if kind == "stage" and stage else f"{stamp}-{slug(clean_title)}.md"
            relative = str(PurePosixPath("raw/articles", ticket_dir, *(filter(None, [RAW_KINDS[kind]])), name))
            content = (_frontmatter(fields) + f"\n# {clean_title}\n\n{clean_body.rstrip()}\n").encode("utf-8")
            written = _create_unique(root_fd, relative, content)
            log_lines = [f"[[{wiki_layout._link_target(written)}]]"] + ([f"ticket: {ticket}"] if ticket else [])
            _log(root_fd, "ingest", f"{kind} — {clean_title}", log_lines)
            return {"status": "WRITTEN", "kind": kind, "path": written, "created": True}
        if kind == "turn":
            day = when.date().isoformat()
            session_slug = slug(session or clean_title, fallback="session")
            relative = str(PurePosixPath("raw/transcripts/sessions", f"{day}-{session_slug}.md"))
            header_fields = {**fields, "title": f"Session {session or clean_title}", "kind": "transcript"}
            header = (_frontmatter(header_fields) + f"\n# Session {session or clean_title}\n").encode("utf-8")
            entry = f"\n## {when.isoformat().replace('+00:00', 'Z')} — {clean_title}\n\n{clean_body.rstrip()}\n".encode("utf-8")
            created = _append_or_create(root_fd, relative, header, entry)
            if created:
                _log(root_fd, "ingest", f"session {session or clean_title}", [f"[[{wiki_layout._link_target(relative)}]]"])
            return {"status": "WRITTEN", "kind": kind, "path": relative, "created": created}
        folder = PAGE_KINDS[kind]
        relative = str(PurePosixPath(folder, f"{slug(clean_title)}.md"))
        page_fields = {
            "title": clean_title,
            "created": when.date().isoformat(),
            "updated": when.date().isoformat(),
            "type": kind,
            "tags": ["decision"] if kind == "decision" else [],
            "sources": [f"raw/articles/{ticket_dir}"] if ticket else [],
            "ticket": ticket,
        }
        header = (_frontmatter(page_fields) + f"\n# {clean_title}\n").encode("utf-8")
        entry = f"\n## {when.date().isoformat()}{f' — {stage}' if stage else ''}\n\n{clean_body.rstrip()}\n".encode("utf-8")
        created = _append_or_create(root_fd, relative, header, entry)
        _index_page(container, root_fd, relative, _summary_line(clean_body))
        _log(root_fd, "create" if created else "update", f"{kind} — {clean_title}", [f"[[{wiki_layout._link_target(relative)}]]"])
        return {"status": "WRITTEN", "kind": kind, "path": relative, "created": created}
    except OSError as error:
        raise WikiJournalError("WIKI_PATH_UNSAFE", f"{error.filename or container}: {error.strerror or error}") from error
    finally:
        os.close(root_fd)


def record_for_workspace(workspace: Path, **kwargs: Any) -> dict[str, Any]:
    return record(container_for(workspace), **kwargs)


# --------------------------------------------------------------------------- action journal mirror


def _read_artifact(path_text: Any) -> str | None:
    if not isinstance(path_text, str) or not path_text:
        return None
    try:
        fd = os.open(path_text, os.O_RDONLY | wiki_layout._NOFOLLOW | wiki_layout._CLOEXEC)
    except OSError:
        return None
    try:
        status = os.fstat(fd)
        if not stat.S_ISREG(status.st_mode):
            return None
        data = os.read(fd, MAX_RECORD_BYTES + 1)
    finally:
        os.close(fd)
    return data.decode("utf-8", errors="replace")


def action_body(journal: Mapping[str, Any], *, outcome: str) -> str:
    action = journal.get("action", {}) or {}
    artifact = journal.get("artifact", {}) or {}
    process = journal.get("process", {}) or {}
    lines = [
        f"- outcome: {outcome}",
        f"- action: {action.get('id')} · attempt {action.get('attempt')}",
        f"- stage: {action.get('stage')} · name: {action.get('name')}",
        f"- executor: {action.get('executor')}",
        f"- process: started {process.get('started_at')} · finished {process.get('finished_at')} · exit {process.get('exit_code')}",
        f"- artifact: {artifact.get('validation_status')} · sha256 {artifact.get('sha256')}",
    ]
    if action.get("invalid_fields"):
        lines.append(f"- invalid fields: {', '.join(map(str, action['invalid_fields']))}")
    message = _read_artifact(action.get("final_message_path"))
    if message is not None:
        lines += ["", "## Executor result", "", "```text", message.rstrip(), "```"]
    return "\n".join(lines) + "\n"


def mirror_action(journal: Mapping[str, Any], *, outcome: str) -> dict[str, Any]:
    """Record one finished journal action in the wiki; never raises."""
    try:
        action = journal.get("action", {}) or {}
        workspace = Path((journal.get("workspace", {}) or {}).get("path", ""))
        return record_for_workspace(
            workspace,
            kind="action",
            title=f"{action.get('stage') or 'action'} {action.get('id') or ''} {outcome}".strip(),
            body=action_body(journal, outcome=outcome),
            ticket=action.get("ticket"),
            stage=action.get("stage"),
            metadata={"action_id": action.get("id"), "outcome": outcome},
        )
    except WikiJournalError as error:
        return {"status": "SKIPPED", "reason": error.code, "detail": error.detail}
    except Exception as error:  # the journal must never fail because the wiki could not be written
        return {"status": "SKIPPED", "reason": "WIKI_RECORD_FAILED", "detail": f"{error.__class__.__name__}: {error}"}


# --------------------------------------------------------------------------- hooks


def _installed_container() -> Path | None:
    candidate = Path(__file__).resolve().parents[3]
    binding = candidate / ".hermes" / "obsidian.json"
    return candidate if binding.is_file() and not binding.is_symlink() else None


def registered_workspaces(container: Path) -> list[Path]:
    """Workspaces the installer set up for this container, read from each runtime STATE."""
    runtime = container / ".hermes-runtime"
    found: list[Path] = []
    if not runtime.is_dir() or runtime.is_symlink():
        return found
    for entry in sorted(runtime.iterdir()):
        state_file = entry / "STATE.md"
        if entry.is_symlink() or not state_file.is_file() or state_file.is_symlink():
            continue
        try:
            text = state_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        match = re.search(r"^workspace:\s*\n\s+path:\s*\"?([^\"\n]+)\"?", text, re.MULTILINE)
        if match:
            found.append(Path(match.group(1)))
    return found


def _related(cwd: Path, workspace: Path) -> bool:
    """A session belongs to a workspace when it runs inside it or in its direct parent folder."""
    if cwd.parts[: len(workspace.parts)] == workspace.parts:
        return True
    return cwd == workspace.parent


def hook_container(payload: Mapping[str, Any]) -> Path | None:
    """The container a hook event belongs to, or None when the session is unrelated to it."""
    cwd_text = payload.get("cwd")
    if not isinstance(cwd_text, str) or not cwd_text:
        return None
    cwd = Path(os.path.realpath(cwd_text))
    container = _installed_container()
    if container is not None:
        if any(_related(cwd, Path(os.path.realpath(workspace))) for workspace in registered_workspaces(container)):
            return prepare_container(container)
        return None
    # Repository-local controller: <repo>/.hermes/orchestration/runtime/this file.
    repo = Path(__file__).resolve().parents[3]
    if _related(cwd, repo) and os.path.lexists(repo / obsidian_binding.BINDING_RELATIVE_PATH):
        return container_for(repo)
    return None


def _extra(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    extra = payload.get("extra")
    return extra if isinstance(extra, Mapping) else {}


def record_turn_event(payload: Mapping[str, Any]) -> dict[str, Any]:
    container = hook_container(payload)
    if container is None:
        return {"status": "SKIPPED", "reason": "NOT_A_BOUND_WORKSPACE"}
    extra = _extra(payload)
    user = extra.get("user_message")
    assistant = extra.get("assistant_response")
    user_text = user if isinstance(user, str) else ""
    assistant_text = assistant if isinstance(assistant, str) else ""
    if not user_text.strip() and not assistant_text.strip():
        return {"status": "SKIPPED", "reason": "EMPTY_TURN"}
    session = str(payload.get("session_id") or extra.get("session_id") or "session")
    body = f"**User**\n\n{user_text.strip()}\n\n**Hermes**\n\n{assistant_text.strip()}\n"
    return record(
        container,
        kind="turn",
        title=f"turn {extra.get('turn_id') or ''}".strip(),
        body=body,
        session=session,
        metadata={"model": extra.get("model"), "platform": extra.get("platform"), "cwd": payload.get("cwd")},
    )


def record_session_end_event(payload: Mapping[str, Any]) -> dict[str, Any]:
    container = hook_container(payload)
    if container is None:
        return {"status": "SKIPPED", "reason": "NOT_A_BOUND_WORKSPACE"}
    extra = _extra(payload)
    session = str(payload.get("session_id") or "session")
    lines = [
        f"session: {session}",
        f"exit: {extra.get('turn_exit_reason')}",
        f"completed: {extra.get('completed')} · failed: {extra.get('failed')} · interrupted: {extra.get('interrupted')}",
    ]
    root_fd = wiki_layout._open_root(container)
    try:
        _log(root_fd, "update", f"session {session} ended", lines)
    finally:
        os.close(root_fd)
    return {"status": "WRITTEN", "kind": "session_end", "path": "log.md"}


def hook_main(handler) -> int:
    """Observer hook entry point: read Hermes JSON from stdin, never block the agent."""
    try:
        payload = json.load(sys.stdin)
        if isinstance(payload, dict):
            handler(payload)
    except Exception:
        pass
    print("{}")
    return 0


# --------------------------------------------------------------------------- CLI


def _body(args: argparse.Namespace) -> str:
    if args.body_file == "-":
        return sys.stdin.read()
    if args.body_file:
        return Path(args.body_file).read_text(encoding="utf-8")
    return args.body or ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    write = commands.add_parser("record", help="write one record into the bound wiki")
    target = write.add_mutually_exclusive_group(required=True)
    target.add_argument("--repo", help="worktree whose Obsidian binding selects the wiki")
    target.add_argument("--container", help="absolute project container path")
    write.add_argument("--kind", required=True, choices=KINDS)
    write.add_argument("--title", required=True)
    body = write.add_mutually_exclusive_group()
    body.add_argument("--body")
    body.add_argument("--body-file", help="path, or - for stdin")
    write.add_argument("--ticket")
    write.add_argument("--stage")
    write.add_argument("--session")
    write.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        container = container_for(Path(args.repo)) if args.repo else prepare_container(Path(args.container))
        report = record(
            container, kind=args.kind, title=args.title, body=_body(args),
            ticket=args.ticket, stage=args.stage, session=args.session,
        )
        code = 0
    except WikiJournalError as error:
        report, code = {"status": "BLOCKED", "reason": error.code, "detail": error.detail}, 2
    except OSError as error:
        report, code = {"status": "BLOCKED", "reason": "WIKI_PATH_UNSAFE", "detail": f"{error.filename}: {error.strerror or error}"}, 2
    print(json.dumps(report, ensure_ascii=False, indent=None if args.json else 2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
