# Architecture compliance against declared rules

This judgment fails by importing opinion as law. Every codebase violates someone's preferred architecture, so reasoning from general principle produces endless findings and recasts a team's deliberate choices as defects. The authority is the project's own declared rules, and a violation is reportable only by naming the rule it breaks.

## Method

1. Read the project's declared rules first: architecture documentation, ADRs, module or dependency configuration, lint and boundary tooling, and the conventions the codebase applies consistently.
2. Derive the intended layer map and the allowed direction of dependency between layers, and state it explicitly before judging anything.
3. Compare the actual import, reference and instantiation graph against that map.
4. For each violation, name the rule, the source and target, the exact path and line, and what the violation makes possible that the rule was meant to prevent.
5. Separate what this change introduced from what already existed on the paths it touched.
6. Report the smallest correction that restores the rule, without implementing it during REVIEW.

## Violation surfaces

- Layer traversal: presentation referencing data or infrastructure directly; a use case reaching a client, DAO or HTTP type; a view holding a repository implementation instead of an abstraction.
- Dependency direction and inversion: an inner layer importing an outer one; domain depending on a framework, serialization annotation, transport type or persistence detail; inversion in name only, where the interface sits on the outer side; an abstraction with a single implementation that exists only to satisfy ritual.
- Misplaced responsibility: business rules in a controller, widget, view model or mapper; orchestration in an entity; persistence or transport concerns inside domain services.
- Leaking abstractions: a data-layer model, framework type, generated client class or error type crossing into domain or presentation; a repository signature exposing the storage it hides.
- Module boundaries: reaching into another module's internals, a circular dependency between modules, a shared module accumulating unrelated responsibilities.
- Dependency registration: a concrete implementation injected where the abstraction is declared, construction of dependencies inside a class that should receive them.
- Consistency: an established pattern applied one way everywhere and another way in the change under review.

## Evidence rules

- Cite the declared rule each violation breaks, quoting the document, configuration or lint setting that states it; a finding with no cited rule is an opinion.
- Never enforce a convention the project has not declared. Report an undeclared but consistent convention as a question: propose declaring it and say plainly that it is unwritten.
- Never invent an architectural rule, and never infer a rule from a single occurrence.
- Distinguish a violation this change introduced from one it inherited; only the first blocks the change.
- Report a documented exception as satisfied rather than violated.
- Rank findings by what the violation risks — untestability, coupling that blocks change, a rule that will erode next time.
- Report proven violations separately from unproven suspicions.
