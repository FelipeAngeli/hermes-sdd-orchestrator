"""ApprovalBridge: single-use, bounded human approvals bound to the exact action they cover.

An action the evaluator answers ``REQUIRE_APPROVAL`` gets a request id derived from
its canonical SHA-256. A human grant (``grant``) records that id, the action hash,
the approver, their quoted words and an expiry no later than the policy TTL. The
grant is the only thing that turns that one action into ``ALLOW``, once:

* ``consume`` renames the grant file to a ``.used`` name; ``rename`` is atomic, so
  of two racing executions exactly one wins, and the loser gets ``APPROVAL_USED``;
* the action is re-hashed at consumption; any change after approval (a different
  command, path or target) yields ``APPROVAL_MISMATCH``, closing the race between
  approval and execution;
* ``revoke`` (cancel after approval) moves the grant to ``.revoked`` before use;
* there is no "session", "always" or "all" scope: one grant, one action, once.

Machine actors cannot grant: the approver name goes through the loop engine's
``loop_control.is_machine_name`` check, and the decision of an agent never reaches
this module except as the action to approve.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from pathlib import Path
from typing import Any, Callable

import loop_control

#: Words that name a group, a role or a mailbox rather than one person.
#: Words that name a group, role, bot or mailbox. A name is refused when one of its words is one of
#: them, or is two of them glued together ("opsteam", "deploybot", "teamlead"), so real surnames that
#: merely contain one ("Botelho", "Talbot", "Steamer") stay valid.
_GROUP_WORDS = frozenset({
    "ops", "devops", "dev", "devs", "all", "everyone", "root", "admin", "admins", "crew", "staff", "team", "teams",
    "group", "groups", "squad", "support", "noreply", "service", "services", "system", "systems", "bot", "bots",
    "deploy", "build", "release", "ci", "cd", "qa", "infra", "platform", "lead", "leads", "desk", "help",
    "helpdesk", "sec", "security", "oncall", "reply", "no", "it"})
_ROLE_ONLY = frozenset({"no", "it", "lead", "leads", "desk", "help", "dev", "build", "release", "deploy", "sec", "cd"})
_WORD = re.compile(r"[a-z0-9]+")
#: "<tool>bot" compounds and team-/group- prefixed words. Other "-bot" surnames (Talbot, Abbot) stay valid;
#: an unlisted "<word>bot" may pass, and loop_control.is_machine_name still applies to every name.
_BOT_PREFIXES = ("slack", "chat", "git", "github", "gitlab", "dependa", "renova", "mention", "auto", "discord",
                 "telegram", "jira", "merge", "review", "test", "teams", "mattermost", "notify", "alert", "pager")
_GROUP_PREFIX = re.compile(r"^(team|group|squad)[a-z]{2,}$|^bot(net|farm|s)$")


def _group_name(name: str) -> bool:
    words = _WORD.findall(re.sub(r"([a-z])([A-Z])", r"\1 \2", name).lower())
    for word in words:
        if word in _GROUP_WORDS and word not in _ROLE_ONLY:
            return True
        if any(word[:cut] in _GROUP_WORDS and word[cut:] in _GROUP_WORDS for cut in range(2, len(word) - 1)):
            return True
        if _GROUP_PREFIX.match(word) or (word.endswith(("bot", "bots")) and word.startswith(_BOT_PREFIXES)):
            return True
    return any(left in ("no", "it", "help", "build", "release", "deploy") and right in _GROUP_WORDS
               for left, right in zip(words, words[1:]))


def is_human_approver(name: Any) -> bool:
    """A named person: not empty, not a program or agent, not a team, role or mailbox."""
    return isinstance(name, str) and not loop_control.is_machine_name(name) and not _group_name(name)

GRANTS = "approvals"
_ID = re.compile(r"^ap-[0-9a-f]{32}$")


class ApprovalError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def action_sha256(action: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(action, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()


def request_id(action: dict[str, Any]) -> str:
    return "ap-" + action_sha256(action)[:32]


def _write_exclusive(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(value, sort_keys=True))
        handle.flush()
        os.fsync(handle.fileno())


def _read_private(path: Path) -> dict[str, Any]:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ApprovalError("APPROVAL_INVALID", "grant file is not a private regular file")
        value = json.loads(handle.read(8192).decode("utf-8"))
    if not isinstance(value, dict):
        raise ApprovalError("APPROVAL_INVALID", "grant is not an object")
    return value


class ApprovalBridge:
    def __init__(self, state_dir: str | Path, *, ttl_seconds: int, clock: Callable[[], float] = time.time) -> None:
        self.dir = Path(state_dir) / GRANTS
        self.ttl, self.clock = ttl_seconds, clock

    def _path(self, identifier: str, suffix: str = ".json") -> Path:
        if not _ID.match(identifier):
            raise ApprovalError("APPROVAL_INVALID", "request id has the wrong shape")
        return self.dir / f"{identifier}{suffix}"

    def request(self, action: dict[str, Any]) -> str:
        """Record the exact action awaiting a human decision (idempotent); returns its request id."""
        identifier = request_id(action)
        try:
            _write_exclusive(self._path(identifier, ".request"), {"request_id": identifier, "action": action})
        except FileExistsError:
            pass
        return identifier

    def pending_action(self, identifier: str) -> dict[str, Any]:
        """The action recorded by ``request``; the owner reviews it before ``grant``."""
        try:
            value = _read_private(self._path(identifier, ".request"))
        except FileNotFoundError as error:
            raise ApprovalError("APPROVAL_MISSING", "no pending request with this id") from error
        action = value.get("action")
        if not isinstance(action, dict) or request_id(action) != identifier:
            raise ApprovalError("APPROVAL_MISMATCH", "the recorded request does not match its id")
        return action

    def grant(self, action: dict[str, Any], *, by: str, quote: str, ttl_seconds: int | None = None) -> dict[str, Any]:
        """Record one human approval for exactly this action; refused for machine actors or empty words."""
        if not is_human_approver(by) or not (quote or "").strip():
            raise ApprovalError("APPROVER_NOT_HUMAN", "only a named human with their quoted words can approve")
        ttl = min(self.ttl, ttl_seconds or self.ttl)
        identifier = request_id(action)
        now = self.clock()
        grant = {"request_id": identifier, "action_sha256": action_sha256(action), "by": by[:60],
                 "quote_sha256": hashlib.sha256(quote.encode("utf-8")).hexdigest(), "granted_at": now, "expires_at": now + ttl}
        try:
            _write_exclusive(self._path(identifier), grant)
        except FileExistsError as error:
            raise ApprovalError("APPROVAL_EXISTS", "a grant for this action is already pending") from error
        return grant

    def consume(self, action: dict[str, Any]) -> dict[str, Any]:
        """Use the grant for this action once; raises when absent, expired, revoked, used or for another action."""
        identifier = request_id(action)
        pending, used = self._path(identifier), self._path(identifier, f".used-{os.getpid()}-{time.time_ns()}")
        try:
            os.rename(pending, used)  # atomic: exactly one consumer wins
        except FileNotFoundError as error:
            if self._path(identifier, ".revoked").exists():
                raise ApprovalError("APPROVAL_REVOKED", "the approval was cancelled") from error
            if any(self.dir.glob(f"{identifier}.used-*")):
                raise ApprovalError("APPROVAL_USED", "the approval was already used") from error
            raise ApprovalError("APPROVAL_MISSING", "no pending approval for this action") from error
        grant = _read_private(used)
        if grant.get("action_sha256") != action_sha256(action) or grant.get("request_id") != identifier:
            raise ApprovalError("APPROVAL_MISMATCH", "the action changed after it was approved")
        if not isinstance(grant.get("expires_at"), (int, float)) or self.clock() > grant["expires_at"]:
            raise ApprovalError("APPROVAL_EXPIRED", "the approval expired before use")
        return grant

    def revoke(self, identifier: str) -> bool:
        """Cancel a pending grant; False when it was already used or never existed."""
        try:
            os.rename(self._path(identifier), self._path(identifier, ".revoked"))
            return True
        except FileNotFoundError:
            return False
