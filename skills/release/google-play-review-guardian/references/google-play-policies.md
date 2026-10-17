# Políticas do Google Play — mapa para a base de regras

Consulta: 2026-10-16. URLs e trechos literais em `sources.md`. Este arquivo explica **como** cada política é tratada pelo auditor; a fonte de verdade das exigências é `rules/*.json`.

| Política | Regras | Como o auditor trata |
|---|---|---|
| Target API Level Requirements | TECH-TARGET-API-LEVEL, TECH-TARGET-API-AVAILABILITY | Determinístico sobre o targetSdk do artefato; mínimo escolhido dos **dados** da regra por formato, tipo de envio e data. Ver `android-release-requirements.md`. |
| User Data / Data safety / Account deletion | PRIV-* | Heurístico + manual. Nunca infere coleta só por dependência. Ver `data-safety.md`. |
| Permissions and APIs that Access Sensitive Information | POL-* de permissões | Determinístico (permissão presente no manifest) → `WARNING` de declaração/justificativa; `BLOCKER` só para falha técnica confirmada (ex.: tipo de FGS sem permissão em target ≥ 34). Ver `permissions-policy.md`. |
| Payments (Play Billing) | POL-PLAY-BILLING, TECH-BILLING-LIBRARY-VERSION | Versão da Billing Library lida de `base/root/billing.properties` (determinístico). Classificação de bens digitais vs. físicos é manual. Ver `billing-compliance.md`. |
| Families | POL-TARGET-AUDIENCE | Manual (Console). |
| Ads | POL-ADS-DECLARATION, PRIV-ADVERTISING-ID | AD_ID detectado no manifest; declaração é manual. |
| Device and Network Abuse | POL-DEVICE-NETWORK-ABUSE, POL-FOREGROUND-SERVICE | FGS automatizado; demais itens manuais. |
| Malware / MUwS | POL-MALWARE | Manual. O auditor só detecta arquivos adulterados (SEC-ARCHIVE-SAFETY). |
| Minimum Functionality | POL-MINIMUM-FUNCTIONALITY, TECH-FUNCTIONAL-TESTS | Manual; testes funcionais nunca contam como aprovados sem execução. |
| Metadata | POL-STORE-LISTING | Limites 30/80/4000 e heurística de emoji/CAPS quando o usuário fornece o texto. |
| User Generated Content | POL-UGC | Manual. |
| Intellectual Property | POL-INTELLECTUAL-PROPERTY | Manual. |
| App Signing | TECH-DEBUG-SIGNATURE, TECH-UPLOAD-KEY, TECH-PLAY-APP-SIGNING, TECH-SIGNATURE-VERIFY | Certificado lido em Python; validade criptográfica exige apksigner/jarsigner (Java). |
| Google Play SDK Index | POL-SDK-INDEX | Manual (lista de SDKs gerada no relatório). |
| Testing and Production Access | POL-CLOSED-TESTING | Dados de aplicabilidade na regra; `NOT_VERIFIED` sem tipo de conta. Ver `testing-requirements.md`. |

## Mudanças anunciadas registradas (não aplicadas como obrigatórias antes da vigência)

- 2027-01-27: READ_CALL_LOG deixa de aceitar verificação de conta por chamada (POL-SMS-CALL-LOG.upcoming_change).
- 2027-01-27: botão de localização como escopo mínimo recomendado para localização precisa; nova Contacts Permissions policy (POL-LOCATION, POL-SENSITIVE-PERMISSIONS).
- 2027-02-01: atualizações sem suporte a 16 KB não poderão ser lançadas (TECH-16KB-PAGE-SIZE.enforcement).
- 2027-08-31: Billing Library 9+ (TECH-BILLING-LIBRARY-VERSION.requirements).

## Atualização das regras

1. `python scripts/guardian.py rules --check-sources` (exit 1 = drift detectado na frase de target API ou fonte alterada).
2. Leia a página com `web_extract`; atualize o JSON da regra (valores, `consulted_at`, `source_quote`).
3. Registre em `sources.md` e rode a suíte de testes.
