# Observability and behavioral testing

## Observability

Instrument decisions and outcomes, not raw payloads. Prefer stable operation names and bounded labels. Use the logging, metrics and tracing libraries the project already uses.

- Logs: correlation identity, safe resource identity, decision/outcome and error class.
- Metrics: rate, errors, duration and saturation for user-visible or shared-resource paths.
- Traces: process/network boundaries and material retries, with safe attributes.

Do not emit secrets, tokens, complete request bodies, personal data or unbounded identifiers. Redaction is a fallback, not permission to log sensitive content.

## Debugging evidence

When a slice fixes or investigates a defect, record evidence another engineer can reproduce:

- the exact input, configuration and version that reproduce the failure, with sensitive values removed;
- the failing command or request and its observed output before the fix;
- the log lines, metric or trace span that locate the failure, by correlation identity;
- the same command passing after the fix.

A hypothesis without a reproduction is reported as unconfirmed, not as the cause.

## Choosing the test layer

Choose the lowest layer that can fail for the acceptance invariant, then add a higher layer only for what the lower one cannot observe:

| Layer | Choose it when | It cannot prove |
| --- | --- | --- |
| Unit | The rule is pure domain/application logic with inputs and outputs under test control. | Wiring, serialization, real I/O behavior. |
| Component | A handler or service must run with its real collaborators except an explicit I/O boundary. | Behavior of the real database, broker or provider. |
| Integration | Correctness depends on serialization, dependency injection, a real database/cache/queue, migrations, transactions or an adapter's wire format. Prefer the project's disposable test infrastructure. | The complete user outcome across deployed services. |
| End-to-end | A critical user outcome crosses several deployed components and is not proven below. Keep these few and stable. | Fine-grained branches; use lower layers for them. |

Mocks prove caller decisions, not integration compatibility. A green fixture built from the same model under test is not independent contract evidence. A test that needs a shared or external environment requires authorization; otherwise record the gap.

## Failure cases

For changed backend paths, consider malformed input, unauthorized target, not found versus forbidden disclosure, dependency timeout, duplicate request, partial effect and concurrency conflict.

## Verification

Each PASS cites the project-owned command and observable result. Reuse valid evidence for unchanged inputs; expand only for an unresolved risk or failure.
