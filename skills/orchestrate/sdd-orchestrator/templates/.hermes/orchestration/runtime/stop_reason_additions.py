"""Additional stop-reason rows kept outside the baseline-limited registry module."""
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
