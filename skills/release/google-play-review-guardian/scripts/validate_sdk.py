"""Target / min SDK validation driven by rule data (rules/technical-rules.json).

No API level, date or deadline is hard-coded here: the applicable minimum is
selected from `TECH-TARGET-API-LEVEL.requirements` by form factor, submission
type and audit date.
"""
from __future__ import annotations

import datetime as _dt

from common import (BLOCKER, INFO, NOT_VERIFIED, PASS, WARNING, FindingFactory, parse_date)

FORM_FACTORS = ("phone/tablet", "wear", "tv", "automotive", "xr")
SUBMISSIONS = ("new_app", "app_update", "existing_app")


def detect_form_factor(info: dict | None) -> tuple[str, str]:
    """Heuristic from <uses-feature>. Returns (form_factor, evidence)."""
    if not info:
        return "phone/tablet", "padrão (sem manifest)"
    feats = {f.get("name"): f.get("required") for f in info.get("features", [])}
    if "android.hardware.type.watch" in feats:
        return "wear", "uses-feature android.hardware.type.watch"
    if "android.hardware.type.automotive" in feats:
        return "automotive", "uses-feature android.hardware.type.automotive"
    if feats.get("android.software.leanback") is True:
        return "tv", "uses-feature android.software.leanback required=true"
    if "android.software.xr.api.openxr" in feats or "android.software.xr.api.spatial" in feats:
        return "xr", "uses-feature android.software.xr.*"
    return "phone/tablet", "nenhum uses-feature de outro formato"


def applicable_requirement(rule: dict, form_factor: str, submission: str, as_of: _dt.date) -> tuple[dict | None, dict | None]:
    """Return (current_requirement, next_requirement) from rule data."""
    reqs = [r for r in rule.get("requirements", [])
            if r["form_factor"] == form_factor and submission in r["submission"]]
    current = [r for r in reqs if parse_date(r["effective_from"]) and parse_date(r["effective_from"]) <= as_of]
    future = [r for r in reqs if parse_date(r["effective_from"]) and parse_date(r["effective_from"]) > as_of]
    cur = max(current, key=lambda r: (r["min_target_api"], r["effective_from"]), default=None)
    nxt = min(future, key=lambda r: r["effective_from"], default=None)
    return cur, nxt


def validate_target_sdk(target_sdk: int | None, *, rules: dict, F: FindingFactory, component: str,
                        form_factor: str = "phone/tablet", submission: str | None = None,
                        as_of: _dt.date | None = None, extension_granted: bool = False,
                        method: str = "deterministic", confidence: str = "high", source_note: str = "") -> list:
    as_of = as_of or _dt.date.today()
    fs = []
    rid = "TECH-TARGET-API-LEVEL"
    rule = rules[rid]
    if target_sdk is None:
        fs.append(F(rid, NOT_VERIFIED, "target_sdk", "targetSdkVersion não determinado",
                    component=component, evidence=source_note or "targetSdkVersion ausente/não resolvido", method="manual",
                    recommendation="Audite o artefato release (.aab) para obter o targetSdk efetivo do manifest mesclado."))
        return fs
    subs = [submission] if submission else ["new_app", "app_update"]
    worst = None
    for sub in subs:
        if sub == "existing_app":
            rule_used = rules["TECH-TARGET-API-AVAILABILITY"]
            cur, nxt = applicable_requirement(rule_used, form_factor, sub, as_of)
        else:
            rule_used = rule
            cur, nxt = applicable_requirement(rule, form_factor, sub, as_of)
        if cur is None:
            continue
        if worst is None or cur["min_target_api"] > worst[0]["min_target_api"]:
            worst = (cur, nxt, sub, rule_used)
    if worst is None:
        fs.append(F(rid, NOT_VERIFIED, "target_sdk", "Nenhuma exigência de target API encontrada nos dados de regra",
                    component=component, evidence=f"form_factor={form_factor} data={as_of}", method="manual",
                    recommendation="Atualize rules/technical-rules.json com `guardian.py rules --check-sources` e revise a página oficial."))
        return fs
    cur, nxt, sub, rule_used = worst
    req = cur["min_target_api"]
    sub_label = submission or "new_app/app_update (tipo de envio não informado)"
    evidence = (f"targetSdk={target_sdk}; mínimo={req} ({cur.get('android_version', '')}) para {form_factor}, "
                f"{sub_label}, vigente desde {cur['effective_from']} [regra {rule_used['id']}, consultada {rule_used['consulted_at']}]")
    if source_note:
        evidence += f"; fonte: {source_note}"
    if target_sdk >= req:
        fs.append(F(rule_used["id"], PASS, "target_sdk", "Requisito de Target SDK atendido",
                    component=component, evidence=evidence, method=method, confidence=confidence))
    else:
        ext = parse_date(cur.get("extension_until"))
        rec = (f"Defina targetSdkVersion {req} ou superior (android/app/build.gradle: targetSdk = {req}; em Flutter atualize o SDK "
               f"ou fixe `targetSdkVersion {req}`) e teste as mudanças de comportamento da versão.")
        if sub == "existing_app":
            sev = WARNING
            title = "App existente abaixo do target de disponibilidade"
        elif extension_granted and ext and as_of < ext:
            sev = WARNING
            title = f"Target SDK abaixo do mínimo; extensão informada até {cur['extension_until']}"
        else:
            sev = BLOCKER
            title = "Target SDK abaixo do mínimo exigido para envio"
            if ext and as_of < ext:
                rec += f" Se precisar de prazo, solicite a extensão até {cur['extension_until']} (Policy status no Play Console) e rode novamente com --extension-granted."
        if method != "deterministic" or confidence != "high":
            sev = WARNING if sev == BLOCKER else sev
        fs.append(F(rule_used["id"], sev, "target_sdk", title, component=component, evidence=evidence,
                    method=method, confidence=confidence, recommendation=rec))
    if nxt and target_sdk < nxt["min_target_api"]:
        fs.append(F(rid, INFO, "target_sdk", f"Próxima exigência: API {nxt['min_target_api']} a partir de {nxt['effective_from']}",
                    component=component, evidence=f"targetSdk={target_sdk}", method="deterministic",
                    recommendation="Planeje a migração antes da data."))
    return fs

