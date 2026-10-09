#!/usr/bin/env python3
"""Mask common credential shapes before text is written anywhere synced.

``redact`` is the only public name. It is a safety net, not a guarantee: it
covers well-known token prefixes, private-key blocks, JWS/JWE tokens,
credentials in URLs, authorization and cookie headers, connection strings,
password flags of common CLIs, ``key = value`` assignments whose key names a
secret, and plain-language "password is X". Every pattern runs in linear time;
callers should still bound the input (``wiki_journal.record`` truncates first).
Stdlib only.
"""
from __future__ import annotations

import re

__all__ = ["redact"]

# Every repetition below is bounded and every pattern anchors on a literal, so
# redaction stays linear in the input; record() also truncates before redacting.
# Linear-time rules for every pattern below: each repetition has an upper bound,
# and each match must start at a token boundary expressed as a fixed-width
# lookbehind (``\b`` re-matches after every '-' or '.', which made runs such as
# ``eyJ-eyJ-…`` or ``a-a-…`` quadratic). record() also truncates before redacting.
# Token patterns start after any non-word character, so a token after "=",
# "/", "?", ":", "." or "-" is still found (KEY=sk-…, ?jwt=eyJ…). Only the JWT
# pattern, which must fail after a long scan when no "." follows, also refuses
# to start after "-" (``eyJ-eyJ-…`` would otherwise be quadratic).
_T = r"(?<![A-Za-z0-9_])"  # not inside a word
_SECRET_KEY = r"(?<![A-Za-z0-9_.-])"  + r"[A-Za-z0-9_.-]{0,40}?(?:api[_-]?key|apikey|access[_-]?key|secret|token|passw(?:or)?d|passwd|pwd|credential|private[_-]?key|client[_-]?secret|auth)[A-Za-z0-9_.-]{0,40}"
_PLAIN_VALUE = re.compile(r"^(?:\d+(?:\.\d+)?|true|false|null|none|\[redacted\])$", re.IGNORECASE)
# Keys whose values are credentials even when they look like plain numbers.
_ALWAYS_SECRET_KEY = re.compile(r"(?i)passw|pwd|secret|credential|private[_-]?key|passphrase")
_SECRET_PATTERNS = (
    # Well-known token shapes (private-key blocks are handled by _redact_key_blocks).
    re.compile(_T + r"(?:sk|rk|pk)[-_](?:live|test|proj|ant)?[-_]?[A-Za-z0-9_-]{16,512}"),
    re.compile(_T + r"github_pat_[A-Za-z0-9_]{20,512}"),
    re.compile(_T + r"gh[pousr]_[A-Za-z0-9]{20,512}"),
    re.compile(_T + r"glpat-[A-Za-z0-9_-]{16,512}"),
    re.compile(_T + r"hf_[A-Za-z0-9]{20,512}"),
    re.compile(_T + r"npm_[A-Za-z0-9]{20,512}"),
    re.compile(_T + r"whsec_[A-Za-z0-9+/=]{16,512}"),
    re.compile(_T + r"xox[abposr]-[A-Za-z0-9-]{10,512}"),
    re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/_-]{1,512}"),
    re.compile(_T + r"AIza[0-9A-Za-z_-]{30,512}"),
    re.compile(_T + r"(?:AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Z])"),
)
_SECRET_SUBSTITUTIONS = (
    # scheme://user:password@host — the password may itself contain '@'; the last '@' before the host wins.
    (re.compile(r"((?<![a-z0-9])[a-z][a-z0-9+.-]{0,30}://[^\s:/@]{0,256}:)([^\s]{1,512}?)(@[^\s@/]{1,256}(?:[/:?#\s]|$))", re.IGNORECASE | re.MULTILINE), r"\1[REDACTED]\3"),
    # Authorization schemes followed by a credential.
    (re.compile(r"(?i)" + _T + r"((?:bearer|basic|token|digest)[ \t]{1,16})([A-Za-z0-9._~+/=-]{8,4096})"), r"\1[REDACTED]"),
    # Cookie / Set-Cookie headers carry session credentials.
    (re.compile(r"(?i)" + _T + r"((?:set-)?cookie[ \t]{0,16}:[ \t]{0,16})([^\n\"']{1,4096})"), r"\1[REDACTED]"),
    # Azure-style connection strings.
    (re.compile(r"(?i)" + _T + r"((?:AccountKey|SharedAccessKey|SharedAccessSignature|sig)=)([^;\s&]{1,1024})"), r"\1[REDACTED]"),
    # curl -u user:pw, --user user:pw, --user=user:pw
    (re.compile(r"((?:^|(?<=\s))(?:-u[ \t]{0,16}|--user(?:[ \t]{1,16}|=))['\"]?[^\s:'\"]{1,256}:)([^\s'\"]{1,512})", re.MULTILINE), r"\1[REDACTED]"),
    # --password X, --password=X, --pass X, sshpass -p X
    (re.compile(r"(?i)((?:^|(?<=[^A-Za-z0-9_]))(?:--pass(?:word)?(?:[ \t]{1,16}|=)|sshpass[ \t]{1,16}-p[ \t]{0,16}))(\"[^\"\n]{0,512}\"|'[^'\n]{0,512}'|[^\s'\"]{1,512})", re.MULTILINE), r"\1[REDACTED]"),
    # mysql/mariadb -pSECRET glued to the flag; the gap to the flag is bounded so the scan stays linear
    (re.compile(r"(?i)(" + _T + r"(?:mysql|mariadb|mysqldump|mysqladmin)(?![A-Za-z0-9_])[^\n]{0,200}?\s-p)(\"[^\"\n]{0,512}\"|'[^'\n]{0,512}'|[^\s'\"]{1,512})"), r"\1[REDACTED]"),
    # natural language: "password is X", "senha: X"
    (re.compile(r"(?i)" + _T + r"((?:password|passphrase|senha|token|secret)[ \t]{1,16}(?:is|é|eh|=)[ \t]{1,16})(\S{1,512})"), r"\1[REDACTED]"),
)
_SECRET_ASSIGNMENT = re.compile(
    r"""(?ix)
    (?P<key>(?:\\?["'])?""" + _SECRET_KEY + r"""(?:\\?["'])?[ \t]{0,16}[:=][ \t]{0,16})
    (?P<value>\\"(?:[^"\\\n]|\\[^"]){0,1024}\\"|"(?:[^"\\\n]|\\.){0,1024}"|'[^'\n]{0,1024}'|[^\s,;}\]\)]{1,1024})
    """
)


def _redact_assignment(match: re.Match[str]) -> str:
    value = match.group("value")
    if value.startswith('\\"') and value.endswith('\\"') and len(value) >= 4:
        quote, inner = '\\"', value[2:-2]
    elif value[:1] in {'"', "'"} and len(value) >= 2:
        quote, inner = value[0], value[1:-1]
    else:
        quote, inner = "", value
    if not inner:
        return match.group(0)
    if _PLAIN_VALUE.match(inner) and not (_ALWAYS_SECRET_KEY.search(match.group("key")) and inner.lower() not in {"true", "false", "null", "none", "[redacted]"}):
        return match.group(0)
    return f"{match.group('key')}{quote}[REDACTED]{quote}"


_KEY_BLOCK_BEGIN = re.compile(r"-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?-----")
_KEY_BLOCK_END = re.compile(r"-----END [A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?-----")


def _redact_key_blocks(text: str) -> str:
    """Replace each private-key block (to its END line, or to the end of text) in one linear pass."""
    parts: list[str] = []
    position = 0
    while True:
        begin = _KEY_BLOCK_BEGIN.search(text, position)
        if begin is None:
            parts.append(text[position:])
            return "".join(parts)
        parts.append(text[position:begin.start()])
        parts.append("[REDACTED]")
        end = _KEY_BLOCK_END.search(text, begin.end())
        if end is None:
            return "".join(parts)
        position = end.end()


_DOTTED_RUN = re.compile(r"[A-Za-z0-9_.=-]+")
_JWT_HEADER = "eyJ"
_JWT_MAX_SEGMENTS = 5  # JWS has 3 segments, JWE has 5
_JWT_HEADER_AFTER = "-_="  # x-auth-eyJ…, ACCESS_TOKEN_eyJ…, jwt=eyJ…


def _jwt_header_start(part: str) -> int:
    """Index of the first ``eyJ`` in ``part`` that starts a token (at 0 or after '-', '_', '='), else -1."""
    index = part.find(_JWT_HEADER)
    while index != -1:
        if index == 0 or part[index - 1] in _JWT_HEADER_AFTER:
            return index
        index = part.find(_JWT_HEADER, index + 1)
    return -1


def _redact_jwt_run(run: str) -> str:
    parts = run.split(".")
    out: list[str] = []
    index = 0
    while index < len(parts):
        part = parts[index]
        start = _jwt_header_start(part)
        payload = parts[index + 1] if index + 1 < len(parts) else ""
        third = parts[index + 2] if index + 2 < len(parts) else None
        # An empty second segment is a JWE with alg "dir" (eyJ…..iv.ciphertext.tag)
        # or a JWS with a detached payload (eyJ…..signature).
        is_jwt = (
            start != -1
            and len(part) - start >= 8
            and (
                (len(payload) >= 5 and (third is not None or payload.startswith(_JWT_HEADER)))
                or (payload == "" and third is not None and len(third) >= 5)
            )
        )
        if not is_jwt:
            out.append(part)
            index += 1
            continue
        out.append(part[:start] + "[REDACTED]")
        index += min(_JWT_MAX_SEGMENTS, len(parts) - index)
    return ".".join(out)


def _redact_jwts(text: str) -> str:
    """Replace JWS/JWE tokens of any size in one linear pass over dotted token runs.

    A header is ``eyJ`` at the start of a run segment or right after a '-', '_'
    or '=' (``x-auth-eyJ…``, ``ACCESS_TOKEN_eyJ…``, ``jwt=eyJ…``); the header,
    payload and every following dotted segment (up to five, for JWE; an empty
    second segment for ``dir`` JWE and detached payloads) are replaced, so no
    part of a large token survives. Over-redaction such as
    ``report.eyJanuary.final.pdf`` is accepted: this is a safety net.
    """
    if _JWT_HEADER not in text:
        return text
    return _DOTTED_RUN.sub(lambda match: _redact_jwt_run(match.group(0)) if _JWT_HEADER in match.group(0) else match.group(0), text)


def redact(text: str) -> str:
    """Mask common credential shapes; the wiki is synced and must never hold a secret.

    This is a safety net, not a guarantee: never paste a credential into a
    conversation or an artifact that is recorded.
    """
    text = _redact_key_blocks(text)
    text = _redact_jwts(text)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    for pattern, replacement in _SECRET_SUBSTITUTIONS:
        text = pattern.sub(replacement, text)
    return _SECRET_ASSIGNMENT.sub(_redact_assignment, text)
