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
PROJECT_SKILLS = (
    ".hermes/skills/sdd-backend-engineering",
    ".hermes/skills/sdd-architecture-decisions",
    ".hermes/skills/sdd-database-design-migrations",
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
#: Controller files the owner is told to edit after install. Created when
#: absent, never compared, so a second worktree can share the container.
OBSIDIAN_USER_CONFIGURED = frozenset({f"{CONFIG_ROOT}/policies/GATES.md"})
