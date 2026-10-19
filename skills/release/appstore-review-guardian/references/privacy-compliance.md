# Privacidade: privacy manifest, Required Reason API, permissões e ATT

## Privacy manifest (`PrivacyInfo.xcprivacy`)

Fontes: privacy-manifest-files, TN3181 e describing-data-use-in-privacy-manifests (consultadas em 2026-10-16).

Chaves de topo válidas: `NSPrivacyTracking` (Boolean), `NSPrivacyTrackingDomains` (array de strings), `NSPrivacyCollectedDataTypes` (array de dicionários) e `NSPrivacyAccessedAPITypes` (array de dicionários).

O manifest é inválido (BLOCKER, `PRIV-MANIFEST-002`/`PRIV-TRACK-001`/`PRIV-RRAPI-001`/`PRIV-DATA-001`) quando:
- a plist está mal formada ou tem chave de topo desconhecida;
- `NSPrivacyTracking` não é Boolean, ou `NSPrivacyTrackingDomains` não é array de strings;
- um domínio tem caminho, query ou barra final;
- `NSPrivacyTracking=true` vem com lista de domínios vazia;
- a categoria de API é desconhecida, a lista de motivos está vazia ou o motivo não pertence àquela categoria;
- o tipo de dado ou a finalidade é desconhecido, ou `...Linked`/`...Tracking` não é Boolean.

Desde 12 nov 2024, manifests inválidos geram e-mail de rejeição do App Store Connect (TN3181).

## Categorias e motivos aprovados (Required Reason API)

| Categoria | Motivos |
|---|---|
| NSPrivacyAccessedAPICategoryFileTimestamp | DDA9.1, C617.1, 3B52.1, 0A2A.1 |
| NSPrivacyAccessedAPICategorySystemBootTime | 35F9.1, 8FFB.1, 3D61.1 |
| NSPrivacyAccessedAPICategoryDiskSpace | 85F4.1, E174.1, 7D9E.1, B728.1 |
| NSPrivacyAccessedAPICategoryActiveKeyboards | 3EC4.1, 54BD.1 |
| NSPrivacyAccessedAPICategoryUserDefaults | CA92.1, 1C8F.1, C56D.1, AC6B.1 |

A detecção de uso (`PRIV-RRAPI-002`) procura símbolos exatos nos binários do bundle, como `_stat`, `_mach_absolute_time`, `_statfs`, `_OBJC_CLASS_$_NSUserDefaults` e `systemUptime`. Encontrar o símbolo prova referência, não uso em runtime; por isso o achado é heurístico. Antes de declarar, confira qual componente usa a API: um SDK com manifest próprio já cobre o próprio uso.

## SDKs de terceiros

A lista oficial (86 nomes, em `rules/reference-data.json`) exige privacy manifest em app novo ou quando a atualização adiciona o SDK. SDKs binários também precisam de assinatura. O guardian não sabe se o build é de app novo ou se o SDK já existia, então SDK listado sem manifest gera WARNING (`PRIV-SDK-001`). Fachadas federadas (`url_launcher`, `path_provider`, `sqflite`…) ficam NOT_APPLICABLE; quem é avaliada é a implementação iOS (`url_launcher_ios`, `path_provider_foundation`, `sqflite_darwin`).

## Permissões (`*UsageDescription`)

- Só são aceitas chaves da lista "Protected resources" (56 chaves). Uma chave desconhecida gera `PRIV-USAGE-004`.
- Texto vazio = CRITICAL. Texto genérico ("Allow this app to access your photos") = WARNING (5.1.1(ii)).
- Permissão sem uso aparente e uso sem permissão (`PRIV-USAGE-003`) são heurísticas baseadas em plugins e código. Ter o plugin no projeto não prova acesso.
- Localize os textos em `InfoPlist.strings` quando o app tiver vários idiomas.

## ATT

Rastrear exige permissão via ATT (5.1.2(i)). `NSPrivacyTracking=true` no manifest do app ou `AppTrackingTransparency.framework` vinculado sem `NSUserTrackingUsageDescription` = CRITICAL (determinístico no bundle).

## Política de privacidade e App Privacy Details

- 5.1.1(i): o link é obrigatório no App Store Connect e dentro do app. O guardian procura URL/tela no código (heurística) e valida o campo do ASC quando os metadados são fornecidos.
- App Privacy Details (nutrition label) fica sempre como pendência manual (`ASC-PRIV-001`). Declare também os dados coletados pelos SDKs (ex.: OneSignal, Sentry).
