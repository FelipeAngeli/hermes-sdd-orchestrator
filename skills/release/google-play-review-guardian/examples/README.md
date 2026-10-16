# Exemplos reais

`google-play-audit-report.md` e `google-play-audit-report.json` foram produzidos em 2026-10-16 executando, sem edição manual do conteúdo:

```bash
python scripts/guardian.py rules --check-sources --output-dir /private/tmp/gprg_out/real
python scripts/guardian.py gate --release ~/Documents/KelTech/mently/application/apps/app \
  --output-dir /private/tmp/gprg_out/real --no-cache --json        # exit code 2 (BLOCKED)
```

Alvo: projeto Flutter Mently (somente leitura) + artefatos auto-descobertos `build/app/outputs/bundle/release/app-release.aab` e `build/app/outputs/flutter-apk/app-release.apk`. Ambiente: aapt2/zipalign/apksigner (build-tools 37.0.0), apkanalyzer, llvm-readelf (NDK 30), bundletool 1.18.3 (`<sdk>/bundletool/`) e o Java 21 do Android Studio (JBR, achado automaticamente; inclui `jarsigner`). Resultado: `bundletool validate` OK, `apksigner verify` do APK e `jarsigner -verify` do AAB aprovados.

Única transformação aplicada após a geração: o prefixo do diretório home (`/Users/<usuário>`) foi substituído por `~` (`sed`). O JSON resultante continua validando contra `schemas/audit-report.schema.json`. Nenhum conteúdo de `key.properties` ou keystore foi lido; o relatório só cita presença e status no Git.
