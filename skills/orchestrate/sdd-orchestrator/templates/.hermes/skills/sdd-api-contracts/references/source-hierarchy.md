# Source hierarchy

The sources disagree in practice. When they conflict, rank the evidence:

1. The deployed runtime contract, when the controller authorized observing it.
2. The specification served by the deployed environment, which describes what is live.
3. The server implementation source, which may be ahead of or behind what is deployed.
4. A specification file committed in the repository, which may be stale.
5. The client models, which are the subject under audit and never the arbiter.

Never resolve a conflict by choosing the convenient source. Record which source supports each claim, and treat a conflict between higher-ranked sources as a finding in its own right.

## Planning a change

When the change itself alters the contract, the plan names the authoritative source that will carry the new shape first, the order in which the other sources follow, and the window in which old and new clients coexist. A client that ships before the server accepts the new shape is a rollout defect, not a test failure.
