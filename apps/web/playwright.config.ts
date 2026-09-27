import { defineConfig, devices } from "@playwright/test";

// Mock-driver e2e (FE-001). The app is served from a production build on :3100 unless
// PERSONA_WEB_URL points at an already-running instance.
const PORT = 3100;
const external = process.env.PERSONA_WEB_URL;

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
    : { command: `npm run build && npx next start -p ${PORT}`, port: PORT, reuseExistingServer: !process.env.CI, timeout: 240_000 },
});
