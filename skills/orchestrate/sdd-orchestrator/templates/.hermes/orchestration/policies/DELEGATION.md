# Delegation manager

The delegation manager is opt-in. An owner may copy this policy outside the repository to experiment, then pass that copy with `--config`; the shipped file remains disabled.

```json
{"schema_version":1,"enabled":false,"concurrency_enabled":false,"max_concurrent_worktrees":3,"max_children_per_parent":3,"max_depth":2,"default_token_budget":null,"bytes_per_token":4,"on_budget_exceeded":"BLOCK","session_binding":"EXPLICIT","worktree_management":"PREPARED_ONLY","auto_delegate_on_conflict":false,"on_parent_cancel":"CASCADE"}
```

Owner decisions, recorded here and enforced by the delegation code as it lands (PR 2–4; PR 1 only validates this file and contracts):
- **Worktrees:** only prepared worktrees may be used. The manager never creates, deletes or modifies a worktree.
- **Isolation:** each worktree keeps its own state, journal and temporary files.
- **Shared resources:** they are never written concurrently.
- **Default behavior:** concurrency needs both `enabled` and `concurrency_enabled`. With either flag off, the current one-leaf-worker rule is unchanged.
- **Hard ceilings:** 3 worktrees, 3 children and depth 2.
