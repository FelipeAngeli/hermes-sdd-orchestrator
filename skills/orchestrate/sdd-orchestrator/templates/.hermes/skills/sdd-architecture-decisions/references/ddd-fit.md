# DDD fit

## Verdict first

Use **Proceed**, **Downgrade** or **Refuse** before introducing DDD vocabulary.

Proceed when the domain has evolving language, consequential invariants, multiple business capabilities and enough essential complexity to repay modeling cost. Downgrade to transaction scripts plus clear boundaries for straightforward CRUD or syntax validation. Refuse a redesign when domain experts/evidence are unavailable or the demand does not touch the model.

## Strategic checks

- Distinguish subdomain, bounded context and deployment unit.
- Split a polysemous term only when meaning or ownership differs.
- Every context has purpose, language, inbound/outbound contracts and owner.
- Every context-map edge states direction and relationship; avoid pattern catalogs without a decision.

## Tactical checks

- Aggregate boundary follows invariants that must be consistent together.
- Other aggregates are referenced by identity.
- Value objects are immutable and equal by components.
- One transaction updates one aggregate unless evidence requires otherwise.
- Cross-aggregate effects name transactional/eventual behavior and owner.
- Repository methods load, save or search; business rules belong to the model owner.

## Tripwires

Layer folders, ports or entities do not prove DDD. Do not introduce CQRS, Event Sourcing, microservices or a repository interface merely to look domain-driven.

## Verification

Accepted language appears consistently in requirements, code and tests; each invariant has one owner and an observable behavioral example.
