"""Demo profile setup, run once by the `init-demo` service (deploy/compose.yaml --profile demo).

Signs in as the demo admin through the public API and:
1. creates an integration for each demo connector (deploy/connectors.demo.yaml), so the
   organization can collect from the demo Prometheus, Jaeger and Loki;
2. makes automatic investigations (of alert-created incidents) use live telemetry.

This is the setup backend/tests/integrations/test_live_demo.py does directly in the database.
Idempotent: an existing integration (409) is fine. DEMO MODE only (development login).
"""

from __future__ import annotations

import os
import sys

import httpx

API = os.environ.get("AEGIS_API_URL", "http://api:8000")
ORIGIN = os.environ.get("AEGIS_DEMO_ORIGIN", "http://localhost:8080")
ADMIN = os.environ.get("AEGIS_DEMO_ADMIN", "admin@example.com")
INTEGRATIONS = (
    ("demo-prom", "Prometheus (demo)"),
    ("demo-jaeger", "Jaeger (demo)"),
    ("demo-loki", "Loki (demo)"),
)


def main() -> int:
    headers = {"Origin": ORIGIN, "X-Aegis-CSRF": "1"}
    with httpx.Client(base_url=API, headers=headers, timeout=10) as client:
        r = client.post("/auth/dev/login", json={"email": ADMIN, "org": "demo"})
        if r.status_code != 200:
            print(f"init-demo: sign-in failed: HTTP {r.status_code}", file=sys.stderr)
            return 1
        for connector_id, name in INTEGRATIONS:
            r = client.post("/api/v1/integrations", json={"connector_id": connector_id, "name": name})
            if r.status_code not in (201, 409):
                print(f"init-demo: integration {connector_id}: HTTP {r.status_code} {r.text[:300]}", file=sys.stderr)
                return 1
            print(f"init-demo: integration {connector_id}: {'created' if r.status_code == 201 else 'exists'}")
        r = client.patch(
            "/api/v1/organization/settings",
            json={"auto_investigate": True, "default_source": {"kind": "live"}},
        )
        if r.status_code != 200:
            print(f"init-demo: settings: HTTP {r.status_code} {r.text[:300]}", file=sys.stderr)
            return 1
        print("init-demo: automatic investigations use live telemetry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
