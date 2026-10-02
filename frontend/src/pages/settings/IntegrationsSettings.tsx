import { useId, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, seg } from "../../api/client";
import { qk } from "../../api/queries";
import type { Connector, CredentialIn, Integration, IntegrationTest } from "../../api/types";
import { useCan } from "../../auth/context";
import { P } from "../../auth/permissions";
import { ConfirmButton } from "../../components/Controls";
import { EmptyState, ErrorNotice, Loading, Notice } from "../../components/Feedback";
import { Panel } from "../../components/Layout";
import { DateTime, Relative } from "../../components/Time";
import { capitalize } from "../../lib/format";
import { CredentialFields, EMPTY_CREDENTIAL } from "./CredentialFields";
import { RequirePermission } from "./SettingsLayout";

const integrationPath = (id: string) => `/api/v1/integrations/${seg(id)}`;

function useInvalidateIntegrations() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: qk.integrations });
    void qc.invalidateQueries({ queryKey: ["dashboard", "integrations"] });
    void qc.invalidateQueries({ queryKey: qk.sources });
  };
}

function CreateIntegration({ connectors, existing }: { connectors: Connector[]; existing: Integration[] }) {
  const invalidate = useInvalidateIntegrations();
  const used = new Set(existing.map((i) => i.connector_id));
  const free = connectors.filter((c) => !used.has(c.id));
  const ids = { connector: useId(), name: useId(), enabled: useId() };
  const [connectorId, setConnectorId] = useState("");
  const [name, setName] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [credential, setCredential] = useState<CredentialIn>(EMPTY_CREDENTIAL);
  const create = useMutation({
    mutationFn: () =>
      api.post<Integration>("/api/v1/integrations", {
        connector_id: connectorId,
        name: name.trim(),
        enabled,
        credential,
      }),
    onSuccess: () => {
      setConnectorId("");
      setName("");
      setCredential(EMPTY_CREDENTIAL); // drop the secret from memory
      invalidate();
    },
  });
  if (free.length === 0) {
    return <p className="muted small">Every connector available to this organization already has an integration.</p>;
  }
  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate();
  }
  return (
    <form className="form-grid" onSubmit={submit} aria-label="Add integration">
      <div className="field">
        <label htmlFor={ids.connector}>Connector</label>
        <select id={ids.connector} required value={connectorId} onChange={(e) => setConnectorId(e.target.value)}>
          <option value="">Choose a connector…</option>
          {free.map((c) => (
            <option key={c.id} value={c.id}>
              {c.id} ({c.kind})
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor={ids.name}>Name</label>
        <input id={ids.name} required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="field field-check">
        <input id={ids.enabled} type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        <label htmlFor={ids.enabled}>Enabled</label>
      </div>
      <div className="field-wide">
        <CredentialFields value={credential} onChange={setCredential} />
      </div>
      <div className="form-actions field-wide">
        <button type="submit" className="btn btn-primary" disabled={create.isPending}>
          {create.isPending ? "Adding…" : "Add integration"}
        </button>
      </div>
      {create.isError ? (
        <div className="field-wide">
          <ErrorNotice error={create.error} title="The integration was not added" />
        </div>
      ) : null}
    </form>
  );
}

function ReplaceCredential({ integration, onDone }: { integration: Integration; onDone: () => void }) {
  const invalidate = useInvalidateIntegrations();
  const [credential, setCredential] = useState<CredentialIn>(EMPTY_CREDENTIAL);
  const update = useMutation({
    mutationFn: () => api.patch<Integration>(integrationPath(integration.id), { credential }),
    onSuccess: () => {
      setCredential(EMPTY_CREDENTIAL);
      invalidate();
      onDone();
    },
  });
  return (
    <form
      className="inline-form"
      onSubmit={(e) => {
        e.preventDefault();
        update.mutate();
      }}
      aria-label={`Replace credential of ${integration.name}`}
    >
      <CredentialFields value={credential} onChange={setCredential} legend="New credential" />
      <div className="form-actions">
        <button type="submit" className="btn btn-small btn-primary" disabled={update.isPending}>
          Save credential
        </button>
        <button type="button" className="btn btn-small" onClick={onDone}>
          Cancel
        </button>
      </div>
      {update.isError ? <ErrorNotice error={update.error} /> : null}
    </form>
  );
}

function IntegrationRow({ integration, canManage }: { integration: Integration; canManage: boolean }) {
  const invalidate = useInvalidateIntegrations();
  const [replacing, setReplacing] = useState(false);
  const [testResult, setTestResult] = useState<IntegrationTest | null>(null);
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api.patch<Integration>(integrationPath(integration.id), { enabled }),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: () => api.del(integrationPath(integration.id)),
    onSuccess: invalidate,
  });
  const test = useMutation({
    mutationFn: () => api.post<IntegrationTest>(`${integrationPath(integration.id)}/test`),
    onSuccess: (res) => {
      setTestResult(res);
      invalidate();
    },
  });
  const error = toggle.error ?? remove.error ?? test.error;
  return (
    <tr>
      <td>
        <strong>{integration.name}</strong>
        <div className="muted small mono">{integration.connector_id}</div>
        {!integration.connector_available ? (
          <span className="badge test-error" title="The operator removed this connector from the registry">
            connector unavailable
          </span>
        ) : null}
      </td>
      <td>{integration.kind}</td>
      <td>{integration.enabled ? "Enabled" : <span className="muted">Disabled</span>}</td>
      <td>
        {integration.credential_type}
        {integration.has_credential ? <span className="muted small"> (set)</span> : null}
      </td>
      <td>
        {integration.last_test_status ? (
          <span className={`badge ${integration.last_test_status === "ok" ? "test-ok" : "test-error"}`}>
            {capitalize(integration.last_test_status)}
          </span>
        ) : (
          <span className="muted">never</span>
        )}{" "}
        <Relative value={integration.last_test_at} />
        {integration.last_test_detail ? <div className="small">{integration.last_test_detail}</div> : null}
        {testResult ? (
          <div className={`notice notice-${testResult.status === "ok" ? "success" : "error"} small`} role="status">
            Test {testResult.status === "ok" ? "succeeded" : "failed"}
            {testResult.category ? ` (${testResult.category})` : ""}: {testResult.detail}
          </div>
        ) : null}
      </td>
      <td>
        <DateTime value={integration.updated_at} />
      </td>
      <td>
        {canManage ? (
          <div className="button-row">
            <button type="button" className="btn btn-small" onClick={() => test.mutate()} disabled={test.isPending}>
              {test.isPending ? "Testing…" : "Test connection"}
            </button>
            <button
              type="button"
              className="btn btn-small"
              onClick={() => toggle.mutate(!integration.enabled)}
              disabled={toggle.isPending}
            >
              {integration.enabled ? "Disable" : "Enable"}
            </button>
            <button type="button" className="btn btn-small" onClick={() => setReplacing((v) => !v)}>
              Replace credential
            </button>
            <ConfirmButton
              label="Delete"
              prompt={`Delete “${integration.name}”?`}
              onConfirm={() => remove.mutate()}
              pending={remove.isPending}
            />
          </div>
        ) : (
          <span className="muted small">read-only</span>
        )}
        {replacing ? <ReplaceCredential integration={integration} onDone={() => setReplacing(false)} /> : null}
        {error ? <ErrorNotice error={error} /> : null}
      </td>
    </tr>
  );
}

export function IntegrationsSettings() {
  const can = useCan();
  const canManage = can(P.integrationManage);
  const connectors = useQuery({ queryKey: qk.connectors, queryFn: () => api.get<Connector[]>("/api/v1/connectors") });
  const integrations = useQuery({ queryKey: qk.integrations, queryFn: () => api.get<Integration[]>("/api/v1/integrations") });
  return (
    <RequirePermission permission={P.integrationRead}>
      <Panel
        title="Integrations"
        description="Integrations connect this organization to telemetry backends (and other services) registered by the operator. You choose a connector and supply credentials; endpoints cannot be typed in."
      >
        {integrations.isPending ? (
          <Loading />
        ) : integrations.isError ? (
          <ErrorNotice error={integrations.error} onRetry={() => void integrations.refetch()} />
        ) : integrations.data.length === 0 ? (
          <EmptyState title="No integrations">
            <p>Without a telemetry integration, only synthetic scenarios can be investigated.</p>
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <caption className="visually-hidden">Integrations</caption>
              <thead>
                <tr>
                  <th scope="col">Name</th>
                  <th scope="col">Kind</th>
                  <th scope="col">State</th>
                  <th scope="col">Credential</th>
                  <th scope="col">Last test</th>
                  <th scope="col">Updated</th>
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody>
                {integrations.data.map((i) => (
                  <IntegrationRow key={i.id} integration={i} canManage={canManage} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
      {canManage && connectors.data && integrations.data ? (
        <Panel title="Add an integration">
          <CreateIntegration connectors={connectors.data} existing={integrations.data} />
        </Panel>
      ) : null}
      <Panel title="Available connectors" description="Registered by the deployment operator for this organization (read-only).">
        {connectors.isPending ? (
          <Loading />
        ) : connectors.isError ? (
          <ErrorNotice error={connectors.error} />
        ) : connectors.data.length === 0 ? (
          <Notice tone="info">
            <p>The operator has not registered any connector for this organization.</p>
          </Notice>
        ) : (
          <div className="table-wrap">
            <table className="table table-compact">
              <caption className="visually-hidden">Connectors</caption>
              <thead>
                <tr>
                  <th scope="col">Connector</th>
                  <th scope="col">Kind</th>
                  <th scope="col">Endpoint</th>
                </tr>
              </thead>
              <tbody>
                {connectors.data.map((c) => (
                  <tr key={c.id}>
                    <td className="mono">{c.id}</td>
                    <td>{c.kind}</td>
                    <td className="mono small">
                      {c.scheme}://{c.host}:{c.port}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </RequirePermission>
  );
}
