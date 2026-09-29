import enum

from sqlalchemy import Enum as SAEnum


def pg_enum[E: enum.Enum](enum_cls: type[E], name: str) -> SAEnum:
    """Native Postgres enum storing each member's `.value` (lowercase) as the DB label,
    instead of SQLAlchemy's default of the member `.name` (uppercase)."""
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=True,
        values_callable=lambda e: [m.value for m in e],
    )


class Frequency(enum.StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"
    CUSTOM = "custom"
    ONCE = "once"


class TransactionType(enum.StrEnum):
    SPEND = "spend"
    SPEND_SAVED = "spend_saved"
    SAVE = "save"
    # A Transfer is basically a convience feature that automatically moves money from a source
    # expense, fund a pool, and if another expense is provided, funds that expense from the pool.
    # This grouping is denoted by the transfer_id in which they all share the same id (anchor's id)
    TRANSFER = "transfer"
    INCOME = "income"
