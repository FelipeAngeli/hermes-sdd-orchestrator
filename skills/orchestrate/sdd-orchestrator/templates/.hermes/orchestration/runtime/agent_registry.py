"""Deterministic, read-only capability registry for installed stage and role briefs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema

import executor_launch


class RegistryError(Exception):
    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _frontmatter(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RegistryError("REGISTRY_SOURCE_INVALID") from exc
    if not text.startswith("---\n"):
        raise RegistryError("REGISTRY_SOURCE_INVALID")
    end = text.find("\n---", 4)
    if end < 0:
        raise RegistryError("REGISTRY_SOURCE_INVALID")
    result: dict[str, Any] = {}
    for line in text[4:end].splitlines():
        # Playbook metadata may contain nested YAML.  The registry needs only
        # its top-level scalar/list fields, so nested data is deliberately inert.
        if line[:1].isspace():
            continue
        if ":" not in line:
            raise RegistryError("REGISTRY_SOURCE_INVALID")
        key, value = (part.strip() for part in line.split(":", 1))
        if not key or key in result:
            raise RegistryError("REGISTRY_SOURCE_INVALID")
        if value.startswith("[") and value.endswith("]"):
            result[key] = [item.strip() for item in value[1:-1].split(",") if item.strip()]
        elif value:
            result[key] = value
        elif key == "metadata":
            result[key] = {}
        else:
            raise RegistryError("REGISTRY_SOURCE_INVALID")
    return result


def _tools(value: Any) -> list[str] | None:
    if value is None or value == "default":
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def _stage_agent(root: Path, path: Path, policy: dict[str, Any]) -> dict[str, Any]:
    meta = _frontmatter(path)
    name, stage = meta.get("name"), meta.get("stage")
    if not all(isinstance(meta.get(key), str) and meta[key] for key in ("name", "stage", "executor_policy", "result_schema")):
        raise RegistryError("REGISTRY_SOURCE_INVALID")
    try:
        settings = executor_launch.stage_settings(policy, stage, executor=None, model=None, timeout=None)
    except executor_launch.LaunchError as exc:
        raise RegistryError(exc.status) from exc
    return {
        "agent_id": name, "kind": "STAGE", "role": None, "stages": [stage],
        "source": path.relative_to(root).as_posix(), "source_sha256": _hash(path),
        "executor_policy": meta.get("executor_policy"), "result_schema": meta.get("result_schema"), "leaf_only": True,
        "executor": settings["executor"], "model": settings.get("model"),
        "timeout_seconds": settings.get("timeout_seconds"), "max_turns": settings.get("max_turns"),
        "tools": _tools(settings.get("tools")), "sandbox": settings.get("sandbox"),
        "permission_mode": settings.get("permission_mode"), "writes": executor_launch.is_writing_worker(settings),
    }


def _role_agent(root: Path, path: Path) -> dict[str, Any]:
    meta = _frontmatter(path)
    name, role, stages = meta.get("name"), meta.get("role"), meta.get("allowed_stages")
    if (not isinstance(name, str) or not name or not isinstance(role, str) or not role or not isinstance(meta.get("executor_policy"), str)
            or not isinstance(meta.get("result_schema"), str) or not isinstance(stages, list) or not stages
            or not all(stage in executor_launch.STAGES for stage in stages) or len(set(stages)) != len(stages)):
        raise RegistryError("REGISTRY_SOURCE_INVALID")
    return {
        "agent_id": name, "kind": "ROLE", "role": role, "stages": stages,
        "source": path.relative_to(root).as_posix(), "source_sha256": _hash(path),
        "executor_policy": meta.get("executor_policy"), "result_schema": meta.get("result_schema"), "leaf_only": True,
        "executor": None, "model": None, "timeout_seconds": None, "max_turns": None,
        "tools": None, "sandbox": None, "permission_mode": None, "writes": False,
    }


def _playbooks(root: Path) -> list[dict[str, Any]]:
    directory = root.parent / "skills"
    values = []
    # Project skills may come from third parties (vendored TypeSafe skill, folded YAML, no version).
    # They are capabilities, never agents, so an unreadable one is listed as UNPARSED instead of
    # breaking the registry; it is still hashed, so a change to it is still visible.
    for path in sorted(directory.glob("*/SKILL.md")) if directory.is_dir() else []:
        try:
            meta = _frontmatter(path)
        except RegistryError:
            meta = None
        try:
            digest = _hash(path)
        except OSError as exc:
            # Bytes that cannot be hashed cannot be vouched for: fail closed.
            raise RegistryError("REGISTRY_SOURCE_INVALID") from exc
        name = meta.get("name") if meta else None
        version = meta.get("version") if meta else None
        values.append({"name": name if isinstance(name, str) and name else path.parent.name,
                       "version": version if isinstance(version, str) and version else None,
                       "status": "PARSED" if meta is not None else "UNPARSED",
                       "source": (Path("..") / path.relative_to(root.parent)).as_posix(), "source_sha256": digest})
    return sorted(values, key=lambda item: item["name"])


def build_registry(root: Path) -> dict[str, Any]:
    """Build a validated-in-source registry without creating or modifying files."""
    root = Path(root).resolve()
    policy_path = root / "policies" / "EXECUTORS.md"
    try:
        policy = executor_launch.load_policy(policy_path)
    except executor_launch.LaunchError as exc:
        raise RegistryError(exc.status) from exc
    agents = [_stage_agent(root, path, policy) for path in sorted((root / "agents").glob("*.md"))]
    agents += [_role_agent(root, path) for path in sorted((root / "sub-agents").glob("*.md"))]
    if len({agent["agent_id"] for agent in agents}) != len(agents):
        raise RegistryError("REGISTRY_SOURCE_INVALID")
    sources = {"EXECUTORS.md": _hash(policy_path) if policy_path.is_file() else "DEFAULTS"}
    result = {"registry_version": 1, "agents": sorted(agents, key=lambda item: item["agent_id"]),
              "playbooks": _playbooks(root), "sources": sources}
    canonical = json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8")
    result["registry_sha256"] = hashlib.sha256(canonical).hexdigest()
    try:
        schema = json.loads((root / "schemas" / "AGENT_REGISTRY_SCHEMA.json").read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(result)
    except (OSError, UnicodeError, ValueError, jsonschema.ValidationError) as exc:
        raise RegistryError("REGISTRY_SOURCE_INVALID") from exc
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = build_registry(args.root)
    except RegistryError as exc:
        print(json.dumps({"status": exc.status}) if args.json else exc.status)
        return 2
    print(json.dumps(result, sort_keys=True) if args.json else result["registry_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
