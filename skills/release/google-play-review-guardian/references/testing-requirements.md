# Teste fechado e acesso à produção

Fonte: answer/14151465 (consulta 2026-10-16).

> Developers with personal accounts created after November 13, 2023, must run a closed test for their app with a minimum of 12 testers who have been opted in continuously for at least 14 days.

Dados da regra POL-CLOSED-TESTING (`conditions`): `account_type=personal`, `account_created_after=2023-11-13`, `min_testers=12`, `min_consecutive_days=14`, `requires_production_access_application=true`.

| Entrada | Resultado |
|---|---|
| tipo de conta não informado | NOT_VERIFIED (pendente) |
| `organization` | NOT_APPLICABLE |
| `production_access=true` | NOT_APPLICABLE |
| pessoal criada em/antes de 2023-11-13 | NOT_APPLICABLE |
| pessoal posterior, sem dados do teste | WARNING (teste obrigatório) |
| testers ≥ 12 e dias ≥ 14 (informado) | PASS com confiança `medium` e nota "não verificado no Console" |
| abaixo dos mínimos | WARNING |

Observações oficiais: testadores que saem antes de 14 dias não contam; após cumprir, clique em *Apply for production* e responda as perguntas (teste, app, prontidão); revisão costuma levar até 7 dias.

## Testes funcionais (TECH-FUNCTIONAL-TESTS)

Sempre `NOT_VERIFIED` a menos que o usuário confirme execução: inicialização, crashes/ANR, login/logout, navegação, permissões em runtime, deep links, offline, orientação, layout. Ferramentas: `flutter test integration_test`, Espresso, UI Automator, Android Lint, pre-launch report do Play Console. O auditor nunca instala o app em dispositivo sem autorização explícita.
