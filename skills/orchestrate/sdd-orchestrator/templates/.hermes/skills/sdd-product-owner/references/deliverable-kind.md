# Deliverable kind and implementation scope

## Classification

Every SPECIFY result records two facts in `context_assessment.facts`, each with a literal quote from the request as evidence:

- `deliverable_kind`: `CODE` (working software changes), `DECISION_DOC` (an ADR, spike report or decision record and no product code), or `BOTH` (decisions recorded and then implemented).
- `implementation_in_scope`: `true` when the request asks for code to be written or changed, `false` otherwise.

Write them as statements such as `deliverable_kind: BOTH` with evidence `request: "orquestre essa implementação ... fechar as decisões"`.

## Reading the request

- The verb carries scope. "Implement", "build", "fix", "orchestrate this implementation" keep code in scope even when the objective also mentions decisions, design or alignment.
- "Decide", "evaluate", "write an ADR", "spike", "compare options" without an implementation verb indicate `DECISION_DOC`.
- A request that asks to decide and then build is `BOTH`: the decision is recorded first and implementation follows in the same demand.
- Repetition is evidence: a requester who restates "do only what I asked" points back at the original verb, not at a narrower reading.

## One material question

When quotes conflict and the two readings change what is delivered, return exactly one material unresolved question routed to CLARIFY. State both readings, the evidence for each, and the default the worker will follow if the requester simply says "continue". Never ask a question the request already answers, and never split one ambiguity into several questions.

## Failure this prevents

A request to orchestrate an implementation whose objective mentioned "closing decisions" was specified as documentation only, with a rule that code could not start before a separate approval. The requester asked four times where the code was. The verb was the evidence, and it was ignored.
