#!/usr/bin/env python3
"""App Store Review Guardian — read-only iOS App Store pre-submission auditor.

Commands:
  guardian.py audit --quick|--full|--policy <project-dir> [--ipa F.ipa | --app Runner.app]
  guardian.py audit --ipa F.ipa
  guardian.py gate --release <project-dir> [--ipa F.ipa]
  guardian.py rules --check-sources [--force] [--offline] [--ttl-days N]
  guardian.py asc --template

Common options: --output-dir DIR, --json, --asc-metadata FILE, --network, --no-cache.
Exit codes: 0 READY, 1 NEEDS_REVIEW, 2 BLOCKED, 3 usage error.
Never modifies, builds, signs or uploads the audited project.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    NOT_VERIFIED, PASS, TOOL_VERSION, WARNING, Finding, HashCache, have_tool, rules, sha256_file,
)
import check_sources  # noqa: E402
import generate_report  # noqa: E402
import inspect_asc  # noqa: E402
import inspect_ipa  # noqa: E402
import inspect_project  # noqa: E402

EXIT = {"READY": 0, "NEEDS_REVIEW": 1, "BLOCKED": 2}
USAGE = 3
TOOLS = ("codesign", "security", "otool", "lipo", "plutil")
PROJECT_INPUTS = ("pubspec.yaml", "pubspec.lock", ".flutter-plugins-dependencies", "ios/Podfile", "ios/Podfile.lock",
                  "ios/ExportOptions.plist", "ios/Flutter/Generated.xcconfig")


class UsageError(Exception):
    pass


# --------------------------------------------------------------------------- hashing of inputs
def project_digest(root: Path) -> tuple[str, dict[str, str]]:
    files: dict[str, str] = {}
    loc = inspect_project.locate(root)
    cands = [root / p for p in PROJECT_INPUTS]
    ios = loc["ios"]
    if ios:
        cands += list((ios / "Runner").glob("*.plist")) + list((ios / "Runner").glob("*.entitlements")) + list((ios / "Runner").glob("*.xcprivacy"))
        cands += list(ios.glob("*.storekit"))
        if loc["xcodeproj"]:
            cands.append(loc["xcodeproj"] / "project.pbxproj")
    src_root = root / "lib" if (root / "lib").is_dir() else (ios or root)
    cands += list(inspect_project.iter_sources(src_root))
    for p in cands:
        if p.is_file():
            files[str(p.relative_to(root))] = sha256_file(p)
    h = hashlib.sha256()
    for k in sorted(files):
        h.update(f"{k}\0{files[k]}\n".encode())
    return h.hexdigest(), files


def dir_digest(app: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(app.rglob("*")):
        if p.is_file() and not p.is_symlink():
            h.update(f"{p.relative_to(app)}\0{sha256_file(p)}\n".encode())
    return h.hexdigest()


def rules_digest() -> str:
    h = hashlib.sha256()
    for p in sorted(check_sources.RULES_DIR.glob("*.json")):
        h.update(p.read_bytes())
    h.update(TOOL_VERSION.encode())
    return h.hexdigest()[:16]


# --------------------------------------------------------------------------- core
def run_audit(*, mode: str, project: Path | None, ipa: Path | None, app: Path | None, out_dir: Path,
              asc_metadata: Path | None, network: bool, use_cache: bool) -> tuple[dict, list[str] | None, Path, Path]:
    for p in (project, ipa, app):
        if p is not None and not p.exists():
            raise UsageError(f"caminho não encontrado: {p}")
    project = project.resolve() if project else None
    ipa = ipa.resolve() if ipa else None
    app = app.resolve() if app else None
    for p in (project, app):
        if p is not None:
            try:
                out_dir.resolve().relative_to(p.resolve())
                raise UsageError("--output-dir não pode ficar dentro do projeto/bundle auditado (a auditoria é somente leitura)")
            except ValueError:
                pass
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = HashCache(out_dir / ".guardian-cache.json" if use_cache else None)
    rd = rules_digest()
    findings: list[Finding] = []
    facts: dict = {}
    target = {"project": str(project) if project else None, "ipa": None, "app_name": None, "bundle_id": None,
              "version": None, "build": None, "ipa_sha256": None}
    changed: list[str] = []
    sub_mode = "full" if mode == "gate" else mode

    # ---- project
    if project:
        digest, files = project_digest(project)
        prev = (cache.data.get("files") or {}).get(str(project), {})
        changed = sorted(k for k in files if prev.get(k) != files[k]) + sorted(k for k in prev if k not in files)
        cache.data.setdefault("files", {})[str(project)] = files

        fetch = None
        if network:
            fetch = check_sources.default_fetch
        key = f"project:{project.resolve()}:{sub_mode}:{rd}:{int(network)}"
        pf, pfacts = cache.module(key, digest, lambda: inspect_project.inspect_project(project, sub_mode, fetch))
        findings += pf
        facts["project"] = pfacts
        target.update({"app_name": pfacts.get("name"), "bundle_id": pfacts.get("bundle_id"),
                       "version": pfacts.get("version"), "build": pfacts.get("build")})

    # ---- ipa / app
    bundle_audited = False
    signed_ipa = False
    if ipa or app:
        artifact = ipa or app
        full = sub_mode != "quick"
        digest = sha256_file(artifact) if ipa else dir_digest(artifact)
        key = f"{'ipa' if ipa else 'app'}:{artifact.resolve()}:{int(full)}:{rd}:{','.join(t for t in TOOLS if have_tool(t))}"
        if ipa:
            bf, bfacts = cache.module(key, digest, lambda: inspect_ipa.inspect_ipa(artifact, full=full))
            target["ipa"] = str(ipa)
            target["ipa_sha256"] = digest
        else:
            bf, bfacts = cache.module(key, digest, lambda: inspect_ipa.inspect_app_dir(artifact, "app", artifact.name, full=full))
            target["ipa"] = f"{app} (bundle .app local)"
        findings += bf
        facts["bundle"] = bfacts
        bundle_audited = True
        signed_ipa = bool(ipa) and bool((bfacts.get("signing") or {}).get("signed"))
        if not (bfacts.get("signing") or {}).get("signed"):
            target["ipa"] = f"{target['ipa']} [UNSIGNED — não serve para upload; assinatura/perfil não verificáveis]"
            facts["artifact_label"] = "UNSIGNED"
        info = bfacts.get("info") or {}
        target["bundle_id"] = info.get("bundle_id") or target["bundle_id"]
        target["version"] = info.get("CFBundleShortVersionString") or target["version"]
        target["build"] = info.get("CFBundleVersion") or target["build"]
        target["app_name"] = bfacts.get("name") or target["app_name"]
        if project and facts.get("project", {}).get("bundle_id") and info.get("bundle_id") and facts["project"]["bundle_id"] != info["bundle_id"]:
            findings.append(Finding("PLIST-ID-001", WARNING, "app", f"bundle id do projeto ({facts['project']['bundle_id']}) ≠ do artefato ({info['bundle_id']})",
                                    confidence=0.9, recommendation="Confirme que o .ipa corresponde a este projeto/flavor."))
        missing = [t for t in ("codesign", "security") if not have_tool(t)]
        if missing:
            findings.append(Finding("TOOLS-001", NOT_VERIFIED, "environment", f"ferramentas ausentes: {missing}; checagens criptográficas de assinatura/perfil não executadas",
                                    confidence=1.0, recommendation="Rode em macOS com Xcode Command Line Tools ('xcode-select --install')."))
    elif mode in ("gate",) or project:
        for rid in ("SIGN-001", "SIGN-004", "BUILD-SDK-001", "BIN-ARCH-001", "ENT-GTA-001"):
            findings.append(Finding(rid, NOT_VERIFIED, "app", "nenhum .ipa/.app informado: verificação de artefato não executada", confidence=0.1,
                                    recommendation="Gere o .ipa (com autorização) e rode: guardian.py gate --release <projeto> --ipa build/ios/ipa/<App>.ipa"))

    # ---- App Store Connect
    if mode in ("full", "policy", "gate"):
        md = None
        if asc_metadata:
            try:
                md = json.loads(asc_metadata.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise UsageError(f"--asc-metadata inválido: {exc}") from exc
        af, afacts = inspect_asc.check_asc(md)
        findings += af
        facts["asc"] = afacts

    # ---- rules freshness
    fr = check_sources.freshness(out_dir)
    sc = fr.get("sources_cache") or {}
    if fr["rules_age_days"] > 30 or sc.get("changed"):
        findings.append(Finding("RULES-FRESH-001", WARNING, "rules",
                                f"regras consultadas em {fr['rules_consulted_at']} ({fr['rules_age_days']} dias); fontes alteradas: {sc.get('changed', 'não verificado')}",
                                confidence=0.9, recommendation="Rode 'guardian.py rules --check-sources --output-dir <dir>' e revise as regras afetadas."))
    else:
        findings.append(Finding("RULES-FRESH-001", PASS, "rules",
                                f"regras consultadas em {fr['rules_consulted_at']} ({fr['rules_age_days']} dias)"
                                + (f"; última checagem de fontes {sc.get('checked_at')}" if sc else "; fontes não rechecadas neste diretório"),
                                confidence=0.7))

    if mode == "policy":
        findings = [f for f in findings if rules().get(f.rule_id).get("area") in ("privacy", "app_review", "submission", "meta")]

    tools = {t: have_tool(t) for t in TOOLS}
    report = generate_report.build_report(
        findings, mode=mode, target=target, facts=facts, tools=tools, network=network,
        rules_info={"rules_consulted_at": fr["rules_consulted_at"], "rules_age_days": fr["rules_age_days"], "sources_cache": fr["sources_cache"]},
        cache_info={"file": str(cache.path) if cache.path else None, "hits": cache.hits, "misses": cache.misses, "changed_inputs": changed[:50]},
        ipa_audited=signed_ipa or (bundle_audited and mode != "gate"),
    )
    cache.save()
    errors = generate_report.validate_report(report)
    jp, mp = generate_report.write_reports(report, out_dir)
    return report, errors, jp, mp


def summary(report: dict, errors, jp: Path, mp: Path) -> dict:
    top = [{"id": f["id"], "severity": f["severity"], "rule_id": f["rule_id"], "title": f["title"], "component": f["component"]}
           for f in report["findings"] if f["severity"] in ("BLOCKER", "CRITICAL", "WARNING")][:12]
    return {"final_status": report["final_status"], "mode": report["mode"], "counts": report["counts"],
            "sections": {k: v["status"] for k, v in report["sections"].items()},
            "readiness": {k: {"conformity_pct": v["conformity_pct"], "coverage_pct": v["coverage_pct"]}
                          for k, v in report["readiness"].items() if k != "note"},
            "gate_reasons": report.get("gate_reasons", [])[:6], "top_findings": top,
            "pending_mandatory": sum(1 for p in report["pending"] if p["gate_relevant"]),
            "reports": {"json": str(jp), "md": str(mp)}, "schema_errors": errors,
            "cache": {"hits": report["cache"]["hits"], "misses": report["cache"]["misses"]},
            "disclaimer": "READY não garante aprovação da Apple."}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="guardian.py", description="App Store Review Guardian (somente leitura)")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--output-dir", type=Path)
        p.add_argument("--json", action="store_true", help="resumo JSON compacto no stdout")
        p.add_argument("--asc-metadata", type=Path)
        p.add_argument("--network", action="store_true", help="permite GET de AASA (Universal Links)")
        p.add_argument("--no-cache", action="store_true")

    a = sub.add_parser("audit")
    g = a.add_mutually_exclusive_group()
    g.add_argument("--quick", action="store_true")
    g.add_argument("--full", action="store_true")
    g.add_argument("--policy", action="store_true")
    a.add_argument("project", nargs="?", type=Path)
    a.add_argument("--ipa", type=Path)
    a.add_argument("--app", type=Path, help="bundle .app local (ex.: build --no-codesign)")
    common(a)

    gt = sub.add_parser("gate")
    gt.add_argument("--release", type=Path, required=True, metavar="PROJECT")
    gt.add_argument("--ipa", type=Path)
    gt.add_argument("--app", type=Path)
    common(gt)

    r = sub.add_parser("rules")
    r.add_argument("--check-sources", action="store_true")
    r.add_argument("--list", action="store_true")
    r.add_argument("--force", action="store_true")
    r.add_argument("--offline", action="store_true")
    r.add_argument("--ttl-days", type=int, default=7)
    r.add_argument("--output-dir", type=Path)
    r.add_argument("--json", action="store_true")

    s = sub.add_parser("asc")
    s.add_argument("--template", action="store_true", required=True)
    return ap


def default_out(name: str) -> Path:
    return Path(tempfile.gettempdir()) / "appstore-review-guardian" / name


def main(argv=None) -> int:
    ap = build_parser()
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return USAGE if exc.code else 0
    try:
        if args.cmd == "asc":
            print(json.dumps(inspect_asc.TEMPLATE, ensure_ascii=False, indent=2))
            return 0
        if args.cmd == "rules":
            out = args.output_dir or default_out("rules")
            if args.list:
                print(json.dumps([{k: r[k] for k in ("id", "severity_if_violated", "requirement_level", "verification_method", "guideline")}
                                  for r in rules().rules.values()], ensure_ascii=False))
                return 0
            if not args.check_sources:
                raise UsageError("use 'rules --check-sources' ou 'rules --list'")
            res = check_sources.check_sources(out, args.ttl_days, args.force, args.offline)
            stale = [u for u, e in res["sources"].items() if e.get("changed_since_baseline")]
            errs = {u: e.get("error") for u, e in res["sources"].items() if e.get("error")}
            payload = {"summary": res["summary"], "rules_consulted_at": res["rules_consulted_at"], "changed_since_baseline": stale,
                       "errors": errs, "cache_file": res["cache_file"]}
            print(json.dumps(payload, ensure_ascii=False, indent=None if args.json else 2))
            return 1 if stale or errs else 0
        if args.cmd == "audit":
            mode = "quick" if args.quick else "policy" if args.policy else "full"
            if not (args.project or args.ipa or args.app):
                raise UsageError("informe <project-dir>, --ipa ou --app")
            if args.ipa and args.app:
                raise UsageError("use --ipa ou --app, não ambos")
            project, ipa, app = args.project, args.ipa, args.app
        elif args.cmd == "gate":
            mode, project, ipa, app = "gate", args.release, args.ipa, args.app
            if ipa and app:
                raise UsageError("use --ipa ou --app, não ambos")
        else:
            ap.print_help(sys.stderr)
            return USAGE
        name = (project or ipa or app).resolve().name
        out = args.output_dir or default_out(name)
        report, errors, jp, mp = run_audit(mode=mode, project=project, ipa=ipa, app=app, out_dir=out,
                                           asc_metadata=args.asc_metadata, network=args.network, use_cache=not args.no_cache)
        s = summary(report, errors, jp, mp)
        if args.json:
            print(json.dumps(s, ensure_ascii=False, separators=(",", ":")))
        else:
            print(f"FINAL STATUS: {s['final_status']}  ({', '.join(f'{k} {v}' for k, v in s['counts'].items() if v)})")
            for k, v in s["sections"].items():
                print(f"  {k}: {v}")
            for f in s["top_findings"]:
                print(f"  [{f['severity']}] {f['rule_id']} {f['title']} ({f['component']})")
            print(f"Relatórios: {jp} | {mp}")
            if errors:
                print(f"ERROS DE SCHEMA: {errors[:5]}", file=sys.stderr)
            print("READY não garante aprovação da Apple.")
        return EXIT[report["final_status"]]
    except UsageError as exc:
        print(f"erro de uso: {exc}", file=sys.stderr)
        return USAGE


if __name__ == "__main__":
    raise SystemExit(main())
