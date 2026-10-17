# Checklist do Play Console (manual) e formato de `--declarations`

A Google Play Developer API v3 não expõe para leitura: Data safety, classificação de conteúdo, público-alvo, declaração de anúncios, app access, declarações de permissões/FGS, store listing completo sem criar um *edit*. Por isso estes itens são `NOT_VERIFIED` por padrão e geram ação manual.

| Item | Onde | Regra |
|---|---|---|
| Store listing (título ≤ 30, curta ≤ 80, completa ≤ 4000, ícone, feature graphic, screenshots) | Grow users > Store presence > Main store listing | POL-STORE-LISTING |
| Política de privacidade | App content > Privacy policy | PRIV-PRIVACY-POLICY |
| Data safety (incl. exclusão de conta) | App content > Data safety | PRIV-DATA-SAFETY-FORM, PRIV-ACCOUNT-DELETION |
| Anúncios | App content > Ads | POL-ADS-DECLARATION |
| App access (credenciais de revisão) | App content > App access | POL-APP-ACCESS |
| Content rating | App content > Content ratings | POL-CONTENT-RATING |
| Público-alvo | App content > Target audience and content | POL-TARGET-AUDIENCE |
| Permissões restritas / FGS / Advertising ID | App content | POL-PERMISSIONS-DECLARATION, POL-FOREGROUND-SERVICE, PRIV-ADVERTISING-ID |
| Play App Signing e SHA-256 da app signing key | Test and release > App integrity | TECH-PLAY-APP-SIGNING |
| versionCode maior que o último | Test and release > App bundle explorer | TECH-VERSION-CODE-INCREMENT |
| Teste fechado / acesso à produção | Dashboard | POL-CLOSED-TESTING |

## API somente leitura (opcional)

Endpoints usados (verificados na referência oficial em 2026-10-16), todos `GET`:

- `applications/{packageName}/tracks/{track}/releases` — releases por track (production, beta, alpha, internal)
- `applications/{packageName}/subscriptions`
- `applications/{packageName}/oneTimeProducts`

Autenticação: `--play-service-account sa.json` (exige o pacote opcional `cryptography` para assinar o JWT RS256) ou `GPRG_PLAY_ACCESS_TOKEN` com um token obtido externamente. Sem isso, a skill segue offline e marca `NOT_VERIFIED`.

## `--declarations` (JSON fornecido pelo usuário)

```json
{
  "account": {"type": "personal", "created": "2024-03-01", "production_access": false,
              "closed_test_testers": 12, "closed_test_days": 15},
  "privacy_policy_url": "https://exemplo.com/privacidade",
  "account_creation": true,
  "account_deletion_url": "https://exemplo.com/excluir-conta",
  "data_safety": {"data_types": ["Email address", "Crash logs"]},
  "permissions_declared": ["android.permission.ACCESS_BACKGROUND_LOCATION"],
  "fgs_declared": true,
  "store_listing": {"title": "Meu App", "short_description": "…", "full_description": "…"},
  "manual_confirmations": {"POL-CONTENT-RATING": "IARC concluído em 2026-10-10"}
}
```

`manual_confirmations` rebaixa `NOT_VERIFIED` manuais para `INFO` com confiança `low` e a evidência "confirmação do usuário" — nunca para `PASS`.
