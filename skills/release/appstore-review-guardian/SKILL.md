---
name: appstore-review-guardian
description: Audit iOS builds before App Store submission.
version: 0.1.0
author: Felipe Angeli (FelipeAngeli), Claude Code
license: MIT
platforms: [macos, linux]
metadata:
  hermes:
    tags: [ios, app-store, app-review, flutter, privacy-manifest, release-gate, codesign]
    category: release
    related_skills: [google-play-review-guardian]
---

# App Store Review Guardian Skill

Auditor preventivo e somente leitura para submissões à App Store. Scripts determinísticos em Python (stdlib) inspecionam o projeto iOS/Flutter, o `.ipa` (estrutura, Info.plist, Mach-O, assinatura, perfil, entitlements, privacy manifests, SDKs) e os metadados do App Store Connect quando fornecidos. A saída é um relatório JSON + Markdown com gate `READY / NEEDS_REVIEW / BLOCKED`, e cada achado cita a regra oficial da Apple, a evidência e a data de consulta.

## When to Use

- Antes de enviar um build ao TestFlight/App Store ou de clicar em "Submit for Review".
- Depois de uma rejeição (2.1, 4.8, 5.1.1, ITMS-91053/91056…), para confirmar a correção antes de reenviar.
- Como etapa obrigatória de release em projetos Flutter/iOS: inspeção → build → auditoria → correção → nova auditoria → gate.
- Não use para Android/Google Play (veja `google-play-review-guardian`) nem para gerar build, assinar ou fazer upload. A skill nunca faz nada disso.

## Prerequisites

- Python 3.10+. Nada mais é obrigatório. Com `jsonschema` instalado, o relatório é validado contra `schemas/audit-report.schema.json`; sem ele, a validação é pulada.
- macOS com Xcode Command Line Tools (`codesign`, `security`) é opcional. Sem essas ferramentas (Linux/CI), as checagens criptográficas de assinatura e perfil ficam `NOT_VERIFIED`, nunca `PASS`.
- Rede é opcional: só `rules --check-sources` e `--network` (AASA de Universal Links) a usam. Offline, o guardian degrada sem erro.
- App Store Connect API: não suportada sem biblioteca ES256. Variáveis `ASC_*` são só detectadas. Nunca peça a chave `.p8` no chat.

## How to Run

Rode tudo pela ferramenta `terminal`. `S` é o diretório desta skill (o `skill_dir` retornado por `skill_view`). Use sempre `--output-dir` fora do projeto auditado.

```
terminal(command="python3 S/scripts/guardian.py gate --release <app_flutter> --ipa <App.ipa> --output-dir /tmp/asrg/<app> --json", timeout=600)
```

Códigos de saída: 0 READY, 1 NEEDS_REVIEW, 2 BLOCKED, 3 erro de uso. `--json` imprime um resumo compacto (status, contagens, top findings, caminhos dos relatórios, `schema_errors`).

## Quick Reference

```
python3 S/scripts/guardian.py audit --quick  <projeto>                       # rápido, sem heurísticas de código
python3 S/scripts/guardian.py audit --full   <projeto> [--ipa X.ipa|--app Runner.app]
python3 S/scripts/guardian.py audit --policy <projeto>                       # só privacidade/guidelines/ASC
python3 S/scripts/guardian.py audit --ipa X.ipa                              # só o artefato
python3 S/scripts/guardian.py gate --release <projeto> --ipa X.ipa [--asc-metadata asc.json] [--network]
python3 S/scripts/guardian.py rules --check-sources --output-dir DIR [--force|--offline]
python3 S/scripts/guardian.py rules --list
python3 S/scripts/guardian.py asc --template > asc.json
# módulos isolados (JSON no stdout)
python3 S/scripts/inspect_ipa.py X.ipa|Runner.app [--full]
python3 S/scripts/inspect_project.py <projeto> [--mode quick|full|policy]
python3 S/scripts/inspect_dependencies.py <projeto>
python3 S/scripts/validate_plist.py Info.plist [--source project|bundle]
python3 S/scripts/validate_privacy.py PrivacyInfo.xcprivacy
python3 S/scripts/validate_entitlements.py Runner.entitlements [--source bundle|project]
python3 S/scripts/validate_signing.py Runner.app
python3 S/scripts/generate_report.py appstore-audit-report.json --validate --md out.md
```

| Severidade | Significado |
|---|---|
| BLOCKER | Falha confirmada que impede a submissão ou invalida o build |
| CRITICAL | Violação confirmada ou risco muito alto de rejeição |
| WARNING | Possível problema; precisa de validação |
| INFO | Recomendação |
| PASS | Verificação executada e requisito atendido |
| NOT_VERIFIED | Não executada ou sem evidência suficiente |
| NOT_APPLICABLE | Comprovadamente não se aplica |

## Procedure

1. **Regras.** Rode `rules --check-sources --output-dir <dir>`. Saída 0 = fontes iguais à baseline de 2026-10-16. Saída 1 = alguma fonte mudou ou falhou: leia as URLs em `changed_since_baseline` com `web_extract` e revise as regras afetadas em `rules/*.json` antes de confiar no gate. Offline, o comando informa `offline`; siga em frente, anotando no relatório que as fontes não foram rechecadas.
2. **Escopo.** Identifique o diretório do app (com `pubspec.yaml` + `ios/`, ou com `*.xcodeproj`) e o artefato. Use `search_files` para achar `*.ipa` em `build/ios/ipa/`. **Não gere build sem autorização explícita**: `flutter build ipa` pode acionar a assinatura automática e alterar perfis na conta Apple. Sem `.ipa`, audite só o projeto; se houver `Runner.app` de `--no-codesign`, passe `--app` e trate o resultado como UNSIGNED.
3. **Executar.** Rode `guardian.py gate --release <app> [--ipa X.ipa] --output-dir <dir> --json`. Termina quando o JSON trouxer `schema_errors: []` (ou `null` sem jsonschema) e os dois relatórios existirem.
4. **Interpretar.** Leia só o resumo `--json` e, se precisar de detalhes, os findings BLOCKER/CRITICAL/WARNING do `appstore-audit-report.json` via `read_file`. Não carregue binários nem o relatório inteiro sem necessidade. Achados `heuristic` precisam ser confirmados no código (`search_files` no arquivo/linha citados) antes de virar recomendação firme.
5. **Pendências.** Para cada item `pending` com `gate_relevant: true`, peça o dado ao usuário (gere o formulário com `asc --template`) ou registre a validação manual. Repasse o checklist de `references/app-store-connect-checklist.md`.
6. **Corrigir.** Proponha cada correção com o comando ou trecho exato da `recommendation`. Só edite arquivos, gere build, assine ou faça upload com autorização explícita do usuário.
7. **Reauditar.** Rode o mesmo comando com o mesmo `--output-dir`. O cache por sha256 reaproveita os módulos cujas entradas não mudaram, e `cache.changed_inputs` lista o que mudou. Só apresente READY se o script retornar READY, sempre com o aviso de que isso não garante aprovação.

Entregue ao usuário: status do gate, bloqueios e riscos com a correção de cada um, pendências obrigatórias, indicadores de prontidão (técnico, guidelines Apple e submissão, que não são probabilidade de aprovação) e o caminho dos relatórios.

## Pitfalls

- READY ≠ aprovação. O disclaimer está no relatório e precisa ser repassado.
- O código rebaixa automaticamente PASS sem evidência para NOT_VERIFIED, heurística para no máximo WARNING e regra `recommended` para no máximo WARNING. Não "promova" severidades à mão.
- Um `.app` de `flutter build ios --no-codesign` sempre dá `SIGN-001` BLOCKER. Isso é esperado: rotule como UNSIGNED e não trate como bloqueio do código.
- `aps-environment=development` no `.entitlements` de origem é WARNING (o Xcode troca pelo perfil na exportação). No `.ipa` assinado, vira CRITICAL.
- `PRIV-RRAPI-002` acusa referência de símbolo, não uso. Confira se o SDK que usa a API já declara a categoria no próprio manifest.
- SDK da lista Apple sem manifest gera WARNING porque a obrigação depende de o app ser novo ou de a atualização adicionar o SDK. Pergunte isso ao usuário.
- Sem macOS, `SIGN-002` fica NOT_VERIFIED e o gate não chega a READY. Rode o gate final em macOS.
- Nunca grave o `--output-dir` dentro do projeto: o CLI recusa com saída 3.

## Verification

- Rodar `python3 -m unittest discover -s S/tests -p 'test_*.py'` deve passar. A suíte cobre IPA válido/inválido (zip-slip, symlink, zip bomb, corrompido, sem Payload, sem Info.plist, plist binária), Info.plist, privacy manifest, entitlements, assinatura com e sem `codesign` (mock), projeto Flutter descartável, severidades, cache incremental, relatórios válidos no schema e modo offline.
- `rules --check-sources` retorna `changed_since_baseline: []` e `errors: {}`.
- O relatório tem `schema_errors: []` e `final_status` coerente com o código de saída.
- Exemplos reais em `examples/` (Mently: projeto + `.ipa` UNSIGNED). Fontes em `references/sources.md` e limitações em `references/limitations.md`.
