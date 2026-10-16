# Limitações conhecidas

- **Não é o App Review.** READY só quer dizer que as verificações executadas não acharam bloqueio. Não é garantia nem probabilidade de aprovação.
- **Sem macOS:** sem `codesign`/`security`, a verificação criptográfica da assinatura e do perfil (CMS) fica NOT_VERIFIED. Entitlements e a plist do perfil continuam sendo lidos por stdlib. A autoridade do certificado é inferida por strings, com confiança menor.
- **App Store Connect API:** não implementada. O JWT ES256 exige biblioteca criptográfica fora da stdlib, então os dados do ASC vêm de um JSON preenchido pelo usuário ou ficam como checklist manual.
- **Heurísticas de código** (4.8, 5.1.1(v), 1.2, 3.1.1, placeholders, política de privacidade): usam regex em `lib/`. Podem errar para mais ou para menos, nunca bloqueiam e sempre pedem confirmação humana. Login feito pelo backend/broker OAuth só é detectado quando o código usa os padrões conhecidos.
- **Required Reason API:** busca de símbolos exatos nos binários. Não detecta chamadas dinâmicas e não prova uso em runtime. Símbolos Swift mangled são cobertos só parcialmente.
- **Novo app × atualização:** a obrigação de manifest para SDKs listados depende de o app ser novo ou de a atualização adicionar o SDK. O guardian não sabe disso, então emite WARNING.
- **Builds `--no-codesign`:** servem para pré-auditoria (Info.plist, binário, manifests, frameworks). Assinatura, perfil e entitlements finais só são verificáveis no `.ipa` exportado.
- **Universal Links:** o AASA só é consultado com `--network`. A validação é estrutural (`applinks` presente) e não confere o appID.
- **pbxproj:** o parser é por regex (formato OpenStep). Projetos muito customizados (vários targets de app, xcconfig com `#include`) podem não resolver a configuração Release, e o resultado vira NOT_VERIFIED.
- **Regras:** a base de regras é estática (consultada em 2026-10-16). `rules --check-sources` detecta mudança de conteúdo por hash, mas não atualiza as regras sozinho. Mudanças precisam ser revisadas.
- **Hash de HTML:** páginas da Apple podem mudar elementos sem relevância e gerar falsos "changed". Nesse caso, revise e regenere a baseline.
