"""Repo-local Obsidian binding resolver for the SDD orchestrator.

The binding is the single source of truth for where this repository's Obsidian
second brain lives. No other module may build a vault path by string
concatenation.

Design constraints:

* Stdlib only. No Python on this machine ships PyYAML, so the binding is JSON.
* The runtime directory is keyed per WORKTREE, never per project: a repository
  can have many live worktrees and each one owns its own STATE/journal. Sharing
  one runtime path would break the "one executor at a time per worktree"
  guarantee.
* The runtime subpath starts with a dot so Obsidian never indexes it. Runtime is
  never a note.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

SCHEMA_VERSION = 1
BINDING_RELATIVE_PATH = ".hermes/obsidian.json"
VAULT_ENV = "HERMES_OBSIDIAN_VAULT"

#: Sentinel used by tests/builders to omit an optional key entirely.
OMIT = object()

_REQUIRED_FIELDS = ("schema_version", "vault_path", "project_container")
_DEFAULT_RUNTIME_SUBPATH = ".hermes-runtime"
_SLUG_HASH_LENGTH = 8
_SLUG_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class BindingError(Exception):
    """Raised when the binding is absent, malformed or unsafe.

    Carries a stable ``code`` so callers can branch without string matching and
    so the orchestrator can journal the failure.
    """

    def __init__(self, code: str, message: str, *, expected: str = "", actual: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.expected = expected
        self.actual = actual

    def __str__(self) -> str:  # pragma: no cover - trivial formatting
        parts = [f"{self.code}: {self.message}"]
        if self.expected:
            parts.append(f"expected={self.expected}")
        if self.actual:
            parts.append(f"actual={self.actual}")
        return " | ".join(parts)


@dataclass(frozen=True)
class Binding:
    """Resolved binding. ``vault_path`` is absolute; subpaths are relative."""

    vault_path: Path
    project_container: str
    runtime_subpath: str
    protocol_path: str
    raw: Dict[str, Any]

    @property
    def container_path(self) -> Path:
        return self.vault_path / self.project_container

    @property
    def runtime_root(self) -> Path:
        return self.container_path / self.runtime_subpath

    @property
    def protocol_abspath(self) -> Path:
        return self.vault_path / self.protocol_path


def binding_path(repo_root: Path) -> Path:
    """Return the binding governing ``repo_root``.

    A legacy repository-local binding wins when present. Otherwise, when this
    runtime is installed inside an Obsidian project container (the default
    storage), the container's own ``.hermes/obsidian.json`` is authoritative so
    the user's repository never has to carry any Hermes file.

    This precedence serves operator-invoked CLIs with an explicit ``--repo``.
    Hooks act on repository content that may be untrusted, so with a
    vault-resident controller they use only the container binding instead.
    """
    local = Path(repo_root) / BINDING_RELATIVE_PATH
    if local.is_file():
        return local
    installed = _installed_container_binding()
    return installed if installed is not None else local


def _installed_container_binding() -> Path | None:
    # runtime/ -> orchestration/ -> .hermes/ -> <project container>
    container = Path(__file__).resolve().parents[3]
    candidate = container / BINDING_RELATIVE_PATH
    return candidate if candidate.is_file() and not candidate.is_symlink() else None


def load(repo_root: Path) -> Binding:
    """Load and validate the binding for ``repo_root``.

    Raises BindingError with codes: BINDING_MISSING, BINDING_INVALID,
    BINDING_SCHEMA_UNSUPPORTED.
    """
    return load_path(binding_path(repo_root))


def load_path(path: Path) -> Binding:
    """Load and validate one explicit binding file (same error codes as ``load``)."""
    path = Path(path)
    if not path.is_file():
        raise BindingError(
            "BINDING_MISSING",
            "No Obsidian binding for this repository.",
            expected=str(path),
            actual="absent",
        )

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BindingError(
            "BINDING_INVALID",
            f"Binding is not valid JSON: {exc.__class__.__name__}.",
            expected="a JSON object",
            actual=str(path),
        ) from None

    if not isinstance(raw, dict):
        raise BindingError(
            "BINDING_INVALID",
            "Binding root must be a JSON object.",
            expected="object",
            actual=type(raw).__name__,
        )

    missing = [field for field in _REQUIRED_FIELDS if field not in raw]
    if missing:
        raise BindingError(
            "BINDING_INVALID",
            f"Binding is missing required field(s): {', '.join(missing)}.",
            expected=", ".join(_REQUIRED_FIELDS),
            actual=", ".join(sorted(raw)) or "empty object",
        )

    # Schema gate runs before any other semantic check: an unknown version may
    # legitimately carry fields this code cannot interpret.
    if raw["schema_version"] != SCHEMA_VERSION:
        raise BindingError(
            "BINDING_SCHEMA_UNSUPPORTED",
            "Binding schema version is not supported by this orchestrator.",
            expected=str(SCHEMA_VERSION),
            actual=repr(raw["schema_version"]),
        )

    vault_path = _resolve_vault_path(raw)
    project_container = _validate_container(raw["project_container"])
    runtime_subpath = _validate_runtime_subpath(
        raw.get("runtime_subpath", _DEFAULT_RUNTIME_SUBPATH)
    )

    return Binding(
        vault_path=vault_path,
        project_container=project_container,
        runtime_subpath=runtime_subpath,
        protocol_path=str(raw.get("protocol_path", "")),
        raw=raw,
    )


def _resolve_vault_path(raw: Dict[str, Any]) -> Path:
    """Vault path from the environment override, else from the binding.

    Risk R4: the binding is versioned but ``vault_path`` is machine-specific.
    A clone on another machine overrides via ``HERMES_OBSIDIAN_VAULT``. There is
    deliberately no silent fallback to a default location.
    """
    override = os.environ.get(VAULT_ENV)
    source_field = VAULT_ENV if override else "vault_path"
    value = override if override else raw["vault_path"]

    if not isinstance(value, str) or not value.strip():
        raise BindingError(
            "BINDING_INVALID",
            f"{source_field} must be a non-empty string.",
            expected="absolute path",
            actual=repr(value),
        )

    candidate = Path(value)
    if not candidate.is_absolute():
        raise BindingError(
            "BINDING_INVALID",
            f"{source_field} must be an absolute path.",
            expected="absolute path",
            actual=value,
        )
    return candidate


def _validate_container(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BindingError(
            "BINDING_INVALID",
            "project_container must be a non-empty string.",
            expected="path relative to the vault root",
            actual=repr(value),
        )
    if Path(value).is_absolute():
        raise BindingError(
            "BINDING_INVALID",
            "project_container must be relative to the vault root.",
            expected="relative path",
            actual=value,
        )
    if ".." in Path(value).parts:
        raise BindingError(
            "BINDING_INVALID",
            "project_container must not escape the vault root.",
            expected="no '..' segments",
            actual=value,
        )
    return value.strip("/")


def _validate_runtime_subpath(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BindingError(
            "BINDING_INVALID",
            "runtime_subpath must be a non-empty string.",
            expected="hidden directory name",
            actual=repr(value),
        )
    cleaned = value.strip("/")
    if Path(cleaned).is_absolute() or ".." in Path(cleaned).parts:
        raise BindingError(
            "BINDING_INVALID",
            "runtime_subpath must stay inside the project container.",
            expected="relative path without '..'",
            actual=value,
        )
    if not cleaned.startswith("."):
        raise BindingError(
            "BINDING_INVALID",
            "runtime_subpath must start with '.' so Obsidian never indexes it.",
            expected="a dot-prefixed directory",
            actual=value,
        )
    return cleaned


def worktree_slug(worktree_root: Path) -> str:
    """Stable, collision-resistant directory name for a worktree.

    The basename alone is NOT unique: two different repositories (or two
    worktree trees) can both hold ``app-455``. The absolute path hash suffix
    keeps runtime directories disjoint, which is what protects the
    one-executor-per-worktree guarantee.

    Symlinks are resolved first. On macOS ``/var`` is a symlink to
    ``/private/var``, so hashing the unresolved path would hand the SAME
    worktree two different runtime directories depending on which spelling the
    caller used — reintroducing the very contention this suffix prevents.
    """
    absolute = _resolve_existing(Path(worktree_root))
    digest = hashlib.sha256(
        str(absolute).encode("utf-8")
    ).hexdigest()[:_SLUG_HASH_LENGTH]
    base = _SLUG_SAFE.sub("-", absolute.name).strip("-.") or "worktree"
    return f"{base}-{digest}"


def runtime_dir(binding: Binding, worktree_root: Path) -> Path:
    return binding.runtime_root / worktree_slug(worktree_root)


def state_path(binding: Binding, worktree_root: Path) -> Path:
    return runtime_dir(binding, worktree_root) / "STATE.md"


def journal_path(binding: Binding, worktree_root: Path) -> Path:
    return runtime_dir(binding, worktree_root) / "ACTION_JOURNAL.json"


def is_inside_container(binding: Binding, path: Path) -> bool:
    """True when ``path`` resolves inside the bound project container.

    Symlinks are resolved BEFORE comparison: a link inside the container that
    points elsewhere must not pass. Comparison is on path parts, never on string
    prefixes, so ``Demo-archive`` cannot masquerade as ``Demo``.
    """
    container = _resolve_existing(binding.container_path)
    target = _resolve_existing(Path(path))
    return target.parts[: len(container.parts)] == container.parts


def _resolve_existing(path: Path) -> Path:
    """Resolve symlinks for the longest existing prefix of ``path``.

    ``Path.resolve()`` on a non-existent path does not resolve intermediate
    symlinks consistently across Python versions, so resolve the deepest
    existing ancestor and re-attach the remainder.
    """
    absolute = Path(os.path.abspath(str(path)))
    remainder: list[str] = []
    current = absolute
    while True:
        if current.exists():
            resolved = current.resolve()
            for part in reversed(remainder):
                resolved = resolved / part
            return _drop_dot_segments(resolved)
        if current.parent == current:
            return _drop_dot_segments(absolute)
        remainder.append(current.name)
        current = current.parent


def _drop_dot_segments(path: Path) -> Path:
    parts: list[str] = []
    for part in path.parts:
        if part == ".":
            continue
        if part == ".." and parts and parts[-1] not in ("..", os.sep, "/"):
            parts.pop()
            continue
        parts.append(part)
    return Path(*parts) if parts else path
