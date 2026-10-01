"""API keys and session tokens (ADR-011).

* API key format: ``aegis_<prefix>_<secret>``; ``prefix`` is 8 base32 characters (shown in
  lists and logs), ``secret`` is 32 random bytes (base64url). The database stores only
  ``HMAC-SHA-256(pepper, full key)``, so a database dump does not yield usable keys and the
  lookup is an equality match on a keyed hash (no timing-sensitive comparison in Python).
* Session tokens are 32 random bytes (base64url) in an HttpOnly cookie; the database stores
  ``HMAC-SHA-256(pepper, "session:" + token)``.

The pepper is ``AEGIS_SECRET_KEY``. Rotating it invalidates every API key and session.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

API_KEY_RE = re.compile(r"^aegis_([a-z2-7]{8})_([A-Za-z0-9_-]{43})$")
SESSION_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
MAX_PRESENTED_KEY = 128


def _mac(pepper: bytes, purpose: str, value: str) -> bytes:
    return hmac.new(pepper, f"{purpose}:{value}".encode(), hashlib.sha256).digest()


@dataclass(frozen=True)
class NewApiKey:
    plaintext: str  # shown once at creation, never stored
    prefix: str
    secret_hash: bytes


def _random_prefix() -> str:
    return base64.b32encode(secrets.token_bytes(5)).decode().lower()  # exactly 8 chars


def new_api_key(pepper: bytes) -> NewApiKey:
    prefix = _random_prefix()
    secret = secrets.token_urlsafe(32)  # 43 chars
    plaintext = f"aegis_{prefix}_{secret}"
    return NewApiKey(
        plaintext=plaintext, prefix=prefix, secret_hash=hash_api_key(pepper, plaintext)
    )


def hash_api_key(pepper: bytes, presented: str) -> bytes:
    return _mac(pepper, "api-key", presented)


def parse_api_key(presented: str) -> str | None:
    """Return the prefix of a well-formed key, None otherwise (never raises)."""
    if len(presented) > MAX_PRESENTED_KEY:
        return None
    m = API_KEY_RE.fullmatch(presented)
    return m.group(1) if m else None


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(pepper: bytes, token: str) -> bytes:
    return _mac(pepper, "session", token)


def well_formed_session_token(token: str) -> bool:
    return bool(SESSION_TOKEN_RE.fullmatch(token))
