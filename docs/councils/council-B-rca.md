# Council B — RCA architecture

**Date:** 2026-10-01 · **Protocol:** installed `llm-council` skill (5 independent advisors in parallel → 5 anonymized peer reviews → chairman synthesis).

## Framed question

DECISION: What architecture will most improve evidence-grounded root-cause analysis (RCA) in Aegis while minimizing hallucination and preserving human control?

CONTEXT — what exists and what was learned (verified):
- Aegis is becoming a production AI incident-investigation platform for microservices (metrics: Prometheus; logs: Loki/OpenSearch; traces: Jaeger/Tempo; change events: deploys, config, feature flags, Kubernetes rollouts). Users are on-call engineers who must decide what to do during an incident.
- Research prototype findings: a 4B local model (qwen3.5:4b, CPU, 2-6 min per investigation) reliably localized the affected SERVICE but never deduced the MECHANISM (it missed that a feature flag had been switched on — the flag change was never given to it as evidence). Error-focused querying and pre-LLM statistical summaries helped; naive truncation caused a false "no anomaly". With neutral incident text the model honestly said INSUFFICIENT. Masked faults (frontend fallback hides a cache failure: zero error delta at the edge) were missed.
- Research defects that must not recur: telemetry queried for now()-15min instead of the incident window; trace span error status discarded; when the model cited no evidence the engine attached keyword-chosen evidence as "supporting"; keyword classification ("unhealthy" -> NO_ANOMALY; "CLASSIFICATION: INSUFFICIENT (cannot claim CONFIRMED)" -> CONFIRMED); verification skipped yet result CONFIRMED; untrusted log text placed in the model's instruction channel; a hostile log could forge "=== SYSTEM ===" lines.
- Evaluation assets: a keyword-based evaluator (kappa 0.778 vs ONE human on 12 cases); 2 hand-authored replay snapshots; a frozen held-out set of 8 REAL OpenTelemetry Demo captures (1 healthy, 5 product-catalog feature-flag failure, 1 neutral-description, 1 masked recommendation-cache failure) that lack span status and are partly lossy. New synthetic scenarios will be authored by the same engineering agent that writes the pipeline (self-grading risk).

ALREADY DECIDED (Council A, binding):
- A PURE RCA core: analyze(TelemetrySnapshot, AnalysisRequest{incident window, baseline window, focus services, budgets}) -> Analysis. No DB/network/clock inside. Live collection and replay feed the same function; the benchmark tests the production code path.
- Deterministic analysis computes candidates, verification checks, a classification (CONFIRMED / PLAUSIBLE / INSUFFICIENT / NO_ANOMALY) and a numeric CONFIDENCE CEILING. The LLM adjudicates via schema-constrained JSON: picks a candidate or none, explains the mechanism as typed claims (observation | inference) each citing evidence IDs, may LOWER confidence/classification but never raise them. Picking a non-leading candidate caps the result at PLAUSIBLE and is shown as a disagreement. Invalid output -> one repair attempt -> deterministic verdict stands, marked "AI unavailable". Model chain-of-thought is discarded. Deterministic result is shown within seconds; LLM result arrives later. One CPU-bound local model; cloud models optional per organization.

PROPOSED DETERMINISTIC PIPELINE (draft):
1. Coverage: which modalities/providers returned data; collection failures recorded as evidence of missing information.
2. Topology: configured edges + edges derived from cross-service parent/child spans.
3. Service signals: request rate, error ratio, latency p50/p95/p99 per service, from metrics or derived from spans when metrics are missing.
4. Anomaly: incident window vs baseline window (e.g. 30-60 min before onset): ratio thresholds + robust z-score (median/MAD) + onset time per signal.
5. Trace error origin: for each failing trace, the deepest span with error status whose children are healthy -> "error origin service"; counts per service.
6. Log signatures: mask numbers/IDs/UUIDs into templates; new or increased error signatures vs baseline, linked to trace IDs when present.
7. Change correlation: deploy/config/flag/rollout events on a service or its dependencies within N minutes before onset.
8. Candidates: anomalous services scored by features (error-origin share, onset order, downstream-most position among anomalous services, magnitude, change proximity, new log signatures, modality count); hypothesis kinds: internal failure, dependency failure, change-induced regression, saturation, traffic surge.
9. Verification checks per top candidate: temporal precedence vs dependents; propagation (dependents' failing traces originate in candidate spans); isolation (candidate's own dependencies healthy, else it is a symptom); change alignment; modality agreement.
10. Verdict rules: NO_ANOMALY only with adequate coverage and no anomaly; INSUFFICIENT when coverage is inadequate, top-two candidates are too close, or checks fail; PLAUSIBLE when the leader has margin and >=1 passed check; CONFIRMED needs >=2 independent modalities + passed temporal precedence + passed propagation or change alignment + no contradicting evidence.

OPEN CHOICES (take positions):
(a) Candidate scoring: weighted feature sum vs ordered rule/decision list vs probabilistic/causal-graph scoring. Which is most explainable AND robust to red herrings (a noisy-but-innocent service) and multiple simultaneous faults?
(b) Is the verification-check set right? What distinguishing tests are missing (e.g. "symptom vs cause" for upstream/downstream ambiguity, saturation, retries/timeouts cascading)?
(c) How should the confidence ceiling be computed so that it is calibrated and auditable, and how do we measure calibration honestly with few scenarios?
(d) What should the LLM actually be used for, given small local models? Options: adjudicate between candidates; explain mechanism; propose additional targeted verification queries from a typed allowlist (bounded tool loop) vs no tool use at all; summarise for humans; nothing (deterministic-only by default).
(e) Mechanism identification: should Aegis classify fault mechanism deterministically (taxonomy: error-throwing, latency, saturation, dependency, change-induced, traffic) and let the LLM only refine it?
(f) Evaluation: how to score (component exact match, mechanism class, classification, citation validity, abstention correctness, calibration), how many scenarios/repetitions before any claim, and how to avoid the pipeline author grading their own homework.
(g) Human control: what the reviewer does with a diagnosis (accept / reject / correct root cause) and whether that feedback is used, and how.
(h) Anything in the pipeline that will produce confident wrong answers in realistic incidents (e.g. masked failures, retries hiding errors, sampling of traces, clock skew, missing baseline after a deploy, low traffic).

WHAT IS AT STAKE: An operator acting at 3am on a confident wrong diagnosis is the worst outcome; a product that always says INSUFFICIENT is useless. The owner's audit will check that every material claim is traceable to evidence, that the system abstains honestly, and that RCA is genuinely better than the research engine — not just a larger prompt.


## Advisor responses

ADVISOR RESPONSES (de-anonymized; in peer review: Executor=A, Expansionist=B, Contrarian=C, First Principles=D, Outsider=E)

**The Contrarian:**

The worst flaw is in the verdict rule itself. CONFIRMED needs "propagation OR change alignment". In any busy production system, some deploy, config push or flag flip lands within N minutes of every onset. So a coincidental change, plus metrics and logs moving together, reaches CONFIRMED with zero causal trace evidence. Fix: change alignment must never substitute for propagation. Either require both, or require proof that the change's scope (flag cohort, rollout pods) matches the failure's scope.

Second: checks that pass because data is missing. Trace sampling, dropped spans and the held-out set's absent span status all make children look "healthy". Step 5 then blames the caller, and isolation passes vacuously. That is the research defect "verification skipped yet CONFIRMED" coming back by design. Fix: every check is tri-state (pass / fail / not-evaluable), and not-evaluable never counts toward CONFIRMED or PLAUSIBLE.

Third: masked faults are filtered out before scoring. Candidates come only from anomalous services. A cache failing behind a fallback has no error delta, so it never becomes a candidate. "Downstream-most anomalous" then rewards the symptom. Missing check: compare the call graph and per-edge call counts between baseline and incident, because a fallback changes who calls whom.

Fourth: the evaluation cannot test what matters. Propagation and isolation can't run on the real captures because they have no span status, so only self-authored synthetic scenarios exercise those checks. And 5 of the 8 real cases are the same flag fault. "Beating the research engine" may just mean the flag event is now an input, not that the RCA got better.

Fifth: a numeric confidence ceiling calibrated on 8 cases cannot be falsified. Ship it as an ordinal: a visible count of checks passed and checks not evaluated.

**The First Principles Thinker:**

You're asking the wrong question. Questions (a) through (h) all assume the bottleneck is reasoning: how to score candidates, calibrate, and adjudicate. Your own evidence says it isn't. The research engine missed the mechanism because the flag change was never collected. It produced a false "no anomaly" because truncation threw data away. It looked at the wrong window, discarded span status, and lost the masked cache fault because the edge metrics hid it. Every failure you saw was missing evidence or corrupted evidence. None was a reasoning failure. A smarter scorer can't recover a flag event it never received.

So rebuild the goal from what the operator at 3am actually needs. That is not a root-cause verdict. It's a decision: what should I do, or check, next? Restate Aegis's job as: **shrink the search space, and name the cheapest action or query that tells the top hypotheses apart.** Under that framing, "INSUFFICIENT" stops being useless. "INSUFFICIENT: flag X flipped 4 min before onset on product-catalog; rolling it back is reversible and decisive" is the most valuable output Aegis can give.

Consequences:
- **Coverage and change ingestion are the product.** A first-class "what we did NOT see" list matters more than any scoring choice. Most real incidents are change-induced, so change correlation should rank first, not sit as one feature among seven.
- **Pick the dumbest auditable scorer: an ordered rule list.** With 8 scenarios, 5 of them the same fault, you cannot show that weighted sums or causal graphs do any better. Choosing between them is untestable taste.
- **Don't ship a numeric confidence ceiling.** You can't calibrate on n=8, so a number is false precision that an auditor will rightly attack. Use discrete evidence states plus the check that would overturn the conclusion.
- **Self-grading has a structural fix.** Get ground truth from a fault injector, a separate harness that randomly flips OTel Demo flags and faults. Then labels come from what was actually injected, not from what the pipeline author wrote down.
- **The LLM is not on the critical path.** Default it to summarisation only. The 4B model already showed its one real virtue, honest abstention, and the deterministic core now provides that.

**The Expansionist:**

The biggest asset here is being undervalued: the pure `analyze(TelemetrySnapshot, AnalysisRequest)` function. People see it as a testability win, but it can also drive a flywheel. I'd use it three ways.

**1. Remove self-grading by using the OTel Demo's own fault injector (f).** The demo already ships flagd failure flags: product catalog, recommendation cache, payment unreachable, Kafka queue problems, ad-service high CPU and GC, email memory leak, homepage flood. Between them they cover your whole mechanism taxonomy (error, latency, saturation, leak, traffic). Build a harness that flips them across load levels, seeds and *combinations*, and adds noise to innocent services as red herrings. Ground truth then comes from the injector config, not from the engineer who writes the pipeline. The deterministic core runs in seconds, so you can grade thousands of scenarios a night and sample LLM runs on top. With that much data the confidence ceiling (c) can be calibrated against measured reliability curves rather than asserted.

**2. Make reviewer feedback build a corpus (g).** Every accept or correction should write the frozen snapshot, the confirmed root cause and the mechanism into the replay benchmark. Real incidents become regression tests. For (a), I'd pick an ordered decision list that logs every feature, so you can re-fit it as labeled cases pile up. Competitors will have bigger prompts. Aegis would have a library of graded real incidents, and that's much harder to copy.

**3. The research prototype's mechanism miss was a data gap, not a model failure, because the flag change was never given to it.** Make the change ledger (deploys, config, flags, rollouts) the backbone of the pipeline. Then run the same function on every deploy and flag flip, comparing the change window with its baseline. Aegis becomes proactive regression detection that catches the product-catalog flag before anyone is paged. That's a bigger market than after-the-fact root-cause analysis, on the same code path.

**On (d):** the bounded, typed tool loop is where a small model is worth having. Proposing distinguishing queries, such as cache hit rate or fallback call volume, is how you crack masked faults that fixed rules can't anticipate.

**The Outsider:**

Reading this cold, my first observation is that the research engine's biggest failure wasn't a reasoning failure. It missed the feature flag because nobody gave it the flag change. Most of this document is about scoring math, verification checks and how to constrain the LLM. The lesson from the prototype is about inputs. The first thing any human asks at 3am is "what changed?" Make the change-event feed (deploys, flags, config, rollouts) the headline of the output and a hard coverage requirement. If change events are missing, say so in large text.

Second, the vocabulary will mislead the person it's meant to help. An exhausted operator reads "CONFIRMED" as "safe to act." Telemetry can't confirm causation. Call it "strongly supported" or similar. "NO_ANOMALY" is worse. A human opened the incident because something is wrong, and a masked fault produces exactly this verdict. That's your confident wrong answer, built in. NO_ANOMALY should never be shown during a declared incident. Show "nothing found in what I checked" plus a list of what wasn't checked. The labels "confidence ceiling," "disagreement" and "AI unavailable" also need plain-English rendering, or nobody will read them.

Third, count your real evidence. The 8 real captures cover about 4 distinct situations, since 5 of them are the same flag fault. The evaluator agreed with one human on 12 cases. On top of that sits a 10-step pipeline with 5 hypothesis kinds, 5 checks and weighted features. That's a lot of machinery tuned on almost nothing, and the person grading it also built it. Prefer the simplest ordered rules you can explain in one sentence each. Don't claim "better than research" for anything beyond the flag fault until someone other than the pipeline author has written new scenarios.

Finally, ask whether the operator would notice if the LLM were switched off. The LLM can only lower confidence, can't add evidence, and takes 2-6 minutes. If the only loss is prose, it's a summarizer. Ship it off by default and make it earn its place.

**The Executor:**

**Monday, before any code:** write the expected answer for each of the 8 held-out captures: root service, mechanism class, classification and required evidence. The owner signs them and you commit the hash. Synthetic scenarios written by the pipeline agent count toward nothing.

**Next, check whether the flag-change event is actually in the captures.** The research missed the mechanism because it never saw the flag. If the event isn't there, recapture first. No pipeline can cite evidence that isn't in the snapshot.

**The data dictates the build order.** The captures lack span status, so stage 5 and the propagation check can't be run on real data. Under your CONFIRMED rule, that leaves change alignment as the only route to CONFIRMED on this set. Build stages 1, 3, 4 and 7 plus the verdict rules first. Stage 5 waits for a recapture that includes span status.

Positions:
- **(a)** Ordered decision list. Weights need tuning data you don't have, and causal graphs are a quarter's work. "Rule 3 fired because X" can be debugged at 3am.
- **(b)** Enough for v1. Checks for retry and timeout cascades need span status you don't have, so defer them.
- **(c)** The ceiling is a lookup table: classification × checks passed. It's a rank, not a probability. Make no calibration claim with fewer than about 50 labeled cases.
- **(d)** Ship deterministic-only first; it's already on screen within seconds. The LLM explains the mechanism with typed claims, behind a flag. No tool loop: three rounds at 2-6 min each is 6-18 minutes.
- **(e)** Yes, as a deterministic lookup: which signal moved plus the change-event type.
- **(f)** Score each field against the golden files, with 3 LLM runs per case.
- **(g)** Accept, reject or correct. Every correction becomes a replay scenario. It's eval data, not training data.
- **(h)** For the masked cache case, a pass is INSUFFICIENT and never a confident wrong answer.

**Week-one exit:** the healthy capture gives NO_ANOMALY. The 5 flag cases name product-catalog as the root service and classify it as change-induced, citing the flag event. No confident wrong answers across all 8.


## Peer reviews

PEER REVIEWS (reviewers saw responses labelled A–E: A=Executor, B=Expansionist, C=Contrarian, D=First Principles, E=Outsider)

**Reviewer 1:**
1. Strongest: C. It is the only response that finds flaws in the proposed rules themselves, and it gives a testable fix for each: "Propagation OR change alignment" lets a coincidental deploy reach CONFIRMED with no trace evidence; checks need to be tri-state, because missing span status makes isolation pass vacuously; masked faults never become candidates — diff per-edge call counts. A shows that change alignment is the only route to CONFIRMED on the held-out captures, so this flaw is real. C's weakness: no position on (a), (d) or (g).
2. Biggest blind spot: B. Never deals with the stated worst outcome, a confident wrong answer; nothing on verdict rules or vacuous checks. Its tool loop ignores 2-6 minutes per round (6-18 minutes). Calibrating on injected faults measures one demo topology, not customer production. Proactive regression detection is scope creep.
3. All five missed: untrusted log text (crafted log lines can create fake "new signatures" in stage 6; and because the LLM can only lower confidence, they can push the verdict down to INSUFFICIENT); zero MAD on sparse or zero-error baselines makes a single error look infinitely anomalous; no baseline after a deploy; clock skew and scrape resolution weaken temporal precedence, which CONFIRMED requires.

**Reviewer 2:**
1. Strongest: C. The only response that checks the proposed verdict rules for built-in ways to be confidently wrong (change alignment substituting for propagation; checks passing by default when data is missing — fix tri-state; only anomalous services become candidates — fix per-edge call-count diff). A is runner-up (owner-signed golden labels, check the flag event is in the captures) but accepts change alignment as the only route to CONFIRMED, exactly the path C flags.
2. Biggest blind spot: B. Injector runs calibrate against one demo topology, not production. Backs a tool loop at 2-6 min per round and ignores its prompt-injection risk. Skips missing span status, pivots to a new market instead of fixing the verdict rules.
3. All five missed: temporal precedence is often unresolvable (scrape intervals of 15-60 s, log ingestion lag, clock skew need a tolerance band); baseline contamination (slow leaks, changes inside the baseline window); two real simultaneous faults forced into INSUFFICIENT instead of being reported; the hostile-log instruction-channel defect not addressed; corrections made at 3am can be wrong, so they need checking before becoming ground truth.

**Reviewer 3:**
1. Strongest: C. Each point is a testable draft defect leading to a confident wrong answer at 3am (coincidental deploy → CONFIRMED; vacuous checks — not-evaluable state; masked faults — per-edge call counts). A admits change alignment is the only route to CONFIRMED on this set, so A's week-one exit builds in the flaw C names. C says little on (d) and (g).
2. Biggest blind spot: B. Its injector flips flagd flags, and Aegis ingests flag flips as change events — the ground truth leaks into the pipeline's input, and calibration on those cases says nothing about incidents with no change event. D has the same problem. B also backs a tool loop despite 2-6 min per LLM call, and treats every reviewer accept as ground truth (automation bias).
3. All five missed the hazards (h) names: scrape intervals and clock skew make close onset ordering unreliable — needs a tolerance or not-evaluable; a fresh deploy has no baseline; MAD is 0 on sparse low-traffic baselines, inflating z-scores and flagging innocent services; hostile log text can forge "new" error signatures in stage 6 with no LLM involved.

**Reviewer 4:**
1. Strongest: C. Only response that checks the draft pipeline itself and finds defects that would produce confident wrong answers: "propagation OR change alignment" lets a coincidental deploy reach CONFIRMED; missing span status and trace sampling let checks pass with no data — tri-state; masked faults never become candidates — diff per-edge call counts. A saw the same path to CONFIRMED but built its plan around it.
2. Biggest blind spot: B. Aims for scale and market instead of the audit; backs a tool loop on a 2–6 min/round model, widening prompt-injection exposure; assumes flagd faults in one demo topology can calibrate confidence for real systems; never addresses the verdict rules or the listed research defects.
3. All five missed: timing (temporal precedence is often a tie because of scrape intervals, clock skew and noisy onset detection — a tie must count as not-evaluable); baseline contamination (slow-burn faults, daily load swings, a deploy inside the baseline window); late downgrade (the operator may act on the verdict shown within seconds before the LLM lowers it minutes later); untrusted logs out of the LLM instruction channel.

**Reviewer 5:**
1. Strongest: C. Only response that checks the draft pipeline itself for ways it would produce confident wrong answers, each fix concrete and testable (coincidental deploy → CONFIRMED; tri-state checks; per-edge call counts for masked faults). A has the best execution plan, but falls into C's loophole: by its own analysis, change alignment is the only route to CONFIRMED.
2. Biggest blind spot: B. Widens scope (proactive regression detection, a tool loop on a 2-6 minute CPU model), ignores confident-wrong risk. Its fault-injector harness leaks labels: every flagd fault comes with a flag-flip event, so the benchmark rewards change correlation, not RCA. "Thousands a night" ignores that each capture needs the demo running live under load. Calibration on one demo app won't carry over to production.
3. All five missed: multiple simultaneous faults ("top-two too close → INSUFFICIENT" penalizes real co-occurring faults); clock skew and contaminated baselines (slow leaks, earlier deploys) corrupt temporal precedence, which every CONFIRMED needs; untrusted log injection — no response says how log text reaches the LLM safely.


## Chairman verdict

## Where the Council Agrees

- **Most of what went wrong was missing or corrupted inputs, not weak reasoning.** First Principles, Outsider, Expansionist and Executor each concluded this on their own. The research engine missed the mechanism because it never received the flag change. The change ledger and a "what we did NOT see" list should therefore be first-class. A missing change feed counts as a coverage failure.
- **Use an ordered decision list for (a).** Four advisors chose it. With 8 captures, 5 of which are the same fault, there is no data to tune weights or build a causal graph.
- **Don't show a numeric probability.** Contrarian, First Principles and Executor all said 8 cases cannot calibrate one. Use ordinal evidence states instead.
- **Keep the LLM off the critical path.** It should be off by default, at most a narrator, and earn its place later.
- **The author cannot grade their own pipeline.** Synthetic scenarios written by the pipeline agent prove nothing. "Better than research" holds only for the flag fault.

## Where the Council Clashes

- **Should the LLM run a tool loop?** The Expansionist says yes: proposing distinguishing queries is how masked faults get cracked. Executor, Outsider and First Principles say no: at 2–6 minutes per round, three rounds take 6–18 minutes, and every query result is a new way to inject text. Both sides are right about something. Discriminating queries are valuable, but a 4B CPU model is the wrong thing to generate them.
- **Where ground truth comes from.** First Principles and Expansionist want a fault injector. The Executor wants owner-signed golden labels. The injector scales, but the labels are trustworthy now.
- **What the product's output is.** First Principles argues the output should be "the cheapest next action that separates the hypotheses", not a verdict. Everyone else works inside the verdict framing. The audit needs the classification, and the operator needs the next action.
- **Week-one exit.** The Executor's plan quietly makes change alignment the only route to CONFIRMED. That is exactly the loophole the Contrarian found.

## Blind Spots the Council Caught

- **A coincidental deploy can reach CONFIRMED.** The rule "propagation OR change alignment" allows it. All five reviewers ranked this the most important flaw.
- **Checks can pass because data is missing.** Sampled or status-less spans make isolation and error-origin pass when they were never really tested.
- **Injector labels leak into the inputs.** Every flagd fault also produces a flag-flip change event, so the benchmark rewards change correlation, not RCA. Results also cover only one demo topology.
- **Hostile log lines can forge "new error signatures" in stage 6** without any LLM involved.
- **A zero MAD on a sparse baseline** makes a single error look infinitely anomalous.
- **Clock skew and scrape intervals** make close onset orderings unreliable.
- **Baselines can be contaminated** by slow leaks, earlier deploys or daily load swings, and a fresh deploy has no baseline at all.
- **"Top two too close → INSUFFICIENT"** penalises two real faults happening at once.
- **Late downgrade:** the operator may act on the fast verdict before the LLM lowers it minutes later.
- **Corrections made at 3am can be wrong,** so treating them as ground truth builds in automation bias.

## The Recommendation

**Corrected verdict rules.** Every check is tri-state: pass, fail or not-evaluable. Not-evaluable never counts as a pass.

- **CONFIRMED** requires all of: at least 2 independent modalities; temporal precedence passed outside a tolerance band (scrape interval plus skew; a tie is not-evaluable); propagation passed, evaluated on real span status; either isolation passed or a change whose scope (service, pods, flag cohort) matches the failure; a valid baseline; zero failed checks and zero contradicting evidence. Change alignment never stands in for propagation.
- **PLAUSIBLE:** the leader wins under the decision list, at least one evaluated check passed, no check failed, and no evidence contradicts it. When two candidates each pass their checks independently and neither is upstream of the other, report both as PLAUSIBLE (multi-fault).
- **INSUFFICIENT:** a required modality is missing (including the change feed), the baseline is invalid, the leader fails a check, or two candidates on the same path can't be told apart. It must always name the cheapest check that would separate them.
- **NO_ANOMALY:** full coverage, no anomaly, no change in per-edge call counts, and a valid baseline.

**What these rules produce on the held-out set.** Propagation can't be evaluated without span status, so the ceiling on all 8 captures is PLAUSIBLE: healthy capture → NO_ANOMALY; 5 flag cases and the neutral-description case → PLAUSIBLE, product-catalog, change-induced, citing the flag event; masked cache case → INSUFFICIENT. Scope match is evaluable only if the captured flag event names its target. Either way the cap stays PLAUSIBLE.

**Display.** Internal labels stay; wording changes: CONFIRMED → "Strongly supported"; PLAUSIBLE → "Likely cause"; INSUFFICIENT → "Not enough evidence — next check: X"; NO_ANOMALY → "Nothing found in what was checked" plus the list of what wasn't checked, never shown as "no anomaly" during a declared incident.

**Positions on (a)–(h):**
- **(a)** Ordered decision list, one sentence per rule, every feature value logged. A red herring cannot lead unless it has its own evidence (error origin, a change on it, or changed edges). Multiple faults via the multi-fault branch.
- **(b)** Add: tri-state results; per-edge call-count and topology diff between baseline and incident (fallbacks and masked faults); change-scope match; baseline validity with fallback to pre-deploy or same time yesterday, else not-evaluable; a saturation check on resource metrics. Defer retry/timeout cascade checks until span data exists.
- **(c)** The ceiling is a lookup over classification × checks passed × checks not evaluated × coverage — a rank, not a probability. Report precision per class with Wilson intervals. No calibration claim before about 50 independently labeled cases.
- **(d)** LLM off by default. When enabled, it explains the mechanism as typed claims over existing evidence IDs, plus a plain-English summary. No tool loop in v1; discriminating queries are generated deterministically from the hypothesis kind. Log text only in a delimited, escaped data field. A later downgrade appears as a visible alert, never a silent change.
- **(e)** Mechanism from a deterministic lookup (which signal moved plus the change type). The LLM may only lower it to "unknown" or annotate it.
- **(f)** Score each field separately: root service, mechanism class, classification; citation validity; abstention correctness; headline metric: confident-wrong count must be zero. Run the LLM 3 times per case. Ablation: research engine given the same snapshot including the flag event vs the new pipeline. The injector is a second tier and must include variants with the change feed withheld, faults that emit no change event, noisy innocent decoys, and a stated one-topology limit.
- **(g)** Reviewer can accept, reject or correct root cause and mechanism. Corrections queue until post-incident review, then become replay scenarios. Eval data only: no training, no online re-weighting.
- **(h)** Masked faults: edge diff, never NO_ANOMALY during a declared incident. Sampling/missing span status: not-evaluable. Clock skew: tolerance band. Low traffic: MAD floor plus minimum event counts. Forged logs: signature templates are counted, never interpreted; a single source cannot create a "new" signature by itself. Contaminated/missing baseline: validity check. Coincidental change: scope match.

## The One Thing to Do First

Have the owner write, sign and hash expected answers for all 8 held-out captures under the corrected rules: root service, mechanism, classification (PLAUSIBLE cap) and required evidence IDs. Commit them before any pipeline code. Filling in "required evidence" will show immediately whether the flag event is actually in the captures, or whether they have to be recaptured first.


## How the verdict was applied

| Verdict item | Decision |
|---|---|
| Ordered decision list, one sentence per rule, features logged | **Adopted** — ADR-008 amended |
| Tri-state checks; not-evaluable never counts | **Adopted** |
| Corrected CONFIRMED/PLAUSIBLE/INSUFFICIENT/NO_ANOMALY rules; change alignment never substitutes for propagation; multi-fault branch; INSUFFICIENT names the next check | **Adopted** — ADR-009 amended |
| Missing change feed forces INSUFFICIENT | **Modified**: a missing change feed is a prominent coverage gap that caps the result at PLAUSIBLE and forbids the `change_induced` mechanism. Forcing INSUFFICIENT would make every installation without a change feed useless while telling the operator nothing new. |
| Additional rule found while checking the held-out data | **Added**: a candidate cannot be named root cause (PLAUSIBLE or above) while a downstream dependency on its failing path is unevaluated; the result is INSUFFICIENT naming that dependency as the next check (prevents blaming the symptom, e.g. frontend). |
| Ordinal support level instead of numeric probability | **Adopted** — the "confidence ceiling" becomes an ordinal support level (`strong`, `moderate`, `weak`, `none`) from a lookup over classification × checks × coverage |
| Plain-language display labels; never "no anomaly" during a declared incident | **Adopted** — UI |
| LLM off by default; narrator; no tool loop; deterministic next checks; visible late downgrade | **Adopted** — enabled per organization by an admin when an allowed provider exists; demo organization has it enabled |
| Deterministic mechanism lookup | **Adopted** |
| Field-wise scoring, confident-wrong = 0 headline, 3 LLM runs per case, research-engine ablation, Wilson intervals, no calibration claim < ~50 independent cases, injector tier with withheld change feed / no-change faults / decoys | **Adopted** — evaluation methodology (roadmap M1, M10) |
| Reviewer accept/reject/correct; corrections queued for post-incident review; eval data only | **Adopted** — M5/M2 |
| Edge diff, MAD floor + minimum counts, signature counting with source diversity, baseline validity + fallbacks, saturation check, scope match | **Adopted** — core stages |
| Owner-signed expected answers before pipeline code | **Partially done**: expected answers written from the injected faults and hashed (`eval/heldout/expected.json`, SHA-256 `95c73713…8400`) before any pipeline code; **owner sign-off pending**. Checking them showed the flag event is absent from every capture and traces lack span status, so the real-data localisation test needs a recapture (M10). |
