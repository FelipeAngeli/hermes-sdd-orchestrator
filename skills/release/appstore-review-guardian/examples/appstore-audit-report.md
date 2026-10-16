# APP STORE REVIEW GUARDIAN

```text
APP STORE REVIEW GUARDIAN
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Application: Mently
Bundle ID: br.com.mently.app.mently
Version: 1.5.3
Build: 47
Modo: gate

AUDITORIA TÉCNICA
Status: BLOCKED

CONFORMIDADE DE PRIVACIDADE
Status: WARNING

CONFORMIDADE APP REVIEW
Status: WARNING

APP STORE CONNECT / SUBMISSÃO
Status: WARNING

FINAL STATUS: BLOCKED

Findings:
[BLOCKER] Bundle assinado (_CodeSignature) (app)
[WARNING] Mecanismo de restauração de compras (project)
[WARNING] Conteúdo gerado por usuários (project)
[WARNING] Privacy manifest do app (project)
[WARNING] Privacy manifest do app (app)
[WARNING] Purpose strings genéricas (project)
[WARNING] Purpose strings genéricas (app)
[WARNING] Declaração de criptografia (ITSAppUsesNonExemptEncryption) (project)
[WARNING] Declaração de criptografia (ITSAppUsesNonExemptEncryption) (app)
[WARNING] aps-environment (project)
```

> Este relatório é uma auditoria preventiva automatizada. READY significa apenas que nenhuma falha bloqueante foi encontrada nas verificações executadas; não é garantia nem probabilidade de aprovação pela Apple. A decisão final é sempre do App Review.

## Resumo

- Gerado em: 2026-10-16T23:14:44Z — ferramenta appstore-review-guardian 0.1.0
- Projeto: `~/Documents/KelTech/mently/application/apps/app` · IPA/App: `/private/tmp/mently-unsigned.ipa [UNSIGNED — não serve para upload; assinatura/perfil não verificáveis]`
- Contagem: BLOCKER 1, CRITICAL 0, WARNING 9, INFO 3, PASS 149, NOT_VERIFIED 22, NOT_APPLICABLE 4
- Regras: 71 (consultadas em 2026-10-16, idade 0 dias)
- Ambiente: darwin, Python 3.12.13, rede=não, ferramentas: codesign=sim, security=sim, otool=sim, lipo=sim, plutil=sim
- Cache incremental: 0 reutilizado(s), 2 recalculado(s)
- Motivos do status: BLOCKER SIGN-001: Bundle assinado (_CodeSignature)

## Indicadores de prontidão

_Indicadores de prontidão = proporção de verificações executadas que passaram (conformity) e proporção de verificações que puderam ser executadas (coverage). Não são probabilidade de aprovação._

| Indicador | Conformidade | Cobertura | Executadas | Não verificadas | Bloqueantes |
|---|---|---|---|---|---|
| Conformidade técnica | 93% | 82% | 27 | 6 | 1 |
| Conformidade com diretrizes Apple | 95% | 98% | 129 | 2 | 0 |
| Preparação da submissão | 0% | 12% | 2 | 14 | 0 |

## Bloqueios (BLOCKER)

### F001 [BLOCKER] Bundle assinado (_CodeSignature)

- Regra: `SIGN-001` · nível: mandatory · método: deterministic · confiança: 1.0
- Componente: `app` · arquivo: `Runner.app/_CodeSignature/CodeResources`
- Evidência: _CodeSignature/CodeResources ausente; executável sem LC_CODE_SIGNATURE
- Explicação: Para distribuição pela App Store o .app precisa estar assinado; ausência de _CodeSignature/CodeResources comprova build não assinado.
- Recomendação: Gere o build assinado para distribuição: 'flutter build ipa --release --export-options-plist=ios/ExportOptions.plist'. Build '--no-codesign' só serve para pré-auditoria.
- Referência oficial: https://developer.apple.com/documentation/xcode/distributing-your-app-for-beta-testing-and-releases (consultada em 2026-10-16)

## Alertas (WARNING)

### F002 [WARNING] Mecanismo de restauração de compras

- Regra: `REV-IAP-002` · guideline 3.1.1 · nível: recommended · método: heuristic · confiança: 0.55
- Componente: `project`
- Evidência: IAP: deps=['in_app_purchase', 'in_app_purchase_storekit'] storekit_config=True; restauração não encontrada
- Explicação: 3.1.1: 'you should make sure you have a restore mechanism for any restorable in-app purchases'.
- Recomendação: Adicione botão 'Restaurar compras' chamando InAppPurchase.instance.restorePurchases().
- Referência oficial: https://developer.apple.com/app-store/review/guidelines/#in-app-purchase (consultada em 2026-10-16)

### F003 [WARNING] Conteúdo gerado por usuários

- Regra: `REV-UGC-001` · guideline 1.2 · nível: mandatory · método: heuristic · confiança: 0.4
- Componente: `project`
- Evidência: sinais de UGC ['domain/messages/messages_service.dart:42: Future<Message> sendMessage({', 'ui/features/profile/messages/conversation/conversation_page.dart:59: (message) => messagesService.sendMessage(']; moderação/denúncia não encontrada
- Explicação: 1.2: apps com UGC precisam de filtro de conteúdo, denúncia com resposta em tempo hábil, bloqueio de usuários abusivos e contato publicado.
- Recomendação: Se usuários publicam conteúdo, adicione filtro, denúncia, bloqueio de usuários e contato (1.2).
- Referência oficial: https://developer.apple.com/app-store/review/guidelines/#user-generated-content (consultada em 2026-10-16)

### F004 [WARNING] Privacy manifest do app

- Regra: `PRIV-MANIFEST-001` · guideline 5.1.1 · nível: mandatory · método: deterministic · confiança: 0.6
- Componente: `project` · arquivo: `ios/Runner/PrivacyInfo.xcprivacy`
- Evidência: ios/Runner/PrivacyInfo.xcprivacy ausente (manifest do app)
- Explicação: O app registra em PrivacyInfo.xcprivacy os tipos de dados coletados e as Required Reason APIs usadas. Ausência no bundle do app é risco quando o próprio código usa essas APIs.
- Recomendação: Crie via Xcode: File > New > File > App Privacy, target Runner; declare NSPrivacyCollectedDataTypes e as Required Reason APIs do seu código (ex.: UserDefaults CA92.1 via shared_preferences).
- Referência oficial: https://developer.apple.com/documentation/bundleresources/privacy-manifest-files (consultada em 2026-10-16)

### F005 [WARNING] Privacy manifest do app

- Regra: `PRIV-MANIFEST-001` · guideline 5.1.1 · nível: mandatory · método: deterministic · confiança: 0.7
- Componente: `app` · arquivo: `Runner.app/PrivacyInfo.xcprivacy`
- Evidência: sem PrivacyInfo.xcprivacy na raiz do app; o executável principal referencia ['NSPrivacyAccessedAPICategoryFileTimestamp', 'NSPrivacyAccessedAPICategorySystemBootTime']
- Explicação: O app registra em PrivacyInfo.xcprivacy os tipos de dados coletados e as Required Reason APIs usadas. Ausência no bundle do app é risco quando o próprio código usa essas APIs.
- Recomendação: Adicione ios/Runner/PrivacyInfo.xcprivacy ao target Runner (File > New > App Privacy) declarando as categorias usadas.
- Referência oficial: https://developer.apple.com/documentation/bundleresources/privacy-manifest-files (consultada em 2026-10-16)

### F006 [WARNING] Purpose strings genéricas

- Regra: `PRIV-USAGE-002` · guideline 5.1.1(ii) · nível: mandatory · método: heuristic · confiança: 0.7
- Componente: `project` · arquivo: `ios/Runner/Info.plist`
- Evidência: NSPhotoLibraryUsageDescription='Allow this app to access your photos' parece genérico
- Explicação: Textos genéricos (ex.: 'Allow this app to access your photos') costumam ser rejeitados por não explicar o uso dos dados (5.1.1(ii)).
- Recomendação: Explique o uso concreto, ex.: 'O Mently usa suas fotos para definir a foto de perfil.' em NSPhotoLibraryUsageDescription (e localize em InfoPlist.strings).
- Referência oficial: https://developer.apple.com/app-store/review/guidelines/#data-collection-and-storage (consultada em 2026-10-16)

### F007 [WARNING] Purpose strings genéricas

- Regra: `PRIV-USAGE-002` · guideline 5.1.1(ii) · nível: mandatory · método: heuristic · confiança: 0.7
- Componente: `app` · arquivo: `Runner.app/Info.plist`
- Evidência: NSPhotoLibraryUsageDescription='Allow this app to access your photos' parece genérico
- Explicação: Textos genéricos (ex.: 'Allow this app to access your photos') costumam ser rejeitados por não explicar o uso dos dados (5.1.1(ii)).
- Recomendação: Explique o uso concreto, ex.: 'O Mently usa suas fotos para definir a foto de perfil.' em NSPhotoLibraryUsageDescription (e localize em InfoPlist.strings).
- Referência oficial: https://developer.apple.com/app-store/review/guidelines/#data-collection-and-storage (consultada em 2026-10-16)

### F008 [WARNING] Declaração de criptografia (ITSAppUsesNonExemptEncryption)

- Regra: `PLIST-ENC-001` · nível: recommended · método: deterministic · confiança: 1.0
- Componente: `project` · arquivo: `ios/Runner/Info.plist`
- Evidência: ITSAppUsesNonExemptEncryption ausente
- Explicação: Sem a chave, o App Store Connect exige o questionário de export compliance a cada upload. Incluir a chave agiliza a submissão; o valor correto depende do uso real de criptografia.
- Recomendação: Se o app só usa criptografia isenta (ex.: HTTPS do sistema), adicione <key>ITSAppUsesNonExemptEncryption</key><false/>; caso contrário responda o questionário no ASC.
- Referência oficial: https://developer.apple.com/documentation/bundleresources/information-property-list/itsappusesnonexemptencryption (consultada em 2026-10-16)

### F009 [WARNING] Declaração de criptografia (ITSAppUsesNonExemptEncryption)

- Regra: `PLIST-ENC-001` · nível: recommended · método: deterministic · confiança: 1.0
- Componente: `app` · arquivo: `Runner.app/Info.plist`
- Evidência: ITSAppUsesNonExemptEncryption ausente
- Explicação: Sem a chave, o App Store Connect exige o questionário de export compliance a cada upload. Incluir a chave agiliza a submissão; o valor correto depende do uso real de criptografia.
- Recomendação: Se o app só usa criptografia isenta (ex.: HTTPS do sistema), adicione <key>ITSAppUsesNonExemptEncryption</key><false/>; caso contrário responda o questionário no ASC.
- Referência oficial: https://developer.apple.com/documentation/bundleresources/information-property-list/itsappusesnonexemptencryption (consultada em 2026-10-16)

### F010 [WARNING] aps-environment

- Regra: `ENT-APS-001` · nível: mandatory · método: deterministic · confiança: 0.5
- Componente: `project` · arquivo: `ios/Runner/RunnerRelease.entitlements`
- Evidência: aps-environment=development no .entitlements de origem
- Explicação: O Xcode define aps-environment a partir do perfil: development em perfis de desenvolvimento, production em perfis de distribuição. 'development' no artefato final indica perfil errado.
- Recomendação: Normalmente o Xcode troca para 'production' ao exportar com perfil de distribuição; confirme no .ipa ('codesign -d --entitlements - Payload/Runner.app') ou crie RunnerRelease.entitlements com 'production'.
- Referência oficial: https://developer.apple.com/documentation/bundleresources/entitlements/aps-environment (consultada em 2026-10-16)

## Informativos (INFO)

### F011 [INFO] Inventário de URL schemes

- Regra: `PLIST-URL-001` · nível: recommended · método: deterministic · confiança: 1.0
- Componente: `project` · arquivo: `ios/Runner/Info.plist`
- Evidência: URL schemes: ['mently', 'br.com.mently.app.mently']
- Explicação: Lista os CFBundleURLSchemes declarados para revisão de deep links (sem requisito de reprovação associado).
- Recomendação: Confirme que cada scheme é tratado pelo app e não colide com schemes de terceiros.
- Referência oficial: https://developer.apple.com/documentation/bundleresources/information-property-list (consultada em 2026-10-16)

### F012 [INFO] Inventário de URL schemes

- Regra: `PLIST-URL-001` · nível: recommended · método: deterministic · confiança: 1.0
- Componente: `app` · arquivo: `Runner.app/Info.plist`
- Evidência: URL schemes: ['mently', 'br.com.mently.app.mently']
- Explicação: Lista os CFBundleURLSchemes declarados para revisão de deep links (sem requisito de reprovação associado).
- Recomendação: Confirme que cada scheme é tratado pelo app e não colide com schemes de terceiros.
- Referência oficial: https://developer.apple.com/documentation/bundleresources/information-property-list (consultada em 2026-10-16)

### F013 [INFO] Inventário Flutter/Dart

- Regra: `PROJ-FLUTTER-001` · nível: recommended · método: deterministic · confiança: 1.0
- Componente: `flutter` · arquivo: `pubspec.lock`
- Evidência: Flutter >=3.38.4, Dart >=3.10.3 <4.0.0, 20 plugins iOS, 25 pods
- Explicação: Versões de Flutter/Dart e dependências registradas para rastreabilidade (sem requisito de reprovação).
- Referência oficial: https://docs.flutter.dev/deployment/ios (consultada em 2026-10-16)

## Pendências (NOT_VERIFIED)

- [ ] `REV-SUB-001` Assinaturas auto-renováveis (**obrigatório**) — assinatura detectada ["domain/subscription/subscription_inject.dart:8: apiUrl: '/api/subscription',"]; duração/valor só verificáveis no ASC → Confirme período >= 7 dias, termos de renovação e link para gerenciar assinatura.
- [ ] `PRIV-SDK-001` SDK listado pela Apple sem privacy manifest (**obrigatório**) — Flutter.framework: manifest verificado apenas no bundle compilado
- [ ] `ASC-AGE-001` Age Rating atualizado (**obrigatório**) — verificação manual pendente no App Store Connect → Responda o novo questionário de Age Rating em App Information.
- [ ] `ASC-AVAIL-001` Disponibilidade, países e trader status (UE) (**obrigatório**) — verificação manual pendente no App Store Connect → Confirme países/territórios; para a UE, verifique o trader status (DSA).
- [ ] `ASC-EXPORT-001` Export compliance (**obrigatório**) — verificação manual pendente no App Store Connect → Responda as perguntas de criptografia ou declare ITSAppUsesNonExemptEncryption no Info.plist.
- [ ] `ASC-MEDIA-001` Screenshots e previews (**obrigatório**) — verificação manual pendente no App Store Connect → Revise screenshots/previews: devem mostrar o app em uso, não só splash/login (2.3.3).
- [ ] `ASC-META-001` Nome do app (**obrigatório**) — metadados do App Store Connect não fornecidos → Gere o modelo com 'guardian.py asc --template > asc.json', preencha e rode com --asc-metadata asc.json.
- [ ] `ASC-META-002` Subtítulo (**obrigatório**) — metadados do App Store Connect não fornecidos → Gere o modelo com 'guardian.py asc --template > asc.json', preencha e rode com --asc-metadata asc.json.
- [ ] `ASC-META-003` Keywords (**obrigatório**) — metadados do App Store Connect não fornecidos → Gere o modelo com 'guardian.py asc --template > asc.json', preencha e rode com --asc-metadata asc.json.
- [ ] `ASC-META-004` Descrição e texto promocional (**obrigatório**) — metadados do App Store Connect não fornecidos → Gere o modelo com 'guardian.py asc --template > asc.json', preencha e rode com --asc-metadata asc.json.
- [ ] `ASC-META-005` Privacy Policy URL (**obrigatório**) — metadados do App Store Connect não fornecidos → Gere o modelo com 'guardian.py asc --template > asc.json', preencha e rode com --asc-metadata asc.json.
- [ ] `ASC-META-006` Support URL (**obrigatório**) — metadados do App Store Connect não fornecidos → Gere o modelo com 'guardian.py asc --template > asc.json', preencha e rode com --asc-metadata asc.json.
- [ ] `ASC-PRIV-001` App Privacy Details (**obrigatório**) — verificação manual pendente no App Store Connect → Preencha App Privacy (dados do app e SDKs: OneSignal, Sentry etc.) em App Store Connect > App Privacy.
- [ ] `ASC-REVIEW-001` Informações para o App Review (**obrigatório**) — metadados do App Store Connect não fornecidos → Gere o modelo com 'guardian.py asc --template > asc.json', preencha e rode com --asc-metadata asc.json.
- [ ] `REV-DEMO-001` Conta de demonstração para o revisor (**obrigatório**) — app tem login (['core/auth/auth_service.dart:6: Future<User> login(String code, String codeVerifier);']); credenciais de demo só verificáveis no ASC → Preencha App Review Information > Sign-in required com usuário/senha de demonstração e mantenha o backend ativo.
- [ ] `REV-IAP-003` IAP visíveis e funcionais para o revisor (**obrigatório**) — IAPs precisam estar 'Ready to Submit' e visíveis ao revisor (ASC) → Anexe os IAPs/assinaturas à versão no ASC e explique nas notas como acessá-los.
- [ ] `ENT-ASSOC-001` Universal Links / Associated Domains (recomendado) — associated domains: ['applinks:app.mently.com.br']; AASA não consultado (use --network) → Confira https://<domínio>/.well-known/apple-app-site-association com o appID TEAMID.bundleid.
- [ ] `ENT-GTA-001` get-task-allow em build de distribuição (**obrigatório**) — bundle não assinado: verificação impossível
- [ ] `REV-PUSH-001` Push notifications configurado (**obrigatório**) — remote-notification declarado; entitlements não legíveis (bundle não assinado)
- [ ] `SIGN-002` Verificação criptográfica da assinatura (codesign) (**obrigatório**) — bundle não assinado: verificação impossível
- [ ] `SIGN-003` Certificado de distribuição (**obrigatório**) — bundle não assinado: verificação impossível
- [ ] `SIGN-004` Provisioning profile de App Store (**obrigatório**) — embedded.mobileprovision ausente (build não assinado)

## Verificações aprovadas / não aplicáveis

| Status | Regra | Componente | Evidência |
|---|---|---|---|
| PASS | `REV-ACCTDEL-001` | project | criação de conta ['ui/features/signup/providers/signup_page_provider.dart:65: final String userId = await authService.signUp(state.name, state.email,']; exclusã |
| PASS | `REV-IAP-001` | project | IAP via ['in_app_purchase', 'in_app_purchase_storekit']; nenhum SDK/URL de pagamento externo detectado |
| PASS | `REV-LOGIN-001` | project | login social: deps=[] código=['ui/features/signin/signin_page.dart:244: signInSocialMutation.mutate("google");', 'ui/features/signup/signup_page.dart:253: signu |
| PASS | `REV-LOGIN-002` | project | Sign in with Apple no código (['ui/features/signin/signin_page.dart:270: signInSocialMutation.mutate("apple");']); entitlement com.apple.developer.applesignin p |
| PASS | `REV-PLACEHOLDER-001` | project | nenhum placeholder óbvio em literais do código |
| PASS | `RULES-FRESH-001` | rules | regras consultadas em 2026-10-16 (0 dias); última checagem de fontes 2026-10-16T23:14:43Z |
| PASS | `PRIV-DATA-001` | AppAuthCore_Privacy.bundle | 0 tipo(s) de dados declarados |
| PASS | `PRIV-DATA-001` | Flutter.framework | 0 tipo(s) de dados declarados |
| PASS | `PRIV-DATA-001` | OneSignalCore.framework | 0 tipo(s) de dados declarados |
| PASS | `PRIV-DATA-001` | OneSignalExtension.framework | 2 tipo(s) de dados declarados |
| PASS | `PRIV-DATA-001` | OneSignalFramework.framework | 3 tipo(s) de dados declarados |
| PASS | `PRIV-DATA-001` | OneSignalInAppMessages.framework | 1 tipo(s) de dados declarados |
| PASS | `PRIV-DATA-001` | OneSignalLiveActivities.framework | 1 tipo(s) de dados declarados |
| PASS | `PRIV-DATA-001` | OneSignalLocation.framework | 3 tipo(s) de dados declarados |
| PASS | `PRIV-DATA-001` | OneSignalNotifications.framework | 1 tipo(s) de dados declarados |
| PASS | `PRIV-DATA-001` | OneSignalOSCore.framework | 0 tipo(s) de dados declarados |
| PASS | `PRIV-DATA-001` | OneSignalOutcomes.framework | 1 tipo(s) de dados declarados |
| PASS | `PRIV-DATA-001` | OneSignalUser.framework | 2 tipo(s) de dados declarados |
| PASS | `PRIV-DATA-001` | Sentry.bundle | 3 tipo(s) de dados declarados |
| PASS | `PRIV-DATA-001` | TOCropViewControllerBundle.bundle | 0 tipo(s) de dados declarados |
| PASS | `PRIV-DATA-001` | flutter_appauth_privacy.bundle | 0 tipo(s) de dados declarados |
| PASS | `PRIV-DATA-001` | flutter_native_splash_privacy.bundle | 0 tipo(s) de dados declarados |
| PASS | `PRIV-DATA-001` | flutter_secure_storage.bundle | 0 tipo(s) de dados declarados |
| PASS | `PRIV-DATA-001` | image_picker_ios_privacy.bundle | 0 tipo(s) de dados declarados |
| PASS | `PRIV-DATA-001` | in_app_purchase_storekit_privacy.bundle | 0 tipo(s) de dados declarados |
| PASS | `PRIV-DATA-001` | local_auth_darwin_privacy.bundle | 0 tipo(s) de dados declarados |
| PASS | `PRIV-DATA-001` | package_info_plus_privacy.bundle | 0 tipo(s) de dados declarados |
| PASS | `PRIV-DATA-001` | share_plus_privacy.bundle | 0 tipo(s) de dados declarados |
| PASS | `PRIV-DATA-001` | sqflite_darwin_privacy.bundle | 0 tipo(s) de dados declarados |
| PASS | `PRIV-DATA-001` | url_launcher_ios_privacy.bundle | 0 tipo(s) de dados declarados |
| PASS | `PRIV-DATA-001` | video_player_avfoundation_privacy.bundle | 0 tipo(s) de dados declarados |
| PASS | `PRIV-DATA-001` | wakelock_plus_privacy.bundle | 0 tipo(s) de dados declarados |
| PASS | `PRIV-MANIFEST-002` | AppAuthCore_Privacy.bundle | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-MANIFEST-002` | Flutter.framework | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-MANIFEST-002` | OneSignalCore.framework | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-MANIFEST-002` | OneSignalExtension.framework | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-MANIFEST-002` | OneSignalFramework.framework | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-MANIFEST-002` | OneSignalInAppMessages.framework | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-MANIFEST-002` | OneSignalLiveActivities.framework | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-MANIFEST-002` | OneSignalLocation.framework | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-MANIFEST-002` | OneSignalNotifications.framework | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-MANIFEST-002` | OneSignalOSCore.framework | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-MANIFEST-002` | OneSignalOutcomes.framework | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-MANIFEST-002` | OneSignalUser.framework | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-MANIFEST-002` | Sentry.bundle | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-MANIFEST-002` | TOCropViewControllerBundle.bundle | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-MANIFEST-002` | flutter_appauth_privacy.bundle | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-MANIFEST-002` | flutter_native_splash_privacy.bundle | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-MANIFEST-002` | flutter_secure_storage.bundle | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-MANIFEST-002` | image_picker_ios_privacy.bundle | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-MANIFEST-002` | in_app_purchase_storekit_privacy.bundle | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-MANIFEST-002` | local_auth_darwin_privacy.bundle | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-MANIFEST-002` | package_info_plus_privacy.bundle | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-MANIFEST-002` | share_plus_privacy.bundle | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-MANIFEST-002` | sqflite_darwin_privacy.bundle | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-MANIFEST-002` | url_launcher_ios_privacy.bundle | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-MANIFEST-002` | video_player_avfoundation_privacy.bundle | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-MANIFEST-002` | wakelock_plus_privacy.bundle | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-POLICY-001` | project | referência à política de privacidade no código: ['env/env_config.dart:8: defaultValue: "https://mently.com.br/terms/privacy-policy",', "ui/features/signup/signu |
| PASS | `PRIV-RRAPI-001` | AppAuthCore_Privacy.bundle | categorias declaradas: [] |
| PASS | `PRIV-RRAPI-001` | Flutter.framework | categorias declaradas: ['NSPrivacyAccessedAPICategoryFileTimestamp', 'NSPrivacyAccessedAPICategorySystemBootTime'] |
| PASS | `PRIV-RRAPI-001` | OneSignalCore.framework | categorias declaradas: ['NSPrivacyAccessedAPICategoryUserDefaults'] |
| PASS | `PRIV-RRAPI-001` | OneSignalExtension.framework | categorias declaradas: ['NSPrivacyAccessedAPICategoryUserDefaults'] |
| PASS | `PRIV-RRAPI-001` | OneSignalFramework.framework | categorias declaradas: ['NSPrivacyAccessedAPICategoryUserDefaults'] |
| PASS | `PRIV-RRAPI-001` | OneSignalInAppMessages.framework | categorias declaradas: ['NSPrivacyAccessedAPICategoryUserDefaults'] |
| PASS | `PRIV-RRAPI-001` | OneSignalLiveActivities.framework | categorias declaradas: ['NSPrivacyAccessedAPICategoryUserDefaults'] |
| PASS | `PRIV-RRAPI-001` | OneSignalNotifications.framework | categorias declaradas: ['NSPrivacyAccessedAPICategoryUserDefaults'] |
| PASS | `PRIV-RRAPI-001` | OneSignalOSCore.framework | categorias declaradas: ['NSPrivacyAccessedAPICategoryUserDefaults'] |
| PASS | `PRIV-RRAPI-001` | OneSignalOutcomes.framework | categorias declaradas: ['NSPrivacyAccessedAPICategoryUserDefaults'] |
| PASS | `PRIV-RRAPI-001` | OneSignalUser.framework | categorias declaradas: ['NSPrivacyAccessedAPICategoryUserDefaults'] |
| PASS | `PRIV-RRAPI-001` | Sentry.bundle | categorias declaradas: ['NSPrivacyAccessedAPICategoryFileTimestamp', 'NSPrivacyAccessedAPICategorySystemBootTime', 'NSPrivacyAccessedAPICategoryUserDefaults'] |
| PASS | `PRIV-RRAPI-001` | TOCropViewControllerBundle.bundle | categorias declaradas: [] |
| PASS | `PRIV-RRAPI-001` | flutter_appauth_privacy.bundle | categorias declaradas: [] |
| PASS | `PRIV-RRAPI-001` | flutter_native_splash_privacy.bundle | categorias declaradas: [] |
| PASS | `PRIV-RRAPI-001` | flutter_secure_storage.bundle | categorias declaradas: [] |
| PASS | `PRIV-RRAPI-001` | image_picker_ios_privacy.bundle | categorias declaradas: [] |
| PASS | `PRIV-RRAPI-001` | in_app_purchase_storekit_privacy.bundle | categorias declaradas: [] |
| PASS | `PRIV-RRAPI-001` | local_auth_darwin_privacy.bundle | categorias declaradas: [] |
| PASS | `PRIV-RRAPI-001` | package_info_plus_privacy.bundle | categorias declaradas: [] |
| PASS | `PRIV-RRAPI-001` | share_plus_privacy.bundle | categorias declaradas: [] |
| PASS | `PRIV-RRAPI-001` | sqflite_darwin_privacy.bundle | categorias declaradas: [] |
| PASS | `PRIV-RRAPI-001` | url_launcher_ios_privacy.bundle | categorias declaradas: [] |
| PASS | `PRIV-RRAPI-001` | video_player_avfoundation_privacy.bundle | categorias declaradas: [] |
| PASS | `PRIV-RRAPI-001` | wakelock_plus_privacy.bundle | categorias declaradas: [] |
| PASS | `PRIV-RRAPI-002` | app | categorias referenciadas ['NSPrivacyAccessedAPICategoryFileTimestamp', 'NSPrivacyAccessedAPICategorySystemBootTime', 'NSPrivacyAccessedAPICategoryUserDefaults'] |
| PASS | `PRIV-SDK-001` | image_picker_ios | image_picker_ios 0.8.13+6: image_picker_ios/Sources/image_picker_ios/Resources/PrivacyInfo.xcprivacy no código-fonte do plugin |
| PASS | `PRIV-SDK-001` | package_info_plus | package_info_plus 9.0.0: package_info_plus/Sources/package_info_plus/PrivacyInfo.xcprivacy no código-fonte do plugin |
| PASS | `PRIV-SDK-001` | share_plus | share_plus 10.1.4: share_plus/Sources/share_plus/PrivacyInfo.xcprivacy no código-fonte do plugin |
| PASS | `PRIV-SDK-001` | url_launcher_ios | url_launcher_ios 6.4.1: url_launcher_ios/Sources/url_launcher_ios/Resources/PrivacyInfo.xcprivacy no código-fonte do plugin |
| PASS | `PRIV-SDK-001` | video_player_avfoundation | video_player_avfoundation 2.9.4: video_player_avfoundation/Sources/video_player_avfoundation/Resources/PrivacyInfo.xcprivacy no código-fonte do plugin |
| PASS | `PRIV-SDK-001` | AppAuth | pod AppAuth 2.0.0: Sources/AppAuthCore/Resources/PrivacyInfo.xcprivacy |
| PASS | `PRIV-SDK-001` | Flutter | Flutter (lista Apple) com PrivacyInfo.xcprivacy no bundle |
| PASS | `PRIV-SDK-001` | OneSignalCore | OneSignalCore (lista Apple) com PrivacyInfo.xcprivacy no bundle |
| PASS | `PRIV-SDK-001` | OneSignalExtension | OneSignalExtension (lista Apple) com PrivacyInfo.xcprivacy no bundle |
| PASS | `PRIV-SDK-001` | OneSignalOutcomes | OneSignalOutcomes (lista Apple) com PrivacyInfo.xcprivacy no bundle |
| PASS | `PRIV-TRACK-001` | AppAuthCore_Privacy.bundle | NSPrivacyTracking=False, domínios=0 |
| PASS | `PRIV-TRACK-001` | Flutter.framework | NSPrivacyTracking=False, domínios=0 |
| PASS | `PRIV-TRACK-001` | OneSignalCore.framework | NSPrivacyTracking=False, domínios=0 |
| PASS | `PRIV-TRACK-001` | OneSignalExtension.framework | NSPrivacyTracking=False, domínios=0 |
| PASS | `PRIV-TRACK-001` | OneSignalFramework.framework | NSPrivacyTracking=False, domínios=0 |
| PASS | `PRIV-TRACK-001` | OneSignalInAppMessages.framework | NSPrivacyTracking=False, domínios=0 |
| PASS | `PRIV-TRACK-001` | OneSignalLiveActivities.framework | NSPrivacyTracking=False, domínios=0 |
| PASS | `PRIV-TRACK-001` | OneSignalLocation.framework | NSPrivacyTracking=False, domínios=0 |
| PASS | `PRIV-TRACK-001` | OneSignalNotifications.framework | NSPrivacyTracking=False, domínios=0 |
| PASS | `PRIV-TRACK-001` | OneSignalOSCore.framework | NSPrivacyTracking=False, domínios=0 |
| PASS | `PRIV-TRACK-001` | OneSignalOutcomes.framework | NSPrivacyTracking=False, domínios=0 |
| PASS | `PRIV-TRACK-001` | OneSignalUser.framework | NSPrivacyTracking=False, domínios=0 |
| PASS | `PRIV-TRACK-001` | TOCropViewControllerBundle.bundle | NSPrivacyTracking=False, domínios=0 |
| PASS | `PRIV-TRACK-001` | flutter_appauth_privacy.bundle | NSPrivacyTracking=False, domínios=0 |
| PASS | `PRIV-TRACK-001` | flutter_native_splash_privacy.bundle | NSPrivacyTracking=False, domínios=0 |
| PASS | `PRIV-TRACK-001` | flutter_secure_storage.bundle | NSPrivacyTracking=False, domínios=0 |
| PASS | `PRIV-TRACK-001` | image_picker_ios_privacy.bundle | NSPrivacyTracking=False, domínios=0 |
| PASS | `PRIV-TRACK-001` | in_app_purchase_storekit_privacy.bundle | NSPrivacyTracking=False, domínios=0 |
| PASS | `PRIV-TRACK-001` | local_auth_darwin_privacy.bundle | NSPrivacyTracking=False, domínios=0 |
| PASS | `PRIV-TRACK-001` | package_info_plus_privacy.bundle | NSPrivacyTracking=False, domínios=0 |
| PASS | `PRIV-TRACK-001` | share_plus_privacy.bundle | NSPrivacyTracking=False, domínios=0 |
| PASS | `PRIV-TRACK-001` | sqflite_darwin_privacy.bundle | NSPrivacyTracking=False, domínios=0 |
| PASS | `PRIV-TRACK-001` | url_launcher_ios_privacy.bundle | NSPrivacyTracking=False, domínios=0 |
| PASS | `PRIV-TRACK-001` | video_player_avfoundation_privacy.bundle | NSPrivacyTracking=False, domínios=0 |
| PASS | `PRIV-TRACK-001` | wakelock_plus_privacy.bundle | NSPrivacyTracking=False, domínios=0 |
| PASS | `PRIV-USAGE-001` | project | NSFaceIDUsageDescription preenchido (56 caracteres) |
| PASS | `PRIV-USAGE-001` | project | NSLocationWhenInUseUsageDescription preenchido (123 caracteres) |
| PASS | `PRIV-USAGE-001` | app | NSFaceIDUsageDescription preenchido (56 caracteres) |
| PASS | `PRIV-USAGE-001` | app | NSLocationWhenInUseUsageDescription preenchido (123 caracteres) |
| PASS | `BIN-ARCH-001` | app | arquiteturas=['arm64'], plataforma=ios, minos=15.0.0, sdk=26.4.0 |
| PASS | `BIN-ARCH-002` | app | 13 framework(s) sem fatias de simulador |
| PASS | `BIN-DEBUG-001` | app | sem .dSYM ou segmento __DWARF no bundle |
| PASS | `BIN-FLUTTER-001` | app | App.framework sem kernel_blob.bin (AOT release) |
| PASS | `BUILD-SDK-001` | app | DTXcode=2641 (Xcode 26), DTSDKName=iphoneos26.4, DTPlatformVersion=26.4 |
| PASS | `IPA-STRUCT-001` | ipa | ZIP íntegro e seguro (339 entradas, CRC verificado) |
| PASS | `IPA-STRUCT-002` | ipa | Payload/Runner.app |
| PASS | `PLIST-ATS-001` | project | NSAllowsArbitraryLoads ausente ou NO |
| PASS | `PLIST-ATS-001` | app | NSAllowsArbitraryLoads ausente ou NO |
| PASS | `PLIST-BG-001` | project | UIBackgroundModes=['remote-notification'] |
| PASS | `PLIST-BG-001` | app | UIBackgroundModes=['remote-notification'] |
| PASS | `PLIST-ID-001` | project | CFBundleIdentifier=br.com.mently.app.mently |
| PASS | `PLIST-ID-001` | app | CFBundleIdentifier=br.com.mently.app.mently |
| PASS | `PLIST-MINOS-001` | app | MinimumOSVersion=15.0 (>= 13.0) |
| PASS | `PLIST-PARSE-001` | project | Info.plist de origem lido |
| PASS | `PLIST-PARSE-001` | app | Info.plist lido com plistlib |
| PASS | `PLIST-VER-001` | project | CFBundleShortVersionString=1.5.3 |
| PASS | `PLIST-VER-001` | app | CFBundleShortVersionString=1.5.3 |
| PASS | `PLIST-VER-002` | project | CFBundleVersion=47 |
| PASS | `PLIST-VER-002` | app | CFBundleVersion=47 |
| PASS | `PROJ-DEPLOY-001` | project | deployment targets {'project Release': '15.0', 'Podfile platform': '15.0'} >= 13.0 |
| PASS | `PROJ-ENT-001` | project | Release usa Runner/RunnerRelease.entitlements (3 chaves) |
| PASS | `PROJ-EXPORT-001` | project | ExportOptions method=app-store-connect |
| PASS | `REV-PUSH-001` | project | remote-notification + aps-environment=development |
| PASS | `REV-PUSH-001` | project | push via ['onesignal_flutter']; aps-environment='development' |
| NOT_APPLICABLE | `PRIV-ATT-001` | app | sem NSPrivacyTracking=true no manifest do app e sem AppTrackingTransparency vinculado |
| NOT_APPLICABLE | `PRIV-SDK-001` | url_launcher | url_launcher é fachada Dart sem código iOS; implementação embarcada: url_launcher_ios |
| NOT_APPLICABLE | `PRIV-SDK-001` | path_provider | path_provider é fachada Dart sem código iOS; implementação embarcada: path_provider_foundation |
| NOT_APPLICABLE | `PRIV-SDK-001` | sqflite | sqflite é fachada Dart sem código iOS; implementação embarcada: sqflite_darwin |

---
Este relatório é uma auditoria preventiva automatizada. READY significa apenas que nenhuma falha bloqueante foi encontrada nas verificações executadas; não é garantia nem probabilidade de aprovação pela Apple. A decisão final é sempre do App Review.
