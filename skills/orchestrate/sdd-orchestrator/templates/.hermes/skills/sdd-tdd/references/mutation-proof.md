# Mutation proof: does the suite test behavior?

Hunt tests that cannot fail, excessive mocking, weak assertions, implementation-coupled tests and false positives, and prove each verdict by execution rather than by reading.

## Method

1. Capture the baseline before any mutation: record the workspace status and the exact command that reproduces it.
2. Triage the supplied tests and classify each one as healthy, always-green, false positive, weak assertion, over-mocked, implementation-coupled or mirror.
3. Run each candidate test unchanged first and record that it currently passes.
4. Prove every finding by mutation: apply one minimal change to the production code the test claims to cover — invert a condition, change a returned value, delete an effect, or alter an error mapping.
5. Revert each mutation before interpreting the result, then rerun the test and record the exact command, exit code and output.
6. Confirm the workspace is byte-identical to the captured baseline before returning; report any residue as a blocker.
7. Report missing coverage per boundary for the behavior in scope, naming the rule left unprotected.

## Evidence rules

- A test that stays green against broken production code is a proven false positive; a test that turns red is healthy and is removed from the findings.
- One mutation at a time: two simultaneous mutations cannot attribute a result.
- Mutate only production code the test claims to cover; never mutate the test to observe its reaction.
- Revert after every mutation, including when the outcome matched the expectation and when the action is aborted early.
- Report proven findings separately from unproven suspicions.
- Respect the mutation budget; when it is exhausted, report what was proven and what remains unverified.
- Never regenerate snapshots or golden files to resolve a red result; the red result is the evidence.
- Never weaken a gate or convert a disabled gate into PASS.
