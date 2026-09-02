"""Ownership of client-supplied foreign keys.

Two kinds of test here. The first is structural and runs without a database: it
walks every input schema and fails if a foreign-key field was added without
declaring who has to own it. That is the guard against the failure mode this
mechanism exists for - not "the check is wrong" but "nobody remembered to write
one".

The rest are real-Postgres tests, because a mocked session cannot enforce
anything: `mock_db.scalars` hands back whatever the test stubbed, so a forged
parent id looks exactly like a legitimate one.
"""

import datetime
import importlib
import pkgutil
import uuid
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

import app.schemas
from app.models.budget import Budget
from app.models.category import Category
from app.models.enums import Frequency, TransactionType
from app.models.user import User
from app.ownership import NotOwned, _owned_marker
from app.schemas.expense import ExpenseCreate
from app.schemas.transaction import TransactionCreate
from app.schemas.user import UserUpdate
from app.services import expense as expense_service
from app.services import transaction as transaction_service
from app.services import user as user_service
from tests.factories import make_scalars_one

# Fields named `<x>_id` that are deliberately not ownership-checked. Every entry
# needs a reason, and the reason has to be about the field, not about effort.
EXEMPT_FIELDS = {
    # The owner column itself, not a reference to a parent. It is still
    # client-supplied, which is its own bug - the categories router has no
    # CurrentUser yet, so there is no session to take the owner from.
    ("CategoryCreate", "user_id"),
}


def _input_schemas() -> list[type[BaseModel]]:
    """Every Create/Update schema in app.schemas - the payloads clients send."""
    schemas: list[type[BaseModel]] = []
    for module_info in pkgutil.iter_modules(app.schemas.__path__):
        module = importlib.import_module(f"app.schemas.{module_info.name}")
        for name, obj in vars(module).items():
            if (
                isinstance(obj, type)
                and issubclass(obj, BaseModel)
                and name.endswith(("Create", "Update"))
                and obj.__module__ == module.__name__
            ):
                schemas.append(obj)
    return schemas


def test_every_foreign_key_on_an_input_schema_declares_its_owner() -> None:
    """A new `<x>_id` field must be annotated `<X>Ref` or listed as exempt.

    This is the whole point of declaring ownership on the schema: forgetting is
    a failing test rather than a silent hole in a create path.
    """
    unchecked = [
        f"{schema.__name__}.{field_name}"
        for schema in _input_schemas()
        for field_name, field in schema.model_fields.items()
        if field_name.endswith("_id")
        and _owned_marker(field) is None
        and (schema.__name__, field_name) not in EXEMPT_FIELDS
    ]

    assert not unchecked, (
        f"Foreign-key fields with no ownership check: {unchecked}. Annotate each "
        "with the matching *Ref alias from app.ownership, or add it to "
        "EXEMPT_FIELDS with a reason."
    )


def test_the_schemas_actually_scanned_include_the_known_ones() -> None:
    """Guards the guard: a discovery bug would make the test above vacuous."""
    found = {schema.__name__ for schema in _input_schemas()}
    assert {"ExpenseCreate", "TransactionCreate", "UserUpdate"} <= found


# ---------------------------------------------------------------------------
# real Postgres: the checks themselves
# ---------------------------------------------------------------------------


async def _account(db: AsyncSession, name: str) -> tuple[User, Budget, Category]:
    """One user with a budget and a category of their own."""
    user = User(
        display_name=name,
        email=f"{name}-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
    )
    db.add(user)
    await db.flush()
    budget = Budget(user_id=user.id, name=f"{name} budget")
    category = Category(user_id=user.id, name=f"{name} category")
    db.add_all([budget, category])
    await db.flush()
    return user, budget, category


def _expense_payload(budget: Budget, category: Category) -> ExpenseCreate:
    return ExpenseCreate(
        budget_id=budget.id,
        category_id=category.id,
        name="Milk",
        cost=Decimal("10.00"),
        frequency=Frequency.MONTHLY,
        period=datetime.date.today(),
    )


async def test_an_expense_cannot_be_created_in_another_users_budget(
    db_session: AsyncSession,
) -> None:
    alice, _, alice_category = await _account(db_session, "alice")
    _, bob_budget, _ = await _account(db_session, "bob")

    with pytest.raises(NotOwned, match="Budget not found"):
        await expense_service.create_expense(
            db_session, _expense_payload(bob_budget, alice_category), alice
        )


async def test_an_expense_cannot_reference_another_users_category(
    db_session: AsyncSession,
) -> None:
    alice, alice_budget, _ = await _account(db_session, "alice")
    _, _, bob_category = await _account(db_session, "bob")

    with pytest.raises(NotOwned, match="Category not found"):
        await expense_service.create_expense(
            db_session, _expense_payload(alice_budget, bob_category), alice
        )


async def test_an_expense_in_your_own_budget_still_works(
    db_session: AsyncSession,
) -> None:
    alice, budget, category = await _account(db_session, "alice")

    expense = await expense_service.create_expense(
        db_session, _expense_payload(budget, category), alice
    )

    assert expense.user_id == alice.id
    assert expense.budget_id == budget.id


async def test_a_deleted_budget_is_not_a_valid_parent(db_session: AsyncSession) -> None:
    """Ownership rides on the visibility selects, so a soft-deleted budget is as
    unusable as someone else's - the two rules cannot drift apart."""
    alice, budget, category = await _account(db_session, "alice")
    budget.is_deleted = True
    await db_session.commit()

    with pytest.raises(NotOwned, match="Budget not found"):
        await expense_service.create_expense(db_session, _expense_payload(budget, category), alice)


async def test_bulk_creation_is_checked_per_row(db_session: AsyncSession) -> None:
    """The second row is the forged one: bulk cannot be a way around the check."""
    alice, alice_budget, alice_category = await _account(db_session, "alice")
    _, bob_budget, _ = await _account(db_session, "bob")

    with pytest.raises(NotOwned):
        await expense_service.create_bulk_expenses(
            db_session,
            [
                _expense_payload(alice_budget, alice_category),
                _expense_payload(bob_budget, alice_category),
            ],
            alice,
        )


async def test_a_transaction_cannot_be_attached_to_another_users_expense(
    db_session: AsyncSession,
) -> None:
    alice, _, _ = await _account(db_session, "alice")
    bob, bob_budget, bob_category = await _account(db_session, "bob")
    bobs_expense = await expense_service.create_expense(
        db_session, _expense_payload(bob_budget, bob_category), bob
    )

    with pytest.raises(NotOwned, match="Expense not found"):
        await transaction_service.create_transaction(
            db_session,
            alice,
            TransactionCreate(
                expense_id=bobs_expense.id,
                type=TransactionType.SPEND,
                amount=Decimal("5.00"),
                date=datetime.date.today(),
            ),
        )


async def test_a_transfer_cannot_point_at_another_users_transaction(
    db_session: AsyncSession,
) -> None:
    """The nullable reference is checked too - only when the client sets it."""
    alice, alice_budget, alice_category = await _account(db_session, "alice")
    bob, bob_budget, bob_category = await _account(db_session, "bob")
    alices_expense = await expense_service.create_expense(
        db_session, _expense_payload(alice_budget, alice_category), alice
    )
    bobs_expense = await expense_service.create_expense(
        db_session, _expense_payload(bob_budget, bob_category), bob
    )
    bobs_transaction = await transaction_service.create_transaction(
        db_session,
        bob,
        TransactionCreate(
            expense_id=bobs_expense.id,
            type=TransactionType.SPEND,
            amount=Decimal("5.00"),
            date=datetime.date.today(),
        ),
    )

    with pytest.raises(NotOwned, match="Transaction not found"):
        await transaction_service.create_transaction(
            db_session,
            alice,
            TransactionCreate(
                expense_id=alices_expense.id,
                type=TransactionType.TRANSFER,
                amount=Decimal("5.00"),
                date=datetime.date.today(),
                transfer_id=bobs_transaction.id,
            ),
        )


async def test_active_budget_cannot_be_set_to_another_users_budget(
    db_session: AsyncSession,
) -> None:
    """PATCH /users is authenticated, but `active_budget_id` came from the body."""
    alice, _, _ = await _account(db_session, "alice")
    _, bob_budget, _ = await _account(db_session, "bob")

    with pytest.raises(NotOwned, match="Budget not found"):
        await user_service.update_user(
            db_session, alice, UserUpdate(active_budget_id=bob_budget.id)
        )


async def test_an_unset_optional_reference_is_left_alone(
    db_session: AsyncSession,
) -> None:
    """`exclude_unset` matters: not mentioning a reference is not a null lookup."""
    alice, _, _ = await _account(db_session, "alice")

    updated = await user_service.update_user(db_session, alice, UserUpdate(display_name="Alicia"))

    assert updated is not None
    assert updated.display_name == "Alicia"


async def test_a_forged_parent_surfaces_as_404_not_500(
    authed_client: AsyncClient, mock_db: MagicMock
) -> None:
    """NotOwned reaches the client through the AppError handler, as a 404."""
    mock_db.scalars.return_value = make_scalars_one(None)

    response = await authed_client.post(
        "/expenses",
        json={
            "budget_id": str(uuid.uuid4()),
            "category_id": str(uuid.uuid4()),
            "name": "Milk",
            "cost": "10.00",
            "frequency": "monthly",
            "period": "2026-09-01",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Budget not found"
