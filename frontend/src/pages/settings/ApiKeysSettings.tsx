import { useId, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, seg } from "../../api/client";
import { qk } from "../../api/queries";
import { API_KEY_SCOPES, type ApiKey, type ApiKeyCreated, type ApiKeyScope } from "../../api/types";
import { P } from "../../auth/permissions";
import { ConfirmButton, SecretOnce } from "../../components/Controls";
import { EmptyState, ErrorNotice, Loading } from "../../components/Feedback";
import { Panel } from "../../components/Layout";
import { DateTime, Relative } from "../../components/Time";
import { RequirePermission } from "./SettingsLayout";

const SCOPE_HELP: Record<ApiKeyScope, string> = {
  "ingest:alerts": "send alerts (Alertmanager / generic webhook)",
  "ingest:changes": "send change events",
  read: "read incidents, investigations, evidence and change events",
};

interface Revealed {
  title: string;
  secret: string;
  prefix: string;
}

function CreateKey({ onCreated }: { onCreated: (r: Revealed) => void }) {
  const qc = useQueryClient();
  const ids = { name: useId(), days: useId() };
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<ApiKeyScope[]>(["ingest:alerts"]);
  const [days, setDays] = useState(90);
  const create = useMutation({
    mutationFn: () =>
      api.post<ApiKeyCreated>("/api/v1/api-keys", { name: name.trim(), scopes, expires_in_days: days }),
    onSuccess: (res) => {
      setName("");
      void qc.invalidateQueries({ queryKey: qk.apiKeys });
      onCreated({ title: `API key “${res.key.name}” created`, secret: res.secret, prefix: res.key.prefix });
    },
  });
  function submit(e: FormEvent) {
    e.preventDefault();
    if (scopes.length) create.mutate();
  }
  return (
    <form className="form-grid" onSubmit={submit} aria-label="Create API key">
      <div className="field">
        <label htmlFor={ids.name}>Name</label>
        <input id={ids.name} required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor={ids.days}>Expires after (days, 1–365)</label>
        <input
          id={ids.days}
          type="number"
          min={1}
          max={365}
          required
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
        />
      </div>
      <fieldset className="checkbox-group field-wide">
        <legend>Scopes</legend>
        {API_KEY_SCOPES.map((s) => (
          <label key={s} className="check-label">
            <input
              type="checkbox"
              checked={scopes.includes(s)}
              onChange={(e) => setScopes(e.target.checked ? [...scopes, s] : scopes.filter((x) => x !== s))}
            />
            <code>{s}</code> <span className="muted small">— {SCOPE_HELP[s]}</span>
          </label>
        ))}
        {scopes.length === 0 ? <p className="small warn-text">Choose at least one scope.</p> : null}
      </fieldset>
      <p className="muted small field-wide">No scope grants administrative permissions; the key determines the organization.</p>
      <div className="form-actions field-wide">
        <button type="submit" className="btn btn-primary" disabled={create.isPending || scopes.length === 0}>
          {create.isPending ? "Creating…" : "Create API key"}
        </button>
      </div>
      {create.isError ? (
        <div className="field-wide">
          <ErrorNotice error={create.error} title="The key was not created" />
        </div>
      ) : null}
    </form>
  );
}

function KeyRow({ apiKey, onRotated }: { apiKey: ApiKey; onRotated: (r: Revealed) => void }) {
  const qc = useQueryClient();
  const rotate = useMutation({
    mutationFn: () => api.post<ApiKeyCreated>(`/api/v1/api-keys/${seg(apiKey.id)}/rotate`),
    onSuccess: (res) => {
      void qc.invalidateQueries({ queryKey: qk.apiKeys });
      onRotated({
        title: `API key “${res.key.name}” rotated — the old key stays valid for 24 hours`,
        secret: res.secret,
        prefix: res.key.prefix,
      });
    },
  });
  const revoke = useMutation({
    mutationFn: () => api.del(`/api/v1/api-keys/${seg(apiKey.id)}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.apiKeys }),
  });
  const error = rotate.error ?? revoke.error;
  return (
    <tr className={apiKey.active ? undefined : "row-inactive"}>
      <td>
        <strong>{apiKey.name}</strong>
        {apiKey.rotated_from ? <div className="muted small">rotated from an earlier key</div> : null}
      </td>
      <td className="mono">{apiKey.prefix}</td>
      <td>
        {apiKey.scopes.map((s) => (
          <code key={s} className="chip">
            {s}
          </code>
        ))}
      </td>
      <td>
        <DateTime value={apiKey.created_at} />
      </td>
      <td>
        <DateTime value={apiKey.expires_at} />
      </td>
      <td>
        <Relative value={apiKey.last_used_at} />
      </td>
      <td>
        {apiKey.active ? (
          <span className="badge test-ok">Active</span>
        ) : apiKey.revoked_at ? (
          <span className="badge test-error" title={apiKey.revoked_reason ?? undefined}>
            Revoked
          </span>
        ) : (
          <span className="badge neutral">Expired</span>
        )}
        {apiKey.revoked_reason ? <div className="muted small">{apiKey.revoked_reason}</div> : null}
      </td>
      <td>
        {apiKey.active ? (
          <div className="button-row">
            <ConfirmButton
              label="Rotate"
              className="btn btn-small"
              prompt="Issue a new secret? The old one stays valid for 24 h."
              confirmLabel="Yes, rotate"
              onConfirm={() => rotate.mutate()}
              pending={rotate.isPending}
            />
            <ConfirmButton
              label="Revoke"
              prompt="Revoke immediately?"
              onConfirm={() => revoke.mutate()}
              pending={revoke.isPending}
            />
          </div>
        ) : null}
        {error ? <ErrorNotice error={error} /> : null}
      </td>
    </tr>
  );
}

export function ApiKeysSettings() {
  const [revealed, setRevealed] = useState<Revealed | null>(null);
  const keys = useQuery({ queryKey: qk.apiKeys, queryFn: () => api.get<ApiKey[]>("/api/v1/api-keys") });
  return (
    <RequirePermission permission={P.apikeyManage}>
      {revealed ? (
        <SecretOnce title={revealed.title} secret={revealed.secret} onDismiss={() => setRevealed(null)}>
          <p className="small">
            Key prefix <code>{revealed.prefix}</code>. Send it as <code>Authorization: Bearer &lt;secret&gt;</code>.
          </p>
        </SecretOnce>
      ) : null}
      <Panel title="API keys" description="Machine credentials for alert and change ingestion and read access.">
        {keys.isPending ? (
          <Loading />
        ) : keys.isError ? (
          <ErrorNotice error={keys.error} onRetry={() => void keys.refetch()} />
        ) : keys.data.length === 0 ? (
          <EmptyState title="No API keys" />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <caption className="visually-hidden">API keys</caption>
              <thead>
                <tr>
                  <th scope="col">Name</th>
                  <th scope="col">Prefix</th>
                  <th scope="col">Scopes</th>
                  <th scope="col">Created</th>
                  <th scope="col">Expires</th>
                  <th scope="col">Last used</th>
                  <th scope="col">State</th>
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody>
                {keys.data.map((k) => (
                  <KeyRow key={k.id} apiKey={k} onRotated={setRevealed} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
      <Panel title="Create an API key">
        <CreateKey onCreated={setRevealed} />
      </Panel>
    </RequirePermission>
  );
}
