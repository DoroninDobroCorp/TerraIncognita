"""Route class that turns request-validation failures into a *serialisable* 422.

FastAPI's default 422 body echoes the offending ``input``. For ``NaN`` / ``Infinity`` (which
Python's JSON parser accepts, and which a lat/lng range check correctly rejects) that echo
cannot be rendered by ``JSONResponse(allow_nan=False)``: the client got a 500 plus a
traceback in the log instead of a 422.  Use as ``APIRouter(route_class=SafeValidationRoute)``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute


def _public_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only what a client needs (never echo raw input, which may be non-finite)."""
    return [
        {
            "type": str(err.get("type", "value_error")),
            "loc": [p if isinstance(p, int) else str(p) for p in err.get("loc", ())],
            "msg": str(err.get("msg", "Invalid value")),
        }
        for err in errors
    ]


class SafeValidationRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except RequestValidationError as exc:
                return JSONResponse(status_code=422, content={"detail": _public_errors(exc.errors())})

        return handler
