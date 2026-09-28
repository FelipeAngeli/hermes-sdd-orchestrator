#!/usr/bin/env python3
"""Migrate every registered worktree into the vault, one at a time.

Each worktree is handled independently: bootstrap its binding if missing, run
the migration, then prune the empty directories it leaves behind. A refusal on
one worktree (open demand, corrupt STATE) is recorded and never aborts the
others.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import migrate_to_vault  # noqa: E402
import obsidian_binding  # noqa: E402

BINDING_TEMPLATE = {
    "boards_subpath": "boards",
    "decisions_subpath": "decisions",
    "new_project_script": "2 - 🗂 Workflow/AI/scripts/new_project.py",
    "protocol_path": "2 - 🗂 Workflow/AI/Hermes/HERMES_OBSIDIAN_PROTOCOL.md",
    "runtime_subpath": ".hermes-runtime",
    "schema_version": 1,
    "sessions_subpath": "sessions",
    "write_policy": {
        "outside_scope": "requires_human_authorization",
        "preauthorized_scope": "project_container",
    },
}

PRUNE_DIRECTORIES = (
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


def worktrees(repo: Path) -> list[Path]:
    out = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout
    return [Path(line[9:]) for line in out.splitlines() if line.startswith("worktree ")]


def ensure_binding(worktree: Path, vault: str, container: str) -> None:
    path = worktree / ".hermes" / "obsidian.json"
    if path.exists():
        return
    payload = dict(BINDING_TEMPLATE)
    payload["vault_path"] = vault
    payload["project_container"] = container
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def prune_empty(worktree: Path) -> None:
    orchestration = worktree / ".hermes" / "orchestration"
    for name in PRUNE_DIRECTORIES:
        directory = orchestration / name
        if not directory.is_dir():
            continue
        for candidate in sorted(directory.rglob("*"), reverse=True):
            if candidate.is_dir() and not any(candidate.iterdir()):
                candidate.rmdir()
        if not any(directory.iterdir()):
            directory.rmdir()
    tasks = worktree / "tasks"
    if tasks.is_dir():
        for candidate in sorted(tasks.rglob("*"), reverse=True):
            if candidate.is_dir() and not any(candidate.iterdir()):
                candidate.rmdir()
        if not any(tasks.iterdir()):
            tasks.rmdir()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--vault", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    repo = Path(args.repo)
    summary: list[dict[str, object]] = []
    for worktree in worktrees(repo):
        entry: dict[str, object] = {"worktree": worktree.name, "status": "OK"}
        try:
            if args.apply:
                ensure_binding(worktree, args.vault, args.project)
            elif not (worktree / ".hermes" / "obsidian.json").exists():
                entry["status"] = "WOULD_CREATE_BINDING"
            if not (worktree / ".hermes" / "obsidian.json").exists():
                entry["moves"] = 0
                summary.append(entry)
                continue

            binding = obsidian_binding.load(worktree)
            plan = migrate_to_vault.build_plan(worktree, binding, skip_open_demand=True)
            entry["moves"] = len(plan.moves)
            entry["problems"] = [p["path"].split("/")[-1] for p in plan.problems]
            entry["skipped"] = [s["reason"] for s in plan.skipped]
            if args.apply:
                result = migrate_to_vault.apply(plan)
                entry["moved"] = len(result.moved)
                entry["conflicts"] = len(result.conflicts)
                prune_empty(worktree)
                entry["runtime"] = str(
                    obsidian_binding.runtime_dir(binding, worktree).name
                )
        except Exception as exc:  # noqa: BLE001 - one worktree must not stop the rest
            entry["status"] = f"{type(exc).__name__}: {exc}"
        summary.append(entry)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
