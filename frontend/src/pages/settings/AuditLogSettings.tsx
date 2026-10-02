import { useId, useMemo, useState, type FormEvent } from "react";
import { useInfiniteQuery, useMutation } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api, type QueryParams } from "../../api/client";
import { qk } from "../../api/queries";
import type { AuditEvent, AuditVerify, Page } from "../../api/types";
import { P } from "../../auth/permissions";
import { AuditTable } from "../../components/AuditTable";
import { LoadMore } from "../../components/Controls";
import { EmptyState, ErrorNotice, Loading, Notice } from "../../components/Feedback";
import { Panel } from "../../components/Layout";
import { RequirePermission } from "./SettingsLayout";

interface Filters {
  action: string;
  target_type: string;
  target_id: string;
}

function FilterForm({ initial, onApply }: { initial: Filters; onApply: (f: Filters) => void }) {
  const [f, setF] = useState(initial);
  const ids = { action: useId(), type: useId(), id: useId() };
  function submit(e: FormEvent) {
    e.preventDefault();
    onApply({ action: f.action.trim(), target_type: f.target_type.trim(), target_id: f.target_id.trim() });
  }
  return (
    <form className="filters" onSubmit={submit} aria-label="Audit filters">
      <div className="field">
        <label htmlFor={ids.action}>Action</label>
        <input id={ids.action} maxLength={100} placeholder="e.g. incident.updated" value={f.action} onChange={(e) => setF({ ...f, action: e.target.value })} />
      </div>
      <div className="field">
        <label htmlFor={ids.type}>Target type</label>
        <input id={ids.type} maxLength={100} placeholder="e.g. incident" value={f.target_type} onChange={(e) => setF({ ...f, target_type: e.target.value })} />
      </div>
      <div className="field">
        <label htmlFor={ids.id}>Target ID</label>
        <input id={ids.id} maxLength={200} value={f.target_id} onChange={(e) => setF({ ...f, target_id: e.target.value })} />
      </div>
      <div className="filter-actions">
        <button type="submit" className="btn btn-primary">
          Apply filters
        </button>
        <button
          type="button"
          className="btn"
          onClick={() => {
            const empty = { action: "", target_type: "", target_id: "" };
            setF(empty);
            onApply(empty);
          }}
        >
          Reset
        </button>
      </div>
    </form>
  );
}

function VerifyChain() {
  const verify = useMutation({ mutationFn: () => api.get<AuditVerify>("/api/v1/audit/verify") });
  return (
    <div>
      <button type="button" className="btn" onClick={() => verify.mutate()} disabled={verify.isPending}>
        {verify.isPending ? "Verifying…" : "Verify chain"}
      </button>
      {verify.isError ? <ErrorNotice error={verify.error} /> : null}
      {verify.data ? (
        <Notice
          tone={verify.data.intact ? "success" : "error"}
          title={verify.data.intact ? "Hash chain intact" : `Hash chain broken at event ${verify.data.first_broken_seq ?? "?"}`}
          role="status"
        >
          <p>
            {verify.data.events} event(s) checked. Head hash{" "}
            <code className="selectable">{verify.data.head_hash ?? "—"}</code>
          </p>
          <p className="small">{verify.data.note}</p>
        </Notice>
      ) : null}
    </div>
  );
}

export function AuditLogSettings() {
  const [searchParams, setSearchParams] = useSearchParams();
  const filters: Filters = useMemo(
    () => ({
      action: searchParams.get("action") ?? "",
      target_type: searchParams.get("target_type") ?? "",
      target_id: searchParams.get("target_id") ?? "",
    }),
    [searchParams],
  );
  const params: QueryParams = useMemo(() => ({ ...filters, limit: 50 }), [filters]);
  const q = useInfiniteQuery({
    queryKey: qk.audit(params),
    queryFn: ({ pageParam, signal }) =>
      api.get<Page<AuditEvent>>("/api/v1/audit", { ...params, before_seq: pageParam }, { signal }),
    initialPageParam: null as string | null,
    // The audit log pages by sequence number: next_cursor is the before_seq of the next page.
    getNextPageParam: (last) => last.next_cursor,
  });
  const items = q.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <RequirePermission permission={P.auditRead}>
      <Panel
        title="Audit log"
        description="Append-only, hash-chained record of security-relevant actions in this organization."
        actions={<VerifyChain />}
      >
        <details className="disclosure filters-disclosure" open>
          <summary>Filters</summary>
          <FilterForm
            key={searchParams.toString()}
            initial={filters}
            onApply={(f) => {
              const sp = new URLSearchParams();
              for (const [k, v] of Object.entries(f)) if (v) sp.set(k, v);
              setSearchParams(sp);
            }}
          />
        </details>
        {q.isPending ? (
          <Loading />
        ) : q.isError ? (
          <ErrorNotice error={q.error} onRetry={() => void q.refetch()} />
        ) : items.length === 0 ? (
          <EmptyState title="No audit events match" />
        ) : (
          <>
            <AuditTable events={items} caption="Audit events, newest first" />
            <LoadMore
              hasNextPage={q.hasNextPage}
              isFetchingNextPage={q.isFetchingNextPage}
              onLoadMore={() => void q.fetchNextPage()}
              shown={items.length}
              noun="events"
            />
          </>
        )}
      </Panel>
    </RequirePermission>
  );
}
