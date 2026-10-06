#!/usr/bin/env python3
"""Render persistent, terminal-friendly progress for one local SDD run."""
from __future__ import annotations

import argparse
import contextlib
import copy
import json
import os
import secrets
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

if os.name == "posix":
    import fcntl

SCHEMA_VERSION = 1
STAGES = ("SPECIFY", "CLARIFY", "PLAN", "TASKS", "IMPLEMENT", "TEST", "REVIEW", "DONE")
MAX_ACTIVITIES = 20
MAX_TEXT = 240
MAX_FILE_BYTES = 256 * 1024
DEFAULT_PROGRESS_PATH = Path(__file__).resolve().parents[1] / "TERMINAL_PROGRESS.json"


class ProgressError(ValueError):
    """Progress input or persisted presentation state is invalid."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _instant(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProgressError("PROGRESS_TIME_INVALID")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_instant(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ProgressError("PROGRESS_STATE_INVALID")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ProgressError("PROGRESS_STATE_INVALID") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProgressError("PROGRESS_STATE_INVALID")
    return parsed.astimezone(timezone.utc)


def _text(value: Any, code: str = "PROGRESS_INPUT_INVALID") -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > MAX_TEXT
        or not value.isprintable()
    ):
        raise ProgressError(code)
    return value.strip()


def _stage(value: Any) -> str:
    if value not in STAGES:
        raise ProgressError("PROGRESS_STAGE_INVALID")
    return str(value)


def _seconds(started: Any, finished: datetime) -> int:
    start = _parse_instant(started)
    if finished < start:
        raise ProgressError("PROGRESS_TIME_INVALID")
    return int((finished - start).total_seconds())


def new_progress(provider: str, stage: str, at: datetime | None = None) -> dict[str, Any]:
    moment = at or utc_now()
    provider = _text(provider)
    stage = _stage(stage)
    stamp = _instant(moment)
    return {
        "schema_version": SCHEMA_VERSION,
        "run": {
            "provider": provider,
            "status": "RUNNING",
            "started_at": stamp,
            "finished_at": None,
        },
        "stage": {
            "current": stage,
            "started_at": stamp,
            "history": [],
        },
        "activities": [],
        "jev": {
            "active": False,
            "provider": None,
            "model": None,
            "area": None,
            "questions": [],
            "started_at": None,
            "finished_at": None,
            "elapsed_seconds": None,
            "status": "NÃO USADO",
        },
        "updated_at": stamp,
    }


def validate_progress(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schema_version", "run", "stage", "activities", "jev", "updated_at"}:
        raise ProgressError("PROGRESS_STATE_INVALID")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ProgressError("PROGRESS_STATE_INVALID")
    run = value.get("run")
    stage = value.get("stage")
    jev = value.get("jev")
    activities = value.get("activities")
    if not isinstance(run, dict) or set(run) != {"provider", "status", "started_at", "finished_at"}:
        raise ProgressError("PROGRESS_STATE_INVALID")
    _text(run.get("provider"), "PROGRESS_STATE_INVALID")
    if run.get("status") not in {"RUNNING", "DONE", "BLOCKED", "PAUSED"}:
        raise ProgressError("PROGRESS_STATE_INVALID")
    _parse_instant(run.get("started_at"))
    if run.get("finished_at") is not None:
        _parse_instant(run["finished_at"])
    if not isinstance(stage, dict) or set(stage) != {"current", "started_at", "history"}:
        raise ProgressError("PROGRESS_STATE_INVALID")
    _stage(stage.get("current"))
    _parse_instant(stage.get("started_at"))
    if not isinstance(stage.get("history"), list):
        raise ProgressError("PROGRESS_STATE_INVALID")
    seen: set[str] = set()
    for entry in stage["history"]:
        if not isinstance(entry, dict) or set(entry) != {"name", "status", "started_at", "finished_at", "elapsed_seconds"}:
            raise ProgressError("PROGRESS_STATE_INVALID")
        name = _stage(entry.get("name"))
        if name in seen or entry.get("status") not in {"COMPLETED", "SKIPPED", "BLOCKED", "PAUSED"}:
            raise ProgressError("PROGRESS_STATE_INVALID")
        seen.add(name)
        started, finished = _parse_instant(entry.get("started_at")), _parse_instant(entry.get("finished_at"))
        if finished < started or entry.get("elapsed_seconds") != int((finished - started).total_seconds()):
            raise ProgressError("PROGRESS_STATE_INVALID")
    if not isinstance(activities, list) or len(activities) > MAX_ACTIVITIES:
        raise ProgressError("PROGRESS_STATE_INVALID")
    for activity in activities:
        if not isinstance(activity, dict) or set(activity) != {"at", "stage", "message"}:
            raise ProgressError("PROGRESS_STATE_INVALID")
        _parse_instant(activity.get("at"))
        _stage(activity.get("stage"))
        _text(activity.get("message"), "PROGRESS_STATE_INVALID")
    expected_jev = {"active", "provider", "model", "area", "questions", "started_at", "finished_at", "elapsed_seconds", "status"}
    if not isinstance(jev, dict) or set(jev) != expected_jev or not isinstance(jev.get("active"), bool):
        raise ProgressError("PROGRESS_STATE_INVALID")
    for key in ("provider", "model", "area"):
        if jev.get(key) is not None:
            _text(jev[key], "PROGRESS_STATE_INVALID")
    if not isinstance(jev.get("questions"), list) or len(jev["questions"]) > 64:
        raise ProgressError("PROGRESS_STATE_INVALID")
    for question in jev["questions"]:
        _text(question, "PROGRESS_STATE_INVALID")
    for key in ("started_at", "finished_at"):
        if jev.get(key) is not None:
            _parse_instant(jev[key])
    if jev.get("elapsed_seconds") is not None and (not isinstance(jev["elapsed_seconds"], int) or jev["elapsed_seconds"] < 0):
        raise ProgressError("PROGRESS_STATE_INVALID")
    _text(jev.get("status"), "PROGRESS_STATE_INVALID")
    _parse_instant(value.get("updated_at"))
    return value


def _running(value: dict[str, Any]) -> None:
    validate_progress(value)
    if value["run"]["status"] != "RUNNING":
        raise ProgressError("PROGRESS_RUN_FINISHED")


def record_activity(value: dict[str, Any], message: str, at: datetime | None = None) -> dict[str, Any]:
    _running(value)
    changed = copy.deepcopy(value)
    stamp = _instant(at or utc_now())
    changed["activities"].append({"at": stamp, "stage": changed["stage"]["current"], "message": _text(message)})
    changed["activities"] = changed["activities"][-MAX_ACTIVITIES:]
    changed["updated_at"] = stamp
    return validate_progress(changed)


def enter_stage(
    value: dict[str, Any],
    stage: str,
    at: datetime | None = None,
    *,
    previous_status: str = "COMPLETED",
) -> dict[str, Any]:
    _running(value)
    target = _stage(stage)
    current = value["stage"]["current"]
    current_index = STAGES.index(current)
    target_index = STAGES.index(target)
    skips_only_clarify = current == "SPECIFY" and target == "PLAN"
    if (
        (target_index != current_index + 1 and not skips_only_clarify)
        or previous_status not in {"COMPLETED", "SKIPPED"}
        or (previous_status == "SKIPPED" and current != "CLARIFY")
    ):
        raise ProgressError("PROGRESS_TRANSITION_INVALID")
    moment = at or utc_now()
    stamp = _instant(moment)
    changed = copy.deepcopy(value)
    changed["stage"]["history"].append({
        "name": current,
        "status": previous_status,
        "started_at": changed["stage"]["started_at"],
        "finished_at": stamp,
        "elapsed_seconds": _seconds(changed["stage"]["started_at"], moment),
    })
    for skipped in STAGES[current_index + 1:target_index]:
        changed["stage"]["history"].append({
            "name": skipped,
            "status": "SKIPPED",
            "started_at": stamp,
            "finished_at": stamp,
            "elapsed_seconds": 0,
        })
    changed["stage"]["current"] = target
    changed["stage"]["started_at"] = stamp
    changed["updated_at"] = stamp
    return validate_progress(changed)


def start_jev(
    value: dict[str, Any],
    *,
    provider: str,
    area: str,
    questions: list[str],
    at: datetime | None = None,
) -> dict[str, Any]:
    _running(value)
    if value["jev"]["active"] or not questions or len(questions) > 64:
        raise ProgressError("PROGRESS_JEV_INVALID")
    stamp = _instant(at or utc_now())
    changed = copy.deepcopy(value)
    changed["jev"] = {
        "active": True,
        "provider": _text(provider),
        "model": None,
        "area": _text(area),
        "questions": [_text(question) for question in questions],
        "started_at": stamp,
        "finished_at": None,
        "elapsed_seconds": None,
        "status": "EM USO",
    }
    changed["updated_at"] = stamp
    return validate_progress(changed)


def finish_jev(
    value: dict[str, Any],
    status: str,
    at: datetime | None = None,
    *,
    model: str | None = None,
) -> dict[str, Any]:
    _running(value)
    if not value["jev"]["active"]:
        raise ProgressError("PROGRESS_JEV_INVALID")
    moment = at or utc_now()
    stamp = _instant(moment)
    changed = copy.deepcopy(value)
    changed["jev"].update({
        "active": False,
        "model": _text(model) if model is not None else None,
        "finished_at": stamp,
        "elapsed_seconds": _seconds(changed["jev"]["started_at"], moment),
        "status": _text(status),
    })
    changed["updated_at"] = stamp
    return validate_progress(changed)


def finish_run(value: dict[str, Any], status: str, at: datetime | None = None) -> dict[str, Any]:
    _running(value)
    if status not in {"DONE", "BLOCKED", "PAUSED"}:
        raise ProgressError("PROGRESS_STATUS_INVALID")
    moment = at or utc_now()
    stamp = _instant(moment)
    changed = copy.deepcopy(value)
    if changed["jev"]["active"]:
        raise ProgressError("PROGRESS_JEV_ACTIVE")
    current = changed["stage"]["current"]
    if status == "DONE" and current not in {"REVIEW", "DONE"}:
        raise ProgressError("PROGRESS_TRANSITION_INVALID")
    changed["stage"]["history"].append({
        "name": current,
        "status": "COMPLETED" if status == "DONE" else status,
        "started_at": changed["stage"]["started_at"],
        "finished_at": stamp,
        "elapsed_seconds": _seconds(changed["stage"]["started_at"], moment),
    })
    if status == "DONE" and current == "REVIEW":
        changed["stage"]["current"] = "DONE"
        changed["stage"]["started_at"] = stamp
        changed["stage"]["history"].append({
            "name": "DONE",
            "status": "COMPLETED",
            "started_at": stamp,
            "finished_at": stamp,
            "elapsed_seconds": 0,
        })
    changed["run"].update({"status": status, "finished_at": stamp})
    changed["updated_at"] = stamp
    return validate_progress(changed)


def _duration(seconds: int) -> str:
    hours, remainder = divmod(max(0, seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def _paint(text: str, code: str, enabled: bool) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if enabled else text


def render(value: dict[str, Any], *, now: datetime | None = None, color: bool = False) -> str:
    validate_progress(value)
    moment = now or utc_now()
    current = value["stage"]["current"]
    current_index = STAGES.index(current)
    remaining = len(STAGES) - current_index - 1
    elapsed = _seconds(value["stage"]["started_at"], moment) if value["run"]["status"] == "RUNNING" else 0
    lines = [
        _paint("╭─ HERMES SDD · PROGRESSO", "1;36", color),
        f"│ Provider       {value['run']['provider']}",
        f"│ Fase atual     {current} ({current_index + 1}/{len(STAGES)})",
        f"│ Fases restantes {remaining}",
        f"│ Estado         {value['run']['status']}",
        "│",
        "│ Tempos por fase",
    ]
    for entry in value["stage"]["history"]:
        marker = {"COMPLETED": "✓", "SKIPPED": "↷", "BLOCKED": "!", "PAUSED": "Ⅱ"}[entry["status"]]
        lines.append(f"│   {marker} {entry['name']:<10} {_duration(entry['elapsed_seconds'])} · {entry['status'].lower()}")
    if value["run"]["status"] == "RUNNING":
        lines.append(f"│   ▶ {current:<10} {_duration(elapsed)} · em andamento")
    jev = value["jev"]
    lines.extend(["│", "│ Jev"])
    if jev["active"]:
        jev_elapsed = _seconds(jev["started_at"], moment)
        lines.append(f"│   Jev            em uso · {jev['provider']} · {_duration(jev_elapsed)}")
    elif jev["started_at"] is not None:
        lines.append(f"│   Jev            concluído · {jev['provider']} · {_duration(jev['elapsed_seconds'])}")
    else:
        lines.append("│   Jev            não usado")
    if jev["area"]:
        lines.append(f"│   Atuação         {jev['area']}")
    if jev["questions"]:
        lines.append(f"│   Decisões        {', '.join(jev['questions'])}")
    if jev["model"]:
        lines.append(f"│   Modelo          {jev['model']}")
    if jev["status"] not in {"NÃO USADO", "EM USO"}:
        lines.append(f"│   Resultado       {jev['status']}")
    lines.extend(["│", "│ Atividade recente"])
    if value["activities"]:
        for activity in value["activities"][-5:]:
            clock = _parse_instant(activity["at"]).strftime("%H:%M:%S")
            lines.append(f"│   {clock} [{activity['stage']}] {activity['message']}")
    else:
        lines.append("│   nenhuma atividade registrada")
    lines.append("╰────────────────────────────")
    return "\n".join(lines)


def _require_supported_platform() -> None:
    if os.name != "posix":
        raise ProgressError("PROGRESS_PLATFORM_UNSUPPORTED")


def _open_parent(path: Path, *, create: bool) -> tuple[int, str]:
    _require_supported_platform()
    anchor = path.anchor or "."
    parts = path.parts[1:] if path.anchor else path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ProgressError("PROGRESS_FILE_INVALID")
    descriptor: int | None = None
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        descriptor = os.open(anchor, flags)
        for part in parts[:-1]:
            try:
                next_descriptor = os.open(part, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, 0o700, dir_fd=descriptor)
                next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor, parts[-1]
    except (OSError, ProgressError) as error:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if isinstance(error, ProgressError):
            raise
        raise ProgressError("PROGRESS_FILE_INVALID") from error


def _private_regular(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_size <= MAX_FILE_BYTES
        and not (stat.S_IMODE(metadata.st_mode) & ~0o600)
        and (not hasattr(os, "getuid") or metadata.st_uid == os.getuid())
    )


@contextlib.contextmanager
def _progress_lock(path: Path):
    parent, name = _open_parent(path, create=True)
    lock_name = f".{name}.lock"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            lock_name,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        if not _private_regular(os.fstat(descriptor)):
            raise ProgressError("PROGRESS_FILE_INVALID")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield parent, name
    except ProgressError:
        raise
    except OSError as error:
        raise ProgressError("PROGRESS_FILE_INVALID") from error
    finally:
        if descriptor is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(descriptor)
        os.close(parent)


def _load_progress_at(parent: int, name: str) -> dict[str, Any]:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent,
        )
        metadata = os.fstat(descriptor)
        if not _private_regular(metadata):
            raise ProgressError("PROGRESS_FILE_INVALID")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            encoded = stream.read(MAX_FILE_BYTES + 1)
        if len(encoded) > MAX_FILE_BYTES:
            raise ProgressError("PROGRESS_FILE_INVALID")
        return validate_progress(json.loads(encoded.decode("utf-8")))
    except ProgressError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ProgressError("PROGRESS_FILE_INVALID") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _progress_exists_at(parent: int, name: str) -> bool:
    try:
        metadata = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as error:
        raise ProgressError("PROGRESS_FILE_INVALID") from error
    if not _private_regular(metadata):
        raise ProgressError("PROGRESS_FILE_INVALID")
    return True


def _save_progress_at(parent: int, name: str, value: dict[str, Any]) -> None:
    validate_progress(value)
    encoded = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    try:
        existing = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent,
        )
    except FileNotFoundError:
        existing = -1
    except OSError as error:
        raise ProgressError("PROGRESS_FILE_INVALID") from error
    if existing >= 0:
        try:
            if not _private_regular(os.fstat(existing)):
                raise ProgressError("PROGRESS_FILE_INVALID")
        finally:
            os.close(existing)
    descriptor = -1
    temporary = ""
    try:
        for _ in range(128):
            temporary = f".{name}.{secrets.token_hex(8)}"
            try:
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=parent,
                )
                break
            except FileExistsError:
                continue
        if descriptor < 0:
            raise ProgressError("PROGRESS_FILE_INVALID")
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    except ProgressError:
        raise
    except OSError as error:
        raise ProgressError("PROGRESS_FILE_INVALID") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass


def load_progress(path: Path) -> dict[str, Any]:
    with _progress_lock(path) as (parent, name):
        return _load_progress_at(parent, name)


def save_progress(path: Path, value: dict[str, Any]) -> None:
    with _progress_lock(path) as (parent, name):
        _save_progress_at(parent, name, value)


def update_progress(
    path: Path,
    transform: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    with _progress_lock(path) as (parent, name):
        changed = transform(_load_progress_at(parent, name))
        _save_progress_at(parent, name, changed)
        return changed


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--file", type=Path, default=DEFAULT_PROGRESS_PATH)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-color", action="store_true")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start")
    start.add_argument("--provider", required=True)
    start.add_argument("--stage", choices=STAGES, default="SPECIFY")
    start.add_argument("--activity")
    stage = commands.add_parser("stage")
    stage.add_argument("--name", choices=STAGES, required=True)
    stage.add_argument("--previous-status", choices=("COMPLETED", "SKIPPED"), default="COMPLETED")
    activity = commands.add_parser("activity")
    activity.add_argument("--message", required=True)
    jev_start = commands.add_parser("jev-start")
    jev_start.add_argument("--provider", required=True)
    jev_start.add_argument("--area", required=True)
    jev_start.add_argument("--question", action="append", required=True)
    jev_finish = commands.add_parser("jev-finish")
    jev_finish.add_argument("--status", required=True)
    jev_finish.add_argument("--model")
    finish = commands.add_parser("finish")
    finish.add_argument("--status", choices=("DONE", "BLOCKED", "PAUSED"), required=True)
    commands.add_parser("show")
    for command in commands.choices.values():
        _common(command)
    args = parser.parse_args(argv)
    try:
        with _progress_lock(args.file) as (parent, name):
            if args.command == "start":
                if _progress_exists_at(parent, name):
                    existing = _load_progress_at(parent, name)
                    if existing["run"]["status"] == "RUNNING":
                        raise ProgressError("PROGRESS_RUN_ACTIVE")
                value = new_progress(args.provider, args.stage)
                if args.activity:
                    value = record_activity(value, args.activity)
            else:
                value = _load_progress_at(parent, name)
                if args.command == "stage":
                    value = enter_stage(value, args.name, previous_status=args.previous_status)
                elif args.command == "activity":
                    value = record_activity(value, args.message)
                elif args.command == "jev-start":
                    value = start_jev(value, provider=args.provider, area=args.area, questions=args.question)
                elif args.command == "jev-finish":
                    value = finish_jev(value, args.status, model=args.model)
                elif args.command == "finish":
                    value = finish_run(value, args.status)
            if args.command != "show":
                _save_progress_at(parent, name, value)
        if args.json:
            print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        else:
            color = not args.no_color and os.getenv("NO_COLOR") is None and sys.stdout.isatty()
            print(render(value, color=color))
        return 0
    except ProgressError as error:
        payload = {"status": "BLOCKED", "reason": str(error)}
        print(json.dumps(payload, sort_keys=True) if getattr(args, "json", False) else f"BLOCKED: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
