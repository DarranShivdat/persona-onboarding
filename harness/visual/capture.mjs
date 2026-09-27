#!/usr/bin/env node
// qa:visual — capture every spec state from the running web app (mock driver, ?state=<name>)
// at 1440x900 and 390x844, then diff against docs/design/mockups (diff.mjs).
//   node harness/visual/capture.mjs [--url http://localhost:3000] [--out <dir>] [--states a,b] [--no-diff]
// Masks: elements marked data-mask (call timer, captions, ring/waveform) are recorded per
// state and blanked in both images before pixelmatch.
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { EXTRA, GATED, VIEWPORTS } from "./states.mjs";
import { diff } from "./diff.mjs";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const args = process.argv.slice(2);
const opt = (k, d) => (args.includes(k) ? args[args.indexOf(k) + 1] : d);
const url = opt("--url", process.env.PERSONA_WEB_URL || "http://localhost:3000").replace(/\/$/, "");
const out = resolve(ROOT, opt("--out", ".persona-qa/visual/actual"));
const states = opt("--states", [...GATED, ...EXTRA].join(",")).split(",");

async function reachable() {
  try {
    const r = await fetch(url, { signal: AbortSignal.timeout(5000) });
    return r.ok;
  } catch {
    return false;
  }
}

if (!(await reachable())) {
  console.log(`[qa:visual] FAIL — web app not reachable at ${url}. Start it: npm -w apps/web run build && npm -w apps/web run start`);
  process.exit(1);
}

mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ headless: true });
const masks = {};
let buildSha = null;
try {
  for (const [vp, [width, height]] of Object.entries(VIEWPORTS)) {
    const ctx = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 1, reducedMotion: "reduce" });
    const page = await ctx.newPage();
    for (const s of states) {
      await page.goto(`${url}/?state=${encodeURIComponent(s)}&capture=1`, { waitUntil: "networkidle" });
      await page.evaluate(() => document.fonts.ready);
      await page.waitForTimeout(150);
      buildSha ??= await page.locator('meta[name="build-sha"]').getAttribute("content");
      masks[`${s}@${vp}`] = await page.$$eval("[data-mask]", (els) =>
        els.map((e) => {
          const r = e.getBoundingClientRect();
          return { kind: e.getAttribute("data-mask"), x: r.x, y: r.y, w: r.width, h: r.height };
        }),
      );
      await page.screenshot({ path: join(out, `${s}@${vp}.png`), animations: "disabled", caret: "hide" });
    }
    await ctx.close();
  }
} finally {
  await browser.close();
}
writeFileSync(join(out, "masks.json"), JSON.stringify({ url, buildSha, masks }, null, 2) + "\n");
console.log(`[qa:visual] captured ${states.length} states x ${Object.keys(VIEWPORTS).length} viewports -> ${out} (build-sha ${buildSha})`);

if (!args.includes("--no-diff")) {
  const { ok } = diff({ actualDir: out, masks, buildSha, url });
  process.exit(ok ? 0 : 1);
}
