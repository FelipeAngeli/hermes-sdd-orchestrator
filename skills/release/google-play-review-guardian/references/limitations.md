# Limitações conhecidas

## Ambiente
- **Java**: procurado em `JAVA_HOME`, `java` no `PATH` (só se executar de fato; o stub `/usr/bin/java` do macOS sem JRE é ignorado) e no JBR do Android Studio. Sem nenhum Java funcional, `apksigner`, `apkanalyzer` e `bundletool` não rodam: `TECH-SIGNATURE-VERIFY` e `TECH-BUNDLETOOL-VALIDATE` ficam `NOT_VERIFIED` e os certificados são só lidos em Python. Ferramentas Java recebem um ambiente mínimo (`JAVA_HOME`, `PATH`, `HOME`, `LANG`, `TMPDIR`).
- A assinatura do `.aab` usa `jarsigner -verify` do JDK, **sem** `-strict`: o `-strict` reprova todo bundle do Gradle (chave de upload autoassinada sem cadeia PKIX e `META-INF` fora do início do ZIP), o que o Google Play não exige. `PASS` exige a linha `jar verified.`; um JRE sem `jarsigner` mantém `NOT_VERIFIED`.
- Sem Android SDK: só parsers Python (AXML, protobuf, ELF, ZIP). Cada finding informa o método (`python-elf`, `python-elf + llvm-readelf`, `aapt2`, …).
- `bundletool` é procurado no `PATH`, em `BUNDLETOOL_JAR` e em `<sdk>/bundletool/bundletool*.jar` (o mais novo); outros locais não são varridos.

## Cobertura técnica
- O alinhamento ZIP dos APKs que o Google Play gera a partir do `.aab` não é observável no bundle; requer `bundletool build-apks` (Java) ou o aviso do App bundle explorer.
- O parser de Gradle é baseado em expressões regulares (Groovy e KTS comuns); scripts com lógica dinâmica, version catalogs ou convenções em `buildSrc` podem não ser resolvidos → valor `None` e `NOT_VERIFIED`/heurístico.
- `flutter.targetSdkVersion` é resolvido lendo `FlutterExtension.kt` do Flutter SDK local; o valor efetivo é sempre o do artefato.
- A origem das permissões injetadas por plugins é apenas uma dica; o *manifest merge blame* não é lido.
- `dependencies.pb` do bundle é decodificado pelo formato observado (inventário apenas).
- Sem análise de bytecode (DEX) nem de código nativo além dos cabeçalhos ELF.
- A checagem de segredos é por padrões conhecidos (Google API key, AWS, Stripe live, PEM, Slack, GitHub); não é um scanner completo nem consulta bases de vulnerabilidades (CVE). Não há verificação automática de "bibliotecas abandonadas".
- Exceções de target API por categoria (ex.: apps privados) e extensões concedidas dependem de informação do usuário (`--extension-granted`).

## Políticas
- Conteúdo (UGC, IP, malware, abuso, Families, funcionalidade mínima, metadados enganosos) exige revisão humana.
- Data safety e demais declarações do Console não são legíveis pela API pública; ficam `NOT_VERIFIED` até o usuário fornecer/confirmar.
- `READY` = nenhum BLOCKER/CRITICAL/WARNING, nenhuma verificação obrigatória pendente e ao menos um `PASS` determinístico **na cobertura executada**. Não é aprovação do Google.
- `PASS` exige verificação determinística. Inferências por nome/versão/regex (ex.: AGP ≥ 8.5.1, NDK r28+, `flutter.targetSdkVersion` resolvido do SDK local) e dados informados pelo usuário (ex.: testadores do teste fechado) são rebaixados centralmente em `Finding`: `INFO` "confirmar" em regras recomendadas, `NOT_VERIFIED` em regras obrigatórias.

## Testes funcionais
- A camada de testes em dispositivo/emulador é apenas orientativa (`TECH-FUNCTIONAL-TESTS`); o auditor não instala nem executa o app.

## Segurança da execução
- Git: chamadas só via `common.run_git` (argv com `-c core.fsmonitor=false -c core.hooksPath=/dev/null -c core.untrackedCache=false -c protocol.allow=never`, `--` antes de caminhos, ambiente mínimo com `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_TERMINAL_PROMPT=0`, `GIT_OPTIONAL_LOCKS=0`, timeout e saída limitada). `rev-parse`/`ls-files`/`check-ignore` não acionam `diff.external`, `core.sshCommand`, pager nem filtros. Sem `git` no PATH, o status no Git fica `NOT_VERIFIED`.
- ZIP: além do limite por entrada, há limite de razão agregada, total declarado e um orçamento de bytes realmente descomprimidos por arquivo (compartilhado entre o CRC e todas as leituras/extrações dos módulos). Estourar o orçamento gera `SEC-ARCHIVE-SAFETY` e interrompe a leitura.
- Saída: diretório padrão novo e privado (0700); arquivos 0600. Diretórios já existentes passados em `--output-dir` não têm as permissões alteradas.

## Rede
- A auditoria não usa rede. Só `rules --check-sources` (re-fetch das URLs oficiais) e o modo API opcional fazem requisições.
