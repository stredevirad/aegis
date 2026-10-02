"""Encryption of stored credentials (ADR-015/016, security model C15).

``AEGIS_ENCRYPTION_KEYS`` is ``kid:base64key[,kid:base64key...]`` with 32-byte keys, newest
first. New ciphertexts use the first key; older ones stay readable while their key is listed
(rotation: prepend a new key, re-encrypt, then drop the old one).

Each ciphertext is AES-256-GCM with **associated data binding it to its owner** —
``(org_id, connector_id, integration_id)``. A ciphertext copied into another organization's
row, or re-pointed at another connector, fails authentication and is never decrypted
(Council C: "a credential is only ever sent to the connector it was bound to").

Development profiles without configured keys derive one from ``AEGIS_SECRET_KEY`` (never in
staging/production: configuration validation requires ``AEGIS_ENCRYPTION_KEYS``).
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from aegis.config import Settings


class KeyringError(ValueError):
    pass


class DecryptionFailed(Exception):
    """Wrong key, tampered data or wrong binding (never says which)."""


@dataclass(frozen=True)
class Sealed:
    kid: str
    blob: bytes  # nonce (12) || ciphertext+tag


class Keyring:
    def __init__(self, keys: list[tuple[str, bytes]]) -> None:
        if not keys:
            raise KeyringError("at least one encryption key is required")
        for kid, key in keys:
            if not kid or len(kid) > 32 or not kid.replace("-", "").replace("_", "").isalnum():
                raise KeyringError("key IDs must be short alphanumeric identifiers")
            if len(key) != 32:
                raise KeyringError(f"key {kid} must be 32 bytes")
        self._keys = dict(keys)
        self.primary = keys[0][0]

    @classmethod
    def from_settings(cls, settings: Settings) -> Keyring:
        if settings.encryption_keys is not None:
            entries = []
            for part in settings.encryption_keys.get_secret_value().split(","):
                kid, sep, b64 = part.strip().partition(":")
                if not sep:
                    raise KeyringError("encryption keys must be formatted kid:base64key")
                try:
                    entries.append((kid, base64.b64decode(b64, validate=True)))
                except ValueError:
                    raise KeyringError(f"key {kid} is not valid base64") from None
            return cls(entries)
        if not settings.dev_like:
            raise KeyringError("AEGIS_ENCRYPTION_KEYS is required outside development")
        derived = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None, info=b"aegis-dev-credential-key"
        ).derive(settings.secret_key.get_secret_value().encode())
        return cls([("dev", derived)])

    def seal(self, plaintext: bytes, binding: str) -> Sealed:
        nonce = os.urandom(12)
        ct = AESGCM(self._keys[self.primary]).encrypt(nonce, plaintext, binding.encode())
        return Sealed(self.primary, nonce + ct)

    def open(self, sealed: Sealed, binding: str) -> bytes:
        key = self._keys.get(sealed.kid)
        if key is None or len(sealed.blob) < 12 + 16:
            raise DecryptionFailed()
        try:
            return AESGCM(key).decrypt(sealed.blob[:12], sealed.blob[12:], binding.encode())
        except InvalidTag:
            raise DecryptionFailed() from None


def binding(org_id: object, connector_id: str, integration_id: object) -> str:
    return f"aegis-credential:v1:{org_id}:{connector_id}:{integration_id}"
