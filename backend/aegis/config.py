"""Validated configuration (environment variables with the ``AEGIS_`` prefix).

Profiles: ``development``, ``test``, ``staging``, ``production``. ``validate_for_profile``
refuses unsafe settings outside development/test (security model control C21): dev login,
insecure cookies, wildcard origins, debug logging, non-HTTPS public URL, default secrets.
Startup calls it and exits with every violation listed.

The profile defaults to ``production``: a deployment that forgets ``AEGIS_PROFILE`` gets the
strict checks and refuses to start with development defaults, instead of silently running
with dev login. Developers set ``AEGIS_PROFILE=development`` explicitly.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Profile(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


DEV_SECRET = "dev-only-secret-change-me-dev-only-secret-change-me"  # noqa: S105


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AEGIS_", env_file=None, extra="ignore")

    profile: Profile = Profile.PRODUCTION  # fail closed when unset
    # Runtime connection as the restricted application role (no table ownership, no BYPASSRLS).
    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://aegis_app:aegis_app@localhost:55432/aegis"
    )
    # Migration connection as the owning role. Only the migrate job needs it.
    migration_database_url: SecretStr | None = None
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_pool_max_overflow: int = Field(default=5, ge=0, le=100)
    db_statement_timeout_ms: int = Field(default=15_000, ge=100)

    public_base_url: str = "http://localhost:8000"
    allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://localhost:8000"]
    )
    secret_key: SecretStr = SecretStr(DEV_SECRET)  # pepper for API keys + session token MAC
    encryption_keys: SecretStr | None = None  # "kid:base64key,kid2:base64key" (newest first)

    auth_mode: str = Field(default="dev", pattern="^(dev|oidc)$")
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: SecretStr | None = None
    oidc_scopes: list[str] = Field(default_factory=lambda: ["openid", "email", "profile"])
    # Base URL the *server* uses to reach the IdP when it differs from the public issuer
    # (e.g. a container-network hostname); the ID token's iss is still checked against
    # oidc_issuer.
    oidc_internal_issuer: str | None = None
    cookie_secure: bool = False
    session_idle_minutes: int = Field(default=480, ge=5)
    session_absolute_minutes: int = Field(default=1440, ge=10)
    # Operator bootstrap: first organization and its admins (verified emails).
    bootstrap_org_slug: str = "default"
    bootstrap_admin_emails: list[str] = Field(default_factory=list)

    log_level: str = Field(default="INFO", pattern="^(DEBUG|INFO|WARNING|ERROR)$")
    log_json: bool = True
    otel_exporter_endpoint: str | None = None

    worker_id: str | None = None
    worker_concurrency: int = Field(default=2, ge=1, le=64)
    job_lease_seconds: int = Field(default=120, ge=5)
    worker_shutdown_grace_seconds: float = Field(default=30.0, ge=0, le=600)
    # Touched by the worker's maintenance loop every 10 s (exec liveness probe).
    worker_health_file: Path | None = None
    job_heartbeat_seconds: int = Field(default=15, ge=1)
    job_max_attempts: int = Field(default=3, ge=1, le=10)
    org_running_cap: int = Field(default=4, ge=1)
    org_queue_limit: int = Field(default=50, ge=1)
    # Operator-configured directory of synthetic scenarios usable as investigation sources
    # (demo / pipeline exercise). Unset in production unless deliberately enabled.
    scenario_dir: Path | None = None
    # Request bodies above this size are rejected before parsing (bounded ingestion, S-M2).
    max_body_bytes: int = Field(default=1_048_576, ge=1024, le=50_000_000)
    # Per-credential ingestion rate limit (per API replica; token bucket).
    ingest_per_minute: int = Field(default=600, ge=1)
    ingest_burst: int = Field(default=120, ge=1)
    sse_poll_seconds: float = Field(default=1.0, gt=0.05, le=30)
    sse_max_streams_per_user: int = Field(default=10, ge=1)

    @property
    def dev_like(self) -> bool:
        return self.profile in (Profile.DEVELOPMENT, Profile.TEST)


def validate_for_profile(s: Settings) -> list[str]:
    """Return every reason the configuration is unsafe for its profile (empty when OK)."""
    if s.dev_like:
        return []
    problems: list[str] = []
    if s.auth_mode != "oidc":
        problems.append("auth_mode must be 'oidc' outside development/test")
    if not (s.oidc_issuer and s.oidc_client_id and s.oidc_client_secret):
        problems.append("oidc_issuer, oidc_client_id and oidc_client_secret are required")
    elif not s.oidc_issuer.startswith("https://"):
        problems.append("oidc_issuer must use https")
    if s.oidc_internal_issuer and not s.oidc_internal_issuer.startswith("https://"):
        problems.append(
            "oidc_internal_issuer must use https (the back channel carries the client secret)"
        )
    if not s.cookie_secure:
        problems.append("cookie_secure must be true")
    if not s.public_base_url.startswith("https://"):
        problems.append("public_base_url must use https")
    if any(o == "*" or "*" in o for o in s.allowed_origins):
        problems.append("allowed_origins must not contain wildcards")
    if any(not o.startswith("https://") for o in s.allowed_origins):
        problems.append("allowed_origins must use https")
    secret = s.secret_key.get_secret_value()
    if secret == DEV_SECRET or len(secret) < 32:
        problems.append("secret_key must be set to a random value of at least 32 characters")
    if not s.encryption_keys:
        problems.append("encryption_keys must be set")
    if s.log_level == "DEBUG":
        problems.append("log_level must not be DEBUG")
    db = s.database_url.get_secret_value()
    if "aegis_app:aegis_app@" in db or "://postgres:" in db:
        problems.append(
            "database_url must use the restricted application role with a real password"
        )
    return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
