# Validation gates (opt-in, observation mode)

[Docs index](../README.md) · Related: [Loop engine](loop-engine.md), [Gates and stack detection](gates-and-stack-detection.md), [Contracts and schemas](contracts-and-schemas.md)

**Files:** `runtime/gate_registry.py`, `runtime/gate_validator.py`, `runtime/gate_workspace.py`, `runtime/gate_stages.py`, `runtime/gate_reviewer.py`, `runtime/gate_evidence.py`, `runtime/gate_corrections.py`, `runtime/gate_engine.py`, `runtime/gate_cli.py`, `schemas/GATE_DECISION_SCHEMA.json`.

Formal, auditable validation criteria for every SDD stage, inspired by the gates and goal evaluators of CompozyOS. **Nothing in the controller calls them**: `sdd.py`, the drivers and `policies/GATES.md` behave as before. The default mode is `OBSERVE`: every evaluation is recorded and nothing is blocked. They run only through `gate_cli.py evaluate`, `gate_engine.GateEngine` in a host program, or `gate_engine.gated_handler` around a [loop engine](loop-engine.md) handler.

## Components

| Component | Module | Role |
| --- | --- | --- |
| GateRegistry | `gate_registry` | Catalogue: one gate per stage (`gate_id` `sdd.<stage>`), each with criteria `{id, description, evaluator, on_fail}`. `evaluator` is `DETERMINISTIC` or `AGENT`; `GateRegistry.digest()` is part of every decision key, so a changed criterion never reuses an old decision. |
| DeterministicValidator | `gate_validator`, `gate_stages`, `gate_workspace` | Checks the artifact against its JSON Schema (artifacts over `gate_stages.MAX_ARTIFACT_BYTES`, 2 MiB, fail it) and runs the stage's checks against the real workspace and real commands. |
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
| TEST | `SCHEMA_VALID`, `TESTS_CONFIGURED` (BLOCKED), `TESTS_EXIST` (files the test command names exist), `TESTS_PASS` (the gate runs Focused tests itself), `EVIDENCE_REPRODUCED` (claimed passing commands are re-run, at most `MAX_REPRODUCED` = 10), `CHECKS_COVERED` (each AGENT check has a controller-bound verifier or waiver), `ACCEPTANCE:<id>` per check, `NO_BLOCKERS` |
| REVIEW | `SCHEMA_VALID`, `REVIEW_APPROVED` (APPROVED and no CRITICAL, HIGH, BLOCKER or BLOCKING finding), `RECORDED_GATES_PASS` and `BASELINE_AND_OWNERSHIP` (consistency of what the review reports), `TESTS_PASS` and `STATIC_ANALYSIS` (the gate re-runs Focused tests and Analyze itself), `ACCEPTANCE:<id>` per check, `INDEPENDENT_REVIEWER`, `FUNCTIONAL_ADHERENCE`, `SECURITY`, `INTEGRATION` (all three AGENT) |

`ACCEPTANCE:<id>` passes only when the check's bound verifier (`context.check_commands`) is a configured gate command or a command `acceptance_verifiers.allowed_check_command` accepts, and the gate runs it and gets exit 0, or when the controller recorded a waiver for it (`context.waivers`). A waiver written only in the artifact is BLOCKED; a HUMAN check is BLOCKED until a human decides; a check with only prose evidence fails: a textual answer is not proof. The expected checks are the union of the bound, waived and `context.expected_check_ids` ids (plus `acceptance.expected_check_ids` at REVIEW): an expected check the artifact leaves out fails.

The checks are language-neutral: commands come from the Focused tests and Analyze rows of `policies/GATES.md` (`gate_cli.gate_commands`) or from the host's `commands` mapping.

## Controller-owned context

`context` keys (`gate_engine.CONTEXT_KEYS`): `check_commands`, `editable_paths`, `implementer`, `waivers`, `expected_check_ids`. They must come from the controller's own state (for example the verifier binding and the human-decision journal), never from a worker artifact; other keys are dropped. All of them are part of the decision key.

## Rules

- **Evidence, not text.** Every criterion stores what it looked at (`EvidenceCollector.put`); a worker's claim is checked against the workspace or re-run. A PASS always has evidence references beyond the artifact itself, or it becomes INCONCLUSIVE (`EVIDENCE_PRESENT`).
- **Tests are not replaced by judgement.** Test criteria are DETERMINISTIC, and AGENT criteria are asked only when no deterministic criterion FAILed, so a reviewer can never overrule a failing test.
- **Inconclusive is never success.** A timeout or an unrunnable command is INCONCLUSIVE; `gate_engine.is_success` is true only for PASS. Verdict precedence: FAIL, then BLOCKED, then INCONCLUSIVE, then PASS. Criteria skipped after an invalid schema are `NOT_EVALUATED` and do not count.
- **Independent, minimal review.** A reviewer is `Reviewer(name, family, call)`. The implementer is `context.implementer` and the artifact's `executor.name`; families compare by `gate_reviewer.family` (leading letters, case-folded, so `codex-cli` is `codex`). Reviewers of an implementer's family are ignored; with none left, or with no implementer known, the AGENT criteria and `INDEPENDENT_REVIEWER` are BLOCKED. The reviewer sees only the gate, stage, task, criterion texts and short evidence summaries with references, capped at `gate_reviewer.MAX_CONTEXT_BYTES` (16 KiB); never the conversation, the repository or the worker's prose. A PASS that cites no reference from that context, a reviewer error, a malformed answer and disagreeing reviewers are all INCONCLUSIVE.
- **No re-review without new evidence.** The idempotency key hashes the registry, gate, task, artifact, workspace (HEAD plus the hash of every file `git status` reports), commands, timeouts, reviewer roster, context and mode. The same key returns the recorded decision with `reused: true` after the log's hash chain is verified and every evidence reference of that decision resolves: no command runs and no reviewer is asked. Gitignored files are not in the fingerprint, so changing only them is not new evidence. A `retryable` decision (INCONCLUSIVE from a timeout, an unrunnable command, a git failure or a reviewer error; those criteria carry `transient: true`) is never reused: the same inputs are evaluated again as the next generation.
- **History is kept.** Decisions are appended, never rewritten: each has `generation` and `previous_decision_id`, plus `prev_hash` and `record_hash`. Every read used for reuse, history or a new append verifies the whole chain first, so an edited or removed decision raises `DECISION_LOG_TAMPERED` instead of being served. Evidence is stored under its SHA-256, so an edited record is `EVIDENCE_TAMPERED` on read. Both carry a `schema_version`.
- **Commands are contained.** No shell (`shlex.split`), run in the repository with the gate's timeout (default 300 s), no stdin, a minimal environment (`SAFE_ENV`: `PATH`, `HOME`, `LANG`, `LC_ALL`, `TMPDIR`, `SYSTEMROOT`, `PYTHONPATH`, `VIRTUAL_ENV`) and output written to temporary files, of which only the end window is read back, redacted and then cut to 4 000 characters. Every string written to evidence or the decision log goes through `redaction.redact`.
- **Git is hardened.** `gate_workspace` runs git with its own minimal environment (`GIT_SAFE_ENV`, no system or global config, `HOME` set to the null device) and overrides `core.fsmonitor`, `core.untrackedCache` and `core.hooksPath`, so repository settings cannot run a program through those; `--no-renames` makes a moved file report both paths. A failing git call is INCONCLUSIVE, never an empty change list. The store is owner-private (0700 directories, 0600 files, no symlinks), reusing `loop_store`. Each command runs once per evaluation.

## GateDecision

`GATE_DECISION_SCHEMA.json` (v1, closed): `gate_id`, `task_id`, `verdict` (`PASS` | `FAIL` | `BLOCKED` | `INCONCLUSIVE`), `evidence_refs` (`sha256:<hex>`), `failed_criteria` (`{id, status, refs, detail}`), `suggested_actions`, `evaluator` (`gate_engine/deterministic`, plus `+agent:<names>` when reviewers were asked), `timestamp` (UTC ISO 8601), and also `decision_id`, `stage`, `criteria`, `generation`, `previous_decision_id`, `idempotency_key`, `registry_sha256`, `artifact_sha256`, `mode`, `blocking`, `retryable`, `corrective_task`, `prev_hash`, `record_hash`. The value `evaluate` returns adds `reused`, which the schema allows and the log never stores. The schema requires evidence and no failed criteria for every PASS.

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

The suites `test_gates_acceptance.py`, `test_gates_rules.py`, `test_gates_units.py` and `test_gates_review.py` (51 tests) cover the eight required scenarios — correct code, failing test, missing test, false evidence, contradictory reviewers, inconclusive result, unmet acceptance criterion, a failure fixed in a new generation — plus the business rules, every stage gate, the loop-engine integration in both modes, the decision schema, redaction, the minimal environment, no shell, and the review regressions: forged waivers, dropped checks, a REVIEW that re-runs the tests, an unknown or renamed implementer, tampered reuse, hardened git, renames and the re-run cap. A mutation run removes 26 rules one at a time (aggregation, evidence downgrade, reviewer skipping on a deterministic failure, waiver provenance, expected checks, cited-ref filter, chain verification on append and on read, claim matching, context cap, each REVIEW check, coverage, implementer exclusion and normalisation, transient reuse, the git overrides, the re-run cap, redaction order, history, environment and impact files); the suites fail for every one.

Limits, stated plainly:

- A reviewer's honesty is not verified: the rules only ensure it is independent by family, sees minimal context and cites evidence that exists.
- `EVIDENCE_REPRODUCED` re-runs only claimed commands the gate may run (configured or allowlisted); other claims are listed as not reproducible, not as PASS.
- `TESTS_EXIST` recognises file-like arguments of the test command; a test selected only by name inside a runner (for example a `-k` expression) is not checked.
- Evidence records a workspace fingerprint (HEAD plus changed-file hashes); it does not snapshot file contents, and gitignored files are outside it and outside the editable-path check.
- Git still honours other repository-local settings (for example a `filter.<name>.clean` driver from `.gitattributes`), so the worker must not be able to write the repository's `.git` directory.
- `context` is trusted: the gate cannot tell whether a caller passed controller state or worker output.
- The tamper evidence detects edits that do not recompute the hash chain; a process with write access to the store can rewrite it consistently, which is why the store must be owner-private.

## Rollback

Nothing in the controller imports these modules. Revert the change to remove them; delete a store directory to discard its decisions and evidence.
