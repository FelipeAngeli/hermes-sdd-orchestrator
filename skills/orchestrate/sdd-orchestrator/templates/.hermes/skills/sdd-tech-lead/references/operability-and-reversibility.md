# Operability, reversibility and security-by-design pointers

## Operability

For each behavior the change adds, name how an operator learns it failed: the log line, metric, alert or status the project already uses. Prefer the project's existing observability conventions; never log credentials, tokens or personal data to gain visibility.

## Reversibility

- Classify each step as reversible by redeploy, reversible by a compensating action, or irreversible.
- Name the point of irreversibility and what data a rollback would not undo.
- Order slices so the reversible part ships and is verified before the irreversible one.
- Persistence changes follow `sdd-database-design-migrations`; feature flags state their default, owner and off-path behavior.

## Security-by-design pointers

The tech-lead judgment names security-sensitive surfaces; it does not rule on exploitability. Record the surface in `stage_payload.decisions` so the controller can decide on the `security-reviewer` sub-agent under `DISPATCH_POLICY.md`:

- credentials, keys, tokens or connection strings entering code, configuration, tests or CI;
- session, authentication or authorization decisions, especially ones based on client-supplied values;
- storage of personal or sensitive data, and its lifetime after sign-out;
- logs, telemetry, URLs or deep links that may carry sensitive values;
- external mutations, commands built from input, and file paths derived from input.

## Not an approver

A request that names a tech-lead approval is resolved through `PROJECT_SETUP.md` `approvers` (default `requester`) and recorded as a HUMAN check or `WAIVED` evidence, exactly as `sdd-product-owner/references/approvals-and-waivers.md` describes. It never blocks IMPLEMENT unless the request literally says so.
