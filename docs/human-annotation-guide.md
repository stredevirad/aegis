# Aegis RCA Evaluation — Human Annotation Guide

## Objective
This guide defines the methodology for human experts to independently evaluate Root Cause Analysis (RCA) diagnoses produced by Aegis against ground-truth incident facts.

---

## 1. Allowed Annotation Labels

Every candidate diagnosis must be assigned exactly one of the following four labels:

| Label | Definition | Required Criteria |
|:---|:---|:---|
| **`CORRECT`** | Full root cause identification | Accurately identifies the affected component **and** the specific root cause mechanism (e.g. specific feature flag, configuration change, cache disablement) backed by evidence. |
| **`PARTIALLY_CORRECT`** | Accurate localization / symptom correlation | Accurately identifies the affected service or downstream symptom, but does not pinpoint the underlying causal mechanism, or provides a valid high-level localization with minor ambiguity. |
| **`INCORRECT`** | Erroneous conclusion / hallucination | Identifies the wrong component, blames unrelated infrastructure (e.g., hallucinated Kubernetes crashes), or falsely claims `NO_ANOMALY` during an active outage. |
| **`INSUFFICIENT_EVIDENCE`**| Explicit epistemic uncertainty or missing output | Model explicitly and appropriately concludes `INSUFFICIENT` due to ambiguous telemetry, or output diagnosis is empty. |

---

## 2. Evaluation Principles & Guardrails

1. **Symptom Restatement $\neq$ Root Cause**:
   - Diagnoses that merely echo the incident prompt (e.g., *"Users are seeing HTTP 500 on product page"*) describe *what* happened, not *why*. If no causal mechanism is deduced, rate as `PARTIALLY_CORRECT` (if localized) or `INCORRECT` (if uninformative).
2. **Component Localization $\neq$ Causal Mechanism**:
   - Identifying that `product-catalog` is failing is necessary but not sufficient for `CORRECT`. To be `CORRECT`, the diagnosis must identify the failure mechanism (e.g. `productCatalogFailure` flag).
3. **Unsupported Infrastructure Blame**:
   - Fabricating database deadlocks, network partitions, or OOM crashes without evidence in the collected telemetry is `INCORRECT`.
4. **Epistemic Uncertainty is Legitimate**:
   - If telemetry is genuinely ambiguous (e.g. neutral incident descriptions with partial observability), concluding `INSUFFICIENT` is the correct and honest scientific behavior (`INSUFFICIENT_EVIDENCE`).
5. **Healthy Baselines**:
   - Concluding `NO_ANOMALY` when no fault is injected is `CORRECT`. Concluding `NO_ANOMALY` during an active fault is a false negative (`INCORRECT`).
6. **Empty Diagnoses**:
   - Blank or whitespace diagnoses are `INSUFFICIENT_EVIDENCE`.

---

## 3. Annotation Workflow

1. Open [`config/evaluator_human_annotations.json`](file:///c:/Users/LENOVO/Desktop/Aegis/config/evaluator_human_annotations.json).
2. For each case, review:
   - `ground_truth`
   - `incident_title` & `incident_description`
   - `model_diagnosis` & `classification`
3. Enter your independent assessment:
   - Set `"human_label"` to one of: `"CORRECT"`, `"PARTIALLY_CORRECT"`, `"INCORRECT"`, `"INSUFFICIENT_EVIDENCE"`.
   - Set `"human_rationale"` to a concise explanation of your decision.
4. Save the file and execute `pytest tests/test_human_annotation_validation.py -v -s` to calculate Observed Agreement, Cohen's Kappa, and the Confusion Matrix.
