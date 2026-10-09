# Regression hunt

The author reasoned about the new call site. This judgment reasons about every other one.

## Method

1. Enumerate the changed files and the public symbols they declare or alter.
2. Locate every consumer of those symbols that the change did not touch; an untouched consumer is the regression candidate, because nobody reviewed it against the new behavior.
3. Classify the non-local reach of the change: shared modules, dependency registration, routing contracts, entities, repository contracts, persisted keys and network interceptors.
4. Run the consumer's own suite — not the suite of the change — and record the exact command, exit code and failure output.
5. Separate a pre-existing failure from a failure this change introduced; when in doubt, reproduce the suite without the change and restore the workspace immediately afterwards.
6. Report consumers with no protecting suite as unverifiable risk, naming the exposed behavior.

## Regression surfaces

- Data contract: a field that became required or optional, a changed type, a new enum value leaving an existing exhaustive branch incomplete.
- Persisted state: a renamed or reformatted storage key without migration, which breaks only upgrading installations.
- Dependency registration: a changed binding reaches every injection site, including other features.
- Routing: a renamed route reached by deep links, notifications or other features; a new guard blocking a legitimate flow.
- Network: an interceptor, header, retry, timeout or error-mapping change affects every call.
- Shared interface: a reused component, including snapshot or golden comparisons in other areas.

## Evidence rules

- A green consumer suite proves nothing when it never exercises the affected path; report that as uncovered risk.
- Only an executed suite supports a confirmed regression; report unproven suspicions separately.
- Reporting a pre-existing failure as a new regression invalidates the whole report.
- Revert any temporary reproduction step before returning; never regenerate snapshots to resolve a red result.
