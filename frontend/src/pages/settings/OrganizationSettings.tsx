import { useId, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { qk } from "../../api/queries";
import type { Integration, Organization, OrgSettings, SourceSpec, Sources } from "../../api/types";
import { useCan } from "../../auth/context";
import { P } from "../../auth/permissions";
import { SyntheticBadge } from "../../components/Badge";
import { ErrorNotice, Loading, Notice } from "../../components/Feedback";
import { Panel } from "../../components/Layout";
import { RequirePermission } from "./SettingsLayout";

function sourceToValue(s: SourceSpec | null | undefined): string {
  if (!s) return "";
  return s.kind === "live" ? "live" : `scenario:${s.scenario}`;
}

function valueToSource(v: string): SourceSpec | null {
  if (!v) return null;
  if (v === "live") return { kind: "live" };
  return { kind: "scenario", scenario: v.slice("scenario:".length) };
}

function describeSource(s: SourceSpec | null | undefined): string {
  if (!s) return "None (manual starts must choose a source)";
  return s.kind === "live" ? "Live telemetry" : `Scenario ${s.scenario} (synthetic)`;
}

const AI_KEYS = [
  "ai_enabled",
  "ai_integration_id",
  "ai_model",
  "ai_allow_cloud",
  "ai_daily_token_budget",
  "ai_max_output_tokens",
] as const;

function ReadOnlySettings({ settings, aiSupported }: { settings: OrgSettings; aiSupported: boolean }) {
  const yes = (b: boolean | undefined) => (b ? "On" : "Off");
  return (
    <dl className="kv">
      <div className="kv-row">
        <dt>Automatic investigation</dt>
        <dd>{yes(settings.auto_investigate)}</dd>
      </div>
      <div className="kv-row">
        <dt>Default source</dt>
        <dd>
          {describeSource(settings.default_source)}{" "}
          {settings.default_source?.kind === "scenario" ? <SyntheticBadge compact /> : null}
        </dd>
      </div>
      <div className="kv-row">
        <dt>Alert grouping window</dt>
        <dd>{settings.grouping_window_minutes} min</dd>
      </div>
      <div className="kv-row">
        <dt>Auto-resolve</dt>
        <dd>{yes(settings.auto_resolve)}</dd>
      </div>
      <div className="kv-row">
        <dt>Remediation execution</dt>
        <dd>{yes(settings.remediation_enabled)}</dd>
      </div>
      {aiSupported ? (
        <>
          <div className="kv-row">
            <dt>AI adjudication</dt>
            <dd>{yes(settings.ai_enabled)}</dd>
          </div>
          <div className="kv-row">
            <dt>Cloud AI providers</dt>
            <dd>{settings.ai_allow_cloud ? "Allowed" : "Not allowed"}</dd>
          </div>
        </>
      ) : null}
    </dl>
  );
}

function SettingsForm({ org, sources, llmIntegrations, canReadIntegrations }: {
  org: Organization;
  sources: Sources | undefined;
  llmIntegrations: Integration[] | undefined;
  canReadIntegrations: boolean;
}) {
  const qc = useQueryClient();
  const initial = org.settings;
  const aiSupported = initial.ai_enabled !== undefined;
  const [s, setS] = useState<OrgSettings>(initial);
  const [saved, setSaved] = useState(false);
  const ids = {
    auto: useId(), source: useId(), grouping: useId(), resolve: useId(), remediation: useId(),
    aiEnabled: useId(), aiIntegration: useId(), aiModel: useId(), aiCloud: useId(), aiBudget: useId(), aiMax: useId(),
  };
  const set = <K extends keyof OrgSettings>(k: K, v: OrgSettings[K]) => {
    setSaved(false);
    setS((prev) => ({ ...prev, [k]: v }));
  };

  const save = useMutation({
    mutationFn: (patch: Partial<OrgSettings>) => api.patch<Organization>("/api/v1/organization/settings", patch),
    onSuccess: (updated) => {
      qc.setQueryData(qk.organization, updated);
      setS(updated.settings);
      setSaved(true);
    },
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    // Only changed keys are sent (the server merges them into the current settings).
    const patch: Record<string, unknown> = {};
    for (const key of Object.keys(s) as (keyof OrgSettings)[]) {
      if (JSON.stringify(s[key] ?? null) !== JSON.stringify(initial[key] ?? null)) patch[key] = s[key] ?? null;
    }
    if (!aiSupported) for (const k of AI_KEYS) delete patch[k];
    if (Object.keys(patch).length === 0) {
      setSaved(true);
      return;
    }
    save.mutate(patch as Partial<OrgSettings>);
  }

  const defaultValue = sourceToValue(s.default_source);
  const knownScenario = sources?.scenarios ?? [];
  const sourceOptions = new Set(["", "live", ...knownScenario.map((x) => `scenario:${x}`)]);

  return (
    <form className="settings-form" onSubmit={submit} aria-label="Organization settings">
      <fieldset>
        <legend>Investigations</legend>
        <div className="field field-check">
          <input id={ids.auto} type="checkbox" checked={s.auto_investigate} onChange={(e) => set("auto_investigate", e.target.checked)} />
          <label htmlFor={ids.auto}>Start an investigation automatically for new incidents (needs a default source or live telemetry)</label>
        </div>
        <div className="field">
          <label htmlFor={ids.source}>Default source</label>
          <select id={ids.source} value={defaultValue} onChange={(e) => set("default_source", valueToSource(e.target.value))}>
            <option value="">None</option>
            <option value="live">Live telemetry{sources && !sources.live ? " (no telemetry integration yet)" : ""}</option>
            {knownScenario.map((x) => (
              <option key={x} value={`scenario:${x}`}>
                Scenario {x} — SYNTHETIC
              </option>
            ))}
            {!sourceOptions.has(defaultValue) ? <option value={defaultValue}>{describeSource(s.default_source)}</option> : null}
          </select>
          {s.default_source?.kind === "scenario" ? (
            <p className="notice notice-warning small">
              <SyntheticBadge compact /> Automatic investigations will analyse a synthetic scenario, not your telemetry.
            </p>
          ) : null}
        </div>
      </fieldset>

      <fieldset>
        <legend>Alert grouping</legend>
        <div className="field">
          <label htmlFor={ids.grouping}>Grouping window (minutes, 1–1440)</label>
          <input
            id={ids.grouping}
            type="number"
            min={1}
            max={1440}
            required
            value={s.grouping_window_minutes}
            onChange={(e) => set("grouping_window_minutes", Number(e.target.value))}
          />
        </div>
        <div className="field field-check">
          <input id={ids.resolve} type="checkbox" checked={s.auto_resolve} onChange={(e) => set("auto_resolve", e.target.checked)} />
          <label htmlFor={ids.resolve}>Resolve incidents automatically when their alerts resolve</label>
        </div>
      </fieldset>

      <fieldset>
        <legend>Remediation</legend>
        <div className="field field-check">
          <input
            id={ids.remediation}
            type="checkbox"
            checked={s.remediation_enabled}
            onChange={(e) => set("remediation_enabled", e.target.checked)}
          />
          <label htmlFor={ids.remediation}>Enable remediation execution</label>
        </div>
        <Notice tone="warning" role="note">
          <p>
            Enabling this allows Aegis to execute remediation actions once a person has approved them. Approved
            actions change production systems.
          </p>
        </Notice>
      </fieldset>

      {aiSupported ? (
        <fieldset>
          <legend>AI adjudication</legend>
          <p className="small">
            Off by default. When enabled, a language model reviews a redacted, bounded projection of each
            deterministic analysis. Its output is validated: it may cite only existing evidence and can only{" "}
            <strong>lower</strong> the deterministic classification or support, never raise them. If it fails or
            is unavailable, the deterministic verdict stands. There is no fallback to another provider.
          </p>
          <div className="field field-check">
            <input id={ids.aiEnabled} type="checkbox" checked={Boolean(s.ai_enabled)} onChange={(e) => set("ai_enabled", e.target.checked)} />
            <label htmlFor={ids.aiEnabled}>Enable AI adjudication</label>
          </div>
          <div className="field">
            <label htmlFor={ids.aiIntegration}>Model provider (an LLM integration)</label>
            {canReadIntegrations ? (
              <select
                id={ids.aiIntegration}
                value={s.ai_integration_id ?? ""}
                onChange={(e) => set("ai_integration_id", e.target.value || null)}
              >
                <option value="">None</option>
                {(llmIntegrations ?? []).map((i) => (
                  <option key={i.id} value={i.id}>
                    {i.name} ({i.connector_id}){i.enabled ? "" : " — disabled"}
                  </option>
                ))}
                {s.ai_integration_id && !(llmIntegrations ?? []).some((i) => i.id === s.ai_integration_id) ? (
                  <option value={s.ai_integration_id}>Unknown integration {s.ai_integration_id}</option>
                ) : null}
              </select>
            ) : (
              <input id={ids.aiIntegration} readOnly value={s.ai_integration_id ?? "None"} />
            )}
            {llmIntegrations && llmIntegrations.length === 0 ? (
              <p className="muted small">No LLM integration exists yet; add one under Integrations (the operator must register an LLM connector).</p>
            ) : null}
          </div>
          <div className="field">
            <label htmlFor={ids.aiModel}>Model (optional; the connector's default when empty)</label>
            <input
              id={ids.aiModel}
              maxLength={200}
              value={s.ai_model ?? ""}
              onChange={(e) => set("ai_model", e.target.value.trim() ? e.target.value : null)}
            />
          </div>
          <div className="field field-check">
            <input id={ids.aiCloud} type="checkbox" checked={Boolean(s.ai_allow_cloud)} onChange={(e) => set("ai_allow_cloud", e.target.checked)} />
            <label htmlFor={ids.aiCloud}>Allow cloud AI providers</label>
          </div>
          <p className="muted small">
            Explicit opt-in: with a cloud provider, redacted investigation data leaves your network and is sent to that
            provider. Redaction is heuristic and confidentiality from the provider is not guaranteed. Cloud providers
            are refused unless this is on.
          </p>
          <div className="field">
            <label htmlFor={ids.aiBudget}>Daily token budget (0–100,000,000; per 24 h)</label>
            <input
              id={ids.aiBudget}
              type="number"
              min={0}
              max={100_000_000}
              required
              value={s.ai_daily_token_budget ?? 0}
              onChange={(e) => set("ai_daily_token_budget", Number(e.target.value))}
            />
          </div>
          <div className="field">
            <label htmlFor={ids.aiMax}>Maximum output tokens per call (64–8192)</label>
            <input
              id={ids.aiMax}
              type="number"
              min={64}
              max={8192}
              required
              value={s.ai_max_output_tokens ?? 1200}
              onChange={(e) => set("ai_max_output_tokens", Number(e.target.value))}
            />
          </div>
        </fieldset>
      ) : null}

      <div className="form-actions">
        <button type="submit" className="btn btn-primary" disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save settings"}
        </button>
        <span aria-live="polite" className="small">
          {saved && !save.isPending ? "Settings saved." : ""}
        </span>
      </div>
      {save.isError ? <ErrorNotice error={save.error} title="The settings were not saved" /> : null}
    </form>
  );
}

export function OrganizationSettings() {
  const can = useCan();
  const org = useQuery({ queryKey: qk.organization, queryFn: () => api.get<Organization>("/api/v1/organization") });
  const sources = useQuery({
    queryKey: qk.sources,
    queryFn: () => api.get<Sources>("/api/v1/sources"),
    enabled: can(P.investigationRead),
  });
  const integrations = useQuery({
    queryKey: qk.integrations,
    queryFn: () => api.get<Integration[]>("/api/v1/integrations"),
    enabled: can(P.integrationRead) && can(P.orgManage),
  });
  const llm = integrations.data?.filter((i) => i.kind === "llm");
  return (
    <RequirePermission permission={P.orgRead}>
      <Panel title={org.data ? `Organization: ${org.data.name}` : "Organization"}>
        {org.isPending ? (
          <Loading />
        ) : org.isError ? (
          <ErrorNotice error={org.error} onRetry={() => void org.refetch()} />
        ) : (
          <>
            <p className="muted small">
              Slug <code>{org.data.slug}</code> · ID <code>{org.data.id}</code>
            </p>
            {can(P.orgManage) ? (
              <SettingsForm
                key={org.data.id}
                org={org.data}
                sources={sources.data}
                llmIntegrations={llm}
                canReadIntegrations={can(P.integrationRead)}
              />
            ) : (
              <ReadOnlySettings settings={org.data.settings} aiSupported={org.data.settings.ai_enabled !== undefined} />
            )}
          </>
        )}
      </Panel>
    </RequirePermission>
  );
}
