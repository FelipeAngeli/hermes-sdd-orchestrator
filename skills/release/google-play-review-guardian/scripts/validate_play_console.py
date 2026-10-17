"""Play Console checks: production access / closed testing, store listing,
declarations and an OPTIONAL read-only Google Play Developer API mode.

Only GET endpoints verified on 2026-10-16 in
https://developers.google.com/android-publisher/api-ref/rest are used:
  GET https://androidpublisher.googleapis.com/androidpublisher/v3/applications/{packageName}/tracks/{track}/releases
  GET https://androidpublisher.googleapis.com/androidpublisher/v3/applications/{packageName}/subscriptions
  GET https://androidpublisher.googleapis.com/androidpublisher/v3/applications/{packageName}/oneTimeProducts
`edits.insert` (POST) is deliberately NOT used: it creates an edit. Store listing,
Data safety, content rating, target audience and app access therefore remain
NOT_VERIFIED with a manual checklist.
OAuth for a service account requires an RS256-signed JWT; the stdlib has no RSA.
If the optional `cryptography` package is not importable the API mode is
NOT_VERIFIED. A pre-obtained access token can be passed in the env var
GPRG_PLAY_ACCESS_TOKEN (e.g. from `gcloud auth print-access-token`).
"""
from __future__ import annotations

import base64
import datetime as _dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from common import (INFO, NOT_APPLICABLE, NOT_VERIFIED, PASS, WARNING, FindingFactory, parse_date)

API_BASE = "https://androidpublisher.googleapis.com/androidpublisher/v3"
SCOPE = "https://www.googleapis.com/auth/androidpublisher"
TOKEN_URL = "https://oauth2.googleapis.com/token"
TRACKS = ("production", "beta", "alpha", "internal")

MANUAL_CHECKLIST = [
    ("POL-STORE-LISTING", "Store listing: título ≤ 30, descrição curta ≤ 80, completa ≤ 4000; ícone 512x512, feature graphic 1024x500, screenshots; sem emojis/CAPS abusivos no título"),
    ("POL-CONTENT-RATING", "Questionário de classificação de conteúdo (IARC) concluído"),
    ("POL-TARGET-AUDIENCE", "Público-alvo e conteúdo declarados (Families se incluir crianças)"),
    ("POL-ADS-DECLARATION", "Declaração 'contém anúncios' coerente com os SDKs"),
    ("POL-APP-ACCESS", "Credenciais/instruções de acesso para revisão se houver login"),
    ("POL-PERMISSIONS-DECLARATION", "Declarações de permissões restritas e de foreground service enviadas"),
    ("PRIV-DATA-SAFETY-FORM", "Formulário Data safety enviado e coerente com SDKs e código"),
]


def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _sign_rs256(private_key_pem: str, message: bytes) -> bytes | None:
    try:
        from cryptography.hazmat.primitives import hashes, serialization  # type: ignore
        from cryptography.hazmat.primitives.asymmetric import padding  # type: ignore
    except ImportError:
        return None
    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    return key.sign(message, padding.PKCS1v15(), hashes.SHA256())  # type: ignore[union-attr]


def get_access_token(service_account_json: Path | None, timeout: int = 30) -> tuple[str | None, str]:
    env = os.environ.get("GPRG_PLAY_ACCESS_TOKEN")
    if env:
        return env, "token de acesso via GPRG_PLAY_ACCESS_TOKEN"
    if not service_account_json:
        return None, "nenhuma credencial fornecida"
    try:
        sa = json.loads(service_account_json.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"service account ilegível: {type(exc).__name__}"
    now = int(time.time())
    header = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = b64url(json.dumps({"iss": sa.get("client_email"), "scope": SCOPE, "aud": TOKEN_URL,
                                "iat": now, "exp": now + 600}).encode())
    signing_input = f"{header}.{claims}".encode()
    sig = _sign_rs256(sa.get("private_key", ""), signing_input)
    del sa
    if sig is None:
        return None, "assinatura RS256 indisponível (pacote opcional `cryptography` ausente; stdlib não implementa RSA)"
    body = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                   "assertion": f"{header}.{claims}.{b64url(sig)}"}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=body), timeout=timeout) as r:
            return json.loads(r.read())["access_token"], "OAuth service account"
    except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
        return None, f"falha ao obter token: {type(exc).__name__}"


def api_get(path: str, token: str, timeout: int = 30) -> tuple[dict | None, str]:
    req = urllib.request.Request(f"{API_BASE}/{path}", headers={"Authorization": f"Bearer {token}"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}"), "ok"
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return None, f"{type(exc).__name__}"


def read_play_api(package: str, service_account_json: Path | None, F: FindingFactory) -> dict:
    out: dict = {"enabled": False, "tracks": {}, "subscriptions": None, "one_time_products": None, "findings": []}
    fs = out["findings"]
    token, how = get_access_token(service_account_json)
    if not token:
        fs.append(F("POL-STORE-LISTING", NOT_VERIFIED, "play_console", "Google Play Developer API não consultada",
                    component="androidpublisher v3", method="manual", evidence=how,
                    recommendation="Opcional: `pip install cryptography` e --play-service-account <json> (somente leitura), ou exporte GPRG_PLAY_ACCESS_TOKEN."))
        return out
    out["enabled"] = True
    q = urllib.parse.quote(package, safe="")
    for track in TRACKS:
        data, status = api_get(f"applications/{q}/tracks/{track}/releases", token)
        out["tracks"][track] = {"status": status, "releases": [
            {k: r.get(k) for k in ("name", "versionCodes", "releaseLifecycleState", "rolloutPercentage")}
            for r in (data or {}).get("releases", [])]} if data is not None else {"status": status}
    subs, s1 = api_get(f"applications/{q}/subscriptions", token)
    otp, s2 = api_get(f"applications/{q}/oneTimeProducts", token)
    out["subscriptions"] = len((subs or {}).get("subscriptions", [])) if subs is not None else s1
    out["one_time_products"] = len((otp or {}).get("oneTimeProducts", [])) if otp is not None else s2
    ok_tracks = [t for t, v in out["tracks"].items() if v.get("status") == "ok"]
    fs.append(F("POL-STORE-LISTING", INFO, "play_console", "Tracks lidos via Google Play Developer API (somente leitura)",
                component="androidpublisher v3", method="deterministic",
                evidence="; ".join(f"{t}: {len(out['tracks'][t].get('releases', []))} release(s)" for t in ok_tracks) or "nenhum track legível",
                recommendation="-"))
    return out


def validate_testing(account: dict | None, F: FindingFactory, as_of: _dt.date | None = None) -> list:
    """Closed testing rule POL-CLOSED-TESTING; applicability is DATA in the rule."""
    rule_id = "POL-CLOSED-TESTING"
    rule = F.rules[rule_id]
    cond = rule["conditions"]
    fs = []
    if not account or not account.get("type"):
        fs.append(F(rule_id, NOT_VERIFIED, "testing", "Exigência de teste fechado pendente (tipo de conta não informado)",
                    component="Play Console > Dashboard", method="manual",
                    evidence="informe --account-type personal|organization e --account-created YYYY-MM-DD",
                    recommendation=f"Se conta pessoal criada após {cond['account_created_after']}: teste fechado com ≥ {cond['min_testers']} testadores por ≥ {cond['min_consecutive_days']} dias consecutivos e solicite acesso à produção."))
        return fs
    created = parse_date(account.get("created"))
    if account.get("production_access") is True:
        fs.append(F(rule_id, NOT_APPLICABLE, "testing", "Conta já possui acesso à produção (informado)",
                    component="Play Console", method="manual", confidence="medium", evidence="production_access=true"))
        return fs
    if account["type"] != cond["account_type"]:
        fs.append(F(rule_id, NOT_APPLICABLE, "testing", "Conta organizacional: exigência de 12 testadores não se aplica",
                    component="Play Console", method="manual", confidence="medium", evidence=f"account_type={account['type']}"))
        return fs
    if created is None:
        fs.append(F(rule_id, NOT_VERIFIED, "testing", "Data de criação da conta pessoal não informada",
                    component="Play Console", method="manual", evidence="account_type=personal",
                    recommendation="Informe --account-created YYYY-MM-DD."))
        return fs
    if created <= parse_date(cond["account_created_after"]):
        fs.append(F(rule_id, NOT_APPLICABLE, "testing", "Conta pessoal anterior ao corte de 13/11/2023",
                    component="Play Console", method="manual", confidence="medium", evidence=f"created={created}"))
        return fs
    testers = account.get("closed_test_testers")
    days = account.get("closed_test_days")
    if testers is None or days is None:
        fs.append(F(rule_id, WARNING, "testing", "Conta pessoal nova: teste fechado obrigatório antes da produção",
                    component="Play Console > Test and release > Closed testing", method="manual", confidence="medium",
                    evidence=f"account_type=personal created={created}; testadores/dias não informados",
                    recommendation=f"Mantenha ≥ {cond['min_testers']} testadores inscritos continuamente por ≥ {cond['min_consecutive_days']} dias e então 'Apply for production'."))
    elif testers >= cond["min_testers"] and days >= cond["min_consecutive_days"]:
        fs.append(F(rule_id, NOT_VERIFIED, "testing", "Critério de teste fechado atendido segundo dados informados — confirmar no Console",
                    component="Play Console", method="manual", confidence="medium",
                    evidence=f"testers={testers} days={days} (informado pelo usuário; não verificado no Console)",
                    recommendation="Solicite acesso à produção no Dashboard; revisão leva em geral até 7 dias."))
    else:
        fs.append(F(rule_id, WARNING, "testing", "Teste fechado ainda não atende o mínimo",
                    component="Play Console", method="manual", confidence="medium",
                    evidence=f"testers={testers}/{cond['min_testers']} days={days}/{cond['min_consecutive_days']}",
                    recommendation="Continue o teste fechado até atingir os mínimos."))
    return fs


def validate_store_listing(listing: dict | None, rules: dict, F: FindingFactory) -> list:
    fs = []
    if not listing:
        for rid, text in MANUAL_CHECKLIST:
            fs.append(F(rid, NOT_VERIFIED, "play_console", text, component="Play Console", method="manual",
                        evidence="não disponível offline/sem declarações", recommendation="Verificar manualmente no Play Console."))
        return fs
    limits = rules["POL-STORE-LISTING"]["limits"]
    for key, limit in limits.items():
        val = listing.get(key)
        if val is None:
            fs.append(F("POL-STORE-LISTING", NOT_VERIFIED, "play_console", f"{key} não informado", component="store listing",
                        method="manual", evidence="ausente nas declarações"))
        elif len(val) > limit:
            fs.append(F("POL-STORE-LISTING", WARNING, "play_console", f"{key} excede {limit} caracteres", component="store listing",
                        evidence=f"{len(val)} caracteres", recommendation=f"Reduza para ≤ {limit} caracteres."))
        else:
            fs.append(F("POL-STORE-LISTING", PASS, "play_console", f"{key} dentro do limite de {limit}", component="store listing",
                        evidence=f"{len(val)} caracteres (informado pelo usuário)"))
    title = listing.get("title") or ""
    if title and (any(ord(ch) > 0x2600 for ch in title) or (title.isupper() and len(title) > 4)):
        fs.append(F("POL-STORE-LISTING", WARNING, "play_console", "Título com emoji ou todo em maiúsculas", component="store listing",
                    method="heuristic", confidence="medium", evidence=title[:40],
                    recommendation="A política de Metadata desaconselha emojis e CAPS fora da marca."))
    for rid, text in MANUAL_CHECKLIST[1:]:
        fs.append(F(rid, NOT_VERIFIED, "play_console", text, component="Play Console", method="manual",
                    evidence="não verificável via API somente leitura", recommendation="Verificar manualmente no Play Console."))
    return fs
