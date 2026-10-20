"""Stop-reason rows kept outside the baseline-limited registry module ``stop_reasons``.

They are part of the same closed registry: ``stop_reasons`` merges them at import,
so ``tests/test_stop_reasons.py`` and LOOP_POLICY.md §9 see one table.
"""
from __future__ import annotations


def apply(stop_reasons: dict, explanations: dict, human: str, next_command: str) -> None:
    """Add the BUDGET_BELOW_PLAN registry and readable-table rows."""
    stop_reasons["BUDGET_BELOW_PLAN"] = (
        human,
        "The plan needs more TDD slices or executor calls than the budget allows; ask the user to raise the shortfall.",
        next_command,
    )
    explanations["BUDGET_BELOW_PLAN"] = (
        "O plano precisa de mais fatias de TDD ou chamadas ao executor do que o orçamento permite.",
        "A transição para a implementação não pode começar com déficit.",
        "Autorizar o aumento indicado nos comandos e seguir.",
    )


def apply_controller_policy(stop_reasons: dict, explanations: dict, HUMAN: str, BLOCKED: str, _CONFIRM_POLICY: str, _ABANDON: str) -> None:  # noqa: N803
    """Add the controller-policy pin and controller-isolation rows (both tables)."""
    stop_reasons.update({
        "CONTROLLER_POLICY_CHANGED_DURING_DEMAND": (HUMAN, "policies/GATES.md or EXECUTORS.md no longer matches the hash pinned at `sdd.py start`: its gate commands run on the host and it chooses the worker binary, so a worker with write access could have changed it. Show the user the diff; only their confirmation re-pins it.", _CONFIRM_POLICY),
        "CONTROLLER_POLICY_UNPINNED": (HUMAN, "A controller policy file has no pinned SHA-256 for this demand (started before pinning existed), so an edit cannot be attributed to the owner. Review each file with the user and record their confirmation; no gate runs until then.", _CONFIRM_POLICY),
        "CONTROLLER_POLICY_CONFIRMATION_REQUIRED": (HUMAN, "Confirming a controller policy change needs the user's literal words; ask them and record the answer with the printed command.", _CONFIRM_POLICY),
        "CONTROLLER_POLICY_UNREADABLE": (BLOCKED, "A controller policy file (policies/GATES.md or EXECUTORS.md) is missing or unreadable, so its SHA-256 cannot be pinned and confirming it would pin nothing; Git cannot restore it (the Obsidian container is not a Git repository and a `--local-storage` controller is excluded from Git). Run `restore_commands` in order: `sdd.py restore-policy --name <policy> --skill <installed-skill>` recreates it from the installed skill's template (verified against INSTALL_MANIFEST.json) without ending the demand, `sdd.py confirm-policy` records the user's confirmation of the recreated content, then `sdd.py next`. When the template changed since the install, `reinstall_commands` is the fallback: `sdd.py abandon`, `install_project.py --upgrade` dry run and `--apply`, `confirm-policy`, `sdd.py start`. Reconfigure the gate rows of a recreated GATES.md before the first gate.", "{sdd} restore-policy --name <policy> --skill <installed-skill>"),
        "CONTROLLER_WRITABLE_BY_WORKER": (BLOCKED, "This stage runs a writing worker and the controller physically lives inside the repository that worker can write (a `--local-storage` install, with or without an Obsidian binding), so it could rewrite the gate commands, the executor policy, the controller's runtime, STATE or the journal together with every hash they are checked against — detection has no anchor the worker cannot reach. The dispatch is refused, also for an action prepared before the check applied. `migrate_to_vault.py` is not the exit (it keeps `runtime/*.py` and `policies/` in the repository); the stop prints the whole sequence in `exit_commands`: `sdd.py abandon` (the user's words; an undispatched action is archived), `install_project.py --obsidian-vault <vault> --obsidian-project <project>` dry run then `--apply`, `mv` of the in-repository `.hermes/orchestration` (and `.hermes.md`) into `<vault>/<project>/.hermes-local-controller-backup/`, and `start` with the new controller's `sdd.py`. Read-only stages keep running meanwhile.", _ABANDON),
    })
    explanations.update({
        "CONTROLLER_POLICY_CHANGED_DURING_DEMAND": ("Uma política do controlador mudou durante a demanda.", "Esses arquivos definem comandos e executores; um agente poderia tê-los alterado.", "Ver a diferença e confirmar, se a mudança for sua."),
        "CONTROLLER_POLICY_UNPINNED": ("Uma política do controlador não tem hash registrado nesta demanda.", "Sem o hash, não dá para saber quem alterou o arquivo.", "Revisar o arquivo e confirmar."),
        "CONTROLLER_POLICY_CONFIRMATION_REQUIRED": ("A confirmação da política precisa das suas palavras.", "A confirmação fica registrada com o que você disse.", "Responder e registrar com o comando mostrado."),
        "CONTROLLER_POLICY_UNREADABLE": ("Uma política do controlador sumiu ou não pode ser lida.", "Sem o arquivo, não há o que confirmar; ele precisa ser recriado.", "Restaurar a partir da skill instalada e confirmar o conteúdo."),
        "CONTROLLER_WRITABLE_BY_WORKER": ("O controlador está dentro do repositório que o executor pode escrever.", "Um executor com escrita poderia alterar o próprio controlador sem ser notado.", "Encerrar a demanda e mover o controlador para o Obsidian (passos prontos)."),
    })
