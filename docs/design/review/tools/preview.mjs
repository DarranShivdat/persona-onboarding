// DESIGN-002: preview proposed CSS fixes by injecting them into the running app (no apps/ edits).
import { chromium } from "playwright";
const url = "http://localhost:3100";
const OUT = new URL("../shots/", import.meta.url).pathname;
const b = await chromium.launch();
const shot = async (vp, path, css, file, clip, act) => {
  const p = await b.newPage({ viewport: vp });
  await p.goto(`${url}/?state=${path}&capture=1`, { waitUntil: "networkidle" });
  if (css) await p.addStyleTag({ content: css });
  if (act) await act(p);
  await p.waitForTimeout(200);
  await p.screenshot({ path: OUT + file, clip });
  await p.close();
};
const D = { width: 1440, height: 900 }, M = { width: 390, height: 844 };
const focus = async (p) => { await p.locator("textarea.field").fill("Maya"); await p.keyboard.press("Shift+Tab"); await p.keyboard.press("Tab"); };
await shot(D, "call-declined", null, "taste-A-composer-ring@desktop.png", { x: 1840 / 2 - 500, y: 800, width: 700, height: 100 }, focus);
await shot(D, "call-declined", ".composer .field:focus-visible{outline:none;border-color:var(--blue);box-shadow:0 0 0 1px var(--blue)}", "taste-B-composer-border@desktop.png", { x: 1840 / 2 - 500, y: 800, width: 700, height: 100 }, focus);
await shot(M, "gmail-card-wrong-account", null, "noop.png", { x: 0, y: 0, width: 10, height: 10 });
const fix01 = ".gcard li{grid-template-columns:5.5em 1fr;column-gap:8px}";
const idle = async (p) => { await p.evaluate(() => document.querySelector(".gcard")?.classList.remove("in-call")); };
await shot(M, "gmail-card-idle", fix01, "fix-DQ01-gmail-list@mobile.png", { x: 0, y: 420, width: 390, height: 200 }, idle);
await shot(M, "landing", ".landing h1{text-wrap:balance}.landing .lede{text-wrap:pretty}", "fix-DQ06-landing@mobile.png");
await shot(D, "landing", ".landing h1{text-wrap:balance}.landing .lede{text-wrap:pretty;max-width:32ch}", "fix-DQ06-landing@desktop.png");
await b.close();
