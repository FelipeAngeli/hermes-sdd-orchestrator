# SDD Orchestrator V2.5 — Bounded Loop Policy

## 1. Princípio

O modo BOUNDED_AUTO permite que Hermes execute várias ações consecutivas sem exigir confirmação humana após cada ação.

Autonomia nunca significa execução ilimitada.

Toda rodada deve permanecer limitada por:

- FSM;
- invariantes;
- contracts;
- ownership;
- budgets;
- timeouts;
- gates;
- critérios explícitos de parada;
- human approval.

Não existe UNBOUNDED_AUTO.

O objetivo é:

executar o máximo SEGURO e VERIFICÁVEL possível antes do próximo checkpoint humano.

---

## 2. Modos e perfil schema 2

`LOCAL_DELIVERY` não é um enum de `mode`: é o perfil composto por `schema_version: 2`, `local_delivery` e `loop.mode: BOUNDED_AUTO`. O controller verifica a evidência humana antes do uso desse perfil.

### Perfil LOCAL_DELIVERY (schema 2)

Pedido explícito de entrega local vincula uma autorização única ao ticket, escopo/hash, worktree e limites totais fixos. O controller valida a evidência humana e identidade/STATE real; `bounded_run_driver.py bind` só valida dados fornecidos e nunca fabrica aprovação, muda modo, ativa loop ou executa worker. Cada ação consulta o driver; antes do dispatch recupere journal/rollover, envie JSON inline e execute Codex em foreground sem PTY com prompt curto específico da etapa (incluindo orçamento de investigação).

`REPLAN_REQUIRED` exige novo snapshot → plan → bind, preservando ledger e contadores totais, sem nova pergunta humana. `corrective_retries` e `investigation_expansions` em schema 2 são totais espelhados no ledger; o controller ainda limita retry a um por ação. Pare em drift de scope/worktree, protected, resultado desconhecido, erro não recuperável ou limite global. Efeitos externos (commit/push/PR/Linear/Obsidian/backend/DEV E2E) requerem autorização explícita.

### MANUAL

Executar somente a ação solicitada.

Depois:

PAUSED.

### BOUNDED_AUTO legado (schema 1)

Pode continuar automaticamente enquanto:

- todas as invariantes forem válidas;
- nenhum blocker estiver ativo;
- nenhum human approval for necessário;
- budgets estiverem disponíveis;
- a próxima transição estiver autorizada.

Após cada ação concluída, consultar `.hermes/orchestration/runtime/bounded_run_driver.py next`. Não encerrar o turno enquanto o driver retornar `end_turn: false`. `EXECUTE_NEXT` com recovery `DISPATCH_ALLOWED`, próxima ação no plano, budget disponível e sem blocker/human-required exige despachar essa ação na mesma rodada. `ROLLOVER_REQUIRED` exige rollover, journal pristine IDLE e novo recovery `DISPATCH_ALLOWED` antes de consultar o driver novamente; nunca faça `RELEASED → prepare` direto.

Antes de ativar BOUNDED_AUTO, Hermes deve criar e apresentar um `BOUNDED RUN PREVIEW` para `NEXT_HUMAN_CHECKPOINT`, conforme `.hermes/orchestration/policies/BOUNDED_AUTOMATION.md`. O preview é um plano determinístico, hashado e imutável; não executa ação, não chama modelo, não roda gate, não altera STATE e não ativa o modo. Uma resposta afirmativa inequívoca imediatamente após o preview — por exemplo `sim`, `autorizo`, `pode iniciar` ou `continue` — autoriza exclusivamente o `plan_sha256` do preview mais recente na conversa atual, sem exigir que o usuário copie o hash. A confirmação não é reutilizável para planos futuros. Se STATE, worktree, budgets ou decisão de recovery mudarem *antes da ativação*, o preview é `PLAN_STALE` e precisa de novo preview e nova aprovação. O progresso esperado *durante* a rodada autorizada não invalida o plano; o driver decide CONTINUE ou STOP.

### PAUSED

Nenhuma ação automática nova pode começar.

Requer novo comando do usuário para retomar.

### Proibido

UNBOUNDED_AUTO

Loops infinitos

Recursão entre agentes

Executores iniciando novos orquestradores

Autorização genérica reutilizável para planos futuros

---

## 3. Unidade de iteração

Cada iteração executa UMA ação lógica.

Exemplos válidos:

- SPECIFY;
- validar SPECIFY;
- CLARIFY;
- PLAN;
- validar PLAN;
- IMPACT_ANALYSIS;
- TASKS;
- validar TASKS;
- uma TDD slice;
- focused tests;
- format;
- analyze;
- REVIEW;
- CI.

Exemplo inválido:

"implementar toda a demanda"

como uma única iteração.

---

## 4. Budgets padrão por rodada

Esta seção é somente do legado `BOUNDED_AUTO` schema 1. Seus limites são por rodada e um novo preview/confirmação é exigido para nova rodada; não aplique reset a `LOCAL_DELIVERY` schema 2.

Defina:

max_stage_transitions_per_run: 3

max_executor_calls_per_run: 8

max_corrective_retries_per_action: 1

max_tdd_slices_per_run: 3

max_investigation_expansions_per_stage: 1

max_review_cycles_per_run: 2

max_ci_runs_per_run: 1

max_external_mutations_per_run: 0

Budgets não acumulam entre rodadas.

Quando qualquer budget atingir o limite:

1. concluir a ação atual de forma segura;
2. atualizar STATE;
3. não iniciar nova ação;
4. definir status PAUSED;
5. registrar stop_reason específico.

---

## 5. FSM

A FSM continua:

SPECIFY
→ CLARIFY
→ PLAN
→ TASKS
→ IMPLEMENT
→ TEST
→ REVIEW
→ DONE

CLARIFY pode ser:

SKIPPED

somente quando:

- ausência de ambiguidades estiver comprovada;
- motivo estiver registrado no STATE.

Nenhuma outra etapa pode ser pulada automaticamente.

---

## 6. Transições automáticas

### SPECIFY → CLARIFY

Somente quando:

- EXECUTOR_CONTRACT válido;
- scope verificável;
- paths válidos;
- blockers vazios;
- ownership preservado;
- budget disponível.

### CLARIFY → PLAN

Quando:

- dúvidas resolvidas;

OU

- CLARIFY = SKIPPED com justificativa válida;

e nenhum blocker estiver ativo.

### PLAN → TASKS

Somente quando:

- contrato estruturado válido;
- paths validados;
- símbolos validados;
- IMPACT_ANALYSIS executada quando aplicável;
- nenhuma referência inexistente;
- nenhum blocker ativo.

### TASKS → IMPLEMENT

Somente quando:

- tasks validadas;
- paths reais;
- símbolos reais;
- impact_files conhecidos;
- ownership calculado;
- environment preflight concluído;
- nenhuma escrita em protected_preexisting necessária;
- nenhum blocker ativo.

### IMPLEMENT → TEST

Somente quando:

- todas as slices necessárias estiverem GREEN;
- implementação estiver funcionalmente completa;
- ownership preservado;
- arquivos alterados conhecidos;
- blockers vazios.

Se apenas o budget de slices acabar:

IMPLEMENT permanece IMPLEMENT.

Status:

PAUSED

stop_reason:

TDD_SLICE_BUDGET_REACHED

### TEST → REVIEW

Somente conforme `GATES.md` nesta pasta de políticas.

No mínimo:

focused_tests = PASS
format = PASS
analyze = PASS

### REVIEW → CI

Se project_ci_policy.enabled = true:

review = APPROVED
→ CI

Se project_ci_policy.enabled = false:

review = APPROVED
→ ci = DISABLED_BY_PROJECT_POLICY
→ avaliar DONE

Nunca executar CI quando estiver desabilitado pela política do projeto.

### CI → DONE

Quando CI estiver habilitado:

focused_tests = PASS
format = PASS
analyze = PASS
review = APPROVED
ci = PASS

Quando CI estiver desabilitado:

focused_tests = PASS
format = PASS
analyze = PASS
review = APPROVED
ci = DISABLED_BY_PROJECT_POLICY

Nenhum blocker ativo pode existir.

---

## 7. Condições obrigatórias de parada

Parar imediatamente em:

### Segurança

PREEXISTING_FILE_MODIFIED
OWNERSHIP_VIOLATION
PROTECTED_FILE_REQUIRED
working tree integrity uncertain
destructive action required

### Contrato

CONTRACT_INVALID
INVALID_PATH
INVALID_SYMBOL
resultado impossível de validar

após o retry permitido.

### Investigação

INVESTIGATION_BUDGET_EXCEEDED
contexto insuficiente
fontes de verdade conflitantes

### Executor

EXECUTOR_TIMEOUT
EXECUTOR_UNAVAILABLE sem fallback possível
RETRY_BUDGET_REACHED

### TDD

RED_INVALID
GREEN_FAILED
teste falha por motivo inesperado
mudança de escopo necessária

### Gates

FOCUSED_TESTS_FAILED
FORMAT_FAILED
ANALYZE_FAILED
REVIEW_BLOCKED
CI_FAILED
CI_TIMEOUT

### Ambiente

FVM_ENVIRONMENT
SANDBOX_PERMISSION
ENVIRONMENT_BLOCKED

### Human approval

Parar antes de:

- commit;
- push;
- Linear update;
- Obsidian write;
- Obsidian update;
- backend mutation;
- DEV E2E;
- protected file modification;
- ampliação material de escopo;
- ação destrutiva;
- mudança de contrato externo;
- escolha arquitetural material com alternativas equivalentes.

### Budget

Parar ao atingir qualquer budget da rodada.

---

## 8. PAUSED versus BLOCKED

Use:

PAUSED

quando o sistema está saudável, mas parou por:

- budget;
- checkpoint;
- human approval;
- fim voluntário da rodada.

Use:

BLOCKED

quando existe impedimento técnico ou de segurança.

Exemplo:

stage: IMPLEMENT
status: PAUSED
stop_reason: TDD_SLICE_BUDGET_REACHED

versus:

stage: TEST
status: BLOCKED
stop_reason: ANALYZE_FAILED

---

## 9. Stop reasons oficiais

Valores permitidos:

NONE

STAGE_TRANSITION_BUDGET_REACHED
EXECUTOR_CALL_BUDGET_REACHED
TDD_SLICE_BUDGET_REACHED
INVESTIGATION_BUDGET_REACHED
REVIEW_CYCLE_BUDGET_REACHED
CI_RUN_BUDGET_REACHED

HUMAN_APPROVAL_REQUIRED
EXTERNAL_MUTATION_REQUIRED
PROTECTED_FILE_REQUIRED

OWNERSHIP_VIOLATION
PREEXISTING_FILE_MODIFIED
BASELINE_DRIFT_EXTERNAL

CONTRACT_INVALID
INVALID_PATH
INVALID_SYMBOL

EXECUTOR_TIMEOUT
EXECUTOR_UNAVAILABLE
RETRY_BUDGET_REACHED

RED_INVALID
GREEN_FAILED

FOCUSED_TESTS_FAILED
FORMAT_FAILED
ANALYZE_FAILED

REVIEW_CHANGES_REQUIRED
REVIEW_BLOCKED

CI_FAILED
CI_TIMEOUT
EXTERNAL_CI_FAILURE

ENVIRONMENT_BLOCKED

INVESTIGATION_BUDGET_EXCEEDED

STATE_INCONSISTENT
SCOPE_CHANGE_REQUIRED
USER_REQUESTED_PAUSE
STATE_DESYNC
ARTIFACT_PENDING
ACTION_RECOVERY_REQUIRED
PARENT_EVIDENCE_MISMATCH

UNKNOWN_BLOCKER

Não inventar novos stop_reason durante execução.

Caso novo:

- registrar INCIDENT;
- utilizar UNKNOWN_BLOCKER;
- aguardar revisão da política.

---

## 10. Retry

Padrão:

max_corrective_retries_per_action: 1

Retry só é permitido quando:

- causa conhecida;
- correção determinística;
- ownership continua válido;
- não envolve mutação externa;
- não exige mudança arbitrária de implementação.

Retry apropriado:

executor citou path inexistente
→ Hermes fornece inconsistência objetiva
→ executor corrige UMA vez.

Não fazer retry automático para:

- CI FAIL;
- timeout repetido;
- ownership violation;
- baseline drift;
- falha ambiental desconhecida;
- ação destrutiva.

### Action recovery precondition

Before every executor dispatch, run the diagnostic recovery probe from `ACTION_RECOVERY.md` in this policy folder, inspect the journal, and reread/hash STATE when a prepared state commit exists. `DISPATCH_ALLOWED` permits prepare normally. `RELEASED` with a valid next STATE action requires explicit `rollover`, a reread of the new active journal, and a second recovery probe that returns `DISPATCH_ALLOWED` before prepare; `RELEASED → prepare` directly is prohibited. An existing unclassified final-message artifact must be reconciled into STATE before any redispatch. `VALIDATED` with STATE equal to `expected_after_hash` is `ALREADY_COMMITTED`; with STATE equal to `expected_before_hash` it is `STATE_COMMIT_REQUIRED`; neither permits redispatch. A divergent STATE is `STATE_DESYNC` and BLOCKED. `DISPATCHED` or `PROCESS_FINISHED` without an artifact and with an unknown process result uses `ACTION_RECOVERY_REQUIRED`, never automatic redispatch. `PROCESS_FINISHED` with a recorded process result and no valid artifact is not `RELEASED` and must not be rolled over; after human recovery, `archive-interrupted` preserves the original action as `INTERRUPTED` history and opens a pristine `IDLE` journal so a later retry can prepare a new `action_id` with `parent_action_id`. `RECONCILE_ARTIFACT` requires reconciliation; `WAIT_OR_MANUAL_REVIEW` and `BLOCKED` stop the loop.

---

## 11. Fallback de executor

Claude → Codex continua permitido conforme .hermes.md.

Fallback:

- conta como executor call;
- não reinicia retry budget;
- não reinicia transition budget;
- deve ser registrado no STATE.

É proibido:

Claude → Codex → Claude → Codex.

Se fallback também falhar:

BLOCKED.

---

## 12. TDD slices

Cada IMPLEMENT slice deve conter:

1. task pequena;
2. hipótese;
3. RED;
4. validação do RED;
5. mudança mínima;
6. GREEN;
7. validação do GREEN;
8. atualização do STATE;
9. encerramento do executor.

Nova slice exige:

- GREEN anterior;
- budget;
- ownership válido;
- blocker vazio.

Após 3 slices na mesma rodada:

PAUSE.

Não marcar IMPLEMENT como concluído apenas porque o budget foi atingido.

---

## 13. Investigation budget

Continuam válidos os limites do `.hermes.md`.

Uma expansão por stage:

max_investigation_expansions_per_stage: 1

Expansão deve registrar:

- motivo;
- paths extras;
- novo limite.

Nunca transformar expansão em busca irrestrita.

---

## 14. Gates

GATES.md continua sendo a única fonte de verdade para:

- comandos;
- ordem;
- timeout;
- política de CI;
- condições de bloqueio.

LOOP_POLICY apenas decide se a execução pode continuar automaticamente.

Não duplicar ou substituir a política de gates.

---

## 15. REVIEW

Máximo por rodada:

max_review_cycles_per_run: 2

Se:

REVIEW = APPROVED

Hermes pode seguir conforme project_ci_policy.

Se:

REVIEW = CHANGES_REQUIRED

correções automáticas são permitidas somente quando:

- findings possuem evidência verificável;
- paths estão em agent_owned;
- nenhuma mudança de escopo;
- cabem no TDD slice budget;
- review cycle budget disponível.

Caso contrário:

PAUSE ou BLOCKED.

---

## 16. CI

CI é considerado gate caro.

Se habilitado:

max_ci_runs_per_run: 1

CI FAIL nunca gera retry automático.

CI PASS atualiza STATE imediatamente.

Se CI estiver:

DISABLED_BY_PROJECT_POLICY

não executar `make ci`.

Não transformar:

DISABLED_BY_PROJECT_POLICY

em:

PASS.

---

## 17. Human-in-the-loop

Formato de parada:

HUMAN APPROVAL REQUIRED

stage:
reason:
requested_action:
affected_paths:
risk:
recommended_option:

Hermes não continua até resposta.

---

## 18. Obsidian

Obsidian permanece FORA do loop operacional.

Durante:

SPECIFY
CLARIFY
PLAN
TASKS
IMPLEMENT
TEST

é READ-ONLY.

Após REVIEW/DONE:

Hermes pode preparar:

OBSIDIAN WRITE PROPOSAL

Escrita exige aprovação conforme:

HERMES_OBSIDIAN_PROTOCOL.md

Obsidian nunca é condição obrigatória para DONE.

---

## 19. Commit e push

DONE significa:

engenharia validada.

Não significa:

commit realizado
push realizado

Após DONE, Hermes pode apresentar:

COMMIT PROPOSAL

Push exige aprovação separada.

---

## 20. STATE transacional

Depois de cada ação crítica:

ação
→ resultado
→ validação
→ atualização STATE
→ próxima ação

Nunca executar várias ações críticas e atualizar STATE apenas no final.

---

## 21. Integridade do STATE

Antes de cada iteração:

1. ler STATE;

2. validar:
   - schema;
   - stage;
   - status;
   - resume.next_action;
   - ownership;
   - blockers;
   - loop budgets;

3. verificar chaves YAML duplicadas;

4. confirmar transição permitida.

Se inconsistente:

stop_reason:
STATE_INCONSISTENT

e parar.

Não corrigir automaticamente inconsistência estrutural importante e continuar na mesma iteração.

---

## 22. Baseline drift

Mudança no baseline não implica automaticamente escrita do agente.

Se estado Git divergir:

1. verificar write-set da execução;
2. verificar HEAD/branch;
3. verificar commit externo;
4. verificar restore/reset externo;
5. classificar.

Quando não houver evidência de escrita pelo agente:

usar:

BASELINE_DRIFT_EXTERNAL

e PAUSE.

Não acusar:

PREEXISTING_FILE_MODIFIED

sem evidência objetiva.

---

## 23. Progress reporting

Durante BOUNDED_AUTO:

LOOP STATUS

run_id:
mode:

stage:
status:

executor:
slice:

action:
command:
timeout:
last_result:

budget:
  transitions:
  executor_calls:
  tdd_slices:
  retries:
  review_cycles:
  ci_runs:

next_action:
stop_reason:

Mensagens de progresso devem ser curtas.

Não emitir logs extensos.

---

## 24. Encerramento da rodada

Toda rodada deve terminar com:

LOOP RUN SUMMARY

run_id:

start_stage:
end_stage:

result:
  COMPLETED | PAUSED | BLOCKED

actions_completed:

stage_transitions:

executor_calls:

tdd_slices_completed:

gates_executed:

stop_reason:

human_approval_required:
YES | NO

next_action:

state_updated:
YES | NO

repository_integrity:
PRESERVED | VIOLATED

commit:
NOT PERFORMED

push:
NOT PERFORMED

obsidian_write:
NOT PERFORMED

---

## 25. Regra máxima

Quando houver conflito entre:

velocidade
e
segurança/verificabilidade

escolher:

segurança/verificabilidade.

O objetivo do loop é executar o máximo seguro e verificável possível, não executar o máximo absoluto.
