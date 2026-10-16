# Catálogo de rejeições comuns e como o auditor as detecta

| Sintoma no Play Console | Causa | Regra / detecção | Correção |
|---|---|---|---|
| "You uploaded a debuggable APK or Android App Bundle" | `debuggable true` no release ou manifest | TECH-DEBUGGABLE (artefato e build.gradle) | Remover `debuggable`/`isDebuggable` do release |
| "signed in debug mode" | certificado `CN=Android Debug` | TECH-DEBUG-SIGNATURE (certificado lido em Python) | Assinar com chave de upload |
| Upload rejeitado por target API | targetSdk abaixo do mínimo | TECH-TARGET-API-LEVEL (dados da regra) | `targetSdk = <mínimo>` e testar mudanças de comportamento |
| Aviso "does not support 16 KB" no App bundle explorer | `.so` com `p_align < 0x4000` ou APK desalinhado | TECH-16KB-PAGE-SIZE / TECH-16KB-ZIP-ALIGN | NDK r28+, AGP ≥ 8.5.1, atualizar SDK que fornece a `.so` |
| "Version code X has already been used" | versionCode repetido | TECH-VERSION-CODE-INCREMENT (manual) + TECH-ARTIFACT-FRESHNESS | Incrementar versionCode |
| App não instala no Android 12+ | componente com intent-filter sem `android:exported` | TECH-EXPORTED-EXPLICIT | Declarar `android:exported` |
| Bloqueio de publicação por declaração pendente | permissão restrita / FGS sem declaração | POL-* de permissões | Formulário de declaração ou remover permissão |
| Rejeição por Data safety divergente | SDK coleta dados não declarados | PRIV-SDK-DATA-DISCLOSURE (indício) | Revisar formulário com docs do SDK |
| Rejeição por exclusão de conta | app cria conta sem caminho de exclusão/link web | PRIV-ACCOUNT-DELETION | Fluxo no app + URL web no Data safety |
| Rejeição de pagamentos | conteúdo digital vendido fora do Play Billing | POL-PLAY-BILLING | Play Billing ou programa alternativo |
| Billing Library desatualizada | versão abaixo do mínimo | TECH-BILLING-LIBRARY-VERSION | Atualizar `in_app_purchase_android`/billingclient |
| Produção desabilitada (conta pessoal nova) | teste fechado incompleto | POL-CLOSED-TESTING | 12 testadores por 14 dias + Apply for production |
| Reviewer não consegue entrar | credenciais ausentes | POL-APP-ACCESS (manual) | Preencher App access |
