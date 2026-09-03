"""The active Budget cannot be soft-deleted.

Deleting it would hide every Expense under it (ADR-0007) while `User.active_budget_id`
still pointed at it, leaving the account with no live plan. ADR-0011 leans on this
harder: it is half of what makes "only the active Budget holds funded Savings" true, so
that no Budget which *can* be deleted ever holds money.

Mocked session - the guard compares two ids on rows already in hand.
"""

import uuid
from unittest.mock import MagicMock

import pytest

from app.services.budget import ActiveBudgetNotDeletable, soft_delete_budget
from tests.factories import make_budget, make_user


async def test_deleting_an_inactive_budget_is_allowed(mock_db: MagicMock) -> None:
    """The control, and the ordinary case: drafts are what get deleted."""
    user = make_user()
    budget = make_budget(user_id=user.id)
    user.active_budget_id = uuid.uuid4()

    await soft_delete_budget(mock_db, budget, user)

    assert budget.is_deleted is True
    mock_db.commit.assert_awaited_once()


async def test_deleting_the_active_budget_is_refused(mock_db: MagicMock) -> None:
    user = make_user()
    budget = make_budget(user_id=user.id)
    user.active_budget_id = budget.id

    with pytest.raises(ActiveBudgetNotDeletable):
        await soft_delete_budget(mock_db, budget, user)

    assert budget.is_deleted is False
    mock_db.commit.assert_not_awaited()


async def test_a_refused_delete_is_a_409(mock_db: MagicMock) -> None:
    """Not a 404: the Budget is the caller's and it exists. The refusal is about its
    state, and the message has to say what to do instead."""
    user = make_user()
    budget = make_budget(user_id=user.id)
    user.active_budget_id = budget.id

    with pytest.raises(ActiveBudgetNotDeletable) as caught:
        await soft_delete_budget(mock_db, budget, user)

    assert caught.value.status_code == 409
    assert "activate another budget first" in caught.value.message


async def test_a_user_with_no_active_budget_can_delete_anything(mock_db: MagicMock) -> None:
    """`active_budget_id` is nullable. A null must not compare equal to a real id."""
    user = make_user()
    budget = make_budget(user_id=user.id)
    user.active_budget_id = None

    await soft_delete_budget(mock_db, budget, user)

    assert budget.is_deleted is True
