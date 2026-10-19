"""Normalized AndroidManifest model + manifest checks.

The same Element tree is produced by three parsers:
  * inspect_apk.parse_axml        (binary AXML inside APKs)
  * inspect_aab.parse_proto_xml   (aapt2 proto XmlNode inside AABs)
  * parse_source_manifest         (plain XML in the project)
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from common import (ANDROID_NS, BLOCKER, INFO, NOT_VERIFIED, PASS, WARNING,
                    FindingFactory, read_text_limited)

# Resource IDs of framework attributes (public-final.xml, AOSP main, verified 2026-10-16).
# Used when a compiled manifest stores attribute names only as resource ids.
ATTR_IDS = {
    0x01010003: "name", 0x01010010: "exported", 0x0101000F: "debuggable",
    0x010104EC: "usesCleartextTraffic", 0x01010280: "allowBackup",
    0x01010527: "networkSecurityConfig", 0x01010599: "foregroundServiceType",
    0x010104EE: "autoVerify", 0x01010027: "scheme", 0x01010028: "host",
    0x0101002A: "path", 0x0101002B: "pathPrefix", 0x0101002C: "pathPattern",
    0x01010026: "mimeType", 0x010104EA: "extractNativeLibs", 0x01010006: "permission",
    0x01010271: "maxSdkVersion", 0x0101021B: "versionCode", 0x0101021C: "versionName",
    0x0101020C: "minSdkVersion", 0x01010270: "targetSdkVersion",
    0x01010572: "compileSdkVersion", 0x01010001: "label", 0x0101000E: "enabled",
    0x0101028E: "required", 0x01010002: "icon", 0x01010025: "resource", 0x01010024: "value",
    0x01010029: "port", 0x010104EB: "fullBackupContent", 0x0101063E: "dataExtractionRules",
    0x01010644: "usesPermissionFlags", 0x01010603: "requestLegacyExternalStorage",
    0x01010641: "isAccessibilityTool", 0x01010018: "authorities",
    0x0101001B: "grantUriPermissions", 0x01010272: "testOnly", 0x0101061E: "pathSuffix",
    0x01010620: "pathAdvancedPattern", 0x01010573: "compileSdkVersionCodename",
}

# foregroundServiceType flag values (attrs_manifest.xml, AOSP main, verified 2026-10-16).
FGS_FLAGS = {
    0x01: "dataSync", 0x02: "mediaPlayback", 0x04: "phoneCall", 0x08: "location",
    0x10: "connectedDevice", 0x20: "mediaProjection", 0x40: "camera", 0x80: "microphone",
    0x100: "health", 0x200: "remoteMessaging", 0x400: "systemExempted", 0x800: "shortService",
    0x1000: "fileManagement", 0x2000: "mediaProcessing", 0x40000000: "specialUse",
}
FGS_PERMISSION = {
    "dataSync": "FOREGROUND_SERVICE_DATA_SYNC", "mediaPlayback": "FOREGROUND_SERVICE_MEDIA_PLAYBACK",
    "phoneCall": "FOREGROUND_SERVICE_PHONE_CALL", "location": "FOREGROUND_SERVICE_LOCATION",
    "connectedDevice": "FOREGROUND_SERVICE_CONNECTED_DEVICE",
    "mediaProjection": "FOREGROUND_SERVICE_MEDIA_PROJECTION", "camera": "FOREGROUND_SERVICE_CAMERA",
    "microphone": "FOREGROUND_SERVICE_MICROPHONE", "health": "FOREGROUND_SERVICE_HEALTH",
    "remoteMessaging": "FOREGROUND_SERVICE_REMOTE_MESSAGING",
    "systemExempted": "FOREGROUND_SERVICE_SYSTEM_EXEMPTED",
    "specialUse": "FOREGROUND_SERVICE_SPECIAL_USE", "mediaProcessing": "FOREGROUND_SERVICE_MEDIA_PROCESSING",
}


@dataclass
class Element:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["Element"] = field(default_factory=list)

    def iter(self, tag: str | None = None):
        if tag is None or self.tag == tag:
            yield self
        for c in self.children:
            yield from c.iter(tag)

    def find_all(self, tag: str) -> list["Element"]:
        return [c for c in self.children if c.tag == tag]

    def get(self, name: str, default: str | None = None) -> str | None:
        return self.attrs.get(name, default)


def attr_key(ns_uri: str, name: str, res_id: int | None = None) -> str:
    if (not name or name.startswith("0x")) and res_id in ATTR_IDS:
        name = ATTR_IDS[res_id]
    if ns_uri == ANDROID_NS or (not ns_uri and res_id and (res_id >> 24) == 0x01 and res_id in ATTR_IDS):
        return f"android:{name}"
    return name


def parse_source_manifest(path: Path) -> Element:
    """Parse a plain-text AndroidManifest.xml from the project (no DTD/entities)."""
    text = read_text_limited(path)
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise ValueError("manifest com DOCTYPE/ENTITY recusado por segurança")
    root = ET.fromstring(text)

    def conv(node: ET.Element) -> Element:
        attrs = {}
        for k, v in node.attrib.items():
            m = re.match(r"\{(.*)\}(.*)", k)
            if m:
                uri, local = m.groups()
                attrs[f"android:{local}" if uri == ANDROID_NS else (f"tools:{local}" if "tools" in uri else local)] = v
            else:
                attrs[k] = v
        return Element(node.tag, attrs, [conv(c) for c in node if isinstance(c.tag, str)])

    return conv(root)


def _bool(v: str | None) -> bool | None:
    if v is None:
        return None
    v = str(v).strip().lower()
    if v in ("true", "1", "0xffffffff", "-1"):
        return True
    if v in ("false", "0", "0x0", "0x00000000"):
        return False
    return None


def _int(v: str | None) -> int | None:
    if v is None:
        return None
    s = str(v).strip()
    try:
        return int(s, 16) if s.lower().startswith("0x") else int(s)
    except ValueError:
        return None


def decode_fgs_types(v: str | None) -> list[str]:
    if not v:
        return []
    n = _int(v)
    if n is not None:
        return [name for bit, name in sorted(FGS_FLAGS.items()) if n & bit]
    return [p.strip() for p in str(v).split("|") if p.strip()]


COMPONENT_TAGS = ("activity", "activity-alias", "service", "receiver", "provider")


def extract_info(root: Element) -> dict:
    """Turn an Element tree into a JSON-serializable manifest summary."""
    info: dict = {
        "package": root.get("package"),
        "version_code": _int(root.get("android:versionCode")),
        "version_name": root.get("android:versionName"),
        "compile_sdk": _int(root.get("android:compileSdkVersion")) or _int(root.get("platformBuildVersionCode")),
        "min_sdk": None, "target_sdk": None,
        "permissions": [], "features": [], "components": [], "application": {}, "queries": [],
        "meta_data": [],
    }
    for us in root.find_all("uses-sdk"):
        info["min_sdk"] = _int(us.get("android:minSdkVersion"))
        info["target_sdk"] = _int(us.get("android:targetSdkVersion"))
    for tag in ("uses-permission", "uses-permission-sdk-23"):
        for p in root.find_all(tag):
            name = p.get("android:name")
            if name:
                info["permissions"].append({"name": name, "max_sdk": _int(p.get("android:maxSdkVersion")),
                                            "flags": p.get("android:usesPermissionFlags")})
    for f in root.find_all("uses-feature"):
        info["features"].append({"name": f.get("android:name"), "required": _bool(f.get("android:required"))})
    info["queries"] = [c.tag for q in root.find_all("queries") for c in q.children]
    apps = root.find_all("application")
    if apps:
        app = apps[0]
        a = app.attrs
        info["application"] = {
            "name": a.get("android:name"), "label": a.get("android:label"),
            "debuggable": _bool(a.get("android:debuggable")),
            "test_only": _bool(a.get("android:testOnly")),
            "uses_cleartext_traffic": _bool(a.get("android:usesCleartextTraffic")),
            "allow_backup": _bool(a.get("android:allowBackup")),
            "network_security_config": a.get("android:networkSecurityConfig"),
            "extract_native_libs": _bool(a.get("android:extractNativeLibs")),
            "data_extraction_rules": a.get("android:dataExtractionRules"),
            "request_legacy_external_storage": _bool(a.get("android:requestLegacyExternalStorage")),
        }
        for m in app.find_all("meta-data"):
            info["meta_data"].append({"name": m.get("android:name"), "value": m.get("android:value") or m.get("android:resource")})
        for comp in app.children:
            if comp.tag not in COMPONENT_TAGS:
                continue
            filters = []
            for f in comp.find_all("intent-filter"):
                filters.append({
                    "actions": [x.get("android:name") for x in f.find_all("action")],
                    "categories": [x.get("android:name") for x in f.find_all("category")],
                    "data": [{k.split(":", 1)[1]: v for k, v in d.attrs.items() if k.startswith("android:")}
                             for d in f.find_all("data")],
                    "auto_verify": _bool(f.get("android:autoVerify")),
                })
            info["components"].append({
                "type": comp.tag, "name": comp.get("android:name"),
                "exported": _bool(comp.get("android:exported")),
                "permission": comp.get("android:permission"),
                "fgs_types": decode_fgs_types(comp.get("android:foregroundServiceType")),
                "intent_filters": filters,
                "meta_data": [m.get("android:name") for m in comp.find_all("meta-data")],
            })
    return info


PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")


def deep_links(info: dict) -> list[dict]:
    out = []
    for c in info.get("components", []):
        for f in c["intent_filters"]:
            if "android.intent.action.VIEW" not in f["actions"]:
                continue
            for d in f["data"]:
                if d.get("scheme"):
                    out.append({"component": c["name"], "scheme": d.get("scheme"), "host": d.get("host"),
                                "auto_verify": bool(f["auto_verify"]),
                                "browsable": "android.intent.category.BROWSABLE" in f["categories"]})
    return out


def check_manifest(info: dict, F: FindingFactory, *, source: str, artifact: bool) -> list:
    """Checks over a manifest summary. `artifact` = compiled release artifact (merged manifest)."""
    fs = []
    comp = f"{source}:AndroidManifest.xml"
    pkg = info.get("package")
    if pkg:
        if PACKAGE_RE.match(pkg):
            fs.append(F("TECH-PACKAGE-NAME", PASS, "android_build", "Application ID válido",
                        component=comp, evidence=f"package={pkg}"))
        else:
            fs.append(F("TECH-PACKAGE-NAME", BLOCKER, "android_build", "Application ID inválido",
                        component=comp, evidence=f"package={pkg}",
                        recommendation="Use um applicationId no formato reverso (ex.: com.empresa.app)."))
    elif artifact:
        fs.append(F("TECH-PACKAGE-NAME", NOT_VERIFIED, "android_build", "Package não encontrado no manifest",
                    component=comp, evidence="atributo package ausente"))

    if artifact:
        vc = info.get("version_code")
        if vc is None:
            fs.append(F("TECH-VERSION-CODE", NOT_VERIFIED, "android_build", "versionCode não encontrado",
                        component=comp, evidence="android:versionCode ausente"))
        elif 0 < vc <= 2100000000:
            fs.append(F("TECH-VERSION-CODE", PASS, "android_build", "versionCode dentro do limite do Google Play",
                        component=comp, evidence=f"versionCode={vc} (limite 2100000000)"))
        else:
            fs.append(F("TECH-VERSION-CODE", BLOCKER, "android_build", "versionCode fora do intervalo aceito",
                        component=comp, evidence=f"versionCode={vc}",
                        recommendation="Defina versionCode inteiro positivo ≤ 2100000000 e maior que o último publicado."))
        fs.append(F("TECH-VERSION-CODE-INCREMENT", NOT_VERIFIED, "play_console",
                    "versionCode maior que o último publicado não verificável offline",
                    component=comp, evidence=f"versionCode={vc}", method="manual",
                    recommendation="Confira em Play Console > App bundle explorer que o versionCode é maior que o último enviado."))

        app = info.get("application", {})
        if app.get("debuggable") is True:
            fs.append(F("TECH-DEBUGGABLE", BLOCKER, "android_build", "Artefato marcado como debuggable",
                        component=comp, evidence="application android:debuggable=true",
                        recommendation="Gere o build release (flutter build appbundle --release / ./gradlew bundleRelease) sem debuggable=true."))
        else:
            fs.append(F("TECH-DEBUGGABLE", PASS, "android_build", "Artefato não é debuggable",
                        component=comp, evidence=f"android:debuggable={app.get('debuggable')!s} (ausente = false)"))
        if app.get("test_only") is True:
            fs.append(F("TECH-TEST-ONLY", WARNING, "android_build", "Artefato marcado como testOnly",
                        component=comp, evidence="application android:testOnly=true",
                        recommendation="Remova android:testOnly do build de release."))

    target = info.get("target_sdk")
    missing_exported = [c for c in info.get("components", [])
                        if c["intent_filters"] and c["exported"] is None and c["type"] != "provider"]
    if target is not None and target >= 31:
        if missing_exported:
            fs.append(F("TECH-EXPORTED-EXPLICIT", BLOCKER if artifact else WARNING, "android_build",
                        "Componentes com intent-filter sem android:exported explícito",
                        component=comp, evidence=", ".join(c["name"] or "?" for c in missing_exported[:10]),
                        confidence="high" if artifact else "medium",
                        recommendation="Declare android:exported=\"true|false\" em cada componente com intent-filter."))
        else:
            fs.append(F("TECH-EXPORTED-EXPLICIT", PASS, "android_build",
                        "Todos os componentes com intent-filter declaram android:exported",
                        component=comp, evidence=f"{sum(1 for c in info.get('components', []) if c['intent_filters'])} componentes com intent-filter"))
    exported = [c for c in info.get("components", []) if c["exported"] and not c["permission"]
                and not any("android.intent.category.LAUNCHER" in f["categories"] for f in c["intent_filters"])]
    if exported:
        fs.append(F("SEC-EXPORTED-COMPONENTS", INFO, "security",
                    f"{len(exported)} componente(s) exportado(s) sem permissão de proteção",
                    component=comp, method="heuristic", confidence="medium",
                    evidence="; ".join(f"{c['type']}:{c['name']}" for c in exported[:12]) + (" …" if len(exported) > 12 else ""),
                    recommendation="Revise se cada componente precisa ser exportado; use android:exported=\"false\" ou android:permission."))

    app = info.get("application", {})
    if app:
        clr = app.get("uses_cleartext_traffic")
        if clr is True:
            fs.append(F("SEC-CLEARTEXT", WARNING, "security", "Tráfego HTTP em texto claro habilitado",
                        component=comp, evidence="android:usesCleartextTraffic=true", method="deterministic",
                        recommendation="Defina usesCleartextTraffic=false ou restrinja domínios via networkSecurityConfig."))
        elif clr is False or (target or 0) >= 28:
            fs.append(F("SEC-CLEARTEXT", PASS, "security", "Cleartext desabilitado no manifest",
                        component=comp, evidence=f"usesCleartextTraffic={clr} targetSdk={target} (padrão false desde API 28)"))
        if app.get("network_security_config"):
            fs.append(F("SEC-NETWORK-SECURITY-CONFIG", NOT_VERIFIED if artifact else INFO, "security",
                        "networkSecurityConfig declarado; conteúdo precisa ser revisado",
                        component=comp, evidence=f"networkSecurityConfig={app['network_security_config']}", method="manual",
                        recommendation="Revise res/xml do network security config (cleartextTrafficPermitted, trust-anchors de usuário, debug-overrides)."))
        if app.get("allow_backup") in (True, None):
            fs.append(F("SEC-ALLOW-BACKUP", INFO, "security", "Backup automático habilitado",
                        component=comp, evidence=f"android:allowBackup={app.get('allow_backup')} (ausente = true)",
                        method="deterministic",
                        recommendation="Avalie dataExtractionRules/fullBackupContent para excluir tokens e dados sensíveis do backup."))

    links = deep_links(info)
    verified_hosts = sorted({l["host"] for l in links if l["auto_verify"] and l["scheme"] in ("http", "https") and l["host"]})
    custom = sorted({l["scheme"] for l in links if l["scheme"] not in ("http", "https")})
    if verified_hosts:
        fs.append(F("SEC-APP-LINKS", NOT_VERIFIED, "security", "App Links com autoVerify exigem assetlinks.json válido",
                    component=comp, evidence="hosts: " + ", ".join(verified_hosts), method="manual",
                    recommendation="Verifique https://<host>/.well-known/assetlinks.json com o SHA-256 da chave de assinatura do Play (App signing key)."))
    if custom:
        fs.append(F("SEC-CUSTOM-SCHEME", INFO, "security", "Deep links com esquema customizado",
                    component=comp, evidence="schemes: " + ", ".join(custom), method="deterministic",
                    recommendation="Valide parâmetros recebidos por deep links; esquemas customizados podem ser registrados por outros apps."))
    return fs


def check_source_network_config(project_android: Path, F: FindingFactory) -> list:
    """Look at res/xml/*network_security_config*.xml in the project (read-only)."""
    fs = []
    for p in sorted(project_android.glob("app/src/*/res/xml/*.xml")):
        if "network" not in p.name:
            continue
        try:
            text = read_text_limited(p, 256 * 1024)
        except OSError:
            continue
        rel = str(p.relative_to(project_android.parent))
        if re.search(r'cleartextTrafficPermitted\s*=\s*"true"', text):
            fs.append(F("SEC-CLEARTEXT", WARNING, "security", "Network security config permite cleartext",
                        component=rel, evidence='cleartextTrafficPermitted="true"', method="deterministic",
                        recommendation="Restrinja cleartext a domínios de desenvolvimento ou remova."))
        if re.search(r'<certificates\s+src\s*=\s*"user"', text) and "debug-overrides" not in text:
            fs.append(F("SEC-NETWORK-SECURITY-CONFIG", WARNING, "security", "Confia em CAs do usuário fora de debug-overrides",
                        component=rel, evidence='<certificates src="user"/>', method="deterministic",
                        recommendation="Mova trust-anchors de usuário para <debug-overrides>."))
    return fs
