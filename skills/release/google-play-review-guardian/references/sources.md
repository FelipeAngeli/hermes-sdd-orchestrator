# Registro de fontes oficiais

Todas as fontes abaixo foram consultadas em **2026-10-16** (HTTP 200, `urllib`/`curl` com User-Agent de navegador). Cada regra em `rules/*.json` aponta para uma destas URLs (`official_url`) e registra `consulted_at`. Para reconsultar e detectar mudanças: `python scripts/guardian.py rules --check-sources` (grava status, data e SHA-256 do texto em `sources-cache.json`, com TTL; funciona offline usando o último resultado).

Quando um fato não pôde ser confirmado na fonte oficial, ele **não** foi codificado como obrigatório (ver `limitations.md`).

## Google Play — Play Console Help / Policy Center

| URL | Consulta | O que foi extraído | Regras |
|---|---|---|---|
| https://support.google.com/googleplay/android-developer/answer/11926878?hl=en | 2026-10-16 | Texto literal: "Starting August 31, 2026: New apps and app updates must target Android 16 (API level 36) or higher to be submitted to Google Play; except for Wear OS, and Android Automotive OS apps, which must target Android 15 (API level 35) or higher, and Android TV and Android XR apps, which must target Android 14 (API level 34) or higher." Extensão: "You will be able to request an extension to November 1, 2026". Exceção: apps permanentemente privados de uma organização. Disponibilidade de apps existentes: 35 (mobile/Android Auto), 34 Wear, 33 TV, 34 XR, 32 (12L) Automotive até 2026-08-31. Tabelas por formato (Wear 35/34, TV 34, Automotive 35/34, XR 34). | TECH-TARGET-API-LEVEL, TECH-TARGET-API-AVAILABILITY |
| https://support.google.com/googleplay/android-developer/answer/14151465?hl=en | 2026-10-16 | "Developers with personal accounts created after November 13, 2023, must run a closed test for their app with a minimum of 12 testers who have been opted in continuously for at least 14 days." Depois: "Apply for production". Revisão costuma levar ≤ 7 dias. | POL-CLOSED-TESTING |
| https://support.google.com/googleplay/android-developer/answer/10787469?hl=en | 2026-10-16 | Data safety: todos os desenvolvedores declaram coleta/compartilhamento, incluindo SDKs de terceiros; Google pode agir em caso de discrepância. | PRIV-DATA-SAFETY-FORM, PRIV-SDK-DATA-DISCLOSURE, PRIV-DATA-ENCRYPTION-TRANSIT |
| https://support.google.com/googleplay/android-developer/answer/13327111?hl=en | 2026-10-16 | Exclusão de conta: caminho no app + link web; dados associados; exceções (apps permanentemente privados, gerenciamento empresarial, contas offline); apps não-mobile só precisam do link web. | PRIV-ACCOUNT-DELETION |
| https://support.google.com/googleplay/android-developer/answer/10144311?hl=en | 2026-10-16 | User Data: política de privacidade válida no store listing e no app; divulgação destacada e consentimento. | PRIV-PRIVACY-POLICY, PRIV-PROMINENT-DISCLOSURE |
| https://support.google.com/googleplay/android-developer/answer/6048248?hl=en | 2026-10-16 | Advertising ID: apps com target ≥ 33 que usam o ID devem declarar `com.google.android.gms.permission.AD_ID`; SDKs podem injetá-la no merge. | PRIV-ADVERTISING-ID |
| https://support.google.com/googleplay/android-developer/answer/10208820?hl=en | 2026-10-16 | SMS/Call Log: uso só para funcionalidade principal permitida + Permissions Declaration Form. Mudança anunciada (efetiva 2027-01-27): verificação de conta por chamada deixa de ser uso aceito de READ_CALL_LOG. | POL-SMS-CALL-LOG |
| https://support.google.com/googleplay/android-developer/answer/9799150?hl=en | 2026-10-16 | Localização em background: funcionalidade principal, formulário de declaração, vídeo, divulgação destacada, política de privacidade. | POL-BACKGROUND-LOCATION |
| https://support.google.com/googleplay/android-developer/answer/10467955?hl=en | 2026-10-16 | MANAGE_EXTERNAL_STORAGE: aplica-se a target ≥ 30; usos permitidos (gerenciadores de arquivos, backup, antivírus, documentos); declaração obrigatória. | POL-ALL-FILES-ACCESS |
| https://support.google.com/googleplay/android-developer/answer/10158779?hl=en | 2026-10-16 | QUERY_ALL_PACKAGES: efeito só com target ≥ 30; usos permitidos; declaração. | POL-QUERY-ALL-PACKAGES |
| https://support.google.com/googleplay/android-developer/answer/13161072?hl=en | 2026-10-16 | Permissions and APIs that Access Sensitive Information: READ_MEDIA_IMAGES/VIDEO só se o Photo Picker não bastar (target ≥ 33) + declaração; USE_EXACT_ALARM restrita a alarme/timer/calendário; REQUEST_INSTALL_PACKAGES; AccessibilityService; VpnService. Mudanças anunciadas (2027-01-27): botão de localização recomendado; nova política de Contatos (Contact Picker). | POL-PHOTO-VIDEO, POL-EXACT-ALARM, POL-REQUEST-INSTALL-PACKAGES, POL-VPN-SERVICE, POL-LOCATION, POL-SENSITIVE-PERMISSIONS |
| https://support.google.com/googleplay/android-developer/answer/13392821?hl=en | 2026-10-16 | Foreground services (Android 14+): declarar tipos no manifest, permissão por tipo, declaração no Play Console (descrição, impacto, vídeo). | POL-FOREGROUND-SERVICE |
| https://support.google.com/googleplay/android-developer/answer/10964491?hl=en | 2026-10-16 | AccessibilityService API: `isAccessibilityTool` só para ferramentas para pessoas com deficiência; declaração obrigatória (desde 2021-11-03 para target 12); proibição de ações autônomas exceto ferramentas verificadas. | POL-ACCESSIBILITY-API |
| https://support.google.com/googleplay/android-developer/answer/9858738?hl=en | 2026-10-16 | Payments: Play Billing obrigatório para bens/serviços digitais; NÃO usar para bens físicos, serviços físicos, contas/remessas, P2P, leilões, doações isentas, jogos de azar; programas de faturamento alternativo em regiões elegíveis (seções 3, 8, 9). | POL-PLAY-BILLING |
| https://support.google.com/googleplay/android-developer/answer/9842756?hl=en | 2026-10-16 | Play App Signing: chave de upload (sua) vs. chave de assinatura do app (Google); "Must be an RSA key of 2048 bits or more"; v4 automático. | TECH-UPLOAD-KEY, TECH-PLAY-APP-SIGNING |
| https://support.google.com/googleplay/android-developer/answer/9214102?hl=en | 2026-10-16 | Permissions Declaration Form: exibido no release; sem ele não é possível publicar mudanças. | POL-PERMISSIONS-DECLARATION |
| https://support.google.com/googleplay/android-developer/answer/9859152?hl=en | 2026-10-16 | Create and set up your app: limites de 30 / 80 / 4000 caracteres (título, descrição curta, completa). | POL-STORE-LISTING (limits) |
| https://support.google.com/googleplay/android-developer/answer/9898842?hl=en | 2026-10-16 | Metadata: sem emojis/caracteres repetidos/CAPS em título, ícone e nome do desenvolvedor. | POL-STORE-LISTING |
| https://support.google.com/googleplay/android-developer/answer/9898843?hl=en | 2026-10-16 | Content Ratings (título da página confirmado). | POL-CONTENT-RATING |
| https://support.google.com/googleplay/android-developer/answer/15748846?hl=en | 2026-10-16 | Requirements for providing sign in details for review (título confirmado). | POL-APP-ACCESS |
| https://support.google.com/googleplay/android-developer/answer/9893335?hl=en | 2026-10-16 | Families: declarar público-alvo no Console. | POL-TARGET-AUDIENCE |
| https://support.google.com/googleplay/android-developer/answer/9857753?hl=en | 2026-10-16 | Ads policy. | POL-ADS-DECLARATION |
| https://support.google.com/googleplay/android-developer/answer/9876937?hl=en | 2026-10-16 | User Generated Content. | POL-UGC |
| https://support.google.com/googleplay/android-developer/answer/9898783?hl=en | 2026-10-16 | Functionality, Content, and User Experience (funcionalidade mínima). | POL-MINIMUM-FUNCTIONALITY |
| https://support.google.com/googleplay/android-developer/answer/9888379?hl=en | 2026-10-16 | Device and Network Abuse (inclui seção de Foreground Services). | POL-DEVICE-NETWORK-ABUSE |
| https://support.google.com/googleplay/android-developer/answer/9888380?hl=en | 2026-10-16 | Malware. | POL-MALWARE |
| https://support.google.com/googleplay/android-developer/answer/9888072?hl=en | 2026-10-16 | Intellectual Property. | POL-INTELLECTUAL-PROPERTY |
| https://play.google.com/sdks | 2026-10-16 | Google Play SDK Index (página carregou; consulta é manual). | POL-SDK-INDEX |
| https://play.google.com/about/developer-content-policy/ | 2026-10-16 | Índice do Developer Program Policy (navegação para as políticas acima). | — |

## Android Developers

| URL | Consulta | O que foi extraído | Regras |
|---|---|---|---|
| https://developer.android.com/guide/practices/page-sizes | 2026-10-16 ("Last updated 2026-10-16 UTC") | "all apps targeting Android 15 (API level 35) and higher must support 16 KB memory page sizes on 64-bit devices on Google Play. Starting February 1, 2027, if your app updates don't support 16 KB memory page sizes, you won't be able to release these updates." Procedimento: `llvm-objdump -p lib.so \| grep LOAD` (align ≥ 2**14), `zipalign -c -P 16 -v 4 app.apk`, checagem RELRO `(VirtAddr + MemSiz) % 0x4000 == 0` (ou RELRO em LOAD próprio), AGP ≥ 8.5.1, NDK r28+. | TECH-16KB-PAGE-SIZE, TECH-16KB-ZIP-ALIGN, TECH-AGP-16KB |
| https://android-developers.googleblog.com/2025/05/prepare-play-apps-for-devices-with-16kb-page-size.html | 2026-10-16 | "Starting November 1st, 2025, all new apps and updates to existing apps submitted to Google Play and targeting Android 15+ devices must support 16 KB page sizes." (fonte do `effective_from` 2025-11-01) | TECH-16KB-PAGE-SIZE |
| https://developer.android.com/google/play/requirements/64-bit | 2026-10-16 | "Apps published on Google Play need to support 64-bit architectures." | TECH-64BIT |
| https://developer.android.com/guide/app-bundle | 2026-10-16 | "From August 2021, new apps are required to publish with the Android App Bundle on Google Play." | TECH-AAB-REQUIRED, TECH-ARTIFACT-INTEGRITY |
| https://developer.android.com/tools/bundletool | 2026-10-16 | `bundletool build-apks`, `validate`; requer Java. | TECH-BUNDLETOOL-VALIDATE |
| https://developer.android.com/tools/apksigner | 2026-10-16 | `apksigner verify --print-certs`. | TECH-SIGNATURE-VERIFY |
| https://developer.android.com/studio/publish/app-signing | 2026-10-16 | "most app stores (including the Google Play Store) do not accept apps signed with a debug certificate for publishing." | TECH-DEBUG-SIGNATURE, SEC-SIGNING-SECRETS-IN-VCS |
| https://developer.android.com/studio/publish/preparing | 2026-10-16 | Desativar debugging (`debuggable`/`isDebuggable`), escolher applicationId definitivo, R8. | TECH-DEBUGGABLE, TECH-TEST-ONLY, TECH-PACKAGE-NAME, TECH-R8-MINIFY |
| https://developer.android.com/studio/publish/versioning | 2026-10-16 | "The greatest value Google Play allows for versionCode is 2100000000." Cada release usa valor maior. | TECH-VERSION-CODE, TECH-VERSION-CODE-INCREMENT, TECH-ARTIFACT-FRESHNESS |
| https://developer.android.com/about/versions/12/behavior-changes-12 | 2026-10-16 | android:exported explícito para componentes com intent-filter (target ≥ 31), senão não instala no Android 12+. | TECH-EXPORTED-EXPLICIT |
| https://developer.android.com/google/play/billing/deprecation-faq | 2026-10-16 | "By Aug 31, 2026, all new apps and updates to existing apps must use Billing Library version 8 or later ... extension until Nov 1, 2026." Tabela: v7 → 2026-08-31, v8 → 2027-08-31 (prazos de descontinuação). | TECH-BILLING-LIBRARY-VERSION |
| https://developer.android.com/google/play/billing | 2026-10-16 | Visão geral do Play Billing. | POL-PLAY-BILLING |
| https://developer.android.com/google/play/integrity | 2026-10-16 | Play Integrity API (referência; não há checagem automática). | — |
| https://developer.android.com/develop/background-work/services/fgs/service-types | 2026-10-16 | Tipos de FGS e permissões `FOREGROUND_SERVICE_<TIPO>`. | POL-FOREGROUND-SERVICE |
| https://developer.android.com/build/manage-manifests | 2026-10-16 | Manifest merge. | TECH-MERGED-MANIFEST |
| https://developer.android.com/privacy-and-security/risks/zip-path-traversal | 2026-10-16 | Zip path traversal. | SEC-ARCHIVE-SAFETY |
| https://developer.android.com/privacy-and-security/risks/android-exported | 2026-10-16 | Componentes exportados. | SEC-EXPORTED-COMPONENTS |
| https://developer.android.com/privacy-and-security/security-config | 2026-10-16 | Network security config, cleartext. | SEC-CLEARTEXT, SEC-NETWORK-SECURITY-CONFIG |
| https://developer.android.com/privacy-and-security/risks/backup-best-practices | 2026-10-16 | Backup. | SEC-ALLOW-BACKUP |
| https://developer.android.com/training/app-links/verify-applinks | 2026-10-16 | assetlinks.json / autoVerify. | SEC-APP-LINKS |
| https://developer.android.com/privacy-and-security/risks/unsafe-use-of-deeplinks | 2026-10-16 | Deep links inseguros. | SEC-CUSTOM-SCHEME |
| https://developer.android.com/privacy-and-security/risks/hardcoded-cryptographic-secrets | 2026-10-16 | Segredos hardcoded. | SEC-HARDCODED-SECRETS |
| https://developer.android.com/privacy-and-security/risks/insecure-webview-native-bridges | 2026-10-16 | WebView bridges. | SEC-WEBVIEW-JS-BRIDGE |
| https://developer.android.com/studio/test | 2026-10-16 | Testes de app. | TECH-FUNCTIONAL-TESTS |

## Formatos binários (código-fonte oficial AOSP)

| URL | Consulta | O que foi extraído |
|---|---|---|
| https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/tools/aapt2/Resources.proto | 2026-10-16 | `XmlNode{element=1,text=2,source=3}`, `XmlElement{namespace_declaration=1,namespace_uri=2,name=3,attribute=4,child=5}`, `XmlAttribute{namespace_uri=1,name=2,value=3,source=4,resource_id=5,compiled_item=6}`, `Item{ref=1,str=2,raw_str=3,...,prim=7}`, `Primitive{int_decimal_value=6,int_hexadecimal_value=7,boolean_value=8}`. Confirmado contra o mirror GitHub `aosp-mirror/platform_frameworks_base`. |
| https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/core/res/res/values/public-final.xml | 2026-10-16 | IDs de atributos (`name` 0x01010003, `exported` 0x01010010, `debuggable` 0x0101000f, `targetSdkVersion` 0x01010270, …). |
| https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/core/res/res/values/attrs_manifest.xml | 2026-10-16 | Flags de `foregroundServiceType` (dataSync 0x01, mediaPlayback 0x02, …, specialUse 0x40000000). |

O formato `dependencies.pb` do bundle (`AppDependencies.library[].maven_library{groupId=1, artifactId=2, version=5}`) foi inferido do arquivo real do bundletool e conferido em um `.aab` real gerado pelo Gradle; é usado só para inventário (indício), nunca para findings bloqueantes.

## Google Play Developer API (androidpublisher v3)

| URL | Consulta | O que foi extraído |
|---|---|---|
| https://developers.google.com/android-publisher/api-ref/rest | 2026-10-16 | Recursos v3: `applications.tracks.releases`, `edits.*`, `monetization.subscriptions`, `monetization.onetimeproducts`, `purchases.*`, `reviews`, `applications.dataSafety` (POST, escrita). |
| https://developers.google.com/android-publisher/api-ref/rest/v3/applications.tracks.releases/list | 2026-10-16 | `GET .../v3/{parent=applications/*/tracks/*}/releases` |
| https://developers.google.com/android-publisher/api-ref/rest/v3/monetization.subscriptions/list | 2026-10-16 | `GET .../v3/applications/{packageName}/subscriptions` |
| https://developers.google.com/android-publisher/api-ref/rest/v3/monetization.onetimeproducts/list | 2026-10-16 | `GET .../v3/applications/{packageName}/oneTimeProducts` |
| https://developers.google.com/android-publisher/api-ref/rest/v3/edits/insert | 2026-10-16 | `POST .../edits` cria um edit — **não usado** (modo somente leitura). |
| https://developers.google.com/android-publisher/authorization | 2026-10-16 | Escopo `https://www.googleapis.com/auth/androidpublisher`. |

## Fonte não-oficial usada apenas como apoio

- Mensagem de erro do Play Console "You uploaded a debuggable APK or Android App Bundle. For security reasons you need to disable debugging before it can be published in Google Play." aparece em relatos públicos (Stack Overflow, consultado 2026-10-16). A regra TECH-DEBUGGABLE se apoia na instrução oficial de `studio/publish/preparing`; o texto da mensagem é citado apenas como nota.
