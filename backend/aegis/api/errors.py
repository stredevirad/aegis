"""Stable error schema.

Every error response has the shape::

    {"error": {"code": "not_found", "message": "...", "request_id": "...", "details": [...]}}

``code`` is a stable machine-readable identifier; ``message`` is safe to display. Validation
errors list the failing locations and reasons but never echo the submitted values (a
rejected payload may contain secrets). Unexpected exceptions become ``internal_error``
without internal detail; the full exception is logged with the request ID.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from aegis.observability.context import request_id_var

log = logging.getLogger("aegis.api")

_STATUS_CODES = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    406: "not_acceptable",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    503: "unavailable",
}


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or []
        self.headers = headers


def not_found(what: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} not found")


def body(code: str, message: str, details: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id_var.get(),
            "details": details or [],
        }
    }


def _validation_details(exc: RequestValidationError) -> list[dict[str, Any]]:
    out = []
    for e in exc.errors()[:50]:
        out.append(
            {
                "loc": [str(p) for p in e.get("loc", ())][:10],
                "msg": str(e.get("msg", ""))[:300],
                "type": str(e.get("type", ""))[:100],
            }
        )
    return out


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            body(exc.code, exc.message, exc.details), status_code=exc.status, headers=exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            body("validation_error", "The request is invalid", _validation_details(exc)),
            status_code=422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, "error")
        message = (
            exc.detail
            if isinstance(exc.detail, str) and exc.status_code < 500
            else code.replace("_", " ")
        )
        return JSONResponse(
            body(code, message), status_code=exc.status_code, headers=getattr(exc, "headers", None)
        )

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled exception", exc_info=exc)
        return JSONResponse(body("internal_error", "An internal error occurred"), status_code=500)
