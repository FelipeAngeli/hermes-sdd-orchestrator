# Component composition and accessibility

## Public component contracts

- Inspect existing primitives before creating a new component. Reuse established tokens, variants, focus behavior and accessibility patterns.
- Prefer composition when consumers need structurally different behavior. Compound components and provider-owned shared state are often clearer than a growing collection of boolean behavior props.
- Use explicit variants when the visual or behavioral contract genuinely differs. A narrowly named component is safer than a generic component with mutually interacting flags.
- Keep the context interface intentional: state, actions and metadata should be distinguishable, and presentation should not own state that several descendants need to coordinate.
- Prefer children for structural composition; use render props only where the caller must own render-time data or control.

## Visual and interaction floor

- Treat the project's design tokens and component inventory as the authority. Use semantic tokens rather than literal colors when the system supports themes.
- Account for default, hover, focus-visible, disabled, loading, empty, error and success states appropriate to the changed control.
- Preserve keyboard operation and visible focus for every interactive element. Native elements remain preferable when they express the needed semantics.
- Give controls accessible names, connect input errors to their fields, announce dynamic status/errors where the project pattern requires it, and avoid interaction that depends solely on color, hover or pointer precision.
- Respect reduced-motion preferences and avoid decorative animation that hides state changes or blocks interaction.

## Verification

Render the changed states and traverse the interaction with a keyboard when behavior changes. Check focus movement, names/labels and error/status communication against the project's testing or browser evidence.

## Sources reviewed

- Vercel, [`composition-patterns`](https://github.com/pedronauck/skills/tree/0422940cea5d9960b3697016d1cb28c8ed02b030/skills/curated/vercel-composition-patterns).
- Pedro Nauck, [`ui-craft`](https://github.com/pedronauck/skills/tree/0422940cea5d9960b3697016d1cb28c8ed02b030/skills/mine/ui-craft).
- Pedro Nauck, [`shadcn`](https://github.com/pedronauck/skills/tree/0422940cea5d9960b3697016d1cb28c8ed02b030/skills/curated/shadcn).
