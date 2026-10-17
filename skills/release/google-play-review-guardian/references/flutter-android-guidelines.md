# Projetos Flutter — o que o auditor lê (somente leitura)

| Fonte | Extraído |
|---|---|
| `pubspec.yaml` | nome, `version` (versionName+versionCode), restrições Dart/Flutter, dependências |
| `pubspec.lock` | versões travadas, `sdks.dart`/`sdks.flutter` |
| `android/app/build.gradle` ou `.kts` | namespace, applicationId, compile/min/targetSdk (literal ou expressão), ndkVersion, buildTypes.release (debuggable, minify, shrink, signingConfig), productFlavors, senhas literais (só presença) |
| `android/settings.gradle(.kts)` / `build.gradle` | versão do AGP e do plugin Kotlin |
| `android/gradle/wrapper/gradle-wrapper.properties` | versão do Gradle |
| `android/local.properties` | **somente** a chave `flutter.sdk` (caminho), para resolver `flutter.targetSdkVersion` em `FlutterExtension.kt` e a versão do Flutter em `bin/cache/flutter.version.json` |
| `android/app/src/main/AndroidManifest.xml` | permissões e componentes do projeto (sem merge) |
| `android/app/src/*/res/xml/*network*` | cleartext e CAs de usuário |
| `lib/**/*.dart`, `android/**/*.kt|java` | sinais heurísticos: segredos (redigidos), `http://`, WebView JS, criação/exclusão de conta, fluxos de compra, pagamentos externos |
| `key.properties`, `*.jks`, `*.keystore` | **apenas presença** e status Git (`git ls-files`, `git check-ignore`); conteúdo nunca é aberto |

## Boas práticas verificadas

- `flutter build appbundle --release` (fora do auditor) gera `build/app/outputs/bundle/release/app-release.aab`, que é auto-descoberto em `--full` e `gate`.
- O valor efetivo de targetSdk é o do **artefato**. `targetSdkVersion flutter.targetSdkVersion` depende da versão do Flutter SDK instalada; o auditor marca a resolução como heurística.
- `minifyEnabled true` + upload do `mapping.txt` (o bundle inclui `BUNDLE-METADATA/com.android.tools.build.obfuscation/` quando R8 roda).
- Não versionar `key.properties`/keystore; `android/.gitignore` padrão do Flutter já contém `key.properties`, `**/*.keystore`, `**/*.jks` — verifique se não foram comentados.
- Flutter ≥ 3.24 com NDK r28 e AGP ≥ 8.5.1 tende a gerar `libflutter.so`/`libapp.so` alinhadas a 16 KB; bibliotecas pré-compiladas de plugins (ex.: Sentry) precisam ser verificadas individualmente.
