# Value, scope and observable acceptance

## Value

State in one sentence who benefits and what changes for them when the demand ships. A demand whose value cannot be stated is a material question, not a reason to invent one.

## Scope

- Inclusions: each behavior the request authorizes, traced to a quote.
- Exclusions: adjacent behavior a reader might assume but the request does not authorize. Name them so later stages cannot drift into them.
- Constraints: compatibility, channel, audience, data and deadline limits the request or project rules state.

Anything not authorized is out of scope. Adding it requires a new explicit requirement from the requester.

## Observable acceptance criteria

Each criterion:

- describes an outcome a user, operator, test or command can observe, never an internal intention;
- names its verification method (focused test, static analysis, schema validation, STATE or log inspection, or a human decision) and its verifier (`AGENT` or `HUMAN`);
- has a stable identifier that survives every later stage unchanged.

Use `HUMAN` only when no deterministic check can observe the outcome (copy approval, visual judgment, a decision the request assigns to a person). A HUMAN check is satisfied by a recorded human decision or a recorded waiver, never by the worker's opinion.

## Impact on users and channels

Record which audience, channel or integration observes the change, and the behavior they see on failure. A criterion that only covers the happy path leaves the most visible behavior unspecified.
