"""Backend-for-frontend authentication endpoints (``/auth``), ADR-011.

* ``POST /auth/dev/login`` exists only when ``auth_mode == "dev"`` *and* the profile is
  development/test; the route is not even registered otherwise, and configuration validation
  refuses ``auth_mode = dev`` outside those profiles.
* OIDC endpoints (``/auth/login``, ``/auth/callback``) are registered when ``auth_mode ==
  "oidc"`` (``aegis.auth.oidc``).
* ``POST /auth/logout`` revokes the server-side session and clears the cookie.
* ``POST /auth/switch`` moves the session to another organization the user belongs to
  (a new session is issued; the old one is revoked).

Session cookie: ``HttpOnly``, ``SameSite=Lax``, ``Path=/``, ``Secure`` when configured (always
in staging/production), lifetime equal to the absolute session expiry.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from aegis import audit
from aegis.api.deps import app_state, check_csrf, rate_limit, require_authenticated
from aegis.api.errors import ApiError
from aegis.api.state import AppState
from aegis.audit import Action
from aegis.auth import sessions
from aegis.auth.login import Identity, LoginRefused, complete_login, user_orgs
from aegis.auth.principal import Principal
from aegis.config import Settings
from aegis.db.engine import tenant_tx

router = APIRouter(prefix="/auth", tags=["auth"])
dev_router = APIRouter(prefix="/auth/dev", tags=["auth"])


class SessionOut(BaseModel):
    model_config = ConfigDict(frozen=True)
    authenticated: bool
    org_id: str | None = None
    role: str | None = None
    expires_at: datetime | None = None


class DevLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: Annotated[str, Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$")]
    org: Annotated[str, Field(max_length=63)] | None = None


class SwitchOrg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    org: Annotated[str, Field(min_length=1, max_length=63)]


def set_session_cookie(
    response: Response, settings: Settings, token: str, expires_at: datetime
) -> None:
    response.set_cookie(
        sessions.COOKIE_NAME,
        token,
        expires=expires_at,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        sessions.COOKIE_NAME, path="/", httponly=True, secure=settings.cookie_secure, samesite="lax"
    )


@router.get("/session", response_model=SessionOut)
async def session_status(
    request: Request, state: Annotated[AppState, Depends(app_state)]
) -> SessionOut:
    token = request.cookies.get(sessions.COOKIE_NAME)
    info = await sessions.resolve(state.engine, state.settings, token) if token else None
    if info is None:
        return SessionOut(authenticated=False)
    return SessionOut(authenticated=True, org_id=str(info.org_id), role=info.role)


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(require_authenticated())],
    state: Annotated[AppState, Depends(app_state)],
) -> None:
    if principal.kind != "user" or principal.session_id is None:
        raise ApiError(400, "not_a_session", "Only browser sessions can log out")
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        await sessions.revoke(c, principal.org_id, principal.session_id)
        await audit.record(
            c,
            principal.org_id,
            principal.actor,
            Action.LOGOUT,
            target_type="session",
            target_id=principal.session_id,
        )
    clear_session_cookie(response, state.settings)


@router.post("/switch", response_model=SessionOut)
async def switch_org(
    body: SwitchOrg,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(require_authenticated())],
    state: Annotated[AppState, Depends(app_state)],
) -> SessionOut:
    if principal.kind != "user" or principal.user_id is None or principal.session_id is None:
        raise ApiError(400, "not_a_session", "Only browser sessions can switch organization")
    target = next(
        (o for o in await user_orgs(state.engine, principal.user_id) if o[1] == body.org), None
    )
    if target is None:
        raise ApiError(403, "forbidden", "You are not a member of that organization")
    async with tenant_tx(state.engine, principal.org_id, principal.user_id) as c:
        await sessions.revoke(c, principal.org_id, principal.session_id)
        await audit.record(
            c,
            principal.org_id,
            principal.actor,
            Action.SESSION_REVOKED,
            target_type="session",
            target_id=principal.session_id,
            details={"reason": "organization switch"},
        )
    async with tenant_tx(state.engine, target[0], principal.user_id) as c:
        token, sid, expires = await sessions.create_session(
            c, state.settings, target[0], principal.user_id, request.headers.get("user-agent")
        )
        await audit.record(
            c,
            target[0],
            principal.actor,
            Action.LOGIN,
            target_type="session",
            target_id=sid,
            details={"method": "organization switch"},
        )
    set_session_cookie(response, state.settings, token, expires)
    return SessionOut(authenticated=True, org_id=str(target[0]), role=target[3], expires_at=expires)


@dev_router.post("/login", response_model=SessionOut)
async def dev_login(
    body: DevLogin,
    request: Request,
    response: Response,
    state: Annotated[AppState, Depends(app_state)],
) -> SessionOut:
    """Development only: logs in as the given (pre-invited or member) email without an IdP."""
    if not (state.settings.dev_like and state.settings.auth_mode == "dev"):
        raise ApiError(404, "not_found", "Not found")
    check_csrf(request, state)
    rate_limit(state, f"login:{request.client.host if request.client else '-'}", 30, 10)
    ident = Identity(
        issuer="aegis-dev",
        subject=body.email.lower(),
        email=body.email.lower(),
        email_verified=True,
        name=None,
    )
    try:
        result = await complete_login(
            state.engine,
            state.settings,
            ident,
            org_slug=body.org,
            user_agent=request.headers.get("user-agent"),
            method="dev",
        )
    except LoginRefused as exc:
        raise ApiError(403, "login_refused", str(exc)) from None
    set_session_cookie(response, state.settings, result.token, result.expires_at)
    return SessionOut(authenticated=True, org_id=str(result.org_id), expires_at=result.expires_at)
