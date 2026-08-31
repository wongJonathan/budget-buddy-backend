"""Provision one account from the command line.

    uv run python -m app.scripts.create_user \
        --display-name "Alice" --email alice@example.com --password "..."

Run it inside the container for anything that isn't local dev, the same way Alembic is
run - `docker compose exec app uv run python -m app.scripts.create_user ...` - so it
uses the `db` hostname rather than whatever the host's DATABASE_URL happens to point at.

The password is passed on the command line, which means it lands in shell history and
is briefly visible in `ps`. That is a deliberate trade: the credential is meant to be
rotated by its owner on first sign-in. Note that nothing enforces that rotation yet.
"""

import argparse
import asyncio
import sys

from pydantic import BaseModel, ValidationError

from app.database import async_session_factory, engine
from app.schemas.fields import NormalizedEmail
from app.security import audit
from app.services.provisioning import EmailAlreadyExists, provision_user

EXIT_OK = 0
EXIT_ALREADY_EXISTS = 1
EXIT_BAD_INPUT = 2


class ProvisionInput(BaseModel):
    """Validates argv before anything touches the database."""

    display_name: str
    email: NormalizedEmail
    password: str


async def run(provision_input: ProvisionInput) -> int:
    try:
        async with async_session_factory() as db:
            try:
                user, budget = await provision_user(
                    db,
                    display_name=provision_input.display_name,
                    email=provision_input.email,
                    password=provision_input.password,
                )
            except EmailAlreadyExists as exc:
                print(exc.message, file=sys.stderr)
                return EXIT_ALREADY_EXISTS

        # Deliberately not the password, in any form.
        print(f"user id:   {user.id}")
        print(f"email:     {user.email}")
        print(f"budget id: {budget.id}")
        return EXIT_OK
    finally:
        # Two pools to let go of: this script's and the audit module's, which opened
        # its own the moment the USER_CREATED event was written.
        await audit.shutdown()
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="create_user", description="Provision one account with a blank active budget."
    )
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    try:
        provision_input = ProvisionInput(
            display_name=args.display_name, email=args.email, password=args.password
        )
    except ValidationError as exc:
        print(exc, file=sys.stderr)
        return EXIT_BAD_INPUT

    # Anything other than a bad address or a taken one is a real bug: let the traceback
    # through rather than dressing it up. This is an operator tool.
    return asyncio.run(run(provision_input))


if __name__ == "__main__":
    raise SystemExit(main())
