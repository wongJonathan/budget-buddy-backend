import uuid

from httpx import AsyncClient

from app.schemas.savings import SavingsUpdate


async def test_there_is_no_delete_route(authed_client: AsyncClient) -> None:
    """Closing a Savings is what deleting its Expense does (ADR-0014 as amended)."""
    response = await authed_client.delete(f"/savings/{uuid.uuid4()}")

    assert response.status_code == 405


def test_the_note_is_the_only_writable_field() -> None:
    """The Fund is derived, so there is nothing else a PATCH could honestly set."""
    assert set(SavingsUpdate.model_fields) == {"note"}


async def test_period_must_be_year_month(authed_client: AsyncClient) -> None:
    response = await authed_client.get("/savings?period=nonsense")

    assert response.status_code == 422
