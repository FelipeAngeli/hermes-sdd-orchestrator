# Requisitos técnicos de release iOS

| Requisito | Fonte | Regra | Severidade |
|---|---|---|---|
| Target iOS 13 ou posterior (desde 2026-09-09) | Upcoming Requirements | PLIST-MINOS-001, PROJ-DEPLOY-001 | BLOCKER |
| Xcode 26+ e SDK iOS 26 (desde 2026-04-28), lidos de `DTXcode`/`DTSDKName`/`DTPlatformVersion` | Upcoming Requirements | BUILD-SDK-001 | BLOCKER |
| `CFBundleShortVersionString` = três inteiros; `CFBundleVersion` = 1 a 3 inteiros | Information Property List | PLIST-VER-001/002 | CRITICAL/BLOCKER |
| `CFBundleIdentifier` resolvido no artefato | Information Property List | PLIST-ID-001 | BLOCKER |
| Não definir `MinimumOSVersion` à mão no Info.plist de origem | MinimumOSVersion | PLIST-MINOS-002 | WARNING (recomendado) |
| Executável arm64, plataforma iOS (não simulador) | 2.5.1 / distribuição | BIN-ARCH-001 | BLOCKER |
| Bundle assinado (`_CodeSignature`, `LC_CODE_SIGNATURE`) | Distribuição | SIGN-001 | BLOCKER |
| `codesign --verify --deep --strict` aceita o bundle (só em macOS) | Security | SIGN-002 | BLOCKER / NOT_VERIFIED sem a ferramenta |
| Certificado Apple/iPhone Distribution | Distribuição | SIGN-003 | BLOCKER |
| Perfil App Store válido (sem ProvisionedDevices/ProvisionsAllDevices, não expirado, appID correto) | TN3125 | SIGN-004 | BLOCKER |
| `get-task-allow` ausente/false na assinatura | TN3125 | ENT-GTA-001 | BLOCKER |
| `aps-environment=production` no artefato | aps-environment | ENT-APS-001 | CRITICAL |
| Entitlements na allowlist do perfil | TN3125 | ENT-PROFILE-001 | BLOCKER |
| ATS: `NSAllowsArbitraryLoads` exige justificativa | NSAllowsArbitraryLoads | PLIST-ATS-001 | WARNING |
| Export compliance (`ITSAppUsesNonExemptEncryption`) | ITSAppUsesNonExemptEncryption / ASC | PLIST-ENC-001, ASC-EXPORT-001 | WARNING |
| Sem fatias de simulador, `.dSYM` ou `__DWARF` no bundle | boas práticas (sem regra Apple citada) | BIN-ARCH-002, BIN-DEBUG-001 | WARNING (recomendado) |

## Verificação de assinatura sem macOS

Sem `codesign`/`security` (Linux, CI sem Xcode), o guardian:
- lê os entitlements direto do superblob da assinatura no Mach-O (stdlib);
- extrai a plist do `embedded.mobileprovision` sem validar a assinatura CMS (o achado registra "lido via stdlib");
- identifica a autoridade por strings no CMS, com confiança menor;
- marca `SIGN-002` como NOT_VERIFIED. Por isso o gate nunca chega a READY sem macOS.

## Comandos úteis (o guardian só recomenda, nunca executa)

```
flutter build ipa --release --export-options-plist=ios/ExportOptions.plist
codesign -d --entitlements - --xml Payload/Runner.app | plutil -convert xml1 -o - -
security cms -D -i Payload/Runner.app/embedded.mobileprovision
xcrun altool --validate-app -f App.ipa -t ios ...   # validação oficial pelo ASC
```
