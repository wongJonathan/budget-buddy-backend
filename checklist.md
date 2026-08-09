# Test coverage checklist

`tests/` uses a mocked `AsyncSession` (`MagicMock(spec=AsyncSession)`, see `tests/conftest.py`)
instead of a real Postgres connection. Routers and services run for real; only the DB I/O
boundary (`execute`/`get`/`add`/`commit`/`refresh`/`delete`) is faked. This keeps the suite
fast and dependency-free, but anything that only exists as real SQL/Postgres behavior is
**not exercised** by these tests. Each gap below is also marked with a
`# NOT COVERED: mocked session, see checklist.md` comment at the relevant call site.

## Not covered — needs a real Postgres to test meaningfully

- [ ] **`Expense.monthly_cost` GENERATED column math** (`app/models/expense.py`,
      `_MONTHLY_COST_EXPR`). The CASE expression per `frequency` (daily/weekly/monthly/yearly/
      once/custom) runs entirely server-side; there's no Python code computing it. A bug in the
      divisor math or the `custom` frequency's month-count logic can't be caught by a mocked
      session — the test would just be asserting a fake value against itself.
- [ ] **`pg_enum()` lowercase storage** (`app/models/enums.py`). Verifies enum columns store
      `.value` (`daily`) rather than SQLAlchemy's default `.name` (`DAILY`). Only meaningful
      against a real enum column.
- [ ] **Unique constraint `(budget_id, period, series_id)`** on `Expense` (Rollover
      idempotency guard, ADR-0004). Not enforced by the mock.
- [ ] **FK `ondelete` cascade behavior**: `Expense`/`Budget`→`User` cascade, `Transaction`→
      `Expense` cascade, `Expense`→`Category` restrict, `User.active_budget_id`/
      `Transaction.transfer_id` set-null. None of this is enforced without a real DB.
- [ ] **`list_budget_expenses`'s period filtering** (`app/services/expense.py`,
      `extract("year", ...)`/`extract("month", ...)`). `extract()` is a SQL function; a mocked
      `execute()` just returns whatever the test configured, so the actual WHERE-clause
      correctness (right budget, right year+month, `is_deactivated` filter) is untested.
- [ ] **`convert_json_to_budget` real atomicity** (`app/services/budget.py`). The
      flush-then-commit-once design is meant to guarantee nothing persists if a row fails
      partway through — that's only verifiable against a real transaction/rollback, not a mock.
- [ ] **Server-generated defaults**: `gen_random_uuid()` PKs, `series_id` default,
      `created_at`/`last_active` defaults, `is_deleted`/`is_deactivated` server defaults.
      Tests populate these manually via fake `refresh()` side effects rather than letting
      Postgres generate them, so a migration/model drift on a default wouldn't be caught.
- [ ] **Alembic migrations themselves**, including the manual circular-FK fix
      (`users.active_budget_id` ↔ `budgets.user_id`). Migrations never run against this suite.

## How to close these later

Needs a real (test or ephemeral) Postgres — e.g. a dedicated test database on the existing
`docker-compose.yml` `db` service, migrated via `alembic upgrade head`, with per-test isolation
(transaction rollback via SAVEPOINT, or table truncation between tests).
