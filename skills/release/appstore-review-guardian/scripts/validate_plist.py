"""Info.plist validation (bundle artifact or project source).

Isolated use:
  python3 validate_plist.py <Info.plist> [--source project|bundle]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    BLOCKER, CRITICAL, INFO, NOT_VERIFIED, PASS, WARNING, Collector, Finding, load_plist, ref, version_tuple,
)

VAR_RE = re.compile(r"\$\(([^)]+)\)|\$\{([^}]+)\}")
SHORT_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
BUILD_VERSION_RE = re.compile(r"^\d+(\.\d+){0,2}$")


def _is_var(v) -> bool:
    return isinstance(v, str) and bool(VAR_RE.search(v))


def validate_info_plist(pl: dict, source: str = "bundle", file: str = "Info.plist",
                        component: str = "app", resolved: dict | None = None) -> tuple[list[Finding], dict]:
    """Validate an Info.plist dict.

    source="bundle": compiled artifact (variables must be resolved).
    source="project": source file; build variables are resolved from `resolved` when possible.
    """
    c = Collector()
    resolved = resolved or {}
    facts: dict = {}

    def val(key):
        v = pl.get(key)
        if source == "project" and _is_var(v):
            return resolved.get(key, v)
        return v

    # ---- identifier
    bid = val("CFBundleIdentifier")
    facts["bundle_id"] = bid
    if not bid:
        c.add("PLIST-ID-001", BLOCKER, component, "CFBundleIdentifier ausente", file=file,
              recommendation="Defina PRODUCT_BUNDLE_IDENTIFIER no target e CFBundleIdentifier=$(PRODUCT_BUNDLE_IDENTIFIER).")
    elif _is_var(bid):
        sev = BLOCKER if source == "bundle" else NOT_VERIFIED
        c.add("PLIST-ID-001", sev, component, f"CFBundleIdentifier não resolvido: {bid}", file=file,
              confidence=0.95 if source == "bundle" else 0.5,
              recommendation="No artefato o valor deve estar resolvido; no projeto confira PRODUCT_BUNDLE_IDENTIFIER do Release.")
    else:
        c.add("PLIST-ID-001", PASS, component, f"CFBundleIdentifier={bid}", file=file, confidence=1.0)

    # ---- versions
    for key, rule, rx, sev in (("CFBundleShortVersionString", "PLIST-VER-001", SHORT_VERSION_RE, CRITICAL),
                               ("CFBundleVersion", "PLIST-VER-002", BUILD_VERSION_RE, BLOCKER)):
        v = val(key)
        facts[key] = v
        if v is None:
            c.add(rule, sev if source == "bundle" else NOT_VERIFIED, component, f"{key} ausente", file=file,
                  confidence=1.0 if source == "bundle" else 0.4,
                  recommendation=f"Defina {key} (Flutter: 'version: X.Y.Z+N' no pubspec.yaml).")
        elif _is_var(v):
            c.add(rule, BLOCKER if source == "bundle" else NOT_VERIFIED, component,
                  f"{key} usa variável de build não resolvida: {v}", file=file,
                  confidence=0.9 if source == "bundle" else 0.5,
                  recommendation="Valor resolvido no build; valide no .ipa ou no pubspec.yaml.")
        elif not rx.match(str(v)):
            c.add(rule, sev, component, f"{key}={v!r} fora do formato documentado", file=file, confidence=0.95,
                  recommendation=f"Use apenas inteiros separados por ponto em {key}.")
        else:
            c.add(rule, PASS, component, f"{key}={v}", file=file, confidence=1.0)

    # ---- minimum OS
    min_req = ref("upload_minimums")["min_ios_deployment_target"]
    if source == "bundle":
        mos = pl.get("MinimumOSVersion")
        facts["MinimumOSVersion"] = mos
        if mos is None:
            c.add("PLIST-MINOS-001", NOT_VERIFIED, component, "MinimumOSVersion ausente no bundle", file=file,
                  confidence=0.5, recommendation="Confira o Deployment Target no Xcode.")
        elif version_tuple(mos) < version_tuple(min_req):
            c.add("PLIST-MINOS-001", BLOCKER, component, f"MinimumOSVersion={mos} < {min_req}", file=file,
                  confidence=1.0, recommendation=f"Suba IPHONEOS_DEPLOYMENT_TARGET para >= {min_req} e recompile.")
        else:
            c.add("PLIST-MINOS-001", PASS, component, f"MinimumOSVersion={mos} (>= {min_req})", file=file, confidence=1.0)
    elif "MinimumOSVersion" in pl:
        c.add("PLIST-MINOS-002", WARNING, component, f"MinimumOSVersion definido no Info.plist de origem: {pl['MinimumOSVersion']}",
              file=file, confidence=0.9, auto_fixable=True,
              recommendation="Remova a chave e use o Deployment Target do target no Xcode.")

    # ---- SDK / Xcode (only compiled bundles have DT* keys)
    if source == "bundle":
        um = ref("upload_minimums")
        xcode, sdkname, plat = pl.get("DTXcode"), pl.get("DTSDKName"), pl.get("DTPlatformVersion")
        facts.update({"DTXcode": xcode, "DTSDKName": sdkname, "DTPlatformVersion": plat, "DTXcodeBuild": pl.get("DTXcodeBuild")})
        if not xcode or not (sdkname or plat):
            c.add("BUILD-SDK-001", NOT_VERIFIED, component, "DTXcode/DTSDKName ausentes no Info.plist do bundle", file=file,
                  confidence=0.5, recommendation="Gere o build com Xcode 26+ (flutter build ipa).")
        else:
            xmaj = int(str(xcode)[:2]) if str(xcode)[:2].isdigit() else 0
            m = re.search(r"(\d+)(?:\.\d+)?$", str(sdkname or ""))
            smaj = int(m.group(1)) if m else version_tuple(plat or "0")[0]
            ev = f"DTXcode={xcode} (Xcode {xmaj}), DTSDKName={sdkname}, DTPlatformVersion={plat}"
            if xmaj < um["min_xcode_major"] or smaj < um["min_sdk_major"]:
                c.add("BUILD-SDK-001", BLOCKER, component, ev, file=file, confidence=0.95,
                      recommendation=f"Recompile com Xcode {um['min_xcode_major']}+ e SDK iOS {um['min_sdk_major']}+.")
            else:
                c.add("BUILD-SDK-001", PASS, component, ev, file=file, confidence=0.95)

    # ---- export compliance
    if "ITSAppUsesNonExemptEncryption" not in pl:
        c.add("PLIST-ENC-001", WARNING, component, "ITSAppUsesNonExemptEncryption ausente", file=file, confidence=1.0,
              recommendation="Se o app só usa criptografia isenta (ex.: HTTPS do sistema), adicione "
                             "<key>ITSAppUsesNonExemptEncryption</key><false/>; caso contrário responda o questionário no ASC.")
    else:
        v = pl.get("ITSAppUsesNonExemptEncryption")
        if v is True and not pl.get("ITSEncryptionExportComplianceCode"):
            c.add("PLIST-ENC-001", INFO, component, "ITSAppUsesNonExemptEncryption=YES sem ITSEncryptionExportComplianceCode",
                  file=file, confidence=0.9, recommendation="Envie a documentação de export compliance no ASC e adicione o código recebido.")
        else:
            c.add("PLIST-ENC-001", PASS, component, f"ITSAppUsesNonExemptEncryption={v} declarado (exatidão não verificável)",
                  file=file, confidence=0.8)
    facts["ITSAppUsesNonExemptEncryption"] = pl.get("ITSAppUsesNonExemptEncryption")

    # ---- ATS
    ats = pl.get("NSAppTransportSecurity") or {}
    if isinstance(ats, dict) and ats.get("NSAllowsArbitraryLoads") is True:
        ignored = [k for k in ("NSAllowsArbitraryLoadsInWebContent", "NSAllowsArbitraryLoadsForMedia", "NSAllowsLocalNetworking") if k in ats]
        ev = "NSAllowsArbitraryLoads=YES" + (f" (ignorado em iOS 10+ porque há {', '.join(ignored)})" if ignored else "")
        c.add("PLIST-ATS-001", INFO if ignored else WARNING, component, ev, file=file, confidence=0.95,
              recommendation="Prefira NSExceptionDomains específicos; se mantiver, prepare a justificativa para o App Review.")
    else:
        c.add("PLIST-ATS-001", PASS, component, "NSAllowsArbitraryLoads ausente ou NO", file=file, confidence=1.0)

    # ---- background modes
    bg = pl.get("UIBackgroundModes")
    if isinstance(bg, list):
        valid = set(ref("background_modes"))
        bad = [m for m in bg if m not in valid]
        facts["UIBackgroundModes"] = bg
        if bad:
            c.add("PLIST-BG-001", WARNING, component, f"valores não documentados em UIBackgroundModes: {bad}", file=file,
                  confidence=0.9, recommendation="Remova valores inválidos; use cada modo apenas para seu propósito (2.5.4).")
        else:
            c.add("PLIST-BG-001", PASS, component, f"UIBackgroundModes={bg}", file=file, confidence=1.0)

    # ---- URL schemes
    schemes = []
    for t in pl.get("CFBundleURLTypes") or []:
        if isinstance(t, dict):
            schemes += [s for s in t.get("CFBundleURLSchemes") or [] if isinstance(s, str)]
    facts["url_schemes"] = schemes
    if schemes:
        c.add("PLIST-URL-001", INFO, component, f"URL schemes: {schemes}", file=file, confidence=1.0,
              recommendation="Confirme que cada scheme é tratado pelo app e não colide com schemes de terceiros.")

    # ---- usage descriptions
    known = set(ref("usage_description_keys"))
    patterns = [re.compile(p, re.I) for p in ref("generic_purpose_patterns")]
    usage = {}
    for key, value in pl.items():
        if not key.endswith("UsageDescription"):
            continue
        usage[key] = value
        if key not in known:
            c.add("PRIV-USAGE-004", WARNING, component, f"chave desconhecida: {key}", file=file, confidence=0.8,
                  recommendation="Confira a grafia na lista de recursos protegidos da Apple.")
        text = value if isinstance(value, str) else ""
        if not text.strip():
            c.add("PRIV-USAGE-001", CRITICAL, component, f"{key} vazio ou não é string", file=file, confidence=1.0,
                  recommendation=f"Escreva em {key} por que o app usa o recurso e como os dados são usados.")
        elif any(p.search(text.strip()) for p in patterns):
            c.add("PRIV-USAGE-002", WARNING, component, f"{key}={text!r} parece genérico", file=file, confidence=0.7,
                  recommendation=f"Explique o uso concreto, ex.: 'O Mently usa suas fotos para definir a foto de perfil.' em {key} (e localize em InfoPlist.strings).")
        else:
            c.add("PRIV-USAGE-001", PASS, component, f"{key} preenchido ({len(text)} caracteres)", file=file, confidence=0.9)
    facts["usage_descriptions"] = sorted(usage)
    return c.items, facts


def validate_file(path: Path, source: str = "bundle", component: str = "app", resolved: dict | None = None):
    pl, err = load_plist(path)
    if err or not isinstance(pl, dict):
        f = Finding("PLIST-PARSE-001", BLOCKER, component, f"Info.plist ilegível: {err or 'raiz não é dicionário'}",
                    file=str(path), confidence=1.0, recommendation="Corrija a sintaxe com 'plutil -lint Info.plist'.")
        return [f], {}
    findings, facts = validate_info_plist(pl, source, str(path), component, resolved)
    findings.insert(0, Finding("PLIST-PARSE-001", PASS, component, "Info.plist lido com plistlib", file=str(path), confidence=1.0))
    return findings, facts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("plist")
    ap.add_argument("--source", choices=["bundle", "project"], default="bundle")
    a = ap.parse_args(argv)
    findings, facts = validate_file(Path(a.plist), a.source)
    print(json.dumps({"facts": facts, "findings": [f.to_dict(i + 1) for i, f in enumerate(findings)]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
