# Exemplos (sintéticos)

`google-play-audit-report.md` e `google-play-audit-report.json` foram produzidos executando o `guardian.py` de verdade, sem edição manual, sobre **fixtures sintéticas**: nenhum dado de app real, caminho pessoal, keystore ou certificado real.

Proveniência (2026-10-16):

Tudo é refeito por `python tests/make_examples.py`, que constrói as fixtures, roda o `gate` e copia os relatórios para cá:

1. Fixtures criadas em `/private/tmp/gprg-example/` com os helpers de `tests/fixtures.py`:
   - `make_flutter_project(root, target_sdk="36", minify=True)` → projeto Flutter descartável `demo_app` (pacote `com.example.app`, `versionCode 45`, AGP 8.7.0), com `ndkVersion '28.0.13004108'` acrescentado ao `android/app/build.gradle` para exercitar a heurística de NDK. O `key.properties`/`upload.jks` gerados são falsos e o diretório não é um repositório Git.
   - `make_aab(...)` e `make_apk(...)` → `build/app/outputs/bundle/release/app-release.aab` e `build/app/outputs/flutter-apk/app-release.apk` com manifest declarando `INTERNET`, `ACCESS_BACKGROUND_LOCATION`, `com.android.vending.BILLING` e um serviço `com.example.Player` com `foregroundServiceType="mediaPlayback"` sem a permissão correspondente; `.so` sintéticas arm64 (ELF 64-bit aarch64) e armv7 (ELF 32-bit arm), ambas alinhadas a 16 KB; Billing Library `7.1.1` no AAB; certificado DER sintético (`CN=Upload Key` / `CN=Release Key`).
2. Execução:

```bash
python scripts/guardian.py gate --release /private/tmp/gprg-example/demo_app \
  --output-dir /private/tmp/gprg-example/out --no-tools --no-cache --json   # exit code 2 (BLOCKED)
```

`--no-tools` mantém o exemplo reproduzível sem Android SDK/Java: as verificações que dependem de `apksigner`/`bundletool`/`jarsigner` aparecem como `NOT_VERIFIED`. Resultado: `BLOCKED` (2 BLOCKER) por `POL-FOREGROUND-SERVICE` (tipo `mediaPlayback` sem `FOREGROUND_SERVICE_MEDIA_PLAYBACK`) e `TECH-BILLING-LIBRARY-VERSION` (7.1.1). Os achados heurísticos de AGP/NDK (`TECH-AGP-16KB`) aparecem como `INFO` "confirmar", e nenhum `PASS`/`NOT_APPLICABLE` do relatório vem de verificação heurística ou manual.

Os relatórios foram copiados de `/private/tmp/gprg-example/out/` sem alteração; o JSON valida contra `schemas/audit-report.schema.json` e um teste confere que os caminhos citados aqui são os que aparecem no relatório comitado (`tests/test_review_fixes.py`).
