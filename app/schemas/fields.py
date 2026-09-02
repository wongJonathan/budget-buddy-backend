"""Shared field types.

`NormalizedEmail` exists so that every entry point storing or looking up an email
agrees on the canonical form. Login and provisioning have to match exactly: if
provisioning stores "Alice@Example.com" and login lowercases before its lookup, that
account can never be signed into, and nothing anywhere raises - it's just a 401
forever.
"""

from typing import Annotated

from pydantic import BeforeValidator, EmailStr


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
