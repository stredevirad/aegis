"""Request dependencies: application state, authentication, CSRF and authorization.

Authentication order: an ``Authorization: Bearer aegis_…`` header (API key), otherwise the
session cookie. A request with neither is rejected with 401.

CSRF (security model C2): cookie-authenticated requests with an unsafe method must carry the
custom header ``X-Aegis-CSRF: 1`` *and* an ``Origin`` that is one of the configured origins.
A cross-site form or image request can do neither. Bearer-key requests are not ambient
credentials and are exempt.

Authorization: every ``/api/v1`` route depends on :func:`require` with one permission; the
dependency carries an ``aegis_permission`` attribute that ``tests/api/test_rbac.py`` uses to
check that no route is left unprotected.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import Request

from aegis.api.errors import ApiError
from aegis.api.state import AppState
from aegis.audit.log import security_event
from aegis.auth import apikeys, sessions
from aegis.auth.principal import Principal
from aegis.auth.rbac import Permission, permissions_for_role, permissions_for_scopes
from aegis.observability.context import org_id_var
from aegis.security.tokens import parse_api_key

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_HEADER = "x-aegis-csrf"
KEY_TOUCH_SECONDS = 300.0


def app_state(request: Request) -> AppState:
    state: AppState = request.app.state.aegis
    return state


def _origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def check_csrf(request: Request, state: AppState) -> None:
    if request.method not in UNSAFE_METHODS:
        return
    if request.headers.get(CSRF_HEADER) != "1":
        raise ApiError(403, "csrf_failed", "Missing CSRF header")
    origin = request.headers.get("origin")
    allowed = {_origin_of(o) for o in state.settings.allowed_origins} | {
        _origin_of(state.settings.public_base_url)
    }
    if origin is None or origin not in allowed:
        raise ApiError(403, "csrf_failed", "Request origin is not allowed")


async def _from_api_key(request: Request, state: AppState, presented: str) -> Principal:
    key = await apikeys.resolve(state.engine, state.settings, presented)
    if key is None:
        security_event("api_key_rejected", prefix=parse_api_key(presented) or "malformed")
        raise ApiError(
            401,
            "unauthenticated",
            "Invalid or expired API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    now = time.monotonic()
    if now - state.key_touched.get(str(key.key_id), 0.0) > KEY_TOUCH_SECONDS:
        state.key_touched[str(key.key_id)] = now
        await apikeys.touch(state.engine, key)
    return Principal(
        kind="api_key",
        org_id=key.org_id,
        permissions=permissions_for_scopes(key.scopes),
        api_key_id=key.key_id,
        api_key_prefix=key.prefix,
    )


async def authenticate(request: Request) -> Principal:
    state = app_state(request)
    auth = request.headers.get("authorization")
    if auth is not None:
        scheme, _, credential = auth.partition(" ")
        if scheme.lower() != "bearer" or not credential.strip():
            raise ApiError(401, "unauthenticated", "Unsupported authorization scheme")
        principal = await _from_api_key(request, state, credential.strip())
    else:
        token = request.cookies.get(sessions.COOKIE_NAME)
        if not token:
            raise ApiError(401, "unauthenticated", "Authentication required")
        info = await sessions.resolve(state.engine, state.settings, token)
        if info is None:
            raise ApiError(401, "unauthenticated", "Session expired or revoked")
        check_csrf(request, state)
        principal = Principal(
            kind="user",
            org_id=info.org_id,
            permissions=permissions_for_role(info.role),
            user_id=info.user_id,
            role=info.role,
            session_id=info.session_id,
        )
    org_id_var.set(str(principal.org_id))
    return principal


def require(permission: Permission) -> Callable[[Request], Awaitable[Principal]]:
    async def dependency(request: Request) -> Principal:
        principal = await authenticate(request)
        if not principal.can(permission):
            raise ApiError(
                403, "forbidden", f"This action requires the '{permission.value}' permission"
            )
        return principal

    dependency.aegis_permission = permission  # type: ignore[attr-defined]
    return dependency


def require_authenticated() -> Callable[[Request], Awaitable[Principal]]:
    async def dependency(request: Request) -> Principal:
        return await authenticate(request)

    dependency.aegis_permission = "authenticated"  # type: ignore[attr-defined]
    return dependency


def rate_limit(state: AppState, key: str, per_minute: int, burst: int) -> None:
    wait = state.limiter.check(key, per_minute, burst)
    if wait is not None:
        raise ApiError(
            429,
            "rate_limited",
            "Too many requests",
            headers={"Retry-After": str(max(1, int(wait + 0.999)))},
        )
