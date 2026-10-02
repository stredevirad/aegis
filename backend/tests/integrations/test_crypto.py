"""Credential encryption: binding to (org, connector, integration), tamper detection, rotation."""

from __future__ import annotations

import base64
import os

import pytest

from aegis.config import Profile, Settings
from aegis.security.crypto import DecryptionFailed, Keyring, KeyringError, Sealed, binding


def _key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def test_round_trip_and_binding():
    kr = Keyring.from_settings(Settings(profile=Profile.TEST, encryption_keys=f"k1:{_key()}"))
    b = binding("org-a", "prom", "int-1")
    sealed = kr.seal(b'{"token": "x"}', b)
    assert kr.open(sealed, b) == b'{"token": "x"}'
    for other in (
        binding("org-b", "prom", "int-1"),
        binding("org-a", "loki", "int-1"),
        binding("org-a", "prom", "int-2"),
    ):
        with pytest.raises(DecryptionFailed):
            kr.open(sealed, other)


def test_tampering_and_unknown_keys_fail():
    kr = Keyring.from_settings(Settings(profile=Profile.TEST, encryption_keys=f"k1:{_key()}"))
    sealed = kr.seal(b"secret", "b")
    flipped = bytearray(sealed.blob)
    flipped[-1] ^= 1
    with pytest.raises(DecryptionFailed):
        kr.open(Sealed(sealed.kid, bytes(flipped)), "b")
    with pytest.raises(DecryptionFailed):
        kr.open(Sealed("unknown", sealed.blob), "b")


def test_rotation_keeps_old_ciphertexts_readable():
    old_key, new_key = _key(), _key()
    old = Keyring.from_settings(Settings(profile=Profile.TEST, encryption_keys=f"k1:{old_key}"))
    sealed = old.seal(b"v", "b")
    rotated = Keyring.from_settings(
        Settings(profile=Profile.TEST, encryption_keys=f"k2:{new_key},k1:{old_key}")
    )
    assert rotated.open(sealed, "b") == b"v"
    assert rotated.seal(b"v", "b").kid == "k2"


def test_invalid_key_material_and_production_requirement():
    for bad in (
        "k1",
        "k1:not-base64!!",
        f"k1:{base64.b64encode(b'short').decode()}",
        f"bad id:{_key()}",
    ):
        with pytest.raises(KeyringError):
            Keyring.from_settings(Settings(profile=Profile.TEST, encryption_keys=bad))
    with pytest.raises(KeyringError):
        Keyring.from_settings(Settings(profile=Profile.PRODUCTION))
    assert Keyring.from_settings(Settings(profile=Profile.DEVELOPMENT)).primary == "dev"
