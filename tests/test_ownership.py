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
from typing import Any
from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

import app.schemas
from app.exceptions import DeletedRow
from app.models.budget import Budget
from app.models.category import Category
from app.models.enums import Frequency, TransactionType
from app.models.expense import Expense
from app.models.user import User
from app.ownership import NotOwned, _owned_marker, require_owned
from app.schemas.expense import ExpenseCreate
from app.schemas.fields import current_period
from app.schemas.transaction import TransactionCreate
from app.schemas.user import UserUpdate
from app.services import expense as expense_service
from app.services import transaction as transaction_service
from app.services import user as user_service
from tests.factories import (
    make_budget,
    make_category,
    make_expense,
    make_scalars_one,
    make_transaction,
)

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
        period=current_period(),
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
    """Visible doesn't mean usable: the caller's own deleted budget is a 409, not a
    404 - ownership is already established, so there is nothing left to hide."""
    alice, budget, category = await _account(db_session, "alice")
    budget.deleted_at = func.now()
    await db_session.commit()

    with pytest.raises(DeletedRow, match="Budget is deleted"):
        await expense_service.create_expense(db_session, _expense_payload(budget, category), alice)


async def test_a_deleted_category_is_not_a_valid_parent(db_session: AsyncSession) -> None:
    """A deleted label can't be put on a new Expense."""
    alice, budget, category = await _account(db_session, "alice")
    category.deleted_at = func.now()
    await db_session.commit()

    with pytest.raises(DeletedRow, match="Category is deleted"):
        await expense_service.create_expense(db_session, _expense_payload(budget, category), alice)


async def test_a_deleted_expense_is_not_a_valid_parent_for_a_transaction(
    db_session: AsyncSession,
) -> None:
    alice, budget, category = await _account(db_session, "alice")
    expense = await expense_service.create_expense(
        db_session, _expense_payload(budget, category), alice
    )
    expense.deleted_at = func.now()
    await db_session.commit()

    with pytest.raises(DeletedRow, match="Expense is deleted"):
        await transaction_service.create_transaction(
            db_session,
            alice,
            TransactionCreate(
                expense_id=expense.id,
                type=TransactionType.SPEND,
                name="Shop",
                amount=Decimal("5.00"),
                date=datetime.date.today(),
            ),
        )


async def test_another_users_deleted_row_is_still_not_found(
    db_session: AsyncSession,
) -> None:
    """Ownership is checked before deletion, so a 409 can never confirm that a
    stranger's row exists (docs/adr/0008)."""
    alice, _, alice_category = await _account(db_session, "alice")
    _, bob_budget, _ = await _account(db_session, "bob")
    bob_budget.deleted_at = func.now()
    await db_session.commit()

    with pytest.raises(NotOwned, match="Budget not found"):
        await expense_service.create_expense(
            db_session, _expense_payload(bob_budget, alice_category), alice
        )


async def test_the_readable_lookup_returns_the_callers_deleted_row(
    db_session: AsyncSession,
) -> None:
    alice, budget, _ = await _account(db_session, "alice")
    budget.deleted_at = func.now()
    await db_session.commit()

    found = await require_owned(db_session, Budget, budget.id, alice, allow_deleted=True)

    assert found.id == budget.id
    with pytest.raises(DeletedRow):
        await require_owned(db_session, Budget, budget.id, alice)


async def test_the_readable_lookup_still_hides_an_expense_under_a_deleted_budget(
    db_session: AsyncSession,
) -> None:
    """`allow_deleted` relaxes the row's own flag only. An Expense whose Budget is
    gone is hidden, not deleted, and stays a 404."""
    alice, budget, category = await _account(db_session, "alice")
    expense = await expense_service.create_expense(
        db_session, _expense_payload(budget, category), alice
    )
    budget.deleted_at = func.now()
    await db_session.commit()

    with pytest.raises(NotOwned, match="Expense not found"):
        await require_owned(db_session, Expense, expense.id, alice, allow_deleted=True)


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
                name="Bob shop",
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
    bobs_rows = await transaction_service.create_transaction(
        db_session,
        bob,
        TransactionCreate(
            expense_id=bobs_expense.id,
            type=TransactionType.SPEND,
            name="Bob shop",
            amount=Decimal("5.00"),
            date=datetime.date.today(),
        ),
    )
    (bobs_transaction,) = bobs_rows

    # A `spend`, not a `transfer`: the transfer type is server-written and rejected on
    # the way in (docs/adr/0011), but `transfer_id` is still a client-supplied reference
    # and it is the ownership of *that* which is under test here.
    with pytest.raises(NotOwned, match="Transaction not found"):
        await transaction_service.create_transaction(
            db_session,
            alice,
            TransactionCreate(
                expense_id=alices_expense.id,
                type=TransactionType.SPEND,
                name="Alice move",
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
            "period": current_period().isoformat(),
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Budget not found"


# ---------------------------------------------------------------------------
# readable vs writable path dependencies
# ---------------------------------------------------------------------------
# GET /{id} uses the readable variant and returns the caller's deleted row. PATCH
# and DELETE use the writable one and refuse it with a 409 - for DELETE that is
# what keeps a second `deleted_at` write from breaking Restore's match
# (docs/adr/0014).

_DELETED_AT = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)

_ROUTES = [
    pytest.param(make_budget, "/budgets", {"name": "New"}, id="budget"),
    pytest.param(make_category, "/categories", {"name": "New"}, id="category"),
    pytest.param(make_expense, "/expenses", {"name": "New"}, id="expense"),
    pytest.param(make_transaction, "/transactions", {"amount": "1.00"}, id="transaction"),
]


@pytest.mark.parametrize(("make", "prefix", "patch_body"), _ROUTES)
async def test_get_returns_the_callers_deleted_row(
    authed_client: AsyncClient,
    mock_db: MagicMock,
    make: Any,
    prefix: str,
    patch_body: dict[str, str],
) -> None:
    row = make(deleted_at=_DELETED_AT)
    mock_db.scalars.return_value = make_scalars_one(row)

    response = await authed_client.get(f"{prefix}/{row.id}")

    assert response.status_code == 200
    assert response.json()["deleted_at"] is not None


@pytest.mark.parametrize(("make", "prefix", "patch_body"), _ROUTES)
async def test_patch_refuses_the_callers_deleted_row(
    authed_client: AsyncClient,
    mock_db: MagicMock,
    make: Any,
    prefix: str,
    patch_body: dict[str, str],
) -> None:
    row = make(deleted_at=_DELETED_AT)
    mock_db.scalars.return_value = make_scalars_one(row)

    response = await authed_client.patch(f"{prefix}/{row.id}", json=patch_body)

    assert response.status_code == 409
    mock_db.commit.assert_not_awaited()


@pytest.mark.parametrize(("make", "prefix", "patch_body"), _ROUTES)
async def test_delete_refuses_an_already_deleted_row(
    authed_client: AsyncClient,
    mock_db: MagicMock,
    make: Any,
    prefix: str,
    patch_body: dict[str, str],
) -> None:
    """A second `deleted_at` write would break Restore's match, so it is refused."""
    row = make(deleted_at=_DELETED_AT)
    mock_db.scalars.return_value = make_scalars_one(row)

    response = await authed_client.delete(f"{prefix}/{row.id}")

    assert response.status_code == 409
    assert row.deleted_at == _DELETED_AT
    mock_db.commit.assert_not_awaited()
