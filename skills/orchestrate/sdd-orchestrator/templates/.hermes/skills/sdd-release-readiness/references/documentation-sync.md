# Documentation kept true to the code

Wrong documentation is worse than absent documentation, because absent documentation sends the reader to the code. A writer risks fluent prose describing code that does not exist — and readers trust it precisely because it reads well.

## Method

1. Read the code as the source of truth; existing documentation is a claim to verify, never an input to paraphrase.
2. Identify what the change actually altered in observable terms: public surface, configuration, commands, data shape, workflow, constraints.
3. Classify each documentation artifact as accurate, stale, incomplete or describing something that no longer exists.
4. Write only what a reader cannot get faster from the code: intent, constraints, non-obvious interactions and the reason behind a decision.
5. Verify every symbol, path, command, flag and output against the repository before writing it.

## Artifacts

- Technical documentation: how a subsystem works, what it guarantees, what it deliberately does not handle, how to operate it.
- ADRs: one decision per record with context, alternatives, choice and consequences — including the unfavorable ones. Supersede an outdated ADR instead of editing history (see `sdd-architecture-decisions`).
- README: what the project is, how to run it, how to test it, where to look next; every command verified as written.
- Diagrams: update when the structure changes, and delete a diagram that no longer matches.
- Changelog and release notes, when the repository keeps them: user-visible change, not commit titles.
- Inline documentation: only where the code cannot express the reason itself.

## Evidence rules

- Write only to paths explicitly assigned by the controller for the slice.
- Never document behavior that was not verified in the code.
- Never invent a rationale; recover it from code, tests, history or supplied evidence, or record it as an open question.
- Remove documentation describing code that no longer exists; a deletion is often the most valuable edit.
- Never weaken or delete a warning, constraint or security note to make the text flow better.
- Never change code to match the documentation; report the mismatch and let the controller decide which side is wrong.
- Match the repository's established voice, structure and terminology.
