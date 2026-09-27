#!/usr/bin/env node
// Pixel diff of captured states vs docs/design/mockups (pixelmatch, masks from capture).
//   node harness/visual/diff.mjs [--actual <dir>] [--expected docs/design/mockups]
// Writes .persona-qa/visual/report.json, per-state diff PNGs and contact sheets
// (expected | actual | diff) per viewport.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import pixelmatch from "pixelmatch";
import { PNG } from "pngjs";
import { EXTRA, GATED, THRESHOLD, VIEWPORTS } from "./states.mjs";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const OUT = join(ROOT, ".persona-qa/visual");
const MASK_FILL = [255, 0, 255, 255];

const readPng = (p) => PNG.sync.read(readFileSync(p));

function fit(img, w, h) {
  // Pad/crop to the expected size (a size mismatch counts as mismatched pixels).
  if (img.width === w && img.height === h) return img;
  const o = new PNG({ width: w, height: h });
  o.data.fill(0);
  for (let y = 0; y < Math.min(h, img.height); y++)
    img.data.copy(o.data, y * w * 4, y * img.width * 4, y * img.width * 4 + Math.min(w, img.width) * 4);
  return o;
}

function applyMasks(img, rects) {
  let px = 0;
  for (const r of rects) {
    const x0 = Math.max(0, Math.floor(r.x)), y0 = Math.max(0, Math.floor(r.y));
    const x1 = Math.min(img.width, Math.ceil(r.x + r.w)), y1 = Math.min(img.height, Math.ceil(r.y + r.h));
    for (let y = y0; y < y1; y++)
      for (let x = x0; x < x1; x++) {
        const i = (y * img.width + x) * 4;
        if (img.data[i] === MASK_FILL[0] && img.data[i + 1] === MASK_FILL[1] && img.data[i + 2] === MASK_FILL[2]) continue;
        img.data.set(MASK_FILL, i);
        px++;
      }
  }
  return px;
}

function downscale(img, f) {
  const w = Math.floor(img.width / f), h = Math.floor(img.height / f);
  const o = new PNG({ width: w, height: h });
  for (let y = 0; y < h; y++)
    for (let x = 0; x < w; x++) {
      const acc = [0, 0, 0, 0];
      for (let dy = 0; dy < f; dy++)
        for (let dx = 0; dx < f; dx++) {
          const i = ((y * f + dy) * img.width + (x * f + dx)) * 4;
          for (let c = 0; c < 4; c++) acc[c] += img.data[i + c];
        }
      o.data.set(acc.map((v) => v / (f * f)), (y * w + x) * 4);
    }
  return o;
}

function contactSheet(rows, file) {
  if (!rows.length) return;
  const cw = rows[0][0].width, ch = rows[0][0].height, gap = 8;
  const W = cw * 3 + gap * 4, H = rows.length * (ch + gap) + gap;
  const sheet = new PNG({ width: W, height: H });
  sheet.data.fill(255);
  rows.forEach((imgs, r) =>
    imgs.forEach((img, c) => PNG.bitblt(img, sheet, 0, 0, img.width, img.height, gap + c * (cw + gap), gap + r * (ch + gap))),
  );
  writeFileSync(file, PNG.sync.write(sheet));
}

export function diff({ actualDir, expectedDir = join(ROOT, "docs/design/mockups"), masks, buildSha = null, url = null }) {
  mkdirSync(join(OUT, "diff"), { recursive: true });
  if (!masks && existsSync(join(actualDir, "masks.json"))) {
    const m = JSON.parse(readFileSync(join(actualDir, "masks.json"), "utf8"));
    masks = m.masks;
    buildSha ??= m.buildSha;
    url ??= m.url;
  }
  const results = [];
  const sheets = { desktop: [], mobile: [] };
  for (const vp of Object.keys(VIEWPORTS)) {
    for (const state of [...GATED, ...EXTRA]) {
      const key = `${state}@${vp}`;
      const expP = join(expectedDir, `${key}.png`), actP = join(actualDir, `${key}.png`);
      const gated = GATED.includes(state);
      if (!existsSync(actP) || !existsSync(expP)) {
        results.push({ state, viewport: vp, gated, status: "missing", mismatch: 1 });
        continue;
      }
      const exp = readPng(expP);
      const act = fit(readPng(actP), exp.width, exp.height);
      const rects = masks?.[key] ?? [];
      applyMasks(exp, rects);
      const masked = applyMasks(act, rects);
      const d = new PNG({ width: exp.width, height: exp.height });
      const bad = pixelmatch(exp.data, act.data, d.data, exp.width, exp.height, { threshold: 0.1 });
      const total = exp.width * exp.height - masked;
      const mismatch = bad / Math.max(1, total);
      writeFileSync(join(OUT, "diff", `${key}.png`), PNG.sync.write(d));
      const f = vp === "desktop" ? 4 : 2;
      sheets[vp].push([downscale(exp, f), downscale(act, f), downscale(d, f)]);
      results.push({
        state,
        viewport: vp,
        gated,
        mismatch: Number(mismatch.toFixed(5)),
        mismatchedPixels: bad,
        maskedPixels: masked,
        masks: rects.map((r) => r.kind),
        status: mismatch <= THRESHOLD ? "pass" : gated ? "fail" : "over-threshold (not gated)",
      });
    }
  }
  contactSheet(sheets.desktop, join(OUT, "contact-sheet@desktop.png"));
  contactSheet(sheets.mobile, join(OUT, "contact-sheet@mobile.png"));
  const failed = results.filter((r) => r.gated && r.status !== "pass");
  const report = {
    ok: failed.length === 0,
    threshold: THRESHOLD,
    buildSha,
    url,
    at: new Date().toISOString(),
    gatedStates: GATED.length,
    viewports: Object.keys(VIEWPORTS),
    contactSheets: ["contact-sheet@desktop.png", "contact-sheet@mobile.png"],
    results,
  };
  writeFileSync(join(OUT, "report.json"), JSON.stringify(report, null, 2) + "\n");
  for (const r of results)
    console.log(`  ${r.status === "pass" ? "ok  " : r.gated ? "FAIL" : "info"} ${`${r.state}@${r.viewport}`.padEnd(34)} ${(r.mismatch * 100).toFixed(2)}%${r.gated ? "" : "  (extra)"}`);
  const worst = Math.max(...results.filter((r) => r.gated).map((r) => r.mismatch));
  console.log(
    `[qa:visual] ${report.ok ? "PASS" : "FAIL"} ${GATED.length} gated states x ${report.viewports.length} viewports, worst ${(worst * 100).toFixed(2)}% (limit ${THRESHOLD * 100}%) -> .persona-qa/visual/report.json`,
  );
  return report;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const a = process.argv.slice(2);
  const opt = (k, d) => (a.includes(k) ? a[a.indexOf(k) + 1] : d);
  const r = diff({ actualDir: resolve(ROOT, opt("--actual", ".persona-qa/visual/actual")), expectedDir: resolve(ROOT, opt("--expected", "docs/design/mockups")) });
  process.exit(r.ok ? 0 : 1);
}
