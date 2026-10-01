"""Configuration safety (security model control C21)."""

from __future__ import annotations

import pytest

from aegis.config import DEV_SECRET, Profile, Settings, validate_for_profile


def _safe(**overrides) -> Settings:
    base = {
        "profile": Profile.PRODUCTION,
        "auth_mode": "oidc",
        "oidc_issuer": "https://idp.example.com",
        "oidc_client_id": "aegis",
        "oidc_client_secret": "s" * 40,
        "cookie_secure": True,
        "public_base_url": "https://aegis.example.com",
        "allowed_origins": ["https://aegis.example.com"],
        "secret_key": "k" * 48,
        "encryption_keys": "k1:" + "A" * 44,
        "database_url": "postgresql+asyncpg://aegis_app:Str0ng-Pw@db:5432/aegis",
    }
    base.update(overrides)
    return Settings(**base)


def test_profile_defaults_to_production(monkeypatch):
    monkeypatch.delenv("AEGIS_PROFILE", raising=False)
    s = Settings()
    assert s.profile is Profile.PRODUCTION
    # An unconfigured deployment refuses to start instead of running with dev defaults.
    problems = validate_for_profile(s)
    assert any("auth_mode" in p for p in problems)
    assert any("secret_key" in p for p in problems)


def test_safe_production_configuration_passes():
    assert validate_for_profile(_safe()) == []


@pytest.mark.parametrize(
    ("override", "fragment"),
    [
        ({"auth_mode": "dev"}, "auth_mode"),
        ({"oidc_issuer": "http://idp.example.com"}, "https"),
        ({"cookie_secure": False}, "cookie_secure"),
        ({"public_base_url": "http://aegis.example.com"}, "public_base_url"),
        ({"allowed_origins": ["*"]}, "wildcards"),
        ({"allowed_origins": ["https://*.example.com"]}, "wildcards"),
        ({"allowed_origins": ["http://aegis.example.com"]}, "https"),
        ({"secret_key": DEV_SECRET}, "secret_key"),
        ({"secret_key": "short"}, "secret_key"),
        ({"encryption_keys": None}, "encryption_keys"),
        ({"log_level": "DEBUG"}, "DEBUG"),
        ({"database_url": "postgresql+asyncpg://postgres:x@db/aegis"}, "restricted"),
    ],
)
def test_each_unsafe_setting_is_rejected(override, fragment):
    problems = validate_for_profile(_safe(**override))
    assert any(fragment in p for p in problems), problems


def test_staging_is_as_strict_as_production():
    assert validate_for_profile(_safe(profile=Profile.STAGING, auth_mode="dev"))


def test_development_profile_is_explicit_opt_in():
    assert validate_for_profile(Settings(profile=Profile.DEVELOPMENT)) == []
