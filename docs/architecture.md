# Aegis Architecture & Lifecycle Reference

This document details the architectural layers, data flows, state machine lifecycle, evidence provenance, and experimental workflows of Aegis.

---

## 1. System Architecture Diagram

```mermaid
flowchart TD
    subgraph Presentation_Layer [Presentation & CLI]
        DemoCLI[src.investigation.demo<br/>CLI & Experiment Harness]
        BatchRunner[scripts/run-fault-investigation.py<br/>Automated Evaluation Harness]
    end

    subgraph Investigation_Layer [Investigation & Orchestration Layer]
        Engine[InvestigationEngine<br/>Finite State Machine & Budget Controller]
        Dispatcher[ToolDispatcher<br/>Pydantic Schema Validation & Tool Registry]
        Summarizer[EvidenceSummarizer<br/>Pre-LLM Diagnostic Feature Extraction]
    end

    subgraph Domain_Layer [Domain Core (Strictly Isolated)]
        Models[Domain Models<br/>Incident, Hypothesis, Evidence, ServiceHealth]
        Interfaces[Provider Interfaces<br/>MetricsProvider, LogsProvider, TracesProvider, LLMProvider]
        Enums[Domain Enums<br/>InvestigationStatus, EvidenceType, DiagnosisClassification]
    end

    subgraph Adapter_Layer [Adapter & Integration Layer]
        PromAdapter[PrometheusProvider<br/>PromQL Range Query Translation]
        OSAdapter[OpenSearchProvider<br/>Elastic/OpenSearch DSL Query Translation]
        JaegerAdapter[JaegerProvider<br/>Trace Tree & Span Structural Normalization]
        OllamaAdapter[OllamaProvider<br/>Multi-Turn Tool Calling & Thinking Tag Parser]
        ReplayAdapter[ReplayProvider<br/>Deterministic Snapshot Replay Provider]
        MockAdapters[MockProviders<br/>In-Memory Synthetic Test Providers]
    end

    subgraph Persistence_Layer [Persistence & Audit Layer]
        SQLiteRepo[SQLiteRepository<br/>Idempotent Migration & Relation Persistence]
        SQLiteDB[(aegis.db / aegis_demo.db<br/>Incidents, Evidence, Hypotheses, Events, Recommendations)]
        ReplaySnap[EvidenceSnapshot<br/>JSON Serialization & Replay Engine]
    end

    subgraph Evaluation_Layer [Evaluation & Validation Layer]
        RCAEval[RCAEvaluator<br/>Incident-Leak Detection & Causal Term Extraction]
        HumanBench[Human Benchmark Dataset<br/>12-Case Single-Human Reference Annotation]
    end

    DemoCLI --> Engine
    BatchRunner --> Engine
    Engine --> Dispatcher
    Engine --> Summarizer
    Engine --> SQLiteRepo
    Engine --> RCAEval

    Dispatcher --> Interfaces
    Interfaces <|.. PromAdapter
    Interfaces <|.. OSAdapter
    Interfaces <|.. JaegerAdapter
    Interfaces <|.. OllamaAdapter
    Interfaces <|.. ReplayAdapter
    Interfaces <|.. MockAdapters

    SQLiteRepo --> SQLiteDB
    ReplaySnap --> ReplayAdapter
    RCAEval --> HumanBench
```

---

## 2. Investigation Lifecycle State Machine

Aegis implements an auditable, finite state machine with strict turn, tool call, and duration budget enforcement:

```mermaid
stateDiagram-v2
    [*] --> INCIDENT_CREATED: Incident Initialized

    INCIDENT_CREATED --> COLLECTING_EVIDENCE: Start Investigation (Fast or Iterative)

    state COLLECTING_EVIDENCE {
        [*] --> DispatchInitialQueries
        DispatchInitialQueries --> NormalizeEvidence: Prometheus / OpenSearch / Jaeger
        NormalizeEvidence --> PreLLMSummarization: Error Signals, Latency Stats, Severity Breakdown
        PreLLMSummarization --> [*]
    }

    COLLECTING_EVIDENCE --> FORMING_HYPOTHESES: Initial Evidence Context Sent to LLM

    state FORMING_HYPOTHESES {
        [*] --> ParseHypotheses
        ParseHypotheses --> RequestVerificationTools: Determine Missing Distinguishing Telemetry
        RequestVerificationTools --> [*]
    }

    FORMING_HYPOTHESES --> VERIFYING_HYPOTHESES: Execute Verification Tool Calls

    state VERIFYING_HYPOTHESES {
        [*] --> ExecuteTools: query_metrics / search_logs / search_traces / error_tools
        ExecuteTools --> StoreVerificationEvidence: Deduplicate & Save
        StoreVerificationEvidence --> [*]
    }

    VERIFYING_HYPOTHESES --> CONCLUDING: Verification Evidence Sent to LLM

    state CONCLUDING {
        [*] --> ExtractDiagnosis: Parse DIAGNOSIS & CLASSIFICATION
        ExtractDiagnosis --> LinkSupportingEvidence: Provenance Citation Validation
        LinkSupportingEvidence --> [*]
    }

    CONCLUDING --> RECOMMENDATION: Formulate Remediation Action

    state RECOMMENDATION {
        [*] --> ParseRecommendation: Action | Reason | Impact | Risk
        ParseRecommendation --> PersistRecommendation
        PersistRecommendation --> [*]
    }

    RECOMMENDATION --> RECORDED: State Fully Persisted to SQLite

    COLLECTING_EVIDENCE --> FAILED: Unhandled Exception / Budget Exceeded
    FORMING_HYPOTHESES --> FAILED: Budget Exceeded (Turns/Tools/Time)
    VERIFYING_HYPOTHESES --> FAILED: Budget Exceeded
    CONCLUDING --> FAILED: Budget Exceeded

    RECORDED --> [*]
    FAILED --> [*]
```

---

## 3. Evidence Normalization & Provenance Flow

```mermaid
sequenceDiagram
    autonumber
    participant App as Monitored Service (Frontend / Catalog)
    participant Telemetry as Telemetry Backend (Prometheus/OpenSearch/Jaeger)
    participant Adapter as Aegis Provider Adapter
    participant Engine as Investigation Engine
    participant SQLite as SQLite Persistence
    participant Evaluator as RCA Evaluator

    App->>Telemetry: Emit Metrics (PromQL), Logs (JSON), Traces (OTel Spans)
    Engine->>Adapter: query_error_metrics(service_name="product-catalog")
    Adapter->>Telemetry: Execute HTTP /api/v1/query_range (status_code=~5..)
    Telemetry-->>Adapter: Raw Backend-Specific Response
    Adapter->>Adapter: Normalize to Evidence(id, type, source, timestamp, data)
    Adapter-->>Engine: ToolResult(evidence=[Evidence], summary=CompactSummary)
    Engine->>SQLite: save_evidence(incident_id, evidence)
    Engine->>Engine: EvidenceSummarizer.summarize_evidence()
    Engine->>Engine: LLM produces Diagnosis + Cites Supporting Evidence IDs
    Engine->>SQLite: save_recommendation(supporting_evidence_ids=[...])
    Engine->>Evaluator: evaluate_rca(ground_truth, diagnosis, supporting_ids)
    Evaluator->>Evaluator: Validate IDs exist in SQLite + compute leak ratio
    Evaluator-->>Engine: RCAEvaluation(rca_result, leak_ratio, valid_evidence_count)
```

---

## 4. Controlled Fault-Injection Experiment Workflow

```mermaid
sequenceDiagram
    autonumber
    participant Harness as Experiment Harness (run-fault-investigation.py)
    participant Flagd as Flagd Service (:8080/feature)
    participant DemoApp as Astronomy Shop Services
    participant Telemetry as Telemetry Stack
    participant Aegis as Aegis Engine & Local LLM
    participant Evaluator as RCA Evaluator

    Harness->>Flagd: Ensure all feature flags OFF (Healthy Baseline)
    Harness->>DemoApp: Generate baseline synthetic traffic
    Harness->>Telemetry: Verify 0 error spans, 0 error logs
    Harness->>Flagd: Set productCatalogFailure defaultVariant="on"
    Harness->>DemoApp: Generate targeted catalog traffic (/api/products/OLJCESPC7Z)
    Harness->>Telemetry: Verify Differential (+11 error spans, HTTP 500 logs)
    Harness->>Aegis: Execute Autonomous Investigation (FAST / Error-Focused)
    Aegis->>Aegis: Collect Multi-Modal Evidence -> Hypotheses -> Verification -> Diagnosis
    Aegis-->>Harness: Investigation Completed (Incident ID, Diagnosis, Cited Evidence)
    Harness->>Evaluator: Run evaluate_rca() with incident-leak check & mechanism alignment
    Evaluator-->>Harness: Score Result (PARTIALLY_CORRECT / CORRECT / INCORRECT)
    Harness->>Flagd: Recover flag: productCatalogFailure="off"
    Harness->>Telemetry: Verify post-recovery health across all backends
```

---

## 5. Architectural Invariants

1. **No External Framework Dependencies**: The core agent loop does not depend on LangChain, CrewAI, AutoGen, or similar orchestration frameworks. This ensures complete transparency, strict budget limits, and straightforward debugging.
2. **Deterministic Fallbacks**: Every LLM extraction step features robust regex fallbacks with structured error handling so malformed LLM responses never crash the investigation.
3. **Immutability of Evidence**: Evidence objects once collected and saved cannot be modified. They are linked via immutable foreign keys in SQLite.
4. **Timezone Awareness**: All datetime objects throughout domain models, persistence layers, and adapter interfaces strictly enforce UTC ISO-8601 timestamps.
