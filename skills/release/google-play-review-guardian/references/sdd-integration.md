# Integração com Hermes e o orquestrador SDD

A skill funciona sozinha, mas foi desenhada como **gate de REVIEW/release** consumível por agentes e pipelines.

## Contrato

```bash
python skills/release/google-play-review-guardian/scripts/guardian.py \
  gate --release <projeto> [--aab <app.aab>] --json --output-dir <dir-fora-do-projeto>
```

- **stdout** (`--json`): uma linha JSON com `status`, `exit_code`, `mode`, `package`, `version`, `target_sdk`, `counts`, `sections`, `top_findings[]`, `not_verified_mandatory`, `report_md`, `report_json`.
- **exit code**: `0` READY, `1` NEEDS_REVIEW, `2` BLOCKED, `3` erro de uso.
- **artefatos**: `google-play-audit-report.json` (valida contra `schemas/audit-report.schema.json`) e `.md`.
- **cache**: `<output-dir>/.guardian-cache` (hash por arquivo/artefato + digest das regras + versão da ferramenta). Reexecuções sem mudanças reutilizam resultados.

## Fluxo SDD sugerido

```text
Projeto → análise estática → build release (fora da skill) → AAB
→ guardian gate --release (técnico + políticas + privacidade)
→ relatório estruturado → correções autorizadas pelo humano → novo build
→ guardian gate --release → Release Quality Gate → upload manual ao Play Console
```

1. No estado REVIEW/RELEASE do orquestrador, chame o gate com `terminal` e capture o exit code.
2. `2` (BLOCKED): o orquestrador volta para implementação com os `top_findings` como tarefas (cada finding traz `rule_id`, arquivo, evidência e recomendação com comando).
3. `1` (NEEDS_REVIEW): exige aprovação humana explícita; liste os `NOT_VERIFIED` obrigatórios como checklist manual (`references/play-console-checklist.md`).
4. `0` (READY): o gate passa, mas o upload continua manual e autorizado pelo humano — a skill nunca publica.
5. Registre o JSON no journal/evidências da tarefa (é compacto; não carregue binários no contexto).

## Exemplo de política de gate em GATES.md

```markdown
### Android release gate
- Comando: `python skills/release/google-play-review-guardian/scripts/guardian.py gate --release apps/app --json --output-dir .sdd/evidence/gprg`
- Aprovação automática: exit 0
- Requer humano: exit 1
- Falha: exit 2 ou 3
- Antes de releases críticos: `guardian.py rules --check-sources` (exit 1 = regras desatualizadas)
```

## Agentes paralelos

Os módulos são isolados (falha de um módulo vira finding `NOT_VERIFIED` e linha em `limitations`), o cache é por arquivo e os temporários usam `mkdtemp` 0700 próprios, então execuções concorrentes com `--output-dir` diferentes são seguras. A skill irmã `appstore-review-guardian` cobre iOS; um gate de release multiplataforma pode exigir ambos com exit 0.
