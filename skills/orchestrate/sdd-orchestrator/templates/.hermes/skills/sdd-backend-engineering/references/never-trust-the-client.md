# Never trust the client

Everything that runs on the user's side (browser, mobile app, CLI, the user) can be changed, maliciously or by accident. The backend must behave as if the client did not exist. Audit first; fix only after the requester approves, item by item. `security-config-and-limits.md` holds the general authorization and secret rules; this reference is the audit procedure.

## Quick reference

| May reach the client | Must never reach the client |
| --- | --- |
| A public/anon key designed for clients, protected by correct row-level security | Service-role keys and backend secrets |
| Publishable keys of SDKs built for the client | Payment gateway API tokens |
| | LLM provider keys and any paid-API key |

Prefixes that put a variable in the client bundle: `NEXT_PUBLIC_` (Next.js), `VITE_` (Vite), `EXPO_PUBLIC_` (Expo), `REACT_APP_` (Create React App), `PUBLIC_` (SvelteKit/Astro), `NUXT_PUBLIC_` (Nuxt).

## Procedure

### 1. Business rules in the client

1. List every rule that exists only in the client and every route that receives a value the server should compute: price, total, discount, shipping, plan limits, available quantity, role/permission (`role`, `isAdmin`), owner `user_id`, validations.
2. Describe the concrete attack for each (for example: edit the checkout payload in developer tools and pay 1 instead of 79).
3. Propose the fix: the server receives identifiers only and recomputes everything from its data; the owner comes from the authenticated session, never from the request body.

### 2. Keys and secrets

1. Search code, tracked `.env*` files, configuration and Git history (`git log -p`, or gitleaks/trufflehog when installed) for key patterns: `sk-`, `sk_live_`, `service_role`, `secret`, `token`, `api_key`, JWTs.
2. Classify each as **public by design** or **secret**, and list secrets behind a public prefix.
3. Check that `.env` is git-ignored.
4. For each exposed secret recommend **rotation** (issue a new one at the provider, revoke the old one) as well as removal. Removing it from code does not un-leak it. Rotation is the owner's action in the provider's console.
5. Never print a whole secret: prefix and last four characters at most.

### 3. Each principal sees only its own data

1. List every table or collection holding user data and every route or query that reads or changes it.
2. With row-level security (Supabase/PostgreSQL), check that it is enabled on each table and explain each policy in plain language (who may read, insert, update, delete, under which condition). Flag permissive policies (`USING (true)`, no `auth.uid()`).
3. Propose minimal policies: each user reaches only their rows, and sensitive fields (payment status, role, balance, plan) change only through the server. Confirm the service-role key never reaches the client.
4. Own backend: check that every query filters by the authenticated principal and tenant (`WHERE user_id = :session_user`) and that no lookup by identifier skips the owner check (IDOR).
5. Check that routes require authentication.

### 4. Report, then fix

For each finding: severity (critical/high/medium/low), `path:line`, the possible attack, the proposed fix and the effort, sorted by severity. **Stop here: nothing has changed yet.** Fix only approved items, one at a time, each with an integration test that proves it (for example: user B gets 403 or an empty list when asking for user A's orders). Policy and row-level-security changes go in a separate migration the owner reviews before it runs.

## Pitfalls

- Client validation is UX comfort, not security: keep it and repeat it on the server.
- Hiding a button does not protect the route.
- Never trust `user_id`, `role` or a price taken from the request body.
- Never expose a secret's value in logs, reports or commits.
- Never disable an existing protection (row-level security, auth middleware, validation) to make something work.
- Row-level-security tests count only against a real local or test database, never production.

## Verification

- No amount or permission depends on what the client sends.
- No secret in the client bundle or the repository; exposed keys are flagged for rotation.
- Row-level security is enabled on every user-data table and each policy is explained.
- Tests prove one user cannot reach another's data.
- Fixes were made only after approval.
