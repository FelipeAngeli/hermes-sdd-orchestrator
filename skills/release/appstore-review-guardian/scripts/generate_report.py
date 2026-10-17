"""Build, validate and render the audit report (JSON + Markdown).

Isolated use (re-render an existing JSON report):
  python3 generate_report.py <appstore-audit-report.json> [--md out.md] [--validate]
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    BLOCKER, CRITICAL, INFO, NON_DETERMINISTIC, NOT_APPLICABLE, NOT_VERIFIED, PASS, SCHEMA_PATH, SEVERITIES, TOOL_NAME,
    TOOL_VERSION, WARNING, Finding, ensure_private_dir, now_iso, redact, rules, write_private,
)

DISCLAIMER = ("Este relatório é uma auditoria preventiva automatizada. READY significa apenas que nenhuma falha bloqueante foi "
              "encontrada nas verificações executadas; não é garantia nem probabilidade de aprovação pela Apple. "
              "A decisão final é sempre do App Review.")
READINESS_NOTE = ("Indicadores de prontidão = proporção de verificações executadas que passaram (conformity) e proporção de "
                  "verificações que puderam ser executadas (coverage). Não são probabilidade de aprovação.")
SECTION_LABELS = {"technical": "AUDITORIA TÉCNICA", "privacy": "CONFORMIDADE DE PRIVACIDADE",
                  "app_review": "CONFORMIDADE APP REVIEW", "submission": "APP STORE CONNECT / SUBMISSÃO"}
SEV_ORDER = {s: i for i, s in enumerate(SEVERITIES)}


def _counts(items) -> dict:
    c = {s: 0 for s in SEVERITIES}
    for f in items:
        c[f["severity"]] += 1
    return c


def section_status(items) -> str:
    c = _counts(items)
    if c[BLOCKER]:
        return "BLOCKED"
    if c[CRITICAL]:
        return "CRITICAL"
    if c[WARNING]:
        return "WARNING"
    if c[PASS] and c[NOT_VERIFIED]:
        return "PASS_WITH_PENDING"
    if c[PASS]:
        return "PASS"
    if c[NOT_VERIFIED]:
        return "NOT_VERIFIED"
    if c[NOT_APPLICABLE] and not c[INFO]:
        return "NOT_APPLICABLE"
    return "NOT_VERIFIED"


def indicator(items, label) -> dict:
    c = _counts(items)
    judged = c[PASS] + c[BLOCKER] + c[CRITICAL] + c[WARNING]
    total = judged + c[NOT_VERIFIED]
    return {"label": label,
            "conformity_pct": round(100 * c[PASS] / judged) if judged else None,
            "coverage_pct": round(100 * judged / total) if total else None,
            "executed": judged, "not_verified": c[NOT_VERIFIED], "blocking": c[BLOCKER] + c[CRITICAL]}


def dedupe(findings: list[Finding]) -> list[Finding]:
    seen = set()
    out = []
    for f in findings:
        k = (f.rule_id, f.component, f.severity, f.evidence, f.file)
        if k in seen:
            continue
        seen.add(k)
        out.append(f)
    return out


def gate_relevant(d: dict) -> bool:
    return d["severity"] == NOT_VERIFIED and d["requirement_level"] == "mandatory"


def decide(findings: list[dict], mode: str, ipa_audited: bool) -> tuple[str, list[str]]:
    """Gate decision. READY requires that every passing verdict came from a deterministic check."""
    reasons = []
    blockers = [f for f in findings if f["severity"] == BLOCKER]
    crit_det = [f for f in findings if f["severity"] == CRITICAL and f["verification_method"] == "deterministic"]
    if blockers or crit_det:
        reasons += [f"{f['severity']} {f['rule_id']}: {f['title']}" for f in blockers + crit_det]
        return "BLOCKED", reasons
    crit = [f for f in findings if f["severity"] == CRITICAL]
    warns = [f for f in findings if f["severity"] == WARNING]
    pend = [f for f in findings if gate_relevant(f)]
    # Defensive: Finding.to_dict never emits these, but a hand-edited or re-rendered report could.
    forged = [f for f in findings if f["severity"] in (PASS, NOT_APPLICABLE) and f["verification_method"] in NON_DETERMINISTIC]
    if crit:
        reasons.append(f"{len(crit)} achado(s) CRITICAL a validar")
    if warns:
        reasons.append(f"{len(warns)} WARNING(s) a validar")
    if pend:
        reasons.append(f"{len(pend)} requisito(s) obrigatório(s) NOT_VERIFIED")
    if forged:
        reasons.append(f"{len(forged)} veredito(s) PASS/NOT_APPLICABLE vindos de verificação heurística/manual (inválido)")
    if mode == "gate" and not ipa_audited:
        reasons.append("nenhum .ipa assinado auditado: assinatura, perfil e entitlements finais não verificados")
    return ("NEEDS_REVIEW" if reasons else "READY"), reasons


def build_report(findings: list[Finding], *, mode: str, target: dict, facts: dict, tools: dict, network: bool,
                 rules_info: dict, cache_info: dict, ipa_audited: bool) -> dict:
    fs = dedupe(findings)
    dicts = [f.to_dict(i + 1) for i, f in enumerate(fs)]
    dicts.sort(key=lambda d: (SEV_ORDER[d["severity"]], d["area"], d["rule_id"]))
    for i, d in enumerate(dicts):
        d["id"] = f"F{i + 1:03d}"
    by_area = {a: [d for d in dicts if (d["area"] if d["area"] != "meta" else "technical") == a] for a in SECTION_LABELS}
    sections = {a: {"label": SECTION_LABELS[a], "status": section_status(v), "counts": _counts(v)} for a, v in by_area.items()}
    readiness = {
        "note": READINESS_NOTE,
        "technical": indicator([d for d in dicts if d["area"] == "technical"], "Conformidade técnica"),
        "apple_guidelines": indicator([d for d in dicts if d["area"] in ("privacy", "app_review")], "Conformidade com diretrizes Apple"),
        "submission_preparation": indicator([d for d in dicts if d["area"] == "submission"], "Preparação da submissão"),
    }
    status, reasons = decide(dicts, mode, ipa_audited)
    pending = [{"finding_id": d["id"], "rule_id": d["rule_id"], "title": d["title"],
                "reason": d["evidence"][:200], "gate_relevant": gate_relevant(d)}
               for d in dicts if d["severity"] == NOT_VERIFIED]
    return {
        "schema_version": 1,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "generated_at": now_iso(),
        "mode": mode,
        "disclaimer": DISCLAIMER,
        "target": {**{k: (redact(v, 400) if isinstance(v, str) and k in ("project", "ipa") else v) for k, v in target.items()}, "facts": facts},
        "rules": {"count": len(rules().rules), **rules_info},
        "environment": {"platform": platform.system().lower(), "python": platform.python_version(), "tools": tools, "network": network},
        "sections": sections,
        "readiness": readiness,
        "final_status": status,
        "gate_reasons": reasons,
        "counts": _counts(dicts),
        "pending": pending,
        "findings": dicts,
        "cache": cache_info,
    }


def validate_report(report: dict) -> list[str] | None:
    """Return list of schema errors, or None when jsonschema is unavailable."""
    try:
        import jsonschema
    except ImportError:
        return None
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    v = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    return [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in v.iter_errors(report)]


def _sev_tag(s: str) -> str:
    return f"[{s}]"


def render_markdown(r: dict) -> str:
    t = r["target"]
    L = []
    L.append("# APP STORE REVIEW GUARDIAN")
    L.append("")
    L.append("```text")
    L.append("APP STORE REVIEW GUARDIAN")
    L.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    L.append("")
    L.append(f"Application: {t.get('app_name') or '—'}")
    L.append(f"Bundle ID: {t.get('bundle_id') or '—'}")
    L.append(f"Version: {t.get('version') or '—'}")
    L.append(f"Build: {t.get('build') or '—'}")
    L.append(f"Modo: {r['mode']}")
    L.append("")
    for key, s in r["sections"].items():
        L.append(s["label"])
        L.append(f"Status: {s['status']}")
        L.append("")
    L.append(f"FINAL STATUS: {r['final_status']}")
    L.append("")
    L.append("Findings:")
    for f in r["findings"]:
        if f["severity"] in (BLOCKER, CRITICAL, WARNING):
            L.append(f"{_sev_tag(f['severity'])} {f['title']} ({f['component']})")
    L.append("```")
    L.append("")
    L.append(f"> {r['disclaimer']}")
    L.append("")
    L.append("## Resumo")
    L.append("")
    L.append(f"- Gerado em: {r['generated_at']} — ferramenta {r['tool']['name']} {r['tool']['version']}")
    L.append(f"- Projeto: `{t.get('project') or '—'}` · IPA/App: `{t.get('ipa') or '—'}`")
    c = r["counts"]
    L.append("- Contagem: " + ", ".join(f"{k} {v}" for k, v in c.items()))
    L.append(f"- Regras: {r['rules']['count']} (consultadas em {r['rules']['rules_consulted_at']}, idade {r['rules']['rules_age_days']} dias)")
    env = r["environment"]
    L.append(f"- Ambiente: {env['platform']}, Python {env['python']}, rede={'sim' if env['network'] else 'não'}, ferramentas: "
             + ", ".join(f"{k}={'sim' if v else 'não'}" for k, v in env["tools"].items()))
    L.append(f"- Cache incremental: {r['cache']['hits']} reutilizado(s), {r['cache']['misses']} recalculado(s)")
    if r.get("gate_reasons"):
        L.append("- Motivos do status: " + "; ".join(r["gate_reasons"][:8]))
    L.append("")
    L.append("## Indicadores de prontidão")
    L.append("")
    L.append(f"_{r['readiness']['note']}_")
    L.append("")
    L.append("| Indicador | Conformidade | Cobertura | Executadas | Não verificadas | Bloqueantes |")
    L.append("|---|---|---|---|---|---|")
    for k in ("technical", "apple_guidelines", "submission_preparation"):
        i = r["readiness"][k]
        fmt = lambda v: "—" if v is None else f"{v}%"  # noqa: E731
        L.append(f"| {i['label']} | {fmt(i['conformity_pct'])} | {fmt(i['coverage_pct'])} | {i['executed']} | {i['not_verified']} | {i['blocking']} |")
    L.append("")
    titles = {BLOCKER: "Bloqueios (BLOCKER)", CRITICAL: "Críticos (CRITICAL)", WARNING: "Alertas (WARNING)", INFO: "Informativos (INFO)"}
    for sev in (BLOCKER, CRITICAL, WARNING, INFO):
        items = [f for f in r["findings"] if f["severity"] == sev]
        if not items:
            continue
        L.append(f"## {titles[sev]}")
        L.append("")
        for f in items:
            g = f" · guideline {f['guideline']}" if f["guideline"] else ""
            L.append(f"### {f['id']} {_sev_tag(sev)} {f['title']}")
            L.append("")
            L.append(f"- Regra: `{f['rule_id']}`{g} · nível: {f['requirement_level']} · método: {f['verification_method']} · confiança: {f['confidence']}")
            L.append(f"- Componente: `{f['component']}`" + (f" · arquivo: `{f['file']}`" if f["file"] else ""))
            L.append(f"- Evidência: {f['evidence']}")
            L.append(f"- Explicação: {f['explanation']}")
            if f["recommendation"]:
                L.append(f"- Recomendação: {f['recommendation']}")
            L.append(f"- Referência oficial: {f['official_url']} (consultada em {f['consulted_at']})")
            if f.get("notes"):
                L.append(f"- Notas: {'; '.join(f['notes'])}")
            L.append("")
    nv = [f for f in r["findings"] if f["severity"] == NOT_VERIFIED]
    if nv:
        L.append("## Pendências (NOT_VERIFIED)")
        L.append("")
        for f in nv:
            mark = "**obrigatório**" if f["requirement_level"] == "mandatory" else "recomendado"
            L.append(f"- [ ] `{f['rule_id']}` {f['title']} ({mark}) — {f['evidence']}" + (f" → {f['recommendation']}" if f["recommendation"] else ""))
        L.append("")
    ok = [f for f in r["findings"] if f["severity"] in (PASS, NOT_APPLICABLE)]
    if ok:
        L.append("## Verificações aprovadas / não aplicáveis")
        L.append("")
        L.append("| Status | Regra | Componente | Evidência |")
        L.append("|---|---|---|---|")
        for f in ok:
            ev = f["evidence"].replace("|", "\\|")[:160]
            L.append(f"| {f['severity']} | `{f['rule_id']}` | {f['component']} | {ev} |")
        L.append("")
    L.append("---")
    L.append(r["disclaimer"])
    L.append("")
    return "\n".join(L)


def write_reports(report: dict, out_dir: Path) -> tuple[Path, Path]:
    ensure_private_dir(out_dir)
    jp = out_dir / "appstore-audit-report.json"
    mp = out_dir / "appstore-audit-report.md"
    write_private(jp, json.dumps(report, ensure_ascii=False, indent=2, default=str))
    write_private(mp, render_markdown(report))
    return jp, mp


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("report_json")
    ap.add_argument("--md")
    ap.add_argument("--validate", action="store_true")
    a = ap.parse_args(argv)
    r = json.loads(Path(a.report_json).read_text(encoding="utf-8"))
    if a.validate:
        errs = validate_report(r)
        print(json.dumps({"schema_errors": errs, "jsonschema_available": errs is not None}, ensure_ascii=False))
        if errs:
            return 1
    if a.md:
        Path(a.md).write_text(render_markdown(r), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
