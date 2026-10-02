// Permission names from GET /api/v1/me (backend aegis.auth.rbac.Permission). The UI hides or
// disables what the user cannot do; the server enforces RBAC regardless.
export const P = {
  incidentRead: "incident.read",
  incidentWrite: "incident.write",
  investigationRead: "investigation.read",
  investigationStart: "investigation.start",
  investigationCancel: "investigation.cancel",
  evidenceRead: "evidence.read",
  changeRead: "change.read",
  changeWrite: "change.write",
  auditRead: "audit.read",
  orgRead: "org.read",
  orgManage: "org.manage",
  memberManage: "member.manage",
  apikeyManage: "apikey.manage",
  integrationRead: "integration.read",
  integrationManage: "integration.manage",
} as const;

export type Permission = (typeof P)[keyof typeof P];
