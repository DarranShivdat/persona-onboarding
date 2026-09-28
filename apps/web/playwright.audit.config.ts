import { defineConfig, devices } from "@playwright/test";

// AUDIT-001: button audit — every control on every screen, desktop + mobile.
//   LOCAL (default, `npm run qa:audit`): production build on :3420 + stub agent on :3219. Offline.
//   LIVE: PERSONA_AUDIT_URL=https://persona-onboarding-darran.vercel.app (no local build/stub).
// Screenshots + result rows go to .persona-qa/audit/ ; with PERSONA_AUDIT_DOCS=1 they are also
// written to docs/qa/button-audit/ and docs/qa/button-audit.md is regenerated (see e2e/audit/report.ts).
const liveEnv = process.env.PERSONA_AUDIT_URL;
if (liveEnv !== undefined && !liveEnv.trim()) throw new Error("PERSONA_AUDIT_URL is set but empty");
const live = liveEnv?.trim().replace(/\/+$/, "");
const PORT = 3420;
const STUB_PORT = 3219;
const agentUrl = `http://127.0.0.1:${STUB_PORT}`;
process.env.PERSONA_AUDIT_TARGET = live ? "live" : "local";
if (!live) process.env.PERSONA_E2E_AGENT_URL = agentUrl; // read by e2e/live.ts + e2e/call.ts (seeding, bot)
const INTERNAL_SECRET = "e2e-internal-secret";
// Fake test-only values: the mock Google double lives in the stub agent (e2e/stub-agent.mjs).
const oauthEnv = {
  GOOGLE_OAUTH_CLIENT_ID: "e2e-client.apps.googleusercontent.com",
  GOOGLE_OAUTH_CLIENT_SECRET: "e2e-not-a-secret",
  GOOGLE_OAUTH_AUTH_URL: `${agentUrl}/__oauth/authorize`,
  GOOGLE_OAUTH_TOKEN_URL: `${agentUrl}/__oauth/token`,
  GOOGLE_OAUTH_ISSUER: agentUrl,
  PERSONA_INTERNAL_SECRET: INTERNAL_SECRET,
};
const args = [
  "--disable-features=WebRtcHideLocalIpsWithMdns",
  "--autoplay-policy=no-user-gesture-required",
  "--use-fake-ui-for-media-stream",
  "--use-fake-device-for-media-stream",
];

export default defineConfig({
  testDir: "./e2e/audit",
  testMatch: "**/*.audit.ts", // not *.spec.ts, so the qa:e2e config never picks these up
  timeout: live ? 180_000 : 60_000,
  fullyParallel: !live,
  workers: live ? 1 : undefined, // LIVE: one client, paced (Vercel bot protection trips on bursts)
  reporter: [["list"]],
  outputDir: "../../.persona-qa/audit/test-results",
  globalSetup: "./e2e/audit/setup.ts",
  globalTeardown: "./e2e/audit/teardown.ts",
  use: {
    baseURL: live ?? `http://localhost:${PORT}`,
    trace: "retain-on-failure",
    permissions: ["microphone"],
    launchOptions: { args },
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 } } },
    // iPhone 13 metrics + touch + UA, on Chromium (fake media flags are Chromium-only).
    { name: "mobile", use: { ...devices["iPhone 13"], browserName: "chromium" } },
  ],
  webServer: live
    ? undefined
    : [
        { command: "node e2e/stub-agent.mjs", url: `${agentUrl}/health`, env: { PORT: String(STUB_PORT), PERSONA_INTERNAL_SECRET: INTERNAL_SECRET }, reuseExistingServer: !process.env.CI },
        {
          command: `npm run build && npx next start -p ${PORT}`,
          port: PORT,
          env: { PERSONA_AGENT_BASE_URL: agentUrl, NEXT_PUBLIC_PERSONA_ICE_URLS: "none", ...oauthEnv },
          reuseExistingServer: !process.env.CI,
          timeout: 240_000,
        },
      ],
});
