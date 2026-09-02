from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from app.config import settings

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}

# The shape BaseHTTPMiddleware hands a dispatch function: the rest of the stack,
# already bound to this request.
CallNext = Callable[[Request], Awaitable[Response]]


async def origin_check(request: Request, call_next: CallNext) -> Response:
    if request.method in UNSAFE:
        origin = request.headers.get("origin")
        if not origin or origin not in settings.cors_allow_origins:
            return JSONResponse({"detail": "Bad origin"}, status_code=403)
    return await call_next(request)
