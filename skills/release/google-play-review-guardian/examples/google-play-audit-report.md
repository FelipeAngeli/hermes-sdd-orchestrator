# Google Play Review Guardian — relatório de auditoria

```text
GOOGLE PLAY REVIEW GUARDIAN
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Application: Mently
Package: br.com.mently.app.mently
Version: 1.5.3
Version Code: 46
Min/Target/Compile SDK: 24 / 36 / 36
Mode: gate   Generated: 2026-10-16T23:37:36+00:00

ANDROID BUILD
Status: WARNING

TARGET SDK
Status: PASS

16 KB PAGE COMPATIBILITY
Status: NEEDS_REVIEW

SIGNING
Status: NEEDS_REVIEW

SECURITY
Status: BLOCKED

PERMISSIONS
Status: WARNING

DATA SAFETY
Status: WARNING

GOOGLE PLAY BILLING
Status: NEEDS_REVIEW

PLAY CONSOLE METADATA
Status: NOT_VERIFIED

TESTING / PRODUCTION ACCESS
Status: NOT_VERIFIED

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FINAL STATUS: BLOCKED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FINDINGS:

[CRITICAL] Arquivo de assinatura versionado no Git.
[CRITICAL] Arquivo de assinatura versionado no Git.
[WARNING] Artefato não corresponde à versão atual do projeto.
[WARNING] Tipos de foreground service exigem declaração no Play Console.
[WARNING] Criação de conta detectada: verificar exclusão no app e via web.
[WARNING] SDKs com possível coleta de dados: confirmar declarações do Data safety.
[INFO] APK não substitui o AAB para novos apps.
[INFO] AAB e APK auditados têm versionCode diferentes.
[INFO] Backup automático habilitado.
[INFO] Backup automático habilitado.
[INFO] Deep links com esquema customizado.
[INFO] Deep links com esquema customizado.
[INFO] 9 componente(s) exportado(s) sem permissão de proteção.
[INFO] 9 componente(s) exportado(s) sem permissão de proteção.
[INFO] Permissões sensíveis: solicitar em contexto e justificar.
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
[PASS] Segmentos ELF PT_LOAD alinhados a 16 KB (8 libs 64-bit).
[PASS] Segmentos ELF PT_LOAD alinhados a 16 KB (8 libs 64-bit).
[PASS] Offsets ZIP das .so alinhados a 16 KB.
[PASS] zipalign -c -P 16 confirmou o alinhamento.
[PASS] NDK 28.2.13676358.
[PASS] AGP 8.12.2 ≥ 8.5.1.
[PASS] Certificado não é de debug.
[PASS] Certificado não é de debug.
[PASS] jarsigner -verify aprovou digests e assinaturas do bundle.
[PASS] apksigner verify aprovou a assinatura.
[PASS] Chave de upload RSA ≥ 2048 bits.
[PASS] Cleartext desabilitado no manifest.
[PASS] Cleartext desabilitado no manifest.
[PASS] Play Billing Library em versão suportada.
[NOT_VERIFIED] Alinhamento ZIP dos APKs gerados pelo Play não verificável a partir do .aab.
[NOT_VERIFIED] Inscrição no Play App Signing não verificável offline.
[NOT_VERIFIED] App Links com autoVerify exigem assetlinks.json válido.
[NOT_VERIFIED] App Links com autoVerify exigem assetlinks.json válido.
[NOT_VERIFIED] networkSecurityConfig declarado; conteúdo precisa ser revisado.
[NOT_VERIFIED] networkSecurityConfig declarado; conteúdo precisa ser revisado.
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

1. [CRITICAL] SEC-SIGNING-SECRETS-IN-VCS: Remova do índice com `git rm --cached android/key.properties`, adicione ao .gitignore e avalie rotacionar a chave de upload no Play Console (o histórico Git mantém cópias).
2. [CRITICAL] SEC-SIGNING-SECRETS-IN-VCS: Remova do índice com `git rm --cached android/keystore_mently.jks`, adicione ao .gitignore e avalie rotacionar a chave de upload no Play Console (o histórico Git mantém cópias).
3. [WARNING] TECH-ARTIFACT-FRESHNESS: Gere novamente o build release (`flutter build appbundle --release`) antes do envio e audite o artefato novo.
4. [WARNING] POL-FOREGROUND-SERVICE: Em Play Console > App content > Foreground service permissions, descreva cada tipo, o impacto se adiado/interrompido e anexe vídeo.
5. [WARNING] PRIV-ACCOUNT-DELETION: Ofereça caminho no app para excluir conta e dados e um link web funcional; informe a URL no Data safety. Desativação temporária não atende.
6. [WARNING] PRIV-SDK-DATA-DISCLOSURE: Confira a documentação de Data safety de cada SDK (SDK Index) e, se necessário, instrumente o tráfego (ex.: proxy em build de teste) antes de preencher o formulário.
7. [NOT_VERIFIED] TECH-16KB-ZIP-ALIGN: Gere APKs com `bundletool build-apks --bundle=app.aab --output=app.apks` e rode `zipalign -c -P 16 -v 4` nos splits; confira o aviso de 16 KB no App bundle explorer.
8. [NOT_VERIFIED] TECH-PLAY-APP-SIGNING: Confira Play Console > Test and release > App integrity > Play app signing; registre o SHA-256 da app signing key em APIs/App Links.
9. [NOT_VERIFIED] SEC-APP-LINKS: Verifique https://<host>/.well-known/assetlinks.json com o SHA-256 da chave de assinatura do Play (App signing key).
10. [NOT_VERIFIED] SEC-NETWORK-SECURITY-CONFIG: Revise res/xml do network security config (cleartextTrafficPermitted, trust-anchors de usuário, debug-overrides).
11. [NOT_VERIFIED] TECH-MERGED-MANIFEST: Rode também `guardian.py audit --aab build/app/outputs/bundle/release/app-release.aab` (ou gate --aab) para auditar o manifest mesclado.
12. [NOT_VERIFIED] POL-SDK-INDEX: Consulte https://play.google.com/sdks para cada SDK de terceiros (avisos de política/versões desatualizadas).
13. [NOT_VERIFIED] PRIV-DATA-SAFETY-FORM: Exporte/compare o formulário Data safety com o inventário de SDKs deste relatório.
14. [NOT_VERIFIED] PRIV-PRIVACY-POLICY: Garanta link válido no store listing e dentro do app, cobrindo coleta, uso, compartilhamento, retenção e exclusão.
15. [NOT_VERIFIED] POL-PLAY-BILLING: Confirme que bens/serviços digitais são vendidos pelo Play Billing, com preço, renovação automática, testes gratuitos e cancelamento claros.
16. [NOT_VERIFIED] POL-ADS-DECLARATION: Verificar manualmente no Play Console.
17. [NOT_VERIFIED] POL-APP-ACCESS: Verificar manualmente no Play Console.
18. [NOT_VERIFIED] POL-CONTENT-RATING: Verificar manualmente no Play Console.
19. [NOT_VERIFIED] POL-PERMISSIONS-DECLARATION: Verificar manualmente no Play Console.
20. [NOT_VERIFIED] POL-STORE-LISTING: Verificar manualmente no Play Console.
21. [NOT_VERIFIED] POL-TARGET-AUDIENCE: Verificar manualmente no Play Console.
22. [NOT_VERIFIED] PRIV-DATA-SAFETY-FORM: Verificar manualmente no Play Console.
23. [NOT_VERIFIED] TECH-VERSION-CODE-INCREMENT: Confira em Play Console > App bundle explorer que o versionCode é maior que o último enviado.
24. [NOT_VERIFIED] POL-CLOSED-TESTING: Se conta pessoal criada após 2023-11-13: teste fechado com ≥ 12 testadores por ≥ 14 dias consecutivos e solicite acesso à produção.
25. [NOT_VERIFIED] TECH-FUNCTIONAL-TESTS: Execute `flutter test integration_test` / Espresso / UI Automator e o pre-launch report do Play Console; nunca classificado como aprovado sem execução.
26. Execute novamente a auditoria após as correções (gate --release).
```

> READY significa apenas ausência de bloqueios nas verificações executadas; não garante aprovação pelo Google Play.

## Alvo e cobertura

- Alvo: `{"project": "~/Documents/KelTech/mently/application/apps/app", "aab": "~/Documents/KelTech/mently/application/apps/app/build/app/outputs/bundle/release/app-release.aab", "apk": "~/Documents/KelTech/mently/application/apps/app/build/app/outputs/flutter-apk/app-release.apk", "aab_sha256": null, "apk_sha256": null, "project_type": "flutter", "form_factor": "phone/tablet", "submission": null}`
- mode: gate
- artifacts_autodiscovered: ['~/Documents/KelTech/mently/application/apps/app/build/app/outputs/bundle/release/app-release.aab', '~/Documents/KelTech/mently/application/apps/app/build/app/outputs/flutter-apk/app-release.apk']
- network: não utilizada na auditoria (somente rules --check-sources e API opcional)
- project_type: flutter
- gradle_file: android/app/build.gradle
- form_factor: phone/tablet (nenhum uses-feature de outro formato)
- source_files_scanned: 606

## Ferramentas

| Ferramenta | Disponível | Caminho/motivo |
|---|---|---|
| aapt2 | sim | ~/Library/Android/sdk/build-tools/37.0.0/aapt2  |
| adb | sim | /opt/homebrew/bin/adb  |
| apkanalyzer | sim | ~/Library/Android/sdk/cmdline-tools/latest/bin/apkanalyzer  |
| apksigner | sim | ~/Library/Android/sdk/build-tools/37.0.0/apksigner  |
| bundletool | sim | ~/Library/Android/sdk/bundletool/bundletool-all-1.18.3.jar  |
| java | sim | /Applications/Android Studio.app/Contents/jbr/Contents/Home/bin/java  |
| llvm-readelf | sim | ~/Library/Android/sdk/ndk/30.0.14904198/toolchains/llvm/prebuilt/darwin-x86_64/bin/llvm-readelf  |
| zipalign | sim | ~/Library/Android/sdk/build-tools/37.0.0/zipalign  |

## Findings detalhados

| ID | Severidade | Regra | Componente | Evidência | Método | Confiança | Recomendação | Fonte (consulta) |
|---|---|---|---|---|---|---|---|---|
| GPRG-001 | CRITICAL | SEC-SIGNING-SECRETS-IN-VCS (recommended) | android/key.properties | android/key.properties está rastreado pelo Git (conteúdo NÃO lido/impresso) | deterministic | high | Remova do índice com `git rm --cached android/key.properties`, adicione ao .gitignore e avalie rotacionar a chave de upload no Play Console (o histórico Git mantém cópias). | [developer.android.com/studio/publish/app-signing](https://developer.android.com/studio/publish/app-signing) (2026-10-16) |
| GPRG-002 | CRITICAL | SEC-SIGNING-SECRETS-IN-VCS (recommended) | android/keystore_mently.jks | android/keystore_mently.jks está rastreado pelo Git (conteúdo NÃO lido/impresso) | deterministic | high | Remova do índice com `git rm --cached android/keystore_mently.jks`, adicione ao .gitignore e avalie rotacionar a chave de upload no Play Console (o histórico Git mantém cópias). | [developer.android.com/studio/publish/app-signing](https://developer.android.com/studio/publish/app-signing) (2026-10-16) |
| GPRG-003 | WARNING | TECH-ARTIFACT-FRESHNESS (recommended) | app-release.aab | pubspec version=1.5.3+47 vs artefato versionCode=46 versionName=1.5.3 | deterministic | high | Gere novamente o build release (`flutter build appbundle --release`) antes do envio e audite o artefato novo. | [developer.android.com/studio/publish/versioning](https://developer.android.com/studio/publish/versioning) (2026-10-16) |
| GPRG-004 | WARNING | POL-FOREGROUND-SERVICE (mandatory) | app-release.aab | tipos: mediaPlayback; serviços: com.ryanheise.audioservice.AudioService | deterministic | high | Em Play Console > App content > Foreground service permissions, descreva cada tipo, o impacto se adiado/interrompido e anexe vídeo. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/13392821?hl=en) (2026-10-16) |
| GPRG-005 | WARNING | PRIV-ACCOUNT-DELETION (mandatory) | Data safety | criação: lib/ui/app_routes.dart:8 'signup'; lib/ui/widgets/button/login_with.dart:4 'signup'; lib/ui/features/signup/signup_page.dart:16 'signup'; URL web de exclusão não informada | heuristic | medium | Ofereça caminho no app para excluir conta e dados e um link web funcional; informe a URL no Data safety. Desativação temporária não atende. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/13327111?hl=en) (2026-10-16) |
| GPRG-006 | WARNING | PRIV-SDK-DATA-DISCLOSURE (mandatory) | Data safety | flutter_appauth (auth: revisar User IDs); sentry_flutter (crash: revisar Crash logs, Diagnostics); image_picker (media: revisar Photos, Videos); onesignal_flutter (push: revisar Device or other IDs, App interactions); com.onesignal:OneSignal:5.7.2 (push: revisar Device or other IDs, App interactions); com.onesignal:core:5.7.2 (push: revisar Device or other IDs, App interactions); com.onesignal:otel:5.7.2 (push: revisar Device or other IDs, App interactions); com.onesignal:notifications:5.7.2 (push: revisar Device or other IDs, App interactions); com.onesignal:in-app-messages:5.7.2 (push: revisar Device or other IDs, App interactions); com.onesignal:location:5.7.2 (push: revisar Device or other IDs, App interactions); io.sentry:sentry-android:7.22.4 (crash: revisar Crash logs, Diagnostics); io.sentry:sentry-android-core:7.22.4 (crash: revisar Crash logs, Diagnostics); io.sentry:sentry:7.22.4 (crash: revisar Crash logs, Diagnostics); io.sentry:sentry-android-ndk:7.22.4 (crash: revisar Crash logs, Diagnostics); io.sentry:sentry-android-replay:7.22.4 (crash: revisar Crash logs, Diagnostics) | heuristic | medium | Confira a documentação de Data safety de cada SDK (SDK Index) e, se necessário, instrumente o tráfego (ex.: proxy em build de teste) antes de preencher o formulário. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/10787469?hl=en) (2026-10-16) |
| GPRG-007 | INFO | TECH-AAB-REQUIRED (mandatory) | app-release.apk | artefato analisado é APK | deterministic | high | Para publicação, envie o Android App Bundle (.aab). Use o APK apenas para testes/sideload. | [developer.android.com/guide/app-bundle](https://developer.android.com/guide/app-bundle) (2026-10-16) |
| GPRG-008 | INFO | TECH-ARTIFACT-FRESHNESS (recommended) | artefatos | aab=46 apk=47 | deterministic | high | Confirme qual artefato será enviado; o Google Play exige o .aab para novos apps. | [developer.android.com/studio/publish/versioning](https://developer.android.com/studio/publish/versioning) (2026-10-16) |
| GPRG-009 | INFO | SEC-ALLOW-BACKUP (recommended) | app-release.aab:AndroidManifest.xml | android:allowBackup=None (ausente = true) | deterministic | high | Avalie dataExtractionRules/fullBackupContent para excluir tokens e dados sensíveis do backup. | [developer.android.com/privacy-and-security/risks](https://developer.android.com/privacy-and-security/risks/backup-best-practices) (2026-10-16) |
| GPRG-010 | INFO | SEC-ALLOW-BACKUP (recommended) | app-release.apk:AndroidManifest.xml | android:allowBackup=None (ausente = true) | deterministic | high | Avalie dataExtractionRules/fullBackupContent para excluir tokens e dados sensíveis do backup. | [developer.android.com/privacy-and-security/risks](https://developer.android.com/privacy-and-security/risks/backup-best-practices) (2026-10-16) |
| GPRG-011 | INFO | SEC-CUSTOM-SCHEME (recommended) | app-release.aab:AndroidManifest.xml | schemes: br.com.mently.app.mently, mently | deterministic | high | Valide parâmetros recebidos por deep links; esquemas customizados podem ser registrados por outros apps. | [developer.android.com/privacy-and-security/risks](https://developer.android.com/privacy-and-security/risks/unsafe-use-of-deeplinks) (2026-10-16) |
| GPRG-012 | INFO | SEC-CUSTOM-SCHEME (recommended) | app-release.apk:AndroidManifest.xml | schemes: br.com.mently.app.mently, mently | deterministic | high | Valide parâmetros recebidos por deep links; esquemas customizados podem ser registrados por outros apps. | [developer.android.com/privacy-and-security/risks](https://developer.android.com/privacy-and-security/risks/unsafe-use-of-deeplinks) (2026-10-16) |
| GPRG-013 | INFO | SEC-EXPORTED-COMPONENTS (recommended) | app-release.aab:AndroidManifest.xml | service:com.ryanheise.audioservice.AudioService; receiver:com.ryanheise.audioservice.MediaButtonReceiver; activity:net.openid.appauth.RedirectUriReceiverActivity; activity:com.onesignal.NotificationOpenedActivityHMS; receiver:com.onesignal.notifications.receivers.NotificationDismissReceiver; receiver:com.onesignal.notifications.receivers.BootUpReceiver; receiver:com.onesignal.notifications.receivers.UpgradeReceiver; activity:com.onesignal.notifications.activities.NotificationOpenedActivity; activity:com.onesignal.notifications.activities.NotificationOpenedActivityAndroid22AndOlder | heuristic | medium | Revise se cada componente precisa ser exportado; use android:exported="false" ou android:permission. | [developer.android.com/privacy-and-security/risks](https://developer.android.com/privacy-and-security/risks/android-exported) (2026-10-16) |
| GPRG-014 | INFO | SEC-EXPORTED-COMPONENTS (recommended) | app-release.apk:AndroidManifest.xml | service:com.ryanheise.audioservice.AudioService; receiver:com.ryanheise.audioservice.MediaButtonReceiver; activity:net.openid.appauth.RedirectUriReceiverActivity; activity:com.onesignal.NotificationOpenedActivityHMS; receiver:com.onesignal.notifications.receivers.NotificationDismissReceiver; receiver:com.onesignal.notifications.receivers.BootUpReceiver; receiver:com.onesignal.notifications.receivers.UpgradeReceiver; activity:com.onesignal.notifications.activities.NotificationOpenedActivity; activity:com.onesignal.notifications.activities.NotificationOpenedActivityAndroid22AndOlder | heuristic | medium | Revise se cada componente precisa ser exportado; use android:exported="false" ou android:permission. | [developer.android.com/privacy-and-security/risks](https://developer.android.com/privacy-and-security/risks/android-exported) (2026-10-16) |
| GPRG-015 | INFO | POL-SENSITIVE-PERMISSIONS (mandatory) | app-release.aab | POST_NOTIFICATIONS | heuristic | medium | Garanta que cada permissão sustenta funcionalidade atual descrita no store listing e é pedida em contexto. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/13161072?hl=en) (2026-10-16) |
| GPRG-016 | INFO | PRIV-ADVERTISING-ID (mandatory) | app-release.aab | AD_ID ausente: o Advertising ID retornará zeros em target ≥ 33 | deterministic | high | Declare 'não usa Advertising ID' no Play Console se for o caso. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/6048248?hl=en) (2026-10-16) |
| GPRG-017 | INFO | TECH-BILLING-LIBRARY-VERSION (mandatory) | monetização | billing_client=8.0.0 | deterministic | high | Planeje a migração. | [developer.android.com/google/play/billing/deprec](https://developer.android.com/google/play/billing/deprecation-faq) (2026-10-16) |
| GPRG-018 | PASS | TECH-64BIT (mandatory) | app-release.aab | ABIs: arm64-v8a, armeabi-v7a, x86_64 | deterministic | high | - | [developer.android.com/google/play/requirements/6](https://developer.android.com/google/play/requirements/64-bit) (2026-10-16) |
| GPRG-019 | PASS | TECH-64BIT (mandatory) | app-release.apk | ABIs: arm64-v8a, armeabi-v7a, x86_64 | deterministic | high | - | [developer.android.com/google/play/requirements/6](https://developer.android.com/google/play/requirements/64-bit) (2026-10-16) |
| GPRG-020 | PASS | TECH-AAB-REQUIRED (mandatory) | app-release.aab | BundleConfig.pb + base/manifest presentes | deterministic | high | - | [developer.android.com/guide/app-bundle](https://developer.android.com/guide/app-bundle) (2026-10-16) |
| GPRG-021 | PASS | TECH-ARTIFACT-INTEGRITY (mandatory) | app-release.aab | 713 entradas; CRC verificado=True; módulos=base; deterministic (decodificador protobuf aapt2 XmlNode em Python); confirmado com aapt2 dump xmltree | deterministic | high | - | [developer.android.com/guide/app-bundle](https://developer.android.com/guide/app-bundle) (2026-10-16) |
| GPRG-022 | PASS | TECH-ARTIFACT-INTEGRITY (mandatory) | app-release.apk | 700 entradas; CRC verificado=True; manifest: deterministic (parser AXML em Python); confirmado com aapt2 dump badging | deterministic | high | - | [developer.android.com/guide/app-bundle](https://developer.android.com/guide/app-bundle) (2026-10-16) |
| GPRG-023 | PASS | TECH-DEBUGGABLE (mandatory) | android/app/build.gradle | debuggable ausente/false em release | deterministic | high | - | [developer.android.com/studio/publish/preparing](https://developer.android.com/studio/publish/preparing) (2026-10-16) |
| GPRG-024 | PASS | TECH-DEBUGGABLE (mandatory) | app-release.aab:AndroidManifest.xml | android:debuggable=None (ausente = false) | deterministic | high | - | [developer.android.com/studio/publish/preparing](https://developer.android.com/studio/publish/preparing) (2026-10-16) |
| GPRG-025 | PASS | TECH-DEBUGGABLE (mandatory) | app-release.apk:AndroidManifest.xml | android:debuggable=None (ausente = false) | deterministic | high | - | [developer.android.com/studio/publish/preparing](https://developer.android.com/studio/publish/preparing) (2026-10-16) |
| GPRG-026 | PASS | TECH-EXPORTED-EXPLICIT (mandatory) | app-release.aab:AndroidManifest.xml | 21 componentes com intent-filter | deterministic | high | - | [developer.android.com/about/versions/12/behavior](https://developer.android.com/about/versions/12/behavior-changes-12) (2026-10-16) |
| GPRG-027 | PASS | TECH-EXPORTED-EXPLICIT (mandatory) | app-release.apk:AndroidManifest.xml | 21 componentes com intent-filter | deterministic | high | - | [developer.android.com/about/versions/12/behavior](https://developer.android.com/about/versions/12/behavior-changes-12) (2026-10-16) |
| GPRG-028 | PASS | TECH-PACKAGE-NAME (mandatory) | app-release.aab:AndroidManifest.xml | package=br.com.mently.app.mently | deterministic | high | - | [developer.android.com/studio/publish/preparing](https://developer.android.com/studio/publish/preparing) (2026-10-16) |
| GPRG-029 | PASS | TECH-PACKAGE-NAME (mandatory) | app-release.apk:AndroidManifest.xml | package=br.com.mently.app.mently | deterministic | high | - | [developer.android.com/studio/publish/preparing](https://developer.android.com/studio/publish/preparing) (2026-10-16) |
| GPRG-030 | PASS | TECH-R8-MINIFY (recommended) | android/app/build.gradle | minifyEnabled=True shrinkResources=True | deterministic | high | - | [developer.android.com/studio/publish/preparing](https://developer.android.com/studio/publish/preparing) (2026-10-16) |
| GPRG-031 | PASS | TECH-VERSION-CODE (mandatory) | app-release.aab:AndroidManifest.xml | versionCode=46 (limite 2100000000) | deterministic | high | - | [developer.android.com/studio/publish/versioning](https://developer.android.com/studio/publish/versioning) (2026-10-16) |
| GPRG-032 | PASS | TECH-VERSION-CODE (mandatory) | app-release.apk:AndroidManifest.xml | versionCode=47 (limite 2100000000) | deterministic | high | - | [developer.android.com/studio/publish/versioning](https://developer.android.com/studio/publish/versioning) (2026-10-16) |
| GPRG-033 | PASS | TECH-TARGET-API-LEVEL (mandatory) | app-release.aab | targetSdk=36; mínimo=36 (Android 16) para phone/tablet, new_app/app_update (tipo de envio não informado), vigente desde 2026-08-31 [regra TECH-TARGET-API-LEVEL, consultada 2026-10-16]; fonte: manifest mesclado do artefato | deterministic | high | - | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/11926878?hl=en) (2026-10-16) |
| GPRG-034 | PASS | TECH-16KB-PAGE-SIZE (mandatory) | app-release.aab | 8/8 libs 64-bit com p_align ≥ 0x4000 [python-elf + llvm-readelf] | deterministic | high | - | [developer.android.com/guide/practices/page-sizes](https://developer.android.com/guide/practices/page-sizes) (2026-10-16) |
| GPRG-035 | PASS | TECH-16KB-PAGE-SIZE (mandatory) | app-release.apk | 8/8 libs 64-bit com p_align ≥ 0x4000 [python-elf + llvm-readelf] | deterministic | high | - | [developer.android.com/guide/practices/page-sizes](https://developer.android.com/guide/practices/page-sizes) (2026-10-16) |
| GPRG-036 | PASS | TECH-16KB-ZIP-ALIGN (mandatory) | app-release.apk | 8 .so 64-bit não comprimidas com offset % 16384 == 0 [python-zip] | deterministic | high | - | [developer.android.com/guide/practices/page-sizes](https://developer.android.com/guide/practices/page-sizes) (2026-10-16) |
| GPRG-037 | PASS | TECH-16KB-ZIP-ALIGN (mandatory) | app-release.apk | zipalign -c -P 16 -v 4: Verification successful | deterministic | high | - | [developer.android.com/guide/practices/page-sizes](https://developer.android.com/guide/practices/page-sizes) (2026-10-16) |
| GPRG-038 | PASS | TECH-AGP-16KB (recommended) | android/app/build.gradle | ndkVersion=28.2.13676358 (r28+ compila 16 KB por padrão; libs pré-compiladas de terceiros não são afetadas) | heuristic | medium | - | [developer.android.com/guide/practices/page-sizes](https://developer.android.com/guide/practices/page-sizes) (2026-10-16) |
| GPRG-039 | PASS | TECH-AGP-16KB (recommended) | android/settings.gradle | com.android.application 8.12.2 | heuristic | high | - | [developer.android.com/guide/practices/page-sizes](https://developer.android.com/guide/practices/page-sizes) (2026-10-16) |
| GPRG-040 | PASS | TECH-DEBUG-SIGNATURE (mandatory) | app-release.aab | CN=Mently SHA-256=CB:EC:AF:8B:7C:99:50:AE:30:69:7B:48:7F:86:C9:C2:14:61:C8:D5:2A:0F:96:8D:CD:AE:CE:61:9D:49:CC:15 | deterministic | high | - | [developer.android.com/studio/publish/app-signing](https://developer.android.com/studio/publish/app-signing) (2026-10-16) |
| GPRG-041 | PASS | TECH-DEBUG-SIGNATURE (mandatory) | app-release.apk | CN=Mently SHA-256=CB:EC:AF:8B:7C:99:50:AE:30:69:7B:48:7F:86:C9:C2:14:61:C8:D5:2A:0F:96:8D:CD:AE:CE:61:9D:49:CC:15 | deterministic | high | - | [developer.android.com/studio/publish/app-signing](https://developer.android.com/studio/publish/app-signing) (2026-10-16) |
| GPRG-042 | PASS | TECH-SIGNATURE-VERIFY (mandatory) | app-release.aab | jarsigner -verify: jar verified. (avisos esperados em bundles Gradle: certificado de upload autoassinado, META-INF fora do início do ZIP) | deterministic | high | - | [developer.android.com/tools/apksigner](https://developer.android.com/tools/apksigner) (2026-10-16) |
| GPRG-043 | PASS | TECH-SIGNATURE-VERIFY (mandatory) | app-release.apk | Verified using v1 scheme (JAR signing): false Verified using v2 scheme (APK Signature Scheme v2): true Verified using v3 scheme (APK Signature Scheme v3): false Verified using v3.1 scheme (APK Signature Scheme v3.1): false Verified using v3.2 scheme (APK Signature Scheme v3.2): false Verified using  | deterministic | high | - | [developer.android.com/tools/apksigner](https://developer.android.com/tools/apksigner) (2026-10-16) |
| GPRG-044 | PASS | TECH-UPLOAD-KEY (mandatory) | app-release.aab | RSA 2048 bits; SHA-256=CB:EC:AF:8B:7C:99:50:AE:30:69:7B:48:7F:86:C9:C2:14:61:C8:D5:2A:0F:96:8D:CD:AE:CE:61:9D:49:CC:15 | deterministic | high | - | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/9842756?hl=en) (2026-10-16) |
| GPRG-045 | PASS | SEC-CLEARTEXT (recommended) | app-release.aab:AndroidManifest.xml | usesCleartextTraffic=False targetSdk=36 (padrão false desde API 28) | deterministic | high | - | [developer.android.com/privacy-and-security/secur](https://developer.android.com/privacy-and-security/security-config) (2026-10-16) |
| GPRG-046 | PASS | SEC-CLEARTEXT (recommended) | app-release.apk:AndroidManifest.xml | usesCleartextTraffic=False targetSdk=36 (padrão false desde API 28) | deterministic | high | - | [developer.android.com/privacy-and-security/secur](https://developer.android.com/privacy-and-security/security-config) (2026-10-16) |
| GPRG-047 | PASS | TECH-BILLING-LIBRARY-VERSION (mandatory) | monetização | billing_client=8.0.0; mínimo 8 desde 2026-08-31 | deterministic | high | - | [developer.android.com/google/play/billing/deprec](https://developer.android.com/google/play/billing/deprecation-faq) (2026-10-16) |
| GPRG-048 | NOT_VERIFIED | TECH-16KB-ZIP-ALIGN (mandatory) | app-release.aab | o alinhamento por offset vale para APKs; o .aab é convertido pelo Google Play | manual | high | Gere APKs com `bundletool build-apks --bundle=app.aab --output=app.apks` e rode `zipalign -c -P 16 -v 4` nos splits; confira o aviso de 16 KB no App bundle explorer. | [developer.android.com/guide/practices/page-sizes](https://developer.android.com/guide/practices/page-sizes) (2026-10-16) |
| GPRG-049 | NOT_VERIFIED | TECH-PLAY-APP-SIGNING (mandatory) | app-release.aab | A assinatura do .aab é a da chave de upload; os APKs distribuídos são re-assinados pelo Google Play. | manual | high | Confira Play Console > Test and release > App integrity > Play app signing; registre o SHA-256 da app signing key em APIs/App Links. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/9842756?hl=en) (2026-10-16) |
| GPRG-050 | NOT_VERIFIED | SEC-APP-LINKS (recommended) | app-release.aab:AndroidManifest.xml | hosts: app.mently.com.br | manual | high | Verifique https://<host>/.well-known/assetlinks.json com o SHA-256 da chave de assinatura do Play (App signing key). | [developer.android.com/training/app-links/verify-](https://developer.android.com/training/app-links/verify-applinks) (2026-10-16) |
| GPRG-051 | NOT_VERIFIED | SEC-APP-LINKS (recommended) | app-release.apk:AndroidManifest.xml | hosts: app.mently.com.br | manual | high | Verifique https://<host>/.well-known/assetlinks.json com o SHA-256 da chave de assinatura do Play (App signing key). | [developer.android.com/training/app-links/verify-](https://developer.android.com/training/app-links/verify-applinks) (2026-10-16) |
| GPRG-052 | NOT_VERIFIED | SEC-NETWORK-SECURITY-CONFIG (recommended) | app-release.aab:AndroidManifest.xml | networkSecurityConfig=@xml/network_security_config | manual | high | Revise res/xml do network security config (cleartextTrafficPermitted, trust-anchors de usuário, debug-overrides). | [developer.android.com/privacy-and-security/secur](https://developer.android.com/privacy-and-security/security-config) (2026-10-16) |
| GPRG-053 | NOT_VERIFIED | SEC-NETWORK-SECURITY-CONFIG (recommended) | app-release.apk:AndroidManifest.xml | networkSecurityConfig=@0x7f130005 | manual | high | Revise res/xml do network security config (cleartextTrafficPermitted, trust-anchors de usuário, debug-overrides). | [developer.android.com/privacy-and-security/secur](https://developer.android.com/privacy-and-security/security-config) (2026-10-16) |
| GPRG-054 | NOT_VERIFIED | TECH-MERGED-MANIFEST (recommended) | android/app/src/main/AndroidManifest.xml | auditoria de fonte; o manifest final só existe no artefato | manual | high | Rode também `guardian.py audit --aab build/app/outputs/bundle/release/app-release.aab` (ou gate --aab) para auditar o manifest mesclado. | [developer.android.com/build/manage-manifests](https://developer.android.com/build/manage-manifests) (2026-10-16) |
| GPRG-055 | NOT_VERIFIED | POL-SDK-INDEX (recommended) | dependências | 67 dependências categorizadas; 173 bibliotecas Maven no bundle | manual | high | Consulte https://play.google.com/sdks para cada SDK de terceiros (avisos de política/versões desatualizadas). | [play.google.com/sdks](https://play.google.com/sdks) (2026-10-16) |
| GPRG-056 | NOT_VERIFIED | PRIV-DATA-SAFETY-FORM (mandatory) | Play Console > App content > Data safety | sem declarações fornecidas (--declarations) | manual | high | Exporte/compare o formulário Data safety com o inventário de SDKs deste relatório. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/10787469?hl=en) (2026-10-16) |
| GPRG-057 | NOT_VERIFIED | PRIV-PRIVACY-POLICY (mandatory) | Play Console > Store listing / App content | não informada | manual | high | Garanta link válido no store listing e dentro do app, cobrindo coleta, uso, compartilhamento, retenção e exclusão. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/10144311?hl=en) (2026-10-16) |
| GPRG-058 | NOT_VERIFIED | POL-PLAY-BILLING (mandatory) | monetização | Play Billing presente: in_app_purchase, in_app_purchase_android, com.android.billingclient:billing:8.0.0, permissão BILLING | manual | high | Confirme que bens/serviços digitais são vendidos pelo Play Billing, com preço, renovação automática, testes gratuitos e cancelamento claros. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/9858738?hl=en) (2026-10-16) |
| GPRG-059 | NOT_VERIFIED | POL-ADS-DECLARATION (mandatory) | Play Console | não disponível offline/sem declarações | manual | high | Verificar manualmente no Play Console. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/9857753?hl=en) (2026-10-16) |
| GPRG-060 | NOT_VERIFIED | POL-APP-ACCESS (mandatory) | Play Console | não disponível offline/sem declarações | manual | high | Verificar manualmente no Play Console. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/15748846?hl=en) (2026-10-16) |
| GPRG-061 | NOT_VERIFIED | POL-CONTENT-RATING (mandatory) | Play Console | não disponível offline/sem declarações | manual | high | Verificar manualmente no Play Console. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/9898843?hl=en) (2026-10-16) |
| GPRG-062 | NOT_VERIFIED | POL-PERMISSIONS-DECLARATION (mandatory) | Play Console | não disponível offline/sem declarações | manual | high | Verificar manualmente no Play Console. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/9214102?hl=en) (2026-10-16) |
| GPRG-063 | NOT_VERIFIED | POL-STORE-LISTING (mandatory) | Play Console | não disponível offline/sem declarações | manual | high | Verificar manualmente no Play Console. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/9898842?hl=en) (2026-10-16) |
| GPRG-064 | NOT_VERIFIED | POL-TARGET-AUDIENCE (mandatory) | Play Console | não disponível offline/sem declarações | manual | high | Verificar manualmente no Play Console. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/9893335?hl=en) (2026-10-16) |
| GPRG-065 | NOT_VERIFIED | PRIV-DATA-SAFETY-FORM (mandatory) | Play Console | não disponível offline/sem declarações | manual | high | Verificar manualmente no Play Console. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/10787469?hl=en) (2026-10-16) |
| GPRG-066 | NOT_VERIFIED | TECH-VERSION-CODE-INCREMENT (mandatory) | app-release.aab:AndroidManifest.xml | versionCode=46 | manual | high | Confira em Play Console > App bundle explorer que o versionCode é maior que o último enviado. | [developer.android.com/studio/publish/versioning](https://developer.android.com/studio/publish/versioning) (2026-10-16) |
| GPRG-067 | NOT_VERIFIED | TECH-VERSION-CODE-INCREMENT (mandatory) | app-release.apk:AndroidManifest.xml | versionCode=47 | manual | high | Confira em Play Console > App bundle explorer que o versionCode é maior que o último enviado. | [developer.android.com/studio/publish/versioning](https://developer.android.com/studio/publish/versioning) (2026-10-16) |
| GPRG-068 | NOT_VERIFIED | POL-CLOSED-TESTING (mandatory) | Play Console > Dashboard | informe --account-type personal\|organization e --account-created YYYY-MM-DD | manual | high | Se conta pessoal criada após 2023-11-13: teste fechado com ≥ 12 testadores por ≥ 14 dias consecutivos e solicite acesso à produção. | [support.google.com/googleplay/android-developer/](https://support.google.com/googleplay/android-developer/answer/14151465?hl=en) (2026-10-16) |
| GPRG-069 | NOT_VERIFIED | TECH-FUNCTIONAL-TESTS (recommended) | - | adb disponível; instalação em dispositivo exige autorização explícita | manual | high | Execute `flutter test integration_test` / Espresso / UI Automator e o pre-launch report do Play Console; nunca classificado como aprovado sem execução. | [developer.android.com/studio/test](https://developer.android.com/studio/test) (2026-10-16) |

## Permissões

| Permissão | Regra | Origem provável | Declarada em |
|---|---|---|---|
| android.permission.INTERNET | - | não determinado (verifique o manifest-merger-report) | manifest mesclado do artefato |
| android.permission.ACCESS_NETWORK_STATE | - | não determinado (verifique o manifest-merger-report) | manifest mesclado do artefato |
| android.permission.USE_BIOMETRIC | - | local_auth / androidx.biometric | manifest mesclado do artefato |
| android.permission.WAKE_LOCK | - | não determinado (verifique o manifest-merger-report) | manifest mesclado do artefato |
| android.permission.FOREGROUND_SERVICE | - | não determinado (verifique o manifest-merger-report) | manifest mesclado do artefato |
| android.permission.FOREGROUND_SERVICE_MEDIA_PLAYBACK | - | audio_service / just_audio | manifest mesclado do artefato |
| android.permission.USE_FINGERPRINT | - | local_auth (API legada) | manifest mesclado do artefato |
| br.com.mently.app.mently.permission.C2D_MESSAGE | - | não determinado (verifique o manifest-merger-report) | manifest mesclado do artefato |
| android.permission.POST_NOTIFICATIONS | POL-SENSITIVE-PERMISSIONS | push/notificações (ex.: OneSignal, firebase_messaging, flutter_local_notifications) | manifest mesclado do artefato |
| com.google.android.c2dm.permission.RECEIVE | - | Firebase Cloud Messaging / push SDK (ex.: OneSignal) | manifest mesclado do artefato |
| android.permission.VIBRATE | - | não determinado (verifique o manifest-merger-report) | manifest mesclado do artefato |
| android.permission.RECEIVE_BOOT_COMPLETED | - | agendamento/push que reagenda após boot | manifest mesclado do artefato |
| com.sec.android.provider.badge.permission.READ | - | não determinado (verifique o manifest-merger-report) | manifest mesclado do artefato |
| com.sec.android.provider.badge.permission.WRITE | - | não determinado (verifique o manifest-merger-report) | manifest mesclado do artefato |
| com.htc.launcher.permission.READ_SETTINGS | - | não determinado (verifique o manifest-merger-report) | manifest mesclado do artefato |
| com.htc.launcher.permission.UPDATE_SHORTCUT | - | não determinado (verifique o manifest-merger-report) | manifest mesclado do artefato |
| com.sonyericsson.home.permission.BROADCAST_BADGE | - | não determinado (verifique o manifest-merger-report) | manifest mesclado do artefato |
| com.sonymobile.home.permission.PROVIDER_INSERT_BADGE | - | não determinado (verifique o manifest-merger-report) | manifest mesclado do artefato |
| com.anddoes.launcher.permission.UPDATE_COUNT | - | não determinado (verifique o manifest-merger-report) | manifest mesclado do artefato |
| com.majeur.launcher.permission.UPDATE_BADGE | - | não determinado (verifique o manifest-merger-report) | manifest mesclado do artefato |
| com.huawei.android.launcher.permission.CHANGE_BADGE | - | não determinado (verifique o manifest-merger-report) | manifest mesclado do artefato |
| com.huawei.android.launcher.permission.READ_SETTINGS | - | não determinado (verifique o manifest-merger-report) | manifest mesclado do artefato |
| com.huawei.android.launcher.permission.WRITE_SETTINGS | - | não determinado (verifique o manifest-merger-report) | manifest mesclado do artefato |
| android.permission.READ_APP_BADGE | - | não determinado (verifique o manifest-merger-report) | manifest mesclado do artefato |
| com.oppo.launcher.permission.READ_SETTINGS | - | não determinado (verifique o manifest-merger-report) | manifest mesclado do artefato |
| com.oppo.launcher.permission.WRITE_SETTINGS | - | não determinado (verifique o manifest-merger-report) | manifest mesclado do artefato |
| me.everything.badger.permission.BADGE_COUNT_READ | - | não determinado (verifique o manifest-merger-report) | manifest mesclado do artefato |
| me.everything.badger.permission.BADGE_COUNT_WRITE | - | não determinado (verifique o manifest-merger-report) | manifest mesclado do artefato |
| com.android.vending.BILLING | - | Play Billing Library (in_app_purchase) | manifest mesclado do artefato |
| br.com.mently.app.mently.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION | - | não determinado (verifique o manifest-merger-report) | manifest mesclado do artefato |

## Bibliotecas nativas

| Biblioteca | ABI | ELF 16 KB | Detalhe | Offset ZIP 16 KB |
|---|---|---|---|---|
| base/lib/arm64-v8a/libapp.so | arm64-v8a | True | PT_LOAD align 0x10000 [python-elf + llvm-readelf] | n/a |
| base/lib/arm64-v8a/libflutter.so | arm64-v8a | True | PT_LOAD align 0x10000 [python-elf + llvm-readelf] | n/a |
| base/lib/arm64-v8a/libsentry-android.so | arm64-v8a | True | PT_LOAD align 0x4000 [python-elf + llvm-readelf] | n/a |
| base/lib/arm64-v8a/libsentry.so | arm64-v8a | True | PT_LOAD align 0x4000 [python-elf + llvm-readelf] | n/a |
| base/lib/armeabi-v7a/libapp.so | armeabi-v7a | True | PT_LOAD align 0x4000 [python-elf + llvm-readelf] | n/a |
| base/lib/armeabi-v7a/libflutter.so | armeabi-v7a | True | PT_LOAD align 0x10000 [python-elf + llvm-readelf] | n/a |
| base/lib/armeabi-v7a/libsentry-android.so | armeabi-v7a | True | PT_LOAD align 0x4000 [python-elf + llvm-readelf] | n/a |
| base/lib/armeabi-v7a/libsentry.so | armeabi-v7a | True | PT_LOAD align 0x4000 [python-elf + llvm-readelf] | n/a |
| base/lib/x86_64/libapp.so | x86_64 | True | PT_LOAD align 0x10000 [python-elf + llvm-readelf] | n/a |
| base/lib/x86_64/libflutter.so | x86_64 | True | PT_LOAD align 0x10000 [python-elf + llvm-readelf] | n/a |
| base/lib/x86_64/libsentry-android.so | x86_64 | True | PT_LOAD align 0x4000 [python-elf + llvm-readelf] | n/a |
| base/lib/x86_64/libsentry.so | x86_64 | True | PT_LOAD align 0x4000 [python-elf + llvm-readelf] | n/a |
| lib/arm64-v8a/libapp.so | arm64-v8a | True | PT_LOAD align 0x10000 [python-elf + llvm-readelf] | True |
| lib/arm64-v8a/libflutter.so | arm64-v8a | True | PT_LOAD align 0x10000 [python-elf + llvm-readelf] | True |
| lib/arm64-v8a/libsentry-android.so | arm64-v8a | True | PT_LOAD align 0x4000 [python-elf + llvm-readelf] | True |
| lib/arm64-v8a/libsentry.so | arm64-v8a | True | PT_LOAD align 0x4000 [python-elf + llvm-readelf] | True |
| lib/armeabi-v7a/libapp.so | armeabi-v7a | True | PT_LOAD align 0x4000 [python-elf + llvm-readelf] | True |
| lib/armeabi-v7a/libflutter.so | armeabi-v7a | True | PT_LOAD align 0x10000 [python-elf + llvm-readelf] | True |
| lib/armeabi-v7a/libsentry-android.so | armeabi-v7a | True | PT_LOAD align 0x4000 [python-elf + llvm-readelf] | True |
| lib/armeabi-v7a/libsentry.so | armeabi-v7a | True | PT_LOAD align 0x4000 [python-elf + llvm-readelf] | True |
| lib/x86_64/libapp.so | x86_64 | True | PT_LOAD align 0x10000 [python-elf + llvm-readelf] | True |
| lib/x86_64/libflutter.so | x86_64 | True | PT_LOAD align 0x10000 [python-elf + llvm-readelf] | True |
| lib/x86_64/libsentry-android.so | x86_64 | True | PT_LOAD align 0x4000 [python-elf + llvm-readelf] | True |
| lib/x86_64/libsentry.so | x86_64 | True | PT_LOAD align 0x4000 [python-elf + llvm-readelf] | True |

## SDKs relevantes para privacidade/cobrança (indícios, não prova de coleta)

| SDK | Versão | Categoria | Tipos a revisar |
|---|---|---|---|
| flutter | 0.0.0 | - | - |
| flutter_localizations | 0.0.0 | - | - |
| intl | 0.19.0 | - | - |
| get_it | 8.3.0 | - | - |
| go_router | 14.8.1 | - | - |
| dio | 5.9.2 | - | - |
| flutter_svg | 2.2.4 | - | - |
| riverpod | 2.6.1 | - | - |
| riverpod_annotation | 2.6.1 | - | - |
| hooks_riverpod | 2.6.1 | - | - |
| flutter_riverpod | 2.6.1 | - | - |
| flutter_hooks | 0.20.5 | - | - |
| freezed | 2.5.8 | - | - |
| freezed_annotation | 2.4.4 | - | - |
| flutter_secure_storage | 9.2.4 | - | - |
| flutter_native_splash | 2.4.7 | - | - |
| toastification | 3.0.3 | - | - |
| email_validator | 3.0.0 | - | - |
| cached_network_image | 3.4.1 | - | - |
| visibility_detector | 0.4.0+2 | - | - |
| carousel_slider | 5.1.2 | - | - |
| pod_player | 0.2.2 | - | - |
| flutter_switch | 0.3.2 | - | - |
| expandable_text | 2.3.0 | - | - |
| table_calendar | 3.2.0 | - | - |
| dio_cache_interceptor | 4.0.5 | - | - |
| http_cache_hive_store | 5.0.2 | - | - |
| path_provider | 2.1.5 | - | - |
| shimmer | 3.0.0 | - | - |
| flutter_appauth | 9.0.1 | auth | User IDs |
| cupertino_icons | 1.0.8 | - | - |
| collection | 1.19.1 | - | - |
| fquery | 1.5.0-beta.1 | - | - |
| sentry_flutter | 8.14.2 | crash | Crash logs, Diagnostics |
| dart_pusher_channels | 1.2.3 | - | - |
| image_picker | 1.2.1 | media | Photos, Videos |
| mime | 2.0.0 | - | - |
| onesignal_flutter | 5.4.4 | push | Device or other IDs, App interactions |
| local_auth | 2.3.0 | biometric | - |
| local_auth_android | 1.0.56 | - | - |
| local_auth_darwin | 1.6.1 | - | - |
| open_file | 3.5.11 | - | - |
| flutter_widget_from_html_core | 0.16.1 | - | - |
| share_plus | 10.1.4 | - | - |
| url_launcher | 6.3.2 | - | - |
| image_cropper | 9.1.0 | - | - |
| json_annotation | 4.9.0 | - | - |
| local_auth_platform_interface | 1.1.0 | - | - |
| qr_flutter | 4.1.0 | - | - |
| in_app_purchase | 3.3.1 | billing_play | Purchase history |
| in_app_purchase_android | 0.5.0 | billing_play | Purchase history |
| in_app_purchase_storekit | 0.4.8+1 | - | - |
| audio_service | 0.18.18 | - | - |
| audio_session | 0.2.2 | - | - |
| just_audio | 0.10.5 | - | - |
| com.android.billingclient:billing:8.0.0 | 8.0.0 | billing_play | Purchase history |
| com.onesignal:OneSignal:5.7.2 | 5.7.2 | push | Device or other IDs, App interactions |
| com.onesignal:core:5.7.2 | 5.7.2 | push | Device or other IDs, App interactions |
| com.onesignal:otel:5.7.2 | 5.7.2 | push | Device or other IDs, App interactions |
| com.onesignal:notifications:5.7.2 | 5.7.2 | push | Device or other IDs, App interactions |
| com.onesignal:in-app-messages:5.7.2 | 5.7.2 | push | Device or other IDs, App interactions |
| com.onesignal:location:5.7.2 | 5.7.2 | push | Device or other IDs, App interactions |
| io.sentry:sentry-android:7.22.4 | 7.22.4 | crash | Crash logs, Diagnostics |
| io.sentry:sentry-android-core:7.22.4 | 7.22.4 | crash | Crash logs, Diagnostics |
| io.sentry:sentry:7.22.4 | 7.22.4 | crash | Crash logs, Diagnostics |
| io.sentry:sentry-android-ndk:7.22.4 | 7.22.4 | crash | Crash logs, Diagnostics |
| io.sentry:sentry-android-replay:7.22.4 | 7.22.4 | crash | Crash logs, Diagnostics |

## Regras

- Base: 67 regras; digest `18733447bfa8380e`; consulta mais recente 2026-10-16
- Status das fontes: fontes não reconsultadas (rode `guardian.py rules --check-sources` antes de auditorias críticas)

## Limitações

- Google Play Developer API não consultada (nenhuma credencial fornecida); metadados do Console exigem verificação manual.
- O alinhamento ZIP de 16 KB dos APKs que o Google Play gera a partir do .aab não é observável no bundle; o auditor verifica ELF e APKs locais.
- Políticas de conteúdo (UGC, IP, malware, abuso de dispositivo, Families) exigem revisão humana; o auditor não as avalia automaticamente.
- Coleta de dados não é inferida de dependências: inventário de SDKs é apenas indício para o Data safety.
- READY nunca significa aprovação garantida pelo Google Play.
