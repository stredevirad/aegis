import { useId } from "react";
import type { CredentialIn, CredentialType } from "../../api/types";

export const EMPTY_CREDENTIAL: CredentialIn = { type: "none" };

/** Write-only credential inputs. Values are never read back from the API or displayed again. */
export function CredentialFields({
  value,
  onChange,
  legend = "Credential",
}: {
  value: CredentialIn;
  onChange: (next: CredentialIn) => void;
  legend?: string;
}) {
  const ids = { type: useId(), token: useId(), user: useId(), pass: useId(), hname: useId(), hval: useId() };
  const setType = (type: CredentialType) => onChange({ type });
  return (
    <fieldset className="credential">
      <legend>{legend}</legend>
      <div className="field">
        <label htmlFor={ids.type}>Type</label>
        <select id={ids.type} value={value.type} onChange={(e) => setType(e.target.value as CredentialType)}>
          <option value="none">None</option>
          <option value="bearer">Bearer token</option>
          <option value="basic">Basic (username and password)</option>
          <option value="header">Custom header</option>
        </select>
      </div>
      {value.type === "bearer" ? (
        <div className="field">
          <label htmlFor={ids.token}>Token</label>
          <input
            id={ids.token}
            type="password"
            autoComplete="new-password"
            required
            maxLength={4096}
            value={value.token ?? ""}
            onChange={(e) => onChange({ ...value, token: e.target.value })}
          />
        </div>
      ) : null}
      {value.type === "basic" ? (
        <>
          <div className="field">
            <label htmlFor={ids.user}>Username</label>
            <input
              id={ids.user}
              autoComplete="off"
              required
              maxLength={256}
              value={value.username ?? ""}
              onChange={(e) => onChange({ ...value, username: e.target.value })}
            />
          </div>
          <div className="field">
            <label htmlFor={ids.pass}>Password</label>
            <input
              id={ids.pass}
              type="password"
              autoComplete="new-password"
              required
              maxLength={1024}
              value={value.password ?? ""}
              onChange={(e) => onChange({ ...value, password: e.target.value })}
            />
          </div>
        </>
      ) : null}
      {value.type === "header" ? (
        <>
          <div className="field">
            <label htmlFor={ids.hname}>Header name</label>
            <input
              id={ids.hname}
              autoComplete="off"
              required
              pattern="[A-Za-z][A-Za-z0-9\-]{0,60}"
              title="Letters, digits and hyphens; starts with a letter"
              value={value.header_name ?? ""}
              onChange={(e) => onChange({ ...value, header_name: e.target.value })}
            />
          </div>
          <div className="field">
            <label htmlFor={ids.hval}>Header value</label>
            <input
              id={ids.hval}
              type="password"
              autoComplete="new-password"
              required
              maxLength={4096}
              value={value.header_value ?? ""}
              onChange={(e) => onChange({ ...value, header_value: e.target.value })}
            />
          </div>
        </>
      ) : null}
      <p className="muted small">Credentials are encrypted by the server and never shown again.</p>
    </fieldset>
  );
}
