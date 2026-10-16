"""Report generation: google-play-audit-report.{md,json} + compact summary."""
from __future__ import annotations

import json
from pathlib import Path

from common import (BLOCKER, CRITICAL, EXIT_CODES, INFO, NOT_APPLICABLE, NOT_VERIFIED, PASS, SECTIONS,
                    SEVERITY_ORDER, TOOL_NAME, TOOL_VERSION, WARNING, Finding, final_status, now_iso,
                    redact, section_status)

SEP = "━" * 32
MD_NAME = "google-play-audit-report.md"
JSON_NAME = "google-play-audit-report.json"


def assign_ids(findings: list[Finding]) -> list[Finding]:
    findings.sort(key=lambda f: (SEVERITY_ORDER[f.severity], list(SECTIONS).index(f.section), f.rule_id, f.component))
    for i, f in enumerate(findings, 1):
        f.id = f"GPRG-{i:03d}"
    return findings


def dedupe(findings: list[Finding]) -> list[Finding]:
    seen, out = set(), []
    for f in findings:
        key = (f.rule_id, f.severity, f.title, f.component, f.evidence)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def build_report(*, mode: str, target: dict, app: dict, findings: list[Finding], coverage: dict,
                 tools: dict, rules_meta: dict, inventory: dict, limitations: list[str], cache_stats: dict) -> dict:
    findings = assign_ids(dedupe(findings))
    sections = {}
    for key, label in SECTIONS.items():
        sec = [f for f in findings if f.section == key]
        sections[key] = {"label": label, "status": section_status(sec), "counts": {s: sum(1 for f in sec if f.severity == s) for s in SEVERITY_ORDER if any(f.severity == s for f in sec)}}
    status = final_status(findings)
    counts = {s: sum(1 for f in findings if f.severity == s) for s in SEVERITY_ORDER}
    actions = recommended_actions(findings)
    report = {
        "schema_version": "1.0",
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "generated_at": now_iso(),
        "mode": mode,
        "target": target,
        "app": app,
        "final_status": status,
        "exit_code": EXIT_CODES[status],
        "status_disclaimer": "READY significa apenas ausência de bloqueios nas verificações executadas; não garante aprovação pelo Google Play.",
        "sections": sections,
        "counts": counts,
        "coverage": coverage,
        "tools": tools,
        "rules": rules_meta,
        "inventory": inventory,
        "findings": [f.to_dict() for f in findings],
        "recommended_actions": actions,
        "limitations": limitations,
        "cache": cache_stats,
    }
    report["summary"] = compact_summary(report)
    return json.loads(redact(json.dumps(report, ensure_ascii=False)))


def recommended_actions(findings: list[Finding]) -> list[str]:
    acts, seen = [], set()
    for f in findings:
        if f.severity in (BLOCKER, CRITICAL, WARNING, NOT_VERIFIED) and f.recommendation and f.recommendation != "-":
            key = (f.rule_id, f.recommendation)
            if key in seen:
                continue
            seen.add(key)
            acts.append(f"[{f.severity}] {f.rule_id}: {f.recommendation}")
    acts.append("Execute novamente a auditoria após as correções (gate --release).")
    return acts[:40]


def compact_summary(report: dict) -> dict:
    top = [f for f in report["findings"] if f["severity"] in (BLOCKER, CRITICAL, WARNING)][:10]
    return {
        "status": report["final_status"],
        "exit_code": report["exit_code"],
        "mode": report["mode"],
        "package": report["app"].get("package"),
        "version": f"{report['app'].get('version_name')} ({report['app'].get('version_code')})",
        "target_sdk": report["app"].get("target_sdk"),
        "counts": {k: v for k, v in report["counts"].items() if v},
        "sections": {k: v["status"] for k, v in report["sections"].items()},
        "top_findings": [{"id": f["id"], "severity": f["severity"], "rule_id": f["rule_id"], "title": f["title"]} for f in top],
        "not_verified_mandatory": sum(1 for f in report["findings"] if f["severity"] == NOT_VERIFIED and f["requirement_level"] == "mandatory"),
    }


def render_markdown(r: dict) -> str:
    a = r["app"]
    L = ["# Google Play Review Guardian — relatório de auditoria", "", "```text", "GOOGLE PLAY REVIEW GUARDIAN", SEP, "",
         f"Application: {a.get('label') or '-'}", f"Package: {a.get('package') or '-'}",
         f"Version: {a.get('version_name') or '-'}", f"Version Code: {a.get('version_code') or '-'}",
         f"Min/Target/Compile SDK: {a.get('min_sdk') or '-'} / {a.get('target_sdk') or '-'} / {a.get('compile_sdk') or '-'}",
         f"Mode: {r['mode']}   Generated: {r['generated_at']}", ""]
    for key in ("android_build", "target_sdk", "page_size_16kb", "signing", "security", "permissions", "data_safety",
                "billing", "play_console", "testing"):
        s = r["sections"][key]
        L += [s["label"], f"Status: {s['status']}", ""]
    L += [SEP, f"FINAL STATUS: {r['final_status']}", SEP, "", "FINDINGS:", ""]
    for f in r["findings"]:
        if f["severity"] in (NOT_APPLICABLE,) :
            continue
        L.append(f"[{f['severity']}] {f['title']}.")
    L += ["", SEP, "", "RECOMMENDED ACTIONS:", ""]
    for i, act in enumerate(r["recommended_actions"], 1):
        L.append(f"{i}. {act}")
    L += ["```", "", f"> {r['status_disclaimer']}", ""]
    L += ["## Alvo e cobertura", "", f"- Alvo: `{json.dumps(r['target'], ensure_ascii=False)}`"]
    for k, v in r["coverage"].items():
        L.append(f"- {k}: {v}")
    L += ["", "## Ferramentas", "", "| Ferramenta | Disponível | Caminho/motivo |", "|---|---|---|"]
    for name, t in sorted(r["tools"].items()):
        L.append(f"| {name} | {'sim' if t['available'] else 'não'} | {t.get('path') or ''} {t.get('reason') or ''} |")
    L += ["", "## Findings detalhados", "", "| ID | Severidade | Regra | Componente | Evidência | Método | Confiança | Recomendação | Fonte (consulta) |",
          "|---|---|---|---|---|---|---|---|---|"]
    esc = lambda s: str(s).replace("|", "\\|").replace("\n", " ")
    for f in r["findings"]:
        L.append(f"| {f['id']} | {f['severity']} | {f['rule_id']} ({f['requirement_level']}) | {esc(f['component'])} | "
                 f"{esc(f['evidence'])} | {f['verification_method']} | {f['confidence']} | {esc(f['recommendation'])}"
                 f"{' (auto-fixável)' if f['auto_fixable'] else ''} | [{f['official_url'].split('//')[-1][:48]}]({f['official_url']}) ({f['consulted_at']}) |")
    inv = r.get("inventory", {})
    if inv.get("permissions"):
        L += ["", "## Permissões", "", "| Permissão | Regra | Origem provável | Declarada em |", "|---|---|---|---|"]
        for p in inv["permissions"]:
            L.append(f"| {p['permission']} | {p.get('rule_id') or '-'} | {esc(p['origin_hint'])} | {p['declared_in']} |")
    if inv.get("native_libs"):
        L += ["", "## Bibliotecas nativas", "", "| Biblioteca | ABI | ELF 16 KB | Detalhe | Offset ZIP 16 KB |", "|---|---|---|---|---|"]
        for l in inv["native_libs"]:
            L.append(f"| {l['name']} | {l['abi']} | {l.get('elf_16kb')} | {esc(l.get('elf_detail', ''))} [{l.get('elf_method', '')}] | "
                     f"{l.get('zip_16kb', 'n/a' if l.get('compressed') is not None else '-')} |")
    if inv.get("dependencies"):
        L += ["", "## SDKs relevantes para privacidade/cobrança (indícios, não prova de coleta)", "", "| SDK | Versão | Categoria | Tipos a revisar |", "|---|---|---|---|"]
        for d in inv["dependencies"]:
            L.append(f"| {d['name']} | {d.get('version') or '-'} | {d.get('category') or '-'} | {', '.join(d.get('review_data_types', [])) or '-'} |")
    L += ["", "## Regras", "", f"- Base: {r['rules']['count']} regras; digest `{r['rules']['digest'][:16]}`; consulta mais recente {r['rules']['consulted_at']}",
          f"- Status das fontes: {r['rules'].get('sources_status', 'não verificado nesta execução')}", "", "## Limitações", ""]
    L += [f"- {x}" for x in r["limitations"]]
    L.append("")
    return "\n".join(L)


def write_reports(report: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    jp = out_dir / JSON_NAME
    mp = out_dir / MD_NAME
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    mp.write_text(render_markdown(report), encoding="utf-8")
    return mp, jp
