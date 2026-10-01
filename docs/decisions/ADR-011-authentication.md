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

## Amendment 1 (Council C, 2026-10-01)
* **Identity-to-organization binding:** OIDC login never auto-joins an organization on an unverified email claim. Membership comes from an explicit invitation (accepted by a user whose verified email matches) or from an operator-configured mapping (verified email domain or IdP group → organization, role). The first administrator is bootstrapped from operator configuration.
* **Revocation:** removing a membership or disabling a user deletes their sessions immediately and disables API keys they created for that organization pending admin review. Sessions are revalidated against membership on every request (membership is looked up, not cached in the cookie); sessions have idle (default 8 h) and absolute (default 24 h) expiry.
* Viewers cannot start investigations or trigger any outbound call (egress and LLM spend are investigator-level actions).
