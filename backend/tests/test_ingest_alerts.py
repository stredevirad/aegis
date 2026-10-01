"""Alert normalisation (pure)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aegis.ingest.alerts import (
    AlertmanagerPayload,
    GenericPayload,
    from_alertmanager,
    from_generic,
    normalise_severity,
    safe_url,
)

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def _am(**alert):
    return AlertmanagerPayload.model_validate({"version": "4", "alerts": [alert]})


def test_severity_normalisation():
    assert [
        normalise_severity(s)
        for s in ("CRIT", "p1", "error", "warning", "minor", "info", "weird", None)
    ] == [
        "critical",
        "critical",
        "high",
        "medium",
        "low",
        "info",
        "medium",
        "medium",
    ]


def test_service_and_environment_label_precedence():
    a = from_alertmanager(_am(labels={"job": "j", "app": "a", "service": "s", "env": "prod"}), NOW)[
        0
    ]
    assert (a.service, a.environment) == ("s", "prod")
    b = from_alertmanager(_am(labels={"job": "j", "app_kubernetes_io_name": "k"}), NOW)[0]
    assert b.service == "k"


def test_unsafe_urls_are_dropped():
    for bad in (
        "javascript:alert(1)",
        "data:text/html,x",
        "http://user:pw@host/x",
        "//host/x",
        "ftp://h/x",
        "x" * 3000,
    ):
        assert safe_url(bad) is None, bad
    assert safe_url("https://grafana.example/d/abc?x=1") == "https://grafana.example/d/abc?x=1"


def test_start_times_reported_effective_and_clamped():
    unset = from_alertmanager(_am(labels={"alertname": "x"}, startsAt="0001-01-01T00:00:00Z"), NOW)[
        0
    ]
    assert unset.reported_start is None and unset.effective_start == NOW
    future = from_alertmanager(
        _am(labels={"alertname": "x"}, startsAt="2999-01-01T00:00:00Z"), NOW
    )[0]
    assert (
        future.clock_skew_clamped
        and future.effective_start == NOW
        and future.reported_start.year == 2999
    )
    small_skew = NOW + timedelta(minutes=2)
    ok = from_alertmanager(_am(labels={"alertname": "x"}, startsAt=small_skew.isoformat()), NOW)[0]
    assert not ok.clock_skew_clamped and ok.effective_start == small_skew


def test_fingerprints_are_stable_and_validated():
    a = from_alertmanager(_am(labels={"alertname": "x", "b": "2", "a": "1"}), NOW)[0]
    b = from_alertmanager(_am(labels={"a": "1", "alertname": "x", "b": "2"}), NOW)[0]
    assert a.fingerprint == b.fingerprint and len(a.fingerprint) == 32
    hostile = from_alertmanager(
        _am(labels={"alertname": "x"}, fingerprint="bad fp\n<script>"), NOW
    )[0]
    assert hostile.fingerprint == a.fingerprint or set(hostile.fingerprint) <= set(
        "0123456789abcdef"
    )


def test_labels_are_bounded_and_single_line():
    labels = {f"k{i:03d}": "v\nx‮" for i in range(100)}
    labels["alertname"] = "x"
    a = from_alertmanager(
        AlertmanagerPayload.model_validate({"alerts": [{"labels": labels}]}), NOW
    )[0]
    assert len(a.labels) == 64
    assert all("\n" not in v and "‮" not in v for v in a.labels.values())


def test_generic_alerts_group_by_service_and_environment():
    p = GenericPayload.model_validate(
        {
            "alerts": [
                {"name": "a", "service": "s", "environment": "e"},
                {"name": "b", "service": "s", "environment": "e"},
                {"name": "c", "service": "s", "environment": "other"},
            ]
        }
    )
    out = from_generic(p, NOW)
    assert out[0].group_key == out[1].group_key != out[2].group_key
    assert out[0].fingerprint != out[1].fingerprint
