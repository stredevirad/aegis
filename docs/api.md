# Aegis — API

Machine-readable contract: `GET /api/v1/openapi.json` (interactive docs at `/api/docs` in development profiles only). This page states the rules that the schema cannot express.

## Authentication

| Caller | Credential | Notes |
|---|---|---|
| Browser | `aegis_session` cookie (HttpOnly, SameSite=Lax, Secure outside development) | Issued by `/auth/*` (OIDC, or `/auth/dev/login` in development profiles only). The server stores only an HMAC of the token; the membership role is re-read on every request; idle (default 8 h) and absolute (24 h) expiry. |
| Machine | `Authorization: Bearer aegis_<prefix>_<secret>` | Created by an admin, shown once, stored as HMAC-SHA-256 with the server pepper. Scopes: `ingest:alerts`, `ingest:changes`, `read`. Mandatory expiry (≤ 365 days). **The key determines the organization; payloads cannot name one.** |

**CSRF.** Cookie-authenticated `POST/PUT/PATCH/DELETE` requests must send `X-Aegis-CSRF: 1` and an `Origin` equal to a configured origin (`AEGIS_ALLOWED_ORIGINS` or the public base URL). Bearer-key requests are exempt (not ambient credentials).

## Authorization (RBAC)

| Permission | admin | investigator | reviewer | viewer | API key scope |
|---|---|---|---|---|---|
| read incidents, investigations, evidence, change events, organization | ✓ | ✓ | ✓ | ✓ | `read` |
| create/update incidents | ✓ | ✓ | ✓ | | |
| start / retry / cancel investigations (egress, model spend) | ✓ | ✓ | | | |
| write change events | ✓ | ✓ | | | `ingest:changes` |
| ingest alerts | | | | | `ingest:alerts` |
| read audit log, verify chain | ✓ | | ✓ | | |
| approve remediation (M6) | ✓ | | ✓ | | |
| propose / execute approved remediation (M6) | ✓ | ✓ | | | |
| members, invitations, API keys, organization settings, integrations | ✓ | | | | |

No API-key scope maps to an administrative permission (enforced in `aegis.auth.rbac.NEVER_FOR_KEYS` and tested). Every `/api/v1` route declares exactly one permission; a test fails if one does not.

## Errors

```json
{"error": {"code": "not_found", "message": "Incident not found", "request_id": "…", "details": []}}
```

Stable codes include `unauthenticated` (401), `forbidden` and `csrf_failed` (403), `not_found` (404 — also returned for other tenants' resources), `conflict`, `invalid_transition`, `idempotency_conflict`, `not_active`, `last_admin` (409), `payload_too_large` (413), `validation_error` (422; locations and reasons only, never the submitted values), `rate_limited`, `queue_full`, `too_many_streams` (429, with `Retry-After`), `internal_error` (500, no internal detail). Every response carries `X-Request-ID` (an inbound value is accepted only if it is a short safe token).

## Resources

| Method and path | Permission | Notes |
|---|---|---|
| `GET /api/v1/me` | authenticated | user, organization, role, permissions, memberships |
| `GET /api/v1/incidents` | incident.read | filters `status`, `severity`, `service`, `environment`, `since`, `until`, `q` (title; wildcards escaped); `sort` = ±`created_at` / ±`started_at`; cursor pagination |
| `POST /api/v1/incidents` | incident.write | manual incident |
| `GET /api/v1/incidents/{id}` | incident.read | includes the latest investigation |
| `PATCH /api/v1/incidents/{id}` | incident.write | title, severity, status (allowed transitions only; resolving or closing ends alert grouping) |
| `GET /api/v1/incidents/{id}/alerts` | incident.read | alert occurrences with receive counts |
| `GET /api/v1/incidents/{id}/investigations` | investigation.read | |
| `POST /api/v1/incidents/{id}/investigations` | investigation.start | optional `source` (`scenario` or `live`), `window`; `Idempotency-Key` header; 201 created / 200 existing (one active per incident); 429 `queue_full` |
| `GET /api/v1/investigations` | investigation.read | filters `status`, `incident_id` |
| `GET /api/v1/investigations/{id}` | investigation.read | explicit incident and baseline windows, classification, support, root causes with evidence IDs, next checks, coverage gaps, `synthetic` flag |
| `POST /api/v1/investigations/{id}/cancel` | investigation.cancel | queued → cancelled immediately; running → cancel requested (stops at the next stage) |
| `POST /api/v1/investigations/{id}/retry` | investigation.start | new investigation with the same source and windows |
| `GET /api/v1/investigations/{id}/events?after=` | investigation.read | polling fallback |
| `GET /api/v1/investigations/{id}/stream` | investigation.read | Server-Sent Events; `id` = event sequence; resume with `Last-Event-ID` or `?after=`; ends after a terminal event or 5 minutes; per-user stream cap |
| `GET /api/v1/investigations/{id}/evidence` | evidence.read | filters `kind`, `modality`, `service`; cursor pagination |
| `GET /api/v1/investigations/{id}/evidence/{evidence_id}` | evidence.read | with provenance (`sources`: provider and query) |
| `GET /api/v1/investigations/{id}/graph` | evidence.read | candidates with tri-state checks, root causes, relations, decision trace |
| `GET /api/v1/investigations/{id}/snapshot` | evidence.read | counts, collection outcomes, digest, redaction record — never the raw payload |
| `POST /api/v1/alerts/alertmanager` | alert.ingest | Alertmanager webhook v4 |
| `POST /api/v1/alerts` | alert.ingest | generic `{"alerts": [...]}` |
| `POST /api/v1/change-events` | change.write | 201 created / 200 duplicate |
| `GET /api/v1/change-events` | change.read | filters `service`, `environment`, `kind`, `since`, `until` |
| `GET /api/v1/sources` | investigation.read | whether live collection is available; synthetic scenario IDs |
| `GET /api/v1/organization` · `PATCH /api/v1/organization/settings` | org.read · org.manage | |
| `GET/PATCH/DELETE /api/v1/members[/{user_id}]` | member.manage | removing a member ends their sessions and revokes the API keys they created; the last admin cannot be removed or demoted |
| `GET/POST/DELETE /api/v1/invitations[/{id}]` | member.manage | |
| `GET/POST /api/v1/api-keys`, `POST /api/v1/api-keys/{id}/rotate`, `DELETE /api/v1/api-keys/{id}` | apikey.manage | rotation keeps the old key valid for 24 h |
| `GET /api/v1/audit`, `GET /api/v1/audit/verify` | audit.read | |
| `GET /healthz` · `GET /readyz` | public | liveness (process) · readiness (database reachable and at the expected migration) |

## Limits

Request bodies above `AEGIS_MAX_BODY_BYTES` (default 1 MiB) are rejected before parsing; alert payloads carry at most 200 alerts, 64 labels each (values ≤ 1 KiB, annotations ≤ 4 KiB); ingestion is rate-limited per credential (`AEGIS_INGEST_PER_MINUTE`, `AEGIS_INGEST_BURST`; per API replica).
