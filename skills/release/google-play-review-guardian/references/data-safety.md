# Data safety, User Data e exclusão de conta

Fontes: answer/10787469 (Data safety), answer/10144311 (User Data), answer/13327111 (Account deletion), answer/6048248 (Advertising ID). Consulta 2026-10-16.

## Princípios aplicados pelo auditor

1. **Presença de SDK não prova coleta.** O inventário (`inspect_dependencies.py`) lista SDKs e os *tipos de dados a revisar*; o finding é `WARNING` heurístico (PRIV-SDK-DATA-DISCLOSURE) com recomendação de checar a documentação do SDK / SDK Index e, se preciso, instrumentar o tráfego.
2. **Formulário do Console não é legível via API.** PRIV-DATA-SAFETY-FORM fica `NOT_VERIFIED` até o usuário fornecer `declarations.data_safety.data_types` (ou confirmar manualmente).
3. **Criptografia em trânsito**: `usesCleartextTraffic=true`, `cleartextTrafficPermitted="true"` ou URLs `http://` no código geram `WARNING`.
4. **Advertising ID**: AD_ID no manifest → `WARNING` (declarar uso); ausente em target ≥ 33 → `INFO`.

## Exclusão de conta (PRIV-ACCOUNT-DELETION)

Requisito oficial: se o app permite criar conta, deve oferecer (a) caminho no app para excluir conta e dados associados e (b) link web para solicitar exclusão sem reinstalar o app; informar a URL no formulário Data safety. Desativação temporária não atende. Isentos: apps permanentemente privados, gerenciamento de dispositivos empresariais, contas offline. Apps não-mobile (TV, Wear) precisam apenas do link web.

Heurística: padrões de criação (`signUp`, `register`, `criar conta`, `cadastro`) e de exclusão (`deleteAccount`, `excluir conta`, …) em código Dart/Kotlin/Java fora de pastas de teste.

| Sinais | Resultado |
|---|---|
| `declarations.account_creation=false` | NOT_APPLICABLE (informado) |
| criação + exclusão no código + `account_deletion_url` | INFO (confirmar manualmente que exclui de fato) |
| criação sem exclusão ou sem URL web | WARNING |
| sem código analisado | NOT_VERIFIED |

## Formato de `--declarations` (privacidade)

```json
{
  "privacy_policy_url": "https://exemplo.com/privacidade",
  "account_creation": true,
  "account_deletion_url": "https://exemplo.com/excluir-conta",
  "data_safety": {"data_types": ["Email address", "User IDs", "Crash logs", "Device or other IDs"]}
}
```
