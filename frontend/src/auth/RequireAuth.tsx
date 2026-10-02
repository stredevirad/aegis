import { useQuery } from "@tanstack/react-query";
import { Navigate, useLocation } from "react-router-dom";
import { api, isUnauthenticated, loginPath } from "../api/client";
import { qk } from "../api/queries";
import type { Me } from "../api/types";
import { ErrorNotice, Loading } from "../components/Feedback";
import { AppLayout } from "../components/Layout";
import { MeProvider } from "./context";

/** Loads the session's identity (GET /api/v1/me); unauthenticated users go to /login. */
export function RequireAuth() {
  const location = useLocation();
  const me = useQuery({
    queryKey: qk.me,
    queryFn: () => api.get<Me>("/api/v1/me", undefined, { redirectOn401: false }),
    staleTime: 60_000,
    retry: false,
  });
  if (me.isPending) {
    return (
      <div className="boot">
        <Loading label="Loading your session…" />
      </div>
    );
  }
  if (me.isError) {
    if (isUnauthenticated(me.error)) {
      return <Navigate to={loginPath(location.pathname + location.search)} replace />;
    }
    return (
      <div className="boot">
        <ErrorNotice error={me.error} title="Aegis is unavailable" onRetry={() => void me.refetch()} />
      </div>
    );
  }
  return (
    <MeProvider me={me.data}>
      <AppLayout />
    </MeProvider>
  );
}
