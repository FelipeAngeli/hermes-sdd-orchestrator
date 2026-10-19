# Catálogo de rejeições comuns → regra → correção

| Motivo típico | Guideline | Regra | Correção |
|---|---|---|---|
| Build com `get-task-allow` / perfil de desenvolvimento | — (assinatura) | ENT-GTA-001, SIGN-003, SIGN-004 | Exportar com perfil App Store (`method: app-store-connect`). |
| Purpose string genérica ou vazia | 5.1.1(ii) | PRIV-USAGE-001/002 | Explicar o uso concreto, ex.: "Usamos suas fotos para definir a foto do perfil." |
| Privacy manifest inválido (e-mail ITMS-91056) | 5.1.1 / TN3181 | PRIV-MANIFEST-002, PRIV-TRACK-001, PRIV-RRAPI-001 | Corrigir tipos/chaves; validar com `plutil -lint`. |
| Required Reason API sem declaração (ITMS-91053) | 5.1.1 | PRIV-RRAPI-002 | Declarar a categoria com o motivo correto no manifest do componente que usa a API. |
| SDK listado sem manifest (ITMS-91061) | 5.1.1 | PRIV-SDK-001 | Atualizar o SDK. |
| Sem exclusão de conta | 5.1.1(v) | REV-ACCTDEL-001 | Fluxo "Excluir conta" no app, sem exigir contato com suporte. |
| Login social sem alternativa equivalente | 4.8 | REV-LOGIN-001/002 | Sign in with Apple com destaque equivalente. |
| Falta conta demo / backend fora do ar | 2.1(a) | REV-DEMO-001, ASC-REVIEW-001 | Credenciais em App Review Information. |
| IAP não encontrado pelo revisor / sem restaurar | 2.1(b), 3.1.1 | REV-IAP-002/003 | Anexar IAPs à versão; botão "Restaurar compras". |
| Pagamento externo para conteúdo digital | 3.1.1 | REV-IAP-001 | Usar IAP ou exceção aplicável (3.1.1(a), 3.1.3). |
| UGC sem denúncia/bloqueio | 1.2 | REV-UGC-001 | Filtro, denúncia, bloqueio e contato. |
| Placeholder / "em breve" | 2.1(a) | REV-PLACEHOLDER-001 | Remover das telas acessíveis. |
| Tracking sem ATT | 5.1.2(i) | PRIV-ATT-001 | ATTrackingManager + NSUserTrackingUsageDescription. |
| SDK/Xcode antigo no upload | Upcoming Requirements | BUILD-SDK-001 | Compilar com Xcode 26+. |
| Deployment target < 13 | Upcoming Requirements | PROJ-DEPLOY-001, PLIST-MINOS-001 | `IPHONEOS_DEPLOYMENT_TARGET >= 13.0`. |
| Política de privacidade ausente | 5.1.1(i) | ASC-META-005, PRIV-POLICY-001 | Link no ASC e no app. |

Os códigos ITMS são citados como referência de quem já submeteu apps. Eles não foram confirmados nas páginas consultadas, e o guardian não depende deles.
