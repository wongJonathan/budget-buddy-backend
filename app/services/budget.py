import json
import uuid
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.budget import Budget
from app.models.category import Category
from app.models.enums import Frequency, TransactionType
from app.models.expense import Expense
from app.models.savings import Savings
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.budget import BudgetCreate, BudgetUpdate
from app.schemas.category import CategoryCreate
from app.schemas.expense import ExpenseCreate
from app.schemas.fields import current_period
from app.services.visibility import live_budgets, live_categories

_REQUIRED_EXPENSE_KEYS = {"tag", "name", "cost", "frequency", "amountSaved"}


async def _import_amount_saved(
    db: AsyncSession, expense: Expense, amount: Decimal, user: User
) -> None:
    """Translate the legacy `amountSaved` figure into the ledger.

    The export format predates ADR-0011 and still carries a single saved figure per
    expense. There is no `amount_saved` column to put it in any more, and dropping it
    would silently lose real money at import, so it becomes what it would have been if
    the user had recorded it: a Savings for the lineage plus one `SAVE` Transaction
    funding it.

    That Transaction draws on the Pool and therefore counts toward the Expense's Met for
    the imported Period, which is correct - setting money aside *is* consuming a
    Period's allocation (docs/adr/0011).
    """
    if amount <= 0:
        return

    savings = Savings(user_id=user.id)
    db.add(savings)
    await db.flush()
    expense.savings_id = savings.id

    db.add(
        Transaction(
            expense_id=expense.id,
            user_id=user.id,
            type=TransactionType.SAVE,
            name=f"Imported savings for {expense.name}",
            amount=amount,
            date=expense.period,
        )
    )


class ActiveBudgetNotDeletable(AppError):
    """The caller asked to delete the Budget they currently have open.

    Deleting it would hide every Expense under it (ADR-0007) while
    `User.active_budget_id` still pointed at it, leaving the account with no live
    plan and its funded Savings unreachable. Refusing here is what makes "only the
    active Budget holds savings" safe to rely on - see docs/adr/0011.
    """

    def __init__(self) -> None:
        super().__init__(
            "the active budget cannot be deleted; activate another budget first",
            status_code=409,
        )


async def create_budget(db: AsyncSession, user: User, data: BudgetCreate) -> Budget:
    budget = Budget(**data.model_dump())
    budget.user_id = user.id
    db.add(budget)
    await db.commit()
    await db.refresh(budget)
    return budget


async def get_budget(
    db: AsyncSession, budget_id: uuid.UUID, user_id: uuid.UUID
) -> Budget | None:
    result = await db.scalars(
        live_budgets().where(Budget.id == budget_id, Budget.user_id == user_id)
    )
    return result.one_or_none()


async def list_budgets(
    db: AsyncSession, user_id: uuid.UUID | None = None
) -> Sequence[Budget]:
    query = live_budgets()
    if user_id is not None:
        query = query.where(Budget.user_id == user_id)
    result = await db.execute(query)
    return result.scalars().all()


async def update_budget(db: AsyncSession, budget: Budget, data: BudgetUpdate) -> Budget:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(budget, field, value)
    await db.commit()
    await db.refresh(budget)
    return budget


async def soft_delete_budget(db: AsyncSession, budget: Budget, user: User) -> None:
    if user.active_budget_id == budget.id:
        raise ActiveBudgetNotDeletable
    budget.deleted_at = func.now()
    await db.commit()


def _get_frequency(frequency: str) -> Frequency:

    match frequency:
        case "Once":
            return Frequency.ONCE
        case "Daily":  # cost * 365 / 12
            return Frequency.DAILY

        case "Weekly":  # cost * 52 / 12
            return Frequency.WEEKLY

        case "Monthly":
            return Frequency.MONTHLY

        case "Yearly":
            return Frequency.YEARLY

        case "Set date":
            # @TODO: Need to update this
            return Frequency.ONCE
        case _:
            raise ValueError(f"{frequency} is not recognized")


async def convert_json_to_budget(
    db: AsyncSession, metadata: BudgetCreate, file: bytes, user: User
) -> Budget:
    json_data = json.loads(file)

    expenses = []
    category_names: set[str] = set()
    categories: dict[str, uuid.UUID] = {}

    for expense_key, expense_data in json_data.items():
        if "transactionType" in expense_data:
            continue

        missing = _REQUIRED_EXPENSE_KEYS - expense_data.keys()
        if missing:
            raise ValueError(
                f"Expense '{expense_key}' is missing required field(s): "
                f"{', '.join(sorted(missing))}"
            )

        category_names.add(expense_data["tag"])
        expenses.append(expense_data)

    # Check for existing categories. A deleted one is not reused: the import creates a
    # fresh Category with the same name instead.
    matching_categories = await db.execute(
        live_categories().where(
            Category.user_id == user.id, Category.name.in_(category_names)
        )
    )
    for matching_category in matching_categories.scalars().all():
        category_names.remove(matching_category.name)
        categories[matching_category.name] = matching_category.id

    budget = Budget(**metadata.model_dump(), user_id=user.id)
    db.add(budget)
    await db.flush()

    for category_name in category_names:
        category_metadata = CategoryCreate(name=category_name)
        category = Category(**category_metadata.model_dump(), user_id=user.id)
        db.add(category)
        await db.flush()
        categories[category_name] = category.id

    for expense in expenses:
        frequency = _get_frequency(str(expense["frequency"]))
        expense_metadata = ExpenseCreate(
            budget_id=budget.id,
            category_id=categories[expense["tag"]],
            name=expense["name"],
            note=expense["note"],
            cost=Decimal(expense["cost"]),
            frequency=frequency,
            # The open month, not today's date: Period is always the first of its
            # month, and `date.today()` reads the local clock where the rest of the
            # app reads UTC.
            period=current_period(),
        )

        new_expense = Expense(**expense_metadata.model_dump(), user_id=user.id)
        db.add(new_expense)
        await db.flush()
        await _import_amount_saved(
            db, new_expense, Decimal(expense["amountSaved"]), user
        )

    await db.commit()

    return budget
