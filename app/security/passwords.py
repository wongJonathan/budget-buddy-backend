from pwdlib import PasswordHash

_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_and_update_password(password: str, hashed_password: str) -> tuple[bool, str | None]:
    return _hasher.verify_and_update(password, hashed_password)
