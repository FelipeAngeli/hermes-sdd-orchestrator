"""INSTALL_MANIFEST.json: what this installer wrote, so an upgrade can tell
pristine files from owner edits."""
from __future__ import annotations

import datetime
import hashlib
import json
import re

from .constants import (
    INSTALL_MANIFEST_VERSION,
    OWNER_FILES,
    PROJECT_SETUP_PATH,
    SKILL_MANIFEST,
    TEMPLATE,
)
from .errors import InstallError, _load_unique_json
from .templates import template_files

_VERSION = re.compile(r"^version:\s*([0-9]+(?:\.[0-9]+)*)\s*$", re.M)


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def skill_version() -> str:
    """The `version:` of the SKILL.md this installer ships with."""
    text = SKILL_MANIFEST.read_text(encoding="utf-8")
    header = text.split("---", 2)[1] if text.startswith("---") else ""
    match = _VERSION.search(header)
    if match is None:
        raise InstallError("SKILL_VERSION_UNAVAILABLE", next_step=f"Restore the version field of {SKILL_MANIFEST}.")
    return match.group(1)


def version_key(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def template_payload() -> dict[str, bytes]:
    """Root-relative path -> bytes of every distributable template file."""
    return {source.relative_to(TEMPLATE).as_posix(): source.read_bytes() for source in template_files()}


def manifest_body(storage: str, payload: dict[str, bytes] | None = None) -> dict[str, object]:
    """Manifest content without its timestamp, as the current template would write it."""
    payload = template_payload() if payload is None else payload
    owner_files: dict[str, dict[str, object]] = {
        relative: {"template_sha256": sha256(content)}
        for relative, content in sorted(payload.items())
        if relative in OWNER_FILES
    }
    owner_files[PROJECT_SETUP_PATH] = {"template_sha256": None}
    return {
        "manifest_version": INSTALL_MANIFEST_VERSION,
        "skill_version": skill_version(),
        "storage": storage,
        "files": {
            relative: {"sha256": sha256(content), "origin": "template"}
            for relative, content in sorted(payload.items())
            if relative not in OWNER_FILES
        },
        "owner_files": dict(sorted(owner_files.items())),
    }


def manifest_bytes(body: dict[str, object]) -> bytes:
    stamped = dict(body)
    stamped["written_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (json.dumps(stamped, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def install_manifest(storage: str) -> bytes:
    """The manifest a fresh installation writes in its own transaction."""
    return manifest_bytes(manifest_body(storage))


def parse_manifest(content: bytes) -> dict[str, object]:
    """Validate a stored manifest; a malformed one never silently becomes a baseline."""
    try:
        value = _load_unique_json(content.decode("utf-8"))
    except (UnicodeError, ValueError) as error:
        raise _invalid(f"not strict UTF-8 JSON ({error})") from error
    if not isinstance(value, dict) or value.get("manifest_version") != INSTALL_MANIFEST_VERSION:
        raise _invalid("unsupported manifest_version")
    version = value.get("skill_version")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version):
        raise _invalid("skill_version is not a dotted number")
    files = value.get("files")
    if not isinstance(files, dict) or not all(
        isinstance(path, str) and isinstance(entry, dict) and isinstance(entry.get("sha256"), str)
        for path, entry in files.items()
    ):
        raise _invalid("files is not a map of {sha256, origin}")
    if not isinstance(value.get("owner_files"), dict):
        raise _invalid("owner_files is not a map")
    return value


def _invalid(detail: str) -> InstallError:
    return InstallError(
        f"UPGRADE_MANIFEST_INVALID: {detail}",
        next_step=(
            "Move INSTALL_MANIFEST.json aside (keep it for review), then rerun --upgrade with "
            "--accept-current-as-baseline after comparing the controller files with the template."
        ),
    )
