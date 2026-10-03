"""Run Rollover for one User from the command line.

    uv run python -m app.scripts.rollover --user-id <uuid> [--through YYYY-MM-DD]

`--through` defaults to the current Period. Any day of the month is accepted; Rollover
reduces it to the first of its month.

Run it inside the container for anything that isn't local dev, the same way Alembic is
run - `docker compose exec app uv run python -m app.scripts.rollover ...` - so it uses
the `db` hostname rather than whatever the host's DATABASE_URL happens to point at.
"""

import argparse
import asyncio
import datetime
import sys
import uuid

from app.database import async_session_factory, engine
from app.models.user import User
from app.schemas.fields import current_period, first_of_month
from app.services.rollover import rollover

EXIT_OK = 0
EXIT_NOT_FOUND = 1
EXIT_BAD_INPUT = 2


async def run(user_id: uuid.UUID, through: datetime.date) -> int:
    try:
        async with async_session_factory() as db:
            user = await db.get(User, user_id)
            if user is None:
                print(f"no user with id {user_id}", file=sys.stderr)
                return EXIT_NOT_FOUND

            await rollover(db, user, through=through)

        print(f"rolled user {user_id} through {first_of_month(through):%Y-%m}")
        return EXIT_OK
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="rollover", description="Roll one User's active Budget forward."
    )
    parser.add_argument("--user-id", required=True)
    parser.add_argument(
        "--through", help="target Period as YYYY-MM-DD (default: the current Period)"
    )
    args = parser.parse_args()

    try:
        user_id = uuid.UUID(args.user_id)
    except ValueError:
        print(f"not a UUID: {args.user_id}", file=sys.stderr)
        return EXIT_BAD_INPUT

    if args.through is None:
        through = current_period()
    else:
        try:
            through = datetime.date.fromisoformat(args.through)
        except ValueError:
            print(f"not a YYYY-MM-DD date: {args.through}", file=sys.stderr)
            return EXIT_BAD_INPUT

    # As in create_user: anything past argv validation is a real bug, so let the
    # traceback through. This is an operator tool.
    return asyncio.run(run(user_id, through))


if __name__ == "__main__":
    raise SystemExit(main())
