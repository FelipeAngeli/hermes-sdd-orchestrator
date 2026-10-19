#!/usr/bin/env python3
"""google-play-review-guardian CLI — read-only Android / Google Play pre-submission auditor.

  guardian.py audit --quick|--full|--policy <project-dir> [--aab f.aab] [--apk f.apk]
  guardian.py audit --aab f.aab | --apk f.apk
  guardian.py gate --release <project-dir> [--aab f.aab] [--apk f.apk]
  guardian.py rules --check-sources [--offline] [--ttl-hours N]
  guardian.py rules --list
  guardian.py tools

Exit codes: 0 READY, 1 NEEDS_REVIEW, 2 BLOCKED, 3 usage error.
Never modifies, builds, signs, uploads or publishes anything.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import html
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (BLOCKER, CRITICAL, EXIT_USAGE, INFO, NOT_APPLICABLE, NOT_VERIFIED, PASS, RULES_DIR, SCHEMA_PATH,
                    SKILL_DIR, TOOL_VERSION, WARNING, Finding, FindingFactory, HashCache, ToolInfo,
                    ZipBudgetExceeded, cache_key, discover_tools, finding_from_dict, load_rules, parse_date, rules_digest,
                    ensure_private_dir, make_private_tempdir, safe_call, today_iso, write_private)
import generate_report
import inspect_aab
import inspect_apk
import inspect_dependencies
import inspect_project
import validate_manifest
import validate_native_libraries
import validate_permissions
import validate_play_console
import validate_privacy
import validate_sdk
import validate_signing


class UsageError(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):  # exit code 3 for usage errors
        self.print_usage(sys.stderr)
        print(f"{self.prog}: erro: {message}", file=sys.stderr)
        sys.exit(EXIT_USAGE)


# ---------------------------------------------------------------------------
def build_parser() -> Parser:
    p = Parser(prog="guardian.py", description="Auditor somente leitura de apps Android antes do envio ao Google Play.")
    sub = p.add_subparsers(dest="command", parser_class=Parser)

    def common_opts(sp):
        sp.add_argument("project", nargs="?", help="diretório do projeto (Flutter/Android)")
        sp.add_argument("--aab", help="caminho do .aab")
        sp.add_argument("--apk", help="caminho do .apk")
        sp.add_argument("--output-dir", help="diretório dos relatórios e do cache (padrão: diretório temporário)")
        sp.add_argument("--json", action="store_true", help="imprime somente o resumo JSON compacto")
        sp.add_argument("--form-factor", choices=validate_sdk.FORM_FACTORS, help="formato do app (padrão: detectado)")
        sp.add_argument("--submission", choices=validate_sdk.SUBMISSIONS, help="tipo de envio (padrão: new_app+app_update)")
        sp.add_argument("--extension-granted", action="store_true", help="extensão de prazo de target API concedida no Play Console")
        sp.add_argument("--account-type", choices=("personal", "organization"))
        sp.add_argument("--account-created", help="data de criação da conta de desenvolvedor (YYYY-MM-DD)")
        sp.add_argument("--declarations", help="JSON com declarações do Play Console fornecidas pelo usuário")
        sp.add_argument("--play-service-account", help="JSON de service account (modo API somente leitura, opcional)")
        sp.add_argument("--no-tools", action="store_true", help="não usar ferramentas externas do Android SDK")
        sp.add_argument("--no-cache", action="store_true")
        sp.add_argument("--timeout", type=int, default=120, help="timeout por ferramenta externa (s)")
        sp.add_argument("--as-of", help=argparse.SUPPRESS)

    a = sub.add_parser("audit", help="auditoria")
    g = a.add_mutually_exclusive_group()
    g.add_argument("--quick", action="store_const", dest="mode", const="quick")
    g.add_argument("--full", action="store_const", dest="mode", const="full")
    g.add_argument("--policy", action="store_const", dest="mode", const="policy")
    common_opts(a)
    gt = sub.add_parser("gate", help="pre-submission gate (READY/NEEDS_REVIEW/BLOCKED)")
    gt.add_argument("--release", action="store_true", required=True)
    common_opts(gt)
    r = sub.add_parser("rules", help="base de regras")
    r.add_argument("--check-sources", action="store_true", help="reconsulta as URLs oficiais (cache com TTL)")
    r.add_argument("--list", action="store_true")
    r.add_argument("--offline", action="store_true")
    r.add_argument("--ttl-hours", type=float, default=24.0)
    r.add_argument("--output-dir")
    r.add_argument("--json", action="store_true")
    sub.add_parser("tools", help="descoberta de ferramentas Android SDK")
    return p


# ---------------------------------------------------------------------------
def default_output_dir(key: str) -> Path:
    """Fresh private (0700) directory per run; a stable path would be guessable/shared in /tmp.

    Pass --output-dir to reuse the incremental cache between runs."""
    return make_private_tempdir(prefix="gprg-" + hashlib.sha256(key.encode()).hexdigest()[:8] + "-")


def tools_snapshot(tools: dict[str, ToolInfo]) -> dict:
    return {n: {"available": t.available, "path": t.path, "reason": t.reason} for n, t in tools.items()}


def no_tools() -> dict[str, ToolInfo]:
    names = ("java", "aapt2", "zipalign", "apksigner", "apkanalyzer", "bundletool", "llvm-readelf", "adb")
    return {n: ToolInfo(n, None, False, "desativado (--no-tools)") for n in names}


def load_declarations(path: str | None) -> dict:
    if not path:
        return {}
    p = Path(path)
    if not p.is_file():
        raise UsageError(f"--declarations não encontrado: {path}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise UsageError(f"--declarations não é JSON válido: {exc}")


def find_release_artifacts(project: Path) -> dict:
    out = {}
    for rel in ("build/app/outputs/bundle/release/app-release.aab", "app/build/outputs/bundle/release/app-release.aab"):
        if (project / rel).is_file():
            out["aab"] = project / rel
            break
    for rel in ("build/app/outputs/flutter-apk/app-release.apk", "build/app/outputs/apk/release/app-release.apk",
                "app/build/outputs/apk/release/app-release.apk"):
        if (project / rel).is_file():
            out["apk"] = project / rel
            break
    return out


# ---------------------------------------------------------------------------
def analyze_artifact(path: Path, kind: str, tools, F, rules, mode: str, timeout: int, cache: HashCache,
                     use_tools: bool) -> dict:
    """Artifact pipeline, cached by sha256(artifact)+rules digest+version+mode."""
    digest = cache.file_hash(path) if cache.enabled else None
    key = cache_key(kind, digest or "", rules_digest(), TOOL_VERSION, mode, str(use_tools), "v4") if digest else None
    if key:
        hit = cache.get(key)
        if hit:
            hit["findings"] = [finding_from_dict(d) for d in hit["findings"]]
            hit["from_cache"] = True
            return hit
    try:
        insp = (inspect_aab.inspect_aab if kind == "aab" else inspect_apk.inspect_apk)(path, tools, F, timeout)
    except ZipBudgetExceeded as exc:  # total decompression budget of this archive exhausted (zip bomb)
        insp = {"manifest": None, "findings": [F("SEC-ARCHIVE-SAFETY", CRITICAL, "security",
                                                 "Descompressão excede o orçamento total do arquivo", component=path.name,
                                                 evidence=str(exc), recommendation="Não extraia nem publique este arquivo.")]}
    fs: list[Finding] = list(insp["findings"])
    info = insp.get("manifest")
    res: dict = {"kind": kind, "path": str(path), "manifest": info, "verification": insp.get("verification", {}),
                 "modules": insp.get("modules"), "billing_library_version": insp.get("billing_library_version"),
                 "native_libs": [], "abis": [], "signatures": None, "permissions": [], "sha256": digest}
    if info is not None:
        fs += validate_manifest.check_manifest(info, F, source=path.name, artifact=True)
        if mode != "policy":
            sig, err = safe_call(validate_signing.validate_signing, path, kind, tools, F, timeout)
            if sig:
                fs += sig["findings"]
                res["signatures"] = sig["signatures"]
            else:
                fs.append(F("TECH-SIGNATURE-VERIFY", NOT_VERIFIED, "signing", "Módulo de assinatura falhou",
                            component=path.name, evidence=err, method="manual"))
            nat, err = safe_call(validate_native_libraries.validate_native_libraries, path, kind, tools, F,
                                 info.get("target_sdk"), timeout, use_tools)
            if nat:
                fs += nat["findings"]
                res["native_libs"] = nat["libs"]
                res["abis"] = nat["abis"]
            else:
                fs.append(F("TECH-16KB-PAGE-SIZE", NOT_VERIFIED, "page_size_16kb", "Módulo de bibliotecas nativas falhou",
                            component=path.name, evidence=err, method="manual"))
    res["findings"] = fs
    if key:
        cache.put(key, {**res, "findings": [f.to_dict() for f in fs]})
    return res


# ---------------------------------------------------------------------------
def run_audit(args, mode: str) -> tuple[dict, Path]:
    rules = load_rules()
    F = FindingFactory(rules)
    as_of = parse_date(args.as_of) if args.as_of else _dt.date.today()
    declarations = load_declarations(args.declarations)
    project = Path(args.project).resolve() if args.project else None
    if project and not project.is_dir():
        raise UsageError(f"diretório do projeto não encontrado: {args.project}")
    aab = Path(args.aab).resolve() if args.aab else None
    apk = Path(args.apk).resolve() if args.apk else None
    for p, flag in ((aab, "--aab"), (apk, "--apk")):
        if p and not p.is_file():
            raise UsageError(f"{flag} não encontrado: {p}")
    if not (project or aab or apk):
        raise UsageError("informe um diretório de projeto, --aab ou --apk")
    autodiscovered = []
    if project and mode in ("gate", "full") and not (aab or apk):
        found = find_release_artifacts(project)
        aab, apk = found.get("aab"), found.get("apk")
        autodiscovered = [str(x) for x in (aab, apk) if x]
    out_dir = Path(args.output_dir).resolve() if args.output_dir else default_output_dir(str(project or aab or apk))
    if project and (out_dir == project or project in out_dir.parents):
        raise UsageError("--output-dir não pode ficar dentro do projeto auditado (modo somente leitura)")
    ensure_private_dir(out_dir)  # a missing --output-dir is created 0700; existing dirs are untouched
    cache = HashCache(out_dir, enabled=not args.no_cache)
    use_tools = not args.no_tools
    tools = discover_tools() if use_tools else no_tools()
    findings: list[Finding] = []
    coverage: dict = {"mode": mode, "artifacts_autodiscovered": autodiscovered or "não",
                      "network": "não utilizada na auditoria (somente rules --check-sources e API opcional)"}
    limitations: list[str] = []
    module_errors: list[str] = []
    inventory: dict = {}
    app: dict = {"package": None, "label": None, "version_name": None, "version_code": None, "min_sdk": None,
                 "target_sdk": None, "compile_sdk": None, "source": "-"}

    # ---------------- project
    proj = None
    if project:
        proj, err = safe_call(inspect_project.inspect_project, project, F, cache)
        if err:
            module_errors.append(f"inspect_project: {err}")
        else:
            findings += proj["findings"]
            gradle = proj.get("gradle") or {}
            coverage["project_type"] = proj["detection"]["type"]
            coverage["gradle_file"] = gradle.get("file", "-").replace(str(project) + "/", "") if gradle else "-"
            hygiene = validate_signing.project_signing_hygiene(proj.get("sensitive_files", []), proj.get("git_status", {}), F)
            findings += hygiene
            if proj.get("manifest_path"):
                try:
                    src_root = validate_manifest.parse_source_manifest(Path(proj["manifest_path"]))
                    src_info = validate_manifest.extract_info(src_root)
                    src_info["target_sdk"] = src_info["target_sdk"] or gradle.get("target_sdk")
                    proj["source_manifest"] = src_info
                    if not (aab or apk):
                        findings += validate_manifest.check_manifest(src_info, F, source="android/app/src/main", artifact=False)
                    findings += validate_manifest.check_source_network_config(Path(proj["detection"]["android_dir"]), F)
                except Exception as exc:  # noqa: BLE001
                    module_errors.append(f"manifest do projeto: {type(exc).__name__}: {exc}")
            app.update({"package": gradle.get("application_id"), "min_sdk": gradle.get("min_sdk"),
                        "target_sdk": gradle.get("target_sdk"), "compile_sdk": gradle.get("compile_sdk"),
                        "source": "projeto (build.gradle)"})
            pub = proj.get("pubspec") or {}
            if pub.get("version"):
                vn, _, vc = pub["version"].partition("+")
                app["version_name"] = vn
                app["version_code"] = int(vc) if vc.isdigit() else None
            fd = proj.get("flutter_defaults") or {}
            inventory["project"] = {
                "type": proj["detection"]["type"], "agp": proj["plugins"].get("agp"), "kotlin": proj["plugins"].get("kotlin"),
                "gradle_wrapper": proj["plugins"].get("gradle_wrapper"),
                "flutter_version": fd.get("flutter_version"), "dart_version": fd.get("dart_version"),
                "flutter_constraint": pub.get("flutter_constraint"), "dart_constraint": pub.get("dart_constraint"),
                "lock_sdks": pub.get("lock_sdks"), "target_sdk_expr": gradle.get("target_sdk_expr"),
                "target_sdk_resolved_from": gradle.get("target_sdk_resolved_from"), "ndk_version": gradle.get("ndk_version"),
                "release_minify": gradle.get("release_minify"), "product_flavors": gradle.get("product_flavors"),
                "sensitive_files_present": proj.get("sensitive_files", []),
            }

    # ---------------- artifacts
    artifacts: list[dict] = []
    for path, kind in ((aab, "aab"), (apk, "apk")):
        if not path:
            continue
        res, err = safe_call(analyze_artifact, path, kind, tools, F, rules, mode, args.timeout, cache, use_tools)
        if err:
            module_errors.append(f"{kind}: {err}")
            findings.append(F("TECH-ARTIFACT-INTEGRITY", NOT_VERIFIED, "android_build", f"Falha ao analisar {kind}",
                              component=path.name, evidence=err, method="manual"))
            continue
        artifacts.append(res)
        findings += res["findings"]
    primary = next((a for a in artifacts if a["manifest"]), None)
    if primary:
        m = primary["manifest"]
        app.update({k: m.get(k) for k in ("package", "version_name", "version_code", "min_sdk", "target_sdk", "compile_sdk")})
        app["label"] = (m.get("application") or {}).get("label")
        app["source"] = f"artefato {Path(primary['path']).name} (manifest mesclado)"
        if project and proj and proj.get("pubspec", {}).get("version"):
            vn, _, vc = proj["pubspec"]["version"].partition("+")
            if vc.isdigit() and m.get("version_code") is not None and int(vc) != m.get("version_code"):
                findings.append(F("TECH-ARTIFACT-FRESHNESS", WARNING, "android_build", "Artefato não corresponde à versão atual do projeto",
                                  component=Path(primary["path"]).name,
                                  evidence=f"pubspec version={proj['pubspec']['version']} vs artefato versionCode={m.get('version_code')} versionName={m.get('version_name')}",
                                  recommendation="Gere novamente o build release (`flutter build appbundle --release`) antes do envio e audite o artefato novo."))
    if len(artifacts) == 2 and all(a["manifest"] for a in artifacts):
        a1, a2 = artifacts[0]["manifest"], artifacts[1]["manifest"]
        if a1.get("version_code") != a2.get("version_code"):
            findings.append(F("TECH-ARTIFACT-FRESHNESS", INFO, "android_build", "AAB e APK auditados têm versionCode diferentes",
                              component="artefatos", evidence=f"aab={a1.get('version_code')} apk={a2.get('version_code')}",
                              recommendation="Confirme qual artefato será enviado; o Google Play exige o .aab para novos apps."))
    if aab is None and apk is not None:
        findings.append(F("TECH-AAB-REQUIRED", WARNING, "android_build", "Nenhum .aab auditado",
                          component=Path(apk).name, evidence="somente APK fornecido",
                          recommendation="Gere e audite o .aab: `flutter build appbundle --release` ou `./gradlew bundleRelease`."))
    elif project and aab is None and mode in ("gate", "full"):
        findings.append(F("TECH-AAB-REQUIRED", NOT_VERIFIED, "android_build", "Nenhum .aab release encontrado para auditar",
                          component=str(project.name), evidence="build/app/outputs/bundle/release/app-release.aab ausente", method="manual",
                          recommendation="Gere o bundle (fora deste auditor) e rode `guardian.py gate --release <projeto> --aab <arquivo>`."))

    # ---------------- target sdk
    manifest_for_ff = primary["manifest"] if primary else (proj or {}).get("source_manifest") if proj else None
    ff, ff_ev = (args.form_factor, "informado (--form-factor)") if args.form_factor else validate_sdk.detect_form_factor(manifest_for_ff)
    coverage["form_factor"] = f"{ff} ({ff_ev})"
    if primary:
        findings += validate_sdk.validate_target_sdk(primary["manifest"].get("target_sdk"), rules=rules, F=F,
                                                     component=Path(primary["path"]).name, form_factor=ff,
                                                     submission=args.submission, as_of=as_of,
                                                     extension_granted=args.extension_granted,
                                                     source_note="manifest mesclado do artefato")
    elif proj and proj.get("gradle"):
        g = proj["gradle"]
        resolved = g.get("target_sdk_resolved_from")
        findings += validate_sdk.validate_target_sdk(g.get("target_sdk"), rules=rules, F=F,
                                                     component=coverage.get("gradle_file", "build.gradle"), form_factor=ff,
                                                     submission=args.submission, as_of=as_of,
                                                     extension_granted=args.extension_granted,
                                                     method="heuristic" if resolved else "deterministic",
                                                     confidence="medium" if resolved else "high",
                                                     source_note=(f"{g.get('target_sdk_expr')} resolvido via {resolved}" if resolved else f"targetSdk={g.get('target_sdk_expr')}"))
    else:
        findings += validate_sdk.validate_target_sdk(None, rules=rules, F=F, component="-", as_of=as_of)

    # ---------------- permissions
    perm_info = primary["manifest"] if primary else (proj or {}).get("source_manifest") if proj else None
    perm_res = validate_permissions.validate_permissions(perm_info, rules, F, component=(Path(primary["path"]).name if primary else "android/app/src/main/AndroidManifest.xml"),
                                                         artifact=bool(primary), declarations=declarations)
    findings += perm_res["findings"]
    inventory["permissions"] = perm_res["permissions"]
    inventory["native_libs"] = [l for a in artifacts for l in a.get("native_libs", [])]
    inventory["artifacts"] = [{"kind": a["kind"], "path": a["path"], "sha256": a.get("sha256"), "modules": a.get("modules"),
                               "abis": a.get("abis"), "verification": a.get("verification"), "from_cache": a.get("from_cache", False),
                               "signature_schemes": (a.get("signatures") or {}).get("schemes"),
                               "certificates": [{k: c.get(k) for k in ("source", "sha256", "subject", "key_algorithm", "key_bits")}
                                                for c in (a.get("signatures") or {}).get("certificates", [])],
                               "billing_library_version": a.get("billing_library_version")} for a in artifacts]

    # ---------------- privacy / billing / deps / console
    signals = None
    if mode in ("full", "policy", "gate") and project and proj:
        signals, err = safe_call(inspect_project.scan_sources, project, cache)
        if err:
            module_errors.append(f"scan_sources: {err}")
        else:
            coverage["source_files_scanned"] = signals["files_scanned"]
            for s in signals["secrets"][:10]:
                findings.append(F("SEC-HARDCODED-SECRETS", WARNING, "security", f"Possível segredo hardcoded ({s['kind']})",
                                  component=f"{s['file']}:{s['line']}", evidence=s["value"], method="heuristic", confidence="medium",
                                  recommendation="Remova o segredo do código, revogue/rotacione a chave e use armazenamento seguro ou backend."))
            if signals["webview_js"]:
                w = signals["webview_js"][0]
                findings.append(F("SEC-WEBVIEW-JS-BRIDGE", WARNING, "security", "WebView com JavaScript/bridge habilitado",
                                  component=f"{w['file']}:{w['line']}", evidence=w["match"], method="heuristic", confidence="medium",
                                  recommendation="Carregue apenas conteúdo confiável (HTTPS) e restrinja interfaces expostas ao JS."))
    if mode != "quick":
        dep_inv = inspect_dependencies.inspect_dependencies((proj or {}).get("pubspec") if proj else None, aab)
        inventory["dependencies"] = dep_inv["dependencies"]
        inventory["maven_library_count"] = dep_inv["maven_count"]
        findings += inspect_dependencies.dependency_findings(dep_inv, F, "dependências")
        findings += validate_privacy.validate_privacy(dep_inv["dependencies"], signals, primary["manifest"] if primary else None, F,
                                                      declarations=declarations, component="Data safety")
        billing_version = next((a.get("billing_library_version") for a in artifacts if a.get("billing_library_version")), None)
        findings += validate_privacy.validate_billing(dep_inv["dependencies"], signals, billing_version,
                                                      [p["name"] for p in (perm_info or {}).get("permissions", [])], rules, F,
                                                      as_of=as_of, component="monetização", declarations=declarations)
        findings += validate_play_console.validate_store_listing(declarations.get("store_listing"), rules, F)
        account = dict(declarations.get("account") or {})
        if args.account_type:
            account["type"] = args.account_type
        if args.account_created:
            account["created"] = args.account_created
        findings += validate_play_console.validate_testing(account or None, F, as_of)
        if args.play_service_account or os.environ.get("GPRG_PLAY_ACCESS_TOKEN"):
            pkg = app.get("package")
            if pkg:
                api = validate_play_console.read_play_api(pkg, Path(args.play_service_account) if args.play_service_account else None, F)
                findings += api["findings"]
                inventory["play_api"] = {k: v for k, v in api.items() if k != "findings"}
        else:
            limitations.append("Google Play Developer API não consultada (nenhuma credencial fornecida); metadados do Console exigem verificação manual.")
    else:
        findings.append(F("PRIV-DATA-SAFETY-FORM", NOT_VERIFIED, "data_safety", "Privacidade/Data safety não avaliados no modo quick",
                          component="-", method="manual", evidence="cobertura parcial (quick)",
                          recommendation="Rode `guardian.py audit --full <projeto>` ou `gate --release`."))
        findings.append(F("POL-PLAY-BILLING", NOT_VERIFIED, "billing", "Cobrança não avaliada no modo quick",
                          component="-", method="manual", evidence="cobertura parcial (quick)", recommendation="Rode --full ou --policy."))
        findings.append(F("POL-STORE-LISTING", NOT_VERIFIED, "play_console", "Metadados do Play Console não avaliados no modo quick",
                          component="-", method="manual", evidence="cobertura parcial (quick)", recommendation="Rode --full, --policy ou gate."))
    if mode == "policy":
        coverage["skipped"] = "assinatura e 16 KB (modo policy)"

    # ---------------- user confirmations of manual checks
    confirmations = declarations.get("manual_confirmations") or {}
    if confirmations:
        for f in findings:
            # Only an explicit, recorded user confirmation turns a pending manual/heuristic item into
            # a visible INFO "confirmado pelo usuário"; nothing is ever promoted to PASS or N/A.
            if f.severity == NOT_VERIFIED and f.verification_method in ("manual", "heuristic") and f.rule_id in confirmations:
                f.severity = INFO
                f.title = f"{f.title} — confirmado pelo usuário"
                f.evidence = f"confirmação do usuário: {str(confirmations[f.rule_id])[:200]}"
                f.confidence = "low"

    # ---------------- functional tests (optional layer, never runs installs)
    adb = tools.get("adb")
    findings.append(F("TECH-FUNCTIONAL-TESTS", NOT_VERIFIED, "testing", "Testes funcionais em dispositivo/emulador não executados",
                      component="-", method="manual",
                      evidence=f"adb {'disponível' if adb and adb.available else 'indisponível'}; instalação em dispositivo exige autorização explícita",
                      recommendation="Execute `flutter test integration_test` / Espresso / UI Automator e o pre-launch report do Play Console; nunca classificado como aprovado sem execução."))

    # ---------------- limitations
    if not use_tools:
        limitations.append("Ferramentas do Android SDK desativadas (--no-tools): só parsers Python foram usados.")
    else:
        java = tools.get("java")
        if not (java and java.available):
            limitations.append(f"Sem Java runtime ({java.reason if java else 'n/d'}): apksigner, apkanalyzer e bundletool não executados; validade criptográfica da assinatura e `bundletool validate` ficam NOT_VERIFIED.")
        for n in ("aapt2", "zipalign", "llvm-readelf"):
            t = tools.get(n)
            if not (t and t.available):
                limitations.append(f"{n} indisponível: {t.reason if t else 'n/d'} — usado fallback Python quando possível.")
    limitations += [
        "O alinhamento ZIP de 16 KB dos APKs que o Google Play gera a partir do .aab não é observável no bundle; o auditor verifica ELF e APKs locais.",
        "Políticas de conteúdo (UGC, IP, malware, abuso de dispositivo, Families) exigem revisão humana; o auditor não as avalia automaticamente.",
        "Coleta de dados não é inferida de dependências: inventário de SDKs é apenas indício para o Data safety.",
        "READY nunca significa aprovação garantida pelo Google Play.",
    ]
    if module_errors:
        limitations += [f"Falha isolada de módulo: {e}" for e in module_errors]
        coverage["module_errors"] = module_errors

    rules_meta = {"count": len(rules), "digest": rules_digest(),
                  "consulted_at": max(r["consulted_at"] for r in rules.values()),
                  "sources_status": sources_status_line(out_dir)}
    target = {"project": str(project) if project else None, "aab": str(aab) if aab else None, "apk": str(apk) if apk else None,
              "aab_sha256": next((a["sha256"] for a in artifacts if a["kind"] == "aab"), None),
              "apk_sha256": next((a["sha256"] for a in artifacts if a["kind"] == "apk"), None),
              "project_type": coverage.get("project_type"), "form_factor": ff, "submission": args.submission}
    report = generate_report.build_report(mode=mode, target=target, app=app, findings=findings, coverage=coverage,
                                          tools=tools_snapshot(tools), rules_meta=rules_meta, inventory=inventory,
                                          limitations=limitations, cache_stats={"enabled": cache.enabled, "hits": cache.hits, "misses": cache.misses})
    return report, out_dir


# ---------------------------------------------------------------------------
SOURCES_CACHE = "sources-cache.json"


def _sources_cache_path(out_dir: Path | None) -> Path:
    # Without --output-dir: a per-user directory (created 0700 by ensure_private_dir).
    shared = Path(tempfile.gettempdir()) / f"google-play-review-guardian-{os.getuid() if hasattr(os, 'getuid') else 'user'}"
    return (out_dir or shared) / SOURCES_CACHE


def sources_status_line(out_dir: Path | None) -> str:
    p = _sources_cache_path(out_dir)
    if not p.is_file():
        p = _sources_cache_path(None)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "fontes não reconsultadas (rode `guardian.py rules --check-sources` antes de auditorias críticas)"
    ok = sum(1 for v in data["urls"].values() if v.get("status") == 200)
    drift = [d for d in data.get("drift", [])]
    return f"última checagem {data.get('checked_at')}: {ok}/{len(data['urls'])} URLs HTTP 200; drift: {drift or 'nenhum'}"


def _page_text(raw: bytes) -> str:
    t = raw.decode("utf-8", "replace")
    t = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", t)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t)


def detect_target_api_drift(text: str, rules: dict) -> list[str]:
    """Compare the 'New apps and app updates must target ... (API level N)' sentence with rule data."""
    drift = []
    m = re.search(r"New apps and app updates must target Android \d+ \(API level (\d+)\)", text)
    if m:
        page = int(m.group(1))
        data = max(r["min_target_api"] for r in rules["TECH-TARGET-API-LEVEL"]["requirements"] if r["form_factor"] == "phone/tablet")
        if page != data:
            drift.append(f"TECH-TARGET-API-LEVEL: página exige API {page}, regras têm {data}")
    else:
        drift.append("TECH-TARGET-API-LEVEL: frase de referência não encontrada (página mudou?)")
    return drift


def check_sources(offline: bool, ttl_hours: float, out_dir: Path | None, timeout: int = 20) -> dict:
    rules = load_rules()
    urls = sorted({r["official_url"] for r in rules.values()} | {u for r in rules.values() for u in r.get("secondary_urls", [])})
    path = _sources_cache_path(out_dir)
    ensure_private_dir(path.parent)
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {"urls": {}}
    now = _dt.datetime.now(_dt.timezone.utc)
    drift: list[str] = []
    fetched = skipped = 0
    for u in urls:
        prev = cache["urls"].get(u, {})
        fresh = prev.get("checked_at") and (now - _dt.datetime.fromisoformat(prev["checked_at"])).total_seconds() < ttl_hours * 3600
        if offline or fresh:
            skipped += 1
            if offline and not prev:
                cache["urls"][u] = {"status": None, "error": "offline: nunca consultada", "checked_at": None}
            continue
        req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 (google-play-review-guardian)", "Accept-Language": "en-US,en"})
        entry: dict = {"checked_at": now.replace(microsecond=0).isoformat()}
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read(8 * 1024 ** 2)
                entry["status"] = r.status
                text = _page_text(raw)
                entry["sha256_text"] = hashlib.sha256(text.encode()).hexdigest()
                entry["changed_since_last_check"] = bool(prev.get("sha256_text")) and prev.get("sha256_text") != entry["sha256_text"]
                if "answer/11926878" in u:
                    d = detect_target_api_drift(text, rules)
                    entry["drift"] = d
                    drift += d
        except urllib.error.HTTPError as exc:
            entry.update({"status": exc.code, "error": f"HTTP {exc.code}"})
        except (urllib.error.URLError, OSError) as exc:
            entry.update({"status": None, "error": f"{type(exc).__name__}: {exc}"[:200]})
            entry["checked_at"] = prev.get("checked_at")  # keep previous successful date
            entry.update({k: prev[k] for k in ("sha256_text",) if k in prev})
        fetched += 1
        cache["urls"][u] = entry
    cache["checked_at"] = now.replace(microsecond=0).isoformat()
    cache["drift"] = drift or [d for v in cache["urls"].values() for d in v.get("drift", [])]
    cache["rules_digest"] = rules_digest()
    write_private(path, json.dumps(cache, indent=1))
    return {"cache_file": str(path), "urls": len(urls), "fetched": fetched, "skipped_cached_or_offline": skipped,
            "ok": sum(1 for v in cache["urls"].values() if v.get("status") == 200),
            "errors": {u: v.get("error") for u, v in cache["urls"].items() if v.get("error")},
            "drift": cache["drift"], "offline": offline}


# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help(sys.stderr)
        return EXIT_USAGE
    try:
        if args.command == "tools":
            print(json.dumps(tools_snapshot(discover_tools()), indent=1))
            return 0
        if args.command == "rules":
            if args.check_sources:
                res = check_sources(args.offline, args.ttl_hours, Path(args.output_dir).resolve() if args.output_dir else None)
                print(json.dumps(res, ensure_ascii=False, indent=None if args.json else 1))
                return 1 if res["drift"] else 0
            if args.list:
                rules = load_rules()
                if args.json:
                    print(json.dumps([{k: r[k] for k in ("id", "requirement_level", "severity_if_violated", "verification_method")} for r in rules.values()]))
                else:
                    for r in rules.values():
                        print(f"{r['id']:<34} {r['requirement_level']:<11} {r['severity_if_violated']:<9} {r['verification_method']:<13} {r['official_url']}")
                return 0
            raise UsageError("use rules --check-sources ou rules --list")
        if args.command == "audit":
            mode = args.mode or ("artifact" if not args.project else "quick")
            if args.mode and not args.project:
                if not (args.aab or args.apk):
                    raise UsageError(f"--{args.mode} exige o diretório do projeto")
        else:
            mode = "gate"
            if not args.project and not (args.aab or args.apk):
                raise UsageError("gate --release exige o diretório do projeto")
        report, out_dir = run_audit(args, mode)
        md, js = generate_report.write_reports(report, out_dir)
        if args.json:
            print(json.dumps({**report["summary"], "report_md": str(md), "report_json": str(js)}, ensure_ascii=False))
        else:
            text = generate_report.render_markdown(report)
            block = text.split("```text", 1)[1].split("```", 1)[0]
            print(block.strip())
            print(f"\nRelatórios: {md}\n            {js}")
        return report["exit_code"]
    except UsageError as exc:
        print(f"erro de uso: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
