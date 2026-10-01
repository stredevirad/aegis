# Aegis — Production Readiness Audit

**Audit date:** 2026-10-01
**Audited state:** commit `dc7c032` on `feature/mcp-interoperability` (tagged `v1.1-research-freeze`); audit work continues on branch `production/platform`
**Method:** every file under `src/`, `scripts/`, `config/`, `eval/`, every document under `docs/`, the test suite, git history, both local SQLite databases, and the external OpenTelemetry Demo fixture were inspected directly. Defects marked **[R]** are confirmed by `scripts/audit/repro_research_defects.py` (runs offline, prints CONFIRMED / NOT REPRODUCED). Defects marked **[I]** were found by inspection only. An independent adversarial security review ran in parallel; its confirmed results are merged in §9 and marked **[S]**.

> Bottom line: Aegis today is a **well-documented, single-user research prototype** with a sound
> domain separation and genuinely useful research infrastructure (replay, evaluator, provenance
> idea, error-focused querying). It is **not** a product. There is no API, no UI, no
> authentication, no multi-user or tenant model, no durable job execution, no production
> database, no deployment artifacts, and no CI. Several correctness defects in the research engine
> (evidence silently moved between incidents, fabricated citations, keyword misclassification,
> telemetry queried for the wrong time window, trace error status discarded) mean the research
> engine **must not be promoted as-is**. The production platform has to be built as a new layer
> that reuses the research layer's ideas and replay assets, not its persistence or orchestration
> code.

---

## 0. Baseline measurements (taken 2026-10-01, before any change)

| Check | Result | Notes |
|---|---|---|
| `pytest tests/` | **160 passed, 5 skipped** in 71 s | Skips: Prometheus, OpenSearch, Jaeger, OTel-MCP, and MCP-transport live tests (Docker daemon not running). Ollama live test **passed** (Ollama 0.35.0 running with `qwen3.5:4b`, `qwen3.5:9b`, `llama3.2:1b`, `llama3:latest`). Docs claim 162/3; the difference is environmental. |
| `mypy src/ tests/` (strict) | **0 issues, 54 files** | |
| `ruff check src/ tests/` | clean | `ruff check scripts/` → **8 errors** (import order, unused imports). |
| `ruff format --check` | **52 of 61 files would be reformatted** | No formatter has ever been applied. |
| Non-editable install (`pip wheel .`) | **broken** | Wheel contains top-level `adapters/`, `domain/`, `investigation/` …; code imports `src.domain`. Works only from a checkout / editable install. |
| `pip-audit` of research runtime deps (pydantic 2.13.4, httpx 0.28.1, PyYAML 6.0.3, mcp 2.0.0) | no known vulnerabilities | Dependencies are lower-bound only (`>=`), no lock file. |
| Local toolchain | Python 3.13.0, Node 24.7, Docker 28.3 (daemon stopped), kubectl 1.32 client, Ollama 0.35, uv 0.12 | No PostgreSQL, Redis, or Helm binaries on the host. |

### Repository facts not visible from the docs

* **`aegis_demo.db` (gitignored, local only) is the real research record.** It holds 18 incidents, 38 hypotheses, 1,184 evidence rows, 278 events and 18 recommendations, including the runs cited in `docs/final-results.md` (`inc_fault_1787558017`, `inc_fault_1787558379`). `aegis.db` is empty. Because `*.db` is gitignored, the research record exists on one laptop only. **Action:** archive an export (see roadmap M0).
* **The external OpenTelemetry Demo fixture is locally modified.** At upstream commit `2e05c45` the `productCatalogFailure` flag has a `targeting` rule that returns `"off"` in both branches, which overrides `defaultVariant`. The local checkout deletes that rule. Without the deletion, `scripts/inject-fault.py --flag productCatalogFailure --value on` silently has no effect. Neither the deletion nor the fixture commit is recorded anywhere in the repository, so the documented Phase 2E/3 experiments are **not reproducible from a fresh clone** as written in `docs/reproducibility.md`.
* **Phase 5 snapshots are hand-authored.** `eval/snapshots/*.json` contain three items each, round timestamps, and (for example) a SQLAlchemy `QueuePool` log line attributed to `product-catalog` (a Go service in the OTel Demo that cannot emit it). They carry `source_provider: prometheus/opensearch/jaeger`. The Phase 5 doc does say "curated", but the provenance fields make synthetic data look captured. This is a labeling discrepancy, not misconduct.
* **Headline research numbers come from single runs.** "2.3× faster / 59 % noise reduction" (README, portfolio) compares one generic run with one error-focused run, and the evaluator rated **both runs INCORRECT** (`final-results.md` §4.3). "Summarizer prevents false negatives" is also one run per condition, against a deliberately weak baseline (truncation to two items).
* **The engine could not see what the experiments measured.** `scripts/run-fault-investigation.py` counts error spans from Jaeger `otel.status_code` tags, but `JaegerProvider._normalize_trace` drops all tags **[R]**, so the engine's "error traces" filter matches the words "error"/"fail"/"500" in summaries built from operation names.
* `master` is the only main branch (the tooling's "main" does not exist). `feature/mcp-interoperability` is 9 commits ahead of `master`; tags: `v1.0-research-complete` (`bba3749`), `v1.1-research-freeze` (`dc7c032`, added by this audit).

---

## 1. Current architecture

```
                 ┌──────────── CLI only (python -m src.investigation.demo / scripts/*.py) ─────────────┐
                 │                                                                                      │
 Prometheus ─┐   │   src/investigation/engine.py  (synchronous FSM, one incident per process)            │
 OpenSearch ─┼──►│     FAST mode: 3 fixed modality calls → summarizer → LLM "HYPOTHESIS:" lines         │
 Jaeger ─────┘   │       → ≤2 LLM-chosen verification calls → LLM "DIAGNOSIS:/CLASSIFICATION:/          │
 (sync httpx)    │         RECOMMENDATION:" lines → regex parse → SQLite                                 │
                 │     ITERATIVE mode: free tool loop with turn/tool/duration budgets                     │
 Ollama ────────►│   src/investigation/tools.py  ToolDispatcher (pydantic arg models, f-string PromQL)  │
 (/api/chat)     │   src/investigation/evidence_summarizer.py  (deterministic stats, keyword error tests) │
                 │   src/persistence/repository.py  SQLite, INSERT OR REPLACE, connection per call       │
                 │   src/persistence/replay.py  JSON snapshots → ReplayProvider                          │
                 │   src/evaluation/rca_evaluator.py  token-overlap evaluator + leak detection           │
                 │   src/adapters/mcp/  MCP server (stdio) + client (subprocess per call)                │
                 └──────────────────────────────────────────────────────────────────────────────────────┘
```

* **Domain** (`src/domain/`): pydantic models (`Incident`, `Evidence`, `Hypothesis`, `RemediationRecommendation`, `ProviderStatus`, LLM request/response) and abstract provider interfaces. No adapter imports. This boundary is real and is the best part of the codebase.
* **Engine:** a mutable `Incident` aggregate carries evidence and hypotheses in memory and is re-persisted wholesale on every transition. LLM output is free text parsed with regexes and keyword lists.
* **Persistence:** SQLite with no migrations beyond `ALTER TABLE … ADD COLUMN` in a try/except, no foreign keys, no indexes, no transactions spanning the aggregate.
* **Interfaces to humans:** none besides CLI printouts.

---

## 2. Twelve-way separation (prompt §0)

### 2.1 Existing research functionality (keep, version, protect)
Deterministic snapshot replay (`replay.py`) · RCA evaluator with incident-leak detection (`rca_evaluator.py`) · 12-case single-human annotation benchmark and κ harness (`config/evaluator_human_annotations.json`, `tests/test_human_annotation_validation.py`) · controlled fault injection via flagd (`scripts/inject-fault.py`) · batch experiment harness (`scripts/run-fault-investigation.py`) · error-focused vs generic query strategies · pre-LLM evidence summarizer · MCP overhead experiment · replay-based model comparison · `aegis_demo.db` run record · all `docs/phase-*.md`, `final-*.md`.

### 2.2 Existing demonstrable product functionality
Effectively none that a non-developer could use. Developer-demonstrable: an end-to-end CLI investigation against mock providers (0.2 s) or live OTel Demo + Ollama (2–6 min on CPU per docs); an MCP stdio server exposing read-only tools.

### 2.3 Unfinished functionality
`TopologyProvider` (only a hard-coded mock), `EnvironmentProvider`/deployment metadata (interface only), `RemediationProvider` (interface only; recommendations are parsed text), hypothesis verification (hypothesis `status`/`confidence` are never updated: every stored hypothesis is `UNKNOWN`), `get_service_health` for Prometheus (always `UNKNOWN`), `OTelDemoMCPAdapter.endpoint_url` (stored, never used — the client only speaks stdio and by default spawns **Aegis's own** server), permission boundaries promised in `requirements.md` ("Automatic, User Confirmation, Human Approval") — absent. `.env.example` advertises `DATABASE_URL`, `PORT`, `HOST`, `LOKI_ENDPOINT`, `TEMPO_ENDPOINT`, `MAXIMUM_EVIDENCE_ITEMS`, `MAXIMUM_CONTEXT_SIZE` that no code reads.

### 2.4 Architectural weaknesses
* Orchestration, prompting, parsing, persistence, and budget logic are interleaved in one 640-line class (`engine.py`).
* Synchronous, process-bound execution; no job model, no cancellation, no resumption; failure = `FAILED` with the exception text as reason.
* Free-text protocol with the model (`HYPOTHESIS:` / `DIAGNOSIS:` lines) and keyword fallbacks that silently invent results (§2.12, AI findings).
* Configuration via `os.environ` read **at import time** as default arguments (`engine.py:35-40`), YAML read ad hoc in two places with hard-coded `localhost` fallbacks.
* Query construction is provider-specific yet lives in the provider-agnostic tool layer (`tools.py:266-277` builds PromQL; `tools.py:300` builds Lucene).
* App-specific knowledge leaks into "generic" code, violating the repo's own `architecture-validation.md` rule 3: PromQL metric name `http_server_request_duration_seconds_count` (prometheus.py:69, tools.py:267), Jaeger path `/jaeger/ui/api` (OTel Demo ingress), OpenSearch index `otel-logs-*`, service aliases for `product`, `catalog`, `cart`, `payment` in the evaluator (rca_evaluator.py:252-259).
* Production code imports test code: `src/investigation/demo.py:161` imports `tests.mock_llm`.

### 2.5 Security weaknesses
See §9 (merged with the independent review). Headline items: no authentication or authorization of any kind; untrusted telemetry concatenated into the USER role of LLM prompts; f-string PromQL/Lucene construction from LLM-chosen arguments; LLM-controlled `trace_id` interpolated into a URL path; MCP server exposes all incidents' evidence and raw PromQL with no auth; MCP client allowlist **fails open** when empty; remote MCP evidence keeps server-supplied IDs and provenance (can overwrite other incidents' rows via the global-ID upsert).

### 2.6 Scalability weaknesses
One investigation per process; all provider calls serial and blocking; a fresh `httpx` client per Jaeger/Ollama call; every `save_incident` rewrites every evidence row of the incident (O(n) per transition, ~10 transitions per run → O(10n) writes); SQLite connection opened per call and never closed **[R]**; MCP client spawns a Python subprocess per tool call (4.6–5.8 s each, measured in Phase 4E).

### 2.7 UX / product gaps
No UI, no API, no incident list/detail, no live progress, no evidence explorer, no way to review/approve a recommendation, no notion of users, no alert ingestion, no history/search.

### 2.8 Operational / deployment gaps
No Dockerfiles, compose, Helm, CI, health endpoints, metrics, structured logging (only `logging.basicConfig` in `demo.py`), config validation, migrations, backup story, or retention. Local endpoint config (`config/opentelemetry-demo.yaml`) pins ephemeral Docker host ports (61092/61093) that differ from the ports quoted in docs (56020/56695).

### 2.9 Testing gaps
165 tests exist and are valuable for the research layer, but: no test exercises concurrent investigations, cross-incident isolation, persistence integrity, prompt injection, query injection, or provider timeouts on the real adapters; the mock LLM keys off exact prompt strings, so prompt edits break tests without behavior change; integration tests silently skip when infrastructure is down (correct for local use, but CI would show green with zero live coverage); no CI at all; no frontend/API tests (nothing to test).

### 2.10 Documentation discrepancies
| Claim | Reality |
|---|---|
| README badge "tests-162 passed", "165 tests (162 passed, 3 skipped)" | Environment-dependent; today 160/5. A static badge cannot be true. |
| README: "license-MIT", "see the LICENSE file" | **No LICENSE file exists.** The project currently has no license grant. |
| README quickstart `git clone https://github.com/your-username/aegis.git` | Placeholder URL. |
| `docs/reproducibility.md` fault injection steps | Fail silently on a fresh OTel Demo clone (targeting rule, see §0). |
| "2.3× faster", "59 % noise reduction" (README, portfolio) | N=1 per condition; both conditions INCORRECT. Must be caveated. |
| Phase 5 snapshots "Prometheus 5xx error rate metrics … OpenSearch … Jaeger" | Hand-authored fixtures labelled with real provider provenance. |
| `final-results.md` §4.1 "+11 error spans (`otel.status_code = ERROR`)" | Measured by the harness, invisible to the engine (§0). |
| "Evidence citations … successfully linked back to concrete trace IDs" | When the model cites nothing, IDs are chosen by keyword heuristics **[R]**. |
| `architecture-validation.md` "Application Neutrality" | Violated in four places (§2.4). |
| 36 absolute `file:///c:/Users/LENOVO/Desktop/Aegis/...` links in 5 docs | Broken for anyone else (and for this checkout, which lives at `D:\Aegis`). |
| `requirements.md` permission boundaries, `search_previous_incidents`, `get_metric_anomalies`, `get_deployment_metadata` tools | Not implemented. |
| `.env.example` variables | Seven are unused (§2.3); `MAXIMUM_INVESTIGATION_DURATION_SECONDS=210` vs engine default 900. |

### 2.11 Technical debt
Unformatted code base (52 files); lint errors in `scripts/`; `# type: ignore` on `traces_provider: TracesProvider = None`; duplicated ToolResult imports inside methods; dead `incident_start_time` parameter (stored, never used — the root of the wrong-window bug); `__pycache__`, `.mypy_cache`, `.ruff_cache` in the tree (gitignored, harmless); `walkthrough.md` duplicates `final-results.md`; hatch `packages` mapping produces an unusable wheel.

### 2.12 Opportunities for genuinely better RCA
1. **Baseline comparison**: nothing compares the incident window with a pre-incident baseline; "error count > 0" is treated as a signal even when it is normal background noise.
2. **Span status and propagation**: use `otel.status_code`, `error=true`, gRPC/HTTP status and parent/child structure to find the *deepest* failing span — the strongest single localization signal in microservices.
3. **Topology from traces**: derive caller→callee edges from parent/child spans across services instead of a hard-coded mock.
4. **Change correlation**: deployments, config, and feature-flag changes are the most common root cause class; Phase 3 already showed a 4B model "cannot deduce the feature-flag mechanism" — because the flag change was never evidence. Making change events first-class evidence turns that from a model limitation into a solvable retrieval problem.
5. **Log signature clustering**: template-mask numbers/IDs and compare signature frequencies baseline vs incident.
6. **Deterministic candidate ranking before the LLM**, so the model adjudicates between ranked, evidence-linked candidates instead of free-forming a diagnosis from a summary.
7. **Structured output + validation**: reject diagnoses citing non-existent evidence or services; abstain when validation fails.

---

## 3. What already works

| Capability | Evidence |
|---|---|
| Clean domain/adapter separation | `src/domain/` imports nothing from adapters; swapping mock/live/replay providers works. |
| Live adapters for Prometheus range queries, OpenSearch search, Jaeger trace search | Unit tests with mocked HTTP + historical live runs in `aegis_demo.db`. |
| Bounded investigation loop with turn/tool/duration budgets | `test_engine.py`, `test_llm_failure.py`. |
| Graceful degradation when one telemetry provider fails | `test_provider_degradation.py`, `test_partial_telemetry.py`. |
| Deterministic replay of evidence snapshots | `test_evidence_replay.py`. |
| Evaluator with incident-leak detection, κ = 0.778 vs one human on 12 cases | `test_human_annotation_validation.py`. |
| MCP server (stdio) and allowlisted client, verified against the OTel Demo's MCP service once | `test_mcp_*.py`, Phase 4 docs. |

## 4. What is research-only
The FAST/ITERATIVE engine, the regex response protocol, the evaluator's keyword heuristics, the SQLite repository, the demo CLI, all `scripts/` harnesses, the MCP overhead experiment, the replay model-comparison runner, the 3 scenario YAMLs (OTel-Demo-specific), the 2 hand-authored snapshots.

## 5. What can be reused
* **Domain concepts** (Evidence, Hypothesis, Incident, classification enum, provider capability discovery) — as the *design basis* for an upgraded, tenant-aware schema.
* **Adapter knowledge**: request shapes, normalization logic, time-bound validation of the three telemetry adapters (ported to async, with span status, safe query construction, egress policy).
* **Summarizer statistics** (percentiles, severity breakdown, message dedup) — folded into the deterministic analysis stage.
* **Replay format** — extended into a versioned scenario format that drives the production RCA pipeline in tests, benchmarks and the demo.
* **Evaluator** — retained for research comparability; a new structured evaluator is added alongside it, never replacing it.
* **MCP knowledge** — allowlist, payload bounds, mutating-tool blocklist concepts.

## 6. What must be refactored
Telemetry adapters (async, span status, safe query building, egress validation, bounded responses); evidence identity (incident-scoped surrogate keys + content fingerprints); MCP client (persistent sessions, streamable-HTTP transport for `endpoint_url`, fail-closed allowlist, response bounds, provenance re-stamping).

## 7. What must be replaced
SQLite repository → PostgreSQL with migrations and tenant scoping; regex/keyword LLM protocol → schema-validated structured output with citation validation; synchronous engine run → durable job + worker; `os.environ`-at-import config → validated settings object; keyword `_classify_diagnosis` and citation fallback → deterministic validators that *abstain* instead of guessing.

## 8. What must be newly built
REST API + OpenAPI · authentication (OIDC + API keys) · RBAC · tenant model · durable investigation jobs with leases, retries, cancellation, idempotency · deterministic RCA pipeline (baseline, anomaly, topology, change correlation, candidate ranking, verification) · LLM provider registry (Ollama, OpenAI-compatible, Anthropic) with health, fallback, circuit breaking, token/cost budgets · alert ingestion (generic webhook, Alertmanager) with dedup/grouping · notifications (Slack, PagerDuty, webhook) · remediation workflow (proposal → approval → execution → verification → rollback) with conservative policy · append-only audit log · retention · self-observability (OTel traces, Prometheus metrics, JSON logs) · web application · containers, compose stack, Helm chart, CI · expanded scenario benchmark and structured evaluator · reproducible demo environment.

---

## 9. Security findings

Severity reflects impact **once the system is multi-user and networked**; today most are latent because nothing listens on a network.

| ID | Sev | Finding | Where | Basis |
|---|---|---|---|---|
| SEC-01 | Critical | No authentication/authorization anywhere; the MCP server exposes every incident's evidence (`get_evidence`) and arbitrary PromQL (`query_metrics`) to any connected client. | `adapters/mcp/server.py:191-259` | I |
| SEC-02 | Critical | Untrusted telemetry (log bodies, span/operation names, error strings) is concatenated into **USER-role** prompt text with no delimiting or labeling; the system prompt does not tell the model that this content is data. A log line can instruct the model, forge `DIAGNOSIS:`/`CLASSIFICATION:` lines, or inject evidence-ID-shaped tokens. | `engine.py:331-340`, `evidence_summarizer.py:297-300`, `tools.py:313-315` | I (see S-items below) |
| SEC-03 | High | Query injection: LLM-chosen `service_name` is interpolated unescaped into PromQL regex (`tools.py:266-276`, `prometheus.py:69`) and Lucene `query_string` (`tools.py:300`, `opensearch.py:221`). `query_metrics` passes arbitrary PromQL through. Enables expensive queries against shared backends and reading series unrelated to the incident. | as listed | I |
| SEC-04 | High | LLM-controlled `trace_id` (≤100 chars, unvalidated) is interpolated into the Jaeger URL path (`jaeger.py:37`) — path manipulation on the Jaeger host. | `jaeger.py:37` | I |
| SEC-05 | High | MCP client allowlist fails open: empty allowlist ⇒ every remote tool allowed (`client.py:173`, `204`). | `client.py` | I |
| SEC-06 | High | Remote MCP evidence keeps server-supplied `id` and `source_provider`; combined with global-ID `INSERT OR REPLACE`, a hostile MCP server can overwrite or re-home other incidents' evidence and spoof provenance (`otel_demo.py:417-419` only re-stamps providers starting with "mcp"). | `client.py:283-309` | I + mechanism [R] (DATA-01) |
| SEC-07 | Medium | No SSRF/egress policy: provider endpoints come from YAML/env and are fetched verbatim; once endpoints are user-configurable (integrations UI) this becomes SSRF into the cluster/cloud metadata service. | all adapters | I |
| SEC-08 | Medium | Responses are unbounded in bytes: Prometheus range results, OpenSearch hits (`size: 100` but unbounded documents), Jaeger traces, MCP `TextContent` — all parsed fully into memory and persisted. | adapters | I |
| SEC-09 | Medium | Error strings from providers (including URLs and backend error bodies) flow into prompts, events, and MCP responses verbatim. | `engine.py:255-267`, `tools.py:209-215` | I |
| SEC-10 | Medium | No redaction: raw log bodies and attributes (which may contain tokens, emails, card numbers) are stored and sent to the model. | `opensearch.py:318-324` | I |
| SEC-11 | Low | Ollama base URL from env without validation; 600 s request timeout. | `ollama.py:196,285` | I |

**Independent adversarial review (merged):** see Appendix A. Items it confirmed with a proof of concept are marked **[S]** there and folded into the controls in the security model.

## 10. Reliability findings

| ID | Finding | Basis |
|---|---|---|
| REL-01 | Investigation lives in one process; a crash, deploy, or laptop sleep loses it. No resume, no retry, no cancellation. | I |
| REL-02 | Any exception anywhere → `FAILED` (`engine.py:121-123`); a transient LLM timeout destroys all collected evidence context. | I |
| REL-03 | When the LLM is unavailable the system produces nothing — no deterministic fallback summary. | I (`test_llm_failure.py` asserts FAILED) |
| REL-04 | No retries/backoff anywhere; no circuit breaking; Ollama timeout 600 s; Jaeger client created per call. | I |
| REL-05 | `_check_budgets` raises → FAILED, so hitting a budget discards partial findings instead of concluding INSUFFICIENT. | I |
| REL-06 | Persistence is not transactional across incident + hypotheses + evidence; a crash mid-save leaves partial state. | I |
| REL-07 | Event IDs `evt_{epoch_ms}` with `INSERT OR REPLACE`: two events in the same millisecond overwrite each other (audit loss). Latent on this machine — a full mock run stored 15/15 events because each SQLite write took >1 ms — but guaranteed on faster storage or concurrent writers. | R (collision), not reproduced in a full run |
| REL-08 | SQLite connections are never closed (`with sqlite3.connect()` only commits); files stay locked on Windows. | R |

## 11. Performance findings

Measured: mock investigation ≈0.2 s; live CPU investigations 137–351 s with `qwen3.5:4b` (from `aegis_demo.db` / final-results, N=1–2 per condition); stdio MCP cold start 4.6–5.8 s per call (Phase 4E, N=5).
By inspection: modality queries run serially; LLM inference dominates wall time; every transition rewrites all evidence rows; evidence grows unbounded (up to 259 items/run observed) and every item is persisted with full payload; `ITERATIVE` mode re-sends the entire conversation each turn. No performance budgets exist.

## 12. UX / product findings
There is no user interface. Investigation output is a terminal printout whose "Diagnosis" line is free text. Users cannot see why a conclusion was reached, which evidence supports it, what the model was uncertain about, or act on a recommendation. Hypotheses carry no status or confidence.

## 13. Deployment findings
Nothing is deployable: no service process, container, manifest, health endpoint, configuration validation, secret handling, or migration path. The only runtime topology is "a developer laptop with the OTel Demo on Docker Desktop and Ollama".

## 14. Data-model findings

| ID | Finding | Basis |
|---|---|---|
| DATA-01 | **Evidence is keyed by a global, deterministic ID** (`ev_trace_{trace_id}`, `ev_log_{md5(index::doc)}`, `ev_prom_{md5(query::labels::ts)}`) with `incident_id` as a plain column and `INSERT OR REPLACE`. When two incidents observe the same trace/log line, the later save **moves** the row: the earlier incident loses that evidence. | **R** |
| DATA-02 | No tenant/organization, user, or actor columns anywhere. | I |
| DATA-03 | No foreign keys, no indexes (every `load_incident` is a full scan of evidence/hypotheses/events), JSON stored as TEXT. | I |
| DATA-04 | Hypotheses are written but never updated; `status`/`confidence`/evidence links are always empty/UNKNOWN. | I |
| DATA-05 | Recommendations lack ID stability (`rec_{ms}`), status, approval, actor, risk taxonomy. | I |
| DATA-06 | Evidence has no relationships (log→trace, span→service, evidence→hypothesis), no fingerprint, no query provenance outside `data`, no redaction state. | I |
| DATA-07 | Timestamps stored as ISO TEXT; ordering by `observed_at` is lexical. | I |
| DATA-08 | No retention or deletion path. | I |

## 15. AI / RCA findings

| ID | Finding | Basis |
|---|---|---|
| AI-01 | **Keyword misclassification:** `_classify_diagnosis` falls back to substring checks; "payment-api is **unhealthy**: connection pool exhausted" → `NO_ANOMALY`. Also, the INSUFFICIENT classification set when verification is skipped (`engine.py:453`) is overwritten by `_store_diagnosis`. | **R** |
| AI-02 | **Fabricated citations:** when the model cites no evidence, `_extract_supporting_evidence_ids` picks up to five items whose text contains "error"/"500"/"fail" (or simply the first two items) and stores them as supporting evidence. The UI-to-be would show these as the basis of the diagnosis. | **R** |
| AI-03 | **Wrong time window:** every tool queries `now()-15m…now()`; the incident's `start_time` (passed to `ToolDispatcher` as `incident_start_time`) is never used. Investigating any incident older than 15 minutes analyzes unrelated telemetry. | **R** |
| AI-04 | **Span status discarded:** Jaeger normalization drops tags, so error spans are indistinguishable from successful ones except by name keywords. | **R** |
| AI-05 | Free-text protocol + three layers of fallback parsing ("bullet points", "first paragraph") convert arbitrary model output into hypotheses/diagnoses; there is no notion of an invalid response. | I |
| AI-06 | No baseline: an error count is interpreted without knowing the normal rate. | I |
| AI-07 | The model chooses verification queries freely in FAST mode (≤2 calls); nothing ties a verification call to a specific hypothesis, and hypothesis status is never updated. | I |
| AI-08 | The evaluator's mechanism check is keyword overlap with hard-coded app aliases; Phase 5 shows `llama3.2:1b` scored CORRECT while `component_localized=false`. | I (results file) |
| AI-09 | Chain-of-thought (`thinking`) is stored in the message history and re-sent; no policy on storing or exposing it. | I |
| AI-10 | No token/cost accounting beyond Ollama's eval counts; no context-window awareness; no model capability metadata. | I |

## 16. Testing gaps
See §2.9. In addition: tests never assert that persisted state survives a second incident (would have caught DATA-01); `test_llm_failure.py` codifies "LLM failure ⇒ FAILED" as correct behavior, which conflicts with the production requirement of graceful degradation; no property/fuzz tests on parsers that consume hostile input.

## 17. Dependency risks
* Runtime: `pydantic>=2`, `httpx>=0.24`, `PyYAML>=6`, `mcp>=2.0.0` — lower bounds only, no lock file; `mcp` 2.x is a young major line whose server API (`mcp.server.mcpserver.MCPServer`) differs from 1.x. A fresh install may resolve to a breaking future version.
* No vulnerabilities in installed versions (pip-audit, 2026-10-01).
* The global interpreter has many unrelated packages installed (FastAPI, SQLAlchemy, OpenTelemetry API…); tests could pass locally because of packages the project never declares. Production must build in an isolated, locked environment.
* No license file → downstream use of the code is legally unclear (**blocker for distribution**).

## 18. Production blockers (must be fixed before any real user)

1. No authentication, authorization, or tenant isolation (SEC-01, DATA-02).
2. Untrusted telemetry in the instruction channel of the model (SEC-02).
3. Evidence integrity: cross-incident evidence reassignment and fabricated citations (DATA-01, AI-02).
4. Wrong telemetry window and missing span status (AI-03, AI-04) — the engine analyzes the wrong data.
5. No durable execution; LLM failure yields nothing (REL-01..03).
6. No API/UI; nothing for an operator to use.
7. No deployment, configuration validation, or self-observability.
8. Unsafe query construction and missing egress policy (SEC-03, SEC-04, SEC-07).
9. No license.

---

## 19. Recommended target architecture (pre-council draft)

This is the draft submitted to Council A. The decided architecture, including what the council changed, is in [`production-architecture.md`](production-architecture.md) and the ADRs in [`decisions/`](decisions/).

1. **Keep the research layer frozen in place** (`src/`, `tests/`, `scripts/`, `eval/`, `config/`) so every research command, test and document keeps working; fix only its packaging and lint so it installs cleanly.
2. **Build the product as a new Python package `aegis`** under `backend/` (distribution name `aegis-platform`, avoiding a collision with the research distribution `aegis`). It must **not import `src.*`**; an automated test enforces this. It ports, rather than imports, the adapter and summarizer knowledge.
3. **Control plane:** FastAPI, OpenAPI, `/api/v1`, request IDs, stable error schema, cursor pagination.
4. **Persistence:** PostgreSQL 16 via SQLAlchemy 2 + Alembic. Every tenant-owned row carries `organization_id`; repositories require a tenant scope argument; Row-Level Security as defence in depth (decision for Council A/C).
5. **Jobs:** investigation jobs stored in PostgreSQL and claimed with `SELECT … FOR UPDATE SKIP LOCKED`, leases + heartbeats, bounded retries with jittered backoff, cancellation flag, idempotency keys. Redis only if a measured need appears (decision for Council A).
6. **Worker:** async Python process running the RCA pipeline with per-call, per-provider and total budgets.
7. **RCA pipeline:** deterministic stages (window → topology → baseline/anomaly → change correlation → candidate ranking → verification checks) produce an evidence graph; the LLM adjudicates via schema-validated structured output; validators reject unknown evidence IDs/services; the system abstains (`INSUFFICIENT`/`NO_ANOMALY`) rather than guessing; a deterministic summary is always produced even if the LLM is down.
8. **Auth:** OIDC authorization-code + PKCE handled by the backend (BFF) with server-side sessions in HttpOnly cookies; hashed, scoped API keys for machines; RBAC roles admin / investigator / reviewer / viewer, enforced server-side.
9. **Frontend:** React + TypeScript + Vite single-page app served by its own container, talking only to `/api/v1`, live updates over Server-Sent Events with polling fallback.
10. **Remediation:** typed action catalog; proposal → approval (role-gated, configurable two-person rule) → execution (disabled by default per organization) → post-action verification → rollback; every step audited.
11. **Self-observability:** OpenTelemetry traces, Prometheus `/metrics`, JSON logs correlated by request/investigation ID; `/healthz` (liveness) and `/readyz` (readiness).
12. **Deployment:** images for api, worker, frontend, migrations; Docker Compose dev stack with a lightweight *real* telemetry demo (instrumented services → OTel Collector → Prometheus/Jaeger/Loki) instead of requiring the 6 GB OTel Demo; Helm chart; GitHub Actions CI.

## 20. Rationale for major architectural decisions (pre-council)

| Decision | Rationale | Alternative rejected (for now) |
|---|---|---|
| New package, research frozen in place | Moving `src/` breaks every research import, script and doc link for no product gain; a frozen, tagged, still-tested research layer is the strongest form of preservation. | Moving to `research/` (needless breakage); refactoring `src/` into the product (inherits SQLite/regex/sync assumptions). |
| PostgreSQL for jobs as well as data | One stateful dependency to operate; transactional enqueue with the incident write (no dual-write); SKIP LOCKED is proven at the expected scale (tens of concurrent investigations, not thousands/sec). | Redis/Celery: second stateful system, dual-write between DB and queue, harder to make exactly-once with audit. |
| BFF cookie sessions | Tokens never reach browser JavaScript; revocation is a row delete; works with any OIDC IdP. | SPA-held access tokens (XSS exfiltration risk, refresh-token storage problem). |
| Deterministic analysis before the LLM | Small local models (the research's own finding) localize but do not reason causally; deterministic ranking makes the product useful without the model and gives the model a constrained choice. | Larger prompts / agent loops (the research showed noise hurts and loops are slow on CPU). |
| Lightweight real demo stack | 16 GB laptop cannot run the OTel Demo, the product stack and a 9B model together; a small instrumented system still produces real OTLP telemetry and a real, safely reversible remediation target. | OTel Demo as the default demo (kept as an optional live fixture). |

---

## Appendix A — Independent adversarial security review

*Merged after the review completes; until then this section is intentionally empty and no claim
in this document depends on it.*
