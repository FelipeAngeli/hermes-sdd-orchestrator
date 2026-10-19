# Permissões restritas e sensíveis

Regra central: **nenhuma permissão é violação por mera presença**. Para cada permissão o relatório registra onde foi declarada (manifest mesclado do artefato ou manifest do projeto), a origem provável (dica heurística de biblioteca), a regra relacionada e a ação (declaração no Console, divulgação destacada, remoção com `tools:node="remove"`).

| Permissão / API | Regra | Severidade padrão | Ação recomendada |
|---|---|---|---|
| READ_SMS, SEND_SMS, RECEIVE_SMS, RECEIVE_MMS, RECEIVE_WAP_PUSH, WRITE_SMS, READ_CALL_LOG, WRITE_CALL_LOG, PROCESS_OUTGOING_CALLS | POL-SMS-CALL-LOG | WARNING | Só funcionalidade principal permitida + Permissions Declaration Form; alternativas (SMS Retriever, Digital Credentials). |
| ACCESS_BACKGROUND_LOCATION | POL-BACKGROUND-LOCATION | WARNING | Declaração, vídeo, divulgação destacada, política de privacidade. |
| ACCESS_FINE/COARSE_LOCATION | POL-LOCATION | INFO | Preferir aproximada; mudança de 2027-01-27 (botão de localização). |
| MANAGE_EXTERNAL_STORAGE (target ≥ 30) | POL-ALL-FILES-ACCESS | WARNING | Usos permitidos limitados; preferir SAF/MediaStore. |
| QUERY_ALL_PACKAGES (target ≥ 30) | POL-QUERY-ALL-PACKAGES | WARNING | Usar `<queries>` específicos. |
| READ_MEDIA_IMAGES / READ_MEDIA_VIDEO (target ≥ 33) | POL-PHOTO-VIDEO | WARNING | Photo Picker; declaração se indispensável. |
| USE_EXACT_ALARM | POL-EXACT-ALARM | WARNING | Só apps de alarme/timer/calendário; senão SCHEDULE_EXACT_ALARM. |
| SCHEDULE_EXACT_ALARM | POL-EXACT-ALARM | INFO | Checar `canScheduleExactAlarms()`. |
| `<service android:foregroundServiceType>` | POL-FOREGROUND-SERVICE | WARNING (declaração) / BLOCKER se faltar `FOREGROUND_SERVICE_<TIPO>` com target ≥ 34 no artefato | Declarar cada tipo em App content (descrição, impacto, vídeo). |
| REQUEST_INSTALL_PACKAGES | POL-REQUEST-INSTALL-PACKAGES | WARNING | Não usar para autoatualização. |
| Serviço com BIND_ACCESSIBILITY_SERVICE | POL-ACCESSIBILITY-API | WARNING | Declaração; `isAccessibilityTool` só para ferramentas de acessibilidade. |
| Serviço com BIND_VPN_SERVICE | POL-VPN-SERVICE | WARNING | Caso de uso permitido + declaração. |
| CAMERA, RECORD_AUDIO, CONTACTS, CALENDAR, BODY_SENSORS, POST_NOTIFICATIONS, … | POL-SENSITIVE-PERMISSIONS | INFO | Pedir em contexto; justificar no store listing. |
| com.google.android.gms.permission.AD_ID | PRIV-ADVERTISING-ID | WARNING | Declarar uso do Advertising ID. |

Declarações já feitas podem ser informadas em `--declarations` (`"permissions_declared": ["android.permission.ACCESS_BACKGROUND_LOCATION"]`, `"fgs_declared": true`), o que rebaixa o finding para INFO (sem verificar o Console).

Permissões injetadas por plugins Flutter (ex.: OneSignal adiciona POST_NOTIFICATIONS, RECEIVE_BOOT_COMPLETED, badges de launchers) só aparecem no manifest mesclado: audite o `.aab`. Para descobrir a origem exata, consulte `build/app/intermediates/manifest_merge_blame_file/` ou o *manifest-merger report* gerado pelo build.
