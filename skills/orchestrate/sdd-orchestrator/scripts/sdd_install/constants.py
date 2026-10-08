"""Installer constants shared by every module."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT.parent / "templates"
CONFIG_ROOT = ".hermes/orchestration"
TYPESAFE_SKILL_ROOT = ".hermes/skills/typesafe-ai"
TYPESAFE_SKILL_PATH = f"{TYPESAFE_SKILL_ROOT}/SKILL.md"
#: Repository-local lock path; the active one is `mode.MODE.typesafe_lock_path`.
TYPESAFE_LOCK_PATH_LOCAL = "skills-lock.json"
#: In an Obsidian container the lock stays hidden so the wiki root holds only notes.
TYPESAFE_LOCK_PATH_OBSIDIAN = ".hermes/skills-lock.json"
TYPESAFE_ENV_PATH = ".hermes/.env"
TYPESAFE_ENV_CONTENT = b"TYPESAFE_API_KEY=\nJEV_AI_API_KEY=\n"
JEV_CACHE_PATH = f"{CONFIG_ROOT}/JEV_CACHE.json"
TERMINAL_PROGRESS_PATH = f"{CONFIG_ROOT}/TERMINAL_PROGRESS.json"
JEV_CACHE_LOCK_PATH = f"{CONFIG_ROOT}/.JEV_CACHE.json.lock"
TERMINAL_PROGRESS_LOCK_PATH = f"{CONFIG_ROOT}/.TERMINAL_PROGRESS.json.lock"
TYPESAFE_ANSWER_DISABLED = '{"install":true,"automatic_semantic_governance":false}'
TYPESAFE_ANSWER_ENABLED = '{"install":true,"automatic_semantic_governance":true}'
TYPESAFE_SOURCE_REF = "65a39f393687675ce170e6094757de20370365b9"
TYPESAFE_UPSTREAM_HASH = "9cd84c5e535dec8dec59917c110f9c00b4a61faadb86b432ec7e41051170af12"
TYPESAFE_TRUSTED_DIGEST = "5266f2a9acfb6ae5fd58717bdf366f38e224cb57a55824e2aa81a0922d5e6964"
TYPESAFE_VENDOR = ROOT.parent / "vendor" / "typesafe-ai"
TYPESAFE_OFFICIAL_COMMAND = (
    "npx", "skills", "add", "typesafe-ai/skills", "--skill", "typesafe-ai",
)
STATE_PATHS = (
    f"{CONFIG_ROOT}/STATE.md",
    f"{CONFIG_ROOT}/PROJECT_SETUP.md",
    f"{CONFIG_ROOT}/INCIDENTS.md",
    f"{CONFIG_ROOT}/ACTION_JOURNAL.json",
)

OBSIDIAN_RUNTIME_SUBPATH = ".hermes-runtime"
OBSIDIAN_BINDING_PATH = ".hermes/obsidian.json"
WORKTREE_RUNTIME_FILES = ("STATE.md", "INCIDENTS.md", "ACTION_JOURNAL.json")
#: Controller files the owner edits after install, in both storage modes.
#: Created from the template when absent, never compared or replaced once the
#: controller is installed (a fresh install never adopts a foreign copy), so a
#: second worktree can share an Obsidian container and a rerun keeps the gates.
OWNER_FILES = frozenset({
    f"{CONFIG_ROOT}/policies/GATES.md",
    f"{CONFIG_ROOT}/policies/EXECUTORS.md",
})
#: Generated owner record; like OWNER_FILES it is never rewritten by an upgrade.
PROJECT_SETUP_PATH = f"{CONFIG_ROOT}/PROJECT_SETUP.md"

#: Written once at fresh install time, inside the install transaction, and by
#: `--upgrade --apply` as its commit point. Never created implicitly for an
#: older installation.
INSTALL_MANIFEST_PATH = f"{CONFIG_ROOT}/INSTALL_MANIFEST.json"
INSTALL_MANIFEST_VERSION = 1
UPGRADE_BACKUPS_PATH = f"{CONFIG_ROOT}/upgrade-backups"
UPGRADE_LOCK_PATH = f"{CONFIG_ROOT}/.upgrade.lock"
SKILL_MANIFEST = ROOT.parent / "SKILL.md"

#: Shown in every install report: Finder and Obsidian hide dot-directories.
HIDDEN_CONTROLLER_NOTE = (
    "The controller lives in the hidden .hermes directory (dot-prefixed folders are hidden by Finder and "
    "Obsidian); open controller_location directly, or press Cmd+Shift+. in Finder to show it."
)
