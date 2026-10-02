import { useId, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, seg } from "../../api/client";
import { qk } from "../../api/queries";
import type { Investigation, InvestigationStart, Sources } from "../../api/types";
import { SyntheticBadge } from "../../components/Badge";
import { ErrorNotice, Loading } from "../../components/Feedback";
import { Panel } from "../../components/Layout";
import { randomToken } from "../../lib/misc";

/** Starts an investigation for an incident. Live collection when available; synthetic scenarios
 * are clearly marked. An Idempotency-Key makes a double submit return the same investigation. */
export function InvestigatePanel({ incidentId, onClose }: { incidentId: string; onClose: () => void }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const groupName = useId();
  const [idempotencyKey] = useState(randomToken);
  const [choice, setChoice] = useState<string | null>(null);
  const sources = useQuery({ queryKey: qk.sources, queryFn: () => api.get<Sources>("/api/v1/sources") });

  const start = useMutation({
    mutationFn: (body: InvestigationStart) =>
      api.post<Investigation>(`/api/v1/incidents/${seg(incidentId)}/investigations`, body, {
        headers: { "Idempotency-Key": idempotencyKey },
      }),
    onSuccess: (inv) => {
      void qc.invalidateQueries({ queryKey: ["incident", incidentId] });
      void qc.invalidateQueries({ queryKey: ["investigations"] });
      navigate(`/investigations/${encodeURIComponent(inv.id)}`);
    },
  });

  // Live is preselected when available; a synthetic scenario is never chosen implicitly.
  const selected = choice ?? (sources.data?.live ? "live" : "");

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!selected) return;
    const body: InvestigationStart =
      selected === "live"
        ? { source: { kind: "live" } }
        : { source: { kind: "scenario", scenario: selected.slice("scenario:".length) } };
    start.mutate(body);
  }

  return (
    <Panel title="Start an investigation">
      {sources.isPending ? (
        <Loading label="Loading telemetry sources…" />
      ) : sources.isError ? (
        <ErrorNotice error={sources.error} onRetry={() => void sources.refetch()} />
      ) : (
        <form onSubmit={submit}>
          <fieldset className="radio-group">
            <legend>Telemetry source</legend>
            <label className="radio-label">
              <input
                type="radio"
                name={groupName}
                value="live"
                checked={selected === "live"}
                disabled={!sources.data.live}
                onChange={() => setChoice("live")}
              />
              <span>
                Live telemetry from your integrations
                {!sources.data.live ? (
                  <span className="muted small"> — unavailable: no enabled telemetry integration</span>
                ) : null}
              </span>
            </label>
            {sources.data.scenarios.length ? (
              <p className="small muted radio-note">
                Synthetic scenarios: {sources.data.note}
              </p>
            ) : null}
            {sources.data.scenarios.map((s) => (
              <label className="radio-label" key={s}>
                <input
                  type="radio"
                  name={groupName}
                  value={`scenario:${s}`}
                  checked={selected === `scenario:${s}`}
                  onChange={() => setChoice(`scenario:${s}`)}
                />
                <span>
                  Scenario <code>{s}</code> <SyntheticBadge compact />
                </span>
              </label>
            ))}
          </fieldset>
          {selected.startsWith("scenario:") ? (
            <p className="notice notice-warning small">
              A scenario analyses SYNTHETIC telemetry, not your systems. Use it for demonstrations and
              pipeline checks only.
            </p>
          ) : null}
          <div className="form-actions">
            <button type="submit" className="btn btn-primary" disabled={!selected || start.isPending}>
              {start.isPending ? "Starting…" : "Start investigation"}
            </button>
            <button type="button" className="btn" onClick={onClose}>
              Cancel
            </button>
          </div>
          {start.isError ? <ErrorNotice error={start.error} title="The investigation was not started" /> : null}
        </form>
      )}
    </Panel>
  );
}
