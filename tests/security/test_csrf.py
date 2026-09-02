"""Tests for app/security/csrf.py.

`origin_check` is ASGI middleware, but it's a plain async function taking
(request, call_next), so it's tested directly with a stub `call_next` - no app, no
transport, and the pass/block decision is visible rather than inferred from a status
code that several other layers could also have produced.

Note this middleware is not registered in main.py yet.
"""

import pytest
from fastapi import Response

from app.security import csrf
from app.security.csrf import origin_check
from tests.factories import make_request

ALLOWED = "http://localhost:5173"
PASSED_THROUGH = "passed-through"


async def call_next(request: object) -> Response:
    """Stands in for the rest of the middleware stack."""
    return Response(PASSED_THROUGH)


@pytest.fixture(autouse=True)
def known_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(csrf.settings, "cors_allow_origins", [ALLOWED])


async def test_allows_an_unsafe_request_from_an_allowed_origin() -> None:
    response = await origin_check(make_request({"origin": ALLOWED}, method="POST"), call_next)

    assert response.body == PASSED_THROUGH.encode()


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_blocks_a_state_changing_request_with_no_origin(method: str) -> None:
    """A cross-site form POST is the classic CSRF shape and arrives without an Origin
    a browser would let us trust."""
    response = await origin_check(make_request(method=method), call_next)

    assert response.status_code == 403


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_blocks_a_state_changing_request_from_a_foreign_origin(method: str) -> None:
    request = make_request({"origin": "https://evil.example"}, method=method)

    response = await origin_check(request, call_next)

    assert response.status_code == 403


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
async def test_lets_safe_methods_through_without_an_origin(method: str) -> None:
    """Read-only requests aren't the CSRF risk, and blocking them would break plain
    navigation - the browser sends no Origin on a top-level GET."""
    response = await origin_check(make_request(method=method), call_next)

    assert response.body == PASSED_THROUGH.encode()


async def test_origin_matching_is_exact() -> None:
    """A prefix match would let http://localhost:5173.evil.example through."""
    request = make_request({"origin": f"{ALLOWED}.evil.example"}, method="POST")

    response = await origin_check(request, call_next)

    assert response.status_code == 403
