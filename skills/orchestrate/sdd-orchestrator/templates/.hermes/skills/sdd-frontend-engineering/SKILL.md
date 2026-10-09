---
name: sdd-frontend-engineering
description: Guide React, Next.js, UI, and Vercel performance work.
version: 0.1.0
author: Felipe Angeli (FelipeAngeli), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [frontend, React, Next.js, accessibility, performance]
    related_skills: [sdd-backend-engineering, sdd-architecture-decisions]
---

# SDD Frontend Engineering

Project-local playbook for React, Next.js and visible UI slices. It distills the React, Next.js and Vercel-oriented skills reviewed from `pedronauck/skills` at commit `0422940cea5d9960b3697016d1cb28c8ed02b030`; project rules, the installed framework version, design authorities and accepted SDD decisions remain authoritative.

## When to Use

Use when a slice changes a React component, hook, client/server rendering boundary, route, data-loading path, design-system primitive, interaction state, accessibility behavior, bundle, loading path or frontend performance.

Do not use for backend-only work, native mobile UI, a visual review with no implementation decision, or a review already assigned to a controller-selected sub-agent.

## Prerequisites

- Read project instructions, the current slice contract and the affected route/component path.
- Detect the installed React, Next.js, compiler, router, styling and test tooling from repository evidence; do not assume versions or Vercel hosting.
- Identify the existing design/token authority and the server/client data ownership boundary.
- Load only the references required by the changed behavior.

## Reference Routing

| Concern | Read |
| --- | --- |
| React state, effects, hooks, compiler and Next.js boundaries | `references/react-and-next-boundaries.md` |
| Component API, UI states, design tokens and accessibility | `references/composition-and-accessibility.md` |
| Waterfalls, bundles, rendering, caching and performance evidence | `references/performance-and-delivery.md` |

## Procedure

1. **Map the user path.** Name the route, entry point, component tree, data owner, rendering boundary and user-visible states. Done when every changed state has a real path and owner.
2. **Preserve rendering boundaries.** Keep server data and secrets on the server, pass only serializable client inputs, and choose client components only for required browser interaction. Done when every client/server crossing has a concrete reason.
3. **Model state and composition deliberately.** Derive values during render, keep effects for synchronization with external systems, and prefer explicit variants or composed children over growing boolean-prop APIs. Done when state has one owner and the public component contract is explainable without hidden flags.
4. **Honor the design and accessibility contract.** Reuse project primitives and semantic tokens; account for loading, empty, error, disabled and success states plus keyboard focus and labels where interaction changes. Done when the changed surface works without pointer-only operation and does not invent a parallel visual system.
5. **Remove measured delivery costs.** First inspect data dependencies and bundle boundaries; parallelize independent I/O, avoid unnecessary client code and defer optional work only when runtime evidence supports it. Done when each optimization names the avoided waterfall, bytes, render work or interaction delay.
6. **Prove the behavior.** Use the project-owned type, lint, unit, integration, visual or browser checks that can falsify the changed invariant. Done when functional, rendering-boundary and accessibility evidence is observable rather than inferred from a successful build.

## Pitfalls

- Do not add `useEffect` to derive render data or repair an avoidable state model.
- Do not add `useMemo`, `useCallback` or `memo` by reflex; retain manual memoization only when identity or a non-compiled consumer requires it.
- Do not move data to the client merely because a component is interactive.
- Do not suppress hydration, lint or accessibility warnings without identifying the underlying mismatch.
- Do not use explicit colors when the project exposes semantic tokens.
- Do not claim a performance improvement without a baseline and a relevant observable measure.
- Do not treat this playbook as authorization to deploy, mutate a shared environment or run unapproved end-to-end tests.

## Verification

- Every changed route/component has named rendering, data and state ownership.
- Client/server boundaries and serialized props match the installed framework's contract.
- Interactive changes preserve keyboard operation, focus behavior, labels and relevant status states.
- Performance changes cite before/after evidence or explicitly record the missing measure.
- Project-owned checks provide observable evidence for the changed behavior.
