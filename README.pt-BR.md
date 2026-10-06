# Hermes SDD Orchestrator

[English](README.md)

Uma **skill de Orquestrador SDD** reutilizável e nativa do Hermes. Instale-a pelo registro de skills do Hermes; quando necessário, a skill instala dentro do projeto atual um controlador orientado à segurança.

A arquitetura segue o padrão de coleção de skills usado por [Ow1onp/hermes-agent-skills](https://github.com/Ow1onp/hermes-agent-skills): uma árvore `skills/` descobrível, um ponto de entrada `SKILL.md` autocontido, scripts e templates de suporte junto da skill e instalação baseada em registro. O estado de orquestração do projeto de destino permanece local e não rastreado.

**Documentação:** [docs/README.md](docs/README.md) — visão geral, arquitetura, uma página por componente e glossário.

## Início rápido

```bash
# Registra este repositório GitHub como fonte de skills do Hermes.
hermes skills tap add FelipeAngeli/hermes-sdd-orchestrator

# Instala a skill SDD Orchestrator no perfil Hermes ativo.
hermes skills install FelipeAngeli/hermes-sdd-orchestrator/skills/orchestrate/sdd-orchestrator --yes
```

Depois, carregue `/skill sdd-orchestrator` no Hermes. A skill orienta a instalação segura do controlador local ao projeto.

## O que é instalado e onde

```text
Perfil Hermes (uma vez)                       Projeto Git de destino (por projeto)
────────────────────────                      ─────────────────────────────────
~/.hermes/skills/sdd-orchestrator/            .hermes.md
├── SKILL.md                                  .hermes/obsidian.json  # vínculo do vault (versionado)
├── scripts/install_project.py                skills-lock.json       # lock opcional do TypeSafe
├── vendor/typesafe-ai/                       .hermes/orchestration/
│   ├── SKILL.md                              ├── agents/      # briefs de workers por estágio
│   └── LICENSE                               ├── contracts/   # interfaces de workers/revisão
└── templates/                                ├── hooks/       # adaptadores opt-in de eventos Hermes
    └── .hermes/                              ├── policies/    # FSM, gates e recuperação
                                               ├── runtime/     # ferramentas executáveis do controlador
                                               ├── schemas/     # contratos de validação JSON
                                               ├── sub-agents/  # workers especializados
                                               ├── tests/       # testes do protocolo instalado
                                               ├── STATE.md (estado local novo)
                                               ├── PROJECT_SETUP.md (conectividade do orquestrador)
                                               ├── ACTION_JOURNAL.json
                                               └── INCIDENTS.md
                                              .hermes/skills/   # playbooks confiáveis do projeto
                                               ├── sdd-backend-engineering/
                                               ├── sdd-architecture-decisions/
                                               ├── sdd-database-design-migrations/
                                               └── typesafe-ai/ # orientação opcional e revisada para Jev
```

`.hermes/obsidian.json` é a única exceção à regra de não rastreamento: ele vincula o projeto ao vault do Obsidian e é versionado para que a conectividade sobreviva a um clone. Quando uma worktree é inicializada com um vault, `STATE.md`, `ACTION_JOURNAL.json` e `INCIDENTS.md` são movidos para esse vault e separados por worktree — veja `templates/.hermes/orchestration/BOOTSTRAP.md`.

A skill é reutilizável. A configuração do controlador, o setup do projeto, o estado, o registro de incidentes e o journal são locais ao projeto e adicionados somente ao `info/exclude` do Git no repositório de destino. O instalador nunca sobrescreve configuração existente nem modifica arquivos rastreados.

## Skills de engenharia locais ao projeto

Cada instalação inclui três playbooks carregados progressivamente em `.hermes/skills/`: engenharia de backend, decisões de arquitetura e design/migrações de banco de dados. Eles orientam planejamento e implementação, enquanto os subagentes especializados existentes fazem revisão independente. O schema 2 de contexto de estágio vincula o workspace Git canônico e verifica byte a byte os arquivos `SKILL.md` e referências exigidos antes de incluir seus descritores no hash de aprovação da fatia; IMPLEMENT falha de forma fechada quando há orientação extra, ausente ou alterada. A instalação nunca muda skills globais nem a confiança do repositório. Depois de inspecionar os arquivos, execute `hermes skills trust` no repositório de destino e inicie uma nova sessão. Veja [Skills de engenharia locais ao projeto](docs/components/project-local-skills.md).

## Integração opcional TypeSafe/Jev

[Jev](https://docs.typesafe.ai/concepts/system-one) é o principal modelo System One da TypeSafe. Durante o onboarding do projeto, o orquestrador pergunta se o projeto deve receber a skill `typesafe-ai`, local ao repositório, que ensina ao Hermes como projetar julgamentos tipados e probabilidades usando Jev e a API TypeSafe.

Visualize e aplique o opt-in explicitamente:

```bash
python3 <skill-instalada>/scripts/install_project.py \
  --target /caminho/absoluto/do/projeto --typesafe-ai install --json

python3 <skill-instalada>/scripts/install_project.py \
  --target /caminho/absoluto/do/projeto --typesafe-ai install --apply --json

# Consentimento separado para classificações automáticas potencialmente cobradas:
python3 <skill-instalada>/scripts/install_project.py \
  --target /caminho/absoluto/do/projeto --typesafe-ai install \
  --automatic-jev-governance --apply --json
```

Use `--typesafe-ai none --apply` somente quando não houver uma skill TypeSafe presente. O instalador **não** executa o comando oficial `npx skills add typesafe-ai/skills --skill typesafe-ai` e não baixa código. Em vez disso, verifica e copia um snapshot revisado, fixado em um commit imutável do upstream, grava `.hermes/skills/typesafe-ai`, mescla `skills-lock.json` com segurança, preserva entradas não relacionadas e nunca altera skills nem configurações globais do Hermes. O opt-in também cria `.hermes/.env` privado e ignorado, com entradas vazias `TYPESAFE_API_KEY=` e `JEV_AI_API_KEY=`, sem sobrescrever um arquivo existente. Instalações conflitantes, rastreadas, com symlink, incompletas, adulteradas ou de outra origem falham de forma fechada.

O conector verifica a configuração sem rede. Instalar TypeSafe não autoriza chamadas e registra `automatic_semantic_governance: false`; respostas antigas somente de instalação reabrem o onboarding. Apenas `--automatic-jev-governance` junto de preflight READY vira autorização permanente. Fatos exatos continuam determinísticos; classificações semânticas são agrupadas em uma chamada Jev e armazenadas por fingerprint.

```bash
python3 .hermes/orchestration/runtime/typesafe_connector.py preflight --json
python3 .hermes/orchestration/runtime/typesafe_connector.py evaluate --input request.json --json
python3 .hermes/orchestration/runtime/semantic_governor.py decide --input governance.json --project-setup .hermes/orchestration/PROJECT_SETUP.md --json
```

`semantic_governor.py` lê o setup por descritores e exige o objeto exato de consentimento habilitado mais preflight local READY antes da avaliação. Aceita somente classificações limitadas `choice` e `noul`, envia todas em uma requisição, aceita confiança `0.70` ou superior e retorna `REVIEW` em vez de inventar uma decisão incerta ou com falha. Um tombstone durável é gravado antes da fronteira paga; falha na persistência final retorna `REVIEW` com recibo e mantém esse tombstone, impedindo retry automático. Chamadas reais imprimem `JEV EM USO`/`JEV USADO` em stderr; acertos de cache não fazem chamada paga. Essas garantias do governor/cache são somente POSIX: no Windows, `JEV_GOVERNANCE_PLATFORM_UNSUPPORTED` ocorre antes de estado ou rede, sem alegar segurança contra junctions. O `evaluate` bruto continua disponível para pedidos tipados explícitos. Nenhum caminho exibe credenciais ou corpos de erro. Consulte [Skill e instalador](docs/components/skill-and-installer.md#automatic-jev-semantic-governance).

## Painel de progresso no terminal

Durante uma execução SDD, o `runtime/terminal_progress.py` local ao projeto torna explícito no CLI clássico do Hermes: provider ativo, fase atual e posição `N/8`, fases restantes, tempo em cada fase, comandos/dispatches/gates/ações recentes e se o Jev está ativo, com provider, modelo, área de classificação e IDs das decisões. O controlador atualiza o painel antes de cada ação material e em toda transição. Em POSIX, o `TERMINAL_PROGRESS.json` ignorado e modo `0600` usa travessia sem seguir links e lock privado durante cada read-modify-write completo; no Windows, retorna `PROGRESS_PLATFORM_UNSUPPORTED` em vez de oferecer tratamento inseguro de junctions. É apenas estado de apresentação—`STATE.md` e journal continuam autoritativos—e nunca guarda segredos nem raciocínio oculto. Chamadas reais do governor atualizam automaticamente o painel do Jev; acertos de cache não fingem que houve chamada paga. Consulte [Progresso no terminal](docs/components/fsm-and-loop.md#terminal-progress).

## Hooks locais ao repositório (opt-in)

Cada instalação inclui `.hermes/orchestration/hooks/`, em paralelo a `agents/` e `sub-agents/`. Os scripts conectam eventos de shell hooks do Hermes ao runtime existente sem introduzir política SDD global:

- `pre_tool_call` falha de forma fechada para `write_file` e `patch`, validando cada destino direto/V4A contra a fatia IMPLEMENT ativa ou o contêiner de vault vinculado.
- `pre_verify` mantém o turno aberto até que a evidência de aceitação HUMAN/AGENT esteja vinculada ao workspace, HEAD, ticket, estágio, fatia, ação e tentativa atuais.
- `subagent_stop` armazena um digest imutável e não sensível do resultado no histórico autoritativo local ou mantido no vault.
- o `pre_llm_call` opcional injeta um resumo limitado de STATE; STATE ausente, não UTF-8, malformado ou com formato incorreto retorna apenas `SDD state unavailable.` e nunca detalhes do parser ou valores de origem.

Os hooks são instalados **inativos**. Para habilitá-los, use um perfil Hermes dedicado, substitua `<ABSOLUTE_PROJECT_ROOT>` em `hooks/hooks.example.yaml`, mescle o bloco na configuração do perfil e aprove cada par `(event, command)`. Comandos absolutos e entre aspas vinculam o consentimento persistente a um checkout e funcionam quando o caminho contém espaços. O instalador nunca edita `~/.hermes/config.yaml`, SOUL, skills globais ou consentimento. Contratos e limitações completos: [Hooks Hermes locais ao repositório](docs/components/hooks.md).

## Dispatch eficiente e seguro

O ponto de entrada instalado mantém o contexto dos workers pequeno e específico ao estágio, com um único leaf worker por vez e transições de estado controladas pelo controlador. Bundles atualizados são aplicados a novas instalações; o instalador deliberadamente não sobrescreve a configuração de um projeto existente, portanto migre uma instalação existente somente após revisar sua configuração local.

## Subagentes

`pr-reviewer` acompanha toda instalação, portanto qualquer projeto pode usá-lo. **Neste** repositório, ele revisa todo pull request e todas as revisões anteriores desse PR (veja [AGENTS.md](AGENTS.md#pull-request-review)).

`sub-agents/` contém briefs de leaf workers específicos que o controlador pode selecionar quando um estágio precisa de um especialista. Eles são despachados somente pelo controlador, nunca por outro agente, e nunca controlam STATE ou transições. Todos são independentes de linguagem e stack.

| Brief | Estágios | Finalidade |
| --- | --- | --- |
| `project-context-guardian` | SPECIFY, PLAN, IMPLEMENT | Contexto de projeto com cache primeiro, obrigatório antes de PLAN e IMPLEMENT; registra divergências entre código e documentação. |
| `investigator` | SPECIFY, CLARIFY, PLAN | Investigação limitada da base de código antes de uma decisão. |
| `data-flow-tracer` | PLAN, IMPLEMENT | Caminho de uma demanda: UI → estado → serviço → repositório → API e retorno. |
| `impact-analyst` | PLAN, TASKS | Raio de impacto completo de uma mudança proposta de contrato ou comportamento. |
| `tdd-implementer` | IMPLEMENT | Uma fatia vertical autorizada sob RED → mínimo → GREEN estrito. |
| `test-runner` | TEST | Execução focada de testes com comandos e códigos de saída exatos. |
| `code-reviewer` | REVIEW | Mudanças entregues comparadas com requisitos, ownership, testes e gates. |
| `security-reviewer` | REVIEW | Falhas exploráveis e divulgação: segredos, armazenamento, autenticação e logs. |
| `tdd-guardian` | TEST, REVIEW | Verifica se a suíte ficaria vermelha caso a regra quebrasse. |
| `regression-hunter` | TEST, REVIEW | O que funcionava antes e pode ter deixado de funcionar. |
| `api-contract-auditor` | PLAN, REVIEW | Modelos do cliente comparados à especificação e ao servidor implantado. |
| `performance-auditor` | PLAN, REVIEW | Trabalho desnecessário realizado pelo sistema. |
| `architecture-guardian` | PLAN, REVIEW | Violações das regras arquiteturais declaradas pelo próprio projeto. |
| `migration-safety-auditor` | PLAN, REVIEW | Rollout concreto de schema/dados: compatibilidade, backfills, locks, reinício e recuperação. |
| `spec-consistency-guardian` | TASKS, REVIEW | Quebras na cadeia SPEC → PLAN → TASKS → CODE → TESTS. |
| `dependency-auditor` | PLAN, REVIEW | Versões, duplicação, compatibilidade e pacotes sem manutenção. |
| `release-readiness-auditor` | REVIEW | Se a entrega pode sair: READY, BLOCKED ou READY_WITH_RISK. |
| `pr-reviewer` | REVIEW | Um pull request como será integrado — escopo, testes, checks, breaking changes, changelog e commits — além da auditoria de revisões anteriores. Neutro em relação ao host; nunca publica sozinho. |
| `documentation-writer` | IMPLEMENT, REVIEW | Documentação, ADRs, README e diagramas alinhados ao código real. |

Os nove papéis de auditoria retornam findings somente quando há evidência, e cada um é limitado pelo modo de falha específico de seu domínio:

- O **TDD guardian** prova um teste fraco alterando o código de produção e observando quais testes permanecem verdes, pois ler um teste produz uma opinião, enquanto alterá-lo produz um fato.
- O **regression hunter** executa as suítes dos consumidores que a mudança não tocou; um consumidor cujos testes passam sem exercitar o caminho afetado é reportado como risco sem cobertura, não como seguro.
- O **API contract auditor** ordena suas fontes — runtime implantado acima da especificação servida, acima do código do servidor, acima da especificação commitada, com os modelos do cliente por último — em vez de confiar na fonte mais próxima, e nunca inventa um elemento de contrato para fechar uma lacuna.
- O **performance auditor** reporta um custo somente com uma medição ou contagem de operações, informa o tamanho de entrada no qual ele importa e pode concluir que nada merece mudança; um papel recompensado por encontrar problemas produzirá ruído.
- O **architecture guardian** cita a regra declarada pelo projeto por trás de cada violação. Uma convenção não declarada é apresentada como pergunta, nunca imposta, pois toda base de código viola a arquitetura preferida por alguém.
- O **migration safety auditor** reconstrói o rollout ordenado e as combinações intermediárias entre aplicação e schema; SQL gerado e verde não prova preservação de dados, limites de lock, reinício ou recuperação.
- O **spec consistency guardian** percorre SPEC → PLAN → TASKS → CODE → TESTS nas duas direções e nunca infere um requisito ausente: inferir um requisito transformaria escopo não autorizado em escopo justificado retroativamente, justamente a falha que ele deve detectar.
- O **dependency auditor** prefere o que o projeto já usa a qualquer dependência nova e encaminha vulnerabilidades ao security reviewer e violações de camada ao architecture guardian em vez de decidir por eles; dois papéis sobre o mesmo domínio fazem cada um presumir que o outro verificou.
- O **release readiness auditor** retorna READY, BLOCKED ou READY_WITH_RISK. Um item não verificado é BLOCKED, nunca READY_WITH_RISK — não saber é diferente de saber e aceitar — e o veredito de risco exige uma pessoa identificada que o aceitou.

Todos os nove mantêm o workspace somente leitura, revertem cada etapa temporária, não fazem reparos e separam findings provados de suspeitas não provadas.

`documentation-writer` é o único papel de escrita entre eles, e seu risco é inverso: um auditor somente leitura produz um finding incorreto que a revisão pode rejeitar, enquanto um writer produz prosa fluente descrevendo código inexistente, na qual leitores confiam porque parece correta. Ele verifica cada símbolo, comando e caminho no repositório antes de escrever, remove documentação cujo assunto deixou de existir e registra uma decisão sem explicação como pergunta aberta em vez de inventar uma justificativa. Assim como o implementer, escreve somente nos caminhos atribuídos pelo controlador.

## Arquitetura

Veja [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) para responsabilidades das camadas, direção das dependências e regras de mudança, e o [índice da documentação](docs/README.md) para uma página por componente.

```text
skills/
└── orchestrate/
    └── sdd-orchestrator/
        ├── SKILL.md                 # ponto de entrada nativo do Hermes
        ├── scripts/install_project.py
        ├── vendor/typesafe-ai/      # snapshot opcional e revisado do TypeSafe/Jev
        │   ├── SKILL.md
        │   └── LICENSE
        └── templates/.hermes/       # payload do controlador local ao projeto
            └── orchestration/
                ├── agents/
                ├── contracts/
                ├── hooks/
                ├── policies/
                ├── runtime/
                ├── schemas/
                ├── sub-agents/
                └── tests/

tests/
└── test_sdd_orchestrator_skill.py   # instala a skill em um repositório de fixture
```

## Instalação local ao projeto

A skill executa o instalador incluído. Ele exige Python 3.10+ com `jsonschema` e a raiz de uma worktree Git existente e com branch anexada:

```bash
python3 <skill-instalada>/scripts/install_project.py \
  --target /caminho/absoluto/do/projeto --json

python3 <skill-instalada>/scripts/install_project.py \
  --target /caminho/absoluto/do/projeto --apply --json

# Opcional: visualize e instale o snapshot revisado da skill TypeSafe/Jev.
python3 <skill-instalada>/scripts/install_project.py \
  --target /caminho/absoluto/do/projeto --typesafe-ai install --json

python3 <skill-instalada>/scripts/install_project.py \
  --target /caminho/absoluto/do/projeto --typesafe-ai install --apply --json
```

A primeira execução é um dry run. Use apply somente quando o resultado for `READY`; uma escrita bem-sucedida retorna `APPLIED`. Reexecutar uma instalação completa retorna `ALREADY_INITIALIZED`; exclusões gerenciadas ausentes são reparadas por um novo apply, enquanto configurações parciais, rastreadas, com symlink, arquivo especial ou conflitantes são bloqueadas antes de qualquer escrita.

Antes da primeira demanda, o Hermes resolve `.hermes/orchestration/PROJECT_SETUP.md`: ele inspeciona evidências do repositório e faz somente perguntas ainda não respondidas sobre acesso ao issue tracker, vínculo opcional com Obsidian, instalação opcional da skill TypeSafe/Jev e outras ferramentas específicas do projeto. `none` é válido quando a integração correspondente está ausente; credenciais e requisitos do produto nunca são solicitados. Depois, configure `.hermes/orchestration/policies/GATES.md` com os comandos reais de formatação, teste, análise e CI do projeto.

## Qualquer linguagem, qualquer projeto

O controlador é independente de linguagem. FSM, contratos, schemas, briefs de estágio e subagentes nunca nomeiam um toolchain; as ações de gate são nomeadas por função (`TEST_FOCUSED`, `FORMAT_CHANGED_FILES`, `ANALYZE`, `CI`). A **única** entrada específica do projeto é a tabela de comandos em `policies/GATES.md`.

Para levar o orquestrador a um novo projeto:

```bash
# 1. Dry run: preflight + stack detectada (somente leitura).
python3 <skill-instalada>/scripts/install_project.py --target /caminho/do/projeto --json

# 2. Instala o controlador local, não rastreado.
python3 <skill-instalada>/scripts/install_project.py --target /caminho/do/projeto --apply --json

# 3. Inspeciona novamente a stack quando necessário.
cd /caminho/do/projeto && python3 .hermes/orchestration/runtime/detect_stack.py --target .
```

O campo `stack` do dry run lista cada ecossistema encontrado — na raiz e em até dois níveis de profundidade para monorepos —, o manifesto que o comprova, os provedores de CI presentes, os arquivos de instrução que devem ser lidos primeiro (`AGENTS.md`, `CLAUDE.md`, ADRs…) e um comando sugerido por gate. Ecossistemas suportados: Node/TypeScript (npm, pnpm, yarn, bun), Python, Go, Rust, Java/Kotlin (Gradle, Maven), .NET, Ruby, PHP, Elixir, Swift, C/C++ (CMake) e Dart/Flutter (com FVM). Um gate sem evidência é `null`, nunca inventado.

4. Resolva `onboarding.questions` do relatório do instalador em `PROJECT_SETUP.md`; faça apenas perguntas de conectividade ainda não resolvidas e aceite `none` somente quando a integração estiver ausente.
5. Se TypeSafe/Jev estiver habilitado, visualize e aplique `--typesafe-ai install`; inspecione `.hermes/skills/typesafe-ai` antes de confiar nas skills do projeto.
6. Copie as sugestões para `GATES.md` somente depois de executar cada comando uma vez no projeto; scripts e etapas de CI do próprio projeto têm precedência.
7. Se habilitado durante o onboarding, vincule o vault do Obsidian com `.hermes/obsidian.json` (veja `BOOTSTRAP.md`).
8. Execute a suíte instalada: `python3 -m unittest discover -s .hermes/orchestration/tests -p 'test_*.py'`.

Os únicos requisitos no destino são Git e Python 3.10+ — além de `jsonschema` para as ferramentas de execução limitada. O projeto pode ser escrito em qualquer linguagem.

## Desenvolvimento e verificação

```bash
# Empacotamento da skill e contratos dos subagentes.
python3 -m unittest discover -s tests -p 'test_*.py'

# Protocolo do controlador instalado, drivers e journal.
python3 -m unittest discover -s skills/orchestrate/sdd-orchestrator/templates/.hermes/orchestration/tests -p 'test_*.py'
```

A primeira suíte instala a skill incluída em um repositório de fixture e garante que cada brief de subagente seja entregue com papel, estágios permitidos e schema de resultado declarados, e que este README liste exatamente os briefs presentes no bundle.

## Como contribuir

Este é um projeto open source e contribuições são bem-vindas. Você pode [sugerir uma melhoria](https://github.com/FelipeAngeli/hermes-sdd-orchestrator/issues/new?template=feature_request.yml), [solicitar suporte a uma linguagem ou ferramenta](https://github.com/FelipeAngeli/hermes-sdd-orchestrator/issues/new?template=ecosystem_support.yml), [relatar um bug](https://github.com/FelipeAngeli/hermes-sdd-orchestrator/issues/new?template=bug_report.yml) ou abrir um pull request. Consulte [CONTRIBUTING.md](CONTRIBUTING.md) para setup, testes e regras do projeto, além de [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) e [SECURITY.md](SECURITY.md) para relatos privados de vulnerabilidades.

## Licença

Código aberto sob a [Licença MIT](LICENSE): uso, modificação, distribuição e uso comercial são permitidos, desde que o aviso de copyright seja preservado. As contribuições são aceitas sob a mesma licença.
