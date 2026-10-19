# Fontes oficiais consultadas

Todas as fontes foram consultadas em **2026-10-16**, com HTTP 200, via `curl -sL -A Mozilla/5.0` e `urllib` (`guardian.py rules --check-sources`). As páginas DocC (`/documentation/...`) foram lidas pelo JSON público `https://developer.apple.com/tutorials/data/documentation/<caminho>.json`, porque o HTML delas é só um shell JS. O hash do conteúdo normalizado de cada URL fica em `rules/sources-baseline.json`. O cache das rechecagens é gravado em `<output-dir>/sources-cache.json` (TTL padrão de 7 dias).

| Fonte | O que foi extraído | Versão/data publicada |
|---|---|---|
| https://developer.apple.com/app-store/review/guidelines/ | Texto de 1.2 (UGC: filtro, denúncia, bloqueio, contato), 1.5, 2.1(a)(b) (completude, conta demo, IAP visível), 2.3.1/2.3.3/2.3.7, 2.5.1/2.5.2/2.5.4, 3.1.1 (IAP obrigatório, restore "should"), 3.1.1(a), 3.1.2(a) (assinatura ≥ 7 dias), 4.5.4, 4.8 (login de terceiros → alternativa equivalente), 5.1.1(i) (política no ASC e no app), 5.1.1(ii), 5.1.1(v) (exclusão de conta no app), 5.1.2(i) (ATT) | "Last Updated: October 16, 2026" |
| https://developer.apple.com/news/upcoming-requirements/ | Desde 9 set 2026: target iOS 13+. Desde 28 abr 2026: Xcode 26+ e SDK iOS 26. Age Rating (31 jan 2026). DSA trader status na UE (17 fev 2025) | itens datados na página |
| https://developer.apple.com/support/third-party-SDK-requirements/ | Lista de 86 SDKs que exigem privacy manifest e assinatura, e a regra de aplicação (app novo ou atualização que adiciona o SDK; assinatura quando a dependência é binária) | sem data |
| https://developer.apple.com/documentation/bundleresources/privacy-manifest-files | Nome `PrivacyInfo.xcprivacy`, chaves de topo, regra de inclusão para SDKs | — |
| https://developer.apple.com/documentation/bundleresources/describing-use-of-required-reason-api (+ `.../nsprivacyaccessedapitypes/nsprivacyaccessedapitype(reasons)`) | 5 categorias e 17 códigos de motivo, com o mapeamento categoria → códigos; lista de APIs por categoria (base das agulhas de símbolos) | obrigatório desde 1 mai 2024 (comunicado Apple; a data é usada só como `effective_from`) |
| https://developer.apple.com/documentation/bundleresources/describing-data-use-in-privacy-manifests (+ `nsprivacycollecteddatatype`, `nsprivacycollecteddatatypepurposes`) | 35 tipos de dados, 6 finalidades, chaves Linked/Tracking/Purposes | — |
| https://developer.apple.com/documentation/technotes/tn3181-debugging-invalid-privacy-manifest | Desde 12 nov 2024 o manifest precisa ser válido; tipos de NSPrivacyTracking/TrackingDomains; formato de domínio; tracking=true com lista vazia é inválido | — |
| https://developer.apple.com/documentation/bundleresources/protected-resources | 56 chaves `*UsageDescription` | — |
| https://developer.apple.com/documentation/bundleresources/information-property-list (+ `cfbundleshortversionstring`, `cfbundleversion`, `minimumosversion`, `uibackgroundmodes`, `nsallowsarbitraryloads`, `itsappusesnonexemptencryption`) | Formatos de versão; não definir MinimumOSVersion à mão; 14 valores de background mode; ATS exige justificativa no review; sem a chave de criptografia o ASC pergunta a cada upload | — |
| https://developer.apple.com/documentation/bundleresources/entitlements/aps-environment | development/production definidos pelo perfil | — |
| https://developer.apple.com/documentation/technotes/tn3125-inside-code-signing-provisioning-profiles | Perfil App Store não tem ProvisionedDevices; ProvisionsAllDevices = Developer ID/Enterprise; entitlements precisam estar na allowlist do perfil; get-task-allow precisa ser autorizado pelo perfil | — |
| https://developer.apple.com/documentation/xcode/distributing-your-app-for-beta-testing-and-releases | Fluxo archive → distribuição; assinatura gerenciada | — |
| https://developer.apple.com/documentation/security | Referência geral (assinatura/codesign) | — |
| https://developer.apple.com/support/offering-account-deletion-in-your-app/ | Desativar não basta; link direto quando a exclusão termina na web; não exigir telefone/e-mail fora de setores regulados | — |
| https://developer.apple.com/app-store/app-privacy-details/ | Critérios de divulgação no ASC (só para a checklist manual) | — |
| https://developer.apple.com/help/app-store-connect/ (+ `reference/app-information/app-information`, `.../platform-version-information`, `manage-app-information/overview-of-export-compliance`, `manage-builds/upload-builds`) | Nome 2–30, subtítulo ≤ 30, keywords ≤ 100 bytes e > 2 caracteres cada, descrição ≤ 4000 sem HTML, texto promocional ≤ 170, Privacy Policy URL obrigatória no iOS; export compliance; uso de JWT na API | — |
| https://docs.flutter.dev/deployment/ios | Referência de `flutter build ipa` (fora da Apple; usada só em recomendações) | — |

## O que NÃO foi transformado em requisito

- Fatias de simulador em frameworks, `.dSYM` dentro do bundle e coerência de versão das extensões não têm página Apple consultada que as defina como motivo de rejeição. Viraram regras `recommended` (WARNING no máximo).
- `ITSAppUsesNonExemptEncryption` é opcional: a Apple diz que incluir a chave "streamlines the submission". Por isso é `recommended`.
- O mapeamento plugin → permissão (`resource_signals`) e os sinais de código (`code_signals`) são heurísticas do guardian, não regras da Apple.
- Os aliases de reempacotamento de SDK (`sdk_repackaging_aliases`, `federated_facades`) são inferências do guardian.

## Revalidação

```
python3 scripts/guardian.py rules --check-sources --output-dir <dir> [--force] [--offline] [--ttl-days 7]
```

Retorna 0 quando todas as fontes respondem e nenhum hash mudou. Retorna 1 quando alguma fonte mudou (`changed_since_baseline`) ou falhou. Sem rede, as fontes ficam como `offline` e nada é inventado. Quando houver mudança, releia a página com `web_extract`, atualize as regras afetadas (`consulted_at`) e regenere `rules/sources-baseline.json`.
