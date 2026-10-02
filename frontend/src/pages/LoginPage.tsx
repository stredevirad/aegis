import { useId, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { qk } from "../api/queries";
import type { AuthConfig, SessionOut } from "../api/types";
import { ErrorNotice, Loading, Notice } from "../components/Feedback";
import { sanitizeReturnTo } from "../lib/misc";

// Errors the OIDC callback can redirect with. Unknown codes are not reflected into the page.
const LOGIN_ERRORS: Record<string, string> = {
  idp_error: "The identity provider reported an error. Try again or contact your administrator.",
  login_failed:
    "Sign-in could not be completed. Your account may not be a member of an organization yet; ask an administrator for an invitation.",
};

export function LoginPage() {
  const [params] = useSearchParams();
  const returnTo = sanitizeReturnTo(params.get("return_to"));
  const errorCode = params.get("error");
  const errorText = errorCode ? (LOGIN_ERRORS[errorCode] ?? "Sign-in failed.") : null;
  const navigate = useNavigate();
  const qc = useQueryClient();
  const emailId = useId();
  const [email, setEmail] = useState("");

  const config = useQuery({
    queryKey: qk.authConfig,
    queryFn: () => api.get<AuthConfig>("/auth/config", undefined, { redirectOn401: false }),
    staleTime: Infinity,
  });

  const devLogin = useMutation({
    mutationFn: (address: string) =>
      api.post<SessionOut>("/auth/dev/login", { email: address }, { redirectOn401: false }),
    onSuccess: () => {
      qc.clear();
      navigate(returnTo, { replace: true });
    },
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    const address = email.trim();
    if (address) devLogin.mutate(address);
  }

  return (
    <main className="login" id="main">
      <div className="login-card">
        <div className="login-brand">
          <svg viewBox="0 0 32 32" width="28" height="28" aria-hidden="true" focusable="false">
            <path d="M16 2 4 6v9c0 7.5 5.1 13.2 12 15 6.9-1.8 12-7.5 12-15V6L16 2z" className="brand-mark" />
          </svg>
          <h1>Aegis</h1>
        </div>
        <p className="muted">Evidence-grounded incident investigation</p>
        {errorText ? (
          <Notice tone="error" role="alert">
            <p>{errorText}</p>
          </Notice>
        ) : null}
        {config.isPending ? (
          <Loading label="Loading sign-in options…" />
        ) : config.isError ? (
          <ErrorNotice error={config.error} title="Sign-in options are unavailable" onRetry={() => void config.refetch()} />
        ) : (
          <>
            {config.data.mode === "oidc" ? (
              <button
                type="button"
                className="btn btn-primary btn-block"
                onClick={() => window.location.assign(`/auth/login?return_to=${encodeURIComponent(returnTo)}`)}
              >
                Sign in
              </button>
            ) : null}
            {config.data.dev_login ? (
              <form className="dev-login" onSubmit={submit} aria-labelledby="dev-login-title">
                <h2 id="dev-login-title" className="dev-login-title">
                  Development login
                </h2>
                <p className="small warn-text">
                  Development only: signs in as the given address without an identity provider. It is
                  disabled in staging and production.
                </p>
                <label htmlFor={emailId}>Email address</label>
                <input
                  id={emailId}
                  type="email"
                  autoComplete="username"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
                <button type="submit" className="btn btn-primary btn-block" disabled={devLogin.isPending}>
                  {devLogin.isPending ? "Signing in…" : "Sign in (development)"}
                </button>
                {devLogin.isError ? <ErrorNotice error={devLogin.error} title="Sign-in failed" /> : null}
              </form>
            ) : null}
            {config.data.mode !== "oidc" && !config.data.dev_login ? (
              <Notice tone="warning">
                <p>No sign-in method is configured for this deployment.</p>
              </Notice>
            ) : null}
          </>
        )}
      </div>
    </main>
  );
}
