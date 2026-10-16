# App Review Guidelines cobertas

Fonte: https://developer.apple.com/app-store/review/guidelines/ ("Last Updated: October 16, 2026"), consultada em 2026-10-16. As citações são resumos fiéis. Confira o texto original antes de responder a uma rejeição.

| Guideline | Requisito (resumo) | Regra(s) | Como o guardian verifica |
|---|---|---|---|
| 1.2 | Apps com UGC precisam de filtro de conteúdo, denúncia com resposta em tempo hábil, bloqueio de usuários abusivos e contato publicado. | REV-UGC-001 | Heurística: procura no código chamadas de publicação/mensagem e sinais de denúncia/bloqueio. Nunca passa de WARNING. |
| 1.5 | App e Support URL devem trazer uma forma fácil de contato. | ASC-META-006 | Metadados do ASC fornecidos pelo usuário. |
| 2.1(a) | Build final, sem placeholders, testado em dispositivo; conta demo (e backend ligado) se houver login. | REV-PLACEHOLDER-001, REV-DEMO-001, BIN-FLUTTER-001 | Literais "lorem ipsum"/"em breve" no código; login detectado gera pendência de conta demo; build Flutter debug (kernel_blob.bin) é CRITICAL. |
| 2.1(b) | IAPs completos, visíveis ao revisor e funcionais; explicar nas notas os que não puderem ser revisados. | REV-IAP-003 | Manual (ASC). |
| 2.3.1(a) | Recursos novos descritos com especificidade nas Notes for Review. | ASC-REVIEW-001 | Metadados do ASC. |
| 2.3.3 / 2.3.4 | Screenshots mostram o app em uso; previews só com capturas do app. | ASC-MEDIA-001 | Manual. |
| 2.3.7 | Nome com até 30 caracteres; metadados sem termos irrelevantes. | ASC-META-001/002/003 | Limites numéricos (determinístico) quando os metadados são fornecidos. |
| 2.5.1 | Apenas APIs públicas; rodar no SO atual. | BIN-ARCH-001 | Mach-O arm64 com plataforma iOS. |
| 2.5.2 | Não baixar/executar código que altere funcionalidades. | REV-CODE-001 | Dependência de code push (heurística). |
| 2.5.4 | Background services só para o propósito declarado. | PLIST-BG-001 | Valores de UIBackgroundModes contra a lista da Apple. |
| 3.1.1 | Desbloqueio de conteúdo digital via IAP; restaurar compras restauráveis ("should"). | REV-IAP-001, REV-IAP-002 | SDKs/URLs de pagamento externo; chamada de restore no código. |
| 3.1.1(a) / 3.1.3 | Links/pagamentos externos só com entitlement/exceções e regiões previstas. | REV-IAP-001 | Heurística. Exige análise humana por região. |
| 3.1.2(a) | Assinatura com valor contínuo, ≥ 7 dias, disponível em todos os dispositivos. | REV-SUB-001 | Manual (ASC). |
| 4.5.4 | Push não pode ser obrigatório para o app funcionar; marketing só com opt-in. | REV-PUSH-001 (configuração) | Só verifica a configuração técnica (aps-environment). O uso de push para marketing é manual. |
| 4.8 | Login social/terceiro para a conta principal exige outra opção equivalente (dados limitados a nome/e-mail, e-mail privado, sem rastreio sem consentimento). | REV-LOGIN-001, REV-LOGIN-002 | Heurística no código (`GoogleSignIn(`, `mutate("google")`…) e entitlement `com.apple.developer.applesignin`. |
| 5.1.1(i) | Link para a política de privacidade no ASC e dentro do app. | PRIV-POLICY-001, ASC-META-005 | URL/tela de política no código (heurística); campo do ASC. |
| 5.1.1(ii) | Purpose strings precisam explicar o uso dos dados. | PRIV-USAGE-001/002 | String vazia = CRITICAL; texto genérico = WARNING. |
| 5.1.1(iii) | Minimização: pedir só o necessário. | PRIV-USAGE-003 | Compara chaves com plugins/código (heurística). |
| 5.1.1(v) | Se o app permite criar conta, precisa permitir excluí-la dentro do app. | REV-ACCTDEL-001 | Heurística: criação de conta x fluxo de exclusão no código. |
| 5.1.2(i) | Rastreamento exige permissão via App Tracking Transparency. | PRIV-ATT-001 | NSPrivacyTracking=true ou ATT vinculado sem NSUserTrackingUsageDescription = CRITICAL. |

Diretrizes não automatizadas (exigem revisão humana): 1.1, 1.3, 1.4, 1.6, 1.7, 2.2, 2.4, 3.2, 4.1–4.3, 4.7, 4.9, 4.10, 5.1.3–5.1.5, 5.2–5.6.
