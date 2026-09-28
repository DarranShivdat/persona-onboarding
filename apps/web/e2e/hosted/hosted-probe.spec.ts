import { expect, test } from "@playwright/test";
import { HOSTED_AGENT, HOSTED_WEB, SKIP_REASON } from "./hosted";

// HOSTED-001: browser-level regression gate for the live deploy (mirrors scripts/deploy/smoke.sh).
// Read-mostly: one throwaway onboarding session (the UI's Get started), one text turn, the ICE
// route, and the OAuth start redirect — which is aborted at accounts.google.com, never logged in.
// Never prints tokens, cookies, or the Google redirect URL's query.
test.skip(!HOSTED_WEB, SKIP_REASON);
test.describe.configure({ mode: "serial" });

test("agent /health is ok with db ok", async ({ request }) => {
  test.skip(!HOSTED_AGENT, "PERSONA_E2E_HOSTED_AGENT_URL unset");
  const r = await request.get(`${HOSTED_AGENT}/health`);
  expect(r.status()).toBe(200);
  const body = (await r.json()) as { ok?: boolean; db?: string };
  expect(body.ok).toBe(true);
  expect(body.db).toBe("ok");
});

test("public pages: /, /about, /privacy are 200 with expected titles and landmarks", async ({ page }) => {
  let res = await page.goto("/");
  expect(res?.status()).toBe(200);
  await expect(page).toHaveTitle("Persona — set up your assistant");
  await expect(page.getByRole("button", { name: "Get started" })).toBeVisible();

  res = await page.goto("/about");
  expect(res?.status()).toBe(200);
  await expect(page).toHaveTitle("Persona — your assistant that gets things done");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("assistant");
  await expect(page.getByRole("link", { name: "Privacy Policy" }).first()).toHaveAttribute("href", "/privacy");

  res = await page.goto("/privacy");
  expect(res?.status()).toBe(200);
  await expect(page).toHaveTitle("Privacy Policy — Persona setup");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Privacy Policy");
  await expect(page.getByText(/Limited Use requirements/)).toBeVisible();
});

test("one session: create, one text turn advances the UI, ICE has TURN, OAuth start heads to Google", async ({ page, context }) => {
  await context.clearCookies();

  // 1. Session create through the web proxy (Get started) — the single throwaway session.
  await page.goto("/");
  const created = page.waitForResponse((r) => r.request().method() === "POST" && new URL(r.url()).pathname === "/api/session");
  await page.getByRole("button", { name: "Get started" }).click();
  expect([200, 201]).toContain((await created).status());
  expect((await context.cookies()).some((c) => c.name === "persona_session")).toBe(true);
  const thread = page.getByTestId("thread");
  await expect(thread.locator('[data-from="agent"]').first()).toBeVisible({ timeout: 20_000 });

  // 2. One text turn via /api/session/turns; the real brain extracts the name → checklist fills.
  const turn = page.waitForResponse((r) => r.request().method() === "POST" && new URL(r.url()).pathname === "/api/session/turns", { timeout: 45_000 });
  const composer = page.getByRole("textbox");
  await composer.fill("Juno");
  await composer.press("Enter");
  expect((await turn).status()).toBe(200);
  await expect(thread.locator('[data-from="user"]').filter({ hasText: "Juno" })).toBeVisible();
  const agentName = page.getByTestId("checklist-desk").locator('[data-slot="agent_name"]');
  await expect(agentName).toHaveClass(/\bdone\b/, { timeout: 30_000 });
  await expect(agentName).toHaveAttribute("aria-label", "Assistant name: Juno");

  // 3. ICE route (same cookie): at least one TURN URL, or SKIP-annotate when TURN is absent.
  const ice = await page.request.get("/api/session/ice");
  expect(ice.status()).toBe(200);
  const iceBody = (await ice.json()) as { iceServers?: { urls: string | string[] }[]; ice_servers?: { urls: string | string[] }[] };
  const urls = (iceBody.iceServers ?? iceBody.ice_servers ?? []).flatMap((s) => (Array.isArray(s.urls) ? s.urls : [s.urls]));
  expect(urls.length).toBeGreaterThan(0);
  const turnUrls = urls.filter((u) => /^turns?:/.test(u));
  if (process.env.PERSONA_E2E_HOSTED_REQUIRE_TURN === "0" && turnUrls.length === 0) {
    test.info().annotations.push({ type: "SKIP", description: "ICE has no TURN URL (allowed by PERSONA_E2E_HOSTED_REQUIRE_TURN=0)" });
  } else {
    expect(turnUrls.length, "ICE must include a turn:/turns: URL").toBeGreaterThan(0);
  }

  // 4. OAuth start heads to accounts.google.com. Read the 302 Location with the session cookie
  // and never follow it (Playwright routes can't intercept redirect hops, so no page.goto).
  const start = await page.request.get("/api/oauth/google/start", { maxRedirects: 0 });
  expect(start.status(), "OAuth start must redirect (oauth_unconfigured / no_session render 200)").toBe(302);
  const g = new URL(start.headers()["location"] ?? "about:blank");
  expect(g.origin).toBe("https://accounts.google.com");
  expect(g.searchParams.get("client_id")).toBeTruthy();
  expect(g.searchParams.get("redirect_uri")).toBe(`${HOSTED_WEB}/api/oauth/google/callback`);
  expect(g.searchParams.get("scope") ?? "").toContain("gmail");
});
