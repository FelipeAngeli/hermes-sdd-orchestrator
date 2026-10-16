"""Read-only project inspection (Flutter / Kotlin / Java / native Android).

Never writes to the project, never runs Gradle/Flutter, never reads the
values of key.properties / keystores / local.properties (only presence).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from common import (BLOCKER, INFO, NOT_VERIFIED, PASS, WARNING, FindingFactory, is_sensitive_file,
                    read_text_limited, redact)

SKIP_DIRS = {"build", ".dart_tool", ".gradle", ".git", "node_modules", "Pods", ".idea", ".cxx", "intermediates"}
TEST_DIRS = {"test", "tests", "integration_test", "androidTest", "testFixtures"}


def find_android_dir(project: Path) -> Path | None:
    for cand in (project / "android", project):
        if (cand / "app").is_dir() and any((cand / "app" / n).is_file() for n in ("build.gradle", "build.gradle.kts")):
            return cand
    return None


def detect_project(project: Path) -> dict:
    android = find_android_dir(project)
    is_flutter = (project / "pubspec.yaml").is_file() and android is not None and (project / "android").is_dir()
    return {"root": str(project), "android_dir": str(android) if android else None,
            "type": "flutter" if is_flutter else ("android" if android else "unknown")}


# ---------------------------------------------------------------- gradle
def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)//.*$", "", text)


def _block(text: str, name: str) -> str | None:
    m = re.search(r"(?<![A-Za-z_])" + re.escape(name) + r"\s*\{", text)
    if not m:
        return None
    depth, i = 1, m.end()
    while i < len(text) and depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[m.end():i - 1]


def _named_block(text: str, name: str) -> str | None:
    """Matches `release {`, `getByName("release") {`, `named("release") {`, `create("release") {`."""
    m = re.search(r"(?:(?<![A-Za-z_])" + name + r"|(?:getByName|named|create|maybeCreate)\(\s*\"" + name + r"\"\s*\))\s*\{", text)
    if not m:
        return None
    depth, i = 1, m.end()
    while i < len(text) and depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[m.end():i - 1]


def _val(block: str | None, *keys: str) -> str | None:
    if not block:
        return None
    for k in keys:
        m = re.search(r"(?m)^\s*" + re.escape(k) + r"\s*(?:=|\s)\s*\(?\s*([^\s)]+)", block)
        if m:
            return m.group(1).strip().strip("\"'")
    return None


def _sdk_value(raw: str | None) -> tuple[int | None, str | None]:
    """Returns (int, raw_expression). Non-literal expressions return (None, expr)."""
    if raw is None:
        return None, None
    m = re.match(r"^(\d+)$", raw)
    if m:
        return int(m.group(1)), raw
    m = re.match(r"^(?:android\.)?(\d+)$", raw)
    return (int(m.group(1)), raw) if m else (None, raw)


def parse_gradle(path: Path) -> dict:
    text = _strip_comments(read_text_limited(path))
    android = _block(text, "android") or ""
    default = _block(android, "defaultConfig") or ""
    build_types = _block(android, "buildTypes") or ""
    release = _named_block(build_types, "release")
    debug = _named_block(build_types, "debug")
    flavors = _block(android, "productFlavors")
    out: dict = {"file": str(path), "kts": path.suffix == ".kts"}
    out["namespace"] = _val(android, "namespace")
    out["application_id"] = _val(default, "applicationId")
    for key, names in (("compile_sdk", ("compileSdk", "compileSdkVersion")),
                       ("min_sdk", ("minSdk", "minSdkVersion")),
                       ("target_sdk", ("targetSdk", "targetSdkVersion"))):
        raw = _val(android if key == "compile_sdk" else default, *names)
        out[key], out[key + "_expr"] = _sdk_value(raw)
    out["ndk_version"] = _val(android, "ndkVersion")
    out["release_present"] = release is not None
    rel = release or ""
    out["release_debuggable"] = (_val(rel, "debuggable", "isDebuggable") or "").lower() == "true"
    out["release_minify"] = (_val(rel, "minifyEnabled", "isMinifyEnabled") or "").lower() == "true"
    out["release_shrink"] = (_val(rel, "shrinkResources", "isShrinkResources") or "").lower() == "true"
    sc = re.search(r"signingConfig\s*(?:=\s*)?signingConfigs\.(?:getByName\(\s*\")?(\w+)", rel)
    out["release_signing_config"] = sc.group(1) if sc else None
    out["product_flavors"] = re.findall(r"(?m)^\s*(?:create\(\s*\")?(\w+)\"?\)?\s*\{", flavors) if flavors else []
    out["uses_key_properties"] = "key.properties" in text
    out["debug_block_present"] = debug is not None
    # Hard-coded signing passwords: report presence only, never value.
    out["inline_passwords"] = bool(re.search(r"(?im)^\s*(storePassword|keyPassword)\s*(?:=\s*)?\(?\s*[\"'][^\"']+[\"']", text))
    return out


def parse_settings_plugins(android_dir: Path) -> dict:
    out = {}
    for name in ("settings.gradle", "settings.gradle.kts", "build.gradle", "build.gradle.kts"):
        p = android_dir / name
        if not p.is_file():
            continue
        t = read_text_limited(p)
        m = re.search(r"com\.android\.application[\"']?\)?\s*version\s*[\"']([^\"']+)", t) or \
            re.search(r"com\.android\.tools\.build:gradle:([0-9][^\"']*)", t)
        if m and "agp" not in out:
            out["agp"] = m.group(1)
        m = re.search(r"(?:org\.jetbrains\.kotlin\.android|kotlin\(\"android\"\))[\"']?\)?\s*version\s*[\"']([^\"']+)", t) or \
            re.search(r"kotlin-gradle-plugin:([0-9][^\"']*)", t)
        if m and "kotlin" not in out:
            out["kotlin"] = m.group(1)
    w = android_dir / "gradle/wrapper/gradle-wrapper.properties"
    if w.is_file():
        m = re.search(r"gradle-([0-9.]+)-(?:bin|all)\.zip", read_text_limited(w))
        if m:
            out["gradle_wrapper"] = m.group(1)
    return out


def _ver_tuple(v: str | None) -> tuple:
    if not v:
        return ()
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


# ---------------------------------------------------------------- pubspec
def parse_pubspec(project: Path) -> dict:
    out: dict = {"present": False}
    p = project / "pubspec.yaml"
    if not p.is_file():
        return out
    t = read_text_limited(p)
    out["present"] = True
    m = re.search(r"(?m)^name:\s*(\S+)", t)
    out["name"] = m.group(1) if m else None
    m = re.search(r"(?m)^version:\s*([^\s#]+)", t)
    out["version"] = m.group(1) if m else None
    m = re.search(r"(?ms)^environment:\s*\n((?:[ \t]+.*\n?)+)", t)
    if m:
        sdk = re.search(r"sdk:\s*['\"]?([^'\"\n]+)", m.group(1))
        fl = re.search(r"flutter:\s*['\"]?([^'\"\n]+)", m.group(1))
        out["dart_constraint"] = sdk.group(1).strip() if sdk else None
        out["flutter_constraint"] = fl.group(1).strip() if fl else None
    deps = {}
    for section in ("dependencies", "dev_dependencies"):
        m = re.search(r"(?ms)^" + section + r":\s*\n((?:(?:[ \t]+.*)?\n)+?)(?=^\S|\Z)", t)
        if m:
            for dm in re.finditer(r"(?m)^  ([A-Za-z0-9_]+):\s*(.*)$", m.group(1)):
                deps[dm.group(1)] = {"constraint": dm.group(2).strip() or None, "dev": section == "dev_dependencies"}
    out["dependencies"] = deps
    lock = project / "pubspec.lock"
    out["lock_present"] = lock.is_file()
    if lock.is_file():
        lt = read_text_limited(lock, 8 * 1024 ** 2)
        locked = {}
        for m in re.finditer(r"(?m)^  ([A-Za-z0-9_]+):\n((?:    .*\n)+)", lt):
            vm = re.search(r'version:\s*"([^"]+)"', m.group(2))
            dm = re.search(r'dependency:\s*"([^"]+)"', m.group(2))
            locked[m.group(1)] = {"version": vm.group(1) if vm else None, "dependency": dm.group(1) if dm else None}
        out["locked"] = locked
        sm = re.search(r"(?ms)^sdks:\n((?:  .*\n?)+)", lt)
        if sm:
            d = re.search(r'dart:\s*"([^"]+)"', sm.group(1))
            f = re.search(r'flutter:\s*"([^"]+)"', sm.group(1))
            out["lock_sdks"] = {"dart": d.group(1) if d else None, "flutter": f.group(1) if f else None}
    return out


def flutter_sdk_defaults(android_dir: Path) -> dict:
    """Resolve flutter.targetSdkVersion etc. from the local Flutter SDK (read-only).

    Reads only the `flutter.sdk` key of local.properties (a path, not a secret)."""
    lp = android_dir / "local.properties"
    if not lp.is_file():
        return {}
    sdk = None
    for line in read_text_limited(lp, 64 * 1024).splitlines():
        if line.startswith("flutter.sdk="):
            sdk = Path(line.split("=", 1)[1].strip().replace("\\:", ":").replace("\\\\", "\\"))
    if not sdk or not sdk.is_dir():
        return {}
    out: dict = {"flutter_sdk_found": True}
    ext = sdk / "packages/flutter_tools/gradle/src/main/kotlin/FlutterExtension.kt"
    if ext.is_file():
        t = read_text_limited(ext)
        for key, name in (("compile_sdk", "compileSdkVersion"), ("min_sdk", "minSdkVersion"), ("target_sdk", "targetSdkVersion")):
            m = re.search(r"val\s+" + name + r"\s*:\s*Int\s*=\s*(\d+)", t)
            if m:
                out[key] = int(m.group(1))
        m = re.search(r"val\s+ndkVersion\s*:\s*String\s*=\s*\"([^\"]+)\"", t)
        if m:
            out["ndk_version"] = m.group(1)
        out["source"] = "FlutterExtension.kt do Flutter SDK local"
    vj = sdk / "bin/cache/flutter.version.json"
    if vj.is_file():
        t = read_text_limited(vj, 64 * 1024)
        m = re.search(r'"frameworkVersion"\s*:\s*"([^"]+)"', t)
        d = re.search(r'"dartSdkVersion"\s*:\s*"([^"]+)"', t)
        out["flutter_version"] = m.group(1) if m else None
        out["dart_version"] = d.group(1) if d else None
    return out


# ---------------------------------------------------------------- git
def git_status_for(paths: list[Path], cwd: Path) -> dict[str, dict]:
    """tracked/ignored status via git (read-only commands)."""
    out: dict[str, dict] = {}
    try:
        top = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return out
    if top.returncode != 0:
        return out
    for p in paths:
        rel = str(p.relative_to(cwd))
        st = {"in_repo": True}
        try:
            t = subprocess.run(["git", "-C", str(cwd), "ls-files", "--error-unmatch", rel], capture_output=True, timeout=20)
            st["tracked"] = t.returncode == 0
            i = subprocess.run(["git", "-C", str(cwd), "check-ignore", "-q", rel], capture_output=True, timeout=20)
            st["ignored"] = i.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            st = {}
        out[rel] = st
    return out


# ---------------------------------------------------------------- source scan
SECRET_RULES = [
    ("Google API key", re.compile(r"AIza[0-9A-Za-z\-_]{35}")),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Stripe live secret", re.compile(r"sk_live_[0-9A-Za-z]{16,}")),
    ("Private key PEM", re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----")),
    ("Slack token", re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}")),
    ("GitHub token", re.compile(r"ghp_[0-9A-Za-z]{36}")),
]
HTTP_RE = re.compile(r"[\"']http://(?!localhost|127\.0\.0\.1|10\.0\.2\.2|schemas\.android\.com|www\.w3\.org|ns\.adobe\.com|xmlpull\.org|apache\.org)[^\"'\s]+")
CODE_EXT = {".dart", ".kt", ".java", ".xml", ".json", ".yaml", ".yml", ".gradle", ".kts", ".properties", ".plist"}


def iter_source_files(project: Path, max_files: int = 20000):
    count = 0
    stack = [project]
    while stack:
        d = stack.pop()
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        for e in entries:
            if e.is_symlink():
                continue
            if e.is_dir():
                if e.name not in SKIP_DIRS and not e.name.startswith("."):
                    stack.append(e)
            elif e.suffix in CODE_EXT and not is_sensitive_file(e):
                count += 1
                if count > max_files:
                    return
                yield e


def scan_sources(project: Path, cache=None) -> dict:
    """Signals for privacy/billing/security heuristics. Results keyed by file hash (incremental)."""
    signals: dict = {"secrets": [], "http_urls": [], "webview_js": [], "account_creation": [], "account_deletion": [],
                     "purchase_flows": [], "external_payment": [], "files_scanned": 0}
    pats = {
        "webview_js": re.compile(r"addJavascriptInterface|setJavaScriptEnabled\(\s*true|javascriptMode:\s*JavascriptMode\.unrestricted|JavaScriptMode\.unrestricted"),
        "account_creation": re.compile(r"(?i)(createUserWithEmailAndPassword|sign[_ ]?up|signup|register(?:User|Account)|criar\s+conta|cadastr(?:o|ar))"),
        "account_deletion": re.compile(r"(?i)(delete[_ ]?account|deleteUser|excluir\s+(?:minha\s+)?conta|apagar\s+conta|account[_/-]deletion|remover\s+conta)"),
        "purchase_flows": re.compile(r"(?i)(buyNonConsumable|buyConsumable|launchBillingFlow|InAppPurchase\.instance|purchaseStream)"),
        "external_payment": re.compile(r"(?i)(stripe|paypal|mercadopago|pagseguro|checkout\.|pix)"),
    }
    for f in iter_source_files(project):
        rel = str(f.relative_to(project))
        key = None
        if cache is not None:
            key = "src-" + cache.file_hash(f)[:40]
            hit = cache.get(key)
            if hit is not None:
                for k, v in hit.items():
                    signals[k].extend(v)
                signals["files_scanned"] += 1
                continue
        try:
            text = read_text_limited(f, 1024 * 1024)
        except OSError:
            continue
        local: dict = {k: [] for k in ("secrets", "http_urls", "webview_js", "account_creation", "account_deletion", "purchase_flows", "external_payment")}
        for label, rx in SECRET_RULES:
            for m in rx.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                local["secrets"].append({"file": rel, "line": line, "kind": label, "value": redact(m.group(0))})
        in_tests = bool(set(Path(rel).parts) & TEST_DIRS)
        if f.suffix in (".dart", ".kt", ".java") and not in_tests:
            for m in HTTP_RE.finditer(text):
                local["http_urls"].append({"file": rel, "line": text.count("\n", 0, m.start()) + 1, "url": redact(m.group(0).strip("\"'"))[:120]})
            for k, rx in pats.items():
                m = rx.search(text)
                if m:
                    local[k].append({"file": rel, "line": text.count("\n", 0, m.start()) + 1, "match": m.group(0)[:60]})
        for k, v in local.items():
            signals[k].extend(v)
        signals["files_scanned"] += 1
        if cache is not None and key:
            cache.put(key, local)
    return signals


def inspect_project(project: Path, F: FindingFactory, cache=None) -> dict:
    det = detect_project(project)
    res: dict = {"detection": det, "findings": [], "gradle": None, "plugins": {}, "pubspec": {}, "flutter_defaults": {},
                 "manifest_path": None}
    fs = res["findings"]
    if not det["android_dir"]:
        fs.append(F("TECH-MERGED-MANIFEST", NOT_VERIFIED, "android_build", "Projeto Android não encontrado",
                    component=str(project), evidence="sem android/app/build.gradle(.kts) nem app/build.gradle(.kts)", method="manual"))
        return res
    android = Path(det["android_dir"])
    g = android / "app/build.gradle.kts"
    if not g.is_file():
        g = android / "app/build.gradle"
    gradle = parse_gradle(g)
    res["gradle"] = gradle
    res["plugins"] = parse_settings_plugins(android)
    if det["type"] == "flutter":
        res["pubspec"] = parse_pubspec(project)
        res["flutter_defaults"] = flutter_sdk_defaults(android)
    rel_g = str(g.relative_to(project))
    # Resolve SDK values that use `flutter.*` expressions
    for key in ("target_sdk", "min_sdk", "compile_sdk"):
        if gradle[key] is None and gradle.get(key + "_expr", "") and str(gradle[key + "_expr"]).startswith("flutter.") and key in res["flutter_defaults"]:
            gradle[key] = res["flutter_defaults"][key]
            gradle[key + "_resolved_from"] = res["flutter_defaults"]["source"]
    if gradle["release_debuggable"]:
        fs.append(F("TECH-DEBUGGABLE", BLOCKER, "android_build", "buildTypes.release com debuggable=true",
                    component=rel_g, evidence="release { debuggable true }",
                    recommendation="Remova `debuggable true` / `isDebuggable = true` do buildType release."))
    elif gradle["release_present"]:
        fs.append(F("TECH-DEBUGGABLE", PASS, "android_build", "buildType release sem debuggable=true",
                    component=rel_g, evidence="debuggable ausente/false em release"))
    if gradle["release_present"]:
        fs.append(F("TECH-R8-MINIFY", PASS if gradle["release_minify"] else INFO, "android_build",
                    "R8/minify habilitado no release" if gradle["release_minify"] else "R8/minify desabilitado no release",
                    component=rel_g, evidence=f"minifyEnabled={gradle['release_minify']} shrinkResources={gradle['release_shrink']}",
                    recommendation="-" if gradle["release_minify"] else "Habilite minifyEnabled/isMinifyEnabled e envie o mapping.txt ao Play Console."))
        if gradle["release_signing_config"] == "debug":
            fs.append(F("TECH-DEBUG-SIGNATURE", WARNING, "signing", "Release configurado para assinar com signingConfigs.debug",
                        component=rel_g, evidence="signingConfig signingConfigs.debug em release", confidence="medium",
                        recommendation="Configure signingConfigs.release com a chave de upload (via key.properties fora do Git)."))
    if gradle["inline_passwords"]:
        fs.append(F("SEC-HARDCODED-SECRETS", WARNING, "security", "Senhas de assinatura literais no build.gradle",
                    component=rel_g, evidence="storePassword/keyPassword com literal (valor omitido)",
                    recommendation="Mova as senhas para key.properties (ignorado pelo Git) ou variáveis de ambiente do CI."))
    agp = res["plugins"].get("agp")
    if agp:
        ok = _ver_tuple(agp) >= (8, 5, 1)
        fs.append(F("TECH-AGP-16KB", PASS if ok else WARNING, "page_size_16kb",
                    f"AGP {agp} {'≥' if ok else '<'} 8.5.1", component="android/settings.gradle", method="heuristic",
                    confidence="high", evidence=f"com.android.application {agp}",
                    recommendation="-" if ok else "Atualize o Android Gradle Plugin para ≥ 8.5.1 para alinhar .so a 16 KB."))
    ndk = gradle.get("ndk_version") or ""
    if ndk.startswith("flutter.") and res["flutter_defaults"].get("ndk_version"):
        ndk = res["flutter_defaults"]["ndk_version"]
    if re.match(r"^\d", ndk):
        major = _ver_tuple(ndk)[:1]
        fs.append(F("TECH-AGP-16KB", PASS if major and major[0] >= 28 else INFO, "page_size_16kb",
                    f"NDK {ndk}", component=rel_g, method="heuristic", confidence="medium",
                    evidence=f"ndkVersion={ndk} (r28+ compila 16 KB por padrão; libs pré-compiladas de terceiros não são afetadas)",
                    recommendation="-" if major and major[0] >= 28 else "Use NDK r28+ ou -Wl,-z,max-page-size=16384."))
    # source manifest
    mp = android / "app/src/main/AndroidManifest.xml"
    if mp.is_file():
        res["manifest_path"] = str(mp)
    fs.append(F("TECH-MERGED-MANIFEST", NOT_VERIFIED, "permissions",
                "Manifest do projeto não inclui permissões/componentes adicionados por plugins no merge",
                component=str(mp.relative_to(project)) if mp.is_file() else "android/app/src/main/AndroidManifest.xml",
                method="manual", evidence="auditoria de fonte; o manifest final só existe no artefato",
                recommendation="Rode também `guardian.py audit --aab build/app/outputs/bundle/release/app-release.aab` (ou gate --aab) para auditar o manifest mesclado."))
    # sensitive files: presence only
    sensitive = [p for p in android.rglob("*") if p.is_file() and (p.name == "key.properties" or p.suffix in (".jks", ".keystore", ".p12"))]
    sensitive = [p for p in sensitive if not (set(p.relative_to(project).parts) & SKIP_DIRS)]
    res["sensitive_files"] = [str(p.relative_to(project)) for p in sensitive]
    res["git_status"] = git_status_for(sensitive, project) if sensitive else {}
    return res
