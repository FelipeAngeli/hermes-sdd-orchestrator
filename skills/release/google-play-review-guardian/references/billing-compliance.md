# Pagamentos e Google Play Billing

Fonte: Payments policy (answer/9858738) e Play Billing Library deprecation FAQ, consultadas 2026-10-16.

## Quando o Play Billing é obrigatório

Compras de funcionalidades ou conteúdo digital dentro do app: itens virtuais, assinaturas (fitness, educação, música, vídeo, upgrades), versão sem anúncios / recursos premium, serviços de nuvem e software.

## Quando NÃO deve ser usado (exceções oficiais)

- bens físicos (mercado, roupas, eletrônicos);
- serviços físicos/presenciais (transporte, limpeza, passagens, academia, delivery, ingressos);
- pagamento de fatura de cartão ou contas de consumo;
- pagamentos P2P, leilões online, doações isentas de impostos;
- jogos de azar com dinheiro real conforme a política específica;
- programas de faturamento alternativo / ofertas externas em países elegíveis, com inscrição.

## Como o auditor classifica

| Evidência | Finding |
|---|---|
| `base/root/billing.properties` com versão < mínimo vigente (dados em TECH-BILLING-LIBRARY-VERSION) | BLOCKER (determinístico) |
| versão ≥ mínimo | PASS (+ INFO sobre o próximo prazo) |
| Play Billing presente (dependência, permissão `com.android.vending.BILLING`, versão) | NOT_VERIFIED: confirmar que todo conteúdo digital usa Play Billing, preço, renovação, trial e cancelamento claros |
| SDK de pagamento externo (Stripe etc.) | WARNING heurístico: classificar cada fluxo (físico vs. digital); **não** é violação automática |
| nenhum sinal e código analisado | NOT_APPLICABLE (heurístico) |
| sem código analisado | NOT_VERIFIED |

Prazos da Billing Library (tabela oficial): v7 → novos apps/updates até 2026-08-31 (extensão 2026-11-01); v8 → até 2027-08-31 (extensão 2027-11-01). Logo, desde 2026-08-31 exige-se v8+.
