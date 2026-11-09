import type { Alert } from "../../api/types";
import { SeverityBadge } from "../../components/Badge";
import { SafeExternalLink } from "../../components/ExternalLink";
import { KeyValueList } from "../../components/KeyValues";
import { DateTime, Relative } from "../../components/Time";
import { capitalize, shortHash } from "../../lib/format";

/** Alert occurrences of an incident. Labels, annotations and generator URLs are untrusted. */
export function AlertsTable({ alerts }: { alerts: Alert[] }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <caption className="visually-hidden">Alerts</caption>
        <thead>
          <tr>
            <th scope="col">Alert</th>
            <th scope="col">Status</th>
            <th scope="col">Severity</th>
            <th scope="col">Service / environment</th>
            <th scope="col">Starts</th>
            <th scope="col">Ends</th>
            <th scope="col" className="num">
              Received
            </th>
            <th scope="col">Last received</th>
            <th scope="col">Source</th>
          </tr>
        </thead>
        <tbody>
          {alerts.map((a) => (
            <tr key={a.id}>
              <td className="cell-title">
                <div>{a.name}</div>
                <details className="disclosure disclosure-inline">
                  <summary>
                    Labels ({Object.keys(a.labels).length}) and annotations ({Object.keys(a.annotations).length})
                  </summary>
                  <h4 className="subhead">Labels</h4>
                  <KeyValueList data={a.labels} className="kv-compact" />
                  <h4 className="subhead">Annotations</h4>
                  <KeyValueList data={a.annotations} className="kv-compact" />
                  <p className="small muted">
                    Fingerprint <code title={a.fingerprint}>{shortHash(a.fingerprint, 16)}</code> · first
                    received <DateTime value={a.first_received_at} />
                  </p>
                </details>
              </td>
              <td>
                <span className={`badge ${a.status === "firing" ? "alert-firing" : "neutral"}`}>{capitalize(a.status)}</span>
              </td>
              <td>
                <SeverityBadge severity={a.severity} />
              </td>
              <td>
                {a.service ?? "—"}
                {a.environment ? <span className="muted"> / {a.environment}</span> : null}
              </td>
              <td>
                <DateTime value={a.starts_at} />
              </td>
              <td>
                <DateTime value={a.ends_at} />
              </td>
              <td className="num">{a.receive_count}×</td>
              <td>
                <Relative value={a.last_received_at} />
              </td>
              <td>
                <span className="muted small">{a.source}</span>{" "}
                <SafeExternalLink href={a.generator_url}>Generator</SafeExternalLink>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
