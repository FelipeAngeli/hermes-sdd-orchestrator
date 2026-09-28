#!/usr/bin/env python3
"""Move orchestration knowledge and runtime from a repository into the vault.

Dry run by default. ``--apply`` performs the migration with a copy → verify
hash → remove sequence, never a bare ``mv``: an interrupted run must leave the
source intact rather than a half-moved tree.

Refusals are deliberate. The tool stops on an open demand, reports a corrupt
STATE instead of guessing a repair, and never overwrites a destination that
already holds different content.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import obsidian_binding
import state_format
import vault_guard
from obsidian_binding import Binding

#: Files that stay in the repository: contracts, schemas and executable tooling.
KEEP_SUFFIXES = (".py",)
KEEP_NAMES = {
    ".hermes.md",
    "LOOP_POLICY.md",
    "GATES.md",
    "EXECUTOR_CONTRACT.md",
    "EXECUTOR_RESULT_SCHEMA.json",
    "REVIEW_CONTRACT.md",
    "REVIEW_RESULT_SCHEMA.json",
    "ACTION_RECOVERY.md",
    "ACTION_JOURNAL_SCHEMA.json",
    "BOUNDED_AUTOMATION.md",
    "BOUNDED_RUN_PLAN_SCHEMA.json",
    "BOUNDED_RUN_DRIVER.md",
    "BOOTSTRAP.md",
    "obsidian.json",
}

#: Runtime files, migrated to the per-worktree runtime directory.
RUNTIME_NAMES = ("STATE.md", "INCIDENTS.md", "ACTION_JOURNAL.json")

#: Knowledge directories under .hermes/orchestration/ migrated into runtime.
RUNTIME_DIRECTORIES = (
    "history",
    "investigations",
    "plans",
    "runs",
    "audits",
    "handoffs",
    "action-journal-history",
    "tasks",
    "delivery-local-20260917",
)

DISCARD_DIRECTORIES = ("__pycache__",)

_HASH_CHUNK = 65536


class MigrationRefused(Exception):
    def __init__(self, code: str, message: str, *, detail: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail

    def __str__(self) -> str:  # pragma: no cover - trivial formatting
        return f"{self.code}: {self.message}" + (f" | {self.detail}" if self.detail else "")


@dataclass(frozen=True)
class Move:
    source: Path
    destination: Path
    kind: str  # runtime | knowledge | task
    normalise_state: bool = False


@dataclass
class Plan:
    repo: Path
    binding: Binding
    moves: List[Move] = field(default_factory=list)
    discards: List[Path] = field(default_factory=list)
    problems: List[Dict[str, str]] = field(default_factory=list)
    skipped: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class Result:
    moved: List[Move] = field(default_factory=list)
    conflicts: List[Dict[str, str]] = field(default_factory=list)
    discarded: List[Path] = field(default_factory=list)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def state_is_open(text: str) -> bool:
    """True when STATE describes a demand still in flight."""
    try:
        data = state_format.parse(text)
    except state_format.StateFormatError:
        return False
    stage = data.get("stage") or {}
    current = stage.get("current")
    status = stage.get("status")
    loop_active = ((data.get("loop") or {}).get("control") or {}).get("loop_active")
    if loop_active is True:
        return True
    return current not in ("DONE", "IDLE") or status not in (
        "SUCCESS",
        "WAITING",
        "COMPLETED",
        "COMPLETE",
    )


def build_plan(
    repo: Path, binding: Binding, *, skip_open_demand: bool = False
) -> Plan:
    repo = Path(repo)
    plan = Plan(repo=repo, binding=binding)
    orchestration = repo / ".hermes" / "orchestration"
    runtime = obsidian_binding.runtime_dir(binding, repo)

    state_path = orchestration / "STATE.md"
    if state_path.is_file():
        text = state_path.read_text(encoding="utf-8", errors="replace")
        if state_is_open(text):
            if not skip_open_demand:
                raise MigrationRefused(
                    "OPEN_DEMAND",
                    "This worktree has a demand in progress; close it or pass --skip-open-demand.",
                    detail=str(state_path),
                )
            plan.skipped.append({"path": str(state_path), "reason": "OPEN_DEMAND"})
        else:
            try:
                state_format.normalise(text)
            except state_format.StateFormatError as exc:
                plan.problems.append(
                    {"path": str(state_path), "code": exc.code, "detail": exc.message}
                )
            else:
                plan.moves.append(
                    Move(state_path, runtime / "STATE.md", "runtime", normalise_state=True)
                )

    skip_runtime = bool(plan.skipped)
    for name in RUNTIME_NAMES:
        if name == "STATE.md":
            continue
        source = orchestration / name
        if source.is_file():
            if skip_runtime:
                # Journal and incidents belong to the same demand as STATE.
                plan.skipped.append({"path": str(source), "reason": "OPEN_DEMAND"})
            else:
                plan.moves.append(Move(source, runtime / name, "runtime"))

    for name in DISCARD_DIRECTORIES:
        candidate = orchestration / name
        if candidate.is_dir():
            plan.discards.append(candidate)
        # __pycache__ also nests inside knowledge directories; discard those too
        # or the migration leaves orphan .pyc files behind in the repository.
        for nested in sorted(orchestration.rglob(name)):
            if nested.is_dir() and nested != candidate:
                plan.discards.append(nested)

    for name in RUNTIME_DIRECTORIES:
        directory = orchestration / name
        if not directory.is_dir():
            continue
        for source in sorted(directory.rglob("*")):
            if not source.is_file() or _is_discarded(source):
                continue
            relative = source.relative_to(orchestration)
            plan.moves.append(Move(source, runtime / relative, "knowledge"))

    tasks_root = repo / "tasks"
    if tasks_root.is_dir():
        for source in sorted(tasks_root.rglob("*")):
            if not source.is_file() or _is_discarded(source) or source.name == ".DS_Store":
                continue
            relative = source.relative_to(tasks_root)
            slug = relative.parts[0]
            if slug.startswith("prd-"):
                slug = slug[len("prd-") :]
            destination = binding.container_path / slug / Path(*relative.parts[1:])
            plan.moves.append(Move(source, destination, "task"))

    for move in plan.moves:
        vault_guard.assert_writable(binding, move.destination)

    plan.moves = [m for m in plan.moves if not _already_migrated(m)]
    return plan


def _is_discarded(path: Path) -> bool:
    return any(part in DISCARD_DIRECTORIES for part in path.parts)


def _already_migrated(move: Move) -> bool:
    if not move.destination.exists():
        return False
    if move.normalise_state:
        try:
            expected = state_format.normalise(
                move.source.read_text(encoding="utf-8", errors="replace")
            )
        except state_format.StateFormatError:
            return False
        return move.destination.read_text(encoding="utf-8", errors="replace") == expected
    return sha256(move.source) == sha256(move.destination)


def apply(plan: Plan) -> Result:
    result = Result()
    for move in plan.moves:
        destination = vault_guard.assert_writable(plan.binding, move.destination)
        destination.parent.mkdir(parents=True, exist_ok=True)

        if move.normalise_state:
            payload = state_format.normalise(
                move.source.read_text(encoding="utf-8", errors="replace")
            ).encode("utf-8")
        else:
            payload = move.source.read_bytes()

        if destination.exists():
            if destination.read_bytes() == payload:
                move.source.unlink()
                result.moved.append(move)
                continue
            # Never overwrite: park the repository copy beside the original and
            # let a human arbitrate the difference.
            alternate = destination.with_name(
                f"{destination.stem}.from-repo{destination.suffix}"
            )
            if not alternate.exists():
                alternate.write_bytes(payload)
            result.conflicts.append(
                {"source": str(move.source), "destination": str(destination), "parked": str(alternate)}
            )
            continue

        destination.write_bytes(payload)
        if sha256(destination) != hashlib.sha256(payload).hexdigest():
            raise MigrationRefused(
                "COPY_VERIFICATION_FAILED",
                "Copied file hash did not match the payload; source left untouched.",
                detail=str(destination),
            )
        move.source.unlink()
        result.moved.append(move)

    for directory in plan.discards:
        shutil.rmtree(directory)
        result.discarded.append(directory)
    return result


def report(plan: Plan, result: Result | None) -> Dict[str, Any]:
    return {
        "repo": str(plan.repo),
        "vault": str(plan.binding.vault_path),
        "project_container": plan.binding.project_container,
        "runtime_dir": str(obsidian_binding.runtime_dir(plan.binding, plan.repo)),
        "dry_run": result is None,
        "counts": {
            "moves": len(plan.moves),
            "discards": len(plan.discards),
            "problems": len(plan.problems),
            "skipped": len(plan.skipped),
            "moved": len(result.moved) if result else 0,
            "conflicts": len(result.conflicts) if result else 0,
        },
        "moves": [
            {"source": str(m.source), "destination": str(m.destination), "kind": m.kind}
            for m in plan.moves
        ],
        "discards": [str(p) for p in plan.discards],
        "problems": plan.problems,
        "skipped": plan.skipped,
        "conflicts": result.conflicts if result else [],
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Worktree root to migrate.")
    parser.add_argument("--apply", action="store_true", help="Perform the migration.")
    parser.add_argument("--json", action="store_true", help="Emit a JSON report.")
    parser.add_argument(
        "--skip-open-demand",
        action="store_true",
        help="Leave runtime in place when the worktree has a demand in progress.",
    )
    args = parser.parse_args(argv)
    repo = Path(args.repo)
    try:
        binding = obsidian_binding.load(repo)
        plan = build_plan(repo, binding, skip_open_demand=args.skip_open_demand)
        result = apply(plan) if args.apply else None
    except (MigrationRefused, obsidian_binding.BindingError, vault_guard.VaultWriteRefused) as exc:
        payload = {"status": getattr(exc, "code", "REFUSED"), "message": str(exc)}
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else str(exc))
        return 2
    payload = report(plan, result)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        counts = payload["counts"]
        print(
            f"{'APPLIED' if result else 'DRY RUN'}: moves={counts['moves']} "
            f"discards={counts['discards']} problems={counts['problems']} "
            f"skipped={counts['skipped']} conflicts={counts['conflicts']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
