# Validation gates (opt-in, observation mode)

[Docs index](../README.md) · Related: [Loop engine](loop-engine.md), [Gates and stack detection](gates-and-stack-detection.md), [Contracts and schemas](contracts-and-schemas.md)

**Files:** `runtime/gate_registry.py`, `runtime/gate_validator.py`, `runtime/gate_stages.py`, `runtime/gate_reviewer.py`, `runtime/gate_evidence.py`, `runtime/gate_corrections.py`, `runtime/gate_engine.py`, `runtime/gate_cli.py`, `schemas/GATE_DECISION_SCHEMA.json`.

Formal, auditable validation criteria for every SDD stage, inspired by the gates and goal evaluators of CompozyOS. **Nothing in the controller calls them**: `sdd.py`, the drivers and `policies/GATES.md` behave as before. The default mode is `OBSERVE`: every evaluation is recorded and nothing is blocked. They run only through `gate_cli.py evaluate`, `gate_engine.GateEngine` in a host program, or `gate_engine.gated_handler` around a [loop engine](loop-engine.md) handler.

## Components

| Component | Module | Role |
| --- | --- | --- |
| GateRegistry | `gate_registry` | Catalogue: one gate per stage (`gate_id` `sdd.<stage>`), each with criteria `{id, description, evaluator, on_fail}`. `evaluator` is `DETERMINISTIC` or `AGENT`; `GateRegistry.digest()` is part of every decision key, so a changed criterion never reuses an old decision. |
| DeterministicValidator | `gate_validator`, `gate_stages` | Checks the artifact against its JSON Schema and runs the stage's checks against the real workspace and real commands. |
| AgentReviewer | `gate_reviewer` | Asks independent reviewers about the `AGENT` criteria with minimal context. |
| EvidenceCollector | `gate_evidence.EvidenceCollector` | Content-addressed, versioned, redacted evidence records. |
| GateDecision | `gate_engine`, `gate_evidence.DecisionLog`, `GATE_DECISION_SCHEMA.json` | Combines the results into one structured decision in an append-only, hash-chained log. |

## Gates per stage

| Stage (`GATE_STAGES`) | Criteria (DETERMINISTIC unless marked AGENT) |
| --- | --- |
| SPECIFY | `SCHEMA_VALID`, `SCOPE_STATED` (non-empty summary), `ACCEPTANCE_CRITERIA_DEFINED` (a check with criterion and verification method), `NO_BLOCKERS` |
| CLARIFY | `SCHEMA_VALID`, `NO_OPEN_MATERIAL_QUESTIONS` (BLOCKED), `MATERIAL_ASSUMPTIONS_VALIDATED` (BLOCKED), `NO_BLOCKERS` |
| PLAN | `SCHEMA_VALID`, `IMPACT_FILES_EXIST` (each file exists in the repository), `DECISIONS_RECORDED`, `INDEPENDENT_REVIEWER`, `FEASIBILITY` (AGENT: dependencies and risks), `NO_BLOCKERS` |
| TASKS | `SCHEMA_VALID`, `TASKS_DEFINED`, `CHECKS_TRACED_TO_SLICES`, `CHECKS_HAVE_VERIFIERS` (each AGENT check names a backticked verifier), `NO_BLOCKERS` |
| IMPLEMENT | `SCHEMA_VALID`, `CLAIMS_MATCH_WORKSPACE` (reported files = `git status`), `WITHIN_EDITABLE_PATHS`, `STATIC_ANALYSIS` (the configured Analyze command), `NO_BLOCKERS` |
| TEST | `SCHEMA_VALID`, `TESTS_CONFIGURED` (BLOCKED), `TESTS_EXIST` (files the test command names exist), `TESTS_PASS` (the gate runs Focused tests itself), `EVIDENCE_REPRODUCED` (claimed passing commands are re-run), `CHECKS_COVERED`, `ACCEPTANCE:<id>` per check, `NO_BLOCKERS` |
| REVIEW | `SCHEMA_VALID`, `REVIEW_APPROVED`, `RECORDED_GATES_PASS`, `BASELINE_AND_OWNERSHIP`, `ACCEPTANCE:<id>` per check, `INDEPENDENT_REVIEWER`, `FUNCTIONAL_ADHERENCE`, `SECURITY`, `INTEGRATION` (all three AGENT) |

`ACCEPTANCE:<id>` passes only when the check's bound verifier (`context.check_commands`) is a configured gate command or a command `acceptance_verifiers.allowed_check_command` accepts, and the gate runs it and gets exit 0. A WAIVED check with a recorded waiver passes; a HUMAN check is BLOCKED until a human decides. A check with only prose evidence fails: a textual answer is not proof.

The checks are language-neutral: commands come from the Focused tests and Analyze rows of `policies/GATES.md` (`gate_cli.gate_commands`) or from the host's `commands` mapping.

## Rules

- **Evidence, not text.** Every criterion stores what it looked at (`EvidenceCollector.put`); a worker's claim is checked against the workspace or re-run. A PASS always has evidence references beyond the artifact itself, or it becomes INCONCLUSIVE (`EVIDENCE_PRESENT`).
- **Tests are not replaced by judgement.** Test criteria are DETERMINISTIC, and AGENT criteria are asked only when no deterministic criterion FAILed, so a reviewer can never overrule a failing test.
- **Inconclusive is never success.** A timeout or an unrunnable command is INCONCLUSIVE; `gate_engine.is_success` is true only for PASS. Verdict precedence: FAIL, then BLOCKED, then INCONCLUSIVE, then PASS. Criteria skipped after an invalid schema are `NOT_EVALUATED` and do not count.
- **Independent, minimal review.** A reviewer is `Reviewer(name, family, call)`. Reviewers of the implementer's family (`context.implementer`) are ignored; with none left the AGENT criteria and `INDEPENDENT_REVIEWER` are BLOCKED. The reviewer sees only the gate, stage, task, criterion texts and short evidence summaries with references, capped at `gate_reviewer.MAX_CONTEXT_BYTES` (16 KiB); never the conversation, the repository or the worker's prose. A PASS that cites no reference from that context, a reviewer error, a malformed answer and disagreeing reviewers are all INCONCLUSIVE.
- **No re-review without new evidence.** The idempotency key hashes the registry, gate, task, artifact, workspace (HEAD plus the hash of every changed file), commands, timeouts, reviewer roster, context and mode. The same key returns the recorded decision with `reused: true`: no command runs and no reviewer is asked.
- **History is kept.** Decisions are appended, never rewritten: each has `generation` and `previous_decision_id`, plus `prev_hash` and `record_hash`; `DecisionLog.verify` detects an edited or removed decision (`DECISION_LOG_TAMPERED`). Evidence is stored under its SHA-256, so an edited record is `EVIDENCE_TAMPERED` on read. Both carry a `schema_version`.
- **Commands are contained.** No shell (`shlex.split`), run in the repository with the gate's timeout (default 300 s), a minimal environment (`SAFE_ENV`: `PATH`, `HOME`, `LANG`, `LC_ALL`, `TMPDIR`, `SYSTEMROOT`, `PYTHONPATH`, `VIRTUAL_ENV`) and output tails of at most 4 000 characters. Every string written to evidence or the decision log goes through `redaction.redact`. The store is owner-private (0700 directories, 0600 files, no symlinks), reusing `loop_store`. Each command runs once per evaluation.

## GateDecision

`GATE_DECISION_SCHEMA.json` (v1, closed): `gate_id`, `task_id`, `verdict` (`PASS` | `FAIL` | `BLOCKED` | `INCONCLUSIVE`), `evidence_refs` (`sha256:<hex>`), `failed_criteria` (`{id, status, refs, detail}`), `suggested_actions`, `evaluator` (`gate_engine/deterministic`, plus `+agent:<names>` when reviewers were asked), `timestamp` (UTC ISO 8601), and also `decision_id`, `stage`, `criteria`, `generation`, `previous_decision_id`, `idempotency_key`, `registry_sha256`, `artifact_sha256`, `mode`, `blocking`, `corrective_task`, `prev_hash`, `record_hash`. The schema requires evidence and no failed criteria for every PASS.

## Correction control

A FAIL produces a `corrective_task` (`gate_corrections`): the failure is classified (`TEST_FAILURE`, `MISSING_TEST`, `FALSE_EVIDENCE`, `ACCEPTANCE_UNMET`, `STATIC_ANALYSIS`, `SCOPE_VIOLATION`, `REVIEW_FINDINGS`, `GATE_FAILURE`, `CONTRACT_INVALID`, `ARTIFACT_INCOMPLETE`), the smallest unit is named (one acceptance check, the failing command, the files outside scope, or the stage), and the task carries the stage to re-run (IMPLEMENT for failures found at IMPLEMENT, TEST or REVIEW; the stage itself otherwise), the failed criteria, one hypothesis, the evidence and a scope limit. BLOCKED and INCONCLUSIVE never produce a task: they need a human or new evidence, not a code change.

`gate_engine.gated_handler(gates, handler, task_id=..., context=...)` wraps a loop-engine stage handler and evaluates `output.artifact`. In `OBSERVE` it only attaches `output.gate`. In `ENFORCE` a FAIL becomes a FUNCTIONAL failure carrying `failed_checks`, `hypothesis` and `evidence`, which the loop engine's bounded correction turns into a corrective IMPLEMENT run; the next evaluation sees the new workspace, so it is a new generation. BLOCKED becomes a FUNCTIONAL failure without a correction and INCONCLUSIVE an UNKNOWN failure; the loop engine escalates both to a human.

## Modes

`MODES`: `OBSERVE` (default) records decisions with `blocking: false`; `ENFORCE` sets `blocking: true` on any verdict other than PASS. Promote to `ENFORCE` only after observing decisions on real demands.

## CLI

`python3 runtime/gate_cli.py <command> ... [--json]`.

| Command | Options and result |
| --- | --- |
| `evaluate` | `--stage <stage> --task <id> --artifact <json> --store <dir> [--repo <dir>] [--gates <GATES.md>] [--context <json>] [--enforce]`: records one decision; exit 0 in observation mode whatever the verdict, 3 with `--enforce` on a non-PASS, 2 on an error. No reviewer is wired from the command line, so AGENT criteria are BLOCKED. |
| `history` | `--store <dir> --gate <gate id> --task <id>`: the recorded decisions in order. |
| `verify` | `--store <dir>`: checks the decision-log hash chain. |
| `registry` | Prints every gate and the registry hash. |

## Quality report

The suites `test_gates_acceptance.py`, `test_gates_rules.py` and `test_gates_units.py` (34 tests) cover the eight required scenarios — correct code, failing test, missing test, false evidence, contradictory reviewers, inconclusive result, unmet acceptance criterion, a failure fixed in a new generation — plus the business rules, every stage gate, the loop-engine integration in both modes, the decision schema, redaction, the minimal environment and no shell. A mutation check confirmed the suites fail when the verdict aggregation, implementer exclusion, false-evidence detection, reviewer-disagreement rule, idempotency, uncited-PASS rule, missing-test detection or enforce-mode blocking is removed.

Limits, stated plainly:

- A reviewer's honesty is not verified: the rules only ensure it is independent by family, sees minimal context and cites evidence that exists.
- `EVIDENCE_REPRODUCED` re-runs only claimed commands the gate may run (configured or allowlisted); other claims are listed as not reproducible, not as PASS.
- `TESTS_EXIST` recognises file-like arguments of the test command; a test selected only by name inside a runner (for example a `-k` expression) is not checked.
- Evidence records a workspace fingerprint (HEAD plus changed-file hashes); it does not snapshot file contents.
- The tamper evidence detects edits that do not recompute the hash chain; a process with write access to the store can rewrite it consistently, which is why the store must be owner-private.

## Rollback

Nothing in the controller imports these modules. Revert the change to remove them; delete a store directory to discard its decisions and evidence.
