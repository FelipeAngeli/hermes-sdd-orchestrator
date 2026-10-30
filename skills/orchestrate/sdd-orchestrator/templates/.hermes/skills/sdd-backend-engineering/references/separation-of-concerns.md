# Separation of concerns: presentation, rules, data

Every piece of code belongs in one of three drawers. This is not Clean Architecture or extra layers: it is the minimum organization that lets people and agents find their way. Tangled code is a main cause of the mid-project productivity drop in AI-assisted projects. Use when files mix presentation, calculation, validation and database/API calls, or when an agent starts repeating code and "forgetting" rules. Apply it **after** integration tests exist. Declared module boundaries: `sdd-architecture-decisions`.

## Quick reference

| Drawer | Holds | Never holds |
| --- | --- | --- |
| Presentation (screen, handler, CLI) | Components, forms, visual state, request parsing, events | Calculations, direct database/API calls |
| Business rules | Calculations, validations, decisions (total, coupon, availability) | UI code, SQL, HTTP calls |
| Data | Database, ORM, external APIs, gateway, email, storage | Business decisions |

Follow the folder convention the stack already uses; without one, propose something simple (`ui/`, `domain/` or `rules/`, `data/` or `services/`). Descriptive names beat layers.

## Procedure

1. **Tests first.** Check that the flows you will touch have integration tests. Without them, stop and apply `integration-test-suite.md` first, or write characterization tests of the current behavior (what the code does today, even if it looks wrong) before moving anything.
2. **Map.** For each large or mixed file, classify each function or block as presentation, rule or data, as a table: file → block → right drawer. Also flag cross dependencies, duplicated code and global state.
3. **Propose the folder structure** following the project's existing pattern, with no new frameworks or libraries.
4. **Plan small steps.** One step moves one piece while keeping public names and a working application (for example: "move the total calculation from `checkout.tsx` to `domain/order.ts`"). Order from safest to riskiest. Get approval of the plan before the first step.
5. **Execute step by step.** After each step run the tests and the lint/type-check/build gates. Green → small commit and next step. Red → fix the code (never the test) or undo the step.
6. **Note bugs, do not fix them.** A bug found during the refactor goes to a list or issue and is fixed later in a separate change. A refactor does not change behavior.
7. **Final report:** what moved, the new structure, the bugs noted.

## Pitfalls

- Changing behavior during the refactor, or "taking the chance" to fix a bug or improve a feature.
- Running the refactor and a bug fix in parallel (tabs, agents or simultaneous branches) on the same code.
- Creating layers, interfaces and abstractions nobody asked for; three drawers are enough.
- Renaming routes, public props, exported functions or database columns.
- Editing tests to fit the refactor: a broken test means behavior changed.
- Doing the whole refactor in one commit.

## Verification

- Every touched file clearly belongs to one drawer.
- Presentation code holds no business rule and no direct database/API access.
- Tests, lint and build are green after every step.
- Observable behavior is identical to before.
- Bugs found are noted, not fixed midway.
