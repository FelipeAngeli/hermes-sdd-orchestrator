# Integration test suite for critical flows

Tests that call the real routes against a real, local database and check the outcome. They catch the new feature that broke an old one before a user does. Use when the project has no tests or only fully mocked unit tests, before any refactor, or when a change "broke something by itself". `observability-and-testing.md` decides the layer for a single acceptance check; this reference builds the suite.

## Quick reference

| Type | Exercises | Database |
| --- | --- | --- |
| Unit | One isolated function | Mock |
| Integration (this reference) | Whole route: parameters → rule → database → response | Real, local |
| End-to-end | User interface interactions | Real |

- Simulate external services (payment gateway, email, LLM providers) at the edge; run everything else for real.
- The test database is the same engine and major version as production, run locally or in disposable infrastructure (for example a container).

## Procedure

1. **Detect the stack.** Language, framework, ORM, database and the existing test framework. Without one, propose the stack's standard and get approval; the install runs only inside an IMPLEMENT slice that owns the manifest and lockfile (`sdd-tech-lead`, `references/dependencies.md`).
2. **List critical flows.** Sign-up, login, checkout/payment, webhooks, scheduling, stock, and every route that moves money or touches another user's data. Present the list.
3. **Write the sentences first.** One test name per expected behavior, covering five categories:
   - happy path;
   - expected errors (invalid data, declined payment);
   - repetition (double submit, duplicated webhook, simultaneous requests);
   - limits (zero stock, expired plan, zero or negative amount);
   - cross-user access (user B reading or changing user A's data).

   ```
   describe("payment webhook")
     it("delivers the plan once when the same event arrives twice")
     it("delivers the plan once when both copies arrive at the same time")
     it("rejects an event with an invalid signature")
   ```
   Get the requester's approval for the sentences before implementing them.
4. **Prepare the environment.** Isolated local database, minimal seed, cleanup between tests. Add an explicit guard that aborts the suite when the database URL looks like production (no `localhost`, `127.0.0.1` or `test`, or the environment variable is not `test`).
5. **Implement the tests through HTTP** with the stack's client (supertest, request specs, `TestClient`…), not by calling internal functions. Fire simultaneous requests truly in parallel (`Promise.all`, threads, `asyncio.gather`).
6. **Run the suite.** Report passing and failing tests. A failure caused by a real bug goes to a bug list for the requester; never change the test to pass. If a test looks wrong, stop and ask. One end-to-end smoke test is optional and needs approval.
7. **Deliver one command** that runs everything (`npm test`, `bundle exec rspec`, `pytest`…), registered where the project keeps commands (`GATES.md`, `package.json`, `Makefile`).

## Pitfalls

- Deleting, skipping (`skip`, `xit`, `.only`) or loosening assertions to make a test pass. A failing test means the code is wrong or the rule changed; in the second case ask.
- Refactoring passing tests that cover business rules.
- Connecting the suite to a database with production data.
- Using a different engine in tests (SQLite in tests, PostgreSQL in production): constraints and locks behave differently.
- Mocking the project's own database or business rules: that is a unit test.

## Verification

- Every approved sentence has an implemented test.
- The suite passes with one command.
- The anti-production guard was exercised (a fake production URL aborts the run).
- No existing test was removed, skipped or weakened.
- The final report lists covered flows, bugs found and the command.
