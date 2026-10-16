# Requisitos técnicos de release Android

## Target API (dados em `rules/technical-rules.json` → TECH-TARGET-API-LEVEL)

Texto oficial (consultado 2026-10-16, answer/11926878):

> Starting August 31, 2026: New apps and app updates must target Android 16 (API level 36) or higher to be submitted to Google Play; except for Wear OS, and Android Automotive OS apps, which must target Android 15 (API level 35) or higher, and Android TV and Android XR apps, which must target Android 14 (API level 34) or higher.

| Formato | Novos apps / atualizações | Vigência | Extensão até | Apps existentes (disponibilidade) |
|---|---|---|---|---|
| Celular/tablet | API 36 (Android 16) | 2026-08-31 | 2026-11-01 (se solicitada) | API 35 |
| Wear OS | API 35 | 2026-08-31 | 2026-11-01 | API 34 |
| Android Automotive OS | API 35 | 2026-08-31 | 2026-11-01 | API 32 (12L) |
| Android TV | API 34 | 2025-08-31 | — (não registrada para envio) | API 33 |
| Android XR | API 34 | 2026-08-31 | 2026-11-01 | API 34 |

Exceção: apps permanentemente privados de distribuição interna de uma organização.

Comportamento do auditor:
- O formato vem de `--form-factor` ou de `<uses-feature>` (watch, automotive, leanback required, xr).
- Sem `--submission`, avalia novo app **e** atualização (mesmo mínimo).
- Abaixo do mínimo: `BLOCKER` (determinístico, artefato). Com `--extension-granted` e antes do fim da extensão: `WARNING`.
- `existing_app` usa TECH-TARGET-API-AVAILABILITY e gera `WARNING` (perda de descoberta, não bloqueio de envio).
- Se o target vier só do projeto via `flutter.targetSdkVersion` (resolvido no Flutter SDK local), o método é heurístico e nunca vira `BLOCKER`.

## 16 KB page size (TECH-16KB-PAGE-SIZE, TECH-16KB-ZIP-ALIGN)

Fonte: developer.android.com/guide/practices/page-sizes (atualizada 2026-10-16) + blog oficial (2025-11-01).

- Apps com target ≥ 35 precisam suportar 16 KB em dispositivos 64-bit; a partir de **2027-02-01** atualizações sem suporte não poderão ser lançadas.
- ELF: todo `PT_LOAD` de `.so` 64-bit com `p_align ≥ 0x4000`. Python lê os program headers em memória; quando `llvm-readelf` (NDK) existe, o resultado é confirmado (`python-elf + llvm-readelf`).
- RELRO: `(VirtAddr + MemSiz) % 0x4000 == 0`, ou RELRO em LOAD próprio, ou sem dados graváveis no restante da página de 16 KB → senão `WARNING`.
- APK: `.so` não comprimidas devem começar em offset múltiplo de 16384 (offset calculado do *local file header*); confirmado com `zipalign -c -P 16 -v 4` quando disponível.
- AAB: o alinhamento ZIP dos APKs gerados pelo Play não é observável → `NOT_VERIFIED` com instrução `bundletool build-apks` + `zipalign`.
- Toolchain: AGP ≥ 8.5.1 e NDK r28+ são `PASS`/`INFO` heurísticos.

## Outros requisitos

| Regra | Requisito | Fonte |
|---|---|---|
| TECH-AAB-REQUIRED | Novos apps publicam com .aab (desde 08/2021) | guide/app-bundle |
| TECH-64BIT | Código nativo precisa de ABI 64-bit | requirements/64-bit |
| TECH-DEBUGGABLE | Release não pode ser debuggable | studio/publish/preparing |
| TECH-DEBUG-SIGNATURE | Certificado de debug não é aceito | studio/publish/app-signing |
| TECH-UPLOAD-KEY | Chave de upload RSA ≥ 2048 | answer/9842756 |
| TECH-VERSION-CODE | 1 … 2100000000; sempre crescente | studio/publish/versioning |
| TECH-EXPORTED-EXPLICIT | `android:exported` explícito com intent-filter (target ≥ 31) | behavior-changes-12 |
| TECH-BILLING-LIBRARY-VERSION | Billing Library ≥ 8 desde 2026-08-31 | billing/deprecation-faq |

## Comandos oficiais (quando há Java/SDK)

```bash
bundletool validate --bundle=app-release.aab
bundletool build-apks --bundle=app-release.aab --output=app.apks
apksigner verify --print-certs --verbose app-release.apk
aapt2 dump badging app-release.apk
aapt2 dump xmltree --file AndroidManifest.xml app-release.apk
zipalign -c -P 16 -v 4 app-release.apk
$NDK/toolchains/llvm/prebuilt/<host>/bin/llvm-readelf -Wl libfoo.so
```
