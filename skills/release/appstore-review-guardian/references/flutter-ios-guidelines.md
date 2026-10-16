# Projetos Flutter: o que o guardian inspeciona

Arquivos lidos (somente leitura): `pubspec.yaml`, `pubspec.lock`, `.flutter-plugins-dependencies`, `ios/Podfile`, `ios/Podfile.lock`, `ios/Runner.xcodeproj/project.pbxproj`, `ios/Runner/Info.plist`, `ios/Runner/*.entitlements`, `ios/Runner/PrivacyInfo.xcprivacy`, `ios/ExportOptions.plist`, `ios/Flutter/Generated.xcconfig`, `ios/*.storekit` e código em `lib/` (`*.g.dart`, `*.freezed.dart` e `*.gen.dart` são ignorados).

O guardian nunca roda `flutter`, `pod` ou `xcodebuild`.

## Checagens específicas

- **Versão:** `version: X.Y.Z+N` no pubspec (ou `FLUTTER_BUILD_NAME`/`NUMBER` do `Generated.xcconfig`) resolve `$(FLUTTER_BUILD_NAME)`.
- **Release:** a configuração Release do target com `PRODUCT_BUNDLE_IDENTIFIER` define `CODE_SIGN_ENTITLEMENTS`, `INFOPLIST_FILE` e o deployment target. O Podfile (`platform :ios`) também é conferido.
- **Plugins iOS:** `.flutter-plugins-dependencies` lista os plugins que realmente compilam para iOS. Para os que estão na lista de SDKs da Apple, o guardian procura `PrivacyInfo.xcprivacy` no código-fonte do plugin (pub cache). Sem o fonte, o resultado é NOT_VERIFIED.
- **CocoaPods:** os pods do `Podfile.lock` que estão na lista Apple são procurados em `ios/Pods/<pod>`. Sem `ios/Pods`, o resultado é NOT_VERIFIED.
- **Build debug:** `App.framework/flutter_assets/kernel_blob.bin` no bundle indica build debug/JIT, que trava fora do depurador (CRITICAL).
- **Push:** `firebase_messaging`/`onesignal_flutter` ou `remote-notification` exigem `aps-environment`. No `.entitlements` de origem, `development` é comum, porque o Xcode troca o valor pelo perfil na exportação. Confirme no `.ipa`.
- **Login:** `GoogleSignIn(`, `mutate("google")`, `OAuthProvider.google` etc. contam como login social. `sign_in_with_apple`, `mutate("apple")` e o entitlement `com.apple.developer.applesignin` contam como alternativa (4.8).
- **Universal Links:** `applinks:` em associated domains. Com `--network`, o guardian baixa `/.well-known/apple-app-site-association`.

## Como evitar falsos positivos

- Ter o pacote não prova uso do recurso. Por isso as checagens de permissão são heurísticas e nunca bloqueiam.
- Linhas de `import`/`export`/comentário são ignoradas nos sinais de código.
- Fachadas Dart federadas não embarcam código iOS e ficam NOT_APPLICABLE.

## Build descartável para pré-auditoria

Rode em uma **cópia** do projeto (nunca no original):

```
rsync -a --exclude build --exclude .dart_tool --exclude ios/Pods <app>/ /private/tmp/app-copy/
cd /private/tmp/app-copy && flutter build ios --release --no-codesign
```

O `.app` resultante não é assinado. Rotule-o como UNSIGNED: `SIGN-001` dá BLOCKER e as demais checagens de assinatura ficam NOT_VERIFIED, o que está correto. `flutter build ipa` com assinatura pode acionar a assinatura automática do Xcode e alterar perfis na conta Apple Developer, então só rode com autorização explícita.
