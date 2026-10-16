---
name: google-play-review-guardian
description: Audit Android apps before Google Play submission.
version: 0.1.0
author: Felipe Angeli (FelipeAngeli), Claude Code
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [android, google-play, release, flutter, aab, apk, compliance, 16kb, target-sdk, read-only]
    category: release
    related_skills: [appstore-review-guardian]
---

# Google Play Review Guardian Skill

Auditor preventivo e somente leitura para apps Android (Flutter, Kotlin, Java, nativo) antes do envio ao Google Play Console. Inspeciona projeto, `.aab` e `.apk` com scripts determinísticos, cruza o resultado com uma base de regras rastreável a fontes oficiais e gera relatórios Markdown/JSON com um gate `READY` / `NEEDS_REVIEW` / `BLOCKED`. `READY` significa apenas ausência de bloqueios nas verificações executadas — nunca aprovação garantida pelo Google.

## When to Use

- Antes de enviar um `.aab` ao Play Console (gate de release em pipeline SDD ou CI).
- Para conferir target SDK, compatibilidade com páginas de 16 KB, assinatura, permissões restritas, Data safety, Play Billing e exigências de teste fechado.
- Para revisar um `.apk`/`.aab` recebido de terceiros sem extraí-lo nem executá-lo.

Não use para compilar, assinar, versionar, enviar ou publicar: a skill nunca altera o projeto nem o Console.

## Prerequisites

- Python 3.10+ (somente biblioteca padrão). `jsonschema` só é necessário para a suíte de testes.
- Opcional, descobertos automaticamente (PATH, `$ANDROID_HOME`, `$ANDROID_SDK_ROOT`, `~/Library/Android/sdk`, `~/Android/Sdk`, build-tools mais novo): `aapt2`, `zipalign`, `llvm-readelf` (NDK). `apksigner`, `apkanalyzer` e `bundletool` exigem Java, procurado em `JAVA_HOME`, no `PATH` e no JBR do Android Studio (nada é instalado); o `bundletool` também é achado em `<sdk>/bundletool/bundletool*.jar` ou `BUNDLETOOL_JAR`. Com um JDK (o JBR é um JDK), a assinatura do `.aab` é verificada com `jarsigner -verify`. Sem Java, essas checagens ficam `NOT_VERIFIED` com motivo explícito. `guardian.py tools` mostra o que foi encontrado.
- Opcional: credencial somente leitura da Google Play Developer API (`--play-service-account` + pacote `cryptography`, ou `GPRG_PLAY_ACCESS_TOKEN`).

## How to Run

Use a ferramenta `terminal` a partir da raiz da skill (ou com caminho absoluto para o script):

```bash
python scripts/guardian.py audit --quick <projeto>
python scripts/guardian.py audit --full <projeto> --output-dir /tmp/gprg
python scripts/guardian.py audit --policy <projeto> --declarations decl.json
python scripts/guardian.py audit --aab build/app/outputs/bundle/release/app-release.aab
python scripts/guardian.py audit --apk app-release.apk
python scripts/guardian.py gate --release <projeto> [--aab app.aab] --json
python scripts/guardian.py rules --check-sources      # reconsulta URLs oficiais (cache TTL)
python scripts/guardian.py tools                      # ferramentas Android SDK encontradas
```

Saídas: `google-play-audit-report.md` e `google-play-audit-report.json` em `--output-dir` (padrão: diretório temporário; nunca dentro do projeto). `--json` imprime só o resumo compacto. Exit codes: `0` READY, `1` NEEDS_REVIEW, `2` BLOCKED, `3` erro de uso.

## Quick Reference

| Modo | Cobertura |
|---|---|
| `audit --quick` | build, manifest, target SDK, permissões, integridade do artefato |
| `audit --full` | tudo: projeto, código, artefatos (auto-descobre `build/.../app-release.aab`), SDKs, privacidade, cobrança, Console |
| `audit --policy` | políticas: permissões, Data safety, exclusão de conta, cobrança, metadados (pula assinatura/16 KB) |
| `gate --release` | auditoria final com status de gate e exit code para pipeline |

| Opção | Uso |
|---|---|
| `--form-factor phone/tablet\|wear\|tv\|automotive\|xr` | sobrescreve a detecção por `uses-feature` |
| `--submission new_app\|app_update\|existing_app` | escolhe a exigência de target API aplicável |
| `--extension-granted` | extensão de prazo concedida no Play Console |
| `--account-type personal\|organization --account-created AAAA-MM-DD` | regra de teste fechado (12 testadores/14 dias) |
| `--declarations decl.json` | declarações do Console (Data safety, store listing, conta, confirmações manuais); ver `references/play-console-checklist.md` |
| `--no-tools` / `--no-cache` / `--timeout N` | só parsers Python / sem cache / timeout por ferramenta |

Regras: `rules/technical-rules.json`, `rules/privacy-rules.json`, `rules/policy-rules.json`. Esquema do relatório: `schemas/audit-report.schema.json`. Fontes e datas: `references/sources.md`.

## Procedure

1. Rode `python scripts/guardian.py tools` para ver quais ferramentas oficiais estão disponíveis; registre as ausentes como limitação.
2. Antes de auditorias críticas, rode `python scripts/guardian.py rules --check-sources`. Exit `1` indica drift (ex.: a página de target API passou a exigir outro nível): atualize as regras com `web_extract` na URL oficial e registre em `references/sources.md` antes de confiar no gate.
3. Garanta que exista um build release recente (gerado fora desta skill). Se não houver `.aab`, peça ao usuário; não compile por conta própria.
4. Rode `gate --release <projeto> --json --output-dir <dir>`. Informe `--account-type/--account-created` e `--declarations` quando o usuário os fornecer.
5. Leia o resumo JSON. Para detalhes, use `read_file` no `google-play-audit-report.md`; carregue apenas a referência relevante (`references/permissions-policy.md`, `references/data-safety.md`, etc.) para interpretar findings.
6. Para cada `BLOCKER`/`CRITICAL`, apresente arquivo, evidência e o comando de correção do relatório. Correções no projeto só com autorização explícita do usuário.
7. Converta `NOT_VERIFIED` obrigatórios em ações manuais (checklist do Play Console) ou em evidências adicionais (`--declarations`, ferramentas Java).
8. Após correções autorizadas e novo build, rode o gate de novo e compare os resumos.

## Pitfalls

- Um `.aab` com ZIP íntegro não está "aprovado": `bundletool validate`, assinatura criptográfica e alinhamento dos APKs gerados pelo Play ficam `NOT_VERIFIED` sem Java; o alinhamento dos APKs gerados pelo Play fica `NOT_VERIFIED` mesmo com Java.
- O manifest do projeto não contém permissões injetadas por plugins; audite o `.aab` (manifest mesclado). O versionCode do artefato pode não bater com o `pubspec.yaml` (`TECH-ARTIFACT-FRESHNESS`).
- Permissão presente não é violação: permissões restritas geram `WARNING` com ação de declaração, nunca `BLOCKER`.
- Dependência de analytics/push não prova coleta de dados; trate como indício para o formulário Data safety.
- `flutter.targetSdkVersion` é resolvido lendo o Flutter SDK local (heurístico); o valor autoritativo é o do artefato.
- `key.properties`, keystores e `local.properties` nunca são lidos (exceto a chave `flutter.sdk`); só presença e status no Git aparecem no relatório.
- A Play Developer API não expõe Data safety, classificação, público-alvo nem app access para leitura; esses itens sempre exigem conferência manual.

## Verification

- Testes: `python -m unittest discover -s skills/release/google-play-review-guardian/tests -p 'test_*.py'` a partir da raiz do repositório (fixtures geradas em memória, sem rede).
- Relatórios reais de exemplo: `examples/google-play-audit-report.md` e `examples/google-play-audit-report.json`, gerados executando o gate.
- Valide um relatório contra `schemas/audit-report.schema.json` (draft 2020-12) e confirme que `summary.exit_code` corresponde ao exit code do processo.
- Integração com o orquestrador SDD: `references/sdd-integration.md`. Limitações conhecidas: `references/limitations.md`.
