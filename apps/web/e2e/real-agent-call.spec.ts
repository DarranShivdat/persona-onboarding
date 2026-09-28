import { expect, test } from "@playwright/test";

// VOICE-005: a browser call through the real agent API (not the stub): Chromium's fake mic
// -> real offer -> /api/session/ice + /api/session/call -> the agent hosts the Pipecat
// pipeline (SmallWebRTC, fake vendors) and answers -> connected -> the brain's opening line
// is captioned -> End releases the lease and the chat resumes. Skipped unless
// PERSONA_E2E_REAL_AGENT_URL is set (scripts/local-call-smoke.sh brings the stack up).
const REAL_AGENT = process.env.PERSONA_E2E_REAL_AGENT_URL;

test.use({
  permissions: ["microphone"],
  launchOptions: {
    args: [
      "--disable-features=WebRtcHideLocalIpsWithMdns",
      "--autoplay-policy=no-user-gesture-required",
      "--use-fake-device-for-media-stream",
      "--use-fake-ui-for-media-stream",
    ],
  },
});

test("real agent: call connects, opening is captioned, hangup resumes in chat", async ({ page }) => {
  test.skip(!REAL_AGENT, "set PERSONA_E2E_REAL_AGENT_URL (scripts/local-call-smoke.sh)");
  test.setTimeout(90_000);
  const calls: { status: number; answer: boolean }[] = [];
  page.on("response", async (r) => {
    if (r.request().method() === "POST" && r.url().endsWith("/api/session/call")) {
      const body = (await r.json().catch(() => null)) as { answer?: { sdp?: string } | null } | null;
      calls.push({ status: r.status(), answer: !!body?.answer?.sdp });
    }
  });
  const ice = page.waitForResponse((r) => r.url().endsWith("/api/session/ice"));

  await page.goto("/");
  await page.getByRole("button", { name: "Get started" }).click();
  const thread = page.getByTestId("thread");
  await expect(thread).toContainText("what would you like to call me?");
  const composer = page.getByRole("textbox");
  await composer.fill("Juno");
  await composer.press("Enter");

  await page.getByTestId("call-offer").getByRole("button", { name: "Call Juno" }).click();
  expect((await ice).status()).toBe(200);
  const panel = page.getByTestId("call-panel");
  await expect(panel).toHaveAttribute("data-status", /connected|muted/, { timeout: 20_000 });
  expect(calls).toEqual([{ status: 201, answer: true }]); // real SDP answer, not lease-only
  // The brain's opening line (call_started) arrives as a voice transcript -> caption.
  await expect(panel.getByLabel("Live captions")).toContainText(/Persona|call/i, { timeout: 15_000 });
  // FakeLlm phrases call_started and call_ended alike: once on voice now, once in chat after.
  const ask = thread.getByText("And what should I call you?", { exact: false });
  await expect(ask).toHaveCount(1);

  await panel.getByRole("button", { name: "End" }).click();
  await expect(panel).toHaveAttribute("data-status", "ended");
  // Hangup released the lease and the brain's call_ended turn resumed the chat.
  await expect(ask).toHaveCount(2, { timeout: 15_000 });
  const state = await page.evaluate(async () => (await (await fetch("/api/session", { cache: "no-store" })).json()) as { state?: { call?: { live?: boolean } } });
  expect(state.state?.call?.live).toBe(false);
});
