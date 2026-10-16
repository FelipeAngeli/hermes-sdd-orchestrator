# Checklist do App Store Connect

Por padrão, o guardian **não** acessa o App Store Connect. A API exige um JWT ES256 assinado com a chave `.p8`, e a stdlib do Python não tem ECDSA. Com `ASC_KEY_ID`, `ASC_ISSUER_ID` e `ASC_KEY_PATH` definidas, o guardian só registra que as credenciais existem (`ASC-API-001` = NOT_VERIFIED) e **nunca** abre nem registra a chave.

## Modo semiautomático (metadados fornecidos)

```
python3 scripts/guardian.py asc --template > asc.json   # preencha a partir do ASC
python3 scripts/guardian.py gate --release <app> --ipa <ipa> --asc-metadata asc.json
```

Não coloque senhas no arquivo. Para a conta demo, informe apenas `"demo_account": {"provided": true}`.

| Campo | Regra | Limite verificado |
|---|---|---|
| name | ASC-META-001 | 2–30 caracteres |
| subtitle | ASC-META-002 | ≤ 30 caracteres |
| keywords | ASC-META-003 | obrigatório, ≤ 100 bytes, cada uma com > 2 caracteres |
| description / promotional_text | ASC-META-004 | ≤ 4000 sem HTML / ≤ 170 |
| privacy_policy_url | ASC-META-005 | obrigatório no iOS (BLOCKER se ausente) |
| support_url | ASC-META-006 | obrigatório |
| review_notes + demo_account | ASC-REVIEW-001, REV-DEMO-001 | notas específicas; credenciais quando há login |

## Pendências manuais (sempre listadas até serem declaradas)

- [ ] App Privacy Details preenchido, incluindo os SDKs (ASC-PRIV-001)
- [ ] Novo questionário de Age Rating respondido (ASC-AGE-001)
- [ ] Screenshots/previews mostram o app em uso (ASC-MEDIA-001)
- [ ] Export compliance respondido ou declarado no Info.plist (ASC-EXPORT-001)
- [ ] Países/territórios revisados; trader status (DSA) verificado na UE (ASC-AVAIL-001)
- [ ] IAPs/assinaturas anexados à versão e visíveis ao revisor (REV-IAP-003)
- [ ] Categoria, ícone 1024×1024, informações de contato do revisor

Valores declarados pelo usuário (`true`) contam como PASS com `verification_method: manual` e confiança 0.5. A responsabilidade continua com quem declarou.
