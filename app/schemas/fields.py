"""Shared field types.

Each one exists because two or more entry points have to agree on a canonical form,
and disagreeing is silent rather than loud.

`NormalizedEmail`: login and provisioning have to match exactly. If provisioning
stores "Alice@Example.com" and login lowercases before its lookup, that account can
never be signed into, and nothing anywhere raises - it's just a 401 forever.

`CurrentPeriod`: a Period is always the first of its month, and an Expense may only
be written into the month that is current now. A row carrying today's real date
instead still inserts happily and then fails to match `period =` comparisons, the
`(budget_id, period, series_id)` constraint, and Rollover's exact-match reuse.
"""

import datetime
from typing import Annotated

from pydantic import AfterValidator, BeforeValidator, EmailStr


def normalize_email(value: str) -> str:
    """The canonical form: trimmed and lowercased."""
    return value.strip().lower()


def _normalize_if_str(value: object) -> object:
    # BeforeValidator sees the raw input, which isn't necessarily a string - leave
    # anything else alone and let EmailStr produce the type error.
    return normalize_email(value) if isinstance(value, str) else value


# Normalizes first, then validates: a pasted "  Alice@Example.com " is accepted and
# stored as "alice@example.com", while "nonsense" is still rejected at the door.
NormalizedEmail = Annotated[EmailStr, BeforeValidator(_normalize_if_str)]


def first_of_month(value: datetime.date) -> datetime.date:
    """The canonical Period form: the month `value` falls in, day 1."""
    return value.replace(day=1)


def current_period() -> datetime.date:
    """The Period that is open right now, on the server clock in UTC.

    UTC rather than the user's zone: the alternative needs a per-user timezone that
    nothing stores yet, and a wrong guess moves the month boundary for everyone.
    """
    return first_of_month(datetime.datetime.now(datetime.UTC).date())


def _to_current_period(value: datetime.date) -> datetime.date:
    """Canonicalize to the first of the month, then insist it is the open one.

    Both steps in one validator rather than two stacked `AfterValidator`s, whose
    relative order is easy to get backwards - truncating has to happen before the
    comparison or every day but the 1st is rejected.
    """
    period = first_of_month(value)
    open_period = current_period()
    if period != open_period:
        raise ValueError(
            f"period must be the current one ({open_period.isoformat()}); "
            "expenses cannot be written ahead of it or behind it"
        )
    return period


# Truncates and then constrains: a payload dated anywhere inside the open month is
# accepted and stored as its 1st, while any other month is a 422. Planning ahead is
# what draft Budgets are for; backdating would let a late edit change a shortfall
# Rollover has already acted on. See docs/adr/0010.
CurrentPeriod = Annotated[datetime.date, AfterValidator(_to_current_period)]
