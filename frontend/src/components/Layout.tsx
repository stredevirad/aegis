import { useId, useState, type ReactNode } from "react";
import { useMutation } from "@tanstack/react-query";
import { NavLink, Outlet } from "react-router-dom";
import { api } from "../api/client";
import { useCan, useMe } from "../auth/context";
import { P } from "../auth/permissions";
import { ErrorNotice } from "./Feedback";

export function PageHeader({
  title,
  kicker,
  actions,
  children,
}: {
  title: ReactNode;
  kicker?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div className="page-header-main">
        {kicker ? <div className="page-kicker">{kicker}</div> : null}
        <h1>{title}</h1>
        {children ? <div className="page-header-meta">{children}</div> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  );
}

export function Panel({
  title,
  actions,
  children,
  className,
  id,
  headingLevel = 2,
  description,
}: {
  title: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  id?: string;
  headingLevel?: 2 | 3;
  description?: ReactNode;
}) {
  const headingId = useId();
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <section className={`panel ${className ?? ""}`} aria-labelledby={headingId} id={id}>
      <div className="panel-head">
        <Heading id={headingId} className="panel-title">
          {title}
        </Heading>
        {actions ? <div className="panel-actions">{actions}</div> : null}
      </div>
      {description ? <div className="panel-description">{description}</div> : null}
      <div className="panel-body">{children}</div>
    </section>
  );
}

function OrgSwitcher() {
  const me = useMe();
  const [target, setTarget] = useState(me.org?.slug ?? "");
  const selectId = useId();
  const switchOrg = useMutation({
    mutationFn: (org: string) => api.post("/auth/switch", { org }),
    // A full reload discards every cached response of the previous organization.
    onSuccess: () => window.location.assign("/"),
  });
  if (me.memberships.length <= 1) return null;
  return (
    <form
      className="org-switch"
      onSubmit={(e) => {
        e.preventDefault();
        if (target && target !== me.org?.slug) switchOrg.mutate(target);
      }}
    >
      <label htmlFor={selectId} className="visually-hidden">
        Organization
      </label>
      <select id={selectId} value={target} onChange={(e) => setTarget(e.target.value)}>
        {me.memberships.map((m) => (
          <option key={m.id} value={m.slug}>
            {m.name} ({m.role})
          </option>
        ))}
      </select>
      <button
        type="submit"
        className="btn btn-small"
        disabled={switchOrg.isPending || !target || target === me.org?.slug}
      >
        Switch
      </button>
      {switchOrg.isError ? <ErrorNotice error={switchOrg.error} /> : null}
    </form>
  );
}

function LogoutButton() {
  const logout = useMutation({
    mutationFn: () => api.post("/auth/logout", undefined, { redirectOn401: false }),
    // Whatever the outcome, leave: a reload drops all cached data.
    onSettled: () => window.location.assign("/login"),
  });
  return (
    <button type="button" className="btn btn-small btn-ghost" onClick={() => logout.mutate()} disabled={logout.isPending}>
      Log out
    </button>
  );
}

export function AppLayout() {
  const me = useMe();
  const can = useCan();
  const showSettings =
    can(P.integrationRead) ||
    can(P.orgRead) ||
    can(P.memberManage) ||
    can(P.apikeyManage) ||
    can(P.auditRead);
  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <header className="topbar">
        <div className="topbar-inner">
          <NavLink to="/" className="brand" end>
            <svg viewBox="0 0 32 32" width="20" height="20" aria-hidden="true" focusable="false">
              <path d="M16 2 4 6v9c0 7.5 5.1 13.2 12 15 6.9-1.8 12-7.5 12-15V6L16 2z" className="brand-mark" />
            </svg>
            <span>Aegis</span>
          </NavLink>
          <nav aria-label="Primary" className="nav">
            <NavLink to="/" end>
              Dashboard
            </NavLink>
            <NavLink to="/incidents">Incidents</NavLink>
            {can(P.changeRead) ? <NavLink to="/changes">Changes</NavLink> : null}
            {showSettings ? <NavLink to="/settings">Settings</NavLink> : null}
          </nav>
          <div className="account">
            <span className="account-org">
              <span className="account-org-name">{me.org?.name ?? "—"}</span>
              <span className="badge neutral">{me.role ?? "—"}</span>
            </span>
            <OrgSwitcher />
            <span className="account-email muted" title="Signed in as">
              {me.email ?? me.display_name ?? ""}
            </span>
            <LogoutButton />
          </div>
        </div>
      </header>
      <main id="main" className="main" tabIndex={-1}>
        <Outlet />
      </main>
    </div>
  );
}
