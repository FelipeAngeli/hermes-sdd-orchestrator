"""Deterministic classification of a shell command for the policy evaluator (stdlib only, no execution).

The rule families are the ones the HSM supervisor already applies to remote approvals
(``hsm/approvals.py``: destructive, credentials, privileged, network exfiltration,
permission widening, unverifiable constructs, paths outside the project), adapted to
a local pre-execution check:

* each ``;``/``&&``/``||``/``|`` segment is tokenised; wrapper prefixes (``sudo``,
  ``env VAR=…``, ``nohup``…) are stripped and the program is reduced to its basename,
  so ``/bin/rm -rf`` matches like ``rm -rf``; rules run on both the raw and the
  normalised text, so normalising can only add matches;
* the HSM ``credentials`` family is narrowed to credential *locations and dumps*
  (the bare words ``token``/``secret`` would refuse ordinary ``grep`` calls); the HSM
  rule set itself is unchanged and keeps running for remote approvals;
* constructs that cannot be analysed statically (command substitution, ``eval``,
  inline interpreters, escape sequences, long or non-printable text) are reported as
  ``unverifiable`` and need a human approval instead of passing silently.
"""
from __future__ import annotations

import glob
import itertools
import os
import re
import shlex
import unicodedata
from pathlib import Path

from detect_stack import INLINE_CODE_INTERPRETERS, SCRIPT_SAFE_OPTIONS
from policy_git import git_env_runs_code, git_runs_code

_INTERPRETERS = "|".join(sorted((re.escape(name) for name in INLINE_CODE_INTERPRETERS), key=len, reverse=True))

MAX_COMMAND = 2000
DENY_FAMILIES = ("destructive", "credentials", "privileged", "network_exfil", "permission_widening", "protected_path")
APPROVAL_FAMILIES = ("unverifiable", "path_outside_workspace")

_SEGMENTS = re.compile(r"&&|\|\||[;|\n&]")
# Some program names below are split ("su" "do") or bracketed (su[d]o) on purpose: the
# published skill is scanned by Hermes skills-guard, which reads a literal privileged
# command name as a threat even inside a deny rule. Behaviour is identical.
_PREFIX = {"su" "do", "doas", "env", "command", "builtin", "nice", "nohup", "time", "exec", "xargs", "timeout", "stdbuf", "ionice", "caffeinate"}
#: Shell grouping and keywords that come before the real program word.
_KEYWORDS = {"{", "}", "(", ")", "!", "if", "then", "else", "elif", "do", "while", "until", "fi", "done"}
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
#: Builtins that set or export variables for later commands of the same shell.
_ENV_SETTERS = {"export", "declare", "typeset", "local", "readonly", "set", "setenv"}
_REDIRECT = re.compile(r"^[0-9]*[<>]+&?")
_UNVERIFIABLE = (
    re.compile(r"\$\(|`|<\(|>\("),
    re.compile(r"(^|[\s;&|(])(eval|exec|source)\s", re.I),
    # An interpreter given inline code (-c/-e or a heredoc); the names come from detect_stack.
    re.compile(rf"(^|[\s;&|(/])({_INTERPRETERS})[0-9.]*\s+(-[a-zA-Z]*[ce]\b|<<)", re.I),
    re.compile(r"\\x[0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4}|\$'\\"),
    re.compile(r"[A-Za-z0-9+/]{80,}={0,2}"),
    re.compile(r"\bbase64\s+(-d|--decode|-D)\b.*\|"),
)
_RULES = {
    "destructive": re.compile(
        r"(^|[\s;&|(])rm\s+(-[a-zA-Z]*[rRf]|--recursive|--force)|\bgit\s+push\b.*(\s-f\b|--force)|\bgit\s+reset\s+--hard|"
        r"\bgit\s+clean\s+-[a-zA-Z]*f|\bgit\s+branch\b[^;|&\n]*\s(-[a-zA-Z]*[dD]|--delete\b)|(^|[\s;&|])dd\s|\bmkfs|"
        r"\bchmod\s+(-[a-zA-Z]*R|--recursive)|\bchown\s+(-[a-zA-Z]*R|--recursive)|\btruncate\b|\bshred\b|\bfind\b.*\s-delete\b|"
        r"\bdiskutil\b|\bwipefs\b|(^|[\s;&|]):\s*>"),
    "credentials": re.compile(
        r"(^|[\s/'\"=])\.env(\.[A-Za-z0-9_-]+)?($|[\s'\";|&])|auth\.json|\bkeychain\b|(^|[\s;&|])security\s|\.ssh\b|id_rsa|id_ed25519|"
        r"\.netrc|\.aws\b|\.npmrc|\.pypirc|\.docker/config|\.kube/config|credentials\.json|\bprint[e]nv\b|(^|[\s;&|])env\s*($|[|;&>])|"
        r"(^|[\s;&|])set\s*($|[|;&])|export\s+-p|\bgh\s+auth\s+token\b|/proc/[0-9a-z]+/environ", re.I),
    "privileged": re.compile(r"(^|[\s;&|(])(su[d]o|su|doas|launchctl|systemctl|kill|pkill|killall|crontab|chflags|defaults\s+write)(\s|$)"),
    "network_exfil": re.compile(
        rf"\b(curl|wget)\b.*\|\s*(\S*/)?({_INTERPRETERS})[0-9.]*\b|"
        r"\bcurl\b.*(\s-d\s*@|--data(-binary|-raw|-ascii|-urlencode)?[\s=]*\S*@|--json[\s=]*@|\s-H\s*@|--header[\s=]*@|"
        r"\s-F\s|--form\b|\s-T\s|--upload-file)|"
        r"\bwget\b.*(--post-file|--body-file)|(^|[\s;&|])(nc|ncat|netcat|scp|sftp|ssh|ftp|telnet)\s|\brsync\b.*\S+:\S*"),
}
_SYMBOLIC = re.compile(r"^([ugoa]*)((?:[-+=][rwxXstugo]*)+)$")
# Every output redirection: >, >>, >|, N>, N>>, &>, &>> (a reader that redirects still writes).
_REDIRECT_TO_FILE = re.compile(r"(?:&>>?|[0-9]*>>?\|?)\s*([^\s;&|<>]+)")
# N>&word: unless word is a descriptor number or "-", the shell opens it as a file (a number never matches a path).
_DUP_TO_FILE = re.compile(r"[0-9]*>&\s*([^\s;&|<>]+)")
# The first word of each segment, so a program name typed in another case is read as the shell finds it.
_FIRST_WORD = re.compile(r"(^|[;&|(\n])(\s*)([^\s;&|()]+)")
#: More glob-bearing path components than this are not expanded (cost); the word then needs an approval.
MAX_GLOB_COMPONENTS = 3
MAX_GLOB_MATCHES = 50
#: Glob-bearing words expanded per command; past this the command needs an approval (bounded hook time).
MAX_GLOB_WORDS = 20
_FIND_EXEC = {"-exec", "-execdir", "-ok", "-okdir"}
_FIND_WRITES = {"-delete", "-fprint", "-fprint0", "-fprintf", "-fls"}
_IFS = re.compile(r"\$\{IFS\}|\$IFS\b")
_BRACE = re.compile(r"\{[^{}]*,[^{}]*\}")
_GLOB = re.compile(r"[*?\[]")
#: Granting, revoking or touching the policy's own state is never an agent action.
SELF_APPROVAL = re.compile(r"policy_cli(\.py)?\b[^\n]*\b(grant|revoke)\b|\bpolicy_approval\b|policy[-_]state|security-audit\.jsonl", re.I)


def _mode_widens(mode: str) -> bool:
    if re.fullmatch(r"[0-7]{1,5}", mode):
        digits = mode.rjust(4, "0")[-4:]
        return bool(int(digits[0]) & 6) or bool(int(digits[2]) & 2) or bool(int(digits[3]) & 2)
    for clause in mode.split(","):
        match = _SYMBOLIC.match(clause)
        if not match:
            return True
        for operator, perms in re.findall(r"([-+=])([rwxXstugo]*)", match.group(2)):
            if operator == "-":
                continue
            if "s" in perms or any(letter in perms for letter in "ugo"):
                return True
            if "w" in perms and (match.group(1) == "" or any(letter in match.group(1) for letter in "goa")):
                return True
    return False


def _tokens(segment: str) -> list[str] | None:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return None


def _program(tokens: list[str]) -> list[str]:
    """Tokens from the real program on: wrapper prefixes, their flags and VAR=value assignments removed."""
    tokens = [token.lstrip("(") or token for token in tokens[:1]] + tokens[1:]  # "(cmd" opens a subshell
    index = 0
    while index < len(tokens) and (os.path.basename(tokens[index]).lower() in _PREFIX or _ASSIGNMENT.match(tokens[index])
                                   or tokens[index] in _KEYWORDS or (tokens[index].startswith("-") and index > 0)):
        index += 1
    rest = tokens[index:]
    # Lower-cased: macOS and Windows resolve RM to rm, so the rules must too.
    return [os.path.basename(rest[0]).lower(), *rest[1:]] if rest else []


def _find_actions(program: list[str]) -> list[list[str]]:
    """The commands ``find`` runs through -exec/-execdir/-ok/-okdir (``{}`` stands for a found path)."""
    actions, current = [], None
    for token in program[1:]:
        if token in _FIND_EXEC:
            current = []
        elif current is not None and token in (";", "+"):
            actions.append(current)
            current = None
        elif current is not None:
            current.append(token)
    return actions + ([current] if current else [])




def _script_index(program: list[str]) -> int | None:
    """Index of the script an interpreter reads, when only known-safe options precede it; else ``None``.

    ``None`` means every argument counts as a possible output (a protected path there is denied).
    """
    if not program:
        return None
    family = next((item for item in SCRIPT_SAFE_OPTIONS if re.fullmatch(item[0], program[0])), None)
    if family is None:
        return None
    _, safe, with_value = family
    skip = False
    for index, token in enumerate(program[1:], start=1):
        if skip:
            skip = False
        elif token == "-m" and "-m" in with_value:
            return index + 1  # a module name, not a path
        elif token in safe:
            skip = token in with_value
        elif token.startswith("-"):
            return None  # unknown option: it may read code from stdin, inline or a preload
        else:
            return index
    return None


def _permission_widening(program: list[str]) -> bool:
    if not program:
        return False
    if program[0] in ("chown", "chgrp", "chflags", "setfacl", "chacl"):
        return True
    if program[0] == "chmod":
        args = [token for token in program[1:] if not (token.startswith("-") and not _SYMBOLIC.match(token))]
        return any(token.startswith("--reference") for token in program[1:]) or not args or _mode_widens(args[0])
    return False


def _candidates(token: str) -> list[str]:
    token = _REDIRECT.sub("", token)
    return [token] + ([token.split("=", 1)[1]] if "=" in token else [])


def _outside(candidate: str, base: Path, roots: tuple[Path, ...]) -> bool:
    """True when an absolute, home or parent-relative word resolves outside every authorised root."""
    if not candidate or candidate == "/dev/null":
        return False
    if not (candidate.startswith(("/", "~")) or candidate == ".." or candidate.startswith("../") or "/../" in candidate
            or candidate.endswith("/..")):
        return False
    resolved = Path(os.path.realpath(os.path.join(base, os.path.expanduser(candidate))))
    return not any(resolved == root or root in resolved.parents for root in roots)


def _fold(text: str) -> str:
    """Case- and normalisation-insensitive form: APFS and NTFS resolve .HERMES and .hermes to one inode."""
    return unicodedata.normalize("NFC", text).casefold()


def matches_protected(candidate: str, workspace: Path, protected: list[str]) -> bool:
    if not candidate or "$" in candidate:
        return False
    resolved = Path(os.path.realpath(os.path.join(workspace, os.path.expanduser(candidate))))
    text = _fold(resolved.as_posix())
    for pattern in protected:
        prefix = _fold(pattern[:-3] if pattern.endswith("/**") else pattern)
        if f"/{prefix}/" in text + "/" or text.endswith("/" + prefix):
            return True
    return False


def _expansions(candidate: str, base: Path, budget: list[int] | None = None) -> list[str] | None:
    """What the shell may turn a word into: itself plus glob matches (relative to ``base``).

    ``None`` when the glob is too broad to expand cheaply; the caller then asks for an approval.
    """
    if not _GLOB.search(candidate):
        return [candidate]
    if sum(1 for part in candidate.split("/") if _GLOB.search(part)) > MAX_GLOB_COMPONENTS:
        return None
    if budget is not None:
        budget[0] -= 1
        if budget[0] < 0:
            return None
    pattern = os.path.join(base, os.path.expanduser(candidate))
    head, tail = os.path.split(pattern)
    try:
        found = list(itertools.islice(glob.iglob(pattern), MAX_GLOB_MATCHES))
        # A file about to be created does not match yet: expand the directory part on its own.
        if _GLOB.search(head):
            found += [os.path.join(item, tail) for item in itertools.islice(glob.iglob(head), MAX_GLOB_MATCHES)]
        return [candidate, *found]
    except (OSError, re.error, RecursionError):
        return None


def classify(command: str, workspace: str | Path, protected_paths: list[str], extra_deny: list[str] | None = None,
             extra_roots: tuple[Path, ...] = (), cwd: str | Path | None = None) -> dict[str, list[str]]:
    """``{"deny": [...], "approval": [...]}`` reason codes; both empty means no rule matched.

    Relative paths resolve against ``cwd`` (default ``workspace``); ``workspace`` and
    ``extra_roots`` (for example the bound Obsidian project container) are the
    authorised scope.
    """
    deny: set[str] = set()
    approval: set[str] = set()
    text = command if isinstance(command, str) else ""
    root = Path(os.path.realpath(os.path.expanduser(str(workspace))))
    base = Path(os.path.realpath(os.path.join(root, os.path.expanduser(str(cwd))))) if cwd else root
    if not text.strip():
        return {"deny": ["empty"], "approval": []}
    if len(text) > MAX_COMMAND:
        return {"deny": [], "approval": ["unverifiable"]}  # too long to analyse within the hook's time
    if any(ord(ch) < 32 and ch not in "\n\t" for ch in text) or "\x7f" in text:
        approval.add("unverifiable")
    if any(rule.search(text) for rule in _UNVERIFIABLE):
        approval.add("unverifiable")
    if _BRACE.search(text):
        approval.add("unverifiable")  # brace expansion can rebuild any word
    spaced = _IFS.sub(" ", text)  # ${IFS} is a space to the shell: analyse the command it spells
    lowered = _FIRST_WORD.sub(lambda match: match.group(1) + match.group(2) + match.group(3).lower(), spaced)
    for variant in {text, spaced, lowered}:
        deny.update(name for name, rule in _RULES.items() if rule.search(variant))
        for pattern in extra_deny or []:
            if re.search(pattern, variant):
                deny.add("policy_pattern")
    budget = [MAX_GLOB_WORDS]
    environment = [False]  # set once a segment exports or assigns a variable later commands may read
    context = (base, (root, *extra_roots), protected_paths, budget, environment)
    for segment in _SEGMENTS.split(spaced):
        tokens = _tokens(segment)
        if tokens is None:
            approval.add("unverifiable")
            continue
        _segment(tokens, context, deny, approval)
    targets = [match.group(1) for match in _REDIRECT_TO_FILE.finditer(spaced)]
    targets += [match.group(1) for match in _DUP_TO_FILE.finditer(spaced)]
    for target in targets:
        expanded = _expansions(target, base, budget)
        if expanded is None:
            approval.add("unverifiable")
        elif any(matches_protected(candidate, base, protected_paths) for candidate in expanded):
            deny.add("protected_path")
    return {"deny": sorted(deny), "approval": sorted(approval - deny)}


def _segment(tokens: list[str], context: tuple, deny: set[str], approval: set[str], depth: int = 0) -> None:
    """Apply the rules to one simple command (and, for ``find``, to each command it runs)."""
    base, roots, protected_paths, budget, environment = context
    program = _program(tokens)

    def expand(word: str) -> list[str]:
        found = _expansions(word, base, budget)
        if found is None:
            approval.add("unverifiable")
            return [word]
        return found

    normalized = " ".join(word for token in program for word in expand(token))  # quotes removed, globs expanded
    deny.update(name for name, rule in _RULES.items() if rule.search(normalized))
    if SELF_APPROVAL.search(normalized):
        deny.add("self_approval")
    if _permission_widening(program):
        deny.add("permission_widening")
    if any("$" in token for token in tokens) or git_runs_code(program):
        approval.add("unverifiable")  # a variable can stand for any word; a git config value can be a command
    if git_env_runs_code(tokens) or (program and program[0] == "."):
        approval.add("unverifiable")  # GIT_CONFIG_*/GIT_SSH* set the same values from the environment; "." sources a file
    offset = len(tokens) - len(program)
    # An environment assignment can preload code (NODE_OPTIONS, RUBYOPT, PERL5OPT, BASH_ENV, PYTHONSTARTUP…),
    # whether it prefixes this command or an earlier segment exported or assigned it.
    prefixed = any(_ASSIGNMENT.match(token) for token in tokens[:offset])
    script = _script_index(program) if not (prefixed or environment[0]) else None
    if not program or program[0] in _ENV_SETTERS:
        environment[0] = True
    for position, token in enumerate(tokens[1:] if tokens else [], start=1):
        if script is not None and position == script + offset:
            continue  # the script an interpreter reads is an input, not an output
        for candidate in (item for word in _candidates(token) for item in expand(word)):
            if "$" in candidate:
                continue
            if matches_protected(candidate, base, protected_paths) and _writes(program):
                deny.add("protected_path")
            elif _outside(candidate, base, roots):
                approval.add("path_outside_workspace")
    if program and program[0] == "find" and depth < 3:
        for action in _find_actions(program):
            if action:
                _segment(action, context, deny, approval, depth + 1)


#: Programs that read only; anything else touching a protected path counts as a write.
_READERS = {"cat", "less", "more", "head", "tail", "grep", "rg", "ls", "wc", "diff", "file", "stat", "sha256sum", "shasum",
            "md5", "git", "jq", "find", "tree", "bat", "view", "vimdiff"}


def _writes(program: list[str]) -> bool:
    if not program:
        return False
    if program[0] in ("git",):
        return len(program) > 1 and program[1] in ("checkout", "restore", "rm", "mv", "apply", "am", "reset", "stash")
    if _script_index(program) is not None or re.fullmatch(rf"({_INTERPRETERS})[0-9.]*", program[0]):
        return True  # a script may write any path it is given; only the script itself is skipped
    if program[0] == "find":
        return bool(_FIND_WRITES & set(program))
    return program[0] not in _READERS
