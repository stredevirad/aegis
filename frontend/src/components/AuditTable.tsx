import type { AuditEvent } from "../api/types";
import { shortHash } from "../lib/format";
import { DateTime } from "./Time";

export function AuditTable({ events, caption }: { events: AuditEvent[]; caption: string }) {
  return (
    <div className="table-wrap">
      <table className="table table-compact">
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          <tr>
            <th scope="col" className="num">
              Seq
            </th>
            <th scope="col">Time</th>
            <th scope="col">Actor</th>
            <th scope="col">Action</th>
            <th scope="col">Target</th>
            <th scope="col">Details</th>
            <th scope="col">Request ID</th>
            <th scope="col">Hash</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.seq}>
              <td className="num">{e.seq}</td>
              <td>
                <DateTime value={e.ts} />
              </td>
              <td>
                {e.actor_type}
                {e.actor_id ? <span className="muted small"> {e.actor_id}</span> : null}
              </td>
              <td>
                <code>{e.action}</code>
              </td>
              <td>
                {e.target_type ?? "—"}
                {e.target_id ? <span className="muted small mono"> {e.target_id}</span> : null}
              </td>
              <td>
                {Object.keys(e.details).length ? (
                  <details className="disclosure disclosure-inline">
                    <summary>{Object.keys(e.details).length} field(s)</summary>
                    <pre className="json">{JSON.stringify(e.details, null, 2)}</pre>
                  </details>
                ) : (
                  <span className="muted">—</span>
                )}
              </td>
              <td className="mono small">{e.request_id ?? "—"}</td>
              <td className="mono small" title={e.hash}>
                {shortHash(e.hash, 10)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
