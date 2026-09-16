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

`RequestedPeriod`: the same canonical form arriving the other way, as a list route's
`?period=YYYY-MM`. The reads filter on `period =`, so a query value that is not the
first of its month matches nothing and returns an empty list rather than an error -
the same silence as above, reached from the read side.
"""

import calendar
import datetime
from typing import Annotated

from pydantic import AfterValidator, BeforeValidator, EmailStr

from app.models.enums import TransactionType


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


def last_of_month(value: datetime.date) -> datetime.date:
    _, days = calendar.monthrange(value.year, value.month)
    return value.replace(day=days)


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


def _parse_requested_period(value: object) -> object:
    # Anything that isn't a string is left for pydantic to reject - a non-str would
    # otherwise reach strptime() as a TypeError, which is a 500 rather than a 422.
    if isinstance(value, str):
        try:
            return first_of_month(datetime.date.strptime(value, "%Y-%m"))
        except ValueError:
            raise ValueError(
                f"'{value}' must be in YYYY-MM format, e.g. 2026-09"
            ) from None
    return value


_SERVER_ONLY_TYPES = frozenset({TransactionType.SPEND_SAVED, TransactionType.TRANSFER})


def _reject_server_only(value: TransactionType) -> TransactionType:
    if value in _SERVER_ONLY_TYPES:
        raise ValueError(
            f"'{value.value}' transactions are written by the server, not posted "
            "directly; spend against the expense and the split is computed for you"
        )
    return value


ClientTransactionType = Annotated[TransactionType, AfterValidator(_reject_server_only)]


# Truncates and then constrains: a payload dated anywhere inside the open month is
# accepted and stored as its 1st, while any other month is a 422. Planning ahead is
# what draft Budgets are for; backdating would let a late edit change a shortfall
# Rollover has already acted on. See docs/adr/0010.
CurrentPeriod = Annotated[datetime.date, AfterValidator(_to_current_period)]

RequestedPeriod = Annotated[
    datetime.date | None, BeforeValidator(_parse_requested_period)
]
