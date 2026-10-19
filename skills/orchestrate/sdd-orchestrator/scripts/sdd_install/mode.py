"""Per-run storage policy, held in one object instead of module globals."""
from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Iterator

from .constants import (
    CONFIG_ROOT,
    LOCAL_CONTROLLER_ROOT,
    OBSIDIAN_BINDING_PATH,
    OBSIDIAN_CONFIG_ROOT,
    OBSIDIAN_CONTROLLER_ROOT,
    OBSIDIAN_ENV_PATH,
    OBSIDIAN_RUNTIME_SUBPATH,
    OBSIDIAN_SKILL_ROOT,
    OBSIDIAN_TYPESAFE_SKILL_PATH,
    OBSIDIAN_TYPESAFE_SKILL_ROOT,
    OBSIDIAN_VISIBLE_BINDING_PATH,
    OBSIDIAN_VISIBLE_RUNTIME_SUBPATH,
    TYPESAFE_ENV_PATH,
    TYPESAFE_LOCK_PATH_LOCAL,
    TYPESAFE_LOCK_PATH_OBSIDIAN,
    TYPESAFE_SKILL_PATH,
    TYPESAFE_SKILL_ROOT,
    TYPESAFE_SKILLS_ROOT,
)


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
    controller_root: str = LOCAL_CONTROLLER_ROOT
    config_root: str = CONFIG_ROOT
    skills_root: str = TYPESAFE_SKILLS_ROOT
    typesafe_skill_root: str = TYPESAFE_SKILL_ROOT
    typesafe_skill_path: str = TYPESAFE_SKILL_PATH
    env_path: str = TYPESAFE_ENV_PATH
    binding_path: str = OBSIDIAN_BINDING_PATH
    runtime_subpath: str = OBSIDIAN_RUNTIME_SUBPATH
    typesafe_lock_path: str = TYPESAFE_LOCK_PATH_LOCAL

    def set(self, *, obsidian: bool) -> None:
        self.credential_file_managed = not obsidian
        self.tracked_destinations_guarded = not obsidian
        self.controller_root = OBSIDIAN_CONTROLLER_ROOT if obsidian else LOCAL_CONTROLLER_ROOT
        self.config_root = OBSIDIAN_CONFIG_ROOT if obsidian else CONFIG_ROOT
        self.skills_root = OBSIDIAN_SKILL_ROOT if obsidian else TYPESAFE_SKILLS_ROOT
        self.typesafe_skill_root = (
            OBSIDIAN_TYPESAFE_SKILL_ROOT if obsidian else TYPESAFE_SKILL_ROOT
        )
        self.typesafe_skill_path = OBSIDIAN_TYPESAFE_SKILL_PATH if obsidian else TYPESAFE_SKILL_PATH
        self.env_path = OBSIDIAN_ENV_PATH if obsidian else TYPESAFE_ENV_PATH
        self.binding_path = OBSIDIAN_VISIBLE_BINDING_PATH if obsidian else OBSIDIAN_BINDING_PATH
        self.runtime_subpath = OBSIDIAN_VISIBLE_RUNTIME_SUBPATH if obsidian else OBSIDIAN_RUNTIME_SUBPATH
        self.typesafe_lock_path = TYPESAFE_LOCK_PATH_OBSIDIAN if obsidian else TYPESAFE_LOCK_PATH_LOCAL

    def destination(self, relative: str) -> str:
        """Translate a template path from the `.hermes` source to the active root."""
        prefix = f"{LOCAL_CONTROLLER_ROOT}/"
        if relative == LOCAL_CONTROLLER_ROOT:
            return self.controller_root
        if relative.startswith(prefix):
            return f"{self.controller_root}/{relative[len(prefix):]}"
        return relative


MODE = StorageMode()


@contextlib.contextmanager
def storage_mode(*, obsidian: bool) -> Iterator[StorageMode]:
    """Apply the storage policy for one run and restore the previous one afterwards."""
    previous = (
        MODE.credential_file_managed, MODE.tracked_destinations_guarded, MODE.controller_root,
        MODE.config_root, MODE.skills_root, MODE.typesafe_skill_root, MODE.typesafe_skill_path,
        MODE.env_path, MODE.binding_path, MODE.runtime_subpath, MODE.typesafe_lock_path,
    )
    MODE.set(obsidian=obsidian)
    try:
        yield MODE
    finally:
        (
            MODE.credential_file_managed, MODE.tracked_destinations_guarded, MODE.controller_root,
            MODE.config_root, MODE.skills_root, MODE.typesafe_skill_root, MODE.typesafe_skill_path,
            MODE.env_path, MODE.binding_path, MODE.runtime_subpath, MODE.typesafe_lock_path,
        ) = previous
