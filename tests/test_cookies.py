"""Tests for app/security/cookies.py.

The cookie flags *are* the security boundary for a session cookie, so each one is
asserted explicitly rather than by comparing the whole header string - a change to any
of them should name itself in the failure.

`cookie_secure` and `cookie_name` are monkeypatched rather than read from the ambient
settings, so these don't quietly pass or fail depending on the developer's .env.
"""

from http.cookies import SimpleCookie

import pytest
from fastapi import Response

from app.security import cookies
from app.security.cookies import clear_session_cookie, set_session_cookie


def parse_set_cookie(response: Response) -> SimpleCookie:
    jar: SimpleCookie = SimpleCookie()
    for header in response.headers.getlist("set-cookie"):
        jar.load(header)
    return jar


@pytest.fixture(autouse=True)
def known_cookie_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the settings these functions read. `cookies.settings` is the shared
    singleton, so monkeypatch (which restores afterwards) rather than assignment."""
    monkeypatch.setattr(cookies.settings, "cookie_name", "session")
    monkeypatch.setattr(cookies.settings, "cookie_secure", True)


def test_set_session_cookie_carries_the_token_and_the_protective_flags() -> None:
    response = Response()

    set_session_cookie(response, "the-token")

    morsel = parse_set_cookie(response)["session"]
    assert morsel.value == "the-token"
    assert morsel["httponly"] is True  # not readable from JS, so XSS can't lift it
    assert morsel["secure"] is True  # HTTPS only
    assert morsel["samesite"] == "lax"  # not sent on cross-site POSTs
    assert morsel["path"] == "/"
    assert int(morsel["max-age"]) == int(cookies.settings.session_ttl_days.total_seconds())


def test_set_session_cookie_honours_cookie_secure_being_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local dev over plain HTTP - the flag has to actually come off, or the browser
    drops the cookie and nothing works."""
    monkeypatch.setattr(cookies.settings, "cookie_secure", False)
    response = Response()

    set_session_cookie(response, "the-token")

    assert parse_set_cookie(response)["session"]["secure"] == ""


def test_clear_session_cookie_expires_it_immediately() -> None:
    response = Response()

    clear_session_cookie(response)

    morsel = parse_set_cookie(response)["session"]
    assert morsel.value == ""
    # Path and flags must match the cookie that was set, or the browser treats it as a
    # different cookie and the original survives the logout.
    assert morsel["path"] == "/"
    assert morsel["httponly"] is True
    assert morsel["samesite"] == "lax"
    assert int(morsel["max-age"]) == 0


def test_the_cookie_name_comes_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cookies.settings, "cookie_name", "__Host-session")
    response = Response()

    set_session_cookie(response, "the-token")

    assert "__Host-session" in parse_set_cookie(response)
