"""OpenID Connect authorization-code flow with PKCE, run by the backend (ADR-011).

Flow::

    GET /auth/login?org=&return_to=   → 302 to the IdP's authorization endpoint
    GET /auth/callback?code=&state=   → code exchanged server-side, ID token validated,
                                        session cookie set, 302 to return_to

* **Login state** (``state``, ``nonce``, PKCE ``code_verifier``, ``return_to``, org hint,
  creation time) travels in an AES-GCM-encrypted, HttpOnly, ``SameSite=Lax`` cookie scoped
  to ``/auth``, valid for 10 minutes and single-use. The ``state`` in the callback must equal
  the one in the cookie (login CSRF); the ID token's ``nonce`` must equal the stored nonce
  (token replay/injection).
* **ID token validation**: signature against the IdP's JWKS (fetched server-side, cached,
  refreshed once on an unknown ``kid``), algorithm from an allowlist (asymmetric only; never
  ``none`` or HMAC), ``iss`` equal to the configured issuer, ``aud`` containing the client ID
  (and ``azp`` equal to it when several audiences are present), ``exp``/``iat`` with 60 s
  leeway, ``sub`` present.
* **Back channel**: discovery, token and JWKS requests go from the server to the IdP with TLS
  verification, timeouts, no redirects and a response size cap. ``oidc_internal_issuer``
  lets containers reach the IdP under another base URL than the browser while ``iss`` is
  still checked against the public issuer.
* **return_to** must be a same-site absolute path (``/…`` but not ``//…`` or ``/\\…``):
  no open redirect.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from aegis.auth.login import Identity
from aegis.config import Settings

STATE_COOKIE = "aegis_oidc"
STATE_TTL_SECONDS = 600
ALLOWED_ALGS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "EdDSA")
MAX_RESPONSE_BYTES = 512 * 1024
_KEY_TYPE = {"RS": "RSA", "PS": "RSA", "ES": "EC", "Ed": "OKP"}
LEEWAY_SECONDS = 60


class OidcError(Exception):
    """Login cannot complete; the message is safe to show (no token contents)."""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def safe_return_to(value: str | None) -> str:
    if not value or len(value) > 500:
        return "/"
    if (
        not value.startswith("/")
        or value.startswith("//")
        or value.startswith("/\\")
        or "\\" in value
    ):
        return "/"
    if any(ord(c) < 0x20 for c in value):
        return "/"
    return value


# --- encrypted login-state cookie ------------------------------------------------------------


def _state_key(settings: Settings) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=b"aegis-oidc-login-state"
    ).derive(settings.secret_key.get_secret_value().encode())


@dataclass(frozen=True)
class LoginState:
    state: str
    nonce: str
    verifier: str
    return_to: str
    org: str | None
    created: float


def seal_state(settings: Settings, st: LoginState) -> str:
    nonce = os.urandom(12)
    payload = json.dumps(st.__dict__).encode()
    return _b64url(nonce + AESGCM(_state_key(settings)).encrypt(nonce, payload, b"aegis-oidc"))


def open_state(settings: Settings, sealed: str | None) -> LoginState:
    if not sealed or len(sealed) > 4096:
        raise OidcError("Login session missing or expired; start again.")
    try:
        raw = base64.urlsafe_b64decode(sealed + "=" * (-len(sealed) % 4))
        data = json.loads(AESGCM(_state_key(settings)).decrypt(raw[:12], raw[12:], b"aegis-oidc"))
        st = LoginState(**data)
    except Exception:
        raise OidcError("Login session invalid; start again.") from None
    if time.time() - st.created > STATE_TTL_SECONDS:
        raise OidcError("Login session expired; start again.")
    return st


# --- provider metadata ---------------------------------------------------------------------------


@dataclass
class _Cache:
    metadata: dict[str, Any] | None = None
    metadata_at: float = 0.0
    jwks: dict[str, Any] | None = None
    jwks_at: float = 0.0
    lock_free_until: float = 0.0


@dataclass
class OidcClient:
    settings: Settings
    transport: httpx.AsyncBaseTransport | None = None
    cache: _Cache = field(default_factory=_Cache)

    @property
    def issuer(self) -> str:
        assert self.settings.oidc_issuer
        return self.settings.oidc_issuer.rstrip("/")

    def _backchannel(self, url: str) -> str:
        """Rewrite a public IdP URL to the internal base the server should use."""
        internal = self.settings.oidc_internal_issuer
        if internal and url.startswith(self.issuer):
            return internal.rstrip("/") + url[len(self.issuer) :]
        return url

    async def _get_json(self, url: str) -> dict[str, Any]:
        return await self._request("GET", url)

    async def _request(self, method: str, url: str, **kw: Any) -> dict[str, Any]:
        async with httpx.AsyncClient(
            transport=self.transport,
            timeout=httpx.Timeout(10.0, connect=5.0),
            follow_redirects=False,
        ) as c:
            try:
                r = await c.request(method, self._backchannel(url), **kw)
            except httpx.HTTPError:
                raise OidcError("The identity provider is unreachable.") from None
        if len(r.content) > MAX_RESPONSE_BYTES:
            raise OidcError("The identity provider returned an oversized response.")
        if r.status_code >= 400:
            raise OidcError(f"The identity provider rejected the request (HTTP {r.status_code}).")
        try:
            data = r.json()
        except ValueError:
            raise OidcError("The identity provider returned invalid JSON.") from None
        if not isinstance(data, dict):
            raise OidcError("The identity provider returned an unexpected response.")
        return data

    async def metadata(self) -> dict[str, Any]:
        if self.cache.metadata is None or time.time() - self.cache.metadata_at > 3600:
            md = await self._get_json(self.issuer + "/.well-known/openid-configuration")
            if md.get("issuer", "").rstrip("/") != self.issuer:
                raise OidcError("The identity provider's issuer does not match the configuration.")
            for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
                if not isinstance(md.get(key), str):
                    raise OidcError(f"The identity provider metadata lacks {key}.")
            self.cache.metadata, self.cache.metadata_at = md, time.time()
        return self.cache.metadata

    async def _jwks(self, refresh: bool = False) -> dict[str, Any]:
        stale = self.cache.jwks is None or time.time() - self.cache.jwks_at > 3600
        if stale or (refresh and time.time() > self.cache.lock_free_until):
            md = await self.metadata()
            self.cache.jwks, self.cache.jwks_at = await self._get_json(md["jwks_uri"]), time.time()
            self.cache.lock_free_until = time.time() + 30  # at most one forced refresh per 30 s
        assert self.cache.jwks is not None
        return self.cache.jwks

    # --- flow ----------------------------------------------------------------------------------

    async def begin(self, return_to: str | None, org: str | None) -> tuple[str, LoginState]:
        md = await self.metadata()
        verifier = secrets.token_urlsafe(64)
        st = LoginState(
            state=secrets.token_urlsafe(32),
            nonce=secrets.token_urlsafe(32),
            verifier=verifier,
            return_to=safe_return_to(return_to),
            org=org,
            created=time.time(),
        )
        params = {
            "response_type": "code",
            "client_id": self.settings.oidc_client_id,
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(self.settings.oidc_scopes),
            "state": st.state,
            "nonce": st.nonce,
            "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()),
            "code_challenge_method": "S256",
        }
        sep = "&" if "?" in md["authorization_endpoint"] else "?"
        return f"{md['authorization_endpoint']}{sep}{urlencode(params)}", st

    @property
    def redirect_uri(self) -> str:
        return self.settings.public_base_url.rstrip("/") + "/auth/callback"

    async def finish(self, st: LoginState, code: str, state: str) -> Identity:
        if not secrets.compare_digest(state, st.state):
            raise OidcError("Login state mismatch; start again.")
        if not code or len(code) > 2048:
            raise OidcError("Missing authorization code.")
        md = await self.metadata()
        assert self.settings.oidc_client_secret is not None
        tokens = await self._request(
            "POST",
            md["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "code_verifier": st.verifier,
            },
            auth=(
                str(self.settings.oidc_client_id),
                self.settings.oidc_client_secret.get_secret_value(),
            ),
            headers={"Accept": "application/json"},
        )
        id_token = tokens.get("id_token")
        if not isinstance(id_token, str):
            raise OidcError("The identity provider returned no ID token.")
        claims = await self.validate_id_token(id_token, st.nonce)
        email = claims.get("email")
        return Identity(
            issuer=self.issuer,
            subject=str(claims["sub"]),
            email=email if isinstance(email, str) else None,
            email_verified=claims.get("email_verified") is True,
            name=claims.get("name") if isinstance(claims.get("name"), str) else None,
        )

    async def validate_id_token(self, token: str, expected_nonce: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise OidcError("Malformed ID token.") from None
        alg, kid = header.get("alg"), header.get("kid")
        if alg not in ALLOWED_ALGS:
            raise OidcError("ID token signed with a disallowed algorithm.")
        key = await self._key_for(kid, alg)
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=[alg],
                audience=self.settings.oidc_client_id,
                issuer=self.issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise OidcError(f"ID token rejected ({type(exc).__name__}).") from None
        aud = claims.get("aud")
        if (
            isinstance(aud, list)
            and len(aud) > 1
            and claims.get("azp") != self.settings.oidc_client_id
        ):
            raise OidcError("ID token rejected (authorized party).")
        if not isinstance(claims.get("nonce"), str) or not secrets.compare_digest(
            claims["nonce"], expected_nonce
        ):
            raise OidcError("ID token rejected (nonce).")
        return claims

    async def _key_for(self, kid: str | None, alg: str) -> Any:
        """The IdP's public key for this token: matching ``kid`` and a key type compatible
        with ``alg``. Symmetric (``oct``) keys are never used, so a token cannot be "signed"
        with HMAC over a public key (algorithm confusion)."""
        want = _KEY_TYPE[alg[:2]]
        for refresh in (False, True):
            jwks = await self._jwks(refresh=refresh)
            try:
                keyset = jwt.PyJWKSet.from_dict(jwks)
            except jwt.PyJWTError:
                raise OidcError("The identity provider's key set is invalid.") from None
            matches = [
                k for k in keyset.keys if k.key_type == want and (kid is None or k.key_id == kid)
            ]
            if kid is None and len(matches) > 1:
                raise OidcError("ID token does not identify its signing key.")
            if matches:
                return matches[0].key
        raise OidcError("ID token signed with an unknown key.")
