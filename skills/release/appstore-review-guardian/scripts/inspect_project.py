"""Inspect an iOS / Flutter project source tree (read-only, never builds).

Isolated use:
  python3 inspect_project.py <project-dir> [--mode quick|full|policy]

<project-dir> may be the Flutter app root (pubspec.yaml + ios/) or a native
iOS directory containing *.xcodeproj.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    BLOCKER, CRITICAL, INFO, NOT_APPLICABLE, NOT_VERIFIED, PASS, WARNING, Collector, Finding, load_plist,
    read_text_safe, ref, rel, version_tuple,
)
from inspect_dependencies import check_dependencies, inventory  # noqa: E402
from validate_entitlements import validate_entitlements  # noqa: E402
from validate_plist import validate_info_plist  # noqa: E402
from validate_privacy import validate_manifest_file  # noqa: E402

SKIP_DIRS = {"build", ".dart_tool", "Pods", ".git", ".symlinks", "node_modules", "DerivedData", ".gradle", "android",
             "web", "linux", "windows", "macos", "test", "integration_test", ".fvm"}
SOURCE_EXT = {".dart", ".swift", ".m", ".mm", ".h"}
MAX_SOURCE_FILES = 6000
MAX_SOURCE_BYTES = 512 * 1024


# --------------------------------------------------------------------------- discovery
def locate(project: Path) -> dict:
    project = project.resolve()
    loc = {"root": project, "flutter": (project / "pubspec.yaml").is_file(), "ios": None, "xcodeproj": None}
    ios = project / "ios" if (project / "ios").is_dir() else project
    projs = sorted(ios.glob("*.xcodeproj"))
    loc["ios"] = ios if projs or (ios / "Runner").is_dir() else None
    loc["xcodeproj"] = next((p for p in projs if p.stem == "Runner"), projs[0] if projs else None)
    return loc


# --------------------------------------------------------------------------- pbxproj
def parse_build_configs(pbx_text: str) -> list[dict]:
    """Extract XCBuildConfiguration blocks: {name, settings}."""
    out = []
    for m in re.finditer(r"isa = XCBuildConfiguration;(?:(?!isa = ).)*?buildSettings = \{(.*?)\n\t\t\t\};\s*name = \"?([^;\"]+)\"?;", pbx_text, re.S):
        body, name = m.group(1), m.group(2)
        settings = {}
        for sm in re.finditer(r"^\s*\"?([A-Za-z0-9_\[\]=*\.]+)\"?\s*=\s*(.*?);\s*$", body, re.M):
            settings[sm.group(1)] = sm.group(2).strip().strip('"')
        out.append({"name": name, "settings": settings})
    return out


def app_target_configs(configs: list[dict]) -> dict[str, dict]:
    """Configurations that look like the app target (have PRODUCT_BUNDLE_IDENTIFIER, not tests/extensions)."""
    res: dict[str, dict] = {}
    for c in configs:
        s = c["settings"]
        bid = s.get("PRODUCT_BUNDLE_IDENTIFIER")
        if not bid or "Tests" in bid or s.get("TEST_HOST") or s.get("BUNDLE_LOADER"):
            continue
        if "INFOPLIST_FILE" in s and "Runner" not in s["INFOPLIST_FILE"] and c["name"] in res:
            continue
        res.setdefault(c["name"], s)
    return res


def project_level_configs(configs: list[dict]) -> dict[str, dict]:
    res: dict[str, dict] = {}
    for c in configs:
        s = c["settings"]
        if "PRODUCT_BUNDLE_IDENTIFIER" not in s and ("SDKROOT" in s or "IPHONEOS_DEPLOYMENT_TARGET" in s):
            res.setdefault(c["name"], s)
    return res


# --------------------------------------------------------------------------- source scanning
def iter_sources(root: Path):
    n = 0
    for p in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts[:-1]):
            continue
        if p.is_file() and p.suffix in SOURCE_EXT and not p.name.endswith((".g.dart", ".freezed.dart", ".gen.dart")):
            n += 1
            if n > MAX_SOURCE_FILES:
                return
            yield p


def scan_code(root: Path, patterns: dict[str, list[str]]) -> dict[str, list[str]]:
    """Return signal -> list of 'file:line: snippet' (max 5 each)."""
    compiled = {k: [re.compile(x) for x in v] for k, v in patterns.items()}
    hits: dict[str, list[str]] = {k: [] for k in patterns}
    for f in iter_sources(root):
        text = read_text_safe(f, MAX_SOURCE_BYTES)
        if not text:
            continue
        lines = None
        for k, regs in compiled.items():
            if len(hits[k]) >= 5:
                continue
            done = False
            for rgx in regs:
                for m in rgx.finditer(text):
                    if lines is None:
                        lines = text.splitlines()
                    ln = text.count("\n", 0, m.start()) + 1
                    line = lines[ln - 1].strip() if ln - 1 < len(lines) else ""
                    if line.startswith(("import ", "export ", "part ", "//", "#import", "///")):
                        continue
                    hits[k].append(f"{rel(f, root)}:{ln}: {line[:120]}")
                    done = True
                    break
                if done:
                    break
    return {k: v for k, v in hits.items() if v}


# --------------------------------------------------------------------------- main audit
def inspect_project(project: Path, mode: str = "full", network_fetch=None) -> tuple[list[Finding], dict]:
    c = Collector()
    loc = locate(project)
    root = loc["root"]
    facts: dict = {"project": root.name, "flutter": loc["flutter"], "ios_dir": rel(loc["ios"], root) if loc["ios"] else None}
    if not loc["ios"]:
        c.add("PLIST-PARSE-001", NOT_VERIFIED, "project", "diretório iOS não encontrado (sem ios/ nem *.xcodeproj)", file=str(root.name),
              confidence=0.2, recommendation="Informe o diretório do app Flutter (com ios/) ou o diretório com o .xcodeproj.")
        return c.items, facts
    ios = loc["ios"]

    # ---- build settings
    configs, app_cfg, proj_cfg = [], {}, {}
    if loc["xcodeproj"] and (loc["xcodeproj"] / "project.pbxproj").is_file():
        configs = parse_build_configs(read_text_safe(loc["xcodeproj"] / "project.pbxproj", 8 * 1024 * 1024))
        app_cfg = app_target_configs(configs)
        proj_cfg = project_level_configs(configs)
    release = app_cfg.get("Release", {})
    facts["release_settings"] = {k: release.get(k) for k in ("PRODUCT_BUNDLE_IDENTIFIER", "CODE_SIGN_ENTITLEMENTS", "CODE_SIGN_STYLE",
                                                             "CODE_SIGN_IDENTITY", "IPHONEOS_DEPLOYMENT_TARGET", "INFOPLIST_FILE")}

    # ---- deployment target
    min_req = ref("upload_minimums")["min_ios_deployment_target"]
    targets = {}
    if release.get("IPHONEOS_DEPLOYMENT_TARGET"):
        targets["Runner Release"] = release["IPHONEOS_DEPLOYMENT_TARGET"]
    elif proj_cfg.get("Release", {}).get("IPHONEOS_DEPLOYMENT_TARGET"):
        targets["project Release"] = proj_cfg["Release"]["IPHONEOS_DEPLOYMENT_TARGET"]
    podfile = ios / "Podfile"
    if podfile.is_file():
        m = re.search(r"^\s*platform\s+:ios\s*,\s*['\"]([\d.]+)['\"]", read_text_safe(podfile), re.M)
        if m:
            targets["Podfile platform"] = m.group(1)
    facts["deployment_targets"] = targets
    if not targets:
        c.add("PROJ-DEPLOY-001", NOT_VERIFIED, "project", "deployment target não encontrado no project.pbxproj/Podfile",
              file=rel(loc["xcodeproj"], root) if loc["xcodeproj"] else None, confidence=0.3)
    else:
        low = {k: v for k, v in targets.items() if version_tuple(v) < version_tuple(min_req)}
        if low:
            c.add("PROJ-DEPLOY-001", BLOCKER, "project", f"deployment target abaixo de {min_req}: {low}", file="ios/Runner.xcodeproj/project.pbxproj",
                  confidence=0.9, recommendation=f"Defina IPHONEOS_DEPLOYMENT_TARGET >= {min_req} e 'platform :ios, \"{min_req}\"' no Podfile.")
        else:
            c.add("PROJ-DEPLOY-001", PASS, "project", f"deployment targets {targets} >= {min_req}", file="ios/Runner.xcodeproj/project.pbxproj", confidence=0.9)

    # ---- Info.plist (source)
    resolved = {}
    gen = ios / "Flutter" / "Generated.xcconfig"
    if gen.is_file():
        g = dict(re.findall(r"^(FLUTTER_BUILD_NAME|FLUTTER_BUILD_NUMBER)=(.*)$", read_text_safe(gen), re.M))
        if g.get("FLUTTER_BUILD_NAME"):
            resolved["CFBundleShortVersionString"] = g["FLUTTER_BUILD_NAME"].strip()
        if g.get("FLUTTER_BUILD_NUMBER"):
            resolved["CFBundleVersion"] = g["FLUTTER_BUILD_NUMBER"].strip()
    pub = (inventory(root).get("pubspec") or {}) if loc["flutter"] else {}
    if pub.get("version") and "CFBundleShortVersionString" not in resolved:
        v = pub["version"]
        resolved["CFBundleShortVersionString"] = v.split("+")[0]
        if "+" in v:
            resolved["CFBundleVersion"] = v.split("+", 1)[1]
    if release.get("PRODUCT_BUNDLE_IDENTIFIER"):
        resolved["CFBundleIdentifier"] = release["PRODUCT_BUNDLE_IDENTIFIER"]
    info_rel = release.get("INFOPLIST_FILE") or "Runner/Info.plist"
    info_path = ios / info_rel
    info: dict = {}
    if not info_path.is_file():
        c.add("PLIST-PARSE-001", NOT_VERIFIED, "project", f"{info_rel} não encontrado", file=f"ios/{info_rel}", confidence=0.3)
    else:
        pl, err = load_plist(info_path)
        if err or not isinstance(pl, dict):
            c.add("PLIST-PARSE-001", BLOCKER, "project", f"Info.plist ilegível: {err}", file=f"ios/{info_rel}", confidence=1.0,
                  recommendation=f"plutil -lint ios/{info_rel}")
        else:
            info = pl
            c.add("PLIST-PARSE-001", PASS, "project", "Info.plist de origem lido", file=f"ios/{info_rel}", confidence=1.0)
            pf, pfacts = validate_info_plist(pl, "project", f"ios/{info_rel}", "project", resolved)
            c.extend(pf)
            facts["info"] = pfacts
    facts["version"] = resolved.get("CFBundleShortVersionString")
    facts["build"] = resolved.get("CFBundleVersion")
    facts["bundle_id"] = resolved.get("CFBundleIdentifier")
    facts["name"] = info.get("CFBundleDisplayName") or info.get("CFBundleName") or pub.get("name")

    # ---- entitlements (Release)
    ents: dict = {}
    ent_rel = release.get("CODE_SIGN_ENTITLEMENTS")
    if ent_rel:
        ent_path = ios / ent_rel
        pl, err = load_plist(ent_path) if ent_path.is_file() else (None, "arquivo não existe")
        if err or not isinstance(pl, dict):
            c.add("PROJ-ENT-001", CRITICAL, "project", f"CODE_SIGN_ENTITLEMENTS={ent_rel}: {err}", file=f"ios/{ent_rel}", confidence=0.95,
                  recommendation="Aponte CODE_SIGN_ENTITLEMENTS (Release) para um .entitlements válido.")
        else:
            ents = pl
            c.add("PROJ-ENT-001", PASS, "project", f"Release usa {ent_rel} ({len(pl)} chaves)", file=f"ios/{ent_rel}", confidence=1.0)
            ef, efacts = validate_entitlements(pl, "project", "project", f"ios/{ent_rel}", None, info)
            c.extend(ef)
            facts["entitlements"] = efacts
            if network_fetch and efacts.get("associated_domains"):
                from validate_entitlements import check_aasa
                c.items = [f for f in c.items if f.rule_id != "ENT-ASSOC-001"]
                c.extend(check_aasa(efacts["associated_domains"], network_fetch, "project"))
    elif release:
        c.add("PROJ-ENT-001", INFO, "project", "Release sem CODE_SIGN_ENTITLEMENTS (nenhuma capability com entitlements)", confidence=0.7)
    else:
        c.add("PROJ-ENT-001", NOT_VERIFIED, "project", "configuração Release do target não identificada", confidence=0.3)

    # ---- ExportOptions
    exp = ios / "ExportOptions.plist"
    if exp.is_file():
        pl, err = load_plist(exp)
        method = (pl or {}).get("method") if isinstance(pl, dict) else None
        facts["export_method"] = method
        if method in ("app-store", "app-store-connect"):
            c.add("PROJ-EXPORT-001", PASS, "project", f"ExportOptions method={method}", file="ios/ExportOptions.plist", confidence=1.0)
        else:
            c.add("PROJ-EXPORT-001", WARNING, "project", f"ExportOptions method={method!r}", file="ios/ExportOptions.plist", confidence=0.9,
                  recommendation="Use method 'app-store-connect' para distribuição App Store.")

    # ---- app privacy manifest in source
    pm = ios / "Runner" / "PrivacyInfo.xcprivacy"
    if pm.is_file():
        mf, mfacts = validate_manifest_file(pm, "app")
        for f in mf:
            f.file = "ios/Runner/PrivacyInfo.xcprivacy"
        c.extend(mf)
        facts["app_privacy_manifest"] = mfacts
        pbx = read_text_safe(loc["xcodeproj"] / "project.pbxproj", 8 * 1024 * 1024) if loc["xcodeproj"] else ""
        if "PrivacyInfo.xcprivacy in Resources" not in pbx:
            c.add("PRIV-MANIFEST-001", WARNING, "project", "PrivacyInfo.xcprivacy existe mas não está na fase Resources do target",
                  file="ios/Runner.xcodeproj/project.pbxproj", confidence=0.7, recommendation="Marque o arquivo no target Runner (Target Membership).")
    else:
        c.add("PRIV-MANIFEST-001", WARNING, "project", "ios/Runner/PrivacyInfo.xcprivacy ausente (manifest do app)", file="ios/Runner/PrivacyInfo.xcprivacy",
              confidence=0.6, recommendation="Crie via Xcode: File > New > File > App Privacy, target Runner; declare NSPrivacyCollectedDataTypes "
                                             "e as Required Reason APIs do seu código (ex.: UserDefaults CA92.1 via shared_preferences).")
        facts["app_privacy_manifest"] = None

    # ---- Flutter: dependencies & inventory
    deps_facts: dict = {}
    if loc["flutter"]:
        if mode != "quick":
            df, deps_facts = check_dependencies(root)
            c.extend(df)
        else:
            deps_facts = {k: v for k, v in check_dependencies(root)[1].items()}
        facts["dependencies"] = deps_facts
        c.add("PROJ-FLUTTER-001", INFO, "flutter",
              f"Flutter {deps_facts.get('flutter_sdk_constraint')}, Dart {deps_facts.get('dart_sdk_constraint')}, "
              f"{len(deps_facts.get('ios_plugins', []))} plugins iOS, {len(deps_facts.get('pods', {}))} pods", file="pubspec.lock", confidence=1.0)

    # ---- permissions vs plugins (heuristic)
    plugins = set(deps_facts.get("ios_plugins", []))
    pods = set(deps_facts.get("pods", {}))
    sig = ref("resource_signals")
    if mode != "quick":
        code_hits_res = scan_code(root / "lib" if (root / "lib").is_dir() else ios, {k: v.get("code", []) for k, v in sig.items()
                                                                                       if not k.startswith("_") and v.get("code")})
    else:
        code_hits_res = {}
    for key, s in sig.items():
        if key.startswith("_"):
            continue
        p_hits = sorted(plugins & set(s.get("plugins", [])) | pods & set(s.get("plugins", [])))
        code = code_hits_res.get(key, [])
        has = bool(info.get(key))
        if (p_hits or code) and not has:
            c.add("PRIV-USAGE-003", WARNING, "project", f"{key} ausente; sinais de uso: plugins={p_hits} código={code[:2]}",
                  file=f"ios/{info_rel}", confidence=0.6 if code else 0.45,
                  recommendation=f"Se o recurso é acessado, adicione {key} com texto específico; o app trava ao pedir permissão sem a chave.")
        elif has and not (p_hits or code) and mode != "quick" and loc["flutter"]:
            c.add("PRIV-USAGE-003", WARNING, "project", f"{key} declarado sem plugin/código correspondente identificado", file=f"ios/{info_rel}",
                  confidence=0.35, recommendation=f"Remova {key} se o recurso não é usado (5.1.1(iii) minimização), ou ignore se usado por SDK nativo.")

    # ---- Apple-guideline heuristics on source (full/policy)
    if mode in ("full", "policy") and loc["flutter"]:
        _guideline_checks(c, root, info, ents, deps_facts, facts)
    elif mode == "quick":
        for rid in ("REV-ACCTDEL-001", "REV-LOGIN-001", "REV-UGC-001", "REV-IAP-001", "PRIV-POLICY-001"):
            c.add(rid, NOT_VERIFIED, "project", "não executado no modo --quick", confidence=0.1)
    return c.items, facts


def _guideline_checks(c: Collector, root: Path, info: dict, ents: dict, deps: dict, facts: dict) -> None:
    cs = ref("code_signals")
    lock_pkgs = set((inventory(root).get("lock") or {}).keys())
    direct = set(deps.get("direct_dependencies", [])) | lock_pkgs
    code_root = root / "lib" if (root / "lib").is_dir() else root
    patterns = {k: v for k, v in cs.items() if k.endswith("_code")}
    hits = scan_code(code_root, patterns)
    facts["code_signals"] = {k: len(v) for k, v in hits.items()}

    def deps_of(key):
        return sorted(direct & set(cs.get(key, [])))

    social = deps_of("social_login_deps")
    social_code = hits.get("social_login_code", [])
    apple = deps_of("apple_login_deps")
    apple_code = hits.get("apple_login_code", [])
    siwa_ent = "com.apple.developer.applesignin" in ents
    login = bool(hits.get("login_code")) or bool(social or social_code)
    account = hits.get("account_creation_code", [])
    deletion = hits.get("account_deletion_code", [])

    # 4.8
    if social or social_code:
        ev = f"login social: deps={social} código={social_code[:2]}"
        if apple or apple_code or siwa_ent:
            c.add("REV-LOGIN-001", PASS, "project", ev + f"; alternativa Sign in with Apple: deps={apple} código={apple_code[:1]} entitlement={siwa_ent}",
                  confidence=0.6, verification_method="heuristic",
                  recommendation="Confirme na UI que a opção Apple tem destaque equivalente.")
        else:
            c.add("REV-LOGIN-001", WARNING, "project", ev + "; nenhuma alternativa equivalente (ex.: Sign in with Apple) encontrada",
                  confidence=0.6, recommendation="Adicione Sign in with Apple (pacote sign_in_with_apple + capability) ou outro login que atenda à 4.8.")
    else:
        c.add("REV-LOGIN-001", NOT_APPLICABLE, "project", "nenhum login social/terceiro detectado no código ou dependências", confidence=0.5)
    if apple or apple_code:
        c.add("REV-LOGIN-002", PASS if siwa_ent else CRITICAL, "project",
              f"Sign in with Apple no código ({(apple_code or apple)[:1]}); entitlement com.apple.developer.applesignin {'presente' if siwa_ent else 'ausente'} no Release",
              confidence=0.85, recommendation="" if siwa_ent else "Adicione a capability Sign in with Apple ao target Runner (Release).")

    # 5.1.1(v)
    if account:
        if deletion:
            c.add("REV-ACCTDEL-001", PASS, "project", f"criação de conta {account[:1]}; exclusão {deletion[:2]}", confidence=0.6,
                  recommendation="Confirme que o fluxo apaga a conta (não só desativa) e é acessível sem contato com suporte.")
        else:
            c.add("REV-ACCTDEL-001", WARNING, "project", f"criação de conta {account[:2]}; nenhum fluxo de exclusão encontrado", confidence=0.55,
                  recommendation="Implemente exclusão de conta no app (Configurações > Excluir conta) chamando o backend.")
    elif login:
        c.add("REV-ACCTDEL-001", NOT_VERIFIED, "project", "login detectado, criação de conta não identificada no código", confidence=0.3,
              recommendation="Se o app permite criar conta (inclusive via login social), ofereça exclusão no app.")
    else:
        c.add("REV-ACCTDEL-001", NOT_APPLICABLE, "project", "sem login nem criação de conta detectados", confidence=0.4)

    # 2.1 demo account
    if login:
        c.add("REV-DEMO-001", NOT_VERIFIED, "submission", f"app tem login ({(hits.get('login_code') or social_code)[:1]}); credenciais de demo só verificáveis no ASC",
              confidence=0.8, recommendation="Preencha App Review Information > Sign-in required com usuário/senha de demonstração e mantenha o backend ativo.")

    # IAP
    iap = deps_of("iap_deps")
    ext_pay = deps_of("external_payment_deps") + hits.get("external_payment_code", [])[:2]
    storekit = any((root / "ios").glob("*.storekit"))
    if iap or storekit:
        restore = hits.get("iap_restore_code", [])
        c.add("REV-IAP-002", PASS if restore else WARNING, "project",
              f"IAP: deps={iap} storekit_config={storekit}; restauração {restore[:1] if restore else 'não encontrada'}", confidence=0.55,
              recommendation="" if restore else "Adicione botão 'Restaurar compras' chamando InAppPurchase.instance.restorePurchases().")
        c.add("REV-IAP-003", NOT_VERIFIED, "submission", "IAPs precisam estar 'Ready to Submit' e visíveis ao revisor (ASC)", confidence=0.8,
              recommendation="Anexe os IAPs/assinaturas à versão no ASC e explique nas notas como acessá-los.")
        if hits.get("subscription_code"):
            c.add("REV-SUB-001", NOT_VERIFIED, "submission", f"assinatura detectada {hits['subscription_code'][:1]}; duração/valor só verificáveis no ASC",
                  confidence=0.6, recommendation="Confirme período >= 7 dias, termos de renovação e link para gerenciar assinatura.")
    if ext_pay:
        c.add("REV-IAP-001", WARNING, "project", f"pagamento externo detectado: {ext_pay}", confidence=0.5,
              recommendation="Use IAP para conteúdo digital; pagamento externo só para bens físicos ou exceções 3.1.1(a)/3.1.3.")
    elif iap or storekit:
        c.add("REV-IAP-001", PASS, "project", f"IAP via {iap or 'StoreKit'}; nenhum SDK/URL de pagamento externo detectado", confidence=0.5)
    else:
        c.add("REV-IAP-001", NOT_APPLICABLE, "project", "sem IAP nem pagamento externo detectados", confidence=0.4)

    # UGC
    ugc = hits.get("ugc_code", [])
    if ugc:
        mod = hits.get("ugc_moderation_code", [])
        c.add("REV-UGC-001", PASS if mod else WARNING, "project",
              f"sinais de UGC {ugc[:2]}; moderação/denúncia {mod[:2] if mod else 'não encontrada'}", confidence=0.4,
              recommendation="" if mod else "Se usuários publicam conteúdo, adicione filtro, denúncia, bloqueio de usuários e contato (1.2).")
    else:
        c.add("REV-UGC-001", NOT_APPLICABLE, "project", "nenhum sinal de conteúdo gerado por usuários", confidence=0.4)

    # Privacy policy in-app
    pp = hits.get("privacy_policy_code", [])
    c.add("PRIV-POLICY-001", PASS if pp else WARNING, "project",
          f"referência à política de privacidade no código: {pp[:2]}" if pp else "nenhuma URL/tela de política de privacidade encontrada",
          confidence=0.55 if pp else 0.5,
          recommendation="Confirme que o link está acessível no app (ex.: tela de login e Configurações)." if pp else
          "Adicione link para a política de privacidade dentro do app e no ASC (5.1.1(i)).")

    # Placeholders
    ph = hits.get("placeholder_code", [])
    if ph:
        c.add("REV-PLACEHOLDER-001", WARNING, "project", f"textos temporários: {ph[:3]}", confidence=0.45,
              recommendation="Remova textos 'em breve'/'lorem ipsum' de telas acessíveis ao revisor (2.1).")
    else:
        c.add("REV-PLACEHOLDER-001", PASS, "project", "nenhum placeholder óbvio em literais do código", confidence=0.4)

    # Push
    push = deps_of("push_deps")
    if push:
        aps = ents.get("aps-environment")
        c.add("REV-PUSH-001", PASS if aps else WARNING, "project", f"push via {push}; aps-environment={aps!r}", confidence=0.8,
              recommendation="" if aps else "Ative a capability Push Notifications no Release.")

    # Tracking / ATT
    trk = deps_of("tracking_deps")
    if trk:
        has = bool(info.get("NSUserTrackingUsageDescription"))
        c.add("PRIV-ATT-001", WARNING if not has else PASS, "project", f"SDKs de tracking/ads {trk}; NSUserTrackingUsageDescription {'presente' if has else 'ausente'}",
              confidence=0.5, verification_method="heuristic",
              recommendation="" if has else "Se houver tracking, peça ATT e adicione NSUserTrackingUsageDescription.")

    # Code push
    cp = deps_of("code_push_deps")
    if cp:
        c.add("REV-CODE-001", WARNING, "project", f"code push: {cp}", confidence=0.5,
              recommendation="Atualizações OTA não podem alterar funcionalidades revisadas (2.5.2).")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project")
    ap.add_argument("--mode", choices=["quick", "full", "policy"], default="full")
    a = ap.parse_args(argv)
    findings, facts = inspect_project(Path(a.project), a.mode)
    print(json.dumps({"facts": facts, "findings": [f.to_dict(i + 1) for i, f in enumerate(findings)]}, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
