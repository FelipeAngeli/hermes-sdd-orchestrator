"""Code signing and provisioning profile checks for an on-disk .app bundle.

Read-only. Uses stdlib parsing of the Mach-O signature and of the profile's
CMS payload; codesign/security (macOS) add cryptographic verification when
available. Missing tools => NOT_VERIFIED, never PASS.

Isolated use:
  python3 validate_signing.py <Runner.app>
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import plistlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import macho  # noqa: E402
from common import (  # noqa: E402
    BLOCKER, NOT_VERIFIED, PASS, WARNING, Collector, Finding, have_tool, run_tool,
)

DIST_PREFIXES = ("Apple Distribution", "iPhone Distribution")
DEV_PREFIXES = ("Apple Development", "iPhone Developer")


def decode_profile(path: Path) -> tuple[dict | None, str, str | None]:
    """Return (profile_dict, method, error). method in {'security', 'stdlib'}."""
    data = path.read_bytes()[: 2 * 1024 * 1024]
    if have_tool("security"):
        rc, out, err = run_tool(["security", "cms", "-D", "-i", str(path)], timeout=30)
        if rc == 0 and out:
            try:
                return plistlib.loads(out), "security", None
            except Exception as exc:  # noqa: BLE001
                return None, "security", f"plist inválida: {exc}"
    m = re.search(rb"<\?xml.*?</plist>", data, re.S)
    if not m:
        return None, "stdlib", "plist não encontrada no CMS"
    try:
        return plistlib.loads(m.group(0)), "stdlib", None
    except Exception as exc:  # noqa: BLE001
        return None, "stdlib", f"plist inválida: {exc}"


def profile_kind(p: dict) -> str:
    if p.get("ProvisionsAllDevices"):
        return "enterprise_or_developer_id"
    if p.get("ProvisionedDevices"):
        ents = p.get("Entitlements") or {}
        return "development" if ents.get("get-task-allow") else "ad_hoc"
    return "app_store"


def check_signing(app: Path, component: str = "app", exec_name: str | None = None,
                  bundle_id: str | None = None, now: dt.datetime | None = None) -> tuple[list[Finding], dict]:
    c = Collector()
    facts: dict = {"signed": False, "authority": [], "profile": None, "entitlements": None, "tools": {}}
    now = now or dt.datetime.now(dt.timezone.utc)
    exe = app / (exec_name or app.stem)
    cr = app / "_CodeSignature" / "CodeResources"
    has_sig = False
    sl = None
    if exe.is_file() and macho.is_macho(exe):
        try:
            mo = macho.parse(exe)
            sl = mo.preferred()
            has_sig = bool(sl and sl.code_signature)
        except macho.MachOError:
            sl = None
    facts["signed"] = has_sig and cr.is_file()
    rel = f"{app.name}/_CodeSignature/CodeResources"
    if not cr.is_file() or not has_sig:
        why = []
        if not cr.is_file():
            why.append("_CodeSignature/CodeResources ausente")
        if sl is not None and not has_sig:
            why.append("executável sem LC_CODE_SIGNATURE")
        c.add("SIGN-001", BLOCKER, component, "; ".join(why) or "assinatura ausente", file=rel, confidence=1.0,
              recommendation="Gere o build assinado para distribuição: 'flutter build ipa --release --export-options-plist=ios/ExportOptions.plist'. "
                             "Build '--no-codesign' só serve para pré-auditoria.")
        for rid in ("SIGN-002", "SIGN-003", "ENT-GTA-001"):
            c.add(rid, NOT_VERIFIED, component, "bundle não assinado: verificação impossível", file=rel, confidence=0.2)
        prof = app / "embedded.mobileprovision"
        if not prof.exists():
            c.add("SIGN-004", NOT_VERIFIED, component, "embedded.mobileprovision ausente (build não assinado)", file=rel, confidence=0.2)
        return c.items, facts
    c.add("SIGN-001", PASS, component, "_CodeSignature/CodeResources e LC_CODE_SIGNATURE presentes", file=rel, confidence=0.9)

    sig = macho.read_signature(exe, sl) if sl else {}
    ents = sig.get("entitlements")
    facts["entitlements"] = ents

    # SIGN-002 cryptographic verification
    if have_tool("codesign"):
        facts["tools"]["codesign"] = True
        rc, out, err = run_tool(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)], timeout=180)
        msg = (err or out).decode("utf-8", "replace").strip().splitlines()
        if rc == 0:
            c.add("SIGN-002", PASS, component, "codesign --verify --deep --strict: OK", file=app.name, confidence=1.0)
        else:
            c.add("SIGN-002", BLOCKER, component, f"codesign --verify falhou (rc={rc}): {' | '.join(msg[-3:])}", file=app.name,
                  confidence=0.95, recommendation="Reassine/exporte novamente pelo Xcode Organizer ou 'flutter build ipa'. Não modifique o bundle após assinar.")
        rc, out, err = run_tool(["codesign", "-dvv", str(app)], timeout=60)
        auth = re.findall(r"^Authority=(.+)$", (err + out).decode("utf-8", "replace"), re.M)
        facts["authority"] = auth
        auth_method = "codesign"
    else:
        facts["tools"]["codesign"] = False
        c.add("SIGN-002", NOT_VERIFIED, component, "codesign indisponível (não é macOS ou ferramenta ausente)", file=app.name,
              confidence=0.2, recommendation="Rode a auditoria em macOS com Xcode Command Line Tools.")
        auth = macho.certificate_names(sig.get("cms"))
        facts["authority"] = auth
        auth_method = "cms-strings"

    leaf = auth[0] if auth else ""
    if not auth:
        c.add("SIGN-003", NOT_VERIFIED, component, "autoridade do certificado não identificada", file=app.name, confidence=0.3)
    elif any(a.startswith(DIST_PREFIXES) for a in auth):
        c.add("SIGN-003", PASS, component, f"certificado de distribuição ({auth_method}): {leaf.split(':')[0]}", file=app.name,
              confidence=0.95 if auth_method == "codesign" else 0.7)
    elif any(a.startswith(DEV_PREFIXES) for a in auth):
        c.add("SIGN-003", BLOCKER, component, f"assinado com certificado de desenvolvimento ({leaf.split(':')[0]})", file=app.name,
              confidence=0.95 if auth_method == "codesign" else 0.75,
              recommendation="Exporte com 'Apple Distribution' e perfil App Store (ExportOptions method=app-store-connect).")
    else:
        c.add("SIGN-003", WARNING, component, f"autoridade inesperada: {leaf.split(':')[0]}", file=app.name, confidence=0.5)

    # SIGN-004 profile
    prof_path = app / "embedded.mobileprovision"
    prof_ents = None
    if not prof_path.is_file():
        c.add("SIGN-004", BLOCKER, component, "embedded.mobileprovision ausente em bundle assinado", file=f"{app.name}/embedded.mobileprovision",
              confidence=0.85, recommendation="Exporte com perfil de distribuição App Store.")
    else:
        prof, method, err = decode_profile(prof_path)
        if not prof:
            c.add("SIGN-004", NOT_VERIFIED, component, f"perfil ilegível ({method}): {err}", file=f"{app.name}/embedded.mobileprovision", confidence=0.3)
        else:
            kind = profile_kind(prof)
            exp = prof.get("ExpirationDate")
            if isinstance(exp, dt.datetime) and exp.tzinfo is None:
                exp = exp.replace(tzinfo=dt.timezone.utc)
            prof_ents = prof.get("Entitlements") or {}
            appid = str(prof_ents.get("application-identifier", ""))
            facts["profile"] = {"name": prof.get("Name"), "kind": kind, "team": (prof.get("TeamIdentifier") or [None])[0],
                                "expires": exp.isoformat() if isinstance(exp, dt.datetime) else None, "decoded_with": method,
                                "application_identifier": appid}
            problems = []
            if kind != "app_store":
                problems.append(f"perfil do tipo {kind} (App Store não tem ProvisionedDevices/ProvisionsAllDevices)")
            if isinstance(exp, dt.datetime) and exp < now:
                problems.append(f"perfil expirado em {exp.date()}")
            if bundle_id and appid and not (appid.endswith("." + bundle_id) or appid.endswith(".*")):
                problems.append(f"application-identifier {appid} não corresponde a {bundle_id}")
            ev = f"perfil '{prof.get('Name')}' tipo={kind}, expira={facts['profile']['expires']}, lido via {method}"
            if problems:
                c.add("SIGN-004", BLOCKER, component, ev + "; " + "; ".join(problems), file=f"{app.name}/embedded.mobileprovision",
                      confidence=0.9, recommendation="Gere um perfil 'App Store Connect' válido no portal Apple Developer e reexporte.")
            else:
                note = "" if method == "security" else " (assinatura CMS do perfil não verificada sem 'security')"
                c.add("SIGN-004", PASS, component, ev + note, file=f"{app.name}/embedded.mobileprovision",
                      confidence=0.9 if method == "security" else 0.75)
    facts["profile_entitlements"] = prof_ents
    if ents is None:
        c.add("ENT-GTA-001", NOT_VERIFIED, component, f"entitlements não extraídos da assinatura: {sig.get('entitlements_error')}",
              file=app.name, confidence=0.3)
    return c.items, facts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("app")
    a = ap.parse_args(argv)
    findings, facts = check_signing(Path(a.app))
    facts.pop("entitlements", None)
    facts.pop("profile_entitlements", None)
    print(json.dumps({"facts": facts, "findings": [f.to_dict(i + 1) for i, f in enumerate(findings)]}, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
