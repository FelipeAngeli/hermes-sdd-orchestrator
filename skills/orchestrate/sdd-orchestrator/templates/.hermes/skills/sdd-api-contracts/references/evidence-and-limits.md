# Evidence rules and limits

- Cite the exact field, path and source for every divergence; a divergence without a citation is not reportable.
- Never invent a field, endpoint, status code, enum value or nullability to close a gap; an invented contract element is worse than an acknowledged gap.
- Report proven findings separately from unproven suspicions.
- Report an unresolvable divergence as a gap and request a decision from the human or the backend owner; do not choose a winner by plausibility.
- A green client test proves nothing about the contract when its fixtures were written from the client model rather than from a real server payload; flag such fixtures explicitly.
- Never call a live API without explicit authorization. A write or state-changing call always requires it, and an unauthorized environment is reported as a gap.
- Treat a read-only server checkout as reference material only, never as an editable workspace.
- In REVIEW, report divergences as findings and never repair a model, serializer, specification or server file; IMPLEMENT changes only the slice's editable paths.
