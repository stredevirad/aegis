# Council C — Threat model pressure test

**Date:** 2026-10-01 · **Protocol:** installed `llm-council` skill (5 independent advisors in parallel → 5 anonymized peer reviews → chairman synthesis).

## Framed question

DECISION: Pressure-test the complete Aegis threat model, especially telemetry prompt injection, SSRF, MCP, credentials, multi-tenancy, authorization and remediation. Is the proposed security design sufficient, and what is missing or wrong?

SYSTEM: Aegis is a multi-tenant web platform for AI-assisted incident investigation. Components: React SPA (served by nginx) -> FastAPI API (stateless) -> PostgreSQL 16 (all state incl. jobs, sessions, audit) <- async workers that pull telemetry from customer backends (Prometheus, Loki/OpenSearch, Jaeger/Tempo, Kubernetes API), call LLMs (local Ollama by default; OpenAI-compatible and Anthropic optional), call external MCP tool servers (read-only allowlist), send notifications (Slack, PagerDuty, webhooks), and execute ONE kind of approved remediation (setting a feature flag on a flag service). Inbound: Alertmanager / generic webhooks, change-event webhooks, browser users, API-key clients. Expected: a few organizations, tens of users, deployed by the customer (Docker Compose or Kubernetes/Helm), often inside the network where the observability backends live.

CONFIRMED ATTACKS ON THE RESEARCH PROTOTYPE (proof-of-concept, must not recur in the product):
- Evidence takeover across incidents via global deterministic IDs + INSERT OR REPLACE; unscoped evidence listing returned every incident's raw data.
- Query injection: PromQL label-selector breakout via a service name (`"} or on() secret_metric{x=~".*`), raw PromQL passthrough, Lucene filter neutralisation (`x) OR (*`).
- Prompt-injection delivery: a log whose service.name contained newlines and a forged "=== SYSTEM ===" header landed verbatim in the USER prompt, repeated per occurrence.
- Fabricated citations by keyword fallback; verification skipped yet CONFIRMED; first-enum-match classification.
- Credentials in endpoint URLs (user:token@host) leaked into provider errors, the LLM prompt, the events table and logs.
- MCP: hostile server spoofed first-party provenance (source_provider="prometheus"); empty allowlist allowed every tool incl. mutating ones; a remote tool could shadow a built-in tool; responses unbounded (10,000 items / 500 KB accepted).
- Jaeger path/parameter injection through an LLM-chosen trace_id ("../../../api/services", "x?limit=9999").
- Tool budget checked only between turns (50 calls executed against a budget of 12); 600 s model timeouts; concurrent writes lost 186/200 recommendations.

PROPOSED PRODUCT SECURITY DESIGN (decided by Council A; pressure-test it):
1. AuthN: OIDC authorization code + PKCE handled by the backend (BFF); server-side sessions; opaque session ID in HttpOnly, Secure, SameSite=Lax cookie; idle + absolute expiry; CSRF defence = Origin check + required custom header on state-changing requests. Dev uses Dex; an insecure dev-login mode is refused by config validation outside development.
2. API keys `aegis_<prefix>_<secret>` stored as HMAC-SHA-256 with a server pepper; scoped to one org + role + scopes (ingest:alerts, ingest:changes, read); expiry/rotation. Webhook ingestion requires an ingest-scoped key and the KEY determines the org, never the payload.
3. AuthZ: per-org roles admin / investigator / reviewer / viewer, enforced server-side per route; remediation approval needs reviewer and (default) a different person from the proposer.
4. Tenancy: org_id on every tenant table + PostgreSQL RLS with FORCE; app connects as non-owner role without BYPASSRLS; SET LOCAL aegis.org_id per transaction; pre-org operations (session lookup, API-key lookup, job claim) via narrow SECURITY DEFINER functions; repository-level org filters too; cross-tenant leak tests per repository and route.
5. Prompt injection: deterministic analysis produces candidates + a confidence ceiling; the LLM gets telemetry only inside a delimited, labelled untrusted-data block (control chars stripped, per-field caps) and returns schema-constrained JSON; validator rejects unknown evidence IDs/services/candidates, confidence above ceiling, classification above the deterministic one; picking a non-leading candidate caps at PLAUSIBLE. Model thinking discarded.
6. Queries: built only from validated parameters (service names validated/escaped as label values; metric names from a configured mapping; trace IDs ^[0-9a-f]{1,32}$, URL-encoded); never from free model/user text; result-size caps.
7. Egress/SSRF: one HTTP client for all outbound calls; http/https only; DNS resolved once and pinned; link-local/metadata always denied; loopback/private denied unless the DEPLOYMENT allowlists them (observability backends are usually internal); redirects off; response size caps; timeouts; credentials never in URLs.
8. Data protection: redaction of secrets/PII at collection time and again before prompting; per-org LLM provider allowlist, cloud providers opt-in, no automatic local->cloud fallback; integration/provider credentials encrypted at rest (key from env/secret manager, key IDs for rotation), never returned after creation; per-org retention.
9. Audit: append-only table, app role has no UPDATE/DELETE grant, per-org SHA-256 hash chain, verification endpoint.
10. MCP: fail-closed allowlist, schema validation, argument/result bounds, timeouts, persistent sessions, remote IDs namespaced and provenance forced to mcp:<server>, built-in tool names reserved, mutating tools unreachable outside remediation.
11. Remediation: disabled per org by default; enabled by admin; one action type (feature flag set) end to end; idempotent by proposal ID; never retried blindly; post-action verification; rollback = inverse action through the same approval path.
12. Ops: config validation rejects wildcard CORS, debug, unauthenticated mode, mutation-enabled-by-default in production; secret scanning in CI; dependency and image scanning; SBOM.

QUESTIONS FOR THE COUNCIL:
(a) What attack paths remain? Think like an attacker who controls: log/trace content in one tenant; an Alertmanager instance; an MCP server; a viewer account; an admin account in tenant A; the LLM provider; the network between Aegis and a backend.
(b) Is the egress design right when the legitimate targets are internal (private IPs)? How should per-tenant integration URLs be constrained so tenant A cannot point Aegis at tenant B's backends or at Aegis's own database/metadata service?
(c) Is RLS + SECURITY DEFINER + SET LOCAL correct and complete? Pitfalls (connection pooling, background jobs, migrations, superuser in tests, views, functions)?
(d) Remediation: is "one real action + approval + two-person rule" enough? What about approval of a proposal whose parameters were influenced by injected telemetry? Replay/double execution? Stale approvals?
(e) What is the minimum set of security TESTS that would convince a hostile auditor, and what claims must NOT be made?
(f) Anything over-engineered for the stated scale, or a control that will be bypassed in practice?

WHAT IS AT STAKE: A tenant leak, an SSRF into the customer's network/cloud metadata, a prompt-injected confident diagnosis, or an unapproved/destructive remediation would each be disqualifying. The owner's audit will actively try these.


## Advisor responses

ADVISOR RESPONSES (de-anonymized; in peer review: Outsider=A, Executor=B, First Principles=C, Contrarian=D, Expansionist=E)

**The Contrarian:**

The biggest flaw is in item 7. The private-range allowlist is set per deployment, not per tenant. Every real backend is internal, so operators will allowlist 10.0.0.0/8 and the control stops doing anything. Tenant A's admin can then point a "Prometheus" integration at:
- tenant B's Loki;
- Aegis's API port, skipping nginx;
- Ollama on :11434, which has unauthenticated /api/pull and /api/delete;
- the Kubernetes API, using Aegis's own service account.

The fix is for the operator to own each org's allowed hosts, CIDR ranges and ports. Tenant admins pick from that list and never type URLs. Slack, PagerDuty and webhook destinations are attacker-chosen egress too. LLM-written text sent to Slack can carry data out through link unfurling.

**Second, the deterministic layer is the soft target.** You hardened the LLM against injection, but the deterministic analyzer eats the same telemetry. Anyone who can emit logs or metrics can forge an error spike on service X. The attacker then sets the confidence ceiling, the evidence IDs are genuine, and the validator passes. The result is a confident, wrong diagnosis with real citations. Never claim "injection-resistant." The honest claim is "bounded by telemetry integrity."

**Third, MCP tool descriptions and schemas go into the model's tool list, above your untrusted-data block.** A hostile server sets readOnlyHint itself. Pin each description by hash when the tool is allowlisted. Also: why can MCP mutate anything at all when the only remediation is one flag-service call?

**Fourth, approval must bind a hash of exactly what will run.** That means flag key, value, target and prior state, with an expiry. Re-read the flag's state before executing and abort if it has changed. Key and value must come from an org-configured catalog, never from LLM output. Otherwise reviewers rubber-stamp injected parameters. Also, routing rollback through the same two-person approval means it waits for a second human in the middle of an incident.

**Fifth, the hash chain doesn't detect tampering by the database owner or migration role.** They can simply recompute it. Anchor it outside the database or drop the claim. SECURITY DEFINER functions without `SET search_path` can be hijacked.

**Last, one Alertmanager can starve every tenant.** The worker queue is shared and has no per-org quotas.

**The First Principles Thinker:**

You're asking the wrong question. The threat model is organized by component: RLS, SSRF, MCP. But what actually needs protecting is Aegis's position. It is a deputy holding credentials, sitting inside the customer's network, and it acts on text written by attackers. Every disqualifying outcome comes from that one fact.

**1. The tenant boundary is the network, not the rows.** Control 7 puts the private-IP allowlist at the deployment level. Picture a deployment that allows 10.0.0.0/8 and an org admin who can type a URL. Tenant A points an integration at tenant B's Loki, or at postgres:5432, and RLS plays no part at all. Two rules fix this:
- Org admins never type URLs. The deployment operator registers endpoints and binds each one to an org, so orgs pick an endpoint ID.
- Aegis's own database, API and worker addresses are always denied.

Go one step further. "A few orgs, deployed by the customer" means that if tenants truly distrust each other, they belong in separate deployments. Treat org_id/RLS as defense in depth, not as the wall. Then the SECURITY DEFINER and pooling pitfalls become backup risks rather than single points of failure.

**2. Stop asking how to keep the model from being fooled.** Assume it is always fooled, and ask what a fooled model can change:
- Confidence: capped by the deterministic ceiling. Good.
- Remediation parameters: nothing in the design covers these.

The rule should be that the LLM may only pick a pre-registered remediation by ID from a runbook a human wrote. The flag key and value never come from the model or from telemetry. Then the reviewer is approving a known action, not content an attacker shaped. Bind each approval to a hash of (action ID, parameters, the target's current state). Execute with compare-and-set, and let approvals expire within minutes. That handles stale approvals and double execution.

**3. Weigh remediation's value against its cost.** It is the only write credential in a system that ingests hostile text. Unless remediation is the product's core thesis, v1 should emit a signed instruction that a human runs.

**Claims not to make:** "prompt-injection resistant." The provable claim is narrower: model output cannot raise confidence above the deterministic ceiling, and cannot trigger any action a human did not author in advance.

**The Expansionist:**

The design is being treated as a cost centre. It is actually the product. Three of its controls are features that competitors in AI ops don't have, and Council A is underselling them.

**1. Proof-carrying diagnoses.** The validator already ties every claim to evidence IDs, and the queries are built deterministically from validated parameters. Go one step further. Store the exact query, backend, time range and a result hash with each piece of evidence, and add a replay endpoint. The auditor can then re-run any cited claim and get the same answer. That answers (e) better than any test list. It also gives you a sales line: "every AI conclusion can be re-checked." Regulated buyers (SOC 2, DORA, banks) will pay for that.

**2. Make the attack corpus a shipped artifact.** The prototype exploits are a ready-made adversarial suite: PromQL breakout, the forged SYSTEM header, MCP provenance spoofing, Jaeger path traversal and budget overrun. Package it as `aegis selftest --adversarial`, which customers run against their own deployment. That turns the owner's audit into a regression gate and builds credibility.

**3. Turn integrations into a connector registry.** For (b): only the deployment operator registers backends. Each entry is a pinned host, port and CA. Tenants bind to registered connectors and never type raw URLs. That closes the cross-tenant SSRF problem. It also gives you a connector registry that can later become a catalogue or marketplace.

**4. Turn approvals into signed change tickets.** For (d): each approval should bind a hash of the parameters, the evidence snapshot hash, an expiry and a single-use nonce. That kills stale approvals, replay, and approvals of parameters steered by injected logs. The same ticket can then flow into ServiceNow or Jira change management. Do that and Aegis becomes the system of record for AI-proposed changes, and adding more action types later costs little.

**5. The MCP controls are a standalone product.** Fail-closed allowlist, namespacing, forced provenance and result bounds add up to an "MCP firewall" that everyone wiring agents into production needs.

**Don't cut RLS for "only a few orgs."** It is what makes a hosted SaaS version possible later without a rewrite.

**The Outsider:**

**1. The design never says who is trusted.** It says "deployed by the customer" and also "multi-tenant", so who are the tenants? Probably teams inside one company that share one operator, one identity provider (IdP) and one database superuser. Whoever holds those can create a second login and beat the two-person rule, rebuild the hash chain (nothing outside the database anchors it), and read the encryption key from the environment. That is acceptable, but write it down: "the operator and the IdP are trusted." Don't imply that the audit chain or the two-person rule protects against them. This also answers (b): only the operator sets per-org integration URL allowlists. A tenant admin never does, or tenant A can point Aegis at anything.

**2. The browser is missing.** None of the twelve controls covers the screen where the reviewer reads attacker-written log lines, alert labels, generatorURLs and LLM prose, and then clicks Approve. One XSS from a rendered field or a `javascript:` link defeats your CSRF defence, because a script running on your own origin sends the Origin and the custom header like the real app. You need a strict CSP, no raw HTML or markdown rendering of telemetry or model output, and an allowlist of URL schemes for links. Notifications have the same problem: Slack and webhooks push attacker text to people, and outbound webhooks are an egress path the SSRF section never names.

**3. The attacker doesn't need the LLM.** The "confidence ceiling" comes from the same telemetry the attacker writes. Emit fake errors from service X and the deterministic layer will honestly blame X. The claim you can defend is "never more confident than the data," not "can't be fooled."

**4. "Set a feature flag" is not harmless.** A flag can turn off checkout or login. Each org needs an allowlist of flag keys and their allowed values. The approver should see the literal key, the old value and the new value, not the model's account of the change.

**The Executor:**

The design is fine on paper. The danger is that twelve controls ship as twelve half-finished features. Monday morning, turn every confirmed prototype attack into a failing test in `tests/hostile/` and make it block merges in CI. That list is the auditor's script. Keep building until it passes.

1. **Tenancy first.** Run the leak tests as `aegis_app`, a role that doesn't own the tables and has no BYPASSRLS. Tests run as `postgres` bypass RLS and pass without proving anything. Policies should use `NULLIF(current_setting('aegis.org_id', true), '')::uuid`, because a pooled connection that ran `SET LOCAL` in an earlier transaction returns `''`, not NULL. Test that a query with no SET returns zero rows. Add a CI catalog query checking that every table with `org_id` has RLS enabled and forced and has a policy. That catches the table someone forgets next month.

2. **Egress (b) takes one day.** In v1, tenants don't type URLs. Operator config defines the `host:port` targets each org may use, and admins pick from that list. Config validation rejects broad CIDRs and Aegis's own Postgres and API endpoints. When you pin the resolved IP, still check TLS against the original hostname. That breaks easily.

3. **Query builders and the LLM validator.** Each PoC string becomes a test fixture: `"} or on()...`, `x) OR (*`, `../../../api/services` and the forged `=== SYSTEM ===` header.

4. **Remediation last (d).** The approval stores a hash of the exact (flag_key, value, env) and expires after 15 minutes. Execute with `UPDATE ... SET status='executing' WHERE id=$1 AND status='approved' AND params_hash=$2 AND expires_at>now() RETURNING id`. It returns one row or nothing, which kills replays and stale approvals. Flag keys and values come from a list the admin maintains, so injected telemetry can't choose the target.

**Cut (f):** ship MCP disabled in v1 and defer the hash-chain verification endpoint.

**Claims (e):** never say "injection-proof." Claim only three things, each backed by a named test: injected text can't push confidence above the deterministic ceiling, can't cite evidence that doesn't exist, and can't reach another tenant.


## Peer reviews

PEER REVIEWS (reviewers saw responses labelled A–E: A=Outsider, B=Executor, C=First Principles, D=Contrarian, E=Expansionist)

**Reviewer 1:**
1. Strongest: D. It is the only response that finds new, concrete attack paths instead of restating the design. Its SSRF targets are Ollama on :11434, which has unauthenticated /api/pull and /api/delete, and the Kubernetes API reached through Aegis's own service account. It points out that MCP tool descriptions and readOnlyHint reach the model above the untrusted-data block. It also flags SECURITY DEFINER functions without `SET search_path`, a hash chain the database owner can recompute, rollback stuck waiting for two-person approval, and no per-org queue quotas. B comes second on testability: NULLIF for pooled connections, running tests as aegis_app, and compare-and-set execution.
2. Biggest blind spot: E. It turns a threat review into a sales pitch (marketplace, "MCP firewall"). It skips the attacker personas in (a), the RLS pitfalls in (c), XSS, and poisoning of the deterministic layer. Its replay endpoint is a new query-execution surface and cannot reproduce results from live backends that keep changing. Its evidence-snapshot hash still lets a reviewer approve parameters that injected text chose.
3. All five missed: the hostile LLM provider (receives all telemetry after redaction and can return schema-valid, under-ceiling but deliberately wrong choices; no response requires caps on model output size, TLS verification for LLM endpoints, or saying that redaction is only heuristic); what the Kubernetes reads can see (Secrets, environment variables); session revocation when a user is removed from the IdP or loses a role.

**Reviewer 2:**
1. Strongest: D. Names attack paths the design actually allows: a deployment-wide 10.0.0.0/8 allowlist exposes Ollama's unauthenticated /api/pull and /api/delete, the K8s API under Aegis's own service account, and the API without nginx in front. MCP tool descriptions and a self-set readOnlyHint reach the model above the untrusted-data block. SECURITY DEFINER without SET search_path, a hash chain the DB owner can recompute, starvation of a shared queue, rollback stuck waiting for a second approver mid-incident. B second, most buildable specifics: NULLIF on current_setting, a compare-and-set UPDATE, a catalog check that RLS is on.
2. Biggest blind spot: E. Sells the controls instead of attacking them, barely touches (a) or (c). Its replay endpoint adds a new stored-query path open to SSRF that re-exposes data; replays stop working once retention deletes the data. "Closes the cross-tenant SSRF problem" claims more than a registry can deliver.
3. All five missed: the worker is the real tenant boundary. One process decrypts every org's credentials with a single key. No one asked that each credential be tied to the job's org_id, for a separate key per org, or for a test where a job runs with another org's credential. Also open: how IdP identities map to org membership (email or group-claim auto-join), and two attacker positions listed in (a), a hostile LLM provider and a viewer account.

**Reviewer 3:**
1. Strongest: D. The only response that works through the listed attackers with specific attack paths (Ollama /api/pull and /api/delete, Kubernetes API under Aegis's service account, MCP descriptions and self-declared readOnlyHint above the untrusted-data block, deterministic layer as the real soft target, re-read the flag's prior state before running, hash chain recomputable by DB owner, SECURITY DEFINER needs SET search_path, per-org queue quotas). B has the best build plan (NULLIF, leak tests as aegis_app, catalog check, compare-and-set UPDATE) and should be merged with D.
2. Biggest blind spot: E. Answers a sales question, not a security one. Misses forged telemetry fooling the deterministic layer, XSS on the approval screen, every RLS pitfall. Adds attack surface: the replay endpoint is a new query and egress path, and an evidence-snapshot hash proves what was seen, not that it was true.
3. All five missed: identity-to-org binding (how an OIDC login becomes membership in org X: unverified email claims, IdP group mapping, invites, prompt removal — a mistake leaks across tenants and RLS enforces the wrong org without complaint); the LLM-provider attacker (prompts to a cloud provider expose tenant telemetry); data outside RLS (Aegis's own logs and backups hold tenant data).

**Reviewer 4:**
1. Strongest: D. Most concrete attack paths beyond the design: Ollama's unauthenticated /api/pull and /api/delete, Kubernetes API via Aegis's service account, MCP tool descriptions landing above the untrusted block (bypassing control 5), SECURITY DEFINER without search_path, a hash chain the DB owner can recompute, one Alertmanager starving every tenant through the shared queue, rollback stuck waiting for a second approver. B is the best build plan but finds less.
2. Biggest blind spot: E. Reads like a sales pitch. Skips poisoning of the deterministic layer, XSS on the approval screen and every RLS pitfall. Claims an approval hash "kills parameters steered by injected logs" — wrong: a hash locks in a poisoned value, it doesn't stop one. Its replay endpoint adds a new way to run queries.
3. All five missed: the tenant boundary starts at login (OIDC identity→org mapping via unverified email or group claims; removing someone from the IdP or org must end live server-side sessions). RLS protects only tables: backups, app/worker logs, error traces and the LLM provider's own logs hold tenant telemetry outside it. Nobody answered the malicious LLM provider persona, where the only control is redaction, which no one proposed testing.

**Reviewer 5:**
1. Strongest: D. Most new attack paths, each concrete: Ollama's unauthenticated /api/pull and /api/delete on :11434; Kubernetes API with Aegis's own service account; MCP tool descriptions entering the model's tool list above the untrusted-data block, fixed by pinning a hash of each description; SECURITY DEFINER without `SET search_path`; hash chain recomputed by the DB owner; rollback stalled behind two-person approval mid-incident; one Alertmanager starving the shared queue. B second: most executable fixes (NULLIF pooling pitfall, compare-and-set execute, catalog check for forced RLS).
2. Biggest blind spot: E. Turns a security review into a product pitch, answers almost nothing in (c), for (f) only argues against cutting RLS. Misses deterministic-layer poisoning, XSS, MCP description injection. Its "replay endpoint" adds on-demand egress and query surface without threat analysis.
3. Missed by all five: hostile LLM provider (sees every tenant's redacted telemetry and controls all model output; a per-org "OpenAI-compatible base URL" is itself an SSRF target); credential-to-endpoint binding (a stored credential must be usable only against its registered endpoint, or a tenant pairs it with a host it controls and captures it); identity lifecycle (sessions and API keys outlive removal from the IdP; no response covers revocation); viewer escalation (nobody tested whether a viewer can trigger investigations, which means egress and LLM spend).


## Chairman verdict

## Where the Council Agrees
- **Tenants never type URLs.** All five advisors said it. The operator registers endpoints and binds each one to an org, and admins choose from that list. Aegis's own DB, API, workers and Ollama are always denied.
- **The LLM never chooses remediation parameters.** Key and value come from an org catalog. The approval binds a hash of the exact parameters, the prior state and an expiry. Execution is compare-and-set.
- **The deterministic layer can be poisoned** (Contrarian, Outsider, First Principles). Forged errors set the ceiling, and the citations they produce are genuine. The defensible claim is "never more confident than the data."
- **Never claim "injection-proof" or "injection-resistant."** Four advisors said so.
- **The operator, the IdP and the DB owner are trusted.** The hash chain and the two-person rule do not protect against them.
- **Every prototype attack becomes a hostile test that blocks merges** (Executor, Expansionist).

## Where the Council Clashes
- **Remediation in v1.** First Principles: emit a signed instruction that a human runs. Executor: build it last. Expansionist: build a change-ticket platform. **Ruling: keep it, off by default.** A catalog, compare-and-set and a prior-state check take parameter choice away from the attacker, and that was the real risk.
- **RLS: the wall, or defense in depth?** First Principles says tenants who truly distrust each other need separate deployments. The Expansionist says keep RLS so a hosted SaaS is possible later. **Both are right.** Keep RLS, and document that mutually hostile tenants get separate deployments.
- **MCP.** Executor: disable it in v1. Contrarian: why can it mutate anything? Expansionist: turn it into a product. **Side with the Executor and the Contrarian:** off by default and read-only only. Remediation never goes through MCP.
- **Rollback through two-person approval** stalls in the middle of an incident. Approve the inverse action together with the forward action.

## Blind Spots the Council Caught
- **Hostile LLM provider (no advisor answered this persona).** It sees every tenant's redacted telemetry, and redaction is only heuristic. It can return output that passes the schema, stays under the ceiling and is still wrong. A per-org "OpenAI-compatible base URL" is itself an SSRF target.
- **Identity-to-org binding.** Unverified email or group claims can auto-join someone to an org. Sessions and API keys outlive removal from the IdP.
- **The worker is the real tenant boundary.** One key decrypts every org's credentials. Bind each credential to its org_id and its registered endpoint. Otherwise a tenant pairs a credential with a host they control and captures it.
- **Tenant data outside RLS:** logs, error traces, backups and the provider's own logs.
- **The Kubernetes service account can read Secrets.** Scope its RBAC.
- **Viewer escalation.** Can a viewer start investigations, and with them egress and LLM spend?
- **The Expansionist's additions create risk.** The replay endpoint is a new query and egress surface. Its approval hash locks a poisoned value in; it does not stop one.

## The Recommendation
**The design points the right way but is not sufficient as written.** Three gaps would each be disqualifying: the deployment-wide private-IP allowlist; remediation parameters that nothing constrains; the approval screen.

**(a) Remaining attack paths**
- Telemetry author: forges deterministic signals; plants stored XSS in log fields or generatorURL (script on our own origin passes the Origin and custom-header checks). Fix: strict CSP, no raw HTML or markdown rendering, URL-scheme allowlist.
- Alertmanager: floods the shared queue and starves every tenant. Fix: per-org quotas.
- MCP server: tool descriptions and readOnlyHint reach the model above the untrusted-data block. Fix: pin a hash of each description when the tool is allowlisted.
- Viewer: may trigger investigations or read raw evidence. Fix: test every route against every role.
- Tenant-A admin can reach tenant B's Loki; Ollama's unauthenticated /api/pull and /api/delete; the API without nginx; the Kubernetes API under Aegis's service account; Slack and webhook destinations as exfiltration channels, including link unfurling.
- LLM provider: limited only by opt-in, redaction, the ceiling, output caps and TLS verification.
- Network: after pinning the resolved IP, still verify TLS against the original hostname. Pin a CA per connector.

**(b) Egress is not right as written.** Replace the deployment-wide allowlist with an operator-owned registry of (host, port, CA, credential) entries, each bound to one org. Config validation rejects broad CIDRs. Aegis's own Postgres, API, workers and Ollama, plus link-local and metadata addresses, are always denied, even inside an allowlisted range. LLM base URLs, webhooks and Slack go through the same registry.

**(c) RLS is correct in principle but incomplete.** Policies with `NULLIF(current_setting('aegis.org_id', true), '')::uuid`, and a test that a query with no SET returns zero rows. Claim jobs through SECURITY DEFINER, then SET LOCAL with that job's org. Migrations under a separate owner role. Tests as `aegis_app`, never `postgres`. `SET search_path` on every SECURITY DEFINER function. Views: `security_invoker` set and checked in CI. CI catalog check: every org_id table has RLS enabled, forced, and a policy.

**(d) Remediation is not enough as written.** The LLM picks a catalog ID only; the approver sees the literal key, old value and new value. Replay/double execution: `UPDATE … WHERE status='approved' AND params_hash=$2 AND expires_at>now() RETURNING id`. Stale approvals: expire after about 15 minutes; re-read the flag's current value before executing and abort if it has changed.

**(e) Minimum test set:** every prototype exploit as a fixture; per-route leak tests as `aegis_app` + no-SET test + catalog check; SSRF to every always-denied target incl. DNS rebinding and redirects; credential used against the wrong endpoint and a job using another org's credential; viewer route matrix; XSS in every rendered telemetry field; replayed, expired and modified approvals; MCP description drift, shadowing, oversized results; session revocation after IdP removal.

**MAY claim (each backed by a named test):** model output cannot raise confidence above the deterministic ceiling; model output cannot cite nonexistent evidence; no action runs that a human did not author in advance; no cross-tenant access on the tested paths; Aegis reaches only operator-registered endpoints, never its own infrastructure or metadata.

**MUST NOT claim:** injection-proof / injection-resistant / "can't be fooled"; an audit trail tamper-evident against the DB owner; a two-person rule that holds against the operator or the IdP; complete redaction; SSRF-proof; isolation from the operator; confidentiality from the LLM provider.

**(f)** Bypassed in practice: the 10/8 allowlist; the two-person rule at tens of users (second account, rubber-stamping) — show the literal diff to the approver. Cut: the hash-chain verification endpoint unless anchored outside the database; mutating MCP; the replay endpoint; the marketplace. Keep RLS.

## The One Thing to Do First
Replace typed integration URLs with operator-registered endpoints bound to an org and a credential. Then write the failing test showing that tenant A cannot reach tenant B's Loki, Aegis's Postgres, Ollama or the metadata service. Cross-tenant SSRF is the one disqualifying attack the current design still allows.


## How the verdict was applied

| Verdict item | Decision |
|---|---|
| Operator-owned endpoint registry; tenants pick connector IDs; always-deny Aegis infra, Ollama admin, link-local/metadata; broad CIDRs rejected; TLS verified against the original hostname; LLM/webhook/Slack destinations through the same registry | **Adopted** — ADR-016 (supersedes the egress part of ADR-015) |
| Credentials bound to (org, connector); a job cannot use another org's credential | **Adopted** — ADR-016 |
| LLM picks remediation catalog IDs only; approval binds params hash + prior state + expiry; compare-and-set execution; re-read state before execution; forward + inverse approved together | **Adopted** — ADR-014 amended |
| RLS details: NULLIF policies, no-SET test, search_path on SECURITY DEFINER, security_invoker views, catalog check in CI, tests as aegis_app | **Adopted** — ADR-012 amended |
| Strict CSP, no raw HTML/markdown rendering of telemetry or model output, URL-scheme allowlist, literal diff on approval screen | **Adopted** — security model §UI |
| Per-org queue quotas | **Adopted** (already ADR-010; strengthened to quota on queued + running) |
| MCP off by default, read-only only, description hash pinning | **Adopted** — ADR-017 |
| Identity-to-org binding (no auto-join on unverified email; explicit invitations or verified-domain/group mapping configured by operator); session and API-key revocation on membership removal; periodic revalidation | **Adopted** — ADR-011 amended |
| Trusted parties stated explicitly (operator, IdP, DB owner); mutually hostile tenants → separate deployments | **Adopted** — security model §Trust boundaries |
| Hostile LLM provider: opt-in, redaction (heuristic, stated), ceiling, output caps, TLS verification | **Adopted** |
| Kubernetes service account scoped (no Secrets read) | **Adopted** — Helm RBAC + integration docs |
| Viewer cannot start investigations | **Adopted** — RBAC matrix |
| Cut hash-chain verification endpoint unless externally anchored | **Modified**: chain kept (detects application-level and accidental tampering); docs state it is *not* tamper-evident against the DB owner; optional anchoring by emitting chain heads to the log stream |
| Claims allowed / forbidden | **Adopted verbatim** in `docs/security-model.md` |
