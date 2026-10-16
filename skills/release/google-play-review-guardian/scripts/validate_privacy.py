"""Privacy / Data safety / account deletion / billing heuristics.

Rules: data collection is never inferred from a dependency name alone; findings
are WARNING/NOT_VERIFIED with a manual action, never BLOCKER/CRITICAL, unless
the user-provided declarations contradict confirmed artifact evidence.
"""
from __future__ import annotations

from common import (BLOCKER, INFO, NOT_APPLICABLE, NOT_VERIFIED, PASS, WARNING, FindingFactory)

DATA_CATEGORIES = ("analytics", "crash", "push", "ads", "attribution", "auth", "location", "media", "contacts",
                   "billing_play", "payments_external", "payments_google_pay")


def validate_privacy(deps: list[dict], signals: dict | None, manifest: dict | None, F: FindingFactory,
                     declarations: dict | None = None, component: str = "-") -> list:
    fs = []
    declarations = declarations or {}
    data_sdks = [d for d in deps if d.get("category") in DATA_CATEGORIES and d["category"] not in ("billing_play",)]
    ds = declarations.get("data_safety")
    if data_sdks:
        listing = "; ".join(f"{d['name']} ({d['category']}: revisar {', '.join(d['review_data_types']) or '-'})" for d in data_sdks[:15])
        if ds is None:
            fs.append(F("PRIV-SDK-DATA-DISCLOSURE", WARNING, "data_safety",
                        "SDKs com possível coleta de dados: confirmar declarações do Data safety",
                        component=component, method="heuristic", confidence="medium", evidence=listing,
                        recommendation="Confira a documentação de Data safety de cada SDK (SDK Index) e, se necessário, instrumente o tráfego (ex.: proxy em build de teste) antes de preencher o formulário."))
        else:
            declared = set(ds.get("data_types", []))
            missing = sorted({t for d in data_sdks for t in d["review_data_types"]} - declared)
            fs.append(F("PRIV-SDK-DATA-DISCLOSURE", WARNING if missing else INFO, "data_safety",
                        "Tipos de dados possivelmente não declarados" if missing else "Declarações cobrem os tipos sugeridos pelos SDKs",
                        component=component, method="heuristic", confidence="medium",
                        evidence=("não declarados: " + ", ".join(missing)) if missing else listing,
                        recommendation="Revise o formulário Data safety para os tipos listados." if missing else "-"))
    fs.append(F("PRIV-DATA-SAFETY-FORM", NOT_VERIFIED if ds is None else INFO, "data_safety",
                "Data safety não pôde ser comparado com o Play Console" if ds is None else "Data safety informado pelo usuário (não verificado no Console)",
                component="Play Console > App content > Data safety", method="manual",
                evidence="sem declarações fornecidas (--declarations)" if ds is None else f"{len(ds.get('data_types', []))} tipos declarados",
                recommendation="Exporte/compare o formulário Data safety com o inventário de SDKs deste relatório."))
    app = (manifest or {}).get("application", {}) if manifest else {}
    if app.get("uses_cleartext_traffic") is True:
        fs.append(F("PRIV-DATA-ENCRYPTION-TRANSIT", WARNING, "data_safety", "Cleartext habilitado: revisar 'dados criptografados em trânsito'",
                    component=component, method="deterministic", evidence="usesCleartextTraffic=true",
                    recommendation="Só declare criptografia em trânsito se todo tráfego usar TLS."))
    if signals and signals.get("http_urls"):
        ex = signals["http_urls"][:5]
        fs.append(F("PRIV-DATA-ENCRYPTION-TRANSIT", WARNING, "data_safety", "URLs http:// no código-fonte",
                    component=ex[0]["file"], method="heuristic", confidence="medium",
                    evidence="; ".join(f"{e['file']}:{e['line']} {e['url']}" for e in ex),
                    recommendation="Troque por https:// ou confirme que não transportam dados de usuário."))
    # privacy policy
    pp = declarations.get("privacy_policy_url")
    fs.append(F("PRIV-PRIVACY-POLICY", NOT_VERIFIED, "data_safety",
                "Política de privacidade não verificada" if not pp else "URL da política de privacidade informada (conteúdo não verificado)",
                component="Play Console > Store listing / App content", method="manual",
                evidence=f"privacy_policy_url={pp}" if pp else "não informada",
                recommendation="Garanta link válido no store listing e dentro do app, cobrindo coleta, uso, compartilhamento, retenção e exclusão."))
    # account deletion
    creates = (signals or {}).get("account_creation", [])
    deletes = (signals or {}).get("account_deletion", [])
    acc_flag = declarations.get("account_creation")
    if acc_flag is False:
        fs.append(F("PRIV-ACCOUNT-DELETION", NOT_APPLICABLE, "data_safety", "App declarado sem criação de contas",
                    component=component, method="manual", confidence="medium",
                    evidence="declarations.account_creation=false (informado pelo usuário)"))
    elif creates or acc_flag:
        ev = "; ".join(f"{c['file']}:{c['line']} '{c['match']}'" for c in creates[:3]) or "account_creation=true (declarado)"
        web = declarations.get("account_deletion_url")
        if deletes and web:
            fs.append(F("PRIV-ACCOUNT-DELETION", INFO, "data_safety", "Sinais de exclusão no app e URL web informada",
                        component=component, method="heuristic", confidence="medium",
                        evidence=f"criação: {ev}; exclusão: {deletes[0]['file']}:{deletes[0]['line']}; web: {web}",
                        recommendation="Confirme manualmente que a exclusão é efetiva (não apenas desativação) e que o link está no Data safety."))
        else:
            missing = []
            if not deletes:
                missing.append("nenhum sinal de fluxo de exclusão no código")
            if not web:
                missing.append("URL web de exclusão não informada")
            fs.append(F("PRIV-ACCOUNT-DELETION", WARNING, "data_safety", "Criação de conta detectada: verificar exclusão no app e via web",
                        component=component, method="heuristic", confidence="medium",
                        evidence=f"criação: {ev}; " + "; ".join(missing),
                        recommendation="Ofereça caminho no app para excluir conta e dados e um link web funcional; informe a URL no Data safety. Desativação temporária não atende."))
    else:
        fs.append(F("PRIV-ACCOUNT-DELETION", NOT_VERIFIED, "data_safety", "Criação de contas não determinada",
                    component=component, method="manual", evidence="sem código-fonte analisado ou sem sinais",
                    recommendation="Informe declarations.account_creation ou audite o projeto com --full."))
    return fs


def validate_billing(deps: list[dict], signals: dict | None, billing_lib_version: str | None, permissions: list[str],
                     rules: dict, F: FindingFactory, as_of=None, component: str = "-", declarations: dict | None = None) -> list:
    import datetime as _dt
    from common import parse_date
    as_of = as_of or _dt.date.today()
    fs = []
    declarations = declarations or {}
    play = [d for d in deps if d.get("category") == "billing_play"]
    ext = [d for d in deps if d.get("category") == "payments_external"]
    has_perm = "com.android.vending.BILLING" in permissions
    uses_play_billing = bool(play) or has_perm or billing_lib_version is not None
    if billing_lib_version:
        rule = rules["TECH-BILLING-LIBRARY-VERSION"]
        cur = max([r for r in rule["requirements"] if parse_date(r["effective_from"]) <= as_of],
                  key=lambda r: r["min_major"], default=None)
        major = int(billing_lib_version.split(".")[0]) if billing_lib_version.split(".")[0].isdigit() else None
        if cur and major is not None:
            if major >= cur["min_major"]:
                fs.append(F("TECH-BILLING-LIBRARY-VERSION", PASS, "billing", "Play Billing Library em versão suportada",
                            component=component, evidence=f"billing_client={billing_lib_version}; mínimo {cur['min_major']} desde {cur['effective_from']}"))
            else:
                fs.append(F("TECH-BILLING-LIBRARY-VERSION", BLOCKER, "billing", "Play Billing Library abaixo da versão mínima",
                            component=component, evidence=f"billing_client={billing_lib_version}; mínimo {cur['min_major']} desde {cur['effective_from']}",
                            recommendation="Atualize com.android.billingclient (ou in_app_purchase_android) para a versão suportada; extensão possível conforme a página oficial."))
            nxt = min([r for r in rule["requirements"] if parse_date(r["effective_from"]) > as_of], key=lambda r: r["effective_from"], default=None)
            if nxt and major is not None and major < nxt["min_major"]:
                fs.append(F("TECH-BILLING-LIBRARY-VERSION", INFO, "billing", f"Billing Library {nxt['min_major']}+ exigida a partir de {nxt['effective_from']}",
                            component=component, evidence=f"billing_client={billing_lib_version}", recommendation="Planeje a migração."))
    if uses_play_billing:
        fs.append(F("POL-PLAY-BILLING", NOT_VERIFIED, "billing", "Produtos digitais: confirmar uso exclusivo do Play Billing",
                    component=component, method="manual",
                    evidence="Play Billing presente: " + ", ".join([d["name"] for d in play][:5] + (["permissão BILLING"] if has_perm else [])),
                    recommendation="Confirme que bens/serviços digitais são vendidos pelo Play Billing, com preço, renovação automática, testes gratuitos e cancelamento claros."))
    if ext:
        fs.append(F("POL-PLAY-BILLING", WARNING, "billing", "Processador de pagamento externo detectado",
                    component=component, method="heuristic", confidence="medium",
                    evidence=", ".join(d["name"] for d in ext),
                    recommendation="Pagamento externo só é permitido para bens/serviços físicos, P2P, doações isentas etc., ou em programas de faturamento alternativo inscritos. Classifique cada fluxo."))
    if not uses_play_billing and not ext:
        if signals is not None and not (signals.get("purchase_flows") or signals.get("external_payment")):
            fs.append(F("POL-PLAY-BILLING", NOT_APPLICABLE, "billing", "Nenhuma monetização detectada",
                        component=component, method="heuristic", confidence="medium",
                        evidence="sem SDKs de cobrança, permissão BILLING ou fluxos de compra no código"))
        else:
            fs.append(F("POL-PLAY-BILLING", NOT_VERIFIED, "billing", "Monetização não determinada",
                        component=component, method="manual", evidence="código-fonte não analisado ou sinais inconclusivos"))
    return fs
