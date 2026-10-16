# Store review guardians

[Docs index](../README.md) · Related: [Testing](testing.md), [Skill and installer](skill-and-installer.md), [Sub-agents](sub-agents.md)

**Files:** `release/appstore-review-guardian/*`, `release/google-play-review-guardian/*` (under `skills/`).

Two standalone Hermes skills that audit a mobile app **before** it is submitted: `appstore-review-guardian` for iOS (`.ipa`, `.app`, iOS/Flutter projects) and `google-play-review-guardian` for Android (`.aab`, `.apk`, Android/Flutter/Kotlin/Java projects). They are independent of the SDD controller: they install into `~/.hermes/skills/` like any skill and are not copied into projects by `install_project.py`. An SDD run can call either as a release gate (see [Release gate integration](#release-gate-integration)).

## What they guarantee

- **Read-only.** They never modify the audited project, never build, sign, upload or publish, and never change store listings or tracks. Reports and the incremental cache go to `--output-dir`, never inside the project.
- **Evidence or nothing.** Severities are `BLOCKER`, `CRITICAL`, `WARNING`, `INFO`, `PASS`, `NOT_VERIFIED` and `NOT_APPLICABLE`. `PASS` is produced only by a check that ran and met its requirement; a check that could not run (tool missing, no artifact, no store data, heuristic only) is `NOT_VERIFIED` with the reason. Every finding carries rule id, component, evidence (secrets redacted), verification method, official URL, consultation date, confidence, recommendation and whether it is auto-fixable.
- **Gate.** `gate --release` returns `READY`, `NEEDS_REVIEW` or `BLOCKED` with exit codes `0`, `1`, `2` (`3` for usage errors). `READY` means no blocker in the checks that ran, with pending items listed; it is never an approval guarantee. Readiness indicators are split by area and are not approval probabilities.
- **Rules are data.** Requirements (minimum Xcode/SDK, Google Play target API level per form factor with effective dates, privacy-manifest categories, permission policies, testing requirements) live in `rules/*.json` with the official URL and the date they were consulted. `rules --check-sources` re-fetches every official URL with `urllib`, records status, date and SHA-256 in a TTL cache in `--output-dir`, and reports which sources changed against the baseline; it works offline by reporting the sources as unchecked. A human updates the rules.
- **Untrusted archives.** `.ipa`, `.aab` and `.apk` files are opened as untrusted ZIPs: absolute paths, `..` segments and symlink entries are refused, total and per-entry sizes and compression ratios are capped, extraction goes to a private `tempfile.mkdtemp` directory that is removed afterwards, and nothing from the archive is executed. External tools run through `subprocess` with argument lists, never a shell, and with timeouts.
- **Python 3.10+ standard library.** Official tools are used when present (`codesign`, `security`, `otool`, `plutil` on macOS; `aapt2`, `zipalign`, `apksigner`, `apkanalyzer`, `bundletool`, NDK `llvm-readelf` for Android). When a tool is missing, a pure-Python reader covers what it safely can (plists, Mach-O load commands, binary AXML and the AAB protobuf manifest, ELF program headers, ZIP offsets) and labels the verification method; the rest is `NOT_VERIFIED`.
- **Store APIs are optional and read-only.** App Store Connect and the Google Play Developer API are only consulted when the user supplies credentials; both need signed JWTs that the standard library cannot produce, so without the optional `cryptography` package the related checks stay `NOT_VERIFIED` and a manual checklist is produced. Key files are never opened by `appstore-review-guardian` and key contents are never logged.

## Commands

| Skill | Command | Purpose |
| --- | --- | --- |
| both | `scripts/guardian.py audit --quick <project>` | Main configuration and critical problems only |
| both | `scripts/guardian.py audit --full <project>` | Project, artifacts, dependencies, privacy and policy checks |
| both | `scripts/guardian.py audit --policy <project>` | Policy, privacy and store-declaration checks only |
| both | `scripts/guardian.py gate --release <project>` | Pre-submission gate: `READY` / `NEEDS_REVIEW` / `BLOCKED` |
| both | `scripts/guardian.py rules --check-sources` | Re-check the official sources behind the rules |
| iOS | `scripts/guardian.py audit --ipa <file.ipa>` · `--app <Runner.app>` | Audit an artifact alone |
| iOS | `scripts/guardian.py asc --template` | Template for user-supplied App Store Connect metadata |
| Android | `scripts/guardian.py audit --aab <file.aab>` · `--apk <file.apk>` | Audit an artifact alone |
| Android | `scripts/guardian.py tools` | Which Android SDK tools were found and why others are unusable |

`--json` prints a compact summary for agents and CI; `--output-dir` receives `appstore-audit-report.{md,json}` or `google-play-audit-report.{md,json}`. Each module script also runs on its own and prints JSON. The full option list is in each skill's `SKILL.md`.

## Release gate integration

A pipeline or an SDD REVIEW/release step runs `gate --release` and consumes the exit code and the `--json` summary: `2` stops the release, `1` needs a person to look at the listed findings, `0` lets the release continue with the pending items recorded. The skills only report; corrections and new builds are made by the developer or an authorized agent, followed by a new audit. Details: `google-play-review-guardian/references/sdd-integration.md`.

## Tests

Each skill ships its own `unittest` suite with fixtures built in code (no binary fixtures, no network, external tools mocked as missing): valid and invalid plists, privacy manifests, entitlements, provisioning profiles, Mach-O and ELF headers, AXML and protobuf manifests, signed and debug-signed artifacts, zip-slip, absolute-path, symlink, zip-bomb and corrupt archives, disposable Flutter projects (Groovy and Kotlin DSL), rule-data-driven target-SDK decisions, severity semantics, schema-valid reports and exit codes. CI runs both suites (`.github/workflows/ci.yml`). Their fixtures are hostile on purpose (zip-slip paths, shell metacharacters passed as plain arguments, fake certificates), so each skill's `.skillignore` excludes `tests/` from the Hermes security scan (`skills_guard`); every other file is scanned and must return `SAFE` (`tools/check_skill_security.py --skill skills/release/<skill>`). `.skillignore` is read only by the scanner: `hermes skills install` still copies `tests/`.

```bash
python -m unittest discover -s skills/release/appstore-review-guardian/tests -p 'test_*.py'
python -m unittest discover -s skills/release/google-play-review-guardian/tests -p 'test_*.py'
```

## Installing

```bash
hermes skills install FelipeAngeli/hermes-sdd-orchestrator/skills/release/appstore-review-guardian --yes
hermes skills install FelipeAngeli/hermes-sdd-orchestrator/skills/release/google-play-review-guardian --yes
```

## Known limitations

See each skill's `references/limitations.md`. In short: without macOS `codesign`/`security` the iOS signature checks stay `NOT_VERIFIED`, so the iOS gate cannot reach `READY` on Linux; `google-play-review-guardian` looks for Java in `JAVA_HOME`, a working `java` on `PATH` and Android Studio's bundled JBR, and for `bundletool` in `PATH`, `BUNDLETOOL_JAR` and `<sdk>/bundletool/`; without a working Java `apksigner`, `apkanalyzer` and `bundletool` do not run, so cryptographic signature validity and `bundletool validate` stay `NOT_VERIFIED`; the bundle's JAR signature is checked with the JDK's `jarsigner -verify` (not `-strict`, which rejects every self-signed upload key); the alignment of the APKs Google Play generates from a bundle cannot be observed without `bundletool build-apks`; guideline checks on source code are heuristics capped at `WARNING`; store data the public APIs do not expose (Data safety, App Privacy, content rating) must be supplied by the user or stays `NOT_VERIFIED`.

## File catalogue

### appstore-review-guardian

| File | Role |
| --- | --- |
| `appstore-review-guardian/.skillignore` | Excludes `tests/` (hostile fixtures by design) from the Hermes security scan (`skills_guard`); the files are still installed and CI still runs them. |
| `appstore-review-guardian/SKILL.md` | Hermes entry point: when to use, how to run, procedure, pitfalls, verification. |
| `appstore-review-guardian/examples/appstore-audit-report.json` | Report generated by running the tool on a real read-only audit of the mently app; not hand-written. |
| `appstore-review-guardian/examples/appstore-audit-report.md` | Report generated by running the tool on a real read-only audit of the mently app; not hand-written. |
| `appstore-review-guardian/examples/fixture/appstore-audit-report.json` | Report generated by running the tool on a fixture (synthetic debug-signed .ipa); not hand-written. |
| `appstore-review-guardian/examples/fixture/appstore-audit-report.md` | Report generated by running the tool on a fixture (synthetic debug-signed .ipa); not hand-written. |
| `appstore-review-guardian/references/app-store-connect-checklist.md` | Reference loaded on demand: Checklist do App Store Connect. |
| `appstore-review-guardian/references/apple-review-guidelines.md` | Reference loaded on demand: App Review Guidelines cobertas. |
| `appstore-review-guardian/references/flutter-ios-guidelines.md` | Reference loaded on demand: Projetos Flutter: o que o guardian inspeciona. |
| `appstore-review-guardian/references/ios-release-requirements.md` | Reference loaded on demand: Requisitos técnicos de release iOS. |
| `appstore-review-guardian/references/limitations.md` | Reference loaded on demand: Limitações conhecidas. |
| `appstore-review-guardian/references/privacy-compliance.md` | Reference loaded on demand: Privacidade: privacy manifest, Required Reason API, permissões e ATT. |
| `appstore-review-guardian/references/rejection-catalog.md` | Reference loaded on demand: Catálogo de rejeições comuns → regra → correção. |
| `appstore-review-guardian/references/sources.md` | Reference loaded on demand: Fontes oficiais consultadas. |
| `appstore-review-guardian/rules/privacy-rules.json` | Rule base: 14 rules, each with official URL, consultation date, requirement level and verification method. |
| `appstore-review-guardian/rules/reference-data.json` | Reference data extracted from Apple documentation (required-reason API categories and codes, collected data types, usage-description keys, ASC limits), with source and consultation date. |
| `appstore-review-guardian/rules/review-rules.json` | Rule base: 12 rules, each with official URL, consultation date, requirement level and verification method. |
| `appstore-review-guardian/rules/sources-baseline.json` | SHA-256 baseline of each official source's normalised text; `rules --check-sources` compares against it. |
| `appstore-review-guardian/rules/submission-rules.json` | Rule base: 13 rules, each with official URL, consultation date, requirement level and verification method. |
| `appstore-review-guardian/rules/technical-rules.json` | Rule base: 32 rules, each with official URL, consultation date, requirement level and verification method. |
| `appstore-review-guardian/schemas/audit-report.schema.json` | JSON Schema (draft 2020-12) of the JSON report; tests validate every generated report against it. |
| `appstore-review-guardian/scripts/check_sources.py` | Official-source freshness checker (urllib, offline-safe). |
| `appstore-review-guardian/scripts/common.py` | Shared primitives for appstore-review-guardian. |
| `appstore-review-guardian/scripts/generate_report.py` | Build, validate and render the audit report (JSON + Markdown). |
| `appstore-review-guardian/scripts/guardian.py` | App Store Review Guardian — read-only iOS App Store pre-submission auditor. |
| `appstore-review-guardian/scripts/inspect_asc.py` | App Store Connect checks: user-supplied metadata JSON + pending manual checklist. |
| `appstore-review-guardian/scripts/inspect_dependencies.py` | Dependency inventory for Flutter/CocoaPods projects (read-only, offline). |
| `appstore-review-guardian/scripts/inspect_ipa.py` | Inspect a .ipa (untrusted ZIP) or an on-disk .app bundle. Read-only. |
| `appstore-review-guardian/scripts/inspect_project.py` | Inspect an iOS / Flutter project source tree (read-only, never builds). |
| `appstore-review-guardian/scripts/macho.py` | Minimal, bounded Mach-O reader (stdlib only). |
| `appstore-review-guardian/scripts/validate_entitlements.py` | Entitlements validation (signed bundle, provisioning profile or project file). |
| `appstore-review-guardian/scripts/validate_plist.py` | Info.plist validation (bundle artifact or project source). |
| `appstore-review-guardian/scripts/validate_privacy.py` | Privacy manifest (PrivacyInfo.xcprivacy) validation per TN3181 and Apple docs. |
| `appstore-review-guardian/scripts/validate_signing.py` | Code signing and provisioning profile checks for an on-disk .app bundle. |
| `appstore-review-guardian/tests/fixtures.py` | Programmatic fixtures for appstore-review-guardian tests (no network, no real apps). |
| `appstore-review-guardian/tests/test_guardian.py` | Unit tests for appstore-review-guardian (stdlib unittest, offline, no real apps). |

### google-play-review-guardian

| File | Role |
| --- | --- |
| `google-play-review-guardian/.skillignore` | Excludes `tests/` (hostile fixtures by design) from the Hermes security scan (`skills_guard`); the files are still installed and CI still runs them. |
| `google-play-review-guardian/SKILL.md` | Hermes entry point: when to use, how to run, procedure, pitfalls, verification. |
| `google-play-review-guardian/examples/README.md` | How the example report was produced from the real artifacts and what was redacted. |
| `google-play-review-guardian/examples/google-play-audit-report.json` | Report generated by running the tool on a real read-only audit of the mently app; not hand-written. |
| `google-play-review-guardian/examples/google-play-audit-report.md` | Report generated by running the tool on a real read-only audit of the mently app; not hand-written. |
| `google-play-review-guardian/references/android-release-requirements.md` | Reference loaded on demand: Requisitos técnicos de release Android. |
| `google-play-review-guardian/references/billing-compliance.md` | Reference loaded on demand: Pagamentos e Google Play Billing. |
| `google-play-review-guardian/references/data-safety.md` | Reference loaded on demand: Data safety, User Data e exclusão de conta. |
| `google-play-review-guardian/references/flutter-android-guidelines.md` | Reference loaded on demand: Projetos Flutter — o que o auditor lê (somente leitura). |
| `google-play-review-guardian/references/google-play-policies.md` | Reference loaded on demand: Políticas do Google Play — mapa para a base de regras. |
| `google-play-review-guardian/references/limitations.md` | Reference loaded on demand: Limitações conhecidas. |
| `google-play-review-guardian/references/permissions-policy.md` | Reference loaded on demand: Permissões restritas e sensíveis. |
| `google-play-review-guardian/references/play-console-checklist.md` | Reference loaded on demand: Checklist do Play Console (manual) e formato de `--declarations`. |
| `google-play-review-guardian/references/rejection-catalog.md` | Reference loaded on demand: Catálogo de rejeições comuns e como o auditor as detecta. |
| `google-play-review-guardian/references/sdd-integration.md` | Reference loaded on demand: Integração com Hermes e o orquestrador SDD. |
| `google-play-review-guardian/references/sources.md` | Reference loaded on demand: Registro de fontes oficiais. |
| `google-play-review-guardian/references/testing-requirements.md` | Reference loaded on demand: Teste fechado e acesso à produção. |
| `google-play-review-guardian/rules/policy-rules.json` | Rule base: 26 rules, each with official URL, consultation date, requirement level and verification method. |
| `google-play-review-guardian/rules/privacy-rules.json` | Rule base: 7 rules, each with official URL, consultation date, requirement level and verification method. |
| `google-play-review-guardian/rules/technical-rules.json` | Rule base: 34 rules, each with official URL, consultation date, requirement level and verification method. |
| `google-play-review-guardian/schemas/audit-report.schema.json` | JSON Schema (draft 2020-12) of the JSON report; tests validate every generated report against it. |
| `google-play-review-guardian/scripts/common.py` | Shared primitives for google-play-review-guardian. |
| `google-play-review-guardian/scripts/generate_report.py` | Report generation: google-play-audit-report.{md,json} + compact summary. |
| `google-play-review-guardian/scripts/guardian.py` | google-play-review-guardian CLI — read-only Android / Google Play pre-submission auditor. |
| `google-play-review-guardian/scripts/inspect_aab.py` | Android App Bundle (.aab) inspection. |
| `google-play-review-guardian/scripts/inspect_apk.py` | APK inspection: binary AXML manifest parser + official-tool cross-checks. |
| `google-play-review-guardian/scripts/inspect_dependencies.py` | Dependency inventory (pubspec.lock, AAB dependencies.pb) with SDK categories. |
| `google-play-review-guardian/scripts/inspect_project.py` | Read-only project inspection (Flutter / Kotlin / Java / native Android). |
| `google-play-review-guardian/scripts/validate_manifest.py` | Normalized AndroidManifest model + manifest checks. |
| `google-play-review-guardian/scripts/validate_native_libraries.py` | 16 KB page-size compatibility of native libraries. |
| `google-play-review-guardian/scripts/validate_permissions.py` | Permission audit. A permission is never a violation by mere presence |
| `google-play-review-guardian/scripts/validate_play_console.py` | Play Console checks: production access / closed testing, store listing, |
| `google-play-review-guardian/scripts/validate_privacy.py` | Privacy / Data safety / account deletion / billing heuristics. |
| `google-play-review-guardian/scripts/validate_sdk.py` | Target / min SDK validation driven by rule data (rules/technical-rules.json). |
| `google-play-review-guardian/scripts/validate_signing.py` | Signing checks: debug certificate detection, certificate fingerprints, upload key size. |
| `google-play-review-guardian/tests/fixtures.py` | Programmatic fixture builders (no binary fixtures committed). |
| `google-play-review-guardian/tests/helpers.py` | Shared test helpers: puts scripts/ and tests/ on sys.path, builds the finding factory. |
| `google-play-review-guardian/tests/test_artifacts.py` | Artifact parsing: AAB proto manifest, APK AXML, invalid/malicious archives. |
| `google-play-review-guardian/tests/test_end_to_end.py` | End-to-end: disposable Flutter project, report generation + schema, CLI exit codes, |
| `google-play-review-guardian/tests/test_native_signing_tools.py` | Native libraries (16 KB), signing, external tool failures, SDK discovery. |
| `google-play-review-guardian/tests/test_policy_and_sdk.py` | Permissions, target SDK (rule-data driven), severity semantics, testing requirement. |
