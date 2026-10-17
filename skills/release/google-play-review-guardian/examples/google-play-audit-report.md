# Google Play Review Guardian — relatório de auditoria

```text
GOOGLE PLAY REVIEW GUARDIAN
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Application: Example App
Package: com.example.app
Version: 2.1.0
Version Code: 45
Min/Target/Compile SDK: 24 / 36 / 36
Mode: gate   Generated: 2026-10-17T00:55:27+00:00

ANDROID BUILD
Status: NEEDS_REVIEW

TARGET SDK
Status: PASS

16 KB PAGE COMPATIBILITY
Status: NEEDS_REVIEW

SIGNING
Status: NEEDS_REVIEW

SECURITY
Status: NEEDS_REVIEW

PERMISSIONS
Status: BLOCKED

DATA SAFETY
Status: WARNING

GOOGLE PLAY BILLING
Status: BLOCKED

PLAY CONSOLE METADATA
Status: NOT_VERIFIED

TESTING / PRODUCTION ACCESS
Status: NOT_VERIFIED

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FINAL STATUS: BLOCKED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FINDINGS:

[BLOCKER] Tipo de foreground service sem a permissão correspondente.
[BLOCKER] Play Billing Library abaixo da versão mínima.
[WARNING] Localização em segundo plano: exige justificativa e declaração no Play Console.
[WARNING] Tipos de foreground service exigem declaração no Play Console.
[WARNING] Criação de conta detectada: verificar exclusão no app e via web.
[WARNING] SDKs com possível coleta de dados: confirmar declarações do Data safety.
[INFO] APK não substitui o AAB para novos apps.
[INFO] NDK 28.0.13004108 (indício heurístico/manual — confirmar).
[INFO] AGP 8.7.0 ≥ 8.5.1 (indício heurístico/manual — confirmar).
[INFO] Backup automático habilitado.
[INFO] Backup automático habilitado.
[INFO] Sem permissão AD_ID.
[INFO] Billing Library 9+ exigida a partir de 2027-08-31.
[PASS] ABIs 64-bit presentes.
[PASS] ABIs 64-bit presentes.
[PASS] Artefato no formato Android App Bundle.
[PASS] Estrutura do AAB íntegra e manifest legível.
[PASS] Estrutura do APK íntegra e manifest legível.
[PASS] buildType release sem debuggable=true.
[PASS] Artefato não é debuggable.
[PASS] Artefato não é debuggable.
[PASS] Todos os componentes com intent-filter declaram android:exported.
[PASS] Todos os componentes com intent-filter declaram android:exported.
[PASS] Application ID válido.
[PASS] Application ID válido.
[PASS] R8/minify habilitado no release.
[PASS] versionCode dentro do limite do Google Play.
[PASS] versionCode dentro do limite do Google Play.
[PASS] Requisito de Target SDK atendido.
[PASS] Segmentos ELF PT_LOAD alinhados a 16 KB (1 libs 64-bit).
[PASS] Segmentos ELF PT_LOAD alinhados a 16 KB (1 libs 64-bit).
[PASS] Offsets ZIP das .so alinhados a 16 KB.
[PASS] Certificado não é de debug.
[PASS] Certificado não é de debug.
[PASS] Chave de upload RSA ≥ 2048 bits.
[PASS] Cleartext desabilitado no manifest.
[PASS] Cleartext desabilitado no manifest.
[NOT_VERIFIED] bundletool validate não executado.
[NOT_VERIFIED] Alinhamento ZIP dos APKs gerados pelo Play não verificável a partir do .aab.
[NOT_VERIFIED] Inscrição no Play App Signing não verificável offline.
[NOT_VERIFIED] Assinatura JAR do bundle não verificada criptograficamente.
[NOT_VERIFIED] Validade criptográfica da assinatura não verificada.
[NOT_VERIFIED] Status Git do arquivo de assinatura desconhecido.
[NOT_VERIFIED] Status Git do arquivo de assinatura desconhecido.
[NOT_VERIFIED] Manifest do projeto não inclui permissões/componentes adicionados por plugins no merge.
[NOT_VERIFIED] SDKs não cruzados com o Google Play SDK Index.
[NOT_VERIFIED] Data safety não pôde ser comparado com o Play Console.
[NOT_VERIFIED] Política de privacidade não verificada.
[NOT_VERIFIED] Produtos digitais: confirmar uso exclusivo do Play Billing.
[NOT_VERIFIED] Declaração 'contém anúncios' coerente com os SDKs.
[NOT_VERIFIED] Credenciais/instruções de acesso para revisão se houver login.
[NOT_VERIFIED] Questionário de classificação de conteúdo (IARC) concluído.
[NOT_VERIFIED] Declarações de permissões restritas e de foreground service enviadas.
[NOT_VERIFIED] Store listing: título ≤ 30, descrição curta ≤ 80, completa ≤ 4000; ícone 512x512, feature graphic 1024x500, screenshots; sem emojis/CAPS abusivos no título.
[NOT_VERIFIED] Público-alvo e conteúdo declarados (Families se incluir crianças).
[NOT_VERIFIED] Formulário Data safety enviado e coerente com SDKs e código.
[NOT_VERIFIED] versionCode maior que o último publicado não verificável offline.
[NOT_VERIFIED] versionCode maior que o último publicado não verificável offline.
[NOT_VERIFIED] Exigência de teste fechado pendente (tipo de conta não informado).
[NOT_VERIFIED] Testes funcionais em dispositivo/emulador não executados.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RECOMMENDED ACTIONS:

1. [BLOCKER] POL-FOREGROUND-SERVICE: Adicione a permissão FOREGROUND_SERVICE_<TIPO> correspondente no AndroidManifest.xml.
2. [BLOCKER] TECH-BILLING-LIBRARY-VERSION: Atualize com.android.billingclient (ou in_app_purchase_android) para a versão suportada; extensão possível conforme a página oficial.
3. [WARNING] POL-BACKGROUND-LOCATION: Confirme que é funcionalidade principal; caso contrário remova (ex.: `<uses-permission android:name="android.permission.ACCESS_BACKGROUND_LOCATION" tools:node="remove"/>` no manifest do app). Se necessária, preencha o Permissions Declaration Form e implemente divulgação destacada.
4. [WARNING] POL-FOREGROUND-SERVICE: Em Play Console > App content > Foreground service permissions, descreva cada tipo, o impacto se adiado/interrompido e anexe vídeo.
5. [WARNING] PRIV-ACCOUNT-DELETION: Ofereça caminho no app para excluir conta e dados e um link web funcional; informe a URL no Data safety. Desativação temporária não atende.
6. [WARNING] PRIV-SDK-DATA-DISCLOSURE: Confira a documentação de Data safety de cada SDK (SDK Index) e, se necessário, instrumente o tráfego (ex.: proxy em build de teste) antes de preencher o formulário.
7. [NOT_VERIFIED] TECH-BUNDLETOOL-VALIDATE: Instale um JRE e o bundletool e rode `java -jar bundletool.jar validate --bundle=<app>.aab`.
8. [NOT_VERIFIED] TECH-16KB-ZIP-ALIGN: Gere APKs com `bundletool build-apks --bundle=app.aab --output=app.apks` e rode `zipalign -c -P 16 -v 4` nos splits; confira o aviso de 16 KB no App bundle explorer.
9. [NOT_VERIFIED] TECH-PLAY-APP-SIGNING: Confira Play Console > Test and release > App integrity > Play app signing; registre o SHA-256 da app signing key em APIs/App Links.
10. [NOT_VERIFIED] TECH-SIGNATURE-VERIFY: Rode `jarsigner -verify -verbose -certs app-release.aab` (requer JDK); o Play Console também valida no upload.
11. [NOT_VERIFIED] TECH-SIGNATURE-VERIFY: Instale um JRE e rode `apksigner verify --print-certs --verbose app-release.apk`.
12. [NOT_VERIFIED] TECH-MERGED-MANIFEST: Rode também `guardian.py audit --aab build/app/outputs/bundle/release/app-release.aab` (ou gate --aab) para auditar o manifest mesclado.
13. [NOT_VERIFIED] POL-SDK-INDEX: Consulte https://play.google.com/sdks para cada SDK de terceiros (avisos de política/versões desatualizadas).
14. [NOT_VERIFIED] PRIV-DATA-SAFETY-FORM: Exporte/compare o formulário Data safety com o inventário de SDKs deste relatório.
15. [NOT_VERIFIED] PRIV-PRIVACY-POLICY: Garanta link válido no store listing e dentro do app, cobrindo coleta, uso, compartilhamento, retenção e exclusão.
16. [NOT_VERIFIED] POL-PLAY-BILLING: Confirme que bens/serviços digitais são vendidos pelo Play Billing, com preço, renovação automática, testes gratuitos e cancelamento claros.
17. [NOT_VERIFIED] POL-ADS-DECLARATION: Verificar manualmente no Play Console.
18. [NOT_VERIFIED] POL-APP-ACCESS: Verificar manualmente no Play Console.
19. [NOT_VERIFIED] POL-CONTENT-RATING: Verificar manualmente no Play Console.
20. [NOT_VERIFIED] POL-PERMISSIONS-DECLARATION: Verificar manualmente no Play Console.
21. [NOT_VERIFIED] POL-STORE-LISTING: Verificar manualmente no Play Console.
22. [NOT_VERIFIED] POL-TARGET-AUDIENCE: Verificar manualmente no Play Console.
23. [NOT_VERIFIED] PRIV-DATA-SAFETY-FORM: Verificar manualmente no Play Console.
24. [NOT_VERIFIED] TECH-VERSION-CODE-INCREMENT: Confira em Play Console > App bundle explorer que o versionCode é maior que o último enviado.
25. [NOT_VERIFIED] POL-CLOSED-TESTING: Se conta pessoal criada após 2023-11-13: teste fechado com ≥ 12 testadores por ≥ 14 dias consecutivos e solicite acesso à produção.
26. [NOT_VERIFIED] TECH-FUNCTIONAL-TESTS: Execute `flutter test integration_test` / Espresso / UI Automator e o pre-launch report do Play Console; nunca classificado como aprovado sem execução.
27. Execute novamente a auditoria após as correções (gate --release).
```

> READY significa apenas ausência de bloqueios nas verificações executadas; não garante aprovação pelo Google Play.

## Alvo e cobertura

- Alvo: `{"project": "/private/tmp/gprg-ex2/demo_app", "aab": "/private/tmp/gprg-ex2/demo_app/build/app/outputs/bundle/release/app-release.aab", "apk": "/private/tmp/gprg-ex2/demo_app/build/app/outputs/flutter-apk/app-release.apk", "aab_sha256": null, "apk_sha256": null, "project_type": "flutter", "form_factor": "phone/tablet", "submission": null}`
- mode: gate
- artifacts_autodiscovered: ['/private/tmp/gprg-ex2/demo_app/build/app/outputs/bundle/release/app-release.aab', '/private/tmp/gprg-ex2/demo_app/build/app/outputs/flutter-apk/app-release.apk']
- network: não utilizada na auditoria (somente rules --check-sources e API opcional)
- project_type: flutter
- gradle_file: android/app/build.gradle
- form_factor: phone/tablet (nenhum uses-feature de outro formato)
- source_files_scanned: 5

## Ferramentas

| Ferramenta | Disponível | Caminho/motivo |
|---|---|---|
| aapt2 | não |  desativado (--no-tools) |
| adb | não |  desativado (--no-tools) |
| apkanalyzer | não |  desativado (--no-tools) |
| apksigner | não |  desativado (--no-tools) |
| bundletool | não |  desativado (--no-tools) |
| java | não |  desativado (--no-tools) |
| llvm-readelf | não |  desativado (--no-tools) |
| zipalign | não |  desativado (--no-tools) |

## Findings detalhados

| ID | Severidade | Regra | Componente | Evidência | Método | Confiança | Recomendação | Fonte (consulta) |
|---|---|---|---|---|---|---|---|---|
| GPRG-001 | BLOCKER | POL-FOREGROUND-SERVICE (mandatory) | app-release.aab | com.example.Player:mediaPlayback requer FOREGROUND_SERVICE_MEDIA_PLAYBACK | deterministic | high | Adicione a permissão FOREGROUND_SERVICE_<TIPO> correspondente no AndroidManifest.xml. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/13392821?hl=en) (2026-10-16) |
| GPRG-002 | BLOCKER | TECH-BILLING-LIBRARY-VERSION (mandatory) | monetização | billing_client=7.1.1; mínimo 8 desde 2026-08-31 | deterministic | high | Atualize com.android.billingclient (ou in_app_purchase_android) para a versão suportada; extensão possível conforme a página oficial. | [developer.android.com/google/play/billing/deprec](https://developer.android.com/google/play/billing/deprecation-faq) (2026-10-16) |
| GPRG-003 | WARNING | POL-BACKGROUND-LOCATION (mandatory) | app-release.aab | declarada(s): android.permission.ACCESS_BACKGROUND_LOCATION; origem provável: não determinado (verifique o manifest-merger-report) | deterministic | high | Confirme que é funcionalidade principal; caso contrário remova (ex.: `<uses-permission android:name="android.permission.ACCESS_BACKGROUND_LOCATION" tools:node="remove"/>` no manifest do app). Se necessária, preencha o Permissions Declaration Form e implemente divulgação destacada. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/9799150?hl=en) (2026-10-16) |
| GPRG-004 | WARNING | POL-FOREGROUND-SERVICE (mandatory) | app-release.aab | tipos: mediaPlayback; serviços: com.example.Player | deterministic | high | Em Play Console > App content > Foreground service permissions, descreva cada tipo, o impacto se adiado/interrompido e anexe vídeo. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/13392821?hl=en) (2026-10-16) |
| GPRG-005 | WARNING | PRIV-ACCOUNT-DELETION (mandatory) | Data safety | criação: lib/main.dart:2 'signUp'; nenhum sinal de fluxo de exclusão no código; URL web de exclusão não informada | heuristic | medium | Ofereça caminho no app para excluir conta e dados e um link web funcional; informe a URL no Data safety. Desativação temporária não atende. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/13327111?hl=en) (2026-10-16) |
| GPRG-006 | WARNING | PRIV-SDK-DATA-DISCLOSURE (mandatory) | Data safety | firebase_analytics (analytics: revisar App activity, Device or other IDs) | heuristic | medium | Confira a documentação de Data safety de cada SDK (SDK Index) e, se necessário, instrumente o tráfego (ex.: proxy em build de teste) antes de preencher o formulário. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/10787469?hl=en) (2026-10-16) |
| GPRG-007 | INFO | TECH-AAB-REQUIRED (mandatory) | app-release.apk | artefato analisado é APK | deterministic | high | Para publicação, envie o Android App Bundle (.aab). Use o APK apenas para testes/sideload. | [developer.android.com/guide/app-bundle](https://developer.android.com/guide/app-bundle) (2026-10-16) |
| GPRG-008 | INFO | TECH-AGP-16KB (recommended) | android/app/build.gradle | ndkVersion=28.0.13004108 (r28+ compila 16 KB por padrão; libs pré-compiladas de terceiros não são afetadas) | heuristic | medium | - | [developer.android.com/guide/practices/page-sizes](https://developer.android.com/guide/practices/page-sizes) (2026-10-16) |
| GPRG-009 | INFO | TECH-AGP-16KB (recommended) | android/settings.gradle | com.android.application 8.7.0 | heuristic | high | - | [developer.android.com/guide/practices/page-sizes](https://developer.android.com/guide/practices/page-sizes) (2026-10-16) |
| GPRG-010 | INFO | SEC-ALLOW-BACKUP (recommended) | app-release.aab:AndroidManifest.xml | android:allowBackup=None (ausente = true) | deterministic | high | Avalie dataExtractionRules/fullBackupContent para excluir tokens e dados sensíveis do backup. | [developer.android.com/privacy-and-security/risks](https://developer.android.com/privacy-and-security/risks/backup-best-practices) (2026-10-16) |
| GPRG-011 | INFO | SEC-ALLOW-BACKUP (recommended) | app-release.apk:AndroidManifest.xml | android:allowBackup=None (ausente = true) | deterministic | high | Avalie dataExtractionRules/fullBackupContent para excluir tokens e dados sensíveis do backup. | [developer.android.com/privacy-and-security/risks](https://developer.android.com/privacy-and-security/risks/backup-best-practices) (2026-10-16) |
| GPRG-012 | INFO | PRIV-ADVERTISING-ID (mandatory) | app-release.aab | AD_ID ausente: o Advertising ID retornará zeros em target ≥ 33 | deterministic | high | Declare 'não usa Advertising ID' no Play Console se for o caso. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/6048248?hl=en) (2026-10-16) |
| GPRG-013 | INFO | TECH-BILLING-LIBRARY-VERSION (mandatory) | monetização | billing_client=7.1.1 | deterministic | high | Planeje a migração. | [developer.android.com/google/play/billing/deprec](https://developer.android.com/google/play/billing/deprecation-faq) (2026-10-16) |
| GPRG-014 | PASS | TECH-64BIT (mandatory) | app-release.aab | ABIs: arm64-v8a, armeabi-v7a | deterministic | high | - | [developer.android.com/google/play/requirements/6](https://developer.android.com/google/play/requirements/64-bit) (2026-10-16) |
| GPRG-015 | PASS | TECH-64BIT (mandatory) | app-release.apk | ABIs: arm64-v8a | deterministic | high | - | [developer.android.com/google/play/requirements/6](https://developer.android.com/google/play/requirements/64-bit) (2026-10-16) |
| GPRG-016 | PASS | TECH-AAB-REQUIRED (mandatory) | app-release.aab | BundleConfig.pb + base/manifest presentes | deterministic | high | - | [developer.android.com/guide/app-bundle](https://developer.android.com/guide/app-bundle) (2026-10-16) |
| GPRG-017 | PASS | TECH-ARTIFACT-INTEGRITY (mandatory) | app-release.aab | 9 entradas; CRC verificado=True; módulos=base; deterministic (decodificador protobuf aapt2 XmlNode em Python) | deterministic | high | - | [developer.android.com/guide/app-bundle](https://developer.android.com/guide/app-bundle) (2026-10-16) |
| GPRG-018 | PASS | TECH-ARTIFACT-INTEGRITY (mandatory) | app-release.apk | 4 entradas; CRC verificado=True; manifest: deterministic (parser AXML em Python) | deterministic | high | - | [developer.android.com/guide/app-bundle](https://developer.android.com/guide/app-bundle) (2026-10-16) |
| GPRG-019 | PASS | TECH-DEBUGGABLE (mandatory) | android/app/build.gradle | debuggable ausente/false em release | deterministic | high | - | [developer.android.com/studio/publish/preparing](https://developer.android.com/studio/publish/preparing) (2026-10-16) |
| GPRG-020 | PASS | TECH-DEBUGGABLE (mandatory) | app-release.aab:AndroidManifest.xml | android:debuggable=None (ausente = false) | deterministic | high | - | [developer.android.com/studio/publish/preparing](https://developer.android.com/studio/publish/preparing) (2026-10-16) |
| GPRG-021 | PASS | TECH-DEBUGGABLE (mandatory) | app-release.apk:AndroidManifest.xml | android:debuggable=None (ausente = false) | deterministic | high | - | [developer.android.com/studio/publish/preparing](https://developer.android.com/studio/publish/preparing) (2026-10-16) |
| GPRG-022 | PASS | TECH-EXPORTED-EXPLICIT (mandatory) | app-release.aab:AndroidManifest.xml | 1 componentes com intent-filter | deterministic | high | - | [developer.android.com/about/versions/12/behavior](https://developer.android.com/about/versions/12/behavior-changes-12) (2026-10-16) |
| GPRG-023 | PASS | TECH-EXPORTED-EXPLICIT (mandatory) | app-release.apk:AndroidManifest.xml | 1 componentes com intent-filter | deterministic | high | - | [developer.android.com/about/versions/12/behavior](https://developer.android.com/about/versions/12/behavior-changes-12) (2026-10-16) |
| GPRG-024 | PASS | TECH-PACKAGE-NAME (mandatory) | app-release.aab:AndroidManifest.xml | package=com.example.app | deterministic | high | - | [developer.android.com/studio/publish/preparing](https://developer.android.com/studio/publish/preparing) (2026-10-16) |
| GPRG-025 | PASS | TECH-PACKAGE-NAME (mandatory) | app-release.apk:AndroidManifest.xml | package=com.example.app | deterministic | high | - | [developer.android.com/studio/publish/preparing](https://developer.android.com/studio/publish/preparing) (2026-10-16) |
| GPRG-026 | PASS | TECH-R8-MINIFY (recommended) | android/app/build.gradle | minifyEnabled=True shrinkResources=False | deterministic | high | - | [developer.android.com/studio/publish/preparing](https://developer.android.com/studio/publish/preparing) (2026-10-16) |
| GPRG-027 | PASS | TECH-VERSION-CODE (mandatory) | app-release.aab:AndroidManifest.xml | versionCode=45 (limite 2100000000) | deterministic | high | - | [developer.android.com/studio/publish/versioning](https://developer.android.com/studio/publish/versioning) (2026-10-16) |
| GPRG-028 | PASS | TECH-VERSION-CODE (mandatory) | app-release.apk:AndroidManifest.xml | versionCode=45 (limite 2100000000) | deterministic | high | - | [developer.android.com/studio/publish/versioning](https://developer.android.com/studio/publish/versioning) (2026-10-16) |
| GPRG-029 | PASS | TECH-TARGET-API-LEVEL (mandatory) | app-release.aab | targetSdk=36; mínimo=36 (Android 16) para phone/tablet, new_app/app_update (tipo de envio não informado), vigente desde 2026-08-31 [regra TECH-TARGET-API-LEVEL, consultada 2026-10-16]; fonte: manifest mesclado do artefato | deterministic | high | - | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/11926878?hl=en) (2026-10-16) |
| GPRG-030 | PASS | TECH-16KB-PAGE-SIZE (mandatory) | app-release.aab | 1/1 libs 64-bit com p_align ≥ 0x4000 [python-elf] | deterministic | high | - | [developer.android.com/guide/practices/page-sizes](https://developer.android.com/guide/practices/page-sizes) (2026-10-16) |
| GPRG-031 | PASS | TECH-16KB-PAGE-SIZE (mandatory) | app-release.apk | 1/1 libs 64-bit com p_align ≥ 0x4000 [python-elf] | deterministic | high | - | [developer.android.com/guide/practices/page-sizes](https://developer.android.com/guide/practices/page-sizes) (2026-10-16) |
| GPRG-032 | PASS | TECH-16KB-ZIP-ALIGN (mandatory) | app-release.apk | 1 .so 64-bit não comprimidas com offset % 16384 == 0 [python-zip] | deterministic | high | - | [developer.android.com/guide/practices/page-sizes](https://developer.android.com/guide/practices/page-sizes) (2026-10-16) |
| GPRG-033 | PASS | TECH-DEBUG-SIGNATURE (mandatory) | app-release.aab | CN=Upload Key SHA-256=C1:14:81:61:59:AF:1E:22:D5:F1:A2:75:B5:AF:A5:6B:4F:45:C1:28:3F:0E:D0:42:B1:40:AA:ED:CF:84:0D:BA | deterministic | high | - | [developer.android.com/studio/publish/app-signing](https://developer.android.com/studio/publish/app-signing) (2026-10-16) |
| GPRG-034 | PASS | TECH-DEBUG-SIGNATURE (mandatory) | app-release.apk | CN=Release Key SHA-256=4A:45:20:E2:CE:48:86:12:4F:96:72:AA:61:BA:81:AD:98:46:59:9B:B8:29:A3:E9:A1:33:50:C5:6D:B3:F5:50 | deterministic | high | - | [developer.android.com/studio/publish/app-signing](https://developer.android.com/studio/publish/app-signing) (2026-10-16) |
| GPRG-035 | PASS | TECH-UPLOAD-KEY (mandatory) | app-release.aab | RSA 2048 bits; SHA-256=C1:14:81:61:59:AF:1E:22:D5:F1:A2:75:B5:AF:A5:6B:4F:45:C1:28:3F:0E:D0:42:B1:40:AA:ED:CF:84:0D:BA | deterministic | high | - | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/9842756?hl=en) (2026-10-16) |
| GPRG-036 | PASS | SEC-CLEARTEXT (recommended) | app-release.aab:AndroidManifest.xml | usesCleartextTraffic=None targetSdk=36 (padrão false desde API 28) | deterministic | high | - | [developer.android.com/privacy-and-security/secur](https://developer.android.com/privacy-and-security/security-config) (2026-10-16) |
| GPRG-037 | PASS | SEC-CLEARTEXT (recommended) | app-release.apk:AndroidManifest.xml | usesCleartextTraffic=None targetSdk=36 (padrão false desde API 28) | deterministic | high | - | [developer.android.com/privacy-and-security/secur](https://developer.android.com/privacy-and-security/security-config) (2026-10-16) |
| GPRG-038 | NOT_VERIFIED | TECH-BUNDLETOOL-VALIDATE (recommended) | app-release.aab | NOT_VERIFIED: desativado (--no-tools) | manual | high | Instale um JRE e o bundletool e rode `java -jar bundletool.jar validate --bundle=<app>.aab`. | [developer.android.com/tools/bundletool](https://developer.android.com/tools/bundletool) (2026-10-16) |
| GPRG-039 | NOT_VERIFIED | TECH-16KB-ZIP-ALIGN (mandatory) | app-release.aab | o alinhamento por offset vale para APKs; o .aab é convertido pelo Google Play | manual | high | Gere APKs com `bundletool build-apks --bundle=app.aab --output=app.apks` e rode `zipalign -c -P 16 -v 4` nos splits; confira o aviso de 16 KB no App bundle explorer. | [developer.android.com/guide/practices/page-sizes](https://developer.android.com/guide/practices/page-sizes) (2026-10-16) |
| GPRG-040 | NOT_VERIFIED | TECH-PLAY-APP-SIGNING (mandatory) | app-release.aab | A assinatura do .aab é a da chave de upload; os APKs distribuídos são re-assinados pelo Google Play. | manual | high | Confira Play Console > Test and release > App integrity > Play app signing; registre o SHA-256 da app signing key em APIs/App Links. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/9842756?hl=en) (2026-10-16) |
| GPRG-041 | NOT_VERIFIED | TECH-SIGNATURE-VERIFY (mandatory) | app-release.aab | jarsigner indisponível (requer JDK, não só JRE); esquemas lidos: v1/jar; certificados extraídos sem validação de assinatura | manual | high | Rode `jarsigner -verify -verbose -certs app-release.aab` (requer JDK); o Play Console também valida no upload. | [developer.android.com/tools/apksigner](https://developer.android.com/tools/apksigner) (2026-10-16) |
| GPRG-042 | NOT_VERIFIED | TECH-SIGNATURE-VERIFY (mandatory) | app-release.apk | apksigner indisponível: desativado (--no-tools); esquemas detectados por parsing: v1/jar | manual | high | Instale um JRE e rode `apksigner verify --print-certs --verbose app-release.apk`. | [developer.android.com/tools/apksigner](https://developer.android.com/tools/apksigner) (2026-10-16) |
| GPRG-043 | NOT_VERIFIED | SEC-SIGNING-SECRETS-IN-VCS (recommended) | android/key.properties | projeto fora de um repositório Git ou git indisponível (conteúdo não lido) | manual | high | - | [developer.android.com/studio/publish/app-signing](https://developer.android.com/studio/publish/app-signing) (2026-10-16) |
| GPRG-044 | NOT_VERIFIED | SEC-SIGNING-SECRETS-IN-VCS (recommended) | android/upload.jks | projeto fora de um repositório Git ou git indisponível (conteúdo não lido) | manual | high | - | [developer.android.com/studio/publish/app-signing](https://developer.android.com/studio/publish/app-signing) (2026-10-16) |
| GPRG-045 | NOT_VERIFIED | TECH-MERGED-MANIFEST (recommended) | android/app/src/main/AndroidManifest.xml | auditoria de fonte; o manifest final só existe no artefato | manual | high | Rode também `guardian.py audit --aab build/app/outputs/bundle/release/app-release.aab` (ou gate --aab) para auditar o manifest mesclado. | [developer.android.com/build/manage-manifests](https://developer.android.com/build/manage-manifests) (2026-10-16) |
| GPRG-046 | NOT_VERIFIED | POL-SDK-INDEX (recommended) | dependências | 3 dependências categorizadas; 0 bibliotecas Maven no bundle | manual | high | Consulte https://play.google.com/sdks para cada SDK de terceiros (avisos de política/versões desatualizadas). | [play.google.com/sdks](https://play.google.com/sdks) (2026-10-16) |
| GPRG-047 | NOT_VERIFIED | PRIV-DATA-SAFETY-FORM (mandatory) | Play Console > App content > Data safety | sem declarações fornecidas (--declarations) | manual | high | Exporte/compare o formulário Data safety com o inventário de SDKs deste relatório. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/10787469?hl=en) (2026-10-16) |
| GPRG-048 | NOT_VERIFIED | PRIV-PRIVACY-POLICY (mandatory) | Play Console > Store listing / App content | não informada | manual | high | Garanta link válido no store listing e dentro do app, cobrindo coleta, uso, compartilhamento, retenção e exclusão. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/10144311?hl=en) (2026-10-16) |
| GPRG-049 | NOT_VERIFIED | POL-PLAY-BILLING (mandatory) | monetização | Play Billing presente: in_app_purchase, permissão BILLING | manual | high | Confirme que bens/serviços digitais são vendidos pelo Play Billing, com preço, renovação automática, testes gratuitos e cancelamento claros. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/9858738?hl=en) (2026-10-16) |
| GPRG-050 | NOT_VERIFIED | POL-ADS-DECLARATION (mandatory) | Play Console | não disponível offline/sem declarações | manual | high | Verificar manualmente no Play Console. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/9857753?hl=en) (2026-10-16) |
| GPRG-051 | NOT_VERIFIED | POL-APP-ACCESS (mandatory) | Play Console | não disponível offline/sem declarações | manual | high | Verificar manualmente no Play Console. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/15748846?hl=en) (2026-10-16) |
| GPRG-052 | NOT_VERIFIED | POL-CONTENT-RATING (mandatory) | Play Console | não disponível offline/sem declarações | manual | high | Verificar manualmente no Play Console. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/9898843?hl=en) (2026-10-16) |
| GPRG-053 | NOT_VERIFIED | POL-PERMISSIONS-DECLARATION (mandatory) | Play Console | não disponível offline/sem declarações | manual | high | Verificar manualmente no Play Console. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/9214102?hl=en) (2026-10-16) |
| GPRG-054 | NOT_VERIFIED | POL-STORE-LISTING (mandatory) | Play Console | não disponível offline/sem declarações | manual | high | Verificar manualmente no Play Console. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/9898842?hl=en) (2026-10-16) |
| GPRG-055 | NOT_VERIFIED | POL-TARGET-AUDIENCE (mandatory) | Play Console | não disponível offline/sem declarações | manual | high | Verificar manualmente no Play Console. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/9893335?hl=en) (2026-10-16) |
| GPRG-056 | NOT_VERIFIED | PRIV-DATA-SAFETY-FORM (mandatory) | Play Console | não disponível offline/sem declarações | manual | high | Verificar manualmente no Play Console. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/10787469?hl=en) (2026-10-16) |
| GPRG-057 | NOT_VERIFIED | TECH-VERSION-CODE-INCREMENT (mandatory) | app-release.aab:AndroidManifest.xml | versionCode=45 | manual | high | Confira em Play Console > App bundle explorer que o versionCode é maior que o último enviado. | [developer.android.com/studio/publish/versioning](https://developer.android.com/studio/publish/versioning) (2026-10-16) |
| GPRG-058 | NOT_VERIFIED | TECH-VERSION-CODE-INCREMENT (mandatory) | app-release.apk:AndroidManifest.xml | versionCode=45 | manual | high | Confira em Play Console > App bundle explorer que o versionCode é maior que o último enviado. | [developer.android.com/studio/publish/versioning](https://developer.android.com/studio/publish/versioning) (2026-10-16) |
| GPRG-059 | NOT_VERIFIED | POL-CLOSED-TESTING (mandatory) | Play Console > Dashboard | informe --account-type personal\|organization e --account-created YYYY-MM-DD | manual | high | Se conta pessoal criada após 2023-11-13: teste fechado com ≥ 12 testadores por ≥ 14 dias consecutivos e solicite acesso à produção. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/14151465?hl=en) (2026-10-16) |
| GPRG-060 | NOT_VERIFIED | TECH-FUNCTIONAL-TESTS (recommended) | - | adb indisponível; instalação em dispositivo exige autorização explícita | manual | high | Execute `flutter test integration_test` / Espresso / UI Automator e o pre-launch report do Play Console; nunca classificado como aprovado sem execução. | [developer.android.com/studio/test](https://developer.android.com/studio/test) (2026-10-16) |

## Permissões

| Permissão | Regra | Origem provável | Declarada em |
|---|---|---|---|
| android.permission.INTERNET | - | não determinado (verifique o manifest-merger-report) | manifest mesclado do artefato |
| android.permission.ACCESS_BACKGROUND_LOCATION | POL-BACKGROUND-LOCATION | não determinado (verifique o manifest-merger-report) | manifest mesclado do artefato |
| com.android.vending.BILLING | - | Play Billing Library (in_app_purchase) | manifest mesclado do artefato |

## Bibliotecas nativas

| Biblioteca | ABI | ELF 16 KB | Detalhe | Offset ZIP 16 KB |
|---|---|---|---|---|
| base/lib/arm64-v8a/libapp.so | arm64-v8a | True | PT_LOAD align 0x4000 [python-elf] | n/a |
| base/lib/armeabi-v7a/libapp.so | armeabi-v7a | True | PT_LOAD align 0x4000 [python-elf] | n/a |
| lib/arm64-v8a/libapp.so | arm64-v8a | True | PT_LOAD align 0x4000 [python-elf] | True |

## SDKs relevantes para privacidade/cobrança (indícios, não prova de coleta)

| SDK | Versão | Categoria | Tipos a revisar |
|---|---|---|---|
| flutter | - | - | - |
| firebase_analytics | 11.3.0 | analytics | App activity, Device or other IDs |
| in_app_purchase | 3.2.0 | billing_play | Purchase history |

## Regras

- Base: 67 regras; digest `18733447bfa8380e`; consulta mais recente 2026-10-16
- Status das fontes: fontes não reconsultadas (rode `guardian.py rules --check-sources` antes de auditorias críticas)

## Limitações

- Google Play Developer API não consultada (nenhuma credencial fornecida); metadados do Console exigem verificação manual.
- Ferramentas do Android SDK desativadas (--no-tools): só parsers Python foram usados.
- O alinhamento ZIP de 16 KB dos APKs que o Google Play gera a partir do .aab não é observável no bundle; o auditor verifica ELF e APKs locais.
- Políticas de conteúdo (UGC, IP, malware, abuso de dispositivo, Families) exigem revisão humana; o auditor não as avalia automaticamente.
- Coleta de dados não é inferida de dependências: inventário de SDKs é apenas indício para o Data safety.
- READY nunca significa aprovação garantida pelo Google Play.
