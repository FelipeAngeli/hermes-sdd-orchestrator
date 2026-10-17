"""Inspect a .ipa (untrusted ZIP) or an on-disk .app bundle. Read-only.

Isolated use:
  python3 inspect_ipa.py <file.ipa | Runner.app> [--full]
"""
from __future__ import annotations

import argparse
import json
import plistlib
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import macho  # noqa: E402
from common import (  # noqa: E402
    BLOCKER, CRITICAL, INFO, NOT_APPLICABLE, NOT_VERIFIED, PASS, WARNING, Collector, Finding, ReadBudget, UnsafeArchive,
    cleanup_dir, extract_members, have_tool, load_plist, make_private_tempdir, ref, stream_check, validate_zip,
)
from validate_entitlements import validate_entitlements  # noqa: E402
from validate_plist import validate_file as validate_plist_file  # noqa: E402
from validate_privacy import validate_manifest_file  # noqa: E402
from validate_signing import check_signing  # noqa: E402

SYSTEM_FRAMEWORK_KEYS = {
    "AppTrackingTransparency": "NSUserTrackingUsageDescription",
}


def _needles() -> dict[str, list[bytes]]:
    out = {}
    for cat, syms in ref("required_reason_symbols").items():
        if cat.startswith("_"):
            continue
        out[cat] = [s[1:].encode() if s.startswith("~") else b"\0" + s.encode() + b"\0" for s in syms]
    return out


def _exec_of(bundle: Path) -> Path:
    pl, _ = load_plist(bundle / "Info.plist")
    name = (pl or {}).get("CFBundleExecutable") if isinstance(pl, dict) else None
    return bundle / (name or bundle.stem)


def _sdk_names() -> dict[str, str]:
    return {s.lower(): s for s in ref("sdks_requiring_manifest")}


def inspect_app_dir(app: Path, component: str = "app", label: str | None = None, full: bool = True) -> tuple[list[Finding], dict]:
    """Run every bundle-level check on an extracted .app directory."""
    c = Collector()
    label = label or app.name
    facts: dict = {"app": label, "frameworks": [], "extensions": [], "privacy_manifests": [], "archs": [],
                   "debug_artifacts": [], "flutter_debug": False}
    info_path = app / "Info.plist"
    if not info_path.is_file():
        c.add("PLIST-PARSE-001", BLOCKER, component, f"{label}/Info.plist ausente", file=f"{label}/Info.plist", confidence=1.0,
              recommendation="Bundle incompleto; gere novamente com 'flutter build ipa --release'.")
        return c.items, facts
    pf, pfacts = validate_plist_file(info_path, "bundle", component)
    for f in pf:
        f.file = f"{label}/Info.plist"
    c.extend(pf)
    facts["info"] = pfacts
    info, _ = load_plist(info_path)
    info = info if isinstance(info, dict) else {}
    facts["name"] = info.get("CFBundleDisplayName") or info.get("CFBundleName")

    # ---- main executable architecture
    exe = _exec_of(app)
    main_slice = None
    if not exe.is_file():
        c.add("IPA-STRUCT-002", BLOCKER, component, f"executável {exe.name} ausente", file=f"{label}/{exe.name}", confidence=1.0)
    elif not macho.is_macho(exe):
        c.add("IPA-STRUCT-002", BLOCKER, component, f"{exe.name} não é Mach-O", file=f"{label}/{exe.name}", confidence=1.0)
    else:
        try:
            mo = macho.parse(exe)
            main_slice = mo.preferred()
            facts["archs"] = mo.archs()
            facts["main_platform"] = main_slice.platform if main_slice else None
            facts["linked_frameworks"] = sorted({d.split("/")[-1].replace(".framework", "") for s in mo.slices for d in s.dylibs
                                                 if "/System/Library/Frameworks/" in d})
            ev = f"arquiteturas={mo.archs()}, plataforma={main_slice.platform if main_slice else None}, minos={main_slice.minos if main_slice else None}, sdk={main_slice.sdk if main_slice else None}"
            if "arm64" in mo.archs() and main_slice and main_slice.platform == "ios":
                c.add("BIN-ARCH-001", PASS, component, ev, file=f"{label}/{exe.name}", confidence=1.0)
            else:
                c.add("BIN-ARCH-001", BLOCKER, component, ev, file=f"{label}/{exe.name}", confidence=0.95,
                      recommendation="Compile para dispositivo (iphoneos, arm64), não simulador: 'flutter build ipa --release'.")
            if "__DWARF" in (main_slice.segments if main_slice else []):
                facts["debug_artifacts"].append(f"{exe.name}: segmento __DWARF")
        except macho.MachOError as exc:
            c.add("BIN-ARCH-001", NOT_VERIFIED, component, f"Mach-O ilegível: {exc}", file=f"{label}/{exe.name}", confidence=0.3)

    # ---- frameworks
    sdk_names = _sdk_names()
    fw_dir = app / "Frameworks"
    bad_slices = []
    binaries = [exe] if exe.is_file() else []
    for fw in sorted(fw_dir.glob("*.framework")) if fw_dir.is_dir() else []:
        fexe = _exec_of(fw)
        entry = {"name": fw.stem, "has_privacy_manifest": (fw / "PrivacyInfo.xcprivacy").is_file()}
        if fexe.is_file() and macho.is_macho(fexe):
            binaries.append(fexe)
            try:
                fmo = macho.parse(fexe)
                entry["archs"] = fmo.archs()
                sims = [s.arch for s in fmo.slices if s.arch in ("x86_64", "i386") or (s.platform or "").endswith("simulator")]
                if sims:
                    bad_slices.append(f"{fw.name}: {sims}")
                if any("__DWARF" in s.segments for s in fmo.slices):
                    facts["debug_artifacts"].append(f"{fw.name}: segmento __DWARF")
            except macho.MachOError:
                entry["archs"] = None
        facts["frameworks"].append(entry)
    for dylib in sorted(fw_dir.glob("*.dylib")) if fw_dir.is_dir() else []:
        facts["frameworks"].append({"name": dylib.name, "has_privacy_manifest": None})
    if facts["frameworks"]:
        if bad_slices:
            c.add("BIN-ARCH-002", WARNING, component, f"fatias de simulador/Intel: {bad_slices}", file=f"{label}/Frameworks", confidence=0.95,
                  recommendation="Use XCFrameworks e deixe o Xcode selecionar a fatia de dispositivo.")
        else:
            c.add("BIN-ARCH-002", PASS, component, f"{len(facts['frameworks'])} framework(s) sem fatias de simulador", file=f"{label}/Frameworks", confidence=0.9)

    # ---- debug artifacts
    for d in app.rglob("*.dSYM"):
        facts["debug_artifacts"].append(str(d.relative_to(app)))
    if facts["debug_artifacts"]:
        c.add("BIN-DEBUG-001", WARNING, component, f"artefatos de depuração: {facts['debug_artifacts'][:5]}", file=label, confidence=0.9,
              recommendation="Remova .dSYM do bundle; mantenha 'uploadSymbols' no ExportOptions.plist.")
    else:
        c.add("BIN-DEBUG-001", PASS, component, "sem .dSYM ou segmento __DWARF no bundle", file=label, confidence=0.85)

    # ---- flutter build mode
    appfw = fw_dir / "App.framework"
    if appfw.is_dir():
        kb = appfw / "flutter_assets" / "kernel_blob.bin"
        if kb.exists():
            facts["flutter_debug"] = True
            c.add("BIN-FLUTTER-001", CRITICAL, component, "App.framework/flutter_assets/kernel_blob.bin presente (build debug/JIT)",
                  file=f"{label}/Frameworks/App.framework", confidence=0.95,
                  recommendation="Gere em release: 'flutter build ipa --release'.")
        else:
            c.add("BIN-FLUTTER-001", PASS, component, "App.framework sem kernel_blob.bin (AOT release)", file=f"{label}/Frameworks/App.framework", confidence=0.9)

    # ---- extensions
    bid = info.get("CFBundleIdentifier") or ""
    for appex in sorted((app / "PlugIns").glob("*.appex")) if (app / "PlugIns").is_dir() else []:
        xpl, _ = load_plist(appex / "Info.plist")
        xpl = xpl if isinstance(xpl, dict) else {}
        xbid = xpl.get("CFBundleIdentifier", "")
        ext = {"name": appex.name, "bundle_id": xbid, "point": (xpl.get("NSExtension") or {}).get("NSExtensionPointIdentifier")}
        facts["extensions"].append(ext)
        probs = []
        if not xbid.startswith(bid + "."):
            probs.append(f"bundle id {xbid!r} não prefixado por {bid!r}")
        for k in ("CFBundleShortVersionString", "CFBundleVersion"):
            if xpl.get(k) != info.get(k):
                probs.append(f"{k} {xpl.get(k)!r} ≠ app {info.get(k)!r}")
        c.add("BIN-EXT-001", WARNING if probs else PASS, component, f"{appex.name}: " + ("; ".join(probs) or "id e versões coerentes"),
              file=f"{label}/PlugIns/{appex.name}", confidence=0.9,
              recommendation="Alinhe o bundle id e as versões da extensão com o app." if probs else "")
        xexe = _exec_of(appex)
        if xexe.is_file():
            binaries.append(xexe)

    # ---- privacy manifests
    manifests = sorted(p for p in app.rglob("PrivacyInfo.xcprivacy") if p.is_file())
    declared: set[str] = set()
    app_manifest = None
    tracking = False
    for m in manifests:
        relp = str(m.relative_to(app))
        comp = "app" if relp == "PrivacyInfo.xcprivacy" else relp.split("/")[-2]
        mf, mfacts = validate_manifest_file(m, comp)
        for f in mf:
            f.file = f"{label}/{relp}"
        c.extend(mf)
        cats_found: dict = mfacts.get("api_categories") or {}
        declared |= set(cats_found)
        facts["privacy_manifests"].append({"path": relp, "valid": mfacts.get("valid"), "categories": sorted(cats_found),
                                           "tracking": mfacts.get("tracking")})
        if comp == "app":
            app_manifest = mfacts
            tracking = bool(mfacts.get("tracking"))

    # ---- required reason API usage (heuristic, bounded streaming)
    used: dict[str, list[str]] = {}
    if full:
        needles = _needles()
        for b in binaries:
            try:
                for cat, hits in macho.scan_strings(b, needles).items():
                    used.setdefault(cat, [])
                    used[cat] += [f"{b.name}:{h.strip(chr(0))}" for h in hits][:3]
            except OSError:
                pass
    facts["required_reason_symbols"] = {k: v[:6] for k, v in used.items()}
    main_used = {k for k, v in used.items() if any(x.startswith(exe.name + ":") for x in v)}
    if app_manifest is None:
        if full and main_used:
            c.add("PRIV-MANIFEST-001", WARNING, component,
                  f"sem PrivacyInfo.xcprivacy na raiz do app; o executável principal referencia {sorted(main_used)}",
                  file=f"{label}/PrivacyInfo.xcprivacy", confidence=0.7,
                  recommendation="Adicione ios/Runner/PrivacyInfo.xcprivacy ao target Runner (File > New > App Privacy) declarando as categorias usadas.")
        elif full:
            c.add("PRIV-MANIFEST-001", INFO, component, "sem PrivacyInfo.xcprivacy na raiz do app; nenhum símbolo de Required Reason API no executável",
                  file=f"{label}/PrivacyInfo.xcprivacy", confidence=0.5,
                  recommendation="Considere adicionar o manifest do app para declarar dados coletados.")
        else:
            c.add("PRIV-MANIFEST-001", NOT_VERIFIED, component, "sem manifest do app; varredura de símbolos só no modo --full",
                  file=f"{label}/PrivacyInfo.xcprivacy", confidence=0.3)
    else:
        c.add("PRIV-MANIFEST-001", PASS, component, "PrivacyInfo.xcprivacy presente na raiz do app", file=f"{label}/PrivacyInfo.xcprivacy", confidence=1.0)
    if full:
        undeclared = sorted(set(used) - declared)
        if undeclared:
            c.add("PRIV-RRAPI-002", WARNING, component,
                  f"categorias referenciadas sem declaração em nenhum manifest: {undeclared}; exemplos: {[used[k][0] for k in undeclared]}",
                  file=label, confidence=0.55,
                  recommendation="Confirme o uso e declare a categoria com o motivo correto no PrivacyInfo.xcprivacy do componente responsável.")
        elif used:
            c.add("PRIV-RRAPI-002", NOT_VERIFIED, component, f"categorias referenciadas {sorted(used)} aparecem declaradas em algum manifest "
                  "(heurística por símbolos: confirme que o componente que usa a API declara o motivo correto)", file=label, confidence=0.6,
                  recommendation="Confira o PrivacyInfo.xcprivacy de cada componente que usa as APIs; a Apple valida por componente no upload.")
        else:
            c.add("PRIV-RRAPI-002", NOT_VERIFIED, component, "nenhum símbolo de Required Reason API encontrado nos binários "
                  "(ausência heurística: chamadas dinâmicas não são detectadas)", file=label, confidence=0.4,
                  recommendation="Confirme no e-mail de processamento do App Store Connect (ITMS-91053) após o upload.")

    # ---- SDKs listed by Apple, embedded as frameworks
    for fw in facts["frameworks"]:
        key = fw["name"].lower()
        if key in sdk_names:
            bundle_manifest = any((app / f"{fw['name']}{suf}" / "PrivacyInfo.xcprivacy").is_file() for suf in ("_Privacy.bundle", "_privacy.bundle", ".bundle"))
            ok = fw["has_privacy_manifest"] or bundle_manifest
            c.add("PRIV-SDK-001", PASS if ok else WARNING, fw["name"],
                  f"{fw['name']} (lista Apple) {'com' if ok else 'sem'} PrivacyInfo.xcprivacy no bundle",
                  file=f"{label}/Frameworks/{fw['name']}.framework",
                  confidence=0.9 if ok else 0.75,
                  recommendation="" if ok else f"Atualize {fw['name']} para uma versão que inclua privacy manifest (obrigatório em app novo ou quando a atualização adiciona o SDK).")

    # ---- ATT
    linked = set(facts.get("linked_frameworks", []))
    if tracking or "AppTrackingTransparency" in linked:
        has = bool(info.get("NSUserTrackingUsageDescription"))
        why = "NSPrivacyTracking=true" if tracking else "AppTrackingTransparency.framework vinculado"
        c.add("PRIV-ATT-001", PASS if has else CRITICAL, component, f"{why}; NSUserTrackingUsageDescription {'presente' if has else 'ausente'}",
              file=f"{label}/Info.plist", confidence=0.9,
              recommendation="" if has else "Adicione NSUserTrackingUsageDescription e peça permissão via ATTrackingManager antes de rastrear.")
    elif full:
        c.add("PRIV-ATT-001", NOT_APPLICABLE, component, "sem NSPrivacyTracking=true no manifest do app e sem AppTrackingTransparency vinculado",
              file=f"{label}/Info.plist", confidence=0.6)

    # ---- permissions vs linked system frameworks (heuristic)
    signals = ref("resource_signals")
    for key, sig in signals.items():
        if key.startswith("_"):
            continue
        hits = [f for f in sig.get("frameworks", []) if f in linked]
        if hits and not info.get(key):
            c.add("PRIV-USAGE-003", WARNING, component, f"framework(s) {hits} vinculados e {key} ausente", file=f"{label}/Info.plist",
                  confidence=0.45, recommendation=f"Se o app acessa o recurso, adicione {key}; vincular o framework não prova acesso.")

    # ---- signing & entitlements
    sf, sfacts = check_signing(app, component, exe.name, bid or None)
    c.extend(sf)
    facts["signing"] = {k: v for k, v in sfacts.items() if k not in ("entitlements", "profile_entitlements")}
    ents = sfacts.get("entitlements")
    if isinstance(ents, dict):
        ef, efacts = validate_entitlements(ents, "bundle", component, f"{label} (assinatura)", sfacts.get("profile_entitlements"), info)
        # avoid duplicating ENT-GTA NOT_VERIFIED placeholder from signing
        c.items = [f for f in c.items if not (f.rule_id == "ENT-GTA-001" and f.severity == NOT_VERIFIED)]
        c.extend(ef)
        facts["entitlements"] = efacts
    else:
        bg = info.get("UIBackgroundModes") or []
        if "remote-notification" in bg:
            c.add("REV-PUSH-001", NOT_VERIFIED, component, "remote-notification declarado; entitlements não legíveis (bundle não assinado)",
                  file=f"{label}/Info.plist", confidence=0.3)
    return c.items, facts


def _select_members(names: list[str], app_prefix: str, full: bool) -> list[str]:
    if full:
        return [n for n in names if n.startswith(app_prefix) and not n.endswith("/")]
    keep = []
    for n in names:
        if not n.startswith(app_prefix) or n.endswith("/"):
            continue
        base = n.rsplit("/", 1)[-1]
        if base in ("Info.plist", "PrivacyInfo.xcprivacy", "embedded.mobileprovision", "CodeResources", "kernel_blob.bin"):
            keep.append(n)
    return keep


def inspect_ipa(path: Path, full: bool = True) -> tuple[list[Finding], dict]:
    c = Collector()
    facts: dict = {"ipa": path.name, "size": path.stat().st_size if path.exists() else None}
    if not path.is_file():
        c.add("IPA-STRUCT-001", BLOCKER, "ipa", "arquivo não encontrado", file=path.name, confidence=1.0)
        return c.items, facts
    tmp = None
    try:
        try:
            zf = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError) as exc:
            c.add("IPA-STRUCT-001", BLOCKER, "ipa", f"ZIP inválido/corrompido: {exc}", file=path.name, confidence=1.0,
                  recommendation="Reexporte o .ipa; não edite o arquivo manualmente.")
            return c.items, facts
        with zf:
            try:
                infos = validate_zip(zf)
            except UnsafeArchive as exc:
                c.add("IPA-STRUCT-001", BLOCKER, "ipa", f"arquivo rejeitado por segurança: {exc}", file=path.name, confidence=1.0,
                      recommendation="Não processe este arquivo; reexporte a partir do Xcode.")
                return c.items, facts
            budget = ReadBudget()
            bad = stream_check(zf, infos, budget) if full else None
            if bad:
                c.add("IPA-STRUCT-001", BLOCKER, "ipa", f"CRC inválido na entrada {bad!r}", file=path.name, confidence=1.0)
                return c.items, facts
            c.add("IPA-STRUCT-001", PASS, "ipa", f"ZIP íntegro e seguro ({len(infos)} entradas{', CRC verificado' if full else ''})", file=path.name, confidence=1.0)
            names = [i.filename for i in infos]
            apps = sorted({n.split("/")[1] for n in names if n.startswith("Payload/") and n.count("/") >= 2 and n.split("/")[1].endswith(".app")})
            facts["payload_apps"] = apps
            if len(apps) != 1:
                c.add("IPA-STRUCT-002", BLOCKER, "ipa", f"esperado 1 Payload/*.app, encontrado {apps or 'nenhum'}", file=path.name, confidence=1.0,
                      recommendation="Gere o .ipa pelo Xcode/Flutter; a estrutura deve ser Payload/<App>.app.")
                return c.items, facts
            app_name = apps[0]
            prefix = f"Payload/{app_name}/"
            if f"{prefix}Info.plist" not in names:
                c.add("PLIST-PARSE-001", BLOCKER, "app", f"{app_name}/Info.plist ausente", file=f"Payload/{app_name}/Info.plist", confidence=1.0)
                return c.items, facts
            c.add("IPA-STRUCT-002", PASS, "ipa", f"Payload/{app_name}", file=path.name, confidence=1.0)
            facts["has_swift_support"] = any(n.startswith("SwiftSupport/") for n in names)
            facts["has_symbols_dir"] = any(n.startswith("Symbols/") for n in names)
            need_full = full or have_tool("codesign")
            members = _select_members(names, prefix, need_full)
            skipped: list[tuple[str, str]] = []
            if not need_full:
                # executables: main + frameworks (needed for arch/signature)
                tmp_meta = make_private_tempdir()
                try:
                    meta = extract_members(zf, [m for m in members if m.endswith("Info.plist")], tmp_meta, budget=budget)
                    skipped += meta.skipped
                    for arc_name, ip in sorted(meta.mapping.items()):
                        pl, _ = load_plist(ip)
                        if isinstance(pl, dict) and isinstance(pl.get("CFBundleExecutable"), str):
                            cand = f"{arc_name.rsplit('/', 1)[0]}/{pl['CFBundleExecutable']}"
                            if cand in names:
                                members.append(cand)
                finally:
                    cleanup_dir(tmp_meta)
            tmp = make_private_tempdir()
            res = extract_members(zf, sorted(set(members)), tmp, budget=budget)
            skipped += [s for s in res.skipped if s not in skipped]
            facts["extracted_members"] = len(res.mapping)
            if skipped:
                facts["skipped_members"] = [{"name": n[:200], "reason": r} for n, r in skipped[:20]]
                c.add("IPA-STRUCT-001", NOT_VERIFIED, "ipa",
                      f"{len(skipped)} entrada(s) não extraída(s) com segurança e não auditada(s): "
                      + "; ".join(f"{n[:80]!r}: {r}" for n, r in skipped[:3]),
                      file=path.name, confidence=0.9,
                      recommendation="Nomes conflitantes (maiúsculas/minúsculas, arquivo × diretório) ou longos demais; "
                                     "reexporte o .ipa pelo Xcode e confira o conteúdo do bundle.")
            findings, afacts = inspect_app_dir(tmp / "Payload" / app_name, "app", app_name, full=full)
            c.extend(findings)
            facts.update(afacts)
    except UnsafeArchive as exc:
        c.add("IPA-STRUCT-001", BLOCKER, "ipa", f"extração interrompida por segurança: {exc}", file=path.name, confidence=1.0)
    finally:
        cleanup_dir(tmp)
    return c.items, facts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target")
    ap.add_argument("--full", action="store_true")
    a = ap.parse_args(argv)
    p = Path(a.target)
    findings, facts = inspect_app_dir(p, full=a.full) if p.is_dir() else inspect_ipa(p, full=a.full)
    print(json.dumps({"facts": facts, "findings": [f.to_dict(i + 1) for i, f in enumerate(findings)]}, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = ["inspect_ipa", "inspect_app_dir", "plistlib"]
