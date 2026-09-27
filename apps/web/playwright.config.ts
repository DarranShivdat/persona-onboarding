import { defineConfig, devices } from "@playwright/test";

// The app is served from a production build on :3100 unless PERSONA_WEB_URL points at an
// already-running instance. `?state=` specs use the mock driver (FE-001); live specs (FE-002)
// hit the real ApiSessionDriver + /api/session proxy backed by the in-memory stub agent
// (e2e/stub-agent.mjs on :3199), or a real agent via PERSONA_E2E_AGENT_URL.
const PORT = 3100;
const STUB_PORT = 3199;
const external = process.env.PERSONA_WEB_URL;
const useStub = !process.env.PERSONA_E2E_AGENT_URL;
const agentUrl = process.env.PERSONA_E2E_AGENT_URL ?? `http://127.0.0.1:${STUB_PORT}`;
process.env.PERSONA_E2E_AGENT_URL = agentUrl; // read by specs (seeding) — test-only
// Mock Google OAuth double lives in the stub agent (FE-003); these are fake test-only values.
const INTERNAL_SECRET = "e2e-internal-secret";
const oauthEnv = {
  GOOGLE_OAUTH_CLIENT_ID: "e2e-client.apps.googleusercontent.com",
  GOOGLE_OAUTH_CLIENT_SECRET: "e2e-not-a-secret",
  GOOGLE_OAUTH_AUTH_URL: `${agentUrl}/__oauth/authorize`,
  GOOGLE_OAUTH_TOKEN_URL: `${agentUrl}/__oauth/token`,
  GOOGLE_OAUTH_ISSUER: agentUrl,
  PERSONA_INTERNAL_SECRET: INTERNAL_SECRET,
};

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  fullyParallel: true,
  reporter: [["list"]],
  outputDir: "../../.persona-qa/e2e/test-results",
  use: {
    baseURL: external ?? `http://localhost:${PORT}`,
    trace: "retain-on-failure",
    // FE-004 call specs connect two local peers: keep host candidates plain (no mDNS) and let
    // the fake tone play without a gesture.
    launchOptions: { args: ["--disable-features=WebRtcHideLocalIpsWithMdns", "--autoplay-policy=no-user-gesture-required"] },
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "mobile", use: { ...devices["Desktop Chrome"], viewport: { width: 390, height: 844 }, hasTouch: true } },
  ],
  webServer: external
    ? undefined
    : [
        ...(useStub
          ? [{ command: "node e2e/stub-agent.mjs", url: `http://127.0.0.1:${STUB_PORT}/health`, env: { PORT: String(STUB_PORT), PERSONA_INTERNAL_SECRET: INTERNAL_SECRET }, reuseExistingServer: !process.env.CI }]
          : []),
        {
          command: `npm run build && npx next start -p ${PORT}`,
          port: PORT,
          // Host-only ICE: e2e never reaches a public STUN server.
          env: { PERSONA_AGENT_BASE_URL: agentUrl, NEXT_PUBLIC_PERSONA_ICE_URLS: "none", ...(useStub ? oauthEnv : {}) },
          reuseExistingServer: !process.env.CI,
          timeout: 240_000,
        },
      ],
});
