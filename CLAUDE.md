# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Dependencies are managed with `uv`; nothing needs a manual venv activation, prefix commands with `uv run`.

```bash
uv sync                                    # install/sync dependencies

uv run ruff check .                        # lint
uv run ruff check --fix .                  # lint, autofix
uv run ruff format .                       # format
uv run mypy app                            # type-check (app/ only, not alembic/ or tests/)
uv run pytest                              # run tests
uv run pytest tests/test_health.py -v      # run a single test file
uv run pytest -k test_liveness             # run a single test by name

uv run pre-commit run --all-files          # ruff + mypy, same as CI would run

docker compose up -d                       # app + Postgres 16, local dev stack
docker compose exec app uv run alembic revision --autogenerate -m "message"
docker compose exec app uv run alembic upgrade head
docker compose restart app                 # picks up host code changes (no --reload in the container's uvicorn)
```

**Run Alembic from inside the `app` container, not from the host.** This machine has a native PostgreSQL install already bound to `localhost:5432`, so a host-side `uv run alembic ...` silently connects to *that* instead of the Dockerized Postgres. `docker compose exec app ...` uses the `db` hostname over Docker's internal network and avoids the collision. If `alembic` reports a password/auth error, this is almost always why.

Compose mounts `./app` and `./alembic` into the container, and the `app` service overrides the Dockerfile's `CMD` with `--reload`, so file edits are picked up automatically — no `docker compose restart app` needed. That override is dev-only; the Dockerfile's `CMD` (no `--reload`) is what any real deployment of the built image uses.

## Architecture

FastAPI + async SQLAlchemy 2.0 + Alembic + Postgres. Layered-by-type structure (`app/routers/`, `app/models/`, `app/schemas/`, `app/services/`) rather than domain/feature folders — a deliberate choice against the more common convention for growing FastAPI apps, see `docs/adr/0001-layered-by-type-structure.md` for why, and nest per-feature subfolders inside a layer if it grows large enough to need it rather than restructuring the whole tree.

Request flow: router (HTTP concerns, 404s) → Pydantic schema (validation, in `app/schemas/`) → service function (`app/services/`, plain async functions, one module per entity, takes an `AsyncSession` + schema, returns the ORM model) → SQLAlchemy model (`app/models/`). `app/dependencies.py` provides `DbSession`, the `Annotated[AsyncSession, Depends(...)]` alias used in every route/service signature.

### Domain model

`CONTEXT.md` is the canonical glossary — check it before introducing new domain terms or renaming existing ones, and update it inline when a term's meaning is resolved or changes. `docs/adr/` records the non-obvious architectural decisions (numbered, read them for the *why* behind anything that looks surprising). Key mechanisms, in brief:

- A `Budget` has no "active" flag of its own — it's active only when `User.active_budget_id` points at it.
- `Category.system_type` (`income` | `saving_goal` | `debt` | `null`) is a reserved business-logic hook, separate from the user-editable `name`.
- `Expense` is the single row type covering planned spending, savings goals, debt paydown, *and* recurring income (via `category.system_type = income`) — there's no separate Income/SavingFund/BudgetItem table (ADR 0002).
- `Expense.series_id` + the `(budget_id, period, series_id)` unique constraint is the lineage key Rollover uses to recognize "this month's Groceries" as the successor of last month's (ADR 0004). Any code path that creates an Expense must assign or propagate a `series_id`.
- Budget activation instantiates fresh `Expense` rows rather than flipping a status flag, so a draft and a live budget stay independent (ADR 0003).
- **Rollover and Activation are not implemented yet** — only their schema groundwork (`period`, `series_id`, the unique constraint) exists. The routes currently in place are plain CRUD with no walk-forward or copy-on-activate logic wired up.
- Soft-delete: `Budget.is_deleted` and `Expense.is_deactivated` — their `DELETE` routes set the flag (see `app/services/budget.py` / `expense.py`), and list queries filter it out. `User`, `Category`, `Transaction` use real hard deletes.

### Non-obvious implementation details

- **`Expense.monthly_cost` is a real Postgres `GENERATED ALWAYS AS (...) STORED` column**, not computed in Python — see `_MONTHLY_COST_EXPR` in `app/models/expense.py`. It branches on `frequency` via a SQL `CASE`. Because generated columns must be immutable (no `now()`/`CURRENT_DATE` allowed), the `custom` frequency's month-count uses the row's own `period` column as the "as of" date instead of literal today — numerically equivalent since a row is always valid for the month `period` marks.
- **Enum columns must use `pg_enum()`** from `app/models/enums.py`, not a bare `sqlalchemy.Enum(...)`. Plain `Enum` stores the Python member *name* (`DAILY`) as the Postgres enum label; `pg_enum()` overrides that to store `.value` (`daily`) instead, so DB values match the API's JSON and any hand-written SQL (like the `monthly_cost` CASE expression) that compares against lowercase literals.
- **Watch for field names that shadow their own type import** (Python 3.14's PEP 649 deferred annotation evaluation breaks on this). `Transaction.date` hit this: `from datetime import date` + a field named `date` makes the annotation `date | None` resolve using the field itself, not the imported type, and crashes at import time. Fix is `import datetime` and annotate as `datetime.date`, not `from datetime import date`.
- **Circular FKs need a manual migration fix.** `budgets.user_id` and `users.active_budget_id` reference each other; Alembic's autogenerate can't order two mutually-dependent `CREATE TABLE`s and will emit an inline FK on the table created first, which fails at `upgrade()` time (referenced table doesn't exist yet). The existing migration splits the cross-reference into a separate `op.create_foreign_key(...)` call after both tables exist, with a matching `op.drop_constraint(...)` before both tables are dropped in `downgrade()`. Any new circular FK needs the same manual edit after autogenerating.

### Testing

`tests/conftest.py` provides a `client` fixture (`httpx.AsyncClient` over `ASGITransport`, no real network). `pytest-asyncio` runs in `asyncio_mode = "auto"`, so async tests don't need an explicit marker. Only `tests/test_health.py` exists so far — no DB-backed test fixtures/strategy have been set up yet.

### Tooling notes

- `pyproject.toml`'s mypy config is deliberately *not* full `--strict` — it's a curated subset of flags (`disallow_untyped_defs`, `check_untyped_defs`, `warn_return_any`, `warn_unused_ignores`, `no_implicit_optional`).
- Python is pinned to `>=3.14` (bleeding-edge as of this project's creation) — a deliberate choice, verified against the full dependency set with `uv pip install --dry-run` rather than assumed safe.
