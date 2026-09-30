# Performance and delivery evidence

## Diagnose before changing code

Measure the relevant user path first: request timeline, bundle contents, server render duration, client render/interaction delay, or browser performance trace. A build that passes is not a performance measurement.

## Priority order

1. **Eliminate waterfalls.** Start independent requests together, avoid awaiting before work that does not depend on the result, and use the framework's supported streaming/suspense boundaries where they improve time to useful content.
2. **Reduce delivered JavaScript.** Keep server-only work server-side, avoid broad barrel imports when a direct import exists, dynamically load truly optional heavy client features, and inspect bundle output instead of guessing.
3. **Protect server work.** Deduplicate identical work within its valid request/cache scope, parallelize independent server operations and never create shared mutable module state for request-specific data.
4. **Keep client work intentional.** Derive state during render, avoid subscription/effect churn, batch DOM reads before writes, and use transitions or deferred values only when they preserve interaction responsiveness.
5. **Optimize rendering last.** Do not memoize or hoist JSX mechanically. Apply render optimization only after the trace identifies avoidable work and verify it does not stale data or hide loading feedback.

## Safe claims

Name the baseline, test conditions, metric and result. If a production-like measure is unavailable, report the verified structural improvement separately from an unproven user-performance claim. Preserve error, loading, cancellation and cache invalidation semantics while optimizing.

## Verification

Run the project's relevant build, type and test gates, then capture the smallest performance evidence capable of falsifying the claim: a request trace for a waterfall, a bundle analyzer for bytes, or a browser/profile trace for client/render work. Compare equivalent before/after conditions.

## Sources reviewed

- Vercel, [`react-best-practices`](https://github.com/pedronauck/skills/tree/0422940cea5d9960b3697016d1cb28c8ed02b030/skills/curated/vercel-react-best-practices).
- Pedro Nauck, [`next-best-practices`](https://github.com/pedronauck/skills/tree/0422940cea5d9960b3697016d1cb28c8ed02b030/skills/curated/next-best-practices).
