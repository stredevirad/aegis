"""OIDC authorization-code + PKCE flow against a fake identity provider (in-process transport).

Covers the happy path and each validation the security model relies on: state (login CSRF),
nonce (token injection), PKCE verifier, issuer, audience, expiry, algorithm allowlist (no
``none``, no HMAC-with-public-key confusion), unknown signing keys, unverified email, open
redirects, tampered login-state cookies, IdP errors.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from aegis.api.app import create_app
from aegis.config import Profile, Settings
from tests.api.conftest import BASE

pytestmark = pytest.mark.db

ISSUER = "https://idp.test"
CLIENT_ID = "aegis"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(key, kid):
    d = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    return {**d, "kid": kid, "use": "sig", "alg": "RS256"}


class FakeIdP:
    def __init__(self):
        self.claims_override: dict = {}
        self.sign_key = KEY
        self.kid = "k1"
        self.alg = "RS256"
        self.last_token_request: dict | None = None
        self.expected_challenge: str | None = None
        self.nonce: str | None = None
        self.raw_token: str | None = None
        self.subject = f"user-{uuid.uuid4().hex[:8]}"
        self.email = f"oidc.{uuid.uuid4().hex[:8]}@example.com"

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/auth",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/keys",
                },
            )
        if path == "/keys":
            return httpx.Response(200, json={"keys": [_jwk(KEY, "k1")]})
        if path == "/token":
            form = parse_qs(request.content.decode())
            self.last_token_request = {k: v[0] for k, v in form.items()}
            verifier = self.last_token_request.get("code_verifier", "")
            digest = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .decode()
                .rstrip("=")
            )
            if digest != self.expected_challenge:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(
                200, json={"id_token": self.raw_token or self.token(), "token_type": "Bearer"}
            )
        return httpx.Response(404)

    def token(self) -> str:
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": self.subject,
            "iat": now,
            "exp": now + 300,
            "nonce": self.nonce,
            "email": self.email,
            "email_verified": True,
            "name": "O. User",
        }
        claims.update(self.claims_override)
        claims = {k: v for k, v in claims.items() if v is not ...}
        if self.alg == "none":
            return jwt.encode(claims, None, algorithm="none")
        return jwt.encode(claims, self.sign_key, algorithm=self.alg, headers={"kid": self.kid})


@pytest.fixture
def idp() -> FakeIdP:
    return FakeIdP()


@pytest.fixture
def oidc_app(app_engine, registry, idp):
    settings = Settings(
        profile=Profile.TEST,
        auth_mode="oidc",
        oidc_issuer=ISSUER,
        oidc_client_id=CLIENT_ID,
        oidc_client_secret="client-secret-value",
        public_base_url=BASE,
        allowed_origins=[BASE],
    )
    return create_app(
        settings,
        engine=app_engine,
        scenarios=registry,
        oidc_transport=httpx.MockTransport(idp.handler),
    )


async def _start(client: httpx.AsyncClient, idp: FakeIdP, **params) -> dict:
    r = await client.get("/auth/login", params=params)
    assert r.status_code == 302
    loc = urlsplit(r.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == f"{ISSUER}/auth"
    q = {k: v[0] for k, v in parse_qs(loc.query).items()}
    assert q["code_challenge_method"] == "S256" and q["response_type"] == "code"
    assert q["redirect_uri"] == f"{BASE}/auth/callback" and q["client_id"] == CLIENT_ID
    idp.expected_challenge = q["code_challenge"]
    idp.nonce = q["nonce"]
    assert "aegis_oidc" in r.cookies or any(
        "aegis_oidc" in c for c in r.headers.get_list("set-cookie")
    )
    return q


@pytest.fixture
async def invited(tenant_factory, su_conn, idp):
    t = await tenant_factory("oidc")
    await su_conn.execute(
        "INSERT INTO aegis.invitations (org_id, email, role, expires_at) VALUES ($1, $2, 'investigator', now() + interval '1 day')",
        t.org_id,
        idp.email,
    )
    return t


async def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


async def test_full_code_flow_with_pkce_creates_session(oidc_app, idp, invited):
    async with await _client(oidc_app) as c:
        q = await _start(c, idp, return_to="/incidents/123?tab=evidence")
        r = await c.get("/auth/callback", params={"code": "the-code", "state": q["state"]})
        assert r.status_code == 302 and r.headers["location"] == "/incidents/123?tab=evidence", (
            r.headers
        )
        assert idp.last_token_request["code"] == "the-code"
        assert idp.last_token_request["redirect_uri"] == f"{BASE}/auth/callback"
        me = (await c.get("/api/v1/me")).json()
        assert me["role"] == "investigator" and me["email"] == idp.email
        # the login state is single-use
        replay = await c.get("/auth/callback", params={"code": "the-code", "state": q["state"]})
        assert replay.headers["location"].startswith("/login?error=")


@pytest.mark.parametrize(
    ("mutate", "label"),
    [
        (lambda idp: idp.claims_override.update(aud="someone-else"), "audience"),
        (lambda idp: idp.claims_override.update(iss="https://evil.test"), "issuer"),
        (
            lambda idp: idp.claims_override.update(
                exp=int(time.time()) - 600, iat=int(time.time()) - 900
            ),
            "expired",
        ),
        (lambda idp: idp.claims_override.update(nonce="replayed-nonce"), "nonce"),
        (lambda idp: idp.claims_override.update(nonce=...), "missing nonce"),
        (lambda idp: idp.claims_override.update(sub=...), "missing sub"),
        (lambda idp: setattr(idp, "sign_key", OTHER_KEY), "bad signature"),
        (lambda idp: setattr(idp, "kid", "unknown-kid"), "unknown key"),
        (lambda idp: setattr(idp, "alg", "none"), "alg none"),
        (lambda idp: idp.claims_override.update(aud=[CLIENT_ID, "other"], azp="other"), "azp"),
    ],
)
async def test_invalid_id_tokens_are_rejected(oidc_app, idp, invited, mutate, label):
    mutate(idp)
    async with await _client(oidc_app) as c:
        q = await _start(c, idp)
        r = await c.get("/auth/callback", params={"code": "c", "state": q["state"]})
        assert r.status_code == 302 and r.headers["location"] == "/login?error=login_failed", label
        assert (await c.get("/api/v1/me")).status_code == 401, label


async def test_hmac_key_confusion_is_rejected(oidc_app, idp, invited):
    public_pem = KEY.public_key().public_bytes(
        encoding=__import__(
            "cryptography.hazmat.primitives.serialization", fromlist=["Encoding"]
        ).Encoding.PEM,
        format=__import__(
            "cryptography.hazmat.primitives.serialization", fromlist=["PublicFormat"]
        ).PublicFormat.SubjectPublicKeyInfo,
    )
    async with await _client(oidc_app) as c:
        q = await _start(c, idp)
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": "x",
            "iat": now,
            "exp": now + 300,
            "nonce": idp.nonce,
            "email": idp.email,
            "email_verified": True,
        }
        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "HS256", "kid": "k1"}).encode()
        ).rstrip(b"=")
        body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=")
        import hmac as _hmac

        sig = base64.urlsafe_b64encode(
            _hmac.new(public_pem, header + b"." + body, hashlib.sha256).digest()
        ).rstrip(b"=")
        idp.raw_token = (header + b"." + body + b"." + sig).decode()
        r = await c.get("/auth/callback", params={"code": "c", "state": q["state"]})
        assert r.headers["location"] == "/login?error=login_failed"


async def test_state_mismatch_missing_or_tampered_cookie(oidc_app, idp, invited):
    async with await _client(oidc_app) as c:
        await _start(c, idp)
        assert (
            (await c.get("/auth/callback", params={"code": "c", "state": "forged"}))
            .headers["location"]
            .startswith("/login?error=")
        )
    async with await _client(oidc_app) as c:  # no login-state cookie at all (login CSRF attempt)
        assert (
            (await c.get("/auth/callback", params={"code": "c", "state": "x"}))
            .headers["location"]
            .startswith("/login?error=")
        )
    async with await _client(oidc_app) as c:
        q = await _start(c, idp)
        c.cookies.set("aegis_oidc", c.cookies.get("aegis_oidc")[:-4] + "AAAA", path="/auth")
        assert (
            (await c.get("/auth/callback", params={"code": "c", "state": q["state"]}))
            .headers["location"]
            .startswith("/login?error=")
        )


async def test_pkce_verifier_must_match(oidc_app, idp, invited):
    async with await _client(oidc_app) as c:
        q = await _start(c, idp)
        idp.expected_challenge = "not-the-challenge"
        r = await c.get("/auth/callback", params={"code": "c", "state": q["state"]})
        assert r.headers["location"] == "/login?error=login_failed"


async def test_unverified_email_does_not_accept_invitations(oidc_app, idp, invited):
    idp.claims_override.update(email_verified=False)
    async with await _client(oidc_app) as c:
        q = await _start(c, idp)
        r = await c.get("/auth/callback", params={"code": "c", "state": q["state"]})
        assert r.headers["location"] == "/login?error=login_failed"


async def test_idp_error_and_open_redirects(oidc_app, idp, invited):
    async with await _client(oidc_app) as c:
        await _start(c, idp)
        r = await c.get("/auth/callback", params={"error": "access_denied", "state": "x"})
        assert r.headers["location"] == "/login?error=idp_error"
    for evil in (
        "//evil.example/x",
        "https://evil.example/",
        "/\\evil.example",
        "javascript:alert(1)",
    ):
        async with await _client(oidc_app) as c:
            q = await _start(c, idp, return_to=evil)
            r = await c.get("/auth/callback", params={"code": "c", "state": q["state"]})
            assert r.headers["location"] == "/", evil


async def test_dev_login_is_not_available_in_oidc_mode(oidc_app):
    async with await _client(oidc_app) as c:
        r = await c.post(
            "/auth/dev/login",
            json={"email": "a@example.com"},
            headers={"X-Aegis-CSRF": "1", "Origin": BASE},
        )
        assert r.status_code == 404
