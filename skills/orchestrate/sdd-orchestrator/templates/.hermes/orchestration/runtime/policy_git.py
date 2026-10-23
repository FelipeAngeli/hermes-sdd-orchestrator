"""Git calls that make git run another program, for the policy evaluator (stdlib only, no execution).

git runs some config values as commands: an alias starting with "!", ``core.sshCommand``,
``core.pager``, filters, credential helpers and similar, set with ``-c``, ``--config-env``,
``git config`` or ``GIT_*`` environment variables. Such a call needs a human approval; a
pager or editor set to ``cat``/``less``/``more``/``true`` and reading or removing a value
run nothing new and pass.
"""
from __future__ import annotations

import re

#: git config keys whose value git runs as a program; a value starting with "!" is a shell command.
_GIT_EXEC_KEYS = re.compile(
    r"(?i)^(alias\..+|core\.(pager|editor|sshcommand|fsmonitor|hookspath|askpass|gitproxy)|sequence\.editor|"
    r"diff\.external|diff\..+\.(textconv|command)|filter\..+|credential\..*|gpg\..*program|.+\.sshcommand|"
    r"merge\..+\.driver|(uploadpack|receivepack|include|includeif)\..+)$")
_SAFE_PAGERS = {"", "cat", "less", "more"}
#: Keys whose value names a pager or editor: a plain cat/less/more/true value runs nothing new.
_PAGER_KEYS = re.compile(r"(?i)^(core\.(pager|editor)|sequence\.editor|pager\..+)$")
_SAFE_PROGRAMS = _SAFE_PAGERS | {"true", ":"}
#: GIT_* variables that only label a commit or pick the repository at .git; any other GIT_* assignment
#: (GIT_INDEX_FILE, GIT_WORK_TREE, GIT_TRACE, GIT_CONFIG_*, GIT_SSH*…) can run a program or write a path.
_GIT_SAFE_ENV = re.compile(r"^GIT_((AUTHOR|COMMITTER)_(NAME|EMAIL|DATE)=|TERMINAL_PROMPT=|DIR=\.git/?$|PAGER=)")
_GIT_ENV = re.compile(r"^GIT_[A-Za-z0-9_]*=(.*)$")
#: Options and subcommands that make `git config` read or remove; they count only before the first key.
_GIT_CONFIG_READS = {"--get", "--get-all", "--get-regexp", "--get-urlmatch", "--list", "-l", "get", "list",
                     "--unset", "--unset-all", "unset"}
#: Flags less/more may carry as a pager: display only (no log file, no key bindings, no commands).
_PAGER_FLAG = re.compile(r"^-[RFXSKimMNnc]+$")
_GIT_CONFIG_VALUE_OPTIONS = {"-f", "--file", "--blob", "--type", "--default", "--comment"}


def git_runs_code(program: list[str]) -> bool:
    """True when a git call sets a config value git will run (``-c alias.x=!cmd``, ``config core.pager …``)."""
    if not program or program[0] != "git":
        return False
    args = program[1:]
    for index, arg in enumerate(args):
        if arg.startswith("--config-env") or arg.startswith("--exec-path="):
            return True
        if arg == "-c" and index + 1 < len(args):
            setting = args[index + 1]
        elif arg.startswith("-c") and len(arg) > 2:
            setting = arg[2:]
        elif arg == "config":
            rest = args[index + 1:]
            values, skip, reading = [], False, False
            for item in rest:
                if skip:
                    skip = False
                elif item in _GIT_CONFIG_VALUE_OPTIONS:
                    skip = True
                elif not values and item in _GIT_CONFIG_READS:
                    reading = True  # an action given before the key: --get, --list, unset…
                elif not item.startswith("-") and not (not values and item in ("set", "add")):
                    values.append(item)
            if reading:
                return False  # reading or removing a value runs nothing
            key, value = (values + ["", ""])[:2]
            setting = f"{key}={value}"
        else:
            continue
        key, _, value = setting.partition("=")
        if value.startswith("!") or (_GIT_EXEC_KEYS.match(key) and not _harmless_git_value(key, value)):
            return True
        if arg == "config":
            break
    return False


def _harmless_git_value(key: str, value: str) -> bool:
    """A pager/editor set to cat/less/more/true (flags allowed), or an empty credential helper (which disables helpers)."""
    if key.lower() == "credential.helper" and not value.strip():
        return True
    if not _PAGER_KEYS.match(key):
        return False  # shell syntax in the value fails the program and flag allowlist below
    words = value.split()
    if not words:
        return True
    program = words[0]  # the bare name only: ./less or x/less is a program from the repository
    if program in ("true", ":"):
        return len(words) == 1
    return program in _SAFE_PROGRAMS and all(_PAGER_FLAG.match(word) for word in words[1:])


def git_env_runs_code(tokens: list[str]) -> bool:
    for token in tokens:
        match = _GIT_ENV.match(token)
        if not match:
            continue
        if token.startswith("GIT_PAGER="):
            if not _harmless_git_value("core.pager", match.group(1)):
                return True
        elif not _GIT_SAFE_ENV.match(token):
            return True
    return False
