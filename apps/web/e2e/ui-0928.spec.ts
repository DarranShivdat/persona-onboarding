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
    ...(await bubbleFonts(page, "welcome-back")),     // resume line
    ...(await bubbleFonts(page, "call-declined")),    // text user
    ...(await bubbleFonts(page, "gmail-card-wrong-account")),
  ];
  const kinds = new Set(all.map((b) => b.kind));
  for (const k of ["agent", "user", "agent-voice", "user-voice"]) expect(kinds, `fixtures cover ${k}`).toContain(k);
  expect(new Set(all.map((b) => b.size)), JSON.stringify(all)).toEqual(new Set(["17px"]));
  expect(new Set(all.map((b) => b.line)).size, JSON.stringify(all)).toBe(1);
});

async function boxes(page: Page) {
  const ring = await page.locator(".device .stage .ring").boundingBox();
  const caps = await page.locator(".device .captions").boundingBox();
  return { ring: ring!, caps: caps! };
}

test("RING-002 + MUTE-002: smaller call ring; the muted badge overlays without moving ring or captions", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/?state=call-muted&capture=1");
  const badge = page.locator(".device .badge");
  await expect(badge).toHaveText(/muted/);
  const withBadge = await boxes(page);
  expect(Math.round(withBadge.ring.width)).toBeGreaterThanOrEqual(118);   // 150 -> ~124 (15-20% smaller)
  expect(Math.round(withBadge.ring.width)).toBeLessThanOrEqual(128);
  expect(await badge.evaluate((el) => getComputedStyle(el).position)).toBe("absolute");
  await badge.evaluate((el) => ((el as HTMLElement).style.display = "none"));
  const without = await boxes(page);
  expect(Math.abs(withBadge.ring.y - without.ring.y)).toBeLessThan(1);
  expect(Math.abs(withBadge.caps.y - without.caps.y)).toBeLessThan(1);
});
