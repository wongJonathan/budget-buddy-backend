"""Tests for app/security/passwords.py.

Thin wrapper over pwdlib, so these check the wiring rather than the crypto: that a
hash is actually a hash, that verification is honest in both directions, and that the
"needs rehash" channel the login route has a TODO for is being surfaced.
"""

from app.security.passwords import hash_password, verify_and_update_password


def test_hash_password_does_not_store_the_password() -> None:
    hashed = hash_password("correct horse battery staple")

    assert "correct horse battery staple" not in hashed
    # PasswordHash.recommended() is Argon2 - assert the scheme, so swapping it out is a
    # deliberate change rather than something that happens silently.
    assert hashed.startswith("$argon2")


def test_hashing_the_same_password_twice_gives_different_hashes() -> None:
    """Distinct salts - two users with the same password must not be linkable."""
    assert hash_password("hunter2") != hash_password("hunter2")


def test_verify_accepts_the_right_password() -> None:
    hashed = hash_password("hunter2")

    is_valid, updated = verify_and_update_password("hunter2", hashed)

    assert is_valid is True
    # Second element is a re-hash, populated only when the stored hash used outdated
    # parameters. Fresh hash, so nothing to update - this is the channel login's
    # "TODO: when an update is available should update the db's stored hash" needs.
    assert updated is None


def test_verify_rejects_the_wrong_password() -> None:
    hashed = hash_password("hunter2")

    is_valid, _ = verify_and_update_password("hunter3", hashed)

    assert is_valid is False


def test_verify_rejects_a_hash_of_a_different_password() -> None:
    is_valid, _ = verify_and_update_password("hunter2", hash_password("something else"))

    assert is_valid is False
