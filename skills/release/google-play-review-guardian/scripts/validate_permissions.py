"""Permission audit. A permission is never a violation by mere presence:
restricted permissions produce WARNING ("declaration/justification required"),
never BLOCKER, unless combined with confirmed evidence of a technical failure
(e.g. a foreground service type without its required permission on target ≥ 34).
"""
from __future__ import annotations

from common import (BLOCKER, INFO, NOT_APPLICABLE, NOT_VERIFIED, PASS, WARNING, FindingFactory)
from validate_manifest import FGS_PERMISSION

A = "android.permission."

# Hints mapping permissions to likely origin libraries (heuristic, for the report only).
ORIGIN_HINTS = {
    "com.google.android.c2dm.permission.RECEIVE": "Firebase Cloud Messaging / push SDK (ex.: OneSignal)",
    A + "POST_NOTIFICATIONS": "push/notificações (ex.: OneSignal, firebase_messaging, flutter_local_notifications)",
    A + "RECEIVE_BOOT_COMPLETED": "agendamento/push que reagenda após boot",
    A + "USE_BIOMETRIC": "local_auth / androidx.biometric",
    A + "USE_FINGERPRINT": "local_auth (API legada)",
    "com.android.vending.BILLING": "Play Billing Library (in_app_purchase)",
    A + "FOREGROUND_SERVICE_MEDIA_PLAYBACK": "audio_service / just_audio",
    A + "CAMERA": "image_picker / camera",
    A + "RECORD_AUDIO": "gravação de áudio / speech",
    "com.google.android.gms.permission.AD_ID": "SDK de anúncios/analytics (Google Mobile Ads, Firebase Analytics etc.)",
}


def _restricted_map(rules: dict) -> dict[str, str]:
    m = {}
    for rid, rule in rules.items():
        for p in rule.get("permissions", []) or []:
            m.setdefault(p, rid)
    return m


def validate_permissions(info: dict | None, rules: dict, F: FindingFactory, *, component: str,
                         artifact: bool, declarations: dict | None = None) -> dict:
    fs = []
    declarations = declarations or {}
    if not info:
        fs.append(F("POL-PERMISSIONS-DECLARATION", NOT_VERIFIED, "permissions", "Permissões não analisadas (sem manifest)",
                    component=component, evidence="manifest indisponível", method="manual",
                    recommendation="Audite o .aab release para obter o manifest mesclado."))
        return {"permissions": [], "findings": fs}
    perms = [p["name"] for p in info.get("permissions", [])]
    pset = set(perms)
    target = info.get("target_sdk")
    restricted = _restricted_map(rules)
    declared = set(declarations.get("permissions_declared", []) or [])
    table = []

    def origin(p: str) -> str:
        return ORIGIN_HINTS.get(p, "não determinado (verifique o manifest-merger-report)")

    for p in perms:
        rid = restricted.get(p)
        table.append({"permission": p, "rule_id": rid, "origin_hint": origin(p),
                      "declared_in": "manifest mesclado do artefato" if artifact else "AndroidManifest.xml do projeto"})

    # --- restricted / high-risk ---
    for rid in ("POL-SMS-CALL-LOG", "POL-BACKGROUND-LOCATION", "POL-ALL-FILES-ACCESS", "POL-QUERY-ALL-PACKAGES",
                "POL-PHOTO-VIDEO", "POL-REQUEST-INSTALL-PACKAGES"):
        rule = rules[rid]
        hits = [p for p in perms if p in (rule.get("permissions") or [])]
        if not hits:
            continue
        if rid in ("POL-ALL-FILES-ACCESS", "POL-QUERY-ALL-PACKAGES") and target is not None and target < 30:
            fs.append(F(rid, INFO, "permissions", f"{', '.join(h.split('.')[-1] for h in hits)} sem efeito com targetSdk < 30",
                        component=component, evidence=f"targetSdk={target}"))
            continue
        if rid == "POL-PHOTO-VIDEO" and target is not None and target < 33:
            continue
        if all(h in declared for h in hits):
            fs.append(F(rid, INFO, "permissions", f"{rule['name']}: declaração informada pelo usuário",
                        component=component, evidence=", ".join(hits), method="manual", confidence="medium",
                        recommendation="Mantenha a declaração do Play Console coerente com o uso real."))
            continue
        fs.append(F(rid, WARNING, "permissions", f"{rule['name']}: exige justificativa e declaração no Play Console",
                    component=component, evidence="declarada(s): " + ", ".join(hits) + f"; origem provável: {origin(hits[0])}",
                    method="deterministic", confidence="high",
                    recommendation=f"Confirme que é funcionalidade principal; caso contrário remova (ex.: `<uses-permission android:name=\"{hits[0]}\" tools:node=\"remove\"/>` no manifest do app). Se necessária, preencha o Permissions Declaration Form e implemente divulgação destacada."))

    # --- exact alarms ---
    if A + "USE_EXACT_ALARM" in pset:
        fs.append(F("POL-EXACT-ALARM", WARNING, "permissions", "USE_EXACT_ALARM é restrita a apps de alarme/calendário",
                    component=component, evidence=f"USE_EXACT_ALARM declarada; origem provável: {origin(A + 'USE_EXACT_ALARM')}",
                    recommendation="Use SCHEDULE_EXACT_ALARM se o app não for de alarme/timer/calendário; remova USE_EXACT_ALARM com tools:node=\"remove\"."))
    elif A + "SCHEDULE_EXACT_ALARM" in pset:
        fs.append(F("POL-EXACT-ALARM", INFO, "permissions", "SCHEDULE_EXACT_ALARM declarada",
                    component=component, evidence="SCHEDULE_EXACT_ALARM (concedida pelo usuário em Android 14+ para a maioria dos apps)",
                    recommendation="Verifique canScheduleExactAlarms() antes de agendar e ofereça fallback inexato."))

    # --- foreground services ---
    services = [c for c in info.get("components", []) if c["type"] == "service"]
    fgs_services = [s for s in services if s["fgs_types"]]
    if fgs_services or A + "FOREGROUND_SERVICE" in pset:
        missing = []
        for s in fgs_services:
            for t in s["fgs_types"]:
                need = FGS_PERMISSION.get(t)
                if need and A + need not in pset:
                    missing.append(f"{s['name']}:{t} requer {need}")
        types = sorted({t for s in fgs_services for t in s["fgs_types"]})
        if missing and target is not None and target >= 34 and artifact:
            fs.append(F("POL-FOREGROUND-SERVICE", BLOCKER, "permissions", "Tipo de foreground service sem a permissão correspondente",
                        component=component, evidence="; ".join(missing),
                        recommendation="Adicione a permissão FOREGROUND_SERVICE_<TIPO> correspondente no AndroidManifest.xml."))
        elif missing:
            fs.append(F("POL-FOREGROUND-SERVICE", WARNING, "permissions", "Possível permissão de FGS ausente",
                        component=component, evidence="; ".join(missing), confidence="medium",
                        recommendation="Confirme no manifest mesclado (artefato) e adicione FOREGROUND_SERVICE_<TIPO>."))
        if A + "FOREGROUND_SERVICE" in pset and not fgs_services and target is not None and target >= 34:
            fs.append(F("POL-FOREGROUND-SERVICE", WARNING, "permissions", "FOREGROUND_SERVICE sem serviço com foregroundServiceType",
                        component=component, evidence="nenhum <service> com android:foregroundServiceType no manifest",
                        confidence="medium", method="heuristic",
                        recommendation="Declare android:foregroundServiceType em cada serviço em primeiro plano ou remova a permissão."))
        if types:
            fs.append(F("POL-FOREGROUND-SERVICE", WARNING if not declarations.get("fgs_declared") else INFO, "permissions",
                        "Tipos de foreground service exigem declaração no Play Console",
                        component=component, evidence="tipos: " + ", ".join(types) + "; serviços: " + ", ".join(s["name"] or "?" for s in fgs_services),
                        method="deterministic",
                        recommendation="Em Play Console > App content > Foreground service permissions, descreva cada tipo, o impacto se adiado/interrompido e anexe vídeo."))

    # --- accessibility / VPN services ---
    acc = [s for s in services if s.get("permission") == A + "BIND_ACCESSIBILITY_SERVICE"]
    if acc:
        fs.append(F("POL-ACCESSIBILITY-API", WARNING, "permissions", "AccessibilityService declarado",
                    component=component, evidence=", ".join(s["name"] or "?" for s in acc),
                    recommendation="Preencha a declaração de AccessibilityService; só use isAccessibilityTool se o app for ferramenta para pessoas com deficiência; implemente divulgação destacada."))
    vpn = [s for s in services if s.get("permission") == A + "BIND_VPN_SERVICE"]
    if vpn:
        fs.append(F("POL-VPN-SERVICE", WARNING, "permissions", "VpnService declarado",
                    component=component, evidence=", ".join(s["name"] or "?" for s in vpn),
                    recommendation="Confirme caso de uso permitido e preencha a declaração no Play Console."))

    # --- sensitive runtime permissions (informative) ---
    sens = [p for p in perms if p in (rules["POL-SENSITIVE-PERMISSIONS"].get("permissions") or [])]
    if sens:
        fs.append(F("POL-SENSITIVE-PERMISSIONS", INFO, "permissions", "Permissões sensíveis: solicitar em contexto e justificar",
                    component=component, evidence=", ".join(s.replace(A, "") for s in sens), method="heuristic", confidence="medium",
                    recommendation="Garanta que cada permissão sustenta funcionalidade atual descrita no store listing e é pedida em contexto."))
    loc = [p for p in perms if p in (rules["POL-LOCATION"].get("permissions") or [])]
    if loc:
        fs.append(F("POL-LOCATION", INFO, "permissions", "Permissões de localização declaradas",
                    component=component, evidence=", ".join(l.replace(A, "") for l in loc), method="deterministic",
                    recommendation="Use localização aproximada quando suficiente; preveja a política de botão de localização (efetiva 2027-01-27)."))
    if "com.google.android.gms.permission.AD_ID" in pset:
        fs.append(F("PRIV-ADVERTISING-ID", WARNING, "data_safety", "Permissão AD_ID presente: declarar uso do Advertising ID",
                    component=component, evidence="com.google.android.gms.permission.AD_ID no manifest", method="deterministic",
                    recommendation="Responda a pergunta de Advertising ID em App content e reflita no Data safety; remova com tools:node=\"remove\" se não usar."))
    elif target is not None and target >= 33:
        fs.append(F("PRIV-ADVERTISING-ID", INFO, "data_safety", "Sem permissão AD_ID",
                    component=component, evidence="AD_ID ausente: o Advertising ID retornará zeros em target ≥ 33",
                    method="deterministic", recommendation="Declare 'não usa Advertising ID' no Play Console se for o caso."))
    if not any(f.rule_id.startswith("POL-") and f.severity in (WARNING, BLOCKER) for f in fs):
        fs.append(F("POL-PERMISSIONS-DECLARATION", PASS, "permissions", "Nenhuma permissão restrita que exija declaração",
                    component=component, evidence=f"{len(perms)} permissões analisadas; nenhuma das listas restritas da base de regras"))
    return {"permissions": table, "findings": fs}
