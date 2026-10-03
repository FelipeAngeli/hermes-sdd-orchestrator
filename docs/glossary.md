# Glossary

[Docs index](README.md) · [Overview](overview.md)

**Controller.** Hermes, running with `.hermes.md`. It is the only actor that changes STATE, decides transitions and dispatches workers. See [FSM and bounded loop](components/fsm-and-loop.md).

**Worker / leaf worker.** One external executor call (Codex or Claude) driven by a single brief. It never spawns another worker. See [Stage agents](components/stage-agents.md).

**Stage agent.** The brief for one FSM stage (`agents/<stage>.md`). See [Stage agents](components/stage-agents.md).

**Sub-agent.** A narrower specialist brief (`sub-agents/<role>.md`), dispatched only when a pending decision depends on it. See [Sub-agents and dispatch](components/sub-agents.md).

<a id="state"></a>**STATE.** `STATE.md`, whose YAML/JSON block is the source of truth for the current demand: ticket, stage, mode, budgets, baseline, ownership and gates. It is created by the [installer](components/skill-and-installer.md) or by the [vault bootstrap](components/obsidian-vault.md).

**Action journal.** A write-ahead log for one in-flight worker action. See [Action journal and recovery](components/action-journal.md).

**Envelope / final message.** The single JSON document a worker returns (`executor_result` or `review_result`). See [Contracts and schemas](components/contracts-and-schemas.md).

**Gate.** An ordered validation step: focused tests, format, analyze, review, CI. See [Gates and stack detection](components/gates-and-stack-detection.md).

**TDD slice.** One vertical increment with RED evidence (an expected functional failure) followed by GREEN evidence (a pass).

**Baseline / protected pre-existing.** Files that were dirty, staged or untracked before the demand started. They are read-only for agents.

**MANUAL / PAUSED / BOUNDED_AUTO / LOCAL_DELIVERY.** The loop modes and the schema 2 delivery profile. See [FSM and bounded loop](components/fsm-and-loop.md#modes).

**Human checkpoint.** Any action classified `HUMAN_REQUIRED`, for example commit, push, a protected-file change or an Obsidian write.

**Vault binding.** `.hermes/obsidian.json`, which links a repository to its Obsidian project container. See [Obsidian vault](components/obsidian-vault.md).

**Context graph.** The project's connected notes — modules, rules, tests, decisions and docs — that record what relates to what and why a decision was taken, so a dispatch can carry the context attached to the work. Optional, read-only, no graph database. See [Context graph](components/context-graph.md).
