"""Per-run storage policy, held in one object instead of module globals."""
from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Iterator

from .constants import TYPESAFE_LOCK_PATH_LOCAL, TYPESAFE_LOCK_PATH_OBSIDIAN


@dataclass
class StorageMode:
    #: False in Obsidian storage mode: credentials never live in the vault or the
    #: user's repository, so no `.env` placeholder is created and the connector
    #: reads keys from the process environment.
    credential_file_managed: bool = True
    #: False in Obsidian storage mode: the vault may be its own Git repository
    #: (for example with obsidian-git); being tracked there is not a conflict,
    #: because the user's repository is never a destination.
    tracked_destinations_guarded: bool = True
    #: In an Obsidian container the lock stays hidden so the wiki root holds only notes.
    typesafe_lock_path: str = TYPESAFE_LOCK_PATH_LOCAL

    def set(self, *, obsidian: bool) -> None:
        self.credential_file_managed = not obsidian
        self.tracked_destinations_guarded = not obsidian
        self.typesafe_lock_path = TYPESAFE_LOCK_PATH_OBSIDIAN if obsidian else TYPESAFE_LOCK_PATH_LOCAL


MODE = StorageMode()


@contextlib.contextmanager
def storage_mode(*, obsidian: bool) -> Iterator[StorageMode]:
    """Apply the storage policy for one run and restore the previous one afterwards."""
    previous = (MODE.credential_file_managed, MODE.tracked_destinations_guarded, MODE.typesafe_lock_path)
    MODE.set(obsidian=obsidian)
    try:
        yield MODE
    finally:
        MODE.credential_file_managed, MODE.tracked_destinations_guarded, MODE.typesafe_lock_path = previous
