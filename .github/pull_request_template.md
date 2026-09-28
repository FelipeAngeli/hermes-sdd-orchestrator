## Summary

<!-- What changes and why. -->

Closes #

## Type

- [ ] feat
- [ ] fix
- [ ] docs
- [ ] test / refactor

## Verification

- [ ] `python3 -m unittest discover -s tests -p 'test_*.py'` passes
- [ ] `python3 -m unittest discover -s skills/orchestrate/sdd-orchestrator/templates/.hermes/orchestration/tests -p 'test_*.py'` passes
- [ ] New behavior has a test that failed before the change (RED → GREEN)

## Invariants

- [ ] Payload stays language-neutral (no toolchain names outside `detect_stack.py` / `GATES.md` reference table)
- [ ] Installer remains idempotent and never modifies tracked files of the target
- [ ] No weakening of human approval, protected files, budgets or the one-leaf-worker rule
- [ ] README / `docs/ARCHITECTURE.md` updated if structure or sub-agents changed
