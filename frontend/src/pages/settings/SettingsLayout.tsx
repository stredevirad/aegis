import type { ReactNode } from "react";
import { Navigate, NavLink, Outlet } from "react-router-dom";
import { useCan } from "../../auth/context";
import { P, type Permission } from "../../auth/permissions";
import { Notice } from "../../components/Feedback";
import { PageHeader } from "../../components/Layout";

const SECTIONS: { to: string; label: string; permission: Permission }[] = [
  { to: "organization", label: "Organization", permission: P.orgRead },
  { to: "integrations", label: "Integrations", permission: P.integrationRead },
  { to: "api-keys", label: "API keys", permission: P.apikeyManage },
  { to: "members", label: "Members", permission: P.memberManage },
  { to: "audit", label: "Audit log", permission: P.auditRead },
];

export function SettingsLayout() {
  const can = useCan();
  const visible = SECTIONS.filter((s) => can(s.permission));
  return (
    <div className="page">
      <PageHeader title="Settings" />
      <div className="settings">
        <nav className="subnav" aria-label="Settings sections">
          {visible.map((s) => (
            <NavLink key={s.to} to={s.to}>
              {s.label}
            </NavLink>
          ))}
        </nav>
        <div className="settings-body">
          <Outlet />
        </div>
      </div>
    </div>
  );
}

export function SettingsIndex() {
  const can = useCan();
  const first = SECTIONS.find((s) => can(s.permission));
  if (!first) {
    return (
      <Notice tone="info">
        <p>Your role has no settings to view.</p>
      </Notice>
    );
  }
  return <Navigate to={first.to} replace />;
}

/** Renders children only with the permission; the server enforces it regardless. */
export function RequirePermission({ permission, children }: { permission: Permission; children: ReactNode }) {
  const can = useCan();
  if (!can(permission)) {
    return (
      <Notice tone="warning" title="Not permitted" role="note">
        <p>
          This section needs the <code>{permission}</code> permission.
        </p>
      </Notice>
    );
  }
  return <>{children}</>;
}
