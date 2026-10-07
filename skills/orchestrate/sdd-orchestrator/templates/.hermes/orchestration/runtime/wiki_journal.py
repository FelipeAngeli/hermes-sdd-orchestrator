#!/usr/bin/env python3
"""Record everything the orchestrator runs into the project's Obsidian LLM Wiki.

The project container is read **and written**: SDD actions, stage artifacts,
gate results, incidents, decisions and Hermes conversation turns that run for a
bound worktree land in the wiki, so the vault is the project's memory.

Where each record goes (relative to the project container):

* ``stage``    -> ``raw/articles/<ticket>/<stamp>-<stage>-<title>.md``
* ``action``   -> ``raw/articles/<ticket>/actions/<stamp>-<title>.md``
* ``gate``     -> ``raw/articles/<ticket>/gates/<stamp>-<title>.md``
* ``incident`` -> ``raw/articles/<ticket>/incidents/<stamp>-<title>.md``
* ``turn``     -> ``raw/transcripts/sessions/<date>-<session>.md`` (append-only, split in parts)
* ``decision`` -> ``concepts/<title>.md`` (``type: decision``; later records append)
* ``concept`` / ``entity`` / ``comparison`` / ``query`` -> the Layer-2 folder

Which container: the controller this module belongs to decides. A controller
installed in the vault writes only into its own container and only for worktrees
registered in its runtime; a repository-local controller writes only for its own
repository, into the container its binding names. A binding planted anywhere else
is never consulted.

Raw sources are created once and never rewritten (``O_EXCL``; a clashing name
gets a numeric suffix); a session transcript only grows. Layer-2 pages are
created or get a dated section appended, and are listed in ``index.md``. Every
record appends one entry to ``log.md``, which rotates. All writes go through
no-follow descriptors anchored at the container and the index/log updates hold
an exclusive lock. Common credential shapes are redacted before anything is
written. Stdlib only; nothing here contacts the network.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import math
import os
import re
import stat
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))

import obsidian_binding  # noqa: E402
import wiki_layout  # noqa: E402

try:  # POSIX only; on other platforms the index/log updates are best effort.
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

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
METADATA_KEYS = ("action_id", "outcome", "model", "platform", "role", "status")
MAX_RECORD_BYTES = 512 * 1024
MAX_TRANSCRIPT_BYTES = 4 * 1024 * 1024
MAX_LOG_ENTRIES = 500
MAX_TITLE_CHARS = 80
NO_TICKET = "no-ticket"
LOCK_NAME = ".wiki-journal.lock"
_SLUG_UNSAFE = re.compile(r"[^0-9A-Za-z\u00C0-\u024F._ -]+")
_SPACES = re.compile(r"[\s_]+")
_LINE_BREAKS = re.compile(r"[\r\n\u2028\u2029\x0b\x0c\x85]+")
# Every repetition below is bounded and every pattern anchors on a literal, so
# redaction stays linear in the input; record() also truncates before redacting.
# Linear-time rules for every pattern below: each repetition has an upper bound,
# and each match must start at a token boundary expressed as a fixed-width
# lookbehind (``\b`` re-matches after every '-' or '.', which made runs such as
# ``eyJ-eyJ-…`` or ``a-a-…`` quadratic). record() also truncates before redacting.
# Token patterns start after any non-word character, so a token after "=",
# "/", "?", ":", "." or "-" is still found (KEY=sk-…, ?jwt=eyJ…). Only the JWT
# pattern, which must fail after a long scan when no "." follows, also refuses
# to start after "-" (``eyJ-eyJ-…`` would otherwise be quadratic).
_T = r"(?<![A-Za-z0-9_])"  # not inside a word
_TOK = r"(?<![A-Za-z0-9_-])"  # not inside a [A-Za-z0-9_-] token
_SECRET_KEY = r"(?<![A-Za-z0-9_.-])"  + r"[A-Za-z0-9_.-]{0,40}?(?:api[_-]?key|apikey|access[_-]?key|secret|token|passw(?:or)?d|passwd|pwd|credential|private[_-]?key|client[_-]?secret|auth)[A-Za-z0-9_.-]{0,40}"
_PLAIN_VALUE = re.compile(r"^(?:\d+(?:\.\d+)?|true|false|null|none|\[redacted\])$", re.IGNORECASE)
# Keys whose values are credentials even when they look like plain numbers.
_ALWAYS_SECRET_KEY = re.compile(r"(?i)passw|pwd|secret|credential|private[_-]?key|passphrase")
_SECRET_PATTERNS = (
    # Well-known token shapes (private-key blocks are handled by _redact_key_blocks).
    # The cheap lookahead requires the first dot before the expensive scan, so a
    # failing position costs O(1) instead of O(n): "eyJ-eyJ-…" stays linear while
    # a real token after a hyphen (x-auth-eyJ…) is still found.
    re.compile(_T + r"(?=[A-Za-z0-9_-]{5,1024}\.)eyJ[A-Za-z0-9_-]{5,1024}\.[A-Za-z0-9_-]{5,8192}\.[A-Za-z0-9_-]{0,4096}"),
    re.compile(_T + r"(?:sk|rk|pk)[-_](?:live|test|proj|ant)?[-_]?[A-Za-z0-9_-]{16,512}"),
    re.compile(_T + r"github_pat_[A-Za-z0-9_]{20,512}"),
    re.compile(_T + r"gh[pousr]_[A-Za-z0-9]{20,512}"),
    re.compile(_T + r"glpat-[A-Za-z0-9_-]{16,512}"),
    re.compile(_T + r"hf_[A-Za-z0-9]{20,512}"),
    re.compile(_T + r"npm_[A-Za-z0-9]{20,512}"),
    re.compile(_T + r"whsec_[A-Za-z0-9+/=]{16,512}"),
    re.compile(_T + r"xox[abposr]-[A-Za-z0-9-]{10,512}"),
    re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/_-]{1,512}"),
    re.compile(_T + r"AIza[0-9A-Za-z_-]{30,512}"),
    re.compile(_T + r"(?:AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Z])"),
)
_SECRET_SUBSTITUTIONS = (
    # scheme://user:password@host — the password may itself contain '@'; the last '@' before the host wins.
    (re.compile(r"((?<![a-z0-9])[a-z][a-z0-9+.-]{0,30}://[^\s:/@]{0,256}:)([^\s]{1,512}?)(@[^\s@/]{1,256}(?:[/:?#\s]|$))", re.IGNORECASE | re.MULTILINE), r"\1[REDACTED]\3"),
    # Authorization schemes followed by a credential.
    (re.compile(r"(?i)" + _T + r"((?:bearer|basic|token|digest)[ \t]{1,16})([A-Za-z0-9._~+/=-]{8,4096})"), r"\1[REDACTED]"),
    # Cookie / Set-Cookie headers carry session credentials.
    (re.compile(r"(?i)" + _T + r"((?:set-)?cookie[ \t]{0,16}:[ \t]{0,16})([^\n\"']{1,4096})"), r"\1[REDACTED]"),
    # Azure-style connection strings.
    (re.compile(r"(?i)" + _T + r"((?:AccountKey|SharedAccessKey|SharedAccessSignature|sig)=)([^;\s&]{1,1024})"), r"\1[REDACTED]"),
    # curl -u user:pw, --user user:pw, --user=user:pw
    (re.compile(r"((?:^|(?<=\s))(?:-u[ \t]{0,16}|--user(?:[ \t]{1,16}|=))['\"]?[^\s:'\"]{1,256}:)([^\s'\"]{1,512})", re.MULTILINE), r"\1[REDACTED]"),
    # --password X, --password=X, --pass X, sshpass -p X
    (re.compile(r"(?i)((?:^|(?<=[^A-Za-z0-9_]))(?:--pass(?:word)?(?:[ \t]{1,16}|=)|sshpass[ \t]{1,16}-p[ \t]{0,16}))(\"[^\"\n]{0,512}\"|'[^'\n]{0,512}'|[^\s'\"]{1,512})", re.MULTILINE), r"\1[REDACTED]"),
    # mysql/mariadb -pSECRET glued to the flag; the gap to the flag is bounded so the scan stays linear
    (re.compile(r"(?i)(" + _T + r"(?:mysql|mariadb|mysqldump|mysqladmin)(?![A-Za-z0-9_])[^\n]{0,200}?\s-p)(\"[^\"\n]{0,512}\"|'[^'\n]{0,512}'|[^\s'\"]{1,512})"), r"\1[REDACTED]"),
    # natural language: "password is X", "senha: X"
    (re.compile(r"(?i)" + _T + r"((?:password|passphrase|senha|token|secret)[ \t]{1,16}(?:is|é|eh|=)[ \t]{1,16})(\S{1,512})"), r"\1[REDACTED]"),
)
_SECRET_ASSIGNMENT = re.compile(
    r"""(?ix)
    (?P<key>(?:\\?["'])?""" + _SECRET_KEY + r"""(?:\\?["'])?[ \t]{0,16}[:=][ \t]{0,16})
    (?P<value>\\"(?:[^"\\\n]|\\[^"]){0,1024}\\"|"(?:[^"\\\n]|\\.){0,1024}"|'[^'\n]{0,1024}'|[^\s,;}\]\)]{1,1024})
    """
)


class WikiJournalError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


# --------------------------------------------------------------------------- helpers


def _redact_assignment(match: re.Match[str]) -> str:
    value = match.group("value")
    if value.startswith('\\"') and value.endswith('\\"') and len(value) >= 4:
        quote, inner = '\\"', value[2:-2]
    elif value[:1] in {'"', "'"} and len(value) >= 2:
        quote, inner = value[0], value[1:-1]
    else:
        quote, inner = "", value
    if not inner:
        return match.group(0)
    if _PLAIN_VALUE.match(inner) and not (_ALWAYS_SECRET_KEY.search(match.group("key")) and inner.lower() not in {"true", "false", "null", "none", "[redacted]"}):
        return match.group(0)
    return f"{match.group('key')}{quote}[REDACTED]{quote}"


_KEY_BLOCK_BEGIN = re.compile(r"-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?-----")
_KEY_BLOCK_END = re.compile(r"-----END [A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?-----")


def _redact_key_blocks(text: str) -> str:
    """Replace each private-key block (to its END line, or to the end of text) in one linear pass."""
    parts: list[str] = []
    position = 0
    while True:
        begin = _KEY_BLOCK_BEGIN.search(text, position)
        if begin is None:
            parts.append(text[position:])
            return "".join(parts)
        parts.append(text[position:begin.start()])
        parts.append("[REDACTED]")
        end = _KEY_BLOCK_END.search(text, begin.end())
        if end is None:
            return "".join(parts)
        position = end.end()


def redact(text: str) -> str:
    """Mask common credential shapes; the wiki is synced and must never hold a secret.

    This is a safety net, not a guarantee: never paste a credential into a
    conversation or an artifact that is recorded.
    """
    text = _redact_key_blocks(text)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    for pattern, replacement in _SECRET_SUBSTITUTIONS:
        text = pattern.sub(replacement, text)
    return _SECRET_ASSIGNMENT.sub(_redact_assignment, text)


def one_line(value: Any, limit: int = 200) -> str:
    """A single Markdown-inert line: no line breaks, so it cannot forge a log entry."""
    return _LINE_BREAKS.sub(" ", str(value)).strip()[:limit]


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
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(value) if math.isfinite(value) else json.dumps(str(value))
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(json.dumps(one_line(item), ensure_ascii=False) for item in value) + "]"
    return json.dumps(one_line(value, 500), ensure_ascii=False)


def _frontmatter(fields: Mapping[str, Any]) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if value is None or value == "" or value == []:
            continue
        lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- controller and container


def _controller_root() -> Path:
    """The folder that holds this controller: runtime/ -> orchestration/ -> .hermes/ -> root."""
    return Path(__file__).resolve().parents[3]


def _controller_binding() -> tuple[str, Path, Path]:
    """Return ``(mode, controller_root, container)`` for the controller this module runs from.

    ``vault`` when the controller lives in the container its binding names,
    ``local`` when it lives in a repository whose binding names a container.
    """
    root = _controller_root()
    binding_file = root / obsidian_binding.BINDING_RELATIVE_PATH
    if binding_file.is_symlink() or not binding_file.is_file():
        raise WikiJournalError("WIKI_BINDING_MISSING", f"no Obsidian binding at {binding_file}")
    try:
        binding = obsidian_binding.load_path(binding_file)
    except obsidian_binding.BindingError as error:
        raise WikiJournalError("WIKI_BINDING_MISSING", str(error)) from error
    container = Path(os.path.realpath(binding.container_path))
    mode = "vault" if container == Path(os.path.realpath(root)) else "local"
    return mode, Path(os.path.realpath(root)), container


def _safe_workspace(path: Path) -> bool:
    """A registered workspace must be a real Git work tree, never ``/`` or the home folder."""
    if not path.is_absolute() or len(path.parts) < 3:
        return False
    home = Path(os.path.realpath(os.path.expanduser("~")))
    if path == home or path in home.parents:
        return False
    return os.path.lexists(path / ".git")


def registered_workspaces(container: Path) -> list[Path]:
    """Workspaces the installer set up for this container, read from each runtime STATE."""
    runtime = container / ".hermes-runtime"
    found: list[Path] = []
    if runtime.is_symlink() or not runtime.is_dir():
        return found
    for entry in sorted(runtime.iterdir()):
        state_file = entry / "STATE.md"
        if entry.is_symlink() or not entry.is_dir() or state_file.is_symlink() or not state_file.is_file():
            continue
        try:
            text = state_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        match = re.search(r"^workspace:[ \t]*\n[ \t]+path:[ \t]*\"?([^\"\n]+?)\"?[ \t]*$", text, re.MULTILINE)
        if not match:
            continue
        path = Path(os.path.realpath(match.group(1)))
        if _safe_workspace(path):
            found.append(path)
    return found


def _served_workspaces() -> tuple[Path, list[Path]]:
    """``(container, workspaces)`` this controller may record for."""
    mode, root, container = _controller_binding()
    if mode == "vault":
        return container, registered_workspaces(container)
    return container, [root]


def container_for(workspace: Path) -> Path:
    """Return the initialized container for a worktree this controller serves."""
    workspace = Path(os.path.realpath(workspace))
    container, workspaces = _served_workspaces()
    if workspace not in workspaces:
        raise WikiJournalError("WIKI_WORKSPACE_NOT_REGISTERED", f"{workspace} is not served by the controller of {container}")
    return prepare_container(container)


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


@contextlib.contextmanager
def _locked(root_fd: int) -> Iterator[None]:
    """Serialize index/log updates between the controller, hooks and journal mirrors."""
    if fcntl is None:
        yield
        return
    fd = os.open(LOCK_NAME, os.O_RDWR | os.O_CREAT | wiki_layout._NOFOLLOW | wiki_layout._CLOEXEC, 0o644, dir_fd=root_fd)
    try:
        status = os.fstat(fd)
        if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
            raise WikiJournalError("WIKI_PATH_UNSAFE", f"{LOCK_NAME} must be a regular file with a single link")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


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


def _append_or_create(root_fd: int, relative: str, header: bytes, entry: bytes, *, max_bytes: int | None = None) -> bool | None:
    """Append ``entry`` to a single-link regular file, creating it with ``header`` first.

    Returns True when created, False when appended, None when ``max_bytes`` would
    be exceeded (nothing written).
    """
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
            fd = os.open(
                name, os.O_WRONLY | os.O_APPEND | os.O_NONBLOCK | wiki_layout._NOFOLLOW | wiki_layout._CLOEXEC,
                dir_fd=directory,
            )
        try:
            status = os.fstat(fd)
            if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
                raise WikiJournalError("WIKI_PATH_UNSAFE", f"{relative} must be a regular file with a single link")
            if not created and max_bytes is not None and status.st_size + len(entry) > max_bytes:
                return None
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
    line = f"- [[{wiki_layout._link_target(relative)}|{PurePosixPath(relative).stem}]] — {one_line(summary, 140)}\n"
    position = text.index(heading) + len(heading)
    text = text[:position] + line + text[position:]
    text = re.sub(r"Total pages: \d+", f"Total pages: {len(wiki_layout._layer_two_pages(container))}", text, count=1)
    text = re.sub(r"Last updated: \S+", f"Last updated: {_now().date().isoformat()}", text, count=1)
    wiki_layout._replace_wiki_file(root_fd, "index.md", text)


def _rotate_log(root_fd: int, today: dt.date) -> None:
    """Keep log.md under MAX_LOG_ENTRIES: move it to log-YYYY.md (or _archive/) and start fresh."""
    try:
        text = wiki_layout._read_wiki_file(root_fd, "log.md")
    except FileNotFoundError:
        return
    if text.count("\n## [") < MAX_LOG_ENTRIES:
        return
    year = today.year
    target = f"log-{year}.md"
    if not _exists(root_fd, target):
        os.rename("log.md", target, src_dir_fd=root_fd, dst_dir_fd=root_fd)
    else:
        archive = wiki_layout._open_dir(root_fd, ("_archive",), create=True)
        try:
            for number in range(2, 100000):
                candidate = f"log-{year}-{number}.md"
                if not _exists(archive, candidate):
                    os.rename("log.md", candidate, src_dir_fd=root_fd, dst_dir_fd=archive)
                    break
        finally:
            os.close(archive)
    header = text.split("\n## [", 1)[0].rstrip("\n") + "\n"
    fd = os.open("log.md", os.O_WRONLY | os.O_CREAT | os.O_EXCL | wiki_layout._NOFOLLOW | wiki_layout._CLOEXEC, 0o644, dir_fd=root_fd)
    try:
        wiki_layout._write_all(fd, header.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def _exists(directory_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _log(root_fd: int, action: str, subject: str, lines: Iterable[str]) -> None:
    today = _now().date()
    try:
        _rotate_log(root_fd, today)
        wiki_layout.append_log_entry(root_fd, today.isoformat(), action, one_line(subject), [one_line(line, 300) for line in lines])
    except FileNotFoundError:
        pass


def _summary_line(body: str) -> str:
    for line in body.splitlines():
        line = line.strip().lstrip("#>-* ").strip()
        if line:
            return one_line(redact(line), 140)
    return "recorded by the SDD orchestrator"


def _metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key in METADATA_KEYS:
        value = (metadata or {}).get(key)
        if value is None or isinstance(value, (dict, list, tuple)):
            continue
        clean[key] = redact(one_line(value)) if isinstance(value, str) else value
    return clean


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
    clean_title = one_line(redact(title))
    # Truncate before redacting so the scan is bounded, then redact what is kept.
    clean_body = redact(_bounded(body))
    ticket = one_line(ticket, 120) if ticket else None
    stage = one_line(stage, 60) if stage else None
    session = one_line(session, 120) if session else None
    fields: dict[str, Any] = {
        "title": clean_title,
        "kind": kind,
        "recorded": when.isoformat().replace("+00:00", "Z"),
        "ticket": ticket,
        "stage": stage,
        "session": session,
        "source": "hermes-sdd-orchestrator",
        **_metadata(metadata),
    }
    container = Path(container)
    root_fd = wiki_layout._open_root(container)
    try:
        ticket_dir = slug(ticket or NO_TICKET, fallback=NO_TICKET)
        if kind in RAW_KINDS:
            name = f"{stamp}-{slug(stage)}-{slug(clean_title)}.md" if kind == "stage" and stage else f"{stamp}-{slug(clean_title)}.md"
            relative = str(PurePosixPath("raw/articles", ticket_dir, *(filter(None, [RAW_KINDS[kind]])), name))
            content = (_frontmatter(fields) + f"\n# {clean_title}\n\n{clean_body.rstrip()}\n").encode("utf-8")
            written = _create_unique(root_fd, relative, content)
            with _locked(root_fd):
                _log(root_fd, "ingest", f"{kind} — {clean_title}", [f"[[{wiki_layout._link_target(written)}]]"] + ([f"ticket: {ticket}"] if ticket else []))
            return {"status": "WRITTEN", "kind": kind, "path": written, "created": True}
        if kind == "turn":
            day = when.date().isoformat()
            session_slug = slug(session or clean_title, fallback="session")
            header_fields = {**fields, "title": f"Session {session or clean_title}", "kind": "transcript"}
            header = (_frontmatter(header_fields) + f"\n# Session {session or clean_title}\n").encode("utf-8")
            entry = f"\n## {when.isoformat().replace('+00:00', 'Z')} — {clean_title}\n\n{clean_body.rstrip()}\n".encode("utf-8")
            with _locked(root_fd):
                for part in range(1, 10000):
                    suffix = "" if part == 1 else f"-part{part}"
                    relative = str(PurePosixPath("raw/transcripts/sessions", f"{day}-{session_slug}{suffix}.md"))
                    created = _append_or_create(root_fd, relative, header, entry, max_bytes=MAX_TRANSCRIPT_BYTES)
                    if created is not None:
                        break
                else:  # pragma: no cover - 40 GB of one session in one day
                    raise WikiJournalError("WIKI_RECORD_NAME_EXHAUSTED", f"no free transcript part for {session_slug}")
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
        with _locked(root_fd):
            created = _append_or_create(root_fd, relative, header, entry)
            _index_page(container, root_fd, relative, _summary_line(clean_body))
            _log(root_fd, "create" if created else "update", f"{kind} — {clean_title}", [f"[[{wiki_layout._link_target(relative)}]]"])
        return {"status": "WRITTEN", "kind": kind, "path": relative, "created": bool(created)}
    except wiki_layout.WikiError as error:
        raise WikiJournalError(error.code, error.detail) from error
    except OSError as error:
        raise WikiJournalError("WIKI_PATH_UNSAFE", f"{error.filename or container}: {error.strerror or error}") from error
    finally:
        os.close(root_fd)


def record_for_workspace(workspace: Path, **kwargs: Any) -> dict[str, Any]:
    return record(container_for(workspace), **kwargs)


def safe_record_for_workspace(workspace: Path, **kwargs: Any) -> dict[str, Any]:
    """``record_for_workspace`` for automatic mirrors: never raises, returns SKIPPED instead."""
    try:
        return record_for_workspace(workspace, **kwargs)
    except WikiJournalError as error:
        return {"status": "SKIPPED", "reason": error.code, "detail": error.detail}
    except Exception as error:  # the caller must never fail because the wiki could not be written
        return {"status": "SKIPPED", "reason": "WIKI_RECORD_FAILED", "detail": f"{error.__class__.__name__}: {error}"}


# --------------------------------------------------------------------------- action journal mirror


def _read_validated_result(path_text: Any, expected_sha256: Any) -> str | None:
    """Return the executor's final message only when it is the validated result itself.

    The file itself must be a non-blocking, single-link regular file that is not
    a symlink (its folder is resolved, since macOS ``/tmp`` is one), its SHA-256
    must equal the journal's validated artifact hash, and it must parse as an
    ``executor_result``/``review_result`` JSON object. Anything else (another
    file, a FIFO, a key) is never read into the synced vault.
    """
    import hashlib

    if not isinstance(path_text, str) or not os.path.isabs(path_text) or not isinstance(expected_sha256, str):
        return None
    # Resolve the folder (macOS /tmp and /var are system symlinks) but never the file itself.
    folder, name = os.path.split(os.path.normpath(path_text))
    if not name or name in {".", ".."}:
        return None
    try:
        directory = os.open(os.path.realpath(folder), os.O_RDONLY | wiki_layout._DIRECTORY | wiki_layout._CLOEXEC)
    except OSError:
        return None
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | wiki_layout._NOFOLLOW | wiki_layout._CLOEXEC, dir_fd=directory)
    except OSError:
        return None
    finally:
        os.close(directory)
    try:
        status = os.fstat(fd)
        if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1 or status.st_size > MAX_RECORD_BYTES:
            return None
        data = b""
        while chunk := os.read(fd, 65536):
            data += chunk
            if len(data) > MAX_RECORD_BYTES:
                return None
    except OSError:
        return None
    finally:
        os.close(fd)
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        return None
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or not ({"executor_result", "review_result"} & set(value)):
        return None
    return json.dumps(value, ensure_ascii=False, indent=2)


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
    if action.get("parent_action_id"):
        lines.append(f"- parent action: {action.get('parent_action_id')}")
    if action.get("invalid_fields"):
        lines.append(f"- invalid fields: {', '.join(map(str, action['invalid_fields']))}")
    if artifact.get("validation_status") == "VALID":
        message = _read_validated_result(action.get("final_message_path"), artifact.get("sha256"))
        if message is not None:
            lines += ["", "## Executor result", "", "```json", message.rstrip(), "```"]
    return "\n".join(lines) + "\n"


def mirror_action(journal: Mapping[str, Any], *, outcome: str) -> dict[str, Any]:
    """Record one journal action in the wiki; never raises."""
    try:
        action = journal.get("action", {}) or {}
        workspace = Path((journal.get("workspace", {}) or {}).get("path", "") or ".")
        return safe_record_for_workspace(
            workspace,
            kind="action",
            title=f"{action.get('stage') or 'action'} {action.get('id') or ''} {outcome}".strip(),
            body=action_body(journal, outcome=outcome),
            ticket=action.get("ticket"),
            stage=action.get("stage"),
            metadata={"action_id": action.get("id"), "outcome": outcome},
        )
    except Exception as error:
        return {"status": "SKIPPED", "reason": "WIKI_RECORD_FAILED", "detail": f"{error.__class__.__name__}: {error}"}


# --------------------------------------------------------------------------- hooks


def _cwd_candidates(payload: Mapping[str, Any]) -> list[Path]:
    """The session's working folders: the hook payload's cwd and Hermes' TERMINAL_CWD."""
    candidates: list[Path] = []
    for value in (payload.get("cwd"), os.environ.get("TERMINAL_CWD")):
        if isinstance(value, str) and value and os.path.isabs(value):
            candidates.append(Path(os.path.realpath(value)))
    return candidates


def _inside(path: Path, workspace: Path) -> bool:
    return path.parts[: len(workspace.parts)] == workspace.parts


def hook_container(payload: Mapping[str, Any]) -> Path | None:
    """The container a hook event belongs to: only sessions running inside a served worktree."""
    try:
        container, workspaces = _served_workspaces()
    except WikiJournalError:
        return None
    for cwd in _cwd_candidates(payload):
        if any(_inside(cwd, workspace) for workspace in workspaces):
            return prepare_container(container)
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
        metadata={"model": extra.get("model"), "platform": extra.get("platform")},
    )


def _transcript_exists(container: Path, session: str) -> bool:
    folder = container / "raw" / "transcripts" / "sessions"
    if folder.is_symlink() or not folder.is_dir():
        return False
    session_slug = slug(session, fallback="session")
    pattern = re.compile(r"^\d{4}-\d{2}-\d{2}-" + re.escape(session_slug) + r"(?:-part\d+)?\.md$")
    return any(pattern.match(entry.name) for entry in folder.iterdir())


def record_session_end_event(payload: Mapping[str, Any]) -> dict[str, Any]:
    """``on_session_finalize``: log the end of a session that has a transcript in this wiki."""
    session = payload.get("session_id") or _extra(payload).get("session_id")
    if not isinstance(session, str) or not session:
        return {"status": "SKIPPED", "reason": "NO_SESSION"}
    try:
        container, _ = _served_workspaces()
    except WikiJournalError as error:
        return {"status": "SKIPPED", "reason": error.code}
    if not _transcript_exists(container, session):
        return {"status": "SKIPPED", "reason": "NO_TRANSCRIPT"}
    extra = _extra(payload)
    subject = one_line(f"session {session} ended")
    root_fd = wiki_layout._open_root(prepare_container(container))
    try:
        with _locked(root_fd):
            try:
                current = wiki_layout._read_wiki_file(root_fd, "log.md")
            except FileNotFoundError:
                current = ""
            if f"| {subject}\n" in current:
                return {"status": "SKIPPED", "reason": "ALREADY_LOGGED"}
            _log(root_fd, "update", subject, [f"reason: {extra.get('reason') or 'finalized'}", f"platform: {extra.get('platform') or 'unknown'}"])
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
    write = commands.add_parser("record", help="write one record into the wiki of a worktree this controller serves")
    write.add_argument("--repo", required=True, help="worktree served by this controller")
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
        report = record_for_workspace(
            Path(args.repo), kind=args.kind, title=args.title, body=_body(args),
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
