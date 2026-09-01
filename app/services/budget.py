import datetime
import json
import uuid
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.budget import Budget
from app.models.category import Category
from app.models.enums import Frequency
from app.models.expense import Expense
from app.models.user import User
from app.schemas.budget import BudgetCreate, BudgetUpdate
from app.schemas.category import CategoryCreate
from app.schemas.expense import ExpenseCreate
from app.services.visibility import live_budgets

_REQUIRED_EXPENSE_KEYS = {"tag", "name", "cost", "frequency", "amountSaved"}


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
    db: AsyncSession, user_id: uuid.UUID | None = None, hide_deleted: bool = True
) -> Sequence[Budget]:
    query = live_budgets(include_deleted=not hide_deleted)
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


async def soft_delete_budget(db: AsyncSession, budget: Budget) -> None:
    budget.is_deleted = True
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

    # Check for existing categories
    matching_categories = await db.execute(
        select(Category).where(
            Category.user_id == user.id, Category.name.in_(category_names)
        )
    )
    for matching_category in matching_categories.scalars().all():
        category_names.remove(matching_category.name)
        categories[matching_category.name] = matching_category.id

    budget = Budget(**metadata.model_dump())
    db.add(budget)
    await db.flush()

    for category_name in category_names:
        category_metadata = CategoryCreate(
            user_id=user.id, name=category_name, system_type=None
        )
        category = Category(**category_metadata.model_dump())
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
            amount_saved=Decimal(expense["amountSaved"]),
            period=datetime.date.today(),
        )

        expense = Expense(**expense_metadata.model_dump())
        db.add(expense)

    await db.commit()

    return budget
