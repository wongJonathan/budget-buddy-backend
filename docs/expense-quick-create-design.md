# Design: frontend-friendly `POST /expenses`

Status: proposed, not implemented — captures the outcome of a grilling session so the changes below can be implemented later. See `docs/adr/0005-category-by-name-resolution-not-shared.md` for the one decision from this session that rose to ADR level.

## Goal

Let the frontend create an `Expense` without already knowing the target `budget_id` (fall back to the user's active budget) or a `category_id` (create a new `Category` by name when the frontend doesn't have one to reference).

## Decisions

- `user_id` becomes a required, explicit field on `ExpenseCreate` — a temporary stand-in for a caller identity until a real auth layer exists. Mirrors how every other create schema in this codebase already carries its owner explicitly (`BudgetCreate.user_id`, `CategoryCreate.user_id`); no hidden/session state.
- `ExpenseCreate` is extended in place rather than split into a new schema. Internal callers (`convert_json_to_budget`, future Rollover/Activation) always pass every field concretely, so the new optional/derived logic is simply unexercised for them, and the new ownership check is a no-op since they already only ever reference their own user's rows.
- `budget_id` becomes optional: if omitted, resolve it from `User.active_budget_id` for the given `user_id`.
- Category selection becomes `category_id: UUID | None` XOR `category_name: str | None` — exactly one must be set.
- Sending `category_name` always creates a new `Category` (never matches an existing one) — this is a **different policy** than `convert_json_to_budget`'s existing get-or-create matching, deliberately kept separate; see ADR 0005.
- New categories created this way always get `system_type=None`. There's no way to set `system_type` at creation time through this endpoint — that stays a deliberate follow-up `PATCH /categories/{id}`.
- Ownership of an explicitly-provided `budget_id`/`category_id` against `user_id` is verified.

## Schema changes — `app/schemas/expense.py`

```python
class ExpenseCreate(BaseModel):
    user_id: uuid.UUID
    budget_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    category_name: str | None = None
    name: str
    note: str | None = None
    cost: Decimal
    frequency: Frequency
    amount_saved: Decimal = Decimal(0)
    goal_amount: Decimal | None = None
    goal_date: date | None = None
    period: date

    @model_validator(mode="after")
    def _validate_category_selector(self) -> "ExpenseCreate":
        if (self.category_id is None) == (self.category_name is None):
            raise ValueError("exactly one of category_id or category_name is required")
        return self
```

`category_id`/`category_name` are request-only — they don't appear on `ExpenseRead` (which keeps its existing `category_id: uuid.UUID`, always the resolved id).

`ExpenseUpdate` is unmodified — it has no `budget_id`/`category_id` today, so this proposal doesn't touch it.

## Migration — `Category` uniqueness

Add a unique constraint on `(user_id, name)` to `categories`, enforced at the DB level (not an app-level pre-check) so it's race-safe under concurrent requests:

```python
# app/models/category.py
class Category(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_category_user_name"),
    )
    ...
```

Generate via `docker compose exec app uv run alembic revision --autogenerate -m "add unique constraint on category user_id+name"`. Check first whether any existing dev data already has duplicate `(user_id, name)` pairs — the migration will fail to apply if so, and those rows would need de-duping before it can run.

Match rule is case-sensitive exact match (matches the existing `Category.name.in_(...)` comparison already used in `convert_json_to_budget`) — no new collation/case-folding logic.

## Service layer changes

### `app/services/user.py`

Need a lookup for the active budget id (or reuse `user_service.get_user` and read `.active_budget_id` directly — no new function strictly required, but a small helper keeps the router thin):

```python
async def get_active_budget_id(db: AsyncSession, user_id: uuid.UUID) -> uuid.UUID | None:
    user = await db.get(User, user_id)
    if user is None:
        return None
    return user.active_budget_id
```

### `app/services/expense.py`

`create_expense` currently does a direct `Expense(**data.model_dump())`, which won't work once `ExpenseCreate` carries `user_id`/`category_name` (not columns on `Expense`). Introduce a resolution step ahead of construction — e.g. a new function that the router calls instead of `create_expense` directly, or a wrapper. Sketch:

```python
async def create_expense_for_user(db: AsyncSession, data: ExpenseCreate) -> Expense:
    budget_id = data.budget_id
    if budget_id is None:
        budget_id = await user_service.get_active_budget_id(db, data.user_id)
        if budget_id is None:
            raise NoActiveBudgetError()  # -> 400

    budget = await db.get(Budget, budget_id)
    if budget is None or budget.user_id != data.user_id:
        raise OwnershipError("budget_id")  # -> 400

    if data.category_id is not None:
        category = await db.get(Category, data.category_id)
        if category is None or category.user_id != data.user_id:
            raise OwnershipError("category_id")  # -> 400
        category_id = category.id
    else:
        category = Category(user_id=data.user_id, name=data.category_name, system_type=None)
        db.add(category)
        try:
            await db.flush()
        except IntegrityError:
            raise DuplicateCategoryNameError(data.category_name)  # -> 409
        category_id = category.id

    expense = Expense(
        budget_id=budget_id,
        category_id=category_id,
        name=data.name,
        note=data.note,
        cost=data.cost,
        frequency=data.frequency,
        amount_saved=data.amount_saved,
        goal_amount=data.goal_amount,
        goal_date=data.goal_date,
        period=data.period,
    )
    db.add(expense)
    await db.commit()
    await db.refresh(expense)
    return expense
```

Notes on the sketch:
- Uses `flush()` (not `commit()`) for the category insert so the whole thing rolls back as one transaction if the later `Expense` insert fails — the atomicity the frontend usage was originally motivated by.
- The `IntegrityError` catch around the `flush()` is how the DB-level unique constraint becomes a `409` rather than a generic `500`.
- Exact exception types/names above are illustrative — pick whatever error-signaling convention fits the rest of `app/services/` (custom exception classes vs. return sentinels); none of the other services currently raise custom exceptions, so this introduces a new pattern worth deciding deliberately rather than copying this sketch verbatim.
- `create_expense` (the existing plain function) stays as-is for `convert_json_to_budget` and any other caller that already has concrete `budget_id`/`category_id` and doesn't need resolution.

## Router changes — `app/routers/expenses.py`

```python
@router.post("", response_model=ExpenseRead, status_code=status.HTTP_201_CREATED)
async def create_expense(data: ExpenseCreate, db: DbSession) -> Expense:
    try:
        return await expense_service.create_expense_for_user(db, data)
    except NoActiveBudgetError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="user has no active budget")
    except OwnershipError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"{e.field} does not belong to user_id")
    except DuplicateCategoryNameError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"category '{e.name}' already exists")
```

## Error responses summary

| condition | status | detail (example) |
|---|---|---|
| `category_id` and `category_name` both set, or neither | 422 (Pydantic validation, automatic) | standard FastAPI validation error |
| `budget_id` omitted and user has no `active_budget_id` | 400 | "user has no active budget" |
| explicit `budget_id` doesn't belong to `user_id` | 400 | "budget_id does not belong to user_id" |
| explicit `category_id` doesn't belong to `user_id` | 400 | "category_id does not belong to user_id" |
| `category_name` collides with an existing `(user_id, name)` | 409 | "category '<name>' already exists" |

## Files touched (checklist for implementation)

- `app/schemas/expense.py` — `ExpenseCreate` fields + validator
- `app/models/category.py` — unique constraint
- new Alembic migration (generated, then hand-checked per this repo's usual autogenerate review)
- `app/services/user.py` — active-budget lookup (or inline in expense service)
- `app/services/expense.py` — new resolution function; existing `create_expense`/`create_bulk_expenses` untouched
- `app/routers/expenses.py` — exception → HTTP status mapping
- `app/services/budget.py` (`convert_json_to_budget`) — **not touched**, per ADR 0005
- `tests/` — no expense/category test coverage exists yet (only `tests/test_health.py`), so this would be the first real service-level test suite in the repo, not just additions to an existing one

## Explicitly out of scope

- `PATCH /expenses/{id}` — has no `budget_id`/`category_id` today; unaffected by this change.
- Real authentication — `user_id` as a body field is a known-temporary stand-in.
- Setting `system_type` on a category created via `category_name`.
