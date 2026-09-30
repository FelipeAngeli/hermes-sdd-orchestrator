# React and Next.js boundaries

## Recover the actual stack

Read the package manifest, route structure, compiler/linter configuration and nearby components before applying framework guidance. React and Next.js APIs vary materially by installed version; preserve the repository's established router and rendering model.

## Rendering and data ownership

- Start at the route boundary and keep data fetching, authorization and secrets on the server unless a browser-only capability requires a client boundary.
- Pass serializable, minimal props across a server/client boundary. Move browser interaction into the smallest client component rather than converting an entire route by default.
- A client component must not become `async`; keep asynchronous server work above it or use the framework's supported client data mechanism.
- Treat hydration warnings as evidence of divergent server/client output. Fix the source of non-determinism before considering any narrow documented exception.
- Respect route-level loading, error and not-found conventions already used by the project. Do not replace framework boundaries with ad hoc booleans when the framework boundary owns the behavior.

## React state, effects and compiler

- Calculate derived values during render. Use an effect only to synchronize with a system outside React, such as a subscription, browser API or imperative library.
- Keep component renders pure: no mutations of props/state, subscriptions, network requests or nondeterministic reads that change output during render.
- Put state at the lowest common owner that must coordinate it. A component should not mirror a parent-controlled value into local state without a defined synchronization contract.
- When React Compiler is enabled, obey the Rules of React and its lint rules before attempting optimization. Compiler bailouts can leave behavior working while skipping optimization.
- Do not add manual memoization for cheap render-local work. Keep it when referential identity is part of a hook dependency or non-compiled consumer contract, and make dependencies exhaustive.

## Verification

Confirm server/client placement with the project's build and route tests. Exercise every changed loading, error and interaction state; a passing unit test alone does not prove serialization or hydration behavior.

## Sources reviewed

- Pedro Nauck, [`react`](https://github.com/pedronauck/skills/tree/0422940cea5d9960b3697016d1cb28c8ed02b030/skills/mine/react).
- Pedro Nauck, [`next-best-practices`](https://github.com/pedronauck/skills/tree/0422940cea5d9960b3697016d1cb28c8ed02b030/skills/curated/next-best-practices).
