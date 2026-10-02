# Aegis — Integrations, Egress and Live Collection

## Connector registry (operator) and integrations (tenants)

Aegis makes outbound requests only to **connectors** the deployment operator lists in a YAML file (`AEGIS_CONNECTORS_FILE`, ADR-016). Tenants never type URLs: an organization admin creates an **integration** that references a connector ID bound to their organization and supplies a credential.

```yaml
connectors:
  - id: prom-main                 # [a-z0-9-]
    kind: prometheus              # prometheus | loki | opensearch | jaeger | tempo | kubernetes | llm | webhook | slack | pagerduty | flag_service | mcp
    url: http://prometheus.monitoring:9090   # exact scheme/host/port[/base path]; no credentials, query or fragment
    orgs: [acme]                  # organization slugs allowed to use it
    allow_private: true           # resolved address may be RFC 1918 / ULA / CGNAT
    insecure_http: true           # required outside development for plain http
    ca_file: /etc/aegis/ca.pem    # optional pinned CA (https only)
    timeout_seconds: 10           # 1–900
    max_response_bytes: 10485760
    options: {}                   # kind-specific (below)
```

Startup refuses a registry with wildcard or range hosts, URLs with credentials, duplicate IDs, entries pointing at Aegis's own database/API or `AEGIS_EGRESS_DENY` entries, plain http without `insecure_http` outside development, or `allow_loopback` outside development.

Credentials are stored AES-256-GCM-encrypted (`AEGIS_ENCRYPTION_KEYS`, key IDs for rotation) with associated data binding each ciphertext to *(organization, connector, integration)*: a ciphertext copied to another tenant's row is not decrypted and nothing is sent (tested). Credentials are write-only through the API.

## Egress policy (`aegis.integrations.egress`)

The single outbound path. Per request:

1. **Path allowlist per connector kind** (e.g. Prometheus `query`, `query_range`, build info — never the admin API; Ollama `chat`, `tags`, `version`, `show` — never `pull`/`push`/`delete`/`create`/`copy`; Jaeger trace IDs hex only). No traversal, encoded separators, query strings or control characters in paths.
2. **DNS resolved once; every answer checked.** Always denied: link-local incl. cloud metadata (169.254.169.254, fd00:ec2::254), Alibaba/Oracle metadata, unspecified, multicast, broadcast, reserved and documentation ranges; IPv4 embedded in IPv6 (mapped, NAT64, 6to4) is unwrapped first. Loopback only in development with `allow_loopback`; private ranges only with `allow_private`. A mixed answer (one allowed, one denied address) is refused.
3. **Aegis infrastructure denied** by name and by resolved address+port.
4. **Pinned connection**: connects to the vetted address with the original `Host` header and TLS SNI (certificate checked against the registered name); DNS rebinding between check and connect is impossible.
5. **No redirects**; streamed **response size cap**; connect/read/total **timeouts**; at most one jittered **retry**, only for GET/HEAD on connect/timeout/429/502/503/504; no proxy environment variables.
6. Credentials only in headers; provenance strings never contain them.

Allowed claim: *Aegis connects only to operator-registered endpoints and never to its own infrastructure or metadata addresses* (`tests/integrations/test_egress.py`, `tests/api/test_integrations.py`). Not claimed: "SSRF-proof".

## Live collection (`aegis.collection`)

`LiveCollector` builds a `TelemetrySnapshot` for an investigation's explicit windows from the organization's usable telemetry integrations:

| Kind | Adapter | Notes |
|---|---|---|
| `prometheus` | `query_range` with operator PromQL templates (`options.queries`, `$label`, `$services`, `$range`) | defaults use the OTel HTTP server histogram with `service_name`; service names RE2- and string-escaped; error ratio zero-filled so baselines exist; request-weighted counts; NaN/Inf dropped |
| `jaeger` | `/api/traces` per service and window | span status from `otel.status_code`, else `error=true`, else UNSET; parents, kind, `peer.service`, HTTP status, instance; allowlisted attributes only |
| `loki` | `query_range`, warn and above | severity from OTLP structured metadata / `detected_level`; trace context; truncation reported |
| `opensearch` | `/{index}/_search` with JSON query DSL | `terms`/`range` filters only (never `query_string`); field names from operator options |
| changes | Aegis's own `change_events` | feed `ok` if any change event in 30 days, else `not_configured` (caps the verdict at PLAUSIBLE) |

Every adapter reports `ok`, `partial` (some sub-queries failed or a limit was hit), `failed` (with a category) or `not_configured`; missing data is a coverage gap, never "healthy". Redaction runs at collection time; counts are stored with the snapshot. If every backend fails the job retries while attempts remain; the last attempt still analyses and reports the failures (INSUFFICIENT with coverage gaps). `tempo` and `kubernetes` connectors are accepted by the registry but their collectors are **not implemented** in this version (reported as failed outcomes).

## Demo telemetry stack

`docker compose -f deploy/compose.yaml --profile demo up -d --build`: four hand-instrumented services (frontend → checkout, catalog → payments; checkout and catalog call an uninstrumented `postgres`), a flag service (the reversible fault and the remediation target; reports its changes as change events), a load generator, OpenTelemetry Collector, Prometheus (with an error-rate alert), Alertmanager (webhook to Aegis), Jaeger and Loki. Inject a fault: `curl -X PUT localhost:18081/flags/catalog.error_rate -H 'content-type: application/json' -d '{"value":"0.3","actor":"me"}'`.

Contract fixtures in `backend/tests/integrations/fixtures/` were recorded from this stack under a real fault (2026-10-02).
