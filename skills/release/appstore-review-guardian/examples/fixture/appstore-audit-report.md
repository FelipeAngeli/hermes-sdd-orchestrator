# APP STORE REVIEW GUARDIAN

```text
APP STORE REVIEW GUARDIAN
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Application: Example App
Bundle ID: com.example.app
Version: 1.2.0
Build: 42
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
[BLOCKER] get-task-allow em build de distribuição (app)
[BLOCKER] Verificação criptográfica da assinatura (codesign) (app)
[BLOCKER] Provisioning profile de App Store (app)
[CRITICAL] aps-environment (app)
[WARNING] Exclusão de conta no app (project)
[WARNING] Login social e alternativa equivalente (project)
[WARNING] Privacy manifest do app (project)
[WARNING] SDK listado pela Apple sem privacy manifest (Alamofire)
[WARNING] Purpose strings genéricas (project)
[WARNING] Declaração de criptografia (ITSAppUsesNonExemptEncryption) (project)
[WARNING] Declaração de criptografia (ITSAppUsesNonExemptEncryption) (app)
[WARNING] aps-environment (project)
[WARNING] NSAllowsArbitraryLoads (app)
[WARNING] CFBundleIdentifier definido (app)
```

> Este relatório é uma auditoria preventiva automatizada. READY significa apenas que nenhuma falha bloqueante foi encontrada nas verificações executadas; não é garantia nem probabilidade de aprovação pela Apple. A decisão final é sempre do App Review.

## Resumo

- Gerado em: 2026-10-16T23:14:44Z — ferramenta appstore-review-guardian 0.1.0
- Projeto: `/private/tmp/asrg-ex/fixture/flutter_app` · IPA/App: `/private/tmp/asrg-ex/fixture/Example-debug-signed.ipa`
- Contagem: BLOCKER 3, CRITICAL 1, WARNING 10, INFO 1, PASS 41, NOT_VERIFIED 16, NOT_APPLICABLE 3
- Regras: 71 (consultadas em 2026-10-16, idade 0 dias)
- Ambiente: darwin, Python 3.12.13, rede=não, ferramentas: codesign=sim, security=sim, otool=sim, lipo=sim, plutil=sim
- Cache incremental: 0 reutilizado(s), 2 recalculado(s)
- Motivos do status: BLOCKER ENT-GTA-001: get-task-allow em build de distribuição; BLOCKER SIGN-002: Verificação criptográfica da assinatura (codesign); BLOCKER SIGN-004: Provisioning profile de App Store; CRITICAL ENT-APS-001: aps-environment

## Indicadores de prontidão

_Indicadores de prontidão = proporção de verificações executadas que passaram (conformity) e proporção de verificações que puderam ser executadas (coverage). Não são probabilidade de aprovação._

| Indicador | Conformidade | Cobertura | Executadas | Não verificadas | Bloqueantes |
|---|---|---|---|---|---|
| Conformidade técnica | 79% | 97% | 33 | 1 | 4 |
| Conformidade com diretrizes Apple | 74% | 90% | 19 | 2 | 0 |
| Preparação da submissão | 0% | 13% | 2 | 13 | 0 |

## Bloqueios (BLOCKER)

### F001 [BLOCKER] get-task-allow em build de distribuição

- Regra: `ENT-GTA-001` · nível: mandatory · método: deterministic · confiança: 1.0
- Componente: `app` · arquivo: `Runner.app (assinatura)`
- Evidência: get-task-allow=true na assinatura do bundle
- Explicação: get-task-allow=true indica assinatura de desenvolvimento (depuração). Entitlements precisam ser autorizados pelo perfil (TN3125); perfis App Store não autorizam get-task-allow, logo o build não é aceito.
- Recomendação: Exporte com perfil App Store: 'flutter build ipa --release --export-options-plist=ios/ExportOptions.plist' (method app-store-connect).
- Referência oficial: https://developer.apple.com/documentation/technotes/tn3125-inside-code-signing-provisioning-profiles (consultada em 2026-10-16)

### F002 [BLOCKER] Verificação criptográfica da assinatura (codesign)

- Regra: `SIGN-002` · nível: mandatory · método: deterministic · confiança: 0.95
- Componente: `app` · arquivo: `Runner.app`
- Evidência: codesign --verify falhou (rc=1): /var/folders/0w/yy0n_v_13092j55ms2yk21zr0000gn/T/asrg-xh19h4ra/Payload/Runner.app: Unknown format in import. | In architecture: arm64
- Explicação: codesign --verify --deep --strict precisa aceitar o bundle. Requer macOS; sem a ferramenta fica NOT_VERIFIED.
- Recomendação: Reassine/exporte novamente pelo Xcode Organizer ou 'flutter build ipa'. Não modifique o bundle após assinar.
- Referência oficial: https://developer.apple.com/documentation/security (consultada em 2026-10-16)

### F003 [BLOCKER] Provisioning profile de App Store

- Regra: `SIGN-004` · nível: mandatory · método: deterministic · confiança: 0.9
- Componente: `app` · arquivo: `Runner.app/embedded.mobileprovision`
- Evidência: perfil 'Test development' tipo=development, expira=2027-05-04T23:14:44+00:00, lido via stdlib; perfil do tipo development (App Store não tem ProvisionedDevices/ProvisionsAllDevices)
- Explicação: embedded.mobileprovision deve existir, não estar expirado e ser de distribuição App Store (sem ProvisionedDevices nem ProvisionsAllDevices, conforme TN3125).
- Recomendação: Gere um perfil 'App Store Connect' válido no portal Apple Developer e reexporte.
- Referência oficial: https://developer.apple.com/documentation/technotes/tn3125-inside-code-signing-provisioning-profiles (consultada em 2026-10-16)

## Críticos (CRITICAL)

### F004 [CRITICAL] aps-environment

- Regra: `ENT-APS-001` · nível: mandatory · método: deterministic · confiança: 1.0
- Componente: `app` · arquivo: `Runner.app (assinatura)`
- Evidência: aps-environment='development' no artefato assinado
- Explicação: O Xcode define aps-environment a partir do perfil: development em perfis de desenvolvimento, production em perfis de distribuição. 'development' no artefato final indica perfil errado.
- Recomendação: Assine com perfil de distribuição App Store; o valor deve ser 'production'.
- Referência oficial: https://developer.apple.com/documentation/bundleresources/entitlements/aps-environment (consultada em 2026-10-16)

## Alertas (WARNING)

### F005 [WARNING] Exclusão de conta no app

- Regra: `REV-ACCTDEL-001` · guideline 5.1.1(v) · nível: mandatory · método: heuristic · confiança: 0.55
- Componente: `project`
- Evidência: criação de conta ["auth/auth.dart:3: Future<void> signUp() async { await api.post('/auth/register'); }"]; nenhum fluxo de exclusão encontrado
- Explicação: 5.1.1(v): se o app permite criar conta, precisa oferecer exclusão de conta dentro do app (desativar não basta).
- Recomendação: Implemente exclusão de conta no app (Configurações > Excluir conta) chamando o backend.
- Referência oficial: https://developer.apple.com/support/offering-account-deletion-in-your-app/ (consultada em 2026-10-16)

### F006 [WARNING] Login social e alternativa equivalente

- Regra: `REV-LOGIN-001` · guideline 4.8 · nível: mandatory · método: heuristic · confiança: 0.6
- Componente: `project`
- Evidência: login social: deps=['google_sign_in'] código=['auth/auth.dart:4: final g = GoogleSignIn();']; nenhuma alternativa equivalente (ex.: Sign in with Apple) encontrada
- Explicação: 4.8: apps que usam login de terceiros/social para a conta principal precisam oferecer outro serviço equivalente que limite dados a nome/e-mail, permita ocultar e-mail e não rastreie sem consentimento (ex.: Sign in with Apple).
- Recomendação: Adicione Sign in with Apple (pacote sign_in_with_apple + capability) ou outro login que atenda à 4.8.
- Referência oficial: https://developer.apple.com/app-store/review/guidelines/#login-services (consultada em 2026-10-16)

### F007 [WARNING] Privacy manifest do app

- Regra: `PRIV-MANIFEST-001` · guideline 5.1.1 · nível: mandatory · método: deterministic · confiança: 0.6
- Componente: `project` · arquivo: `ios/Runner/PrivacyInfo.xcprivacy`
- Evidência: ios/Runner/PrivacyInfo.xcprivacy ausente (manifest do app)
- Explicação: O app registra em PrivacyInfo.xcprivacy os tipos de dados coletados e as Required Reason APIs usadas. Ausência no bundle do app é risco quando o próprio código usa essas APIs.
- Recomendação: Crie via Xcode: File > New > File > App Privacy, target Runner; declare NSPrivacyCollectedDataTypes e as Required Reason APIs do seu código (ex.: UserDefaults CA92.1 via shared_preferences).
- Referência oficial: https://developer.apple.com/documentation/bundleresources/privacy-manifest-files (consultada em 2026-10-16)

### F008 [WARNING] SDK listado pela Apple sem privacy manifest

- Regra: `PRIV-SDK-001` · guideline 5.1.1 · nível: mandatory · método: deterministic · confiança: 0.75
- Componente: `Alamofire` · arquivo: `Runner.app/Frameworks/Alamofire.framework`
- Evidência: Alamofire (lista Apple) sem PrivacyInfo.xcprivacy no bundle
- Explicação: SDKs da lista oficial precisam incluir privacy manifest em apps novos ou quando a atualização adiciona o SDK; SDKs binários também precisam de assinatura. O guardian não sabe se é app novo ou se a atualização adicionou o SDK; por isso emite WARNING com a evidência.
- Recomendação: Atualize Alamofire para uma versão que inclua privacy manifest (obrigatório em app novo ou quando a atualização adiciona o SDK).
- Referência oficial: https://developer.apple.com/support/third-party-SDK-requirements/ (consultada em 2026-10-16)

### F009 [WARNING] Purpose strings genéricas

- Regra: `PRIV-USAGE-002` · guideline 5.1.1(ii) · nível: mandatory · método: heuristic · confiança: 0.7
- Componente: `project` · arquivo: `ios/Runner/Info.plist`
- Evidência: NSPhotoLibraryUsageDescription='Allow this app to access your photos' parece genérico
- Explicação: Textos genéricos (ex.: 'Allow this app to access your photos') costumam ser rejeitados por não explicar o uso dos dados (5.1.1(ii)).
- Recomendação: Explique o uso concreto, ex.: 'O Mently usa suas fotos para definir a foto de perfil.' em NSPhotoLibraryUsageDescription (e localize em InfoPlist.strings).
- Referência oficial: https://developer.apple.com/app-store/review/guidelines/#data-collection-and-storage (consultada em 2026-10-16)

### F010 [WARNING] Declaração de criptografia (ITSAppUsesNonExemptEncryption)

- Regra: `PLIST-ENC-001` · nível: recommended · método: deterministic · confiança: 1.0
- Componente: `project` · arquivo: `ios/Runner/Info.plist`
- Evidência: ITSAppUsesNonExemptEncryption ausente
- Explicação: Sem a chave, o App Store Connect exige o questionário de export compliance a cada upload. Incluir a chave agiliza a submissão; o valor correto depende do uso real de criptografia.
- Recomendação: Se o app só usa criptografia isenta (ex.: HTTPS do sistema), adicione <key>ITSAppUsesNonExemptEncryption</key><false/>; caso contrário responda o questionário no ASC.
- Referência oficial: https://developer.apple.com/documentation/bundleresources/information-property-list/itsappusesnonexemptencryption (consultada em 2026-10-16)

### F011 [WARNING] Declaração de criptografia (ITSAppUsesNonExemptEncryption)

- Regra: `PLIST-ENC-001` · nível: recommended · método: deterministic · confiança: 1.0
- Componente: `app` · arquivo: `Runner.app/Info.plist`
- Evidência: ITSAppUsesNonExemptEncryption ausente
- Explicação: Sem a chave, o App Store Connect exige o questionário de export compliance a cada upload. Incluir a chave agiliza a submissão; o valor correto depende do uso real de criptografia.
- Recomendação: Se o app só usa criptografia isenta (ex.: HTTPS do sistema), adicione <key>ITSAppUsesNonExemptEncryption</key><false/>; caso contrário responda o questionário no ASC.
- Referência oficial: https://developer.apple.com/documentation/bundleresources/information-property-list/itsappusesnonexemptencryption (consultada em 2026-10-16)

### F012 [WARNING] aps-environment

- Regra: `ENT-APS-001` · nível: mandatory · método: deterministic · confiança: 0.5
- Componente: `project` · arquivo: `ios/Runner/Runner.entitlements`
- Evidência: aps-environment=development no .entitlements de origem
- Explicação: O Xcode define aps-environment a partir do perfil: development em perfis de desenvolvimento, production em perfis de distribuição. 'development' no artefato final indica perfil errado.
- Recomendação: Normalmente o Xcode troca para 'production' ao exportar com perfil de distribuição; confirme no .ipa ('codesign -d --entitlements - Payload/Runner.app') ou crie RunnerRelease.entitlements com 'production'.
- Referência oficial: https://developer.apple.com/documentation/bundleresources/entitlements/aps-environment (consultada em 2026-10-16)

### F013 [WARNING] NSAllowsArbitraryLoads

- Regra: `PLIST-ATS-001` · nível: recommended · método: deterministic · confiança: 0.95
- Componente: `app` · arquivo: `Runner.app/Info.plist`
- Evidência: NSAllowsArbitraryLoads=YES
- Explicação: Com NSAllowsArbitraryLoads=YES você precisa fornecer justificativa durante o App Review; a Apple recomenda exceções mais estreitas.
- Recomendação: Prefira NSExceptionDomains específicos; se mantiver, prepare a justificativa para o App Review.
- Referência oficial: https://developer.apple.com/documentation/bundleresources/information-property-list/nsapptransportsecurity/nsallowsarbitraryloads (consultada em 2026-10-16)

### F014 [WARNING] CFBundleIdentifier definido

- Regra: `PLIST-ID-001` · nível: mandatory · método: deterministic · confiança: 0.9
- Componente: `app`
- Evidência: bundle id do projeto (com.example.demo) ≠ do artefato (com.example.app)
- Explicação: O bundle identifier deve estar definido e resolvido (sem variáveis de build não resolvidas no artefato).
- Recomendação: Confirme que o .ipa corresponde a este projeto/flavor.
- Referência oficial: https://developer.apple.com/documentation/bundleresources/information-property-list (consultada em 2026-10-16)

## Informativos (INFO)

### F015 [INFO] Inventário Flutter/Dart

- Regra: `PROJ-FLUTTER-001` · nível: recommended · método: deterministic · confiança: 1.0
- Componente: `flutter` · arquivo: `pubspec.lock`
- Evidência: Flutter >=3.24.0, Dart >=3.5.0 <4.0.0, 0 plugins iOS, 2 pods
- Explicação: Versões de Flutter/Dart e dependências registradas para rastreabilidade (sem requisito de reprovação).
- Referência oficial: https://docs.flutter.dev/deployment/ios (consultada em 2026-10-16)

## Pendências (NOT_VERIFIED)

- [ ] `PRIV-SDK-001` SDK listado pela Apple sem privacy manifest (**obrigatório**) — pod FirebaseMessaging 11.0.0: ios/Pods ausente; rode 'pod install' ou audite o .ipa
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
- [ ] `REV-DEMO-001` Conta de demonstração para o revisor (**obrigatório**) — app tem login (['auth/auth.dart:4: final g = GoogleSignIn();']); credenciais de demo só verificáveis no ASC → Preencha App Review Information > Sign-in required com usuário/senha de demonstração e mantenha o backend ativo.
- [ ] `SIGN-003` Certificado de distribuição (**obrigatório**) — autoridade do certificado não identificada

## Verificações aprovadas / não aplicáveis

| Status | Regra | Componente | Evidência |
|---|---|---|---|
| PASS | `REV-PLACEHOLDER-001` | project | nenhum placeholder óbvio em literais do código |
| PASS | `RULES-FRESH-001` | rules | regras consultadas em 2026-10-16 (0 dias); fontes não rechecadas neste diretório |
| PASS | `PRIV-DATA-001` | Flutter.framework | 0 tipo(s) de dados declarados |
| PASS | `PRIV-DATA-001` | app | 1 tipo(s) de dados declarados |
| PASS | `PRIV-MANIFEST-001` | app | PrivacyInfo.xcprivacy presente na raiz do app |
| PASS | `PRIV-MANIFEST-002` | Flutter.framework | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-MANIFEST-002` | app | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-POLICY-001` | project | referência à política de privacidade no código: ["auth/auth.dart:5: final privacyUrl = 'https://example.com/privacy-policy';"] |
| PASS | `PRIV-RRAPI-001` | Flutter.framework | categorias declaradas: ['NSPrivacyAccessedAPICategoryFileTimestamp', 'NSPrivacyAccessedAPICategoryUserDefaults'] |
| PASS | `PRIV-RRAPI-001` | app | categorias declaradas: ['NSPrivacyAccessedAPICategoryFileTimestamp', 'NSPrivacyAccessedAPICategoryUserDefaults'] |
| PASS | `PRIV-RRAPI-002` | app | nenhum símbolo de Required Reason API encontrado nos binários |
| PASS | `PRIV-SDK-001` | Flutter | Flutter (lista Apple) com PrivacyInfo.xcprivacy no bundle |
| PASS | `PRIV-TRACK-001` | Flutter.framework | NSPrivacyTracking=False, domínios=0 |
| PASS | `PRIV-TRACK-001` | app | NSPrivacyTracking=False, domínios=0 |
| PASS | `PRIV-USAGE-001` | app | NSCameraUsageDescription preenchido (58 caracteres) |
| PASS | `BIN-ARCH-001` | app | arquiteturas=['arm64'], plataforma=ios, minos=15.0.0, sdk=26.4.0 |
| PASS | `BIN-ARCH-002` | app | 2 framework(s) sem fatias de simulador |
| PASS | `BIN-DEBUG-001` | app | sem .dSYM ou segmento __DWARF no bundle |
| PASS | `BUILD-SDK-001` | app | DTXcode=2641 (Xcode 26), DTSDKName=iphoneos26.4, DTPlatformVersion=26.4 |
| PASS | `ENT-PROFILE-001` | app | 3 entitlement(s) presentes na allowlist do perfil |
| PASS | `IPA-STRUCT-001` | ipa | ZIP íntegro e seguro (10 entradas, CRC verificado) |
| PASS | `IPA-STRUCT-002` | ipa | Payload/Runner.app |
| PASS | `PLIST-ATS-001` | project | NSAllowsArbitraryLoads ausente ou NO |
| PASS | `PLIST-BG-001` | project | UIBackgroundModes=['remote-notification'] |
| PASS | `PLIST-BG-001` | app | UIBackgroundModes=['remote-notification'] |
| PASS | `PLIST-ID-001` | project | CFBundleIdentifier=com.example.demo |
| PASS | `PLIST-ID-001` | app | CFBundleIdentifier=com.example.app |
| PASS | `PLIST-MINOS-001` | app | MinimumOSVersion=15.0 (>= 13.0) |
| PASS | `PLIST-PARSE-001` | project | Info.plist de origem lido |
| PASS | `PLIST-PARSE-001` | app | Info.plist lido com plistlib |
| PASS | `PLIST-VER-001` | project | CFBundleShortVersionString=2.0.1 |
| PASS | `PLIST-VER-001` | app | CFBundleShortVersionString=1.2.0 |
| PASS | `PLIST-VER-002` | project | CFBundleVersion=7 |
| PASS | `PLIST-VER-002` | app | CFBundleVersion=42 |
| PASS | `PROJ-DEPLOY-001` | project | deployment targets {'project Release': '15.0', 'Podfile platform': '15.0'} >= 13.0 |
| PASS | `PROJ-ENT-001` | project | Release usa Runner/Runner.entitlements (1 chaves) |
| PASS | `PROJ-EXPORT-001` | project | ExportOptions method=app-store-connect |
| PASS | `REV-PUSH-001` | project | remote-notification + aps-environment=development |
| PASS | `REV-PUSH-001` | project | push via ['firebase_messaging']; aps-environment='development' |
| PASS | `REV-PUSH-001` | app | remote-notification + aps-environment=development |
| PASS | `SIGN-001` | app | _CodeSignature/CodeResources e LC_CODE_SIGNATURE presentes |
| NOT_APPLICABLE | `REV-IAP-001` | project | sem IAP nem pagamento externo detectados |
| NOT_APPLICABLE | `REV-UGC-001` | project | nenhum sinal de conteúdo gerado por usuários |
| NOT_APPLICABLE | `PRIV-ATT-001` | app | sem NSPrivacyTracking=true no manifest do app e sem AppTrackingTransparency vinculado |

---
Este relatório é uma auditoria preventiva automatizada. READY significa apenas que nenhuma falha bloqueante foi encontrada nas verificações executadas; não é garantia nem probabilidade de aprovação pela Apple. A decisão final é sempre do App Review.
