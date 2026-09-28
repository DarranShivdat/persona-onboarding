import { expect, test, type Page } from "@playwright/test";

// FONT-001 / RING-002 / MUTE-002 (live test 2026-09-28), on the `?state=` fixtures (no network).

async function bubbleFonts(page: Page, state: string) {
  await page.goto(`/?state=${state}&capture=1`);
  await expect(page.locator(".thread .msg").first()).toBeVisible();
  return page.locator(".thread .msg").evaluateAll((els) =>
    els.map((el) => {
      const cs = getComputedStyle(el);
      return { kind: `${el.getAttribute("data-from")}${el.classList.contains("voice") ? "-voice" : ""}`, size: cs.fontSize, line: cs.lineHeight };
    }),
  );
}

test("FONT-001: every chat bubble variant has the same computed font-size and line-height", async ({ page }) => {
  const all = [
    ...(await bubbleFonts(page, "call-connected")),   // text agent + voice agent + voice user
    ...(await bubbleFonts(page, "welcome-back")),     // resume + text user
    ...(await bubbleFonts(page, "graduation")),
  ];
  const kinds = new Set(all.map((b) => b.kind));
  for (const k of ["agent", "agent-voice", "user-voice"]) expect(kinds, `fixtures cover ${k}`).toContain(k);
  expect(new Set(all.map((b) => b.size)), JSON.stringify(all)).toEqual(new Set(["17px"]));
  expect(new Set(all.map((b) => b.line)).size, JSON.stringify(all)).toBe(1);
});

async function callBoxes(page: Page, state: string) {
  await page.goto(`/?state=${state}&capture=1`);
  const ring = await page.locator(".device .stage .ring").boundingBox();
  const caps = await page.locator(".device .captions").boundingBox();
  return { ring: ring!, caps: caps! };
}

test("RING-002 + MUTE-002: smaller call ring; the muted badge overlays without moving ring or captions", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  const live = await callBoxes(page, "call-connected");
  expect(Math.round(live.ring.width)).toBeGreaterThanOrEqual(118);   // 150 -> ~124 (15-20% smaller)
  expect(Math.round(live.ring.width)).toBeLessThanOrEqual(128);
  const muted = await callBoxes(page, "call-muted");
  await expect(page.locator(".device .badge")).toHaveText(/muted/);
  expect(await page.locator(".device .badge").evaluate((el) => getComputedStyle(el).position)).toBe("absolute");
  expect(Math.abs(muted.ring.y - live.ring.y)).toBeLessThan(1);
  expect(Math.abs(muted.caps.y - live.caps.y)).toBeLessThan(1);
});
