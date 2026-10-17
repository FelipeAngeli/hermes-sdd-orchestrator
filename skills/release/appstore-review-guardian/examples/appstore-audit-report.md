# APP STORE REVIEW GUARDIAN

```text
APP STORE REVIEW GUARDIAN
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Application: Example App
Bundle ID: com.example.app
Version: 2.0.1
Build: 7
Modo: gate

AUDITORIA TÉCNICA
Status: BLOCKED

CONFORMIDADE DE PRIVACIDADE
Status: WARNING

CONFORMIDADE APP REVIEW
Status: PASS_WITH_PENDING

APP STORE CONNECT / SUBMISSÃO
Status: PASS_WITH_PENDING

FINAL STATUS: BLOCKED

Findings:
[BLOCKER] Bundle assinado (_CodeSignature) (app)
[WARNING] Uso de Required Reason API sem declaração (app)
[WARNING] aps-environment (project)
```

> Este relatório é uma auditoria preventiva automatizada. READY significa apenas que nenhuma falha bloqueante foi encontrada nas verificações executadas; não é garantia nem probabilidade de aprovação pela Apple. A decisão final é sempre do App Review.

## Resumo

- Gerado em: 2026-10-17T00:27:24Z — ferramenta appstore-review-guardian 0.1.0
- Projeto: `/private/tmp/asrg-examples/flutter_app` · IPA/App: `/private/tmp/asrg-examples/Example-unsigned.ipa [UNSIGNED — não serve para upload; assinatura/perfil não verificáveis]`
- Contagem: BLOCKER 1, CRITICAL 0, WARNING 2, INFO 1, PASS 46, NOT_VERIFIED 26, NOT_APPLICABLE 1
- Regras: 72 (consultadas em 2026-10-16, idade 0 dias)
- Ambiente: darwin, Python 3.12.13, rede=não, ferramentas: codesign=sim, security=sim, otool=sim, lipo=sim, plutil=sim
- Cache incremental: 0 reutilizado(s), 2 recalculado(s)
- Motivos do status: BLOCKER SIGN-001: Bundle assinado (_CodeSignature)

## Indicadores de prontidão

_Indicadores de prontidão = proporção de verificações executadas que passaram (conformity) e proporção de verificações que puderam ser executadas (coverage). Não são probabilidade de aprovação._

| Indicador | Conformidade | Cobertura | Executadas | Não verificadas | Bloqueantes |
|---|---|---|---|---|---|
| Conformidade técnica | 93% | 84% | 27 | 5 | 1 |
| Conformidade com diretrizes Apple | 95% | 70% | 19 | 8 | 0 |
| Preparação da submissão | 100% | 13% | 2 | 13 | 0 |

## Bloqueios (BLOCKER)

### F001 [BLOCKER] Bundle assinado (_CodeSignature)

- Regra: `SIGN-001` · nível: mandatory · método: deterministic · confiança: 1.0
- Componente: `app` · arquivo: `Runner.app/_CodeSignature/CodeResources`
- Evidência: _CodeSignature/CodeResources ausente; executável sem LC_CODE_SIGNATURE
- Explicação: Para distribuição pela App Store o .app precisa estar assinado; ausência de _CodeSignature/CodeResources comprova build não assinado.
- Recomendação: Gere o build assinado para distribuição: 'flutter build ipa --release --export-options-plist=ios/ExportOptions.plist'. Build '--no-codesign' só serve para pré-auditoria.
- Referência oficial: https://developer.apple.com/documentation/xcode/distributing-your-app-for-beta-testing-and-releases (consultada em 2026-10-16)

## Alertas (WARNING)

### F002 [WARNING] Uso de Required Reason API sem declaração

- Regra: `PRIV-RRAPI-002` · guideline 5.1.1 · nível: mandatory · método: heuristic · confiança: 0.55
- Componente: `app` · arquivo: `Runner.app`
- Evidência: categorias referenciadas sem declaração em nenhum manifest: ['NSPrivacyAccessedAPICategorySystemBootTime']; exemplos: ['Runner:_mach_absolute_time']
- Explicação: Símbolos de APIs de motivo obrigatório encontrados no binário sem categoria declarada em nenhum manifest. Referência não prova uso em runtime.
- Recomendação: Confirme o uso e declare a categoria com o motivo correto no PrivacyInfo.xcprivacy do componente responsável.
- Referência oficial: https://developer.apple.com/documentation/bundleresources/describing-use-of-required-reason-api (consultada em 2026-10-16)

### F003 [WARNING] aps-environment

- Regra: `ENT-APS-001` · nível: mandatory · método: deterministic · confiança: 0.5
- Componente: `project` · arquivo: `ios/Runner/Runner.entitlements`
- Evidência: aps-environment=development no .entitlements de origem
- Explicação: O Xcode define aps-environment a partir do perfil: development em perfis de desenvolvimento, production em perfis de distribuição. 'development' no artefato final indica perfil errado.
- Recomendação: Normalmente o Xcode troca para 'production' ao exportar com perfil de distribuição; confirme no .ipa ('codesign -d --entitlements - Payload/Runner.app') ou crie RunnerRelease.entitlements com 'production'.
- Referência oficial: https://developer.apple.com/documentation/bundleresources/entitlements/aps-environment (consultada em 2026-10-16)

## Informativos (INFO)

### F004 [INFO] Inventário Flutter/Dart

- Regra: `PROJ-FLUTTER-001` · nível: recommended · método: deterministic · confiança: 1.0
- Componente: `flutter` · arquivo: `pubspec.lock`
- Evidência: Flutter >=3.24.0, Dart >=3.5.0 <4.0.0, 0 plugins iOS, 2 pods
- Explicação: Versões de Flutter/Dart e dependências registradas para rastreabilidade (sem requisito de reprovação).
- Referência oficial: https://docs.flutter.dev/deployment/ios (consultada em 2026-10-16)

## Pendências (NOT_VERIFIED)

- [ ] `REV-ACCTDEL-001` Exclusão de conta no app (**obrigatório**) — criação de conta ["auth/auth.dart:3: Future<void> signUp() async { await api.post('/auth/register'); }"]; indício de exclusão ["auth/auth.dart:6: Future<void> deleteAccount() async { await api.delete('/me'); }"] (heurística: requer confirmação humana) → Confirme que o fluxo apaga a conta (não só desativa) e é acessível sem contato com suporte.
- [ ] `REV-IAP-001` Compras de conteúdo digital via In-App Purchase (**obrigatório**) — sem IAP nem pagamento externo detectados (ausência heurística não prova N/A) → Confirme que o app não vende conteúdo digital; se vender, use IAP (3.1.1).
- [ ] `REV-LOGIN-001` Login social e alternativa equivalente (**obrigatório**) — login social: deps=['google_sign_in'] código=['auth/auth.dart:4: final g = GoogleSignIn();']; indício de alternativa Sign in with Apple: deps=['sign_in_with_apple'] código=['auth/auth.dart:5: Future<void> apple() => SignInWithApple.getAppleIDCredential(scopes: []);'] entitlement=True (heurística: requer confirmação humana) → Confirme na UI que a opção Apple (ou equivalente 4.8) é oferecida com destaque equivalente.
- [ ] `REV-PLACEHOLDER-001` Placeholders e conteúdo temporário (**obrigatório**) — nenhum placeholder óbvio em literais do código (ausência heurística: revise as telas acessíveis ao revisor) → Navegue pelo build de release e confirme que não há telas vazias, 'em breve' ou lorem ipsum (2.1).
- [ ] `REV-UGC-001` Conteúdo gerado por usuários (**obrigatório**) — nenhum sinal de conteúdo gerado por usuários (ausência heurística não prova N/A) → Confirme que usuários não publicam conteúdo visível a outros; se publicarem, atenda à 1.2.
- [ ] `PRIV-POLICY-001` Política de privacidade no app (**obrigatório**) — indício de política de privacidade no código: ["auth/auth.dart:7: final privacyUrl = 'https://example.com/privacy-policy';"] (heurística: requer confirmação humana) → Confirme que o link está acessível no app (ex.: tela de login e Configurações).
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
- [ ] `ENT-GTA-001` get-task-allow em build de distribuição (**obrigatório**) — bundle não assinado: verificação impossível
- [ ] `REV-PUSH-001` Push notifications configurado (**obrigatório**) — remote-notification declarado; entitlements não legíveis (bundle não assinado)
- [ ] `SIGN-002` Verificação criptográfica da assinatura (codesign) (**obrigatório**) — bundle não assinado: verificação impossível
- [ ] `SIGN-003` Certificado de distribuição (**obrigatório**) — bundle não assinado: verificação impossível
- [ ] `SIGN-004` Provisioning profile de App Store (**obrigatório**) — embedded.mobileprovision ausente (build não assinado)

## Verificações aprovadas / não aplicáveis

| Status | Regra | Componente | Evidência |
|---|---|---|---|
| PASS | `REV-LOGIN-002` | project | Sign in with Apple no código (['auth/auth.dart:5: Future<void> apple() => SignInWithApple.getAppleIDCredential(scopes: []);']); entitlement com.apple.developer. |
| PASS | `RULES-FRESH-001` | rules | regras consultadas em 2026-10-16 (0 dias); fontes não rechecadas neste diretório |
| PASS | `PRIV-DATA-001` | app | 1 tipo(s) de dados declarados |
| PASS | `PRIV-DATA-001` | Flutter.framework | 0 tipo(s) de dados declarados |
| PASS | `PRIV-DATA-001` | app | 1 tipo(s) de dados declarados |
| PASS | `PRIV-MANIFEST-001` | app | PrivacyInfo.xcprivacy presente na raiz do app |
| PASS | `PRIV-MANIFEST-002` | app | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-MANIFEST-002` | Flutter.framework | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-MANIFEST-002` | app | manifest bem formado; chaves e tipos válidos |
| PASS | `PRIV-RRAPI-001` | app | categorias declaradas: ['NSPrivacyAccessedAPICategoryFileTimestamp', 'NSPrivacyAccessedAPICategoryUserDefaults'] |
| PASS | `PRIV-RRAPI-001` | Flutter.framework | categorias declaradas: ['NSPrivacyAccessedAPICategoryFileTimestamp', 'NSPrivacyAccessedAPICategoryUserDefaults'] |
| PASS | `PRIV-RRAPI-001` | app | categorias declaradas: ['NSPrivacyAccessedAPICategoryFileTimestamp', 'NSPrivacyAccessedAPICategoryUserDefaults'] |
| PASS | `PRIV-SDK-001` | Flutter | Flutter (lista Apple) com PrivacyInfo.xcprivacy no bundle |
| PASS | `PRIV-TRACK-001` | app | NSPrivacyTracking=False, domínios=0 |
| PASS | `PRIV-TRACK-001` | Flutter.framework | NSPrivacyTracking=False, domínios=0 |
| PASS | `PRIV-TRACK-001` | app | NSPrivacyTracking=False, domínios=0 |
| PASS | `PRIV-USAGE-001` | project | NSPhotoLibraryUsageDescription preenchido (51 caracteres) |
| PASS | `PRIV-USAGE-001` | app | NSCameraUsageDescription preenchido (58 caracteres) |
| PASS | `PRIV-USAGE-001` | app | NSPhotoLibraryUsageDescription preenchido (51 caracteres) |
| PASS | `PLIST-ENC-001` | project | ITSAppUsesNonExemptEncryption=False declarado (exatidão não verificável) |
| PASS | `PLIST-ENC-001` | app | ITSAppUsesNonExemptEncryption=False declarado (exatidão não verificável) |
| PASS | `BIN-ARCH-001` | app | arquiteturas=['arm64'], plataforma=ios, minos=15.0.0, sdk=26.4.0 |
| PASS | `BIN-ARCH-002` | app | 2 framework(s) sem fatias de simulador |
| PASS | `BIN-DEBUG-001` | app | sem .dSYM ou segmento __DWARF no bundle |
| PASS | `BIN-FLUTTER-001` | app | App.framework sem kernel_blob.bin (AOT release) |
| PASS | `BUILD-SDK-001` | app | DTXcode=2641 (Xcode 26), DTSDKName=iphoneos26.4, DTPlatformVersion=26.4 |
| PASS | `IPA-STRUCT-001` | ipa | ZIP íntegro e seguro (8 entradas, CRC verificado) |
| PASS | `IPA-STRUCT-002` | ipa | Payload/Runner.app |
| PASS | `PLIST-ATS-001` | project | NSAllowsArbitraryLoads ausente ou NO |
| PASS | `PLIST-ATS-001` | app | NSAllowsArbitraryLoads ausente ou NO |
| PASS | `PLIST-BG-001` | project | UIBackgroundModes=['remote-notification'] |
| PASS | `PLIST-BG-001` | app | UIBackgroundModes=['remote-notification'] |
| PASS | `PLIST-ID-001` | project | CFBundleIdentifier=com.example.app |
| PASS | `PLIST-ID-001` | app | CFBundleIdentifier=com.example.app |
| PASS | `PLIST-MINOS-001` | app | MinimumOSVersion=15.0 (>= 13.0) |
| PASS | `PLIST-PARSE-001` | project | Info.plist de origem lido |
| PASS | `PLIST-PARSE-001` | app | Info.plist lido com plistlib |
| PASS | `PLIST-VER-001` | project | CFBundleShortVersionString=2.0.1 |
| PASS | `PLIST-VER-001` | app | CFBundleShortVersionString=2.0.1 |
| PASS | `PLIST-VER-002` | project | CFBundleVersion=7 |
| PASS | `PLIST-VER-002` | app | CFBundleVersion=7 |
| PASS | `PROJ-DEPLOY-001` | project | deployment targets {'project Release': '15.0', 'Podfile platform': '15.0'} >= 13.0 |
| PASS | `PROJ-ENT-001` | project | Release usa Runner/Runner.entitlements (2 chaves) |
| PASS | `PROJ-EXPORT-001` | project | ExportOptions method=app-store-connect |
| PASS | `REV-PUSH-001` | project | remote-notification + aps-environment=development |
| PASS | `REV-PUSH-001` | project | push via ['firebase_messaging']; aps-environment='development' |
| NOT_APPLICABLE | `PRIV-ATT-001` | app | sem NSPrivacyTracking=true no manifest do app e sem AppTrackingTransparency vinculado |

---
Este relatório é uma auditoria preventiva automatizada. READY significa apenas que nenhuma falha bloqueante foi encontrada nas verificações executadas; não é garantia nem probabilidade de aprovação pela Apple. A decisão final é sempre do App Review.
