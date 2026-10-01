# Title: OIDC backend-for-frontend sessions and hashed API keys
# Status: Accepted
# Date: 2026-10-01
# Source: production-readiness audit; Council A (docs/councils/council-A-architecture.md)

## Decision
* Users authenticate with any OIDC provider (authorization code + PKCE) through the backend. The backend validates the ID token (issuer, audience, signature via JWKS, nonce, expiry), upserts the user, and creates a server-side session. The browser holds only an opaque session ID in an HttpOnly, Secure, SameSite=Lax cookie. Sessions have idle and absolute expiry and are revocable.
* State-changing requests require a matching Origin and a custom request header (CSRF defence).
* Machines use API keys `aegis_<prefix>_<secret>`: stored as HMAC-SHA-256 with a server pepper, scoped to one organization, a role and optional scopes (`ingest:alerts`, `ingest:changes`, `read`), with expiry and rotation. Webhook ingestion requires an ingest-scoped key; **the key determines the organization**, never the payload.
* Development uses Dex with static users. An insecure "dev login" mode exists only for local development and is rejected by configuration validation in staging/production.

## Alternatives rejected
SPA-held access/refresh tokens (exposed to XSS; refresh-token storage problem); local password database (unnecessary credential liability).
