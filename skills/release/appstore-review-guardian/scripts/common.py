"""Shared primitives for appstore-review-guardian.

Findings model, severities, rule base, redaction, safe archive access,
hashing cache and optional external tools. Python 3.10+ stdlib only.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import plistlib
import re
import shutil
import stat
import subprocess
import tempfile
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Iterable

TOOL_NAME = "appstore-review-guardian"
TOOL_VERSION = "0.1.0"
SKILL_DIR = Path(__file__).resolve().parent.parent
RULES_DIR = SKILL_DIR / "rules"
SCHEMA_PATH = SKILL_DIR / "schemas" / "audit-report.schema.json"

BLOCKER = "BLOCKER"
CRITICAL = "CRITICAL"
WARNING = "WARNING"
INFO = "INFO"
PASS = "PASS"
NOT_VERIFIED = "NOT_VERIFIED"
NOT_APPLICABLE = "NOT_APPLICABLE"
SEVERITIES = [BLOCKER, CRITICAL, WARNING, INFO, PASS, NOT_VERIFIED, NOT_APPLICABLE]
FAILING = {BLOCKER, CRITICAL}
METHODS = {"deterministic", "heuristic", "manual"}
NON_DETERMINISTIC = ("heuristic", "manual")

# Archive limits (untrusted input). Module globals are read at call time so tests can lower them.
MAX_ENTRIES = 50_000
MAX_TOTAL_UNCOMPRESSED = 4 * 1024**3  # 4 GiB declared, summed over ALL entries
MAX_ENTRY_RATIO = 200  # uncompressed/compressed per entry and for the archive as a whole
RATIO_MIN_SIZE = 10 * 1024 * 1024  # per-entry ratio only enforced for entries >10 MiB
TOTAL_RATIO_MIN_SIZE = 64 * 1024 * 1024  # aggregate ratio enforced once declared total >64 MiB
MAX_EXTRACT_BYTES = 1024**3  # 1 GiB actually written to disk
MAX_STREAM_BYTES = 5 * 1024**3  # bytes actually decompressed per archive (CRC pass + extraction)
MAX_COMPONENT_BYTES = 255
MAX_PATH_BYTES = 1000


def today() -> str:
    return _dt.date.today().isoformat()


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------- redaction
_SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|$)", re.S),
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"\b(sk|pk|rk)_(live|test)_[0-9A-Za-z]{10,}"),
    re.compile(r"\bgh[pousr]_[0-9A-Za-z]{20,}"),
    re.compile(r"\beyJ[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{5,}"),  # JWT
    re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd|client[_-]?secret)\b(\s*[:=]\s*)(['\"]?)[^\s'\"]{6,}"),
    re.compile(r"\b[0-9a-fA-F]{40,}\b"),
    re.compile(r"(?<![A-Za-z0-9+_\-])(?=[A-Za-z0-9+_\-]*\d)(?=[A-Za-z0-9+_\-]*[A-Z])(?=[A-Za-z0-9+_\-]*[a-z])[A-Za-z0-9+_\-]{32,}={0,2}"),
]


def redact(text: Any, limit: int = 600) -> str:
    """Redact anything that looks like a credential and cap the length."""
    s = "" if text is None else str(text)
    home = str(Path.home())
    if home and home != "/":
        s = s.replace(home, "~")
    for pat in _SECRET_PATTERNS:
        if pat.groups >= 3 and "api" in pat.pattern:
            s = pat.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}[REDACTED]", s)
        else:
            s = pat.sub("[REDACTED]", s)
    if len(s) > limit:
        s = s[: limit - 1] + "…"
    return s


# --------------------------------------------------------------------------- rules
class RuleBase:
    def __init__(self, rules_dir: Path = RULES_DIR):
        self.rules: dict[str, dict] = {}
        self.reference: dict = {}
        for path in sorted(rules_dir.glob("*-rules.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for rule in data["rules"]:
                if rule["id"] in self.rules:
                    raise ValueError(f"duplicate rule id {rule['id']}")
                rule["_file"] = path.name
                self.rules[rule["id"]] = rule
        ref = rules_dir / "reference-data.json"
        if ref.exists():
            self.reference = json.loads(ref.read_text(encoding="utf-8"))

    def get(self, rule_id: str) -> dict:
        return self.rules[rule_id]

    def oldest_consultation(self) -> str:
        return min(r["consulted_at"] for r in self.rules.values())

    def official_urls(self) -> list[str]:
        urls = {r["official_url"] for r in self.rules.values()}
        for src in self.reference.get("sources", {}).values():
            urls.add(src["url"])
        return sorted(urls)


_RULES: RuleBase | None = None


def rules() -> RuleBase:
    global _RULES
    if _RULES is None:
        _RULES = RuleBase()
    return _RULES


def ref(key: str) -> Any:
    return rules().reference[key]


# --------------------------------------------------------------------------- findings
@dataclass
class Finding:
    rule_id: str
    severity: str
    component: str
    evidence: str
    recommendation: str = ""
    confidence: float = 0.9
    auto_fixable: bool = False
    verification_method: str | None = None
    file: str | None = None
    title: str | None = None
    extra: dict = field(default_factory=dict)

    def to_dict(self, seq: int) -> dict:
        rule = rules().get(self.rule_id)
        method = self.verification_method or rule["verification_method"]
        sev = self.severity
        if sev not in SEVERITIES:
            raise ValueError(f"bad severity {sev}")
        notes = []
        # Hard semantic guards (central: no call site can bypass them).
        # PASS / NOT_APPLICABLE are verdicts that only a deterministic check may emit. A heuristic or a
        # user declaration (manual) never proves compliance, and heuristic absence never proves N/A.
        if method in NON_DETERMINISTIC and sev == PASS:
            if method == "heuristic" and rule.get("requirement_level") == "mandatory":
                notes.append("PASS heurístico rebaixado para NOT_VERIFIED (evidência encontrada; requer confirmação humana)")
                sev = NOT_VERIFIED
            else:
                notes.append(f"PASS {'heurístico' if method == 'heuristic' else 'manual (declarado)'} rebaixado para INFO "
                             "(evidência registrada; não conta como verificação)")
                sev = INFO
        if method in NON_DETERMINISTIC and sev == NOT_APPLICABLE:
            notes.append("NOT_APPLICABLE por ausência de evidência heurística rebaixado para NOT_VERIFIED")
            sev = NOT_VERIFIED
        if method == "heuristic" and sev in FAILING:
            notes.append(f"severidade rebaixada de {sev} para WARNING (achado heurístico)")
            sev = WARNING
        if rule.get("requirement_level") == "recommended" and sev in FAILING:
            notes.append(f"severidade rebaixada de {sev} para WARNING (regra apenas recomendada)")
            sev = WARNING
        if sev == PASS and not self.evidence.strip():
            notes.append("PASS sem evidência rebaixado para NOT_VERIFIED")
            sev = NOT_VERIFIED
        conf = max(0.0, min(1.0, float(self.confidence)))
        d = {
            "id": f"F{seq:03d}",
            "rule_id": self.rule_id,
            "title": self.title or rule["name"],
            "severity": sev,
            "category": rule["category"],
            "area": rule.get("area", "technical"),
            "guideline": rule.get("guideline"),
            "component": self.component,
            "file": redact(self.file, 300) if self.file else None,
            "evidence": redact(self.evidence),
            "explanation": rule["description"],
            "verification_method": method,
            "requirement_level": rule["requirement_level"],
            "official_url": rule["official_url"],
            "consulted_at": rule["consulted_at"],
            "confidence": round(conf, 2),
            "recommendation": redact(self.recommendation, 800),
            "auto_fixable": bool(self.auto_fixable),
        }
        if notes:
            d["notes"] = notes
        return d


class Collector:
    """Accumulates findings for one module run."""

    def __init__(self) -> None:
        self.items: list[Finding] = []

    def add(self, rule_id: str, severity: str, component: str, evidence: str, **kw) -> Finding:
        rules().get(rule_id)  # validate id early
        f = Finding(rule_id=rule_id, severity=severity, component=component, evidence=evidence, **kw)
        self.items.append(f)
        return f

    def extend(self, other: Iterable[Finding]) -> None:
        self.items.extend(other)


def finding_to_cache(f: Finding) -> dict:
    return asdict(f)


def finding_from_cache(d: dict) -> Finding:
    return Finding(**d)


# --------------------------------------------------------------------------- plist
def load_plist_bytes(data: bytes) -> tuple[Any, str | None]:
    try:
        return plistlib.loads(data), None
    except Exception as exc:  # plistlib raises several types
        return None, f"{type(exc).__name__}: {exc}"


def load_plist(path: Path) -> tuple[Any, str | None]:
    try:
        return load_plist_bytes(path.read_bytes())
    except OSError as exc:
        return None, f"{type(exc).__name__}: {exc}"


def plist_format(data: bytes) -> str:
    if data.startswith(b"bplist"):
        return "binary"
    return "xml"


# --------------------------------------------------------------------------- hashing / cache
def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class HashCache:
    """Per-input result cache stored under the report output dir."""

    def __init__(self, path: Path | None):
        self.path = path
        self.data: dict = {"tool_version": TOOL_VERSION, "entries": {}}
        self.hits = 0
        self.misses = 0
        if path and path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if loaded.get("tool_version") == TOOL_VERSION:
                    self.data = loaded
            except (OSError, ValueError):
                pass

    def get(self, key: str, digest: str) -> Any:
        ent = self.data["entries"].get(key)
        if ent and ent.get("sha256") == digest:
            self.hits += 1
            return ent["value"]
        return None

    def put(self, key: str, digest: str, value: Any) -> None:
        self.misses += 1
        self.data["entries"][key] = {"sha256": digest, "at": now_iso(), "value": value}

    def module(self, key: str, digest: str, fn: Callable[[], tuple[list[Finding], dict]]) -> tuple[list[Finding], dict]:
        """Cache a module result (findings + facts) by input digest."""
        hit = self.get(key, digest)
        if hit is not None:
            return [finding_from_cache(x) for x in hit["findings"]], hit["facts"]
        findings, facts = fn()
        self.put(key, digest, {"findings": [finding_to_cache(f) for f in findings], "facts": facts})
        return findings, facts

    def save(self) -> None:
        if not self.path:
            return
        ensure_private_dir(self.path.parent)
        tmp = self.path.with_suffix(".tmp")
        write_private(tmp, json.dumps(self.data, ensure_ascii=False))
        os.replace(tmp, self.path)


# --------------------------------------------------------------------------- private output
def ensure_private_dir(path: Path) -> Path:
    """Create path (and missing parents) with mode 0700. Existing directories are left untouched."""
    path = Path(path)
    missing = []
    p = path
    while not p.exists():
        missing.append(p)
        if p.parent == p:
            break
        p = p.parent
    for d in reversed(missing):
        try:
            d.mkdir(mode=0o700)
        except FileExistsError:
            continue
        os.chmod(d, 0o700)  # mkdir mode is filtered by the umask
    return path


def write_private(path: Path, text: str) -> Path:
    """Write a UTF-8 text file readable only by the owner (0600), also when it already exists."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fd = -1
            fh.write(text)
    finally:
        if fd >= 0:
            os.close(fd)
    return Path(path)


def make_private_outdir(name: str) -> Path:
    """Default report directory: fresh mkdtemp (0700), never a predictable shared /tmp path."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:60] or "audit"
    d = Path(tempfile.mkdtemp(prefix=f"appstore-review-guardian-{safe}-"))
    os.chmod(d, 0o700)
    return d


# --------------------------------------------------------------------------- external tools
def have_tool(name: str) -> bool:
    return shutil.which(name) is not None


def run_tool(args: list[str], timeout: int = 60, input_bytes: bytes | None = None) -> tuple[int, bytes, bytes]:
    """Run an external tool safely: list args, no shell, timeout."""
    exe = shutil.which(args[0])
    if not exe:
        return 127, b"", f"{args[0]} not found".encode()
    try:
        cp = subprocess.run([exe, *args[1:]], input=input_bytes, capture_output=True, timeout=timeout, shell=False, check=False)
        return cp.returncode, cp.stdout, cp.stderr
    except subprocess.TimeoutExpired:
        return 124, b"", b"timeout"
    except OSError as exc:
        return 126, b"", str(exc).encode()


# --------------------------------------------------------------------------- safe archives
class UnsafeArchive(Exception):
    pass


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def validate_zip(zf: zipfile.ZipFile, total_ratio_min: int | None = None) -> list[zipfile.ZipInfo]:
    """Reject unsafe archives. Returns the list of entries when safe.

    Budgets are enforced over the whole archive (sum of declared sizes and aggregate compression
    ratio), not only per entry, so many entries just below the per-entry thresholds are still caught.
    """
    infos = zf.infolist()
    if len(infos) > MAX_ENTRIES:
        raise UnsafeArchive(f"muitas entradas ({len(infos)} > {MAX_ENTRIES})")
    total_ratio_min = TOTAL_RATIO_MIN_SIZE if total_ratio_min is None else total_ratio_min
    total = 0
    total_compressed = 0
    for info in infos:
        name = info.filename
        if name.startswith("/") or name.startswith("\\") or re.match(r"^[A-Za-z]:", name):
            raise UnsafeArchive(f"caminho absoluto na entrada: {name!r}")
        parts = re.split(r"[\\/]", name)
        if ".." in parts:
            raise UnsafeArchive(f"entrada com '..' (zip-slip): {name!r}")
        if "\x00" in name:
            raise UnsafeArchive("nome de entrada com byte nulo")
        if _is_symlink(info):
            raise UnsafeArchive(f"entrada simbólica (symlink): {name!r}")
        total += info.file_size
        total_compressed += info.compress_size
        if total > MAX_TOTAL_UNCOMPRESSED:
            raise UnsafeArchive(f"tamanho descompactado declarado (soma das entradas) excede {MAX_TOTAL_UNCOMPRESSED} bytes")
        if info.file_size > RATIO_MIN_SIZE and info.compress_size > 0:
            if info.file_size / info.compress_size > MAX_ENTRY_RATIO:
                raise UnsafeArchive(f"taxa de compressão suspeita (zip bomb) em {name!r}")
        if info.file_size > RATIO_MIN_SIZE and info.compress_size == 0:
            raise UnsafeArchive(f"entrada com tamanho comprimido zero e grande descompactado: {name!r}")
    if total > total_ratio_min and total / max(total_compressed, 1) > MAX_ENTRY_RATIO:
        raise UnsafeArchive(f"taxa de compressão agregada suspeita (zip bomb): {total} bytes declarados para "
                            f"{total_compressed} comprimidos em {len(infos)} entradas")
    return infos


class ReadBudget:
    """Counts bytes actually decompressed from one archive (all passes) against a hard cap."""

    def __init__(self, limit: int | None = None):
        self.limit = MAX_STREAM_BYTES if limit is None else limit
        self.used = 0

    def consume(self, n: int) -> None:
        self.used += n
        if self.used > self.limit:
            raise UnsafeArchive(f"orçamento de bytes descompactados excedido ({self.limit} bytes lidos de fato)")


def stream_check(zf: zipfile.ZipFile, infos: Iterable[zipfile.ZipInfo], budget: ReadBudget) -> str | None:
    """CRC pass (replaces ZipFile.testzip) that counts streamed bytes. Returns the first bad member or None."""
    for info in infos:
        if info.is_dir():
            continue
        try:
            with zf.open(info) as fh:
                while True:
                    b = fh.read(1 << 20)
                    if not b:
                        break
                    budget.consume(len(b))
        except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError):
            return info.filename
    return None


def read_member(zf: zipfile.ZipFile, name: str, max_bytes: int = 64 * 1024 * 1024) -> bytes:
    """Read a single member with a hard cap regardless of declared size."""
    info = zf.getinfo(name)
    if info.file_size > max_bytes:
        raise UnsafeArchive(f"entrada {name!r} maior que o limite de leitura ({max_bytes})")
    out = bytearray()
    with zf.open(info) as fh:
        while True:
            b = fh.read(1 << 20)
            if not b:
                break
            out += b
            if len(out) > max_bytes:
                raise UnsafeArchive(f"entrada {name!r} excedeu o limite durante a leitura")
    return bytes(out)


def make_private_tempdir(prefix: str = "asrg-") -> Path:
    d = Path(tempfile.mkdtemp(prefix=prefix))
    os.chmod(d, 0o700)
    return d


@dataclass
class ExtractResult:
    """Outcome of extract_members: archive name -> temp path, plus members refused with the reason."""
    root: Path
    mapping: dict[str, Path] = field(default_factory=dict)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    written: int = 0

    def path_for(self, name: str) -> Path | None:
        return self.mapping.get(name)


def _fold(s: str) -> str:
    # APFS/HFS+ default volumes are case-insensitive and normalization-insensitive.
    return unicodedata.normalize("NFD", s).casefold()


def plan_members(names: Iterable[str]) -> tuple[dict[str, list[str]], list[tuple[str, str]]]:
    """Decide where each archive member may live under the temp dir WITHOUT touching the filesystem.

    Refuses (per member, never raising) names that cannot be materialized safely on any supported
    filesystem: components longer than 255 bytes, overlong paths, '.', empty or control-char
    components, case/normalization-insensitive collisions and file-vs-directory conflicts.
    Returns (archive name -> path components, [(archive name, reason)]).
    """
    plan: dict[str, list[str]] = {}
    skipped: list[tuple[str, str]] = []
    files: dict[str, str] = {}  # folded path -> archive name
    dirs: dict[str, str] = {}   # folded dir prefix -> first archive name that needs it
    for name in names:
        parts = name.split("/")
        reason = None
        key, prefixes = "", []
        if len(name.encode("utf-8", "surrogateescape")) > MAX_PATH_BYTES:
            reason = f"caminho com mais de {MAX_PATH_BYTES} bytes"
        elif any(p in ("", ".", "..") for p in parts):
            reason = "componente vazio ou relativo"
        elif any(len(p.encode("utf-8", "surrogateescape")) > MAX_COMPONENT_BYTES for p in parts):
            reason = f"componente com mais de {MAX_COMPONENT_BYTES} bytes"
        elif any(ord(ch) < 32 or ch == "\\" for p in parts for ch in p):
            reason = "caractere de controle ou '\\' no nome"
        if reason is None:
            folded = [_fold(p) for p in parts]
            key = "/".join(folded)
            prefixes = ["/".join(folded[:i]) for i in range(1, len(folded))]
            if key in files:
                reason = f"colide com {files[key]!r} (maiúsculas/minúsculas ou normalização Unicode)"
            elif key in dirs:
                reason = f"arquivo com o mesmo nome do diretório usado por {dirs[key]!r}"
            else:
                clash = next((p for p in prefixes if p in files), None)
                if clash is not None:
                    reason = f"diretório com o mesmo nome do arquivo {files[clash]!r}"
        if reason is not None:
            skipped.append((name, reason))
            continue
        files[key] = name
        for p in prefixes:
            dirs.setdefault(p, name)
        plan[name] = parts
    return plan, skipped


def extract_members(zf: zipfile.ZipFile, names: Iterable[str], dest: Path, max_bytes: int | None = None,
                    budget: ReadBudget | None = None) -> ExtractResult:
    """Extract only selected members into dest (a private temp dir), never following links.

    Archive names never reach the filesystem unchecked: plan_members() rejects anything that could
    collide or exceed OS limits, every target is re-checked to stay inside dest, files are created
    with O_EXCL, and any per-member OSError is recorded in ``skipped`` instead of propagating.
    Only UnsafeArchive (size/budget limits) aborts the whole extraction.
    """
    max_bytes = MAX_EXTRACT_BYTES if max_bytes is None else max_bytes
    budget = budget or ReadBudget()
    dest = dest.resolve()
    res = ExtractResult(root=dest)
    wanted = []
    for name in names:
        info = zf.getinfo(name)
        if not info.is_dir():
            wanted.append(info)
    plan, skipped = plan_members(i.filename for i in wanted)
    res.skipped.extend(skipped)
    for info in wanted:
        parts = plan.get(info.filename)
        if parts is None:
            continue
        target = dest.joinpath(*parts)
        try:
            parent = target.parent
            parent.mkdir(parents=True, exist_ok=True)
            if parent.resolve() != parent or (dest not in parent.parents and parent != dest):
                raise OSError("destino fora do diretório temporário")
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with zf.open(info) as src, os.fdopen(fd, "wb") as dst:
                while True:
                    b = src.read(1 << 20)
                    if not b:
                        break
                    budget.consume(len(b))
                    res.written += len(b)
                    if res.written > max_bytes:
                        raise UnsafeArchive("limite de bytes extraídos excedido")
                    dst.write(b)
        except UnsafeArchive:
            raise
        except (OSError, zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError) as exc:
            res.skipped.append((info.filename, f"{type(exc).__name__}: {getattr(exc, 'strerror', None) or exc}"))
            continue
        res.mapping[info.filename] = target
    return res


def cleanup_dir(path: Path | None) -> None:
    if path and path.exists():
        shutil.rmtree(path, ignore_errors=True)


# --------------------------------------------------------------------------- misc
def rel(path: Path, base: Path | None) -> str:
    try:
        return str(path.relative_to(base)) if base else str(path)
    except ValueError:
        return str(path)


def version_tuple(s: str) -> tuple[int, ...]:
    out = []
    for p in str(s).split("."):
        m = re.match(r"\d+", p)
        out.append(int(m.group(0)) if m else 0)
    return tuple(out)


def read_text_safe(path: Path, max_bytes: int = 2 * 1024 * 1024) -> str:
    try:
        with open(path, "rb") as fh:
            return fh.read(max_bytes).decode("utf-8", errors="replace")
    except OSError:
        return ""


def dump_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False)
