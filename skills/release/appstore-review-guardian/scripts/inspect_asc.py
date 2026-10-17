"""App Store Connect checks: user-supplied metadata JSON + pending manual checklist.

The ASC API needs an ES256-signed JWT. Python's stdlib has no ECDSA, so API
mode is reported as NOT_VERIFIED (credentials are detected only by presence
of env vars/paths; the key file is never opened or logged).

Isolated use:
  python3 inspect_asc.py --template                 # prints metadata template
  python3 inspect_asc.py --metadata asc.json        # validates supplied metadata
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import BLOCKER, CRITICAL, INFO, NOT_VERIFIED, PASS, WARNING, Collector, Finding, ref  # noqa: E402

TEMPLATE = {
    "_note": "Preencha a partir do App Store Connect. Não inclua senhas reais: use 'provided': true para credenciais de demo.",
    "name": "", "subtitle": "", "keywords": "", "description": "", "promotional_text": "",
    "privacy_policy_url": "", "support_url": "", "category": "",
    "age_rating_answered": None, "app_privacy_details_completed": None, "screenshots_reviewed": None,
    "export_compliance_answered": None, "review_notes": "", "demo_account": {"provided": None},
    "iap_attached_to_version": None, "availability_reviewed": None, "eu_trader_status_verified": None,
}

MANUAL = [
    ("ASC-PRIV-001", "app_privacy_details_completed", "Preencha App Privacy (dados do app e SDKs: OneSignal, Sentry etc.) em App Store Connect > App Privacy."),
    ("ASC-AGE-001", "age_rating_answered", "Responda o novo questionário de Age Rating em App Information."),
    ("ASC-MEDIA-001", "screenshots_reviewed", "Revise screenshots/previews: devem mostrar o app em uso, não só splash/login (2.3.3)."),
    ("ASC-EXPORT-001", "export_compliance_answered", "Responda as perguntas de criptografia ou declare ITSAppUsesNonExemptEncryption no Info.plist."),
    ("ASC-AVAIL-001", "availability_reviewed", "Confirme países/territórios; para a UE, verifique o trader status (DSA)."),
]


def credentials_present(env=None) -> dict:
    keys = ("ASC_KEY_ID", "ASC_ISSUER_ID", "ASC_KEY_PATH")
    # Only the three names are read; the environment is never copied or dumped.
    env = {k: os.environ.get(k, "") for k in keys} if env is None else env
    present = {k: bool(env.get(k)) for k in keys}
    path = env.get("ASC_KEY_PATH")
    present["key_file_exists"] = bool(path) and Path(path).is_file()
    return present


def check_asc(metadata: dict | None, env: dict | None = None) -> tuple[list[Finding], dict]:
    c = Collector()
    lim = ref("asc_limits")
    creds = credentials_present(env)
    facts = {"metadata_supplied": metadata is not None, "api_credentials": creds}
    if all(creds[k] for k in ("ASC_KEY_ID", "ASC_ISSUER_ID", "ASC_KEY_PATH")):
        try:
            import cryptography  # noqa: F401
            reason = "biblioteca 'cryptography' disponível, mas o cliente da API não está implementado nesta versão"
        except ImportError:
            reason = "JWT ES256 exige biblioteca criptográfica fora da stdlib ('cryptography' ausente)"
        c.add("ASC-API-001", NOT_VERIFIED, "asc", f"credenciais ASC detectadas por variáveis de ambiente; {reason}", confidence=0.1,
              recommendation="Exporte os metadados manualmente ('guardian.py asc --template') e passe com --asc-metadata.")

    md = metadata or {}

    def text(k):
        v = md.get(k)
        return v if isinstance(v, str) else ""

    if metadata is None:
        for rid in ("ASC-META-001", "ASC-META-002", "ASC-META-003", "ASC-META-004", "ASC-META-005", "ASC-META-006",
                    "ASC-REVIEW-001"):
            c.add(rid, NOT_VERIFIED, "asc", "metadados do App Store Connect não fornecidos", confidence=0.1,
                  recommendation="Gere o modelo com 'guardian.py asc --template > asc.json', preencha e rode com --asc-metadata asc.json.")
        for rid, _key, rec in MANUAL:
            c.add(rid, NOT_VERIFIED, "asc", "verificação manual pendente no App Store Connect", confidence=0.1, recommendation=rec)
        return c.items, facts

    name = text("name")
    if not (lim["name_min"] <= len(name) <= lim["name_max"]):
        c.add("ASC-META-001", CRITICAL, "asc", f"nome com {len(name)} caracteres (permitido {lim['name_min']}–{lim['name_max']})", confidence=1.0,
              recommendation="Ajuste o nome em App Information.")
    else:
        c.add("ASC-META-001", PASS, "asc", f"nome '{name}' ({len(name)} caracteres)", confidence=1.0)
    sub = text("subtitle")
    if len(sub) > lim["subtitle_max"]:
        c.add("ASC-META-002", CRITICAL, "asc", f"subtítulo com {len(sub)} caracteres (> {lim['subtitle_max']})", confidence=1.0)
    else:
        c.add("ASC-META-002", PASS, "asc", f"subtítulo com {len(sub)} caracteres", confidence=1.0)
    kw = text("keywords")
    kwb = len(kw.encode("utf-8"))
    short = [k.strip() for k in kw.split(",") if k.strip() and len(k.strip()) < lim["keyword_min_chars"]]
    if not kw or kwb > lim["keywords_max_bytes"] or short:
        c.add("ASC-META-003", CRITICAL, "asc", f"keywords: {kwb} bytes; curtas demais: {short}; vazio={not kw}", confidence=1.0,
              recommendation="Até 100 bytes, separadas por vírgula, cada uma com mais de dois caracteres.")
    else:
        c.add("ASC-META-003", PASS, "asc", f"keywords com {kwb} bytes", confidence=1.0)
    desc, promo = text("description"), text("promotional_text")
    probs = []
    if not desc:
        probs.append("descrição vazia")
    if len(desc) > lim["description_max"]:
        probs.append(f"descrição com {len(desc)} caracteres")
    if "<" in desc and ">" in desc:
        probs.append("descrição parece conter HTML")
    if len(promo) > lim["promotional_text_max"]:
        probs.append(f"texto promocional com {len(promo)} caracteres")
    c.add("ASC-META-004", CRITICAL if probs else PASS, "asc", "; ".join(probs) or f"descrição {len(desc)} / promocional {len(promo)} caracteres",
          confidence=0.95)
    for rid, key, sev in (("ASC-META-005", "privacy_policy_url", BLOCKER), ("ASC-META-006", "support_url", CRITICAL)):
        u = text(key)
        if not u:
            c.add(rid, sev, "asc", f"{key} ausente", confidence=1.0, recommendation=f"Informe {key} no App Store Connect.")
        elif not u.startswith("https://"):
            c.add(rid, WARNING, "asc", f"{key} não usa https: {u}", confidence=0.8)
        else:
            c.add(rid, PASS, "asc", f"{key}={u} (acessibilidade não verificada)", confidence=0.8)
    demo = md.get("demo_account") or {}
    notes = text("review_notes")
    if demo.get("provided") is True and notes:
        c.add("ASC-REVIEW-001", INFO, "asc", "notas de revisão e conta demo informadas (declarado pelo usuário; não verificado)", confidence=0.6)
        c.add("REV-DEMO-001", INFO, "asc", "conta demo declarada como fornecida (declarado pelo usuário; não verificado)", confidence=0.6,
              verification_method="manual")
    else:
        c.add("ASC-REVIEW-001", WARNING, "asc", f"notas={'sim' if notes else 'não'}; demo={demo.get('provided')}", confidence=0.7,
              recommendation="Preencha App Review Information com notas específicas e credenciais de demonstração.")
    for rid, key, rec in MANUAL:
        v = md.get(key)
        if v is True:
            c.add(rid, INFO, "asc", f"{key}=true (declarado pelo usuário; não verificado)", confidence=0.5, verification_method="manual")
        elif v is False:
            c.add(rid, WARNING, "asc", f"{key}=false", confidence=0.8, recommendation=rec)
        else:
            c.add(rid, NOT_VERIFIED, "asc", f"{key} não informado", confidence=0.1, recommendation=rec)
    return c.items, facts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--template", action="store_true")
    ap.add_argument("--metadata")
    a = ap.parse_args(argv)
    if a.template:
        print(json.dumps(TEMPLATE, ensure_ascii=False, indent=2))
        return 0
    md = json.loads(Path(a.metadata).read_text(encoding="utf-8")) if a.metadata else None
    findings, facts = check_asc(md)
    print(json.dumps({"facts": facts, "findings": [f.to_dict(i + 1) for i, f in enumerate(findings)]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
