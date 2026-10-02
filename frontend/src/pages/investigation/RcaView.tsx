import { useCallback, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { fetchAllEvidence, fetchEvidenceItem, fetchGraph, qk } from "../../api/queries";
import { isNotFound } from "../../api/client";
import type {
  AiAssessment,
  Candidate,
  Evidence,
  Graph,
  Investigation,
  RootCause,
  SnapshotSummary,
} from "../../api/types";
import { CheckBadge, SupportBadge, VerdictBadge } from "../../components/Badge";
import { Citations, EvidenceItem } from "../../components/Evidence";
import { ErrorNotice, Loading, Notice } from "../../components/Feedback";
import { KeyValueList } from "../../components/KeyValues";
import {
  aiStatusLabel,
  checkName,
  collectionStatusLabel,
  hypothesisLabel,
  mechanismLabel,
  presentClassification,
  supportLabel,
} from "../../lib/classification";

// The RCA view separates five epistemic layers (production-architecture §5, docs/rca.md):
//   FACT / OBSERVATION — evidence items with provenance;
//   INFERENCE          — the decision-list rule that selected a leader, and the decision trace;
//   HYPOTHESIS         — ranked candidates and their features (never presented as fact);
//   VERIFIED FINDING   — root causes, only for CONFIRMED / PLAUSIBLE, with the checks that passed;
//   UNCERTAINTY        — next checks, coverage gaps, failed / not-evaluable checks, reasons.
// The AI assessment (if any) is shown apart from the deterministic verdict; it can only lower it.

type LayerKind = "verdict" | "finding" | "uncertainty" | "ai" | "hypothesis" | "inference" | "fact";

const KICKERS: Record<LayerKind, string> = {
  verdict: "VERDICT",
  finding: "VERIFIED FINDING",
  uncertainty: "UNCERTAINTY",
  ai: "AI ASSESSMENT",
  hypothesis: "HYPOTHESIS",
  inference: "INFERENCE",
  fact: "FACT / OBSERVATION",
};

function Layer({
  kind,
  title,
  description,
  actions,
  children,
  id,
}: {
  kind: LayerKind;
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  id?: string;
}) {
  const headingId = useId();
  return (
    <section className={`layer layer-${kind}`} aria-labelledby={headingId} id={id}>
      <div className="layer-head">
        <h2 id={headingId} className="layer-title">
          <span className="layer-kicker">{KICKERS[kind]}</span> <span>{title}</span>
        </h2>
        {actions ? <div className="panel-actions">{actions}</div> : null}
      </div>
      {description ? <p className="layer-description">{description}</p> : null}
      <div className="layer-body">{children}</div>
    </section>
  );
}

type Select = (evidenceId: string) => void;

// --- verdict ----------------------------------------------------------------------------------

export function VerdictSummary({ investigation }: { investigation: Investigation }) {
  const p = presentClassification(investigation.classification);
  const ai = investigation.ai;
  return (
    <Layer kind="verdict" title="Deterministic verdict">
      <div className="verdict-line">
        <VerdictBadge classification={investigation.classification} />
        <SupportBadge support={investigation.support} />
      </div>
      {investigation.display_label ? <p className="verdict-label">{investigation.display_label}</p> : null}
      {investigation.summary ? <p className="verdict-summary">{investigation.summary}</p> : null}
      <p className="muted small">{p.explanation}</p>
      {ai && ai.status === "accepted" && ai.lowered ? (
        <Notice tone="warning" title="AI lowered the deterministic result" role="note">
          <p>
            The AI assessment rates this as “{presentClassification(ai.classification).label}” with{" "}
            {supportLabel(ai.support).toLowerCase()} support. See the AI assessment below.
          </p>
        </Notice>
      ) : null}
      {investigation.blast_radius.length ? (
        <p className="small">
          <span className="muted">Affected services (blast radius): </span>
          {investigation.blast_radius.join(", ")}
        </p>
      ) : null}
    </Layer>
  );
}

// --- verified finding -------------------------------------------------------------------------

function RootCauseCard({
  rootCause,
  candidate,
  graphState,
  classificationLabel,
  onSelect,
}: {
  rootCause: RootCause;
  candidate: Candidate | undefined;
  graphState: "loading" | "error" | "ready";
  classificationLabel: string;
  onSelect: Select;
}) {
  const passed = candidate?.checks.filter((c) => c.state === "pass") ?? [];
  return (
    <article className="finding">
      <header className="finding-head">
        <h3 className="finding-service">{rootCause.service}</h3>
        <span className="badge neutral">{classificationLabel}</span>
        <SupportBadge support={rootCause.support} />
      </header>
      <p>
        <span className="muted">Mechanism: </span>
        {mechanismLabel(rootCause.mechanism)}
      </p>
      <h4 className="subhead">Checks that passed</h4>
      {graphState === "loading" ? (
        <Loading label="Loading checks…" />
      ) : graphState === "error" ? (
        <p className="muted small">The verification checks could not be loaded.</p>
      ) : passed.length ? (
        <ul className="check-list">
          {passed.map((c) => (
            <li key={c.name}>
              <CheckBadge state={c.state} /> <strong>{checkName(c.name)}</strong>
              {c.reason ? <span className="check-reason"> — {c.reason}</span> : null}
              <Citations ids={c.evidence_ids} onSelect={onSelect} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted small">No check passed for this service.</p>
      )}
      <Citations ids={rootCause.supporting_evidence_ids} onSelect={onSelect} label="Supporting evidence" />
      <Citations
        ids={rootCause.contradicting_evidence_ids}
        onSelect={onSelect}
        label="Contradicting evidence"
      />
    </article>
  );
}

export function FindingsLayer({
  investigation,
  graph,
  graphState,
  onSelect,
}: {
  investigation: Investigation;
  graph: Graph | undefined;
  graphState: "loading" | "error" | "ready";
  onSelect: Select;
}) {
  const p = presentClassification(investigation.classification);
  if (!p.presentsFindings) {
    return (
      <Layer kind="finding" title="Root cause">
        <p className="no-finding">
          {p.tone === "nothing"
            ? "No root cause is stated: nothing was found in what was checked. Review what was not checked under Uncertainty."
            : p.tone === "insufficient"
              ? "No root cause is stated: there is not enough evidence to name one. Hypotheses below are unverified; follow the next checks."
              : "No root cause is stated."}
        </p>
      </Layer>
    );
  }
  const roots = investigation.root_causes;
  return (
    <Layer
      kind="finding"
      title={roots.length > 1 ? "Root causes (independent faults)" : "Root cause"}
      description={`${p.label}: ${p.explanation}`}
    >
      {roots.length === 0 ? (
        <p className="no-finding">The verdict names no root cause.</p>
      ) : (
        roots.map((rc) => (
          <RootCauseCard
            key={rc.service}
            rootCause={rc}
            candidate={graph?.candidates.find((c) => c.service === rc.service)}
            graphState={graphState}
            classificationLabel={p.label}
            onSelect={onSelect}
          />
        ))
      )}
    </Layer>
  );
}

// --- uncertainty ------------------------------------------------------------------------------

export function UncertaintyLayer({
  investigation,
  graph,
  snapshot,
  onSelect,
}: {
  investigation: Investigation;
  graph: Graph | undefined;
  snapshot: SnapshotSummary | null | undefined;
  onSelect: Select;
}) {
  const unresolved = (graph?.candidates ?? []).flatMap((c) =>
    c.checks
      .filter((ch) => ch.state === "fail" || ch.state === "not_evaluable")
      .map((ch) => ({ candidate: c.service, check: ch })),
  );
  const collectionProblems = (snapshot?.collection ?? []).filter((c) => c.status !== "ok");
  return (
    <Layer kind="uncertainty" title="What is not known">
      <h3 className="subhead">Next checks</h3>
      {investigation.next_checks.length ? (
        <ol className="next-checks">
          {investigation.next_checks.map((n, i) => (
            <li key={i}>
              <strong>{n.action}</strong>
              {n.reason ? <span className="muted"> — {n.reason}</span> : null}
              {n.services.length ? <span className="small"> (services: {n.services.join(", ")})</span> : null}
            </li>
          ))}
        </ol>
      ) : (
        <p className="muted small">No next check was suggested.</p>
      )}

      <h3 className="subhead">Coverage gaps</h3>
      {investigation.coverage_gaps.length ? (
        <ul className="gap-list">
          {investigation.coverage_gaps.map((g, i) => (
            <li key={i}>{g}</li>
          ))}
        </ul>
      ) : (
        <p className="muted small">No coverage gap was reported.</p>
      )}

      {collectionProblems.length ? (
        <>
          <h3 className="subhead">Telemetry not fully collected</h3>
          <ul className="gap-list">
            {collectionProblems.map((c, i) => (
              <li key={i}>
                {c.modality} from {c.provider}: {collectionStatusLabel(c.status)}
                {c.detail ? ` — ${c.detail}` : ""}
              </li>
            ))}
          </ul>
        </>
      ) : null}

      <h3 className="subhead">Checks that failed or could not be evaluated</h3>
      {!graph ? (
        <p className="muted small">Checks are loading or unavailable.</p>
      ) : unresolved.length ? (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Candidate</th>
                <th scope="col">Check</th>
                <th scope="col">State</th>
                <th scope="col">Reason</th>
                <th scope="col">Evidence</th>
              </tr>
            </thead>
            <tbody>
              {unresolved.map(({ candidate, check }, i) => (
                <tr key={i}>
                  <td>{candidate}</td>
                  <td>{checkName(check.name)}</td>
                  <td>
                    <CheckBadge state={check.state} />
                    {check.qualifier ? <span className="muted small"> ({check.qualifier})</span> : null}
                  </td>
                  <td>{check.reason}</td>
                  <td>
                    <Citations ids={check.evidence_ids} onSelect={onSelect} label="Cited" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted small">Every check either passed or was not applicable.</p>
      )}

      {investigation.reasons.length ? (
        <>
          <h3 className="subhead">Why this classification</h3>
          <ul className="reason-list">
            {investigation.reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </>
      ) : null}

      {investigation.other_anomalies.length ? (
        <>
          <h3 className="subhead">Other anomalies not explained by the verdict</h3>
          <ul className="reason-list">
            {investigation.other_anomalies.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </>
      ) : null}
    </Layer>
  );
}

// --- AI assessment ----------------------------------------------------------------------------

export function AiAssessmentLayer({ ai, onSelect }: { ai: AiAssessment | null | undefined; onSelect: Select }) {
  if (!ai || typeof ai.status !== "string") return null;
  const accepted = ai.status === "accepted";
  const provider = [ai.provider, ai.model, ai.locality ? `${ai.locality} provider` : null]
    .filter(Boolean)
    .join(" · ");
  return (
    <Layer
      kind="ai"
      title="AI assessment"
      description="A language model's narrative, validated against the deterministic analysis. It can cite only existing evidence and can only lower the deterministic result, never raise it. The deterministic verdict above stays authoritative."
    >
      <p>
        <span className="muted">Status: </span>
        <strong>{aiStatusLabel(ai.status)}</strong>
        {provider ? <span className="muted small"> ({provider})</span> : null}
      </p>
      {ai.reason ? <p className="small">Reason: {ai.reason}</p> : null}
      {!accepted && ai.errors && ai.errors.length ? (
        <>
          <h3 className="subhead">Validation errors</h3>
          <ul className="reason-list">
            {ai.errors.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </>
      ) : null}
      {accepted ? (
        <>
          {ai.lowered ? (
            <Notice tone="warning" title="AI lowered the deterministic result" role="note">
              <p>The AI assessment is weaker than the deterministic verdict.</p>
            </Notice>
          ) : null}
          {ai.disagreement ? (
            <Notice tone="warning" title="AI disagrees with the deterministic root cause" role="note">
              <p>
                The AI selected {ai.selected_candidate ? `“${ai.selected_candidate}”` : "a different candidate"},
                which is not the deterministic root cause. Its result is therefore capped at “Likely cause”.
              </p>
            </Notice>
          ) : null}
          <div className="verdict-line">
            <span className="muted small">AI rating:</span>
            <VerdictBadge classification={ai.classification} />
            <SupportBadge support={ai.support} />
            {ai.selected_candidate ? (
              <span className="small">
                <span className="muted">Selected candidate: </span>
                {ai.selected_candidate}
              </span>
            ) : null}
            {ai.mechanism && ai.mechanism !== "unknown" ? (
              <span className="small">
                <span className="muted">Mechanism: </span>
                {mechanismLabel(ai.mechanism)}
              </span>
            ) : null}
          </div>
          {ai.summary ? <p className="ai-summary">{ai.summary}</p> : null}
          <h3 className="subhead">Claims</h3>
          {ai.claims && ai.claims.length ? (
            <ul className="claims">
              {ai.claims.map((c, i) => (
                <li key={i} className="claim">
                  <span className={`badge claim-${c.type === "observation" ? "observation" : "inference"}`}>
                    {c.type === "observation" ? "Observation" : "Inference"}
                  </span>{" "}
                  <span>{c.text}</span>
                  <Citations ids={c.evidence_ids} onSelect={onSelect} label="Cites" />
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted small">No cited claims.</p>
          )}
          {ai.contradicting_evidence_ids && ai.contradicting_evidence_ids.length ? (
            <Citations ids={ai.contradicting_evidence_ids} onSelect={onSelect} label="Contradicting evidence" />
          ) : null}
          {ai.uncertainty ? (
            <>
              <h3 className="subhead">Uncertainty stated by the AI</h3>
              <p>{ai.uncertainty}</p>
            </>
          ) : null}
          {ai.missing_information && ai.missing_information.length ? (
            <>
              <h3 className="subhead">Missing information</h3>
              <ul className="reason-list">
                {ai.missing_information.map((m, i) => (
                  <li key={i}>{m}</li>
                ))}
              </ul>
            </>
          ) : null}
          {ai.next_check ? (
            <p>
              <span className="muted">AI-suggested next check: </span>
              {ai.next_check}
            </p>
          ) : null}
        </>
      ) : null}
    </Layer>
  );
}

// --- hypotheses and inference -----------------------------------------------------------------

function CandidateCard({ candidate, onSelect }: { candidate: Candidate; onSelect: Select }) {
  return (
    <article className="candidate">
      <header className="candidate-head">
        <span className="candidate-rank">#{candidate.rank}</span>
        <h3 className="candidate-service">{candidate.service}</h3>
        <span className="badge neutral">{hypothesisLabel(candidate.hypothesis)}</span>
        {candidate.rule ? <span className="badge leading">Leading (rule {candidate.rule})</span> : null}
        {candidate.inferred ? (
          <span className="badge neutral" title="This service has no telemetry of its own; it was inferred from its callers' spans">
            Inferred
          </span>
        ) : null}
      </header>
      <details className="disclosure">
        <summary>Features ({Object.keys(candidate.features).length})</summary>
        <KeyValueList data={candidate.features} emptyText="No features." className="kv-compact" />
      </details>
      {candidate.checks.length ? (
        <div className="table-wrap">
          <table className="table table-compact">
            <caption className="visually-hidden">Verification checks for {candidate.service}</caption>
            <thead>
              <tr>
                <th scope="col">Check</th>
                <th scope="col">State</th>
                <th scope="col">Reason</th>
                <th scope="col">Evidence</th>
              </tr>
            </thead>
            <tbody>
              {candidate.checks.map((c) => (
                <tr key={c.name}>
                  <td>{checkName(c.name)}</td>
                  <td>
                    <CheckBadge state={c.state} />
                    {c.qualifier ? <span className="muted small"> ({c.qualifier})</span> : null}
                  </td>
                  <td>{c.reason}</td>
                  <td>
                    <Citations ids={c.evidence_ids} onSelect={onSelect} label="Cited" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted small">No checks were run for this candidate.</p>
      )}
      <Citations ids={candidate.supporting_evidence_ids} onSelect={onSelect} label="Supporting evidence" />
      <Citations ids={candidate.contradicting_evidence_ids} onSelect={onSelect} label="Contradicting evidence" />
    </article>
  );
}

export function HypothesesLayer({ graph, onSelect }: { graph: Graph; onSelect: Select }) {
  const ranked = [...graph.candidates].sort((a, b) => a.rank - b.rank);
  return (
    <Layer
      kind="hypothesis"
      title="Candidates considered"
      description="Hypotheses are candidates the analysis ranked, not conclusions. Only the verified finding above is a stated result."
    >
      {ranked.length ? (
        ranked.map((c) => <CandidateCard key={c.service} candidate={c} onSelect={onSelect} />)
      ) : (
        <p className="muted small">No candidate was ranked.</p>
      )}
    </Layer>
  );
}

export function InferenceLayer({ graph }: { graph: Graph }) {
  const leaders = graph.candidates.filter((c) => c.rule);
  return (
    <Layer
      kind="inference"
      title="How the leading candidate was selected"
      description="An ordered decision list: the first rule that selects a leader wins."
    >
      {leaders.length ? (
        <ul className="rule-list">
          {leaders.map((c) => (
            <li key={c.service}>
              <strong>{c.service}</strong> — rule <code>{c.rule}</code>
              {c.rule_sentence ? <>: {c.rule_sentence}</> : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted small">No rule selected a leading candidate.</p>
      )}
      <h3 className="subhead">Decision trace</h3>
      {graph.decision_trace.length ? (
        <ol className="trace">
          {graph.decision_trace.map((t, i) => (
            <li key={i}>{t}</li>
          ))}
        </ol>
      ) : (
        <p className="muted small">No decision trace was recorded.</p>
      )}
    </Layer>
  );
}

// --- facts ------------------------------------------------------------------------------------

function CitedEvidence({
  investigationId,
  evidenceId,
  itemRef,
  onClose,
}: {
  investigationId: string;
  evidenceId: string;
  itemRef: (el: HTMLElement | null) => void;
  onClose: () => void;
}) {
  const q = useQuery({
    queryKey: qk.evidenceItem(investigationId, evidenceId),
    queryFn: () => fetchEvidenceItem(investigationId, evidenceId),
  });
  if (q.isPending) return <Loading label={`Loading evidence ${evidenceId}…`} />;
  if (q.isError) {
    return (
      <div ref={itemRef} tabIndex={-1}>
        {isNotFound(q.error) ? (
          <Notice tone="warning" title="Cited evidence not found">
            <p>
              Evidence <code>{evidenceId}</code> is cited but is not stored for this investigation.
            </p>
          </Notice>
        ) : (
          <ErrorNotice error={q.error} />
        )}
      </div>
    );
  }
  return <EvidenceItem evidence={q.data} expanded onToggle={onClose} highlighted itemRef={itemRef} />;
}

// --- composition ------------------------------------------------------------------------------

export function RcaView({
  investigation,
  snapshot,
}: {
  investigation: Investigation;
  snapshot: SnapshotSummary | null | undefined;
}) {
  const id = investigation.id;
  const graphQ = useQuery({ queryKey: qk.graph(id), queryFn: () => fetchGraph(id) });
  const evidenceQ = useQuery({
    queryKey: qk.evidenceAll(id),
    queryFn: ({ signal }) => fetchAllEvidence(id, signal),
  });
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set());
  const [focused, setFocused] = useState<string | null>(null);
  const elements = useRef(new Map<string, HTMLElement>());
  const pendingScroll = useRef<string | null>(null);

  const evidence: Evidence[] = useMemo(() => evidenceQ.data?.items ?? [], [evidenceQ.data]);
  const known = useMemo(() => new Set(evidence.map((e) => e.evidence_id)), [evidence]);

  const reveal = useCallback((el: HTMLElement) => {
    el.scrollIntoView?.({ block: "center", behavior: "smooth" });
    el.focus({ preventScroll: true });
  }, []);

  const select = useCallback(
    (evidenceId: string) => {
      setOpen((prev) => new Set(prev).add(evidenceId));
      setFocused(evidenceId);
      const el = elements.current.get(evidenceId);
      if (el) reveal(el);
      else pendingScroll.current = evidenceId;
    },
    [reveal],
  );

  const refFor = (evidenceId: string) => (el: HTMLElement | null) => {
    if (el) {
      elements.current.set(evidenceId, el);
      if (pendingScroll.current === evidenceId) {
        pendingScroll.current = null;
        reveal(el);
      }
    } else {
      elements.current.delete(evidenceId);
    }
  };

  const toggle = (evidenceId: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(evidenceId)) next.delete(evidenceId);
      else next.add(evidenceId);
      return next;
    });

  // Cited evidence that is not in the loaded list (beyond the cap) is fetched individually.
  const extras = evidenceQ.isSuccess ? [...open].filter((e) => !known.has(e)) : [];
  const graphState = graphQ.isPending ? "loading" : graphQ.isError ? "error" : "ready";

  return (
    <div className="rca">
      <VerdictSummary investigation={investigation} />
      <FindingsLayer
        investigation={investigation}
        graph={graphQ.data}
        graphState={graphState}
        onSelect={select}
      />
      <UncertaintyLayer investigation={investigation} graph={graphQ.data} snapshot={snapshot} onSelect={select} />
      <AiAssessmentLayer ai={investigation.ai} onSelect={select} />
      {graphQ.isPending ? (
        <Loading label="Loading hypotheses and decision trace…" />
      ) : graphQ.isError ? (
        <ErrorNotice error={graphQ.error} title="The analysis graph could not be loaded" onRetry={() => void graphQ.refetch()} />
      ) : (
        <>
          <HypothesesLayer graph={graphQ.data} onSelect={select} />
          <InferenceLayer graph={graphQ.data} />
        </>
      )}
      <Layer
        kind="fact"
        title={`Evidence${evidenceQ.data ? ` (${evidence.length}${evidenceQ.data.truncated ? "+" : ""})` : ""}`}
        description="Observations extracted from the collected telemetry, each with its provenance (provider and query). Summaries are derived from telemetry and are shown as text."
        actions={
          <>
            <button type="button" className="btn btn-small" onClick={() => setOpen(new Set(known))} disabled={!evidence.length}>
              Expand all
            </button>
            <button type="button" className="btn btn-small" onClick={() => setOpen(new Set())} disabled={!open.size}>
              Collapse all
            </button>
            <Link className="btn btn-small" to={`/investigations/${encodeURIComponent(id)}/evidence`}>
              Evidence explorer
            </Link>
          </>
        }
        id="evidence"
      >
        {extras.map((e) => (
          <CitedEvidence
            key={e}
            investigationId={id}
            evidenceId={e}
            itemRef={refFor(e)}
            onClose={() => toggle(e)}
          />
        ))}
        {evidenceQ.isPending ? (
          <Loading label="Loading evidence…" />
        ) : evidenceQ.isError ? (
          <ErrorNotice error={evidenceQ.error} onRetry={() => void evidenceQ.refetch()} />
        ) : evidence.length === 0 ? (
          <p className="muted">No evidence was stored for this investigation.</p>
        ) : (
          <div className="evidence-list">
            {evidenceQ.data.truncated ? (
              <p className="muted small">
                Showing the first {evidence.length} items; open the evidence explorer for the rest.
              </p>
            ) : null}
            {evidence.map((e) => (
              <EvidenceItem
                key={e.evidence_id}
                evidence={e}
                expanded={open.has(e.evidence_id)}
                onToggle={() => toggle(e.evidence_id)}
                highlighted={focused === e.evidence_id}
                itemRef={refFor(e.evidence_id)}
              />
            ))}
          </div>
        )}
      </Layer>
    </div>
  );
}
