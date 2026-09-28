#!/usr/bin/env node
// DESIGN-002: screenshot every fixture state + a live stub-agent walk, and record layout metrics.
//   node docs/design/review/tools/shoot.mjs [--url http://localhost:3100]
import { writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(HERE, "../shots");
const url = (process.argv.includes("--url") ? process.argv[process.argv.indexOf("--url") + 1] : "http://localhost:3100").replace(/\/$/, "");
const VP = { desktop: [1440, 900], mobile: [390, 844] };
const STATES = [
  "landing", "chat-agent-name", "call-offer", "call-declined", "call-ringing", "call-connected", "call-muted",
  "call-reconnecting", "call-ended", "call-elsewhere", "mic-denied", "gmail-card-idle", "gmail-card-connecting",
  "gmail-card-connected", "gmail-card-error", "gmail-card-wrong-account", "gmail-card-partial", "graduation", "welcome-back",
];

const metrics = async (page) =>
  page.evaluate(() => {
    const r = (s) => document.querySelector(s)?.getBoundingClientRect();
    const box = (b) => (b ? { top: Math.round(b.top), bottom: Math.round(b.bottom), h: Math.round(b.height), w: Math.round(b.width) } : null);
    const ctrls = [...document.querySelectorAll(".rail .ctl")].map((c) => box(c.getBoundingClientRect()));
    return {
      hOverflow: document.documentElement.scrollWidth > innerWidth,
      rail: box(r(".rail")), composer: box(r(".composer")), gcard: box(r(".gcard")), thread: box(r(".thread")),
      threadScrollable: (() => { const t = document.querySelector(".thread"); return t ? t.scrollHeight > t.clientHeight + 1 : null; })(),
      ctrls,
      fonts: [...new Set([...document.querySelectorAll("h1,h3,h4,.msg,.btn,.check")].map((e) => getComputedStyle(e).fontFamily.split(",")[0]))],
    };
  });

const browser = await chromium.launch({ headless: true });
const report = {};
for (const [vp, [width, height]] of Object.entries(VP)) {
  const ctx = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 1, reducedMotion: "reduce" });
  const page = await ctx.newPage();
  for (const s of STATES) {
    await page.goto(`${url}/?state=${s}&capture=1`, { waitUntil: "networkidle" });
    await page.waitForTimeout(250);
    await page.screenshot({ path: join(OUT, `${s}@${vp}.png`) });
    report[`${s}@${vp}`] = await metrics(page);
  }
  // Live walk against the stub agent (real ApiSessionDriver + proxy).
  const live = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 1, reducedMotion: "reduce" });
  const lp = await live.newPage();
  const snap = async (n) => { await lp.waitForTimeout(700); await lp.screenshot({ path: join(OUT, `live-${n}@${vp}.png`) }); report[`live-${n}@${vp}`] = await metrics(lp); };
  await lp.goto(url, { waitUntil: "networkidle" });
  await snap("1-landing");
  await lp.getByRole("button", { name: "Get started" }).click();
  await snap("2-greeting");
  const say = async (t) => { await lp.locator("textarea.field").fill(t); await lp.keyboard.press("Enter"); };
  await say("idk");
  await snap("3-agentname-idk");
  await say("Juno");
  await snap("4-call-offer");
  const keep = lp.getByRole("button", { name: /keep texting/i });
  if (await keep.count()) await keep.first().click();
  await snap("5-keep-texting");
  await say("Maya");
  await snap("6-user-name");
  await say("what do you do with my email?");
  await snap("7-privacy");
  await say("help me get my inbox under control");
  await snap("8-gmail-ask");
  await lp.focus("textarea.field");
  await lp.keyboard.press("Shift+Tab");
  await snap("9-focus-ring");
  await live.close();
  await ctx.close();
}
await browser.close();
writeFileSync(join(OUT, "metrics.json"), JSON.stringify(report, null, 1));
console.log(`wrote ${Object.keys(report).length} shots to ${OUT}`);
