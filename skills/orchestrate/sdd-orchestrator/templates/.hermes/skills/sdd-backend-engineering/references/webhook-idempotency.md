# Webhook and side-effect idempotency

An idempotent operation gives the same result however many times it runs: **if it was already processed, do not process it again**. Gateways resend a webhook when they do not get a 2xx in time (slow server, deploy, outage), sometimes ten times or more; without idempotency each resend grants the plan, sends the email and calls paid APIs again. Use for incoming webhooks (payment, subscription, invoice, messaging), outgoing charges or retried external calls, and reports of duplicated emails, plans or charges. General key rules: `api-and-idempotency.md`.

## Quick reference

New, additive table (existing tables are not changed):

```sql
CREATE TABLE processed_events (
  event_id     TEXT PRIMARY KEY,
  source       TEXT NOT NULL,          -- e.g. 'stripe'
  processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Webhook flow:

1. Verify the provider's signature (HMAC/secret) **over the raw request body**, before any parsing that changes the JSON. Invalid → 401, nothing is written.
2. Open a transaction → `INSERT INTO processed_events (event_id, source) VALUES (...)`.
3. Key conflict (event already seen) → answer the success status the provider expects (200/201) and stop.
4. Inserted → process and commit. If processing fails, roll back so the next resend can retry.

## Procedure

1. **Map** every webhook endpoint and every operation with a side effect: charging, granting a plan or access, crediting a balance, transactional email/SMS/messaging, LLM or other paid API calls.
2. **Identify each provider's event identifier** (payload field or header). When none exists, propose a stable derived key (for example a hash of type + resource identifier + status) and explain its risk.
3. **Check the signature.** A webhook that does not verify it before anything else is a critical plan item.
4. **Plan** the diff: processed-events table, handler change, response for a repeated event. Get approval before changing code.
5. **Do not assume ordering** ("refunded" may arrive before "approved"). When state matters, read the current state from the provider's API before deciding.
6. **Answer quickly.** A webhook replies within seconds; when the project already has a queue or job system, record the event and move heavy work there. Do not introduce a queue only for this.
7. **Outgoing idempotency keys.** For each business operation that calls the gateway (create charge, refund), generate a key (UUID v4) once, store it with the operation and **reuse it on every retry**. Check how the provider receives it in its documentation.
8. **Own endpoints.** Endpoints that charge or create orders accept an `Idempotency-Key` header and return the same response for a repeated key (store key + response under a unique key).
9. **Effects that cannot live inside the transaction** (email, external API): record the event and the result first and fire the effect afterwards, or mark each effect as done, so reprocessing never repeats it.
10. **Test** with integration tests: the same event twice in sequence delivers once and emails once; the same event twice simultaneously, likewise; an invalid signature gives 401 and processes nothing.

## Pitfalls

- An in-memory `if (alreadyProcessed)` or global variable does not work: requests share no memory and state vanishes on restart. Use the database.
- Checking "exists?" then inserting is a race condition; let the database's unique key decide.
- Never answer 4xx/5xx to a repeated event: the provider keeps resending.
- Do not add Redis only for this; the project's database solves it.
- Do not change prices, amounts or charging rules in this change.
- Never use personal data (email, tax identifier) as an idempotency key.
- A new key on every retry protects nothing.

## Verification

- Every webhook verifies the signature before processing.
- Every webhook records the event identifier under a unique key and answers success to repeats.
- Every outgoing charge sends an idempotency key to the provider.
- Repeated-event tests (sequential and simultaneous) pass.
