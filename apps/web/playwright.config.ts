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

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  fullyParallel: true,
  reporter: [["list"]],
  outputDir: "../../.persona-qa/e2e/test-results",
  use: { baseURL: external ?? `http://localhost:${PORT}`, trace: "retain-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "mobile", use: { ...devices["Desktop Chrome"], viewport: { width: 390, height: 844 }, hasTouch: true } },
  ],
  webServer: external
    ? undefined
    : [
        ...(useStub
          ? [{ command: "node e2e/stub-agent.mjs", url: `http://127.0.0.1:${STUB_PORT}/health`, env: { PORT: String(STUB_PORT) }, reuseExistingServer: !process.env.CI }]
          : []),
        {
          command: `npm run build && npx next start -p ${PORT}`,
          port: PORT,
          env: { PERSONA_AGENT_BASE_URL: agentUrl },
          reuseExistingServer: !process.env.CI,
          timeout: 240_000,
        },
      ],
});
