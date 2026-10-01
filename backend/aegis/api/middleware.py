"""Request context, security headers and request-body limits (pure ASGI middleware).

* Request IDs: an inbound ``X-Request-ID`` is accepted only if it is a short safe token;
  otherwise one is minted. It is returned on every response and set in the context used by
  logs, audit records and error bodies.
* Security headers on every response: no sniffing, no framing, no referrer, ``no-store``
  caching for API/auth responses, and a restrictive CSP for JSON responses.
* Body limit: requests declaring a larger ``Content-Length`` are rejected before any
  handler runs; chunked bodies are counted as they stream and cut off at the limit.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from aegis.api.errors import body as error_body
from aegis.observability.context import accept_request_id, request_id_var

access_log = logging.getLogger("aegis.access")

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
]
API_CSP = (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'")


class _TooLarge(Exception):
    pass


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])
        }
        rid = accept_request_id(headers.get("x-request-id"))
        token = request_id_var.set(rid)
        started = time.perf_counter()
        status_holder: dict[str, int] = {"status": 0}
        path: str = scope.get("path", "")

        async def send_wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                hdrs: list[tuple[bytes, bytes]] = list(message.get("headers", []))
                present = {k.lower() for k, _ in hdrs}
                hdrs.append((b"x-request-id", rid.encode()))
                hdrs.extend(h for h in SECURITY_HEADERS if h[0] not in present)
                if path.startswith(("/api/", "/auth/")):
                    if b"cache-control" not in present:
                        hdrs.append((b"cache-control", b"no-store"))
                    if b"content-security-policy" not in present:
                        hdrs.append(API_CSP)
                message["headers"] = hdrs
            await send(message)

        try:
            declared = headers.get("content-length")
            if declared is not None and (
                not declared.isdigit() or int(declared) > self.max_body_bytes
            ):
                await self._reject(
                    send_wrapped, 413, "payload_too_large", "The request body is too large"
                )
                return
            received = 0

            async def receive_limited() -> Message:
                nonlocal received
                message = await receive()
                if message["type"] == "http.request":
                    received += len(message.get("body", b""))
                    if received > self.max_body_bytes:
                        raise _TooLarge()
                return message

            try:
                await self.app(scope, receive_limited, send_wrapped)
            except _TooLarge:
                if status_holder["status"] == 0:
                    await self._reject(
                        send_wrapped, 413, "payload_too_large", "The request body is too large"
                    )
        finally:
            access_log.info(
                "request",
                extra={
                    "http_method": scope.get("method"),
                    "http_path": path[:200],
                    "http_status": status_holder["status"],
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
            request_id_var.reset(token)

    @staticmethod
    async def _reject(send: Send, status: int, code: str, message: str) -> None:
        payload: dict[str, Any] = error_body(code, message)
        data = json.dumps(payload).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(data)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": data})
