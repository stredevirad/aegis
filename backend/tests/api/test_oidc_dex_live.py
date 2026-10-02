"""Live OIDC login against the development Dex (``docker compose -f deploy/compose.dev.yaml
--profile auth up -d``). Skipped when Dex is not reachable.

The test plays the browser: Aegis ``/auth/login`` → Dex login form → credentials → Dex
redirects to ``/auth/callback`` with a code → Aegis exchanges it (PKCE), validates the ID
token against Dex's real JWKS, accepts the invitation and issues a session.
"""

from __future__ import annotations

import re
import secrets
from html import unescape
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
import pytest

from aegis.api.app import create_app
from aegis.config import Profile, Settings

pytestmark = [pytest.mark.db, pytest.mark.live]

DEX = "http://127.0.0.1:5556/dex"
PUBLIC = "http://127.0.0.1:8000"


def _dex_up() -> bool:
    try:
        return httpx.get(f"{DEX}/.well-known/openid-configuration", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


@pytest.fixture
def dex_app(app_engine, registry):
    if not _dex_up():
        pytest.skip(
            "Dex not running (docker compose -f deploy/compose.dev.yaml --profile auth up -d)"
        )
    settings = Settings(
        profile=Profile.DEVELOPMENT,
        auth_mode="oidc",
        oidc_issuer=DEX,
        oidc_client_id="aegis",
        oidc_client_secret="aegis-dev-client-secret",
        public_base_url=PUBLIC,
        allowed_origins=[PUBLIC],
    )
    return create_app(settings, engine=app_engine, scenarios=registry)


async def _dex_login(auth_url: str, email: str, password: str) -> str:
    """Drive Dex's password form; return the redirect to Aegis's callback (not followed)."""
    async with httpx.AsyncClient(timeout=10) as browser:
        r = await browser.get(auth_url, follow_redirects=True)
        form = re.search(r'<form[^>]*method="post"[^>]*action="([^"]+)"', r.text, re.I)
        action = urljoin(str(r.url), unescape(form.group(1))) if form else str(r.url)
        r = await browser.post(action, data={"login": email, "password": password})
        for _ in range(5):
            location = r.headers.get("location", "")
            if location.startswith(PUBLIC):
                return location
            assert r.status_code in (302, 303), (r.status_code, r.text[:300])
            r = await browser.get(urljoin(str(r.url), location))
        raise AssertionError("Dex did not redirect back to Aegis")


async def test_login_through_dex(dex_app, tenant_factory, su_conn):
    t = await tenant_factory("dex")
    await su_conn.execute(
        "INSERT INTO aegis.invitations (org_id, email, role, expires_at) "
        "VALUES ($1, 'reviewer@example.com', 'reviewer', now() + interval '1 hour')",
        t.org_id,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=dex_app), base_url=PUBLIC
    ) as aegis:
        start = await aegis.get("/auth/login", params={"org": t.slug, "return_to": "/incidents"})
        assert start.status_code == 302 and start.headers["location"].startswith(f"{DEX}/auth")
        back = await _dex_login(start.headers["location"], "reviewer@example.com", "password")
        q = {k: v[0] for k, v in parse_qs(urlsplit(back).query).items()}
        done = await aegis.get("/auth/callback", params={"code": q["code"], "state": q["state"]})
        assert done.status_code == 302 and done.headers["location"] == "/incidents", (
            done.headers.get("location")
        )
        me = (await aegis.get("/api/v1/me")).json()
        assert (
            me["role"] == "reviewer"
            and me["org"]["slug"] == t.slug
            and me["email"] == "reviewer@example.com"
        )

        # wrong password: Dex never redirects back with a code
        start = await aegis.get("/auth/login")
        with pytest.raises(AssertionError):
            await _dex_login(
                start.headers["location"], "reviewer@example.com", secrets.token_hex(8)
            )
