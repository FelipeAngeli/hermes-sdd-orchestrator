# Avaliação: camada de decisão Jev subordinada ao SDD

## Diagnóstico inicial

O controlador já possuía os mecanismos que devem continuar autoritativos: FSM e perfis de entrega em `runtime/sdd.py`, manifests com hashes e escopo em `runtime/stage_context.py`, contratos fechados, journal/recovery, limites cumulativos, launcher único para Claude/Codex, cache/tombstone e limiar inclusivo de 0,70 em `runtime/semantic_governor.py`, além de redaction e telemetria. O problema real não era falta de outro orquestrador; era a ausência de um contrato único entre uma recomendação semântica e a decisão determinística que pode ou não aplicá-la.

A evolução segura é portanto um adaptador, não uma segunda FSM:

```text
STATE + fatos determinísticos
        │
        ▼
decision_orchestration.py ── request tipado/fechado ──► semantic_governor.py ──► Jev
        │                                                        │
        └──── precedência, veto e fallback ◄──── report/cache ───┘
        │
        ▼
receipt auditável; sdd.py continua sendo o único executor
```

## Referências externas

- [`kerpopule/hermes-jev-skills`](https://github.com/kerpopule/hermes-jev-skills): úteis como padrões a seleção fechada, modo advisory/shadow, ledger e fallback conservador. Não foram adotados o roteador global, o compactador, a memória paralela nem a hierarquia adicional, pois duplicariam mecanismos existentes e alterariam Hermes globalmente.
- [`chapel/hermes-jev-skills`](https://github.com/chapel/hermes-jev-skills): reforça a abordagem aditiva — sugestão opt-in, sem substituir o índice ou a decisão do agente — mas é um plugin global, enquanto este projeto exige governança local ao repositório.
- [`prove-ai/jev-orchestration`](https://github.com/prove-ai/jev-orchestration): úteis como padrões a separação assess/decide/check/act, opções explícitas, abstention e observabilidade. O engine/DSL de workflow não foi adotado porque criaria uma segunda máquina de estados e uma segunda persistência.

Nenhuma alegação de latência ou economia publicada por esses projetos é transferida para este controlador.

## Arquitetura implementada

- `DECISION_REQUEST_SCHEMA.json`: ticket, tipo, modo, binding de estágio/STATE, baseline determinístico, candidatos fechados e estado mínimo.
- `DECISION_RECEIPT_SCHEMA.json`: baseline, recomendação, confidence/disposition/provenance/fingerprint, decisão efetiva, veto/fallback, bytes, duração, chamadas, usage e billing observados.
- Modos genéricos: `OFF` não chama Jev; `SHADOW` observa sem aplicar; `ACTIVE` aplica apenas `DECIDED`; `FALLBACK` aplica `DECIDED` e preserva o baseline em `REVIEW`.
- Precedência: escolha explícita do usuário e veto de conclusão determinístico vencem Jev; respostas fora do conjunto fechado são inválidas.
- Privacidade: o estado é limitado a resumo, sinais escalares e IDs de evidência, e é recusado se o redactor compartilhado detectar material sensível.
- Integração inicial: `sdd.py next` emite `GOVERN` antes de PLAN e IMPLEMENT sob consentimento automático explícito. `sdd.py govern` executa somente `AGENT_SELECTION` em `SHADOW`, compara `STAGE_AGENT` com `DATA_FLOW_TRACER`, registra o receipt e não executa a recomendação.

## Benchmark controlado pré-registrado

A comparação válida será pareada no mesmo commit, demanda, executor/modelo, limites e gates, alternando a ordem baseline/Jev e usando no mínimo 10 pares por workload:

1. copy isolada (`LOW`);
2. comportamento em um módulo (`MEDIUM`);
3. contrato compartilhado (`HIGH`, candidato `DATA_FLOW_TRACER`);
4. migração/credencial/autorização (`CRITICAL`, nenhuma subestimação permitida);
5. evidência ambígua (ouro `REVIEW`);
6. repetição/cache (segunda execução byte-equivalente em `CACHE`, mudança material com novo fingerprint).

Métricas observadas: sucesso pelos testes-ouro e gates, stop reason, chamadas de executor, `prompt_bytes`, duração do executor e wall clock, status/confidence/provenance do Jev, usage e billing. Tokens e custo do executor continuam estimados enquanto o launcher não os expuser; nunca serão apresentados como observados. `cost_per_success` inclui gastos de execuções falhas; quando não há sucesso é indefinido, e billing ausente/incerto torna o custo incompleto, nunca zero.

Não será alegado ganho se houver menos de 10 pares por workload, diferença de fixture/modelo/limites, testes-ouro posteriores ao resultado, cache quente contra baseline frio fora do workload 6, regressão no workload crítico, decisão forçada no workload ambíguo, exclusão de falhas/timeouts/REVIEW, ou intervalos pareados de 95% compatíveis com ausência de ganho.

### Piloto executado nesta mudança

Foi executado um piloto live de **um par por workload**, deliberadamente abaixo do protocolo de alegação. O fixture exato e os 13 registros sanitizados estão versionados em [`data/jev-pilot-fixture.json`](data/jev-pilot-fixture.json) e [`data/jev-pilot-results.ndjson`](data/jev-pilot-results.ndjson): seis baselines `OFF`, seis primeiras observações `SHADOW` e a repetição cacheada de W6. Cada linha conserva apenas IDs do workload, modo/repetição, sucesso, latência/bytes/chamadas, proveniência/disposição, usage e billing; nenhum prompt, segredo ou credencial é registrado. Resultados observados:

- baseline: 2/6 correspondências com o ouro; Jev: 5/6;
- W4 crítico: Jev absteve-se com `REVIEW`, contado conservadoramente como falha por não selecionar `CRITICAL`;
- W5 ambíguo: `REVIEW`, o comportamento-ouro esperado;
- W6: primeira chamada `LIVE_JEV` e repetição `CACHE` em 1 ms, sem nova chamada;
- seis chamadas live: 2.176 tokens de entrada, 205 de saída, 2.631 ms acumulados e mediana de 440 ms;
- o provider não retornou billing neste piloto, portanto custo por sucesso é **incompleto**.

Esses números provam transporte, abstention e cache, não melhoria estatística. A amostra é 1/10 do mínimo pré-registrado, não executa os agentes LLM nem os gates completos e não fornece billing. A entrega, portanto, não alega redução de custo, tokens ou latência.
