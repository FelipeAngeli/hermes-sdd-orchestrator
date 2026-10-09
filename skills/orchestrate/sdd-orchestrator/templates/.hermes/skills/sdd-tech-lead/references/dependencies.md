# Dependency decisions

Scope the judgment to the change. A full inventory of every dependency is a different, more expensive job that answers no pending decision.

## Method

1. Read the manifest and the lockfile as the source of truth; a version quoted from documentation or memory is not evidence.
2. Use the project's own package-manager resolution, tree and audit commands, read-only, before reasoning about versions.
3. For a dependency the change adds, first search the project for an existing package or internal module that already does the job.
4. For an upgrade, identify what the version range actually resolves to and what changed between the resolved versions.
5. Report each finding with the manifest entry, the resolved version and the command whose output supports it.

## Surfaces

- Version drift: a range that resolves differently across environments or lockfile states; a floating range on a risky dependency.
- Duplication: two packages solving one problem, or one package resolved at several versions in one tree.
- Compatibility: a runtime, SDK or peer version the project does not use; a peer requirement satisfied only by coincidence.
- Maintenance status: an unmaintained or deprecated package, or one whose successor is already the ecosystem default — claimed only with release-history evidence.
- Transitive risk: a critical capability reaching the project only through a transitive package.
- Lockfile integrity: a manifest change not reflected in the lockfile, or a lockfile change with no manifest reason.
- Weight: a dependency pulled in for a fraction of its surface where an equivalent primitive exists.

## Evidence rules

- Never propose a new dependency when the project already has an equivalent.
- Never claim a package is unmaintained without release-history evidence.
- Hand a known vulnerability, unsafe default or package handling credentials to the `security-reviewer` sub-agent; a layer violation goes to `references/architecture-compliance.md`.
- State the cost of a remedy: an upgrade that requires code changes is not a free fix.
- Never run an install or update that writes the manifest, lockfile or module tree outside an IMPLEMENT slice that owns them.
- Return `NO_FINDINGS` when the dependencies the change touches are sound.
