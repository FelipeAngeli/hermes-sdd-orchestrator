# Fundamentals audit and hardening order

Read-only diagnosis of five backend fundamentals, then the order in which to fix them. Use it before hardening a project, when taking over a project nobody reviewed (including AI-written code), or periodically to detect regressions.

## The five fundamentals

| Order | Fundamental | Reference | Why this position |
| --- | --- | --- | --- |
| 1 | Integration test suite | `integration-test-suite.md` | Builds the safety net every later change relies on. |
| 2 | Never trust the client | `never-trust-the-client.md` | Closes data leaks and exposed keys. |
| 3 | Race conditions | `race-conditions.md` | Fixes read-decide-write flows under concurrency. |
| 4 | Webhook and side-effect idempotency | `webhook-idempotency.md` | Protects payments and webhooks; raise it when money or scheduling is involved. |
| 5 | Separation of concerns | `separation-of-concerns.md` | Refactors only once tests pin the behavior. |

Change the default order only for a critical risk found by the audit, such as an exposed secret.

## Audit rules (read-only)

- Change no project file, run no migration, install nothing and commit nothing. The only write is the audit report at the path the slice or the project names.
- Read code and run read-only commands only: listing, searching, `git log`, and `SELECT` against a **local or test** database.
- Mark anything you could not check as **not verified**, with the reason. Never guess.
- Never print a whole secret: show its prefix and last four characters at most.

## Audit procedure

1. **Context.** Stack, database, test framework, external integrations, webhooks, and whether data belongs to several users or tenants.
2. **Integration tests.** Do they exist? Which critical flows (sign-up, login, checkout, webhooks, permissions) do they cover? Is there a guard against a production database?
3. **Never trust the client.** Rules enforced only in the client; client-sent values used without server recomputation; secrets in code, Git history or behind a public bundle prefix; tables without row-level security or with permissive policies; queries not filtered by the authenticated principal.
4. **Race conditions.** Read-decide-write flows without a constraint, conditional update or row lock.
5. **Idempotency.** Webhooks without signature verification or a processed-event record; outgoing charges without an idempotency key.
6. **Separation of concerns.** Files mixing presentation, business rules and data access; largest files and duplications.
7. **Report.** Top five risks first, by impact; then one table per fundamental with finding, risk (high/medium/low), `path:line`, suggested fix and status (confirmed/not verified); end with the reference to apply first and why.

## Hardening procedure

1. Run the audit above; report any exposed secret immediately so the owner can rotate it.
2. Confirm with the requester which fundamentals apply and in which order; some may not (no webhooks: idempotency covers only outgoing charges, or is skipped).
3. One fundamental per slice or change; branch, commit and pull-request operations belong to the project owner under the project's branch rules, never to a stage worker. Present the report or plan and wait for approval before changing code; run the full suite at the end and continue only when green.
4. Record progress where the project keeps it (the SDD ticket, or a hardening log the project names): fundamental, date, branch, what changed, open items.
5. Close with completed fundamentals, open items that depend on the owner (keys to rotate, bugs noted) and the command that runs the tests.

## Pitfalls

- Starting with the refactor: without tests it silently breaks behavior.
- Mixing fixes for different fundamentals in one change.
- Accepting a generated plan without reading it.
- Deleting, skipping or loosening tests to make them pass; using a database with production data; running a new migration that the owner has not reviewed in full.

## Verification

- No project file other than the report changed during the audit (`git status`).
- Every finding has `path:line` or is marked not verified.
- Every completed fundamental has its branch, an approved plan and a green suite.
