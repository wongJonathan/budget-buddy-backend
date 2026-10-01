from collections.abc import Sequence

from fastapi import APIRouter

from app.dependencies import CurrentUser, DbSession, ReadableSavings, WritableSavings
from app.models.savings import Savings
from app.schemas.fields import RequestedPeriod, current_period
from app.schemas.savings import SavingsRead, SavingsUpdate, SavingsWithFund
from app.services import savings as savings_service

# No POST: a Savings comes into being on the first Save against a lineage. No DELETE:
# deleting the lineage's Expense is the one way to close it (ADR-0014 as amended).
router = APIRouter(prefix="/savings", tags=["savings"])


async def _with_fund(
    db: DbSession, savings: Sequence[Savings], period: RequestedPeriod
) -> list[SavingsWithFund]:
    funds = await savings_service.period_funds(
        db, (s.id for s in savings), period or current_period()
    )
    return [
        SavingsWithFund.model_validate(
            {
                **SavingsRead.model_validate(s).model_dump(),
                "before_period_fund": funds[s.id].before_period,
                "period_fund": funds[s.id].period,
                "fund": funds[s.id].total,
            }
        )
        for s in savings
    ]


@router.get("", response_model=list[SavingsWithFund])
async def list_savings(
    user: CurrentUser,
    db: DbSession,
    period: RequestedPeriod = None,
    include_deleted: bool = False,
) -> list[SavingsWithFund]:
    savings = await savings_service.list_savings(
        db, user, include_deleted=include_deleted
    )
    return await _with_fund(db, savings, period)


@router.get("/{savings_id}", response_model=SavingsWithFund)
async def get_savings(
    savings: ReadableSavings, db: DbSession, period: RequestedPeriod = None
) -> SavingsWithFund:
    [read] = await _with_fund(db, [savings], period)
    return read


@router.patch("/{savings_id}", response_model=SavingsRead)
async def update_savings(
    savings: WritableSavings, data: SavingsUpdate, db: DbSession
) -> Savings:
    return await savings_service.update_savings(db, savings, data)
