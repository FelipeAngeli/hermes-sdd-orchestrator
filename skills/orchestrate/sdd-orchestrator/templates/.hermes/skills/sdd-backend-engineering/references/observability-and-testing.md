# Observability and behavioral testing

## Observability

Instrument decisions and outcomes, not raw payloads. Prefer stable operation names and bounded labels.

- Logs: correlation identity, safe resource identity, decision/outcome and error class.
- Metrics: rate, errors, duration and saturation for user-visible or shared-resource paths.
- Traces: process/network boundaries and material retries, with safe attributes.

Do not emit secrets, tokens, complete request bodies, personal data or unbounded identifiers. Redaction is a fallback, not permission to log sensitive content.

## Test ownership

Choose the lowest layer that can observe the acceptance invariant:

- unit: pure domain/application rule;
- component: handler/service with controlled I/O boundary;
- integration: serialization, DI, database, queue or external adapter wiring;
- E2E: critical user outcome not proven below.

Mocks prove caller decisions, not integration compatibility. A green fixture built from the same model under test is not independent contract evidence.

## Failure cases

For changed backend paths, consider malformed input, unauthorized target, not found versus forbidden disclosure, dependency timeout, duplicate request, partial effect and concurrency conflict.

## Verification

Each PASS cites the project-owned command and observable result. Reuse valid evidence for unchanged inputs; expand only for an unresolved risk or failure.
