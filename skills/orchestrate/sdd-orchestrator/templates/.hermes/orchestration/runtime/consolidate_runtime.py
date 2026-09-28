#!/usr/bin/env python3
"""Collapse project history duplicated across per-worktree runtime directories.

Runtime is keyed per worktree so concurrent demands never share STATE or a
journal. But a worktree created from another inherits that worktree's copy of
the PROJECT's history (runs, audits, investigations, handoffs...), so the same
bytes end up stored once per worktree — measured at 69% redundancy here.

This tool separates the two kinds of content:

* per-worktree runtime (``STATE.md``, ``ACTION_JOURNAL.json``, ``INCIDENTS.md``)
  always stays put, even when two worktrees happen to hold identical bytes;
* project history stored identically in two or more worktrees moves once into a
  shared directory beside them.

A relative path is shared ONLY when every worktree holding it agrees on the
hash. Divergent files are distinct evidence and are left untouched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Set

sys.path.insert(0, str(Path(__file__).resolve().parent))

import obsidian_binding  # noqa: E402
import vault_guard  # noqa: E402
from obsidian_binding import Binding  # noqa: E402

#: Underscore keeps it sorted beside worktrees and out of note-like names.
SHARED_DIR = "_shared"

#: Never shared: identical bytes today diverge on the next transition.
PER_WORKTREE_FILES = {"STATE.md", "ACTION_JOURNAL.json", "INCIDENTS.md"}

_HASH_CHUNK = 65536


@dataclass(frozen=True)
class SharedFile:
    relative: str
    digest: str
    sources: tuple[Path, ...]
    destination: Path


@dataclass
class Plan:
    binding: Binding
    shared: List[SharedFile] = field(default_factory=list)
    divergent: List[str] = field(default_factory=list)


@dataclass
class Result:
    shared_files: int = 0
    removed_copies: int = 0
    bytes_reclaimed: int = 0


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_plan(binding: Binding) -> Plan:
    plan = Plan(binding=binding)
    runtime_root = binding.runtime_root
    if not runtime_root.is_dir():
        return plan

    worktrees = [
        d for d in sorted(runtime_root.iterdir()) if d.is_dir() and d.name != SHARED_DIR
    ]
    by_relative: Dict[str, Dict[str, Set[Path]]] = defaultdict(lambda: defaultdict(set))
    for worktree in worktrees:
        for path in worktree.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(worktree).as_posix()
            if relative in PER_WORKTREE_FILES:
                continue
            by_relative[relative][sha256(path)].add(path)

    shared_root = runtime_root / SHARED_DIR
    for relative, digests in sorted(by_relative.items()):
        if len(digests) > 1:
            # The same path holds different bytes in different worktrees.
            plan.divergent.append(relative)
            continue
        digest, sources = next(iter(digests.items()))
        if len(sources) < 2:
            continue
        destination = shared_root / relative
        if destination.exists() and sha256(destination) == digest:
            continue
        plan.shared.append(
            SharedFile(relative, digest, tuple(sorted(sources)), destination)
        )
    return plan


def apply(plan: Plan) -> Result:
    result = Result()
    for entry in plan.shared:
        destination = vault_guard.assert_writable(plan.binding, entry.destination)
        destination.parent.mkdir(parents=True, exist_ok=True)

        payload = entry.sources[0].read_bytes()
        if not destination.exists():
            destination.write_bytes(payload)
            if sha256(destination) != entry.digest:
                raise RuntimeError(
                    f"Hash mismatch writing {destination}; sources left untouched."
                )
        result.shared_files += 1

        for source in entry.sources:
            # Re-verify each copy right before deleting it: only bytes that are
            # provably already in the shared file may be removed.
            if sha256(source) != entry.digest:
                continue
            result.bytes_reclaimed += source.stat().st_size
            source.unlink()
            result.removed_copies += 1

    _prune_empty(plan.binding.runtime_root)
    return result


def _prune_empty(runtime_root: Path) -> None:
    for directory in sorted(runtime_root.rglob("*"), reverse=True):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    binding = obsidian_binding.load(Path(args.repo))
    plan = build_plan(binding)
    result = apply(plan) if args.apply else None

    payload = {
        "dry_run": result is None,
        "shared_candidates": len(plan.shared),
        "redundant_copies": sum(len(s.sources) for s in plan.shared),
        "divergent_paths": len(plan.divergent),
        "shared_dir": str(binding.runtime_root / SHARED_DIR),
        "shared_files": result.shared_files if result else 0,
        "removed_copies": result.removed_copies if result else 0,
        "bytes_reclaimed": result.bytes_reclaimed if result else 0,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            f"{'APPLIED' if result else 'DRY RUN'}: "
            f"shared={payload['shared_candidates']} "
            f"copies={payload['redundant_copies']} "
            f"divergent={payload['divergent_paths']} "
            f"reclaimed={payload['bytes_reclaimed'] // 1024} KB"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
