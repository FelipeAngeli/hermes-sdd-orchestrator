# Errors, retries and background jobs

## Error ownership

- Domain/application code returns typed failures that preserve cause and relevant safe context.
- Transport boundaries map failures to the public envelope once.
- Log or return an error at one owning boundary; avoid duplicate reports.
- Never expose stack traces, credentials, personal data or internal storage details.

## Timeout and cancellation

Propagate the caller's cancellation through every I/O boundary. Set timeouts from project configuration or client policy, not scattered literals. Detached work needs a named owner, independent lifetime and shutdown path.

## Retry gate

Retry only when all are true:

1. the failure is classified transient;
2. the operation is idempotent or protected by a key;
3. attempts, delay and total deadline are bounded;
4. overload signals are respected;
5. final failure remains observable.

## Jobs

A durable job defines payload version, deduplication, lease/visibility timeout, retry policy, poison-message handling and completion evidence. Handlers assume at-least-once delivery unless the actual platform proves otherwise. A job that updates several systems records how partial completion resumes or compensates.

## Verification

Test timeout, cancellation, duplicate delivery, partial effect and exhausted retry behavior at the boundary that owns each guarantee.
