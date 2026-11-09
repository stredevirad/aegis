import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The development API (backend/scripts/dev-stack.sh). Only http://localhost:5173 and
// http://127.0.0.1:5173 are in its AEGIS_ALLOWED_ORIGINS, so the ports are strict: a silent
// fallback to 5174 would make every state-changing request fail the CSRF origin check.
const API = "http://127.0.0.1:8000";
// Bound to IPv4 loopback explicitly: "localhost" can resolve to ::1 only on Windows, and the E2E
// suite and the API's allowed origins use http://127.0.0.1:5173.

const proxy = {
  "/api": { target: API },
  "/auth": { target: API },
};

// Same headers as frontend/nginx.conf, so `vite preview` exercises the production CSP.
const securityHeaders = {
  "Content-Security-Policy":
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; " +
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; " +
    "object-src 'none'",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
  "X-Frame-Options": "DENY",
};

export default defineConfig({
  plugins: [react()],
  server: { host: "127.0.0.1", port: 5173, strictPort: true, proxy },
  preview: { host: "127.0.0.1", port: 5173, strictPort: true, proxy, headers: securityHeaders },
  build: {
    // Nothing becomes a data: URI (CSP allows data: only for images).
    assetsInlineLimit: 0,
    modulePreload: { polyfill: false },
    sourcemap: false,
  },
  test: {
    // Worker threads: forked workers failed to start within vitest's timeout on the Windows
    // development machine; threads behave the same everywhere.
    pool: "threads",
    // Browser tests (e2e/) run under Playwright, not Vitest.
    include: ["src/**/*.test.{ts,tsx}"],
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    restoreMocks: true,
    unstubGlobals: true,
  },
});
