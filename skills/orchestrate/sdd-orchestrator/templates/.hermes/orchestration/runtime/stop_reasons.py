#!/usr/bin/env python3
"""Closed registry of every ``stop_reason`` a runtime emits, with exactly one next action each.

LOOP_POLICY.md §9 renders this table and ``tests/test_stop_reasons.py`` keeps the
three in sync: every literal a runtime emits is registered here, every registered
code is emitted by some runtime, and §9 lists exactly these codes. A controller
that meets a stop reason therefore never has to invent a step: it runs the
``next_command`` (placeholders in ``<...>`` are filled from the user's own
words) and then ``sdd.py next``.

``{sdd}`` in a command template is rendered as the exact interpreter + path of
this controller's ``runtime/sdd.py``.
"""
from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Any

SDD_SCRIPT = Path(__file__).resolve().with_name("sdd.py")

#: kind: PAUSED (healthy, waits for a decision), BLOCKED (technical/safety impediment),
#: HUMAN (needs a user decision recorded through the command) or DONE.
PAUSED, BLOCKED, HUMAN, DONE = "PAUSED", "BLOCKED", "HUMAN", "DONE"

_RAISE = "{sdd} budget --raise %s --by 1 --quote '<user words authorizing more>'"
_REOPEN = "{sdd} reopen --reason '<failure>' --quote '<user words>'"
_REBASELINE = "{sdd} rebaseline --quote '<user words>'"
_CONFIRM_POLICY = "{sdd} confirm-policy --name <policy> --by requester --quote '<user words>'"
_NEXT = "{sdd} next"
_STATUS = "{sdd} status"
_ABANDON = "{sdd} abandon --reason '<reason>' --quote '<user words>'"

#: Placeholders a registered command may carry: each is filled with the user's own
#: words (or, for ``<reason>``, the controller's one-line reason; ``<vault>`` and
#: ``<project>`` are the Obsidian vault and project container the user names;
#: ``<installed-skill>`` is the installed skill directory the Hermes session loaded
#: this skill from, which holds ``scripts/install_project.py``). Anything the
#: runtime knows (gate, check id, question index, budget name) is filled by
#: ``sdd.py`` itself in the printed ``next_command``, so a registry entry only
#: carries ``<...>`` for it when the registry row is the generic fallback (the
#: concrete stop always overrides it with the real value).
USER_PLACEHOLDERS = frozenset({
    "<user words>", "<user words authorizing more>", "<failure>", "<reason>",
    "<ticket-id>", "<title>", "<objective>", "<policy>", "<vault>", "<project>", "<installed-skill>",
})
#: Placeholders the runtime substitutes in the stop it actually prints; they appear
#: in the registry row only as the documented shape of the command.
RUNTIME_PLACEHOLDERS = frozenset({"<check-id>", "<gate>", "<path>"})

STOP_REASONS: dict[str, tuple[str, str, str]] = {
    "NONE": (PAUSED, "Nothing stops the loop; continue with the next step.", _NEXT),
    # --- budgets (bounded_run_driver, correction_loop, sdd.py) ---
    "STAGE_TRANSITION_BUDGET_REACHED": (HUMAN, "The stage-transition budget is spent; ask the user whether to authorize more transitions.", _RAISE % "stage_transitions"),
    "EXECUTOR_CALL_BUDGET_REACHED": (HUMAN, "The executor-call budget is spent; ask the user whether to authorize more calls.", _RAISE % "executor_calls"),
    "RETRY_BUDGET_REACHED": (HUMAN, "The corrective-retry budget for this action is spent; ask the user to authorize one more retry or to reopen the stage.", _RAISE % "corrective_retries"),
    "TDD_SLICE_BUDGET_REACHED": (HUMAN, "The TDD-slice budget is spent; IMPLEMENT stays open. Ask the user to authorize more slices.", _RAISE % "tdd_slices"),
    "INVESTIGATION_BUDGET_REACHED": (HUMAN, "The investigation-expansion budget for this stage is spent; ask for one explicit expansion.", _RAISE % "investigation_expansions"),
    "REVIEW_CYCLE_BUDGET_REACHED": (HUMAN, "The review-cycle budget is spent; ask the user whether to authorize another REVIEW.", _RAISE % "review_cycles"),
    "CI_RUN_BUDGET_REACHED": (HUMAN, "The CI-run budget is spent; ask the user whether to authorize another CI run.", _RAISE % "ci_runs"),
    "EXTERNAL_MUTATION_REQUIRED": (HUMAN, "The next action mutates an external system (commit, push, tracker, backend); it needs separate explicit authorization outside the loop. `sdd.py next` continues the local delivery without it.", _NEXT),
    "BUDGET_REACHED": (HUMAN, "A LOCAL_DELIVERY total limit is spent; `sdd.py next` stops with the named budget reason and its exact `budget --raise <name>` command.", _NEXT),
    "COST_BUDGET_REACHED": (HUMAN, "The correction cost budget is spent; ask the user to authorize more cost or a cheaper tier, then `sdd.py next` (the stage's single corrective retry or its stop).", _NEXT),
    "CORRECTIVE_RETRY_EXHAUSTED": (HUMAN, "The corrective retry of this action was already used; ask the user to authorize one more retry.", _RAISE % "corrective_retries"),
    # --- executor / journal (action_journal, executor_launch via sdd.py) ---
    "EXECUTOR_TIMEOUT": (BLOCKED, "The worker timed out twice (the first timeout already got one reduced-context retry). Ask the user to raise the stage timeout in policies/EXECUTORS.md or authorize one more retry.", _RAISE % "corrective_retries"),
    "EXECUTOR_FAILED": (BLOCKED, "The worker process failed again after its retry; check `executor_launch.py preflight` for that executor, then authorize one more retry.", _RAISE % "corrective_retries"),
    "EXECUTOR_PROCESS_ENDED_WITHOUT_ARTIFACT": (PAUSED, "The worker ended without a final message; archive it as interrupted (the printed command), then `sdd.py next` prepares the single retry.", _NEXT),
    "ACTION_RECOVERY_REQUIRED": (BLOCKED, "The journal holds an open action; `sdd.py next` prints the recovery command for it.", _NEXT),
    "ACTION_BLOCKED": (BLOCKED, "The action was blocked; archive it with a reason (printed command), then `sdd.py next`.", _NEXT),
    "DIRTY_OR_INCONSISTENT_IDLE": (BLOCKED, "The idle journal carries stray evidence; archive it with a reason (printed command), then `sdd.py next`.", _NEXT),
    "JOURNAL_INCONSISTENT": (BLOCKED, "The journal combines evidence no command produces; block and archive it (printed command), then `sdd.py next`.", _NEXT),
    "ARTIFACT_PENDING": (BLOCKED, "A final-message file already exists for an undispatched action; block and archive the action, then `sdd.py next`.", _NEXT),
    "STATE_DESYNC": (BLOCKED, "STATE differs from both prepared hashes; `sdd.py next` prints the journal command that blocks and archives the action. Never hand-edit STATE.", _NEXT),
    "STATE_PATH_REQUIRED": (BLOCKED, "The journal's state commit has no STATE path; archive the action with archive-blocked and run `sdd.py next`.", _NEXT),
    "STATE_PATH_UNSAFE": (BLOCKED, "The journal's STATE path is not this worktree's canonical STATE (see `action_journal.py paths`); archive the action and run `sdd.py next`.", _NEXT),
    "STATE_COMMIT_FILE_MISSING": (BLOCKED, "STATE.md is missing or not a regular file; restore it, then run `sdd.py next`.", _NEXT),
    "STATE_INCONSISTENT": (BLOCKED, "STATE.md cannot be parsed, lacks required keys, or an accepted artifact changed; restore the last committed STATE (history), never repair it by hand, then check it with `sdd.py status`.", _STATUS),
    # --- contract / validation (sdd.py accept) ---
    "CONTRACT_INVALID": (PAUSED, "The worker result failed validation; it is classified invalid. `sdd.py next` archives it and prepares the single corrective retry with the errors.", _NEXT),
    "WORKER_BLOCKED": (HUMAN, "The worker reported blockers; ask the user and record the resolution: the stage is redispatched with it.", "{sdd} unblock --quote '<user words>'"),
    "CLARIFICATION_REQUIRED": (HUMAN, "Material questions are open; ask the user exactly the listed questions and record each answer with the printed `sdd.py answer --index` command.", _NEXT),
    "HUMAN_DECISION_REQUIRED": (HUMAN, "An acceptance check needs the user's decision; record it verbatim with the printed command (`waive` for a HUMAN check, `answer --check` for a requested decision). If the user refuses, the demand has an exit: the stop also prints `refusal_command` (`sdd.py abandon`).", _NEXT),
    "SCOPE_CHANGE_REQUIRED": (HUMAN, "The slice contract differs from the approved one; it is new scope. Record the user's approval of the current slice contracts.", "{sdd} approve-scope --quote '<user words>'"),
    "INVESTIGATION_BUDGET_EXCEEDED": (HUMAN, "The context budget of the manifest is exceeded; ask for an explicit expansion or narrow the sources.", _RAISE % "investigation_expansions"),
    "PROMPT_BUDGET_EXCEEDED": (HUMAN, "The worker prompt exceeds max_prompt_bytes even after reduction; ask the user to authorize a larger prompt.", "{sdd} budget --raise prompt_bytes --by 16384 --quote '<user words authorizing more>'"),
    "MANIFEST_INVALID": (BLOCKED, "The stage-context manifest was refused; the findings name the field. Fix the source data, then `sdd.py next`.", _NEXT),
    "JEV_GOVERNANCE_REQUIRED": (BLOCKED, "Automatic Jev consent is recorded but no governance fingerprint is: run `semantic_governor.py decide` for this ticket (or the user withdraws the consent in PROJECT_SETUP.md), then `sdd.py next` rebuilds the manifest.", _NEXT),
    # --- baseline (sdd.py next) ---
    "BASELINE_DRIFT_EXTERNAL": (HUMAN, "HEAD or branch changed outside the agent; ask the user whether to continue on the new baseline and record the answer (it captures the new baseline).", _REBASELINE),
    "PREEXISTING_FILE_MODIFIED": (HUMAN, "A protected pre-existing file changed during the demand; ask the user. Never restore it with Git reset/checkout. Accepting the change records the answer and captures the new hashes.", _REBASELINE),
    # --- gates (bounded_run_driver, sdd.py gate) ---
    "FOCUSED_TESTS_FAILED": (BLOCKED, "Focused tests failed; reopen IMPLEMENT for a corrective slice with the user's agreement.", _REOPEN),
    "FORMAT_FAILED": (BLOCKED, "The formatter failed; reopen IMPLEMENT for a corrective slice with the user's agreement.", _REOPEN),
    "ANALYZE_FAILED": (BLOCKED, "Static analysis failed; reopen IMPLEMENT for a corrective slice with the user's agreement.", _REOPEN),
    "CI_FAILED": (BLOCKED, "CI failed; CI is never retried automatically. Reopen IMPLEMENT with the user's agreement.", _REOPEN),
    "CI_TIMEOUT": (HUMAN, "CI timed out; it blocks advancement. Ask the user whether to run it once more (raise its timeout in GATES.md first if needed).", "{sdd} gate --name ci --rerun --quote '<user words>'"),
    "GATE_TIMEOUT": (HUMAN, "A gate command timed out; it blocks advancement. Raising its timeout in GATES.md re-runs it on the next `sdd.py next`; otherwise ask the user to confirm one more run with the printed `gate --rerun` command.", _NEXT),
    "REVIEW_BLOCKED": (BLOCKED, "REVIEW reported a blocker; reopen IMPLEMENT with the user's agreement.", _REOPEN),
    "REVIEW_CHANGES_REQUIRED": (HUMAN, "REVIEW requires changes; reopen IMPLEMENT for a corrective slice with the user's agreement.", _REOPEN),
    "OWNERSHIP_VIOLATION": (HUMAN, "REVIEW found writes outside the agent-owned paths; ask the user. Accepting them records the answer and redispatches REVIEW; otherwise reopen IMPLEMENT with `sdd.py reopen`.", _REBASELINE),
    "GATE_COMMAND_UNCONFIGURED": (BLOCKED, "A required gate has no verified command in policies/GATES.md; configure it (`detect_stack.py` suggests one; run it once). Editing GATES.md during a demand then needs the user's confirmation once, which `sdd.py next` prints as `confirm-policy --name gates` before the gate runs.", _NEXT),
    "GATE_CONFIRMATION_REQUIRED": (HUMAN, "GATES.md marks this gate NOT_APPLICABLE; record the user's explicit confirmation with the printed `gate --not-applicable` command.", _NEXT),
    "CONTROLLER_POLICY_CHANGED_DURING_DEMAND": (HUMAN, "policies/GATES.md or EXECUTORS.md no longer matches the hash pinned at `sdd.py start`: its gate commands run on the host and it chooses the worker binary, so a worker with write access could have changed it. Show the user the diff; only their confirmation re-pins it.", _CONFIRM_POLICY),
    "CONTROLLER_POLICY_UNPINNED": (HUMAN, "A controller policy file has no pinned SHA-256 for this demand (started before pinning existed), so an edit cannot be attributed to the owner. Review each file with the user and record their confirmation; no gate runs until then.", _CONFIRM_POLICY),
    "CONTROLLER_POLICY_CONFIRMATION_REQUIRED": (HUMAN, "Confirming a controller policy change needs the user's literal words; ask them and record the answer with the printed command.", _CONFIRM_POLICY),
    "CONTROLLER_POLICY_UNREADABLE": (BLOCKED, "A controller policy file (policies/GATES.md or EXECUTORS.md) is missing or unreadable, so its SHA-256 cannot be pinned and confirming it would pin nothing; Git cannot restore it (the Obsidian container is not a Git repository and a `--local-storage` controller is excluded from Git). Run `restore_commands` in order: `sdd.py restore-policy --name <policy> --skill <installed-skill>` recreates it from the installed skill's template (verified against INSTALL_MANIFEST.json) without ending the demand, `sdd.py confirm-policy` records the user's confirmation of the recreated content, then `sdd.py next`. When the template changed since the install, `reinstall_commands` is the fallback: `sdd.py abandon`, `install_project.py --upgrade` dry run and `--apply`, `confirm-policy`, `sdd.py start`. Reconfigure the gate rows of a recreated GATES.md before the first gate.", "{sdd} restore-policy --name <policy> --skill <installed-skill>"),
    "CONTROLLER_WRITABLE_BY_WORKER": (BLOCKED, "This stage runs a writing worker and the controller physically lives inside the repository that worker can write (a `--local-storage` install, with or without an Obsidian binding), so it could rewrite the gate commands, the executor policy, the controller's runtime, STATE or the journal together with every hash they are checked against — detection has no anchor the worker cannot reach. The dispatch is refused, also for an action prepared before the check applied. `migrate_to_vault.py` is not the exit (it keeps `runtime/*.py` and `policies/` in the repository); the stop prints the whole sequence in `exit_commands`: `sdd.py abandon` (the user's words; an undispatched action is archived), `install_project.py --obsidian-vault <vault> --obsidian-project <project>` dry run then `--apply`, `mv` of the in-repository `.hermes/orchestration` (and `.hermes.md`) into `<vault>/<project>/.hermes-local-controller-backup/`, and `start` with the new controller's `sdd.py`. Read-only stages keep running meanwhile.", _ABANDON),
    "STATE_MODIFIED_DURING_ACTION": (BLOCKED, "STATE changed while an action was open, outside the journal's state commit: in `--local-storage` mode a writing worker can reach STATE. The result is never folded in; `sdd.py abandon` archives the open action as evidence and returns STATE to IDLE with every per-demand block reset, reverting nothing in the worktree, so `sdd.py start` can re-run the demand. Never repair STATE by hand.", _ABANDON),
    "DONE_GATES_NOT_PASSED": (BLOCKED, "DONE needs focused tests, format, analysis, review and CI (or DISABLED_BY_PROJECT_POLICY) to pass; `sdd.py next` prints the missing gate.", _NEXT),
    "FOCUSED_TESTS_REQUIRED": (BLOCKED, "Format runs only after focused tests pass; run the focused-tests gate first.", _NEXT),
    "TEST_AND_FORMAT_REQUIRED": (BLOCKED, "Analysis runs only after focused tests and format pass.", _NEXT),
    "TEST_FORMAT_AND_ANALYZE_REQUIRED": (BLOCKED, "REVIEW runs only after focused tests, format and analysis pass.", _NEXT),
    "REVIEW_PREREQUISITES_REQUIRED": (BLOCKED, "CI runs only after the local gates pass and REVIEW is APPROVED.", _NEXT),
    "CI_DISABLED_BY_PROJECT_POLICY": (PAUSED, "CI is disabled by project policy; record ci as DISABLED_BY_PROJECT_POLICY (never PASS) and evaluate DONE.", _NEXT),
    "IMPLEMENTATION_SLICES_NOT_GREEN": (BLOCKED, "Gates need every planned slice GREEN; finish IMPLEMENT first.", _NEXT),
    "IMPLEMENTATION_CURSOR_INVALID": (BLOCKED, "Completed slices do not advance in planned order; regenerate the snapshot with `sdd.py snapshot` and replan.", "{sdd} snapshot"),
    # --- bounded run driver / planner ---
    "PLAN_STALE": (PAUSED, "The plan no longer matches STATE or the worktree; regenerate the snapshot and plan (schema 2 replans without a new approval).", "{sdd} snapshot"),
    "BLOCKED": (BLOCKED, "STATE has blockers or recovery is BLOCKED; `sdd.py status` names them and `sdd.py next` prints the recovery.", _NEXT),
    "HUMAN_REQUIRED": (HUMAN, "The next planned action needs a human decision (protected file, scope, architecture or external mutation); `sdd.py next` stops at it with the exact command that records the answer.", _NEXT),
    "MANUAL_ACTION_COMPLETE": (PAUSED, "MANUAL mode: the authorized progress is done. A 'continue' from the user authorizes progress up to the next human checkpoint.", _NEXT),
    "LOOP_PAUSED": (PAUSED, "The loop is PAUSED: nothing new starts. Resume only on the user's explicit request.", "{sdd} resume --quote '<user words>'"),
    "EXECUTOR_UNAVAILABLE": (BLOCKED, "The prepared action's executor is not on PATH; nothing was dispatched. Ask the user to install it or to select another executor for the stage in policies/EXECUTORS.md; then `sdd.py next` dispatches, or prints `sdd.py reprepare` (archives the undispatched action and refunds its call).", _NEXT),
    "DONE": (DONE, "DONE: engineering validated. Present a commit proposal (commit and push need separate authorization), then close the demand to return STATE to IDLE.", "{sdd} close"),
    "PLAN_COMPLETE": (PAUSED, "Every planned action ran; regenerate the snapshot and plan for the next checkpoint.", "{sdd} snapshot"),
    "PLAN_EMPTY": (PAUSED, "The plan has no action to run; `sdd.py next` shows why (usually a human checkpoint).", _NEXT),
    "SEQUENCE_SKIPPED": (BLOCKED, "STATE's next action is not the plan's next action; regenerate the snapshot and plan.", "{sdd} snapshot"),
    "LOOP_INACTIVE": (PAUSED, "The bounded loop is not active (MANUAL or PAUSED); `sdd.py next` drives MANUAL progress.", _NEXT),
    "NEXT_HUMAN_CHECKPOINT": (PAUSED, "The projection reached the next human checkpoint; `sdd.py next` names it.", _NEXT),
    "RECOVERY_RECONCILIATION_REQUIRED": (PAUSED, "A pending action must be reconciled first; `sdd.py next` prints the recovery command.", _NEXT),
    "IDLE_NO_DEMAND": (PAUSED, "No demand is active; start one from the user's request.", "{sdd} start --ticket <ticket-id> --title '<title>' --objective '<objective>'"),
    # --- command refusals of sdd.py (raised as SddError: the `status` of an exit-2 payload) ---
    "ABANDON_REQUESTED": (HUMAN, "The user refused the pending decision or asked to drop the demand; `sdd.py abandon` needs their literal words and a reason. It archives the demand to IDLE with the refusal recorded, so a new `start` is accepted.", _ABANDON),
    "DEMAND_ACTIVE": (BLOCKED, "A demand is already active; one controller drives one demand. `sdd.py next` continues it, `sdd.py close` ends a DONE one and `sdd.py abandon` drops a non-DONE one with the user's words.", _NEXT),
    "TICKET_INVALID": (BLOCKED, "The ticket id is not one safe path component (1-64 letters, digits, '.', '_' or '-'); repeat `sdd.py start` with a valid id.", "{sdd} start --ticket <ticket-id> --title '<title>' --objective '<objective>'"),
    "STEP_MISMATCH": (BLOCKED, "The command does not apply to the current stage, journal or evidence; the message says which precondition failed. `sdd.py next` prints the step that does apply.", _NEXT),
    "WAIVE_REQUIRES_HUMAN_CHECK": (HUMAN, "Only a HUMAN check is waived by a quote; an AGENT/COMMAND check needs an answered `request-decision` whose recorded answer is the waiver quote. Ask the user first.", "{sdd} request-decision --check <check-id> --reason '<reason>'"),
    "AGENT_OWNED_PATH_UNSAFE": (BLOCKED, "An agent-owned path has a segment starting with '-' and would become an option of a gate command; drop it from STATE ownership with the printed `sdd.py disown` command (the file itself is left untouched), then `sdd.py next`.", "{sdd} disown --path <path> --reason '<reason>'"),
    # --- correction loop ---
    "NO_NEW_HYPOTHESIS": (HUMAN, "The proposed correction repeats a tried hypothesis; ask the user for a new, specific one and record it (the stage is redispatched with it).", "{sdd} unblock --quote '<user words>'"),
    "NO_PROGRESS": (HUMAN, "The last correction changed nothing observable; investigate the failure with the user and record the new direction (the stage is redispatched with it).", "{sdd} unblock --quote '<user words>'"),
    # --- legacy bounded_loop_driver (deprecated: use sdd.py next) ---
    "PLAN_CURSOR_MISMATCH": (PAUSED, "Legacy driver: the plan cursor does not match STATE; use `sdd.py next`.", _NEXT),
    "MODE_NOT_BOUNDED_AUTO": (PAUSED, "Legacy driver: the loop is not BOUNDED_AUTO; use `sdd.py next`.", _NEXT),
    "HUMAN_APPROVAL_REQUIRED": (HUMAN, "STATE records a pending human approval; ask the user, then `sdd.py next`.", _NEXT),
    "NO_NEXT_ACTION": (PAUSED, "Legacy driver: nothing left in the plan; use `sdd.py next`.", _NEXT),
}

#: Plain-language explanation for each code, in Brazilian Portuguese: what happened,
#: why the controller stopped, and what the user does next. Presentation only; the
#: kind, next_step and next_command above stay authoritative.
EXPLANATIONS: dict[str, tuple[str, str, str]] = {
    "NONE": ("Nada interrompeu o fluxo.", "Não há bloqueio, pausa nem decisão pendente.", "Seguir para o próximo passo com sdd.py next."),
    "STAGE_TRANSITION_BUDGET_REACHED": ("O limite de mudanças de fase desta demanda acabou.", "O orçamento evita que a entrega avance sem fim sem você acompanhar.", "Decidir se autoriza mais transições; o comando de aumento já vem pronto."),
    "EXECUTOR_CALL_BUDGET_REACHED": ("O limite de chamadas aos executores (Claude/Codex) acabou.", "Cada chamada tem custo; o orçamento mantém o gasto sob seu controle.", "Decidir se autoriza mais chamadas."),
    "RETRY_BUDGET_REACHED": ("As tentativas de correção desta ação acabaram.", "Repetir sem limite costuma esconder um problema que precisa de outra abordagem.", "Autorizar mais uma tentativa ou reabrir a fase com uma nova direção."),
    "TDD_SLICE_BUDGET_REACHED": ("O limite de fatias de TDD foi atingido; a implementação continua aberta.", "As fatias foram planejadas; passar disso indica escopo maior que o previsto.", "Autorizar mais fatias ou revisar o plano."),
    "INVESTIGATION_BUDGET_REACHED": ("O limite de expansões de investigação desta fase acabou.", "O contexto enviado aos agentes é limitado para manter custo e foco.", "Autorizar uma expansão explícita ou estreitar o que será lido."),
    "REVIEW_CYCLE_BUDGET_REACHED": ("O limite de ciclos de revisão acabou.", "Muitas rodadas de revisão indicam problema de fundo que pede sua atenção.", "Decidir se autoriza mais uma revisão."),
    "CI_RUN_BUDGET_REACHED": ("O limite de execuções de CI acabou.", "O CI não é repetido automaticamente para não mascarar falhas.", "Decidir se autoriza mais uma execução de CI."),
    "EXTERNAL_MUTATION_REQUIRED": ("O próximo passo altera algo fora da máquina (commit, push, tracker ou backend).", "Mudanças externas sempre exigem autorização separada sua.", "Autorizar fora do loop, se quiser; a entrega local continua sem isso."),
    "BUDGET_REACHED": ("Um limite total da entrega local foi atingido.", "Os limites mantêm a execução automática dentro do que você aprovou.", "Ver qual orçamento acabou em sdd.py next e decidir se aumenta."),
    "COST_BUDGET_REACHED": ("O limite de custo das correções acabou.", "Correções extras usam modelos pagos; o teto protege seu orçamento.", "Autorizar mais custo ou um nível mais barato."),
    "CORRECTIVE_RETRY_EXHAUSTED": ("A única tentativa de correção desta ação já foi usada.", "Cada ação tem uma correção automática; mais que isso precisa de você.", "Autorizar mais uma tentativa, se fizer sentido."),
    "EXECUTOR_TIMEOUT": ("O executor excedeu o tempo limite duas vezes.", "A primeira vez já teve uma nova tentativa com contexto reduzido.", "Aumentar o tempo da fase em EXECUTORS.md ou autorizar mais uma tentativa."),
    "EXECUTOR_FAILED": ("O processo do executor falhou de novo após a nova tentativa.", "Pode ser instalação, login ou modelo indisponível no executor.", "Rodar o preflight do executor e então autorizar nova tentativa."),
    "EXECUTOR_PROCESS_ENDED_WITHOUT_ARTIFACT": ("O executor terminou sem entregar a resposta final.", "Sem o resultado, não há o que validar.", "Arquivar como interrompido (comando pronto) e deixar preparar a nova tentativa."),
    "ACTION_RECOVERY_REQUIRED": ("Há uma ação aberta no diário de execução.", "Uma execução anterior parou no meio; é preciso reconciliar antes de seguir.", "Rodar sdd.py next, que mostra o comando de recuperação."),
    "ACTION_BLOCKED": ("A ação atual foi bloqueada.", "O controlador não seguirá com uma ação que não pôde ser concluída com segurança.", "Arquivar com um motivo (comando pronto) e seguir."),
    "DIRTY_OR_INCONSISTENT_IDLE": ("O diário parado contém evidências soltas.", "Restos de uma execução anterior poderiam confundir a próxima.", "Arquivar com um motivo (comando pronto) e seguir."),
    "JOURNAL_INCONSISTENT": ("O diário de execução tem uma combinação impossível de evidências.", "Nenhum comando produz esse estado; é mais seguro parar.", "Bloquear e arquivar a ação (comando pronto) e seguir."),
    "ARTIFACT_PENDING": ("Já existe uma resposta final para uma ação que não foi despachada.", "Isso indica um arquivo inesperado; ele não será aproveitado.", "Bloquear e arquivar a ação, depois seguir."),
    "STATE_DESYNC": ("O estado da demanda difere do esperado pela ação preparada.", "O estado pode ter sido alterado por fora; nunca é corrigido à mão.", "Seguir o comando de arquivamento mostrado por sdd.py next."),
    "STATE_PATH_REQUIRED": ("O registro da ação não indica onde está o estado.", "Sem o caminho, o controlador não consegue confirmar o que mudou.", "Arquivar a ação como bloqueada e seguir."),
    "STATE_PATH_UNSAFE": ("O caminho do estado no diário não é o desta worktree.", "Gravar em outro lugar poderia corromper outra entrega.", "Arquivar a ação e seguir."),
    "STATE_COMMIT_FILE_MISSING": ("O arquivo de estado (STATE.md) sumiu ou não é um arquivo normal.", "Sem ele, o controlador não sabe em que ponto a entrega está.", "Restaurar o STATE.md e seguir."),
    "STATE_INCONSISTENT": ("O STATE.md não pôde ser lido ou um artefato aceito mudou.", "Estado corrompido não é consertado à mão para não perder rastreabilidade.", "Restaurar o último estado registrado e conferir com sdd.py status."),
    "CONTRACT_INVALID": ("A resposta do executor não passou na validação do contrato.", "Resultados fora do formato acordado não são aceitos.", "Seguir: a única correção automática já vem preparada com os erros."),
    "WORKER_BLOCKED": ("O executor relatou um impedimento para continuar.", "Ele precisa de uma informação ou decisão que não tem.", "Responder ao impedimento; a fase será refeita com sua resposta."),
    "CLARIFICATION_REQUIRED": ("Há perguntas importantes em aberto sobre o pedido.", "Seguir sem a resposta poderia entregar algo diferente do esperado.", "Responder às perguntas listadas; cada resposta tem seu comando."),
    "HUMAN_DECISION_REQUIRED": ("Um critério de aceite precisa da sua decisão.", "Esse critério foi definido como dependente de pessoa, não de teste.", "Registrar sua decisão, ou recusar e encerrar a demanda."),
    "SCOPE_CHANGE_REQUIRED": ("O contrato da fatia mudou em relação ao aprovado.", "Mudança de escopo não é aceita sem sua aprovação.", "Aprovar o novo escopo, ou pedir para manter o original."),
    "INVESTIGATION_BUDGET_EXCEEDED": ("O contexto reunido passou do limite permitido.", "Contexto grande demais encarece e dispersa os agentes.", "Autorizar uma expansão ou reduzir as fontes."),
    "PROMPT_BUDGET_EXCEEDED": ("A instrução ao executor ficou grande demais, mesmo reduzida.", "O limite evita custo alto e respostas piores.", "Autorizar um prompt maior, se fizer sentido."),
    "MANIFEST_INVALID": ("O manifesto de contexto da fase foi recusado.", "Ele aponta arquivos, hashes ou skills que não conferem.", "Corrigir o dado indicado nos achados e seguir."),
    "JEV_GOVERNANCE_REQUIRED": ("Falta a decisão do Jev para esta fase.", "Você consentiu com o Jev automático, mas ele ainda não classificou este ticket.", "Rodar a decisão do Jev, ou retirar o consentimento no setup, e seguir."),
    "BASELINE_DRIFT_EXTERNAL": ("O branch ou o último commit mudou por fora.", "Alguém alterou o repositório fora do controlador durante a entrega.", "Decidir se continua sobre a nova base."),
    "PREEXISTING_FILE_MODIFIED": ("Um arquivo protegido foi alterado durante a demanda.", "Arquivos que já existiam são protegidos; nada é revertido automaticamente.", "Decidir se aceita a mudança."),
    "FOCUSED_TESTS_FAILED": ("Os testes focados falharam.", "A mudança quebrou um comportamento coberto por testes.", "Reabrir a implementação para uma fatia de correção, com sua concordância."),
    "FORMAT_FAILED": ("A formatação de código falhou.", "O código não segue o formatador configurado no projeto.", "Reabrir a implementação para corrigir, com sua concordância."),
    "ANALYZE_FAILED": ("A análise estática encontrou problemas.", "O analisador do projeto aponta erros que bloqueiam a entrega.", "Reabrir a implementação para corrigir, com sua concordância."),
    "CI_FAILED": ("O CI falhou.", "O CI nunca é repetido automaticamente para não esconder falhas.", "Reabrir a implementação para corrigir, com sua concordância."),
    "CI_TIMEOUT": ("O CI excedeu o tempo limite.", "Sem resultado do CI, a entrega não avança.", "Decidir se roda o CI mais uma vez."),
    "GATE_TIMEOUT": ("Um gate de verificação excedeu o tempo limite.", "Sem resultado, o gate bloqueia o avanço.", "Aumentar o tempo do gate em GATES.md ou confirmar nova execução."),
    "REVIEW_BLOCKED": ("A revisão encontrou um bloqueio.", "Há um problema que impede aceitar a entrega como está.", "Reabrir a implementação com sua concordância."),
    "REVIEW_CHANGES_REQUIRED": ("A revisão pediu mudanças.", "O revisor encontrou pontos que precisam ser corrigidos antes de concluir.", "Reabrir a implementação para uma fatia de correção."),
    "OWNERSHIP_VIOLATION": ("A revisão encontrou alterações fora dos arquivos permitidos.", "Os agentes só podem escrever nos caminhos que receberam.", "Decidir se aceita essas alterações ou reabre a implementação."),
    "GATE_COMMAND_UNCONFIGURED": ("Um gate obrigatório não tem comando configurado.", "Sem comando verificado, não dá para provar que a entrega passa.", "Configurar o gate em GATES.md (o detect_stack sugere) e seguir."),
    "GATE_CONFIRMATION_REQUIRED": ("Um gate está marcado como não aplicável.", "Pular uma verificação exige sua confirmação explícita.", "Confirmar com o comando mostrado, se concordar."),
    "CONTROLLER_POLICY_CHANGED_DURING_DEMAND": ("Uma política do controlador mudou durante a demanda.", "Esses arquivos definem comandos e executores; um agente poderia tê-los alterado.", "Ver a diferença e confirmar, se a mudança for sua."),
    "CONTROLLER_POLICY_UNPINNED": ("Uma política do controlador não tem hash registrado nesta demanda.", "Sem o hash, não dá para saber quem alterou o arquivo.", "Revisar o arquivo e confirmar."),
    "CONTROLLER_POLICY_CONFIRMATION_REQUIRED": ("A confirmação da política precisa das suas palavras.", "A confirmação fica registrada com o que você disse.", "Responder e registrar com o comando mostrado."),
    "CONTROLLER_POLICY_UNREADABLE": ("Uma política do controlador sumiu ou não pode ser lida.", "Sem o arquivo, não há o que confirmar; ele precisa ser recriado.", "Restaurar a partir da skill instalada e confirmar o conteúdo."),
    "CONTROLLER_WRITABLE_BY_WORKER": ("O controlador está dentro do repositório que o executor pode escrever.", "Um executor com escrita poderia alterar o próprio controlador sem ser notado.", "Encerrar a demanda e mover o controlador para o Obsidian (passos prontos)."),
    "STATE_MODIFIED_DURING_ACTION": ("O estado mudou enquanto uma ação estava aberta.", "Mudança fora do diário indica escrita indevida; o resultado é descartado.", "Encerrar a demanda (nada é revertido) e começar de novo."),
    "DONE_GATES_NOT_PASSED": ("Ainda falta passar algum gate para concluir.", "Concluir exige testes, formatação, análise, revisão e CI aprovados.", "Seguir: sdd.py next mostra qual gate falta."),
    "FOCUSED_TESTS_REQUIRED": ("A formatação só roda depois dos testes focados.", "Os gates têm ordem fixa.", "Rodar primeiro o gate de testes focados."),
    "TEST_AND_FORMAT_REQUIRED": ("A análise só roda depois de testes e formatação.", "Os gates têm ordem fixa.", "Concluir testes e formatação primeiro."),
    "TEST_FORMAT_AND_ANALYZE_REQUIRED": ("A revisão só roda depois de testes, formatação e análise.", "Os gates têm ordem fixa.", "Concluir os gates locais primeiro."),
    "REVIEW_PREREQUISITES_REQUIRED": ("O CI só roda depois dos gates locais e da revisão aprovada.", "Os gates têm ordem fixa.", "Concluir a revisão primeiro."),
    "CI_DISABLED_BY_PROJECT_POLICY": ("O CI está desativado pela política do projeto.", "Ele fica registrado como desativado, nunca como aprovado.", "Seguir para avaliar a conclusão."),
    "IMPLEMENTATION_SLICES_NOT_GREEN": ("Nem todas as fatias planejadas estão verdes.", "Os gates só fazem sentido com a implementação completa.", "Terminar a implementação primeiro."),
    "IMPLEMENTATION_CURSOR_INVALID": ("As fatias concluídas não seguem a ordem planejada.", "A ordem garante que cada fatia parta da anterior.", "Gerar um novo snapshot e replanejar."),
    "PLAN_STALE": ("O plano não corresponde mais ao estado atual.", "Algo mudou desde que o plano foi feito.", "Gerar um novo snapshot e plano (sem nova aprovação)."),
    "BLOCKED": ("A demanda está bloqueada.", "Há um impedimento registrado no estado.", "Ver o motivo em sdd.py status e a recuperação em sdd.py next."),
    "HUMAN_REQUIRED": ("A próxima ação planejada precisa de uma decisão sua.", "Envolve arquivo protegido, escopo, arquitetura ou mudança externa.", "Responder com o comando que sdd.py next mostra."),
    "MANUAL_ACTION_COMPLETE": ("No modo manual, o passo autorizado terminou.", "No modo manual, nada avança sem você pedir.", "Dizer continuar para avançar até a próxima decisão."),
    "LOOP_PAUSED": ("O loop está pausado.", "Você pediu pausa; nada novo começa.", "Retomar quando quiser, com seu pedido explícito."),
    "EXECUTOR_UNAVAILABLE": ("O executor desta fase não está instalado.", "Nada foi enviado; o binário não está no PATH.", "Instalar o executor ou escolher outro em EXECUTORS.md."),
    "DONE": ("A entrega foi concluída e validada.", "Todos os gates e a revisão passaram.", "Ver a proposta de commit (precisa de autorização) e fechar a demanda."),
    "PLAN_COMPLETE": ("Todas as ações planejadas rodaram.", "O plano atual acabou.", "Gerar novo snapshot e plano para o próximo ponto."),
    "PLAN_EMPTY": ("O plano não tem ação para rodar.", "Normalmente há uma decisão humana pendente.", "Ver o motivo em sdd.py next."),
    "SEQUENCE_SKIPPED": ("A próxima ação do estado não é a próxima do plano.", "O plano ficou fora de sincronia.", "Gerar novo snapshot e plano."),
    "LOOP_INACTIVE": ("O loop automático não está ativo.", "O modo é manual ou está pausado.", "Usar sdd.py next para avançar manualmente."),
    "NEXT_HUMAN_CHECKPOINT": ("A execução chegou ao próximo ponto de decisão humana.", "Esse ponto foi definido no plano aprovado.", "Ver qual é a decisão em sdd.py next."),
    "RECOVERY_RECONCILIATION_REQUIRED": ("Uma ação pendente precisa ser reconciliada.", "Uma execução anterior não terminou de forma limpa.", "Seguir o comando de recuperação de sdd.py next."),
    "IDLE_NO_DEMAND": ("Nenhuma demanda está ativa.", "O controlador está ocioso.", "Iniciar uma demanda a partir do seu pedido."),
    "ABANDON_REQUESTED": ("Você recusou a decisão ou pediu para encerrar a demanda.", "Encerrar exige suas palavras e um motivo, que ficam registrados.", "Confirmar o encerramento com o comando mostrado."),
    "DEMAND_ACTIVE": ("Já existe uma demanda ativa.", "Um controlador conduz uma demanda por vez.", "Continuar, fechar a concluída ou encerrar a atual."),
    "TICKET_INVALID": ("O identificador do ticket não é válido.", "Ele vira nome de pasta; só letras, números, ponto, hífen e sublinhado.", "Iniciar de novo com um identificador válido."),
    "STEP_MISMATCH": ("O comando não se aplica ao momento atual.", "A fase, o diário ou as evidências não permitem esse passo agora.", "Usar sdd.py next, que mostra o passo válido."),
    "WAIVE_REQUIRES_HUMAN_CHECK": ("Só critérios humanos podem ser dispensados com uma frase.", "Critérios de agente ou comando precisam de decisão registrada antes.", "Pedir a decisão primeiro com o comando mostrado."),
    "AGENT_OWNED_PATH_UNSAFE": ("Um caminho dos agentes começa com hífen.", "Ele seria lido como opção de um comando de gate.", "Retirar o caminho da posse dos agentes (o arquivo não é alterado)."),
    "NO_NEW_HYPOTHESIS": ("A correção proposta repete uma hipótese já tentada.", "Repetir a mesma ideia não traz progresso.", "Sugerir uma nova hipótese específica."),
    "NO_PROGRESS": ("A última correção não mudou nada observável.", "Insistir no mesmo caminho só gasta tempo e custo.", "Investigar a falha e indicar uma nova direção."),
    "PLAN_CURSOR_MISMATCH": ("O driver antigo encontrou o plano fora de sincronia.", "Esse driver está obsoleto.", "Usar sdd.py next."),
    "MODE_NOT_BOUNDED_AUTO": ("O driver antigo exige o modo automático limitado.", "Esse driver está obsoleto.", "Usar sdd.py next."),
    "HUMAN_APPROVAL_REQUIRED": ("Há uma aprovação humana pendente.", "O estado registra que você precisa aprovar antes de seguir.", "Responder e então seguir com sdd.py next."),
    "NO_NEXT_ACTION": ("O driver antigo não tem mais ações no plano.", "Esse driver está obsoleto.", "Usar sdd.py next."),
}

#: Codes a runtime emits through a computed value rather than a literal (journal
#: error codes surfaced by ``recover`` when the STATE commit cannot be hashed).
DYNAMIC_STOP_REASONS = frozenset({"STATE_PATH_REQUIRED", "STATE_PATH_UNSAFE", "STATE_COMMIT_FILE_MISSING"})


def sdd_command() -> str:
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(SDD_SCRIPT))}"


def describe(code: str) -> dict[str, Any]:
    """Return {stop_reason, kind, next_step, next_command, explanation} for a registered code."""
    kind, step, command = STOP_REASONS.get(code, STOP_REASONS["BLOCKED"])
    return {
        "stop_reason": code,
        "kind": kind,
        "next_step": step,
        "next_command": command.format(sdd=sdd_command()),
        "explanation": explain(code),
    }


def explain(code: str) -> dict[str, str]:
    """Return the plain-language {what, why, action} for a code (unknown codes fall back to BLOCKED)."""
    what, why, action = EXPLANATIONS.get(code, EXPLANATIONS["BLOCKED"])
    return {"what": what, "why": why, "action": action}


def policy_table() -> str:
    """Markdown table rendered in LOOP_POLICY.md §9 (the test compares the code column)."""
    rows = ["| Stop reason | Kind | Next action |", "| --- | --- | --- |"]
    for code, (kind, step, command) in STOP_REASONS.items():
        action = f"{step} → `{command.replace('{sdd} ', 'sdd.py ')}`"
        rows.append(f"| `{code}` | {kind} | {action} |")
    return "\n".join(rows)


if __name__ == "__main__":  # pragma: no cover - documentation helper
    print(policy_table())
