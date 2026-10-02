import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { RequireAuth } from "./auth/RequireAuth";
import { ChangesPage } from "./pages/ChangesPage";
import { DashboardPage } from "./pages/DashboardPage";
import { EvidenceExplorerPage } from "./pages/EvidenceExplorerPage";
import { IncidentDetailPage } from "./pages/incident/IncidentDetailPage";
import { IncidentsPage } from "./pages/IncidentsPage";
import { InvestigationPage } from "./pages/investigation/InvestigationPage";
import { LoginPage } from "./pages/LoginPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { ApiKeysSettings } from "./pages/settings/ApiKeysSettings";
import { AuditLogSettings } from "./pages/settings/AuditLogSettings";
import { IntegrationsSettings } from "./pages/settings/IntegrationsSettings";
import { MembersSettings } from "./pages/settings/MembersSettings";
import { OrganizationSettings } from "./pages/settings/OrganizationSettings";
import { SettingsIndex, SettingsLayout } from "./pages/settings/SettingsLayout";

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth />}>
        <Route index element={<DashboardPage />} />
        <Route path="incidents" element={<IncidentsPage />} />
        <Route path="incidents/:incidentId" element={<IncidentDetailPage />} />
        <Route path="investigations/:investigationId" element={<InvestigationPage />} />
        <Route path="investigations/:investigationId/evidence" element={<EvidenceExplorerPage />} />
        <Route path="changes" element={<ChangesPage />} />
        <Route path="settings" element={<SettingsLayout />}>
          <Route index element={<SettingsIndex />} />
          <Route path="integrations" element={<IntegrationsSettings />} />
          <Route path="api-keys" element={<ApiKeysSettings />} />
          <Route path="members" element={<MembersSettings />} />
          <Route path="organization" element={<OrganizationSettings />} />
          <Route path="audit" element={<AuditLogSettings />} />
          <Route path="*" element={<Navigate to="/settings" replace />} />
        </Route>
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}

export function App({ queryClient }: { queryClient: QueryClient }) {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
