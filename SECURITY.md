# Security Policy

## Supported versions

Only the latest release on `main` receives security fixes.

## Reporting a vulnerability

Please **do not** open a public issue for a vulnerability. Use GitHub's private reporting instead: **Security → Report a vulnerability** on this repository.

Relevant examples for this project:

- the installer writing outside the target repository, following a symlink, or modifying tracked files;
- a vault write escaping the bound `project_container`;
- a way to bypass human approval, budgets, protected-file ownership or the one-leaf-worker invariant;
- secrets or credentials exposed through state, journal, logs or worker prompts.

Include the version/commit, reproduction steps and impact. You will receive an acknowledgement, and a fix will be coordinated before public disclosure.
