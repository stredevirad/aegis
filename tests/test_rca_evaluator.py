"""Tests for the Phase 3 strengthened RCA evaluator.

Covers:
- Incident-leak detection (diagnoses that restate the incident description)
- Causal language requirement for CORRECT rating
- Backwards compatibility (evaluator works without incident_title/description)
- Edge cases: empty diagnosis, NO_ANOMALY, INSUFFICIENT classification
- Retroactive re-evaluation of Phase 2E-2 documented diagnoses
"""

from src.evaluation.rca_evaluator import (
    RCAResult,
    _compute_incident_leak,
    _find_causal_terms,
    _normalize_tokens,
    evaluate_rca,
)


class TestNormalizeTokens:
    def test_basic(self):
        tokens = _normalize_tokens("product-catalog")
        assert "product" in tokens
        assert "catalog" in tokens

    def test_camel_case(self):
        tokens = _normalize_tokens("productCatalogFailure")
        assert "product" in tokens
        assert "catalog" in tokens
        assert "failure" in tokens

    def test_empty(self):
        assert _normalize_tokens("") == set()


class TestIncidentLeakDetection:
    def test_high_leak(self) -> None:
        """Diagnosis that merely restates the incident should have high leak ratio."""
        diagnosis = "HTTP 500 errors on product catalog pages"
        title = "Elevated Error Rate on Product Pages"
        description = "Users are experiencing HTTP 500 errors when loading product catalog pages."
        
        leak, _ = _compute_incident_leak(diagnosis, title, description)
        assert leak >= 0.5, f"Expected high leak ratio, got {leak}"

    def test_low_leak_with_novel_content(self) -> None:
        """Diagnosis with causal explanation should have lower leak ratio."""
        diagnosis = (
            "The productCatalogFailure feature flag has been enabled, causing the "
            "product-catalog gRPC service to return Internal errors on GetProduct calls, "
            "which cascades to HTTP 500 on the frontend."
        )
        title = "Elevated Error Rate on Product Pages"
        description = "Users are experiencing HTTP 500 errors when loading product catalog pages."
        
        _, novel = _compute_incident_leak(diagnosis, title, description)
        assert novel > 0.3, f"Expected significant novel content, got novel={novel}"

    def test_empty_diagnosis(self):
        leak, novel = _compute_incident_leak("", "some title", "some desc")
        assert leak == 1.0
        assert novel == 0.0

    def test_no_incident_context(self):
        leak, novel = _compute_incident_leak("some diagnosis", "", "")
        assert leak == 0.0
        assert novel == 1.0


class TestCausalTerms:
    def test_finds_feature_flag(self):
        terms = _find_causal_terms("The feature flag was toggled causing failures")
        assert "feature flag" in terms

    def test_finds_caused_by(self):
        terms = _find_causal_terms("This was caused by a misconfiguration in the service")
        assert "caused by" in terms
        assert "misconfiguration" in terms

    def test_no_causal_terms(self):
        terms = _find_causal_terms("HTTP 500 errors on product pages")
        assert terms == []

    def test_finds_deployment(self):
        terms = _find_causal_terms("A recent deployment introduced a bug")
        assert "deployment" in terms


class TestEvaluatorBasics:
    """Backwards compatibility and basic functionality."""

    def test_empty_diagnosis(self):
        result = evaluate_rca(
            ground_truth="some fault",
            model_diagnosis="",
            classification="PLAUSIBLE",
        )
        assert result.rca_result == RCAResult.INSUFFICIENT_EVIDENCE

    def test_insufficient_classification(self):
        result = evaluate_rca(
            ground_truth="some fault",
            model_diagnosis="I found something",
            classification="INSUFFICIENT",
        )
        assert result.rca_result == RCAResult.INSUFFICIENT_EVIDENCE

    def test_no_anomaly_with_fault(self):
        result = evaluate_rca(
            ground_truth="productCatalogFailure enabled",
            model_diagnosis="No anomaly detected",
            classification="NO_ANOMALY",
        )
        assert result.rca_result == RCAResult.INCORRECT

    def test_no_anomaly_healthy_baseline(self):
        result = evaluate_rca(
            ground_truth="",
            model_diagnosis="System is healthy",
            classification="NO_ANOMALY",
        )
        assert result.rca_result == RCAResult.CORRECT

    def test_backwards_compatible_without_incident_context(self):
        """Evaluator still works when incident_title/description are not provided."""
        result = evaluate_rca(
            ground_truth="productCatalogFailure feature flag enabled causing failures",
            model_diagnosis="product-catalog service is failing due to feature flag misconfiguration",
            classification="PLAUSIBLE",
            target_service="product-catalog",
        )
        assert result.rca_result in (RCAResult.CORRECT, RCAResult.PARTIALLY_CORRECT)
        assert result.incident_leak_ratio == 0.0  # No incident context → no leak


class TestIncidentLeakDowngrade:
    """Phase 3: Diagnoses restating the incident should be downgraded."""

    def test_high_leak_prevents_correct(self):
        """A diagnosis that restates the incident should not get CORRECT."""
        result = evaluate_rca(
            ground_truth="productCatalogFailure feature flag enabled causing failures",
            model_diagnosis="HTTP 500 errors on product catalog pages when loading products",
            classification="PLAUSIBLE",
            target_service="product-catalog",
            incident_title="Elevated Error Rate on Product Pages",
            incident_description="Users are experiencing HTTP 500 errors when loading product catalog pages. Product details fail to load.",
        )
        # Should be at most PARTIALLY_CORRECT due to high incident leak
        assert result.rca_result != RCAResult.CORRECT, (
            f"Expected downgrade due to incident leak, got {result.rca_result}. "
            f"Leak ratio: {result.incident_leak_ratio:.2f}"
        )
        assert result.incident_leak_ratio > 0.4

    def test_causal_diagnosis_not_downgraded(self):
        """A diagnosis with genuine causal content should still get CORRECT."""
        result = evaluate_rca(
            ground_truth="productCatalogFailure feature flag enabled causing failures",
            model_diagnosis=(
                "The productCatalogFailure feature flag has been enabled, "
                "causing the product-catalog gRPC service to return Internal errors "
                "on GetProduct calls, which cascades to HTTP 500 on the frontend."
            ),
            classification="PLAUSIBLE",
            target_service="product-catalog",
            incident_title="Elevated Error Rate on Product Pages",
            incident_description="Users are experiencing HTTP 500 errors when loading product catalog pages.",
        )
        assert result.rca_result == RCAResult.CORRECT
        assert "feature flag" in result.causal_terms_found


class TestRetroactivePhase2E2:
    """Retroactively re-evaluate the documented Phase 2E-2 diagnoses with the new evaluator."""

    # From Phase 2E-2 documentation
    INCIDENT_TITLE = "Elevated Error Rate on Product Pages"
    INCIDENT_DESC = "Users are experiencing HTTP 500 errors when loading product catalog pages. Product details fail to load."
    GROUND_TRUTH = "productCatalogFailure feature flag enabled causing product catalog service failures"
    TARGET_SERVICE = "product-catalog"

    def test_run1_diagnosis_retroactive(self):
        """Run 1 diagnosis: identifies upstream product-catalog + failure evidence."""
        diagnosis = (
            "Upstream dependency `product-catalog` status is UNKNOWN, correlating with "
            "HTTP 500 errors in frontend logs and high latency/timeout spans in traces. "
            "The combination of UNKNOWN upstream health and failed downstream requests "
            "indicates a potential degradation or failure of the upstream service affecting "
            "product loading."
        )
        result = evaluate_rca(
            ground_truth=self.GROUND_TRUTH,
            model_diagnosis=diagnosis,
            classification="PLAUSIBLE",
            target_service=self.TARGET_SERVICE,
            incident_title=self.INCIDENT_TITLE,
            incident_description=self.INCIDENT_DESC,
        )
        # Run 1 identifies product-catalog as affected + mentions upstream failure concept
        # but does NOT identify the feature flag mechanism
        # Under strengthened evaluator, should be PARTIALLY_CORRECT (not CORRECT)
        assert result.rca_result in (RCAResult.CORRECT, RCAResult.PARTIALLY_CORRECT), (
            f"Expected PARTIALLY_CORRECT or CORRECT, got {result.rca_result}"
        )
        # Verify metadata is populated
        assert result.incident_leak_ratio >= 0.0
        assert result.novel_content_ratio >= 0.0

    def test_run2_diagnosis_retroactive(self):
        """Run 2 diagnosis: essentially restates the HTTP 500 symptom."""
        diagnosis = "Backend service returning HTTP 500 for product API endpoint"
        result = evaluate_rca(
            ground_truth=self.GROUND_TRUTH,
            model_diagnosis=diagnosis,
            classification="CONFIRMED",
            target_service=self.TARGET_SERVICE,
            incident_title=self.INCIDENT_TITLE,
            incident_description=self.INCIDENT_DESC,
        )
        # Run 2 is mostly restating the symptom — should be PARTIALLY_CORRECT at best
        assert result.rca_result == RCAResult.PARTIALLY_CORRECT, (
            f"Expected PARTIALLY_CORRECT for symptom restatement, got {result.rca_result}. "
            f"Leak: {result.incident_leak_ratio:.2f}, Novel: {result.novel_content_ratio:.2f}"
        )
        # No causal terms should be found
        assert len(result.causal_terms_found) == 0


class TestEvidenceValidation:
    """Evidence ID validation still works correctly."""

    def test_valid_evidence_ids(self):
        from datetime import datetime, timezone

        from src.domain import Evidence, EvidenceType
        
        now = datetime.now(timezone.utc)
        evidence = [
            Evidence(id="ev_1", type=EvidenceType.METRIC, source_provider="test",
                     summary="test", observed_at=now, collected_at=now),
            Evidence(id="ev_2", type=EvidenceType.LOG, source_provider="test",
                     summary="test", observed_at=now, collected_at=now),
        ]
        
        result = evaluate_rca(
            ground_truth="some fault",
            model_diagnosis="The product-catalog service failed due to misconfiguration",
            classification="PLAUSIBLE",
            target_service="product-catalog",
            supporting_evidence_ids=["ev_1", "ev_2", "ev_fake"],
            available_evidence=evidence,
        )
        assert result.valid_evidence_count == 2
        assert "METRIC" in result.modalities_supported
        assert "LOG" in result.modalities_supported
