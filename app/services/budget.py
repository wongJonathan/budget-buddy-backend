import datetime
from decimal import Decimal
import json
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.budget import Budget
from app.models.category import Category
from app.models.enums import Frequency
from app.models.expense import Expense
from app.schemas.budget import BudgetCreate, BudgetUpdate
from app.schemas.category import CategoryCreate
from app.schemas.expense import ExpenseCreate
from app.services.category import create_category
from app.services.expense import create_expense

_REQUIRED_EXPENSE_KEYS = {"tag", "name", "cost", "frequency", "amountSaved"}


async def create_budget(db: AsyncSession, data: BudgetCreate) -> Budget:
    budget = Budget(**data.model_dump())
    db.add(budget)
    await db.commit()
    await db.refresh(budget)
    return budget


async def get_budget(db: AsyncSession, budget_id: uuid.UUID) -> Budget | None:
    return await db.get(Budget, budget_id)


async def list_budgets(db: AsyncSession) -> Sequence[Budget]:
    result = await db.execute(select(Budget).where(~Budget.is_deleted))
    return result.scalars().all()


async def update_budget(
    db: AsyncSession, budget_id: uuid.UUID, data: BudgetUpdate
) -> Budget | None:
    budget = await db.get(Budget, budget_id)
    if budget is None:
        return None
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(budget, field, value)
    await db.commit()
    await db.refresh(budget)
    return budget


async def soft_delete_budget(db: AsyncSession, budget_id: uuid.UUID) -> bool:
    budget = await db.get(Budget, budget_id)
    if budget is None:
        return False
    budget.is_deleted = True
    await db.commit()
    return True


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
    db: AsyncSession, metadata: BudgetCreate, file: bytes
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
                f"Expense '{expense_key}' is missing required field(s): {', '.join(sorted(missing))}"
            )

        category_names.add(expense_data["tag"])
        expenses.append(expense_data)

    # Check for existing categories
    matching_categories = await db.execute(
        select(Category).where(
            Category.user_id == metadata.user_id, Category.name.in_(category_names)
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
            user_id=metadata.user_id, name=category_name, system_type=None
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
