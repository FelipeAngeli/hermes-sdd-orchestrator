# Stage agents

[Docs index](../README.md) · Related: [FSM and bounded loop](fsm-and-loop.md), [Sub-agents](sub-agents.md), [Contracts and schemas](contracts-and-schemas.md)

**Files:** `agents/*.md`, one brief per executable [FSM](fsm-and-loop.md#the-fsm) stage.

A stage agent is the worker brief the controller loads before dispatching a stage. It narrows the mission and never grants controller authority. Every brief has frontmatter with `name`, `stage`, `executor_policy: CONTROLLER_SELECTED` and `result_schema`, and it must contain these rules (enforced by [tests](testing.md#skill-suite-tests)):

- Never write `STATE.md`. Never spawn another worker. The controller alone decides transitions.
- Never commit, push, open a PR, mutate a backend, update an external system, or run unapproved E2E.
- The workspace is read-only, except in IMPLEMENT, which writes only to paths assigned by the controller.

| Brief | Stage | Mission | Result schema |
| --- | --- | --- | --- |
| `agents/specify.md` | `SPECIFY` | Turn the request into a verifiable problem statement: objective, outcomes, scope, exclusions, acceptance criteria. | `EXECUTOR_RESULT_SCHEMA.json` |
| `agents/clarify.md` | `CLARIFY` | Resolve material ambiguity with repository evidence and name decisions that need a human. | `EXECUTOR_RESULT_SCHEMA.json` |
| `agents/plan.md` | `PLAN` | Design the smallest approach grounded in real files, symbols, contracts and test seams. | `EXECUTOR_RESULT_SCHEMA.json` |
| `agents/tasks.md` | `TASKS` | Split the plan into ordered vertical slices, each traced to a requirement and a focused test. | `EXECUTOR_RESULT_SCHEMA.json` |
| `agents/implement.md` | `IMPLEMENT` | Implement exactly one slice under RED → minimal implementation → GREEN. | `EXECUTOR_RESULT_SCHEMA.json` |
| `agents/test.md` | `TEST` | Run the authorized focused validation and report exact commands and exit codes. | `EXECUTOR_RESULT_SCHEMA.json` |
| `agents/review.md` | `REVIEW` | Independently assess the diff and evidence against requirements, baseline, ownership and gates. | `REVIEW_RESULT_SCHEMA.json` |

The controller sends each stage only the context it needs: the request and constraints for SPECIFY/CLARIFY, validated paths, symbols and impact for PLAN/TASKS, and the slice, command boundary, ownership and gates for IMPLEMENT/TEST. It never sends conversation history or state dumps.

When a stage needs a narrower specialist, the controller may use one [sub-agent](sub-agents.md) instead. Stage agents never dispatch sub-agents themselves.
