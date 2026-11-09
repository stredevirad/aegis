import { useId, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, seg } from "../../api/client";
import { qk } from "../../api/queries";
import { ROLES, type Invitation, type Member, type Role } from "../../api/types";
import { useMe } from "../../auth/context";
import { P } from "../../auth/permissions";
import { ConfirmButton } from "../../components/Controls";
import { EmptyState, ErrorNotice, Loading } from "../../components/Feedback";
import { Panel } from "../../components/Layout";
import { DateTime } from "../../components/Time";
import { capitalize, parseDate } from "../../lib/format";
import { RequirePermission } from "./SettingsLayout";

function MemberRow({ member, isSelf }: { member: Member; isSelf: boolean }) {
  const qc = useQueryClient();
  const selectId = useId();
  const [role, setRole] = useState(member.role);
  const change = useMutation({
    mutationFn: (next: string) => api.patch<Member>(`/api/v1/members/${seg(member.user_id)}`, { role: next }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.members }),
  });
  const remove = useMutation({
    mutationFn: () => api.del(`/api/v1/members/${seg(member.user_id)}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.members }),
  });
  const error = change.error ?? remove.error;
  return (
    <tr>
      <td>
        {member.email ?? "—"}
        {isSelf ? <span className="badge neutral">you</span> : null}
        {member.display_name ? <div className="muted small">{member.display_name}</div> : null}
      </td>
      <td>
        <form
          className="inline-form-row"
          onSubmit={(e) => {
            e.preventDefault();
            if (role !== member.role) change.mutate(role);
          }}
        >
          <label htmlFor={selectId} className="visually-hidden">
            Role of {member.email ?? member.user_id}
          </label>
          <select id={selectId} value={role} onChange={(e) => setRole(e.target.value)}>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {capitalize(r)}
              </option>
            ))}
          </select>
          <button type="submit" className="btn btn-small" disabled={role === member.role || change.isPending}>
            Change role
          </button>
        </form>
      </td>
      <td>
        <DateTime value={member.created_at} />
      </td>
      <td>
        <ConfirmButton
          label="Remove"
          prompt="Remove this member? Their sessions end and the API keys they created are revoked."
          onConfirm={() => remove.mutate()}
          pending={remove.isPending}
        />
        {error ? <ErrorNotice error={error} /> : null}
      </td>
    </tr>
  );
}

function invitationState(inv: Invitation): string {
  if (inv.accepted_at) return "Accepted";
  if (inv.revoked_at) return "Revoked";
  const expires = parseDate(inv.expires_at);
  if (expires && expires.getTime() < Date.now()) return "Expired";
  return "Open";
}

function InvitationRow({ invitation }: { invitation: Invitation }) {
  const qc = useQueryClient();
  const revoke = useMutation({
    mutationFn: () => api.del(`/api/v1/invitations/${seg(invitation.id)}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.invitations }),
  });
  const state = invitationState(invitation);
  return (
    <tr className={state === "Open" ? undefined : "row-inactive"}>
      <td>{invitation.email}</td>
      <td>{capitalize(invitation.role)}</td>
      <td>
        <span className={`badge ${state === "Open" ? "test-ok" : "neutral"}`}>{state}</span>
      </td>
      <td>
        <DateTime value={invitation.created_at} />
      </td>
      <td>
        <DateTime value={invitation.expires_at} />
      </td>
      <td>
        {state === "Open" ? (
          <ConfirmButton label="Revoke" onConfirm={() => revoke.mutate()} pending={revoke.isPending} />
        ) : null}
        {revoke.isError ? <ErrorNotice error={revoke.error} /> : null}
      </td>
    </tr>
  );
}

function InviteForm() {
  const qc = useQueryClient();
  const ids = { email: useId(), role: useId(), days: useId() };
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const [days, setDays] = useState(7);
  const invite = useMutation({
    mutationFn: () =>
      api.post<Invitation>("/api/v1/invitations", { email: email.trim(), role, expires_in_days: days }),
    onSuccess: () => {
      setEmail("");
      void qc.invalidateQueries({ queryKey: qk.invitations });
    },
  });
  function submit(e: FormEvent) {
    e.preventDefault();
    invite.mutate();
  }
  return (
    <form className="form-grid" onSubmit={submit} aria-label="Invite a member">
      <div className="field">
        <label htmlFor={ids.email}>Email address</label>
        <input id={ids.email} type="email" required maxLength={320} value={email} onChange={(e) => setEmail(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor={ids.role}>Role</label>
        <select id={ids.role} value={role} onChange={(e) => setRole(e.target.value as Role)}>
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {capitalize(r)}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor={ids.days}>Expires after (days, 1–30)</label>
        <input id={ids.days} type="number" min={1} max={30} required value={days} onChange={(e) => setDays(Number(e.target.value))} />
      </div>
      <div className="form-actions field-wide">
        <button type="submit" className="btn btn-primary" disabled={invite.isPending}>
          {invite.isPending ? "Inviting…" : "Invite"}
        </button>
      </div>
      <p className="muted small field-wide">
        The invitation is accepted when that address signs in with a verified email.
      </p>
      {invite.isError ? (
        <div className="field-wide">
          <ErrorNotice error={invite.error} title="The invitation was not created" />
        </div>
      ) : null}
    </form>
  );
}

export function MembersSettings() {
  const me = useMe();
  const members = useQuery({ queryKey: qk.members, queryFn: () => api.get<Member[]>("/api/v1/members") });
  const invitations = useQuery({ queryKey: qk.invitations, queryFn: () => api.get<Invitation[]>("/api/v1/invitations") });
  return (
    <RequirePermission permission={P.memberManage}>
      <Panel
        title="Members"
        description="Roles: admin (everything), investigator (starts investigations), reviewer (lifecycle, audit, approvals), viewer (read-only). An organization always keeps at least one admin."
      >
        {members.isPending ? (
          <Loading />
        ) : members.isError ? (
          <ErrorNotice error={members.error} onRetry={() => void members.refetch()} />
        ) : members.data.length === 0 ? (
          <EmptyState title="No members" />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <caption className="visually-hidden">Members</caption>
              <thead>
                <tr>
                  <th scope="col">Member</th>
                  <th scope="col">Role</th>
                  <th scope="col">Member since</th>
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody>
                {members.data.map((m) => (
                  <MemberRow key={`${m.user_id}:${m.role}`} member={m} isSelf={m.user_id === me.user_id} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
      <Panel title="Invitations">
        <InviteForm />
        {invitations.isPending ? (
          <Loading />
        ) : invitations.isError ? (
          <ErrorNotice error={invitations.error} />
        ) : invitations.data.length === 0 ? (
          <p className="muted">No invitations.</p>
        ) : (
          <div className="table-wrap">
            <table className="table table-compact">
              <caption className="visually-hidden">Invitations</caption>
              <thead>
                <tr>
                  <th scope="col">Email</th>
                  <th scope="col">Role</th>
                  <th scope="col">State</th>
                  <th scope="col">Created</th>
                  <th scope="col">Expires</th>
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody>
                {invitations.data.map((i) => (
                  <InvitationRow key={i.id} invitation={i} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </RequirePermission>
  );
}
