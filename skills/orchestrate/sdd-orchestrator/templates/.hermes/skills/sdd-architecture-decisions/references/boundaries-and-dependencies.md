# Boundaries and dependencies

## Derive, do not impose

Start from the project's declared architecture, build configuration, import rules and repeated code structure. A boundary is authoritative only when declared or accepted for the demand.

## Boundary record

For each changed boundary, name:

- purpose and owner;
- public contract;
- allowed callers;
- dependency direction;
- data/error types allowed to cross;
- side effects and consistency guarantee;
- test or lint mechanism that protects it.

## Design rules

- Inner policy should not require an outer transport/framework type unless the project explicitly chose that coupling.
- Put interfaces where consumers need substitution, not automatically beside implementations.
- A shared module needs one coherent reason to change; “importable everywhere” is not ownership.
- Cross-module access uses the published surface, not internal files.
- Split deployment only for a named ownership, scaling, isolation or release need; process boundaries add failure modes.
- Keep synchronous work synchronous unless durable asynchronous behavior solves a stated requirement.

## Migration

A boundary change lists definitions, call sites, registrations, serializers, tests and downstream consumers. Preserve compatibility or stage the transition; renaming a type without migrating its consumers is not a boundary design.

## Verification

Use existing boundary tooling where available. Otherwise verify concrete imports/calls and record uninspected dynamic paths as gaps.
