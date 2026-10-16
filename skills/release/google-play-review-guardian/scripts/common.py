"""Shared primitives for google-play-review-guardian.

Findings, severities, rule loading, safe ZIP handling, hash cache, tool
discovery and a subprocess runner with timeouts. Python 3.10+ stdlib only.
Everything here is read-only with respect to the audited project.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
import zlib
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Iterable

TOOL_NAME = "google-play-review-guardian"
TOOL_VERSION = "0.1.0"
SKILL_DIR = Path(__file__).resolve().parent.parent
RULES_DIR = SKILL_DIR / "rules"
SCHEMA_PATH = SKILL_DIR / "schemas" / "audit-report.schema.json"

ANDROID_NS = "http://schemas.android.com/apk/res/android"

# --------------------------------------------------------------------------
# Severities and statuses
# --------------------------------------------------------------------------
BLOCKER = "BLOCKER"
CRITICAL = "CRITICAL"
WARNING = "WARNING"
INFO = "INFO"
PASS = "PASS"
NOT_VERIFIED = "NOT_VERIFIED"
NOT_APPLICABLE = "NOT_APPLICABLE"
SEVERITIES = (BLOCKER, CRITICAL, WARNING, INFO, PASS, NOT_VERIFIED, NOT_APPLICABLE)
SEVERITY_ORDER = {s: i for i, s in enumerate(SEVERITIES)}

METHODS = ("deterministic", "heuristic", "manual")
CONFIDENCE = ("high", "medium", "low")

READY = "READY"
NEEDS_REVIEW = "NEEDS_REVIEW"
BLOCKED = "BLOCKED"
EXIT_CODES = {READY: 0, NEEDS_REVIEW: 1, BLOCKED: 2}
EXIT_USAGE = 3

SECTIONS = {
    "android_build": "ANDROID BUILD",
    "target_sdk": "TARGET SDK",
    "page_size_16kb": "16 KB PAGE COMPATIBILITY",
    "signing": "SIGNING",
    "security": "SECURITY",
    "permissions": "PERMISSIONS",
    "data_safety": "DATA SAFETY",
    "billing": "GOOGLE PLAY BILLING",
    "play_console": "PLAY CONSOLE METADATA",
    "testing": "TESTING / PRODUCTION ACCESS",
}


def today_iso() -> str:
    return _dt.date.today().isoformat()


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def parse_date(value: str | None) -> _dt.date | None:
    if not value:
        return None
    try:
        return _dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------
_SECRET_ASSIGN = re.compile(
    r"(?i)((?:store|key)?password|passwd|secret|api[_-]?key|token|private[_-]?key)(\s*[:=]\s*|\s+(?=['\"]))(['\"]?)([^'\"\s,;]{3,})"
)
_SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
    re.compile(r"sk_live_[0-9A-Za-z]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"ghp_[0-9A-Za-z]{30,}"),
    re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}"),
]


def mask(value: str) -> str:
    """Show only a short prefix and the length of a secret-looking value."""
    value = str(value)
    if len(value) <= 6:
        return "***"
    return f"{value[:4]}…[redacted:{len(value)} chars]"


def redact(text: Any) -> Any:
    if not isinstance(text, str):
        return text
    out = _SECRET_ASSIGN.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{mask(m.group(4))}", text)
    for pat in _SECRET_PATTERNS:
        out = pat.sub(lambda m: mask(m.group(0)), out)
    return out


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------
RULE_FILES = ("technical-rules.json", "privacy-rules.json", "policy-rules.json")
RULE_REQUIRED_FIELDS = (
    "id", "name", "description", "category", "official_url", "consulted_at",
    "effective_from", "platforms_affected", "applies_when", "severity_if_violated",
    "verification_method", "requirement_level",
)


def load_rules(rules_dir: Path | None = None) -> dict[str, dict]:
    rules_dir = rules_dir or RULES_DIR
    rules: dict[str, dict] = {}
    for name in RULE_FILES:
        data = json.loads((rules_dir / name).read_text(encoding="utf-8"))
        for rule in data["rules"]:
            missing = [f for f in RULE_REQUIRED_FIELDS if f not in rule]
            if missing:
                raise ValueError(f"rule {rule.get('id')} in {name} missing {missing}")
            if rule["id"] in rules:
                raise ValueError(f"duplicate rule id {rule['id']}")
            rule["_file"] = name
            rules[rule["id"]] = rule
    return rules


def rules_digest(rules_dir: Path | None = None) -> str:
    h = hashlib.sha256()
    for name in RULE_FILES:
        h.update(((rules_dir or RULES_DIR) / name).read_bytes())
    return h.hexdigest()


# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------
@dataclass
class Finding:
    rule_id: str
    severity: str
    section: str
    title: str
    component: str
    evidence: str
    verification_method: str
    confidence: str
    recommendation: str
    official_url: str = ""
    consulted_at: str = ""
    requirement_level: str = "recommended"
    auto_fixable: bool = False
    id: str = ""

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"bad severity {self.severity}")
        if self.verification_method not in METHODS:
            raise ValueError(f"bad method {self.verification_method}")
        if self.confidence not in CONFIDENCE:
            raise ValueError(f"bad confidence {self.confidence}")
        if self.section not in SECTIONS:
            raise ValueError(f"bad section {self.section}")
        self.evidence = redact(self.evidence)
        self.recommendation = redact(self.recommendation)
        self.component = redact(self.component)

    def to_dict(self) -> dict:
        return asdict(self)


class FindingFactory:
    """Creates findings bound to the rule base (URL/date come from rule data)."""

    def __init__(self, rules: dict[str, dict]):
        self.rules = rules

    def __call__(self, rule_id: str, severity: str, section: str, title: str, *,
                 component: str = "", evidence: str = "", method: str = "deterministic",
                 confidence: str = "high", recommendation: str = "", auto_fixable: bool = False) -> Finding:
        rule = self.rules.get(rule_id)
        if rule is None:
            raise KeyError(f"unknown rule id {rule_id}")
        # Hard semantic guards: BLOCKER/CRITICAL need confirmed evidence.
        if severity in (BLOCKER, CRITICAL) and (method == "manual" or confidence == "low"):
            severity = WARNING
        return Finding(
            rule_id=rule_id, severity=severity, section=section, title=title,
            component=component or "-", evidence=evidence or "-",
            verification_method=method, confidence=confidence,
            recommendation=recommendation or "-", official_url=rule["official_url"],
            consulted_at=rule["consulted_at"], requirement_level=rule["requirement_level"],
            auto_fixable=auto_fixable,
        )


def finding_from_dict(d: dict) -> Finding:
    return Finding(**{k: d[k] for k in Finding.__dataclass_fields__ if k in d})


def section_status(findings: Iterable[Finding]) -> str:
    sev = {f.severity for f in findings}
    if not sev:
        return NOT_VERIFIED
    if sev & {BLOCKER, CRITICAL}:
        return "BLOCKED"
    if WARNING in sev:
        return WARNING
    if NOT_VERIFIED in sev:
        return NEEDS_REVIEW if sev & {PASS, INFO} else NOT_VERIFIED
    if sev <= {NOT_APPLICABLE}:
        return NOT_APPLICABLE
    return PASS


def final_status(findings: Iterable[Finding]) -> str:
    """READY means only: no blocker in the checks that ran, no pending mandatory check."""
    findings = list(findings)
    if any(f.severity in (BLOCKER, CRITICAL) for f in findings):
        return BLOCKED
    if any(f.severity == WARNING for f in findings):
        return NEEDS_REVIEW
    if any(f.severity == NOT_VERIFIED and f.requirement_level == "mandatory" for f in findings):
        return NEEDS_REVIEW
    if not any(f.severity == PASS for f in findings):
        return NEEDS_REVIEW
    return READY


# --------------------------------------------------------------------------
# Safe ZIP handling (archives are untrusted input)
# --------------------------------------------------------------------------
MAX_ENTRIES = 200_000
MAX_TOTAL_UNCOMPRESSED = 8 * 1024 ** 3
MAX_RATIO = 200
RATIO_MIN_SIZE = 10 * 1024 ** 2


@dataclass
class ZipIssue:
    kind: str  # zip_slip | absolute_path | symlink | zip_bomb | too_many_entries | corrupt | duplicate
    entry: str
    detail: str = ""


@dataclass
class ZipScan:
    ok: bool
    entries: list[zipfile.ZipInfo] = field(default_factory=list)
    issues: list[ZipIssue] = field(default_factory=list)
    error: str = ""
    crc_checked: bool = False


def is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF)


def unsafe_name(name: str) -> str | None:
    norm = name.replace("\\", "/")
    if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm):
        return "absolute_path"
    if any(part == ".." for part in norm.split("/")):
        return "zip_slip"
    return None


def scan_zip(path: Path, crc_check: bool = True) -> ZipScan:
    """List and validate an archive without extracting it."""
    if not path.is_file():
        return ZipScan(ok=False, error=f"arquivo não encontrado: {path}")
    if not zipfile.is_zipfile(path):
        return ZipScan(ok=False, error="arquivo não é um ZIP válido (EOCD ausente ou corrompido)")
    scan = ZipScan(ok=True)
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            scan.entries = infos
            if len(infos) > MAX_ENTRIES:
                scan.issues.append(ZipIssue("too_many_entries", "*", str(len(infos))))
            total = 0
            seen: set[str] = set()
            for info in infos:
                kind = unsafe_name(info.filename)
                if kind:
                    scan.issues.append(ZipIssue(kind, info.filename))
                if is_symlink(info):
                    scan.issues.append(ZipIssue("symlink", info.filename))
                if info.filename in seen:
                    scan.issues.append(ZipIssue("duplicate", info.filename))
                seen.add(info.filename)
                total += info.file_size
                if info.file_size > RATIO_MIN_SIZE and info.file_size / max(info.compress_size, 1) > MAX_RATIO:
                    scan.issues.append(ZipIssue("zip_bomb", info.filename,
                                                f"ratio {info.file_size // max(info.compress_size, 1)}:1"))
            if total > MAX_TOTAL_UNCOMPRESSED:
                scan.issues.append(ZipIssue("zip_bomb", "*", f"total uncompressed {total} bytes"))
            dangerous = any(i.kind in ("zip_bomb", "too_many_entries") for i in scan.issues)
            if crc_check and not dangerous:
                scan.crc_checked = True
                for info in infos:
                    if info.is_dir() or is_symlink(info):
                        continue
                    try:
                        with zf.open(info) as fh:
                            while fh.read(1024 * 1024):
                                pass
                    except (zipfile.BadZipFile, zlib.error, EOFError, OSError, RuntimeError, NotImplementedError) as exc:
                        scan.issues.append(ZipIssue("corrupt", info.filename, f"CRC/descompressão falhou: {type(exc).__name__}"))
                        break
    except (zipfile.BadZipFile, OSError, EOFError, RuntimeError, NotImplementedError) as exc:
        return ZipScan(ok=False, error=f"ZIP corrompido: {type(exc).__name__}: {exc}")
    scan.ok = not any(i.kind in ("corrupt",) for i in scan.issues)
    return scan


def read_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, max_bytes: int = 64 * 1024 ** 2) -> bytes:
    """Read at most max_bytes from a member (bounded, never trusts declared size)."""
    with zf.open(info) as fh:
        return fh.read(max_bytes)


def local_data_offset(path: Path, info: zipfile.ZipInfo) -> int:
    """Offset of the member's data, computed from the *local* file header."""
    with open(path, "rb") as fh:
        fh.seek(info.header_offset)
        hdr = fh.read(30)
        if len(hdr) < 30 or hdr[:4] != b"PK\x03\x04":
            raise ValueError("local header inválido")
        name_len = int.from_bytes(hdr[26:28], "little")
        extra_len = int.from_bytes(hdr[28:30], "little")
        return info.header_offset + 30 + name_len + extra_len


class SecureTempDir:
    """mkdtemp with 0700 permissions, always removed on exit."""

    def __init__(self, prefix: str = "gprg-"):
        self.prefix = prefix
        self.path: Path | None = None

    def __enter__(self) -> Path:
        self.path = Path(tempfile.mkdtemp(prefix=self.prefix))
        os.chmod(self.path, 0o700)
        return self.path

    def __exit__(self, *exc) -> None:
        if self.path and self.path.exists():
            shutil.rmtree(self.path, ignore_errors=True)


def extract_member_safe(zf: zipfile.ZipFile, info: zipfile.ZipInfo, dest_dir: Path, index: int,
                        max_bytes: int = 512 * 1024 ** 2) -> Path:
    """Extract one member to a name we generate (archive paths are never used as paths)."""
    if unsafe_name(info.filename) or is_symlink(info):
        raise ValueError(f"entrada insegura recusada: {info.filename!r}")
    base = re.sub(r"[^A-Za-z0-9._-]", "_", info.filename.rsplit("/", 1)[-1])[:80] or "member"
    target = dest_dir / f"{index:05d}_{base}"
    written = 0
    with zf.open(info) as src, open(target, "xb") as dst:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                raise ValueError("entrada excede limite de extração")
            dst.write(chunk)
    os.chmod(target, 0o600)
    return target


# --------------------------------------------------------------------------
# Hash cache (incremental)
# --------------------------------------------------------------------------
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class HashCache:
    """JSON cache under <output-dir>/.guardian-cache. Corrupt cache is ignored."""

    def __init__(self, output_dir: Path | None, enabled: bool = True):
        self.enabled = enabled and output_dir is not None
        self.dir = (output_dir / ".guardian-cache") if output_dir else None
        self.hits = 0
        self.misses = 0
        self._index: dict = {}
        if self.enabled:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._index = self._load("file-index.json")

    def _load(self, name: str) -> dict:
        try:
            return json.loads((self.dir / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def file_hash(self, path: Path) -> str:
        st = path.stat()
        key = str(path.resolve())
        meta = self._index.get(key)
        if meta and meta.get("size") == st.st_size and meta.get("mtime_ns") == st.st_mtime_ns:
            return meta["sha256"]
        digest = sha256_file(path)
        self._index[key] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": digest}
        if self.enabled:
            (self.dir / "file-index.json").write_text(json.dumps(self._index, indent=0), encoding="utf-8")
        return digest

    def get(self, key: str) -> Any:
        if not self.enabled:
            return None
        p = self.dir / "results" / f"{key}.json"
        try:
            value = json.loads(p.read_text(encoding="utf-8"))
            self.hits += 1
            return value
        except (OSError, ValueError):
            self.misses += 1
            return None

    def put(self, key: str, value: Any) -> None:
        if not self.enabled:
            return
        d = self.dir / "results"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{key}.json").write_text(json.dumps(value), encoding="utf-8")


def cache_key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


# --------------------------------------------------------------------------
# Tool discovery and execution
# --------------------------------------------------------------------------
@dataclass
class ToolResult:
    ok: bool
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None  # not_found | timeout | nonzero | os_error

    def reason(self) -> str:
        if self.error == "timeout":
            return "ferramenta excedeu o timeout"
        if self.error == "not_found":
            return "ferramenta não encontrada"
        if self.error == "nonzero":
            return f"ferramenta retornou código {self.returncode}: {self.stderr.strip()[:200]}"
        if self.error:
            return f"falha ao executar ferramenta: {self.error}"
        return "ok"


MAX_OUTPUT = 4 * 1024 ** 2


def run_tool(cmd: list[str], timeout: int = 120, env: dict[str, str] | None = None) -> ToolResult:
    """Run an external tool without a shell; bounded time and output. ``env`` replaces the environment."""
    if not cmd or not cmd[0]:
        return ToolResult(False, None, error="not_found")
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL, env=env)
    except FileNotFoundError:
        return ToolResult(False, None, error="not_found")
    except subprocess.TimeoutExpired:
        return ToolResult(False, None, error="timeout")
    except OSError as exc:
        return ToolResult(False, None, error=f"os_error: {exc}")
    out = proc.stdout[:MAX_OUTPUT].decode("utf-8", "replace")
    err = proc.stderr[:MAX_OUTPUT].decode("utf-8", "replace")
    if proc.returncode != 0:
        return ToolResult(False, proc.returncode, out, err, error="nonzero")
    return ToolResult(True, 0, out, err)


def _version_key(name: str) -> tuple:
    return tuple(int(p) if p.isdigit() else -1 for p in re.split(r"[.\-]", name))


def sdk_roots() -> list[Path]:
    cands = []
    for env in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        value = os.environ.get(env)
        if value:
            cands.append(Path(value))
    home = Path.home()
    cands += [home / "Library/Android/sdk", home / "Android/Sdk"]
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        cands.append(Path(local_appdata) / "Android/Sdk")
    seen, out = set(), []
    for c in cands:
        if c.is_dir() and str(c) not in seen:
            seen.add(str(c))
            out.append(c)
    return out


def _newest_subdir(parent: Path) -> list[Path]:
    if not parent.is_dir():
        return []
    return sorted([p for p in parent.iterdir() if p.is_dir()], key=lambda p: _version_key(p.name), reverse=True)


JAVA_TOOLS = ("apksigner", "apkanalyzer", "bundletool")


@dataclass
class ToolInfo:
    name: str
    path: str | None
    available: bool
    reason: str = ""
    java_home: str | None = None
    java_path: str | None = None


def discover_tools() -> dict[str, ToolInfo]:
    """Locate official Android tools. Java-backed tools need a working JRE."""
    exe = ".exe" if os.name == "nt" else ""
    found: dict[str, str | None] = {}
    for name in ("aapt2", "zipalign", "apksigner", "apkanalyzer", "bundletool", "llvm-readelf", "adb"):
        found[name] = shutil.which(name)
    for root in sdk_roots():
        for bt in _newest_subdir(root / "build-tools"):
            for name in ("aapt2", "zipalign", "apksigner"):
                cand = bt / (name + (".bat" if name == "apksigner" and os.name == "nt" else exe))
                if not found.get(name) and cand.is_file():
                    found[name] = str(cand)
        cand = root / "cmdline-tools/latest/bin" / ("apkanalyzer" + (".bat" if os.name == "nt" else ""))
        if not found.get("apkanalyzer") and cand.is_file():
            found["apkanalyzer"] = str(cand)
        cand = root / "platform-tools" / ("adb" + exe)
        if not found.get("adb") and cand.is_file():
            found["adb"] = str(cand)
        for ndk in _newest_subdir(root / "ndk"):
            for prebuilt in sorted((ndk / "toolchains/llvm/prebuilt").glob("*")):
                cand = prebuilt / "bin" / ("llvm-readelf" + exe)
                if not found.get("llvm-readelf") and cand.is_file():
                    found["llvm-readelf"] = str(cand)
    bundletool_jar = os.environ.get("BUNDLETOOL_JAR")
    if not found.get("bundletool") and bundletool_jar and Path(bundletool_jar).is_file():
        found["bundletool"] = bundletool_jar
    if not found.get("bundletool"):
        for root in sdk_roots():
            jars = sorted((root / "bundletool").glob("bundletool*.jar"), key=lambda p: _version_key(p.stem), reverse=True)
            if jars:
                found["bundletool"] = str(jars[0])
                break
    java_home, java_ok, java_reason = _discover_java()
    java_path = str(_java_executable(java_home)) if java_home else shutil.which("java")
    tools = {"java": ToolInfo("java", java_path, java_ok, java_reason, java_home=str(java_home) if java_ok and java_home else None)}
    for name, path in found.items():
        if not path:
            tools[name] = ToolInfo(name, None, False, "não encontrado (PATH, ANDROID_HOME, ANDROID_SDK_ROOT, SDK padrão)")
        elif name in JAVA_TOOLS and not java_ok:
            tools[name] = ToolInfo(name, path, False, f"encontrado, mas requer Java: {java_reason}")
        elif name in JAVA_TOOLS:
            tools[name] = ToolInfo(name, path, True, "", java_home=tools["java"].java_home, java_path=java_path)
        else:
            tools[name] = ToolInfo(name, path, True, "")
    return tools


def _java_executable(home: Path) -> Path:
    return home / "bin" / ("java.exe" if os.name == "nt" else "java")


def android_studio_java_homes() -> list[Path]:
    """Java runtimes bundled with Android Studio (JBR), newest layouts first; nothing is installed."""
    home = Path.home()
    cands = [
        Path("/Applications/Android Studio.app/Contents/jbr/Contents/Home"),
        home / "Applications/Android Studio.app/Contents/jbr/Contents/Home",
        Path("/opt/android-studio/jbr"), home / "android-studio/jbr",
    ]
    program_files = os.environ.get("ProgramFiles")
    if program_files:
        cands.append(Path(program_files) / "Android/Android Studio/jbr")
    return cands


def _discover_java() -> tuple[Path | None, bool, str]:
    """Pick a working Java: JAVA_HOME, then `java` on PATH, then Android Studio's bundled JBR.

    Returns (java_home or None when only PATH's `java` works, ok, reason). Each candidate is
    run once with `-version`; a binary that exists but does not run (macOS stub without a JRE)
    is skipped, not trusted.
    """
    candidates: list[tuple[Path | None, str]] = []
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidates.append((Path(java_home), "JAVA_HOME"))
    on_path = shutil.which("java")
    if on_path:
        candidates.append((None, on_path))
    candidates += [(home, "Android Studio") for home in android_studio_java_homes()]
    tried: list[str] = []
    for home, label in candidates:
        exe = str(_java_executable(home)) if home else label
        if home and not Path(exe).is_file():
            continue
        if run_tool([exe, "-version"], timeout=20).ok:
            return home, True, ""
        tried.append(label)
    if tried:
        return None, False, f"java presente mas não executa ({', '.join(tried)}; sem JRE instalado?)"
    return None, False, "java não encontrado (JAVA_HOME, PATH, Android Studio)"


def tool_env(tool: ToolInfo) -> dict[str, str] | None:
    """Minimal environment for Java-based wrapper scripts: the discovered Java first on PATH.

    Native tools return None (inherit the default environment). Only the variables a JVM
    wrapper needs are passed, so no credential in the caller's environment reaches the tool.
    """
    if tool.name not in JAVA_TOOLS or not tool.java_home:
        return None
    env = {"JAVA_HOME": tool.java_home, "PATH": os.pathsep.join([str(Path(tool.java_home) / "bin"), os.defpath])}
    for name in ("HOME", "LANG", "TMPDIR"):
        value = os.environ.get(name)
        if value:
            env[name] = value
    return env


def tool_cmd(tool: ToolInfo, *args: str) -> list[str]:
    if tool.name == "bundletool" and tool.path and tool.path.endswith(".jar"):
        return [tool.java_path or "java", "-jar", tool.path, *args]
    return [tool.path or "", *args]


# --------------------------------------------------------------------------
# Misc
# --------------------------------------------------------------------------
def read_text_limited(path: Path, max_bytes: int = 2 * 1024 ** 2) -> str:
    with open(path, "rb") as fh:
        return fh.read(max_bytes).decode("utf-8", "replace")


SENSITIVE_FILENAMES = re.compile(r"(?i)(^key\.properties$|\.jks$|\.keystore$|\.p12$|\.pem$|^local\.properties$|service[-_]?account.*\.json$)")


def is_sensitive_file(path: Path) -> bool:
    return bool(SENSITIVE_FILENAMES.search(path.name))


def safe_call(fn: Callable, *args, **kwargs):
    """Run a module; return (result, error_string). Isolates module failures."""
    try:
        return fn(*args, **kwargs), None
    except Exception as exc:  # noqa: BLE001 - module isolation is intentional
        return None, f"{type(exc).__name__}: {redact(str(exc))[:300]}"
