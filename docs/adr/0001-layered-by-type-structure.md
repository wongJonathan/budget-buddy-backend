# Layered-by-type project structure instead of domain-based

**Status**: accepted

Research into current FastAPI production practices (Aug 2026) found the ecosystem trending toward domain/feature-based structure (each feature owns its own router, schema, model, and service) for apps expected to grow across many domains — the official FastAPI docs' own example is layered-by-type but explicitly doesn't address production scale, and widely-cited references like zhanymkanov/fastapi-best-practices (modeled on Netflix/dispatch) warn that layered-by-type "doesn't scale well for monoliths with many domains and modules."

We chose layered-by-type anyway (`app/routers/`, `app/models/`, `app/schemas/`, `app/services/`), with the intent to nest feature subfolders inside a layer only once that layer actually grows large enough to need it, rather than committing to per-feature folders upfront for a codebase that doesn't have them yet.

## Considered Options

- **Domain/feature-based** (each feature owns `router.py`, `schemas.py`, `models.py`, `service.py`): rejected for now — imposes per-feature structure before there are enough features to justify it.
- **Layered-by-type** (chosen): lower ceremony at low feature counts, defers the domain-split decision until it's actually needed.

## Consequences

If the app grows to many domains, migrating fully to domain-based structure later is the same costly restructuring the community consensus warns against. Worth revisiting if cross-feature coupling or per-layer file count becomes painful.
