import { expect, type Browser, type BrowserContext, type Locator, type Page, type TestInfo } from "@playwright/test";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

// AUDIT-001 core: enumerate every control on a screen, demand an expectation for each, run
// generic checks (accessible name, 44px tap target on mobile, visible focus ring, disabled
// semantics, hrefs resolve) and each control's action on a fresh copy of the screen.

export type Target = "local" | "live";
export const TARGET: Target = process.env.PERSONA_AUDIT_TARGET === "live" ? "live" : "local";
/** Repo root: walk up from the cwd (apps/web under npm -w) to the dir holding harness/qa.mjs. */
export const REPO = (() => {
  let d = process.cwd();
  while (!existsSync(join(d, "harness/qa.mjs")) && dirname(d) !== d) d = dirname(d);
  return d;
})();
export const OUT = join(REPO, ".persona-qa/audit");
export const DOCS = process.env.PERSONA_AUDIT_DOCS === "1";
export const SHOTS = DOCS ? join(REPO, "docs/qa/button-audit") : join(OUT, "shots");

export type Result = "PASS" | "FAIL" | "FIXME" | "SKIP" | "BLOCKED";

/** Vercel's bot protection ("We're verifying your browser") served instead of the app (LIVE). */
export const CHECKPOINT = "Vercel Security Checkpoint";
export async function checkpoint(page: Page): Promise<boolean> {
  return (await page.getByText(CHECKPOINT).count().catch(() => 0)) > 0;
}
export interface Row {
  target: Target;
  viewport: string;
  screen: string;
  control: string;
  expected: string;
  result: Result;
  note: string;
}

/** One enumerated control, as seen in the page. */
export interface Ctl {
  idx: number;
  key: string; // `${kind}:${name} @${zone}` (+ `#n` for repeats)
  kind: string;
  name: string;
  zone: string;
  disabled: boolean;
  ariaDisabled: boolean;
  cursor: string;
  href: string | null;
  inline: boolean;
  w: number;
  h: number;
}

export interface Ctx {
  page: Page;
  context: BrowserContext;
  browser: Browser;
  baseURL: string;
  target: Target;
  mobile: boolean;
  /** Run on teardown of this copy of the screen (bot pages, routes). */
  cleanup: (fn: () => Promise<unknown>) => void;
  /** Free-form data a setup hands to the actions (e.g. the seeded session id). */
  data: Record<string, unknown>;
}

export interface Expectation {
  /** Control key (exact) or pattern. */
  match: string | RegExp;
  expected: string;
  /** Exercise the control on a fresh copy of the screen; throw / fail an expect on error. */
  run?: (ctl: Locator, ctx: Ctx) => Promise<void>;
  /** The control must be disabled (native `disabled` or aria-disabled) with no pointer cursor. */
  disabled?: boolean;
  /** Control may legitimately be absent on this screen. */
  optional?: boolean;
  /** Known failure owned elsewhere: run as test.fixme-style (recorded FIXME, not failing). */
  fixme?: string;
  /** Action not exercised on LIVE (read-only guard); generic checks still run. */
  skipLive?: string;
}

export interface Screen {
  name: string;
  title: string;
  targets?: Target[];
  viewports?: ("desktop" | "mobile")[];
  setup: (ctx: Ctx) => Promise<void>;
  controls: Expectation[];
  /** Exercise actions in order on the setup page instead of a fresh copy each (LIVE call). */
  sequential?: boolean;
  /** Extra screen-level assertions after setup (e.g. captions region, 404 status). */
  check?: (ctx: Ctx) => Promise<void>;
}

const CONTROL_SEL = 'button, a[href], input:not([type="hidden"]), textarea, select, [role="button"], [role="checkbox"], [role="link"], [contenteditable="true"]';

/** Enumerate visible controls in DOM order; tags each with data-audit-idx. */
export async function enumerate(page: Page): Promise<Ctl[]> {
  return page.evaluate((sel) => {
    const name = (el: HTMLElement): string => {
      const l = el.getAttribute("aria-label");
      if (l?.trim()) return l.trim();
      const lb = el.getAttribute("aria-labelledby");
      if (lb) return lb.split(/\s+/).map((id) => document.getElementById(id)?.textContent ?? "").join(" ").trim();
      const labels = (el as HTMLInputElement).labels;
      if (labels?.length) return [...labels].map((x) => x.textContent ?? "").join(" ").trim();
      const t = (el.innerText ?? "").replace(/\s+/g, " ").trim();
      if (t) return t;
      return (el.getAttribute("title") ?? "").trim();
    };
    const kind = (el: HTMLElement): string => {
      if (el.closest(".chips")) return "chip";
      const tag = el.tagName.toLowerCase();
      if (tag === "a" || el.getAttribute("role") === "link") return "link";
      if (tag === "textarea" || (tag === "input" && !["checkbox", "radio", "button", "submit"].includes((el as HTMLInputElement).type))) return "textbox";
      if ((el as HTMLInputElement).type === "checkbox" || el.getAttribute("role") === "checkbox") return "checkbox";
      return "button";
    };
    const zone = (el: HTMLElement): string => {
      const p = el.parentElement;
      const z = p?.closest<HTMLElement>("[data-testid], form.composer, header, footer, main");
      if (!z) return "page";
      if (z.dataset.testid) return z.dataset.testid;
      return z.matches("form.composer") ? "composer" : z.tagName.toLowerCase();
    };
    const out: unknown[] = [];
    const seen = new Map<string, number>();
    let idx = 0;
    for (const el of document.querySelectorAll<HTMLElement>(sel)) {
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      if (r.width === 0 || r.height === 0 || cs.visibility === "hidden" || !el.checkVisibility({ opacityProperty: false })) continue;
      if (el.closest("[aria-hidden='true']")) continue;
      const k = kind(el);
      const n = name(el);
      const z = zone(el);
      let key = `${k}:${n} @${z}`;
      const c = (seen.get(key) ?? 0) + 1;
      seen.set(key, c);
      if (c > 1) key += `#${c}`;
      el.dataset.auditIdx = String(idx);
      const block = el.parentElement?.closest("p, li");
      const inline = el.tagName === "A" && !!block && (block.textContent ?? "").trim().length > (el.textContent ?? "").trim().length + 1;
      out.push({
        idx: idx++,
        key,
        kind: k,
        name: n,
        zone: z,
        disabled: (el as HTMLButtonElement).disabled === true,
        ariaDisabled: el.getAttribute("aria-disabled") === "true",
        cursor: cs.cursor,
        href: el.tagName === "A" ? el.getAttribute("href") : null,
        inline,
        w: Math.round(r.width),
        h: Math.round(r.height),
      });
    }
    return out;
  }, CONTROL_SEL) as Promise<Ctl[]>;
}

export const byIdx = (page: Page, c: Ctl) => page.locator(`[data-audit-idx="${c.idx}"]`);

const matches = (e: Expectation, key: string) => (typeof e.match === "string" ? e.match === key : e.match.test(key));

/** Find the control for `key` on a (fresh) page; patterns fall back to their first match. */
export async function find(page: Page, key: string, e: Expectation): Promise<Locator> {
  const ctls = await enumerate(page);
  const c = ctls.find((x) => x.key === key) ?? ctls.find((x) => matches(e, x.key));
  if (!c) throw new Error(`control ${key} not found on a fresh copy of the screen (have: ${ctls.map((x) => x.key).join(" | ")})`);
  return byIdx(page, c);
}

/** Is the focus indicator visible? Compares focused vs blurred outline/box-shadow/border. */
async function focusRing(page: Page, loc: Locator): Promise<string | null> {
  return loc.evaluate(async (el: HTMLElement) => {
    const snap = () => {
      const s = getComputedStyle(el);
      return { o: s.outlineStyle !== "none" && parseFloat(s.outlineWidth) >= 1 ? `${s.outlineStyle} ${s.outlineWidth}` : "", sh: s.boxShadow, b: s.borderColor };
    };
    el.blur();
    const before = snap();
    el.focus({ preventScroll: true });
    await new Promise((r) => requestAnimationFrame(() => r(null)));
    const after = snap();
    const focused = document.activeElement === el;
    el.blur();
    if (!focused) return "cannot receive focus";
    if (after.o) return null;
    if (after.sh !== before.sh && after.sh !== "none") return null;
    if (after.b !== before.b) return null;
    return "no visible focus indicator";
  });
}

/** Generic checks for one control. Returns failure notes (empty = pass) and info notes. */
export async function generic(page: Page, c: Ctl, e: Expectation | undefined, ctx: Ctx): Promise<{ fails: string[]; info: string[] }> {
  const fails: string[] = [];
  const info: string[] = [];
  const loc = byIdx(page, c);
  if (!c.name) fails.push("no accessible name");
  const isDisabled = c.disabled || c.ariaDisabled;
  if (e?.disabled) {
    if (!isDisabled) fails.push("expected disabled");
    if (c.cursor === "pointer") fails.push("disabled but cursor:pointer");
  } else if (isDisabled) {
    fails.push("unexpectedly disabled");
  }
  if (ctx.mobile && !isDisabled && !c.inline && (c.w < 44 || c.h < 44)) fails.push(`tap target ${c.w}x${c.h} < 44px`);
  if (!isDisabled) {
    const ring = await focusRing(page, loc);
    if (ring) fails.push(ring);
  }
  if (c.href !== null) {
    const h = c.href;
    if (h.startsWith("mailto:")) {
      if (!/^mailto:[^@\s]+@[^@\s]+\.[a-z]{2,}$/i.test(h)) fails.push(`bad mailto ${h}`);
      else info.push("mailto");
    } else {
      const url = new URL(h, page.url());
      const external = url.origin !== new URL(ctx.baseURL).origin;
      if (external && ctx.target === "local") {
        info.push(`external ${url.host} (checked on LIVE)`);
      } else {
        const r = await page.request.get(url.toString(), { maxRedirects: 5, failOnStatusCode: false, timeout: 20_000 }).catch((err: Error) => ({ status: () => 0, err }));
        const st = r.status();
        // Some sites answer bots with 403/429; that is not a dead link.
        if (st === 0 || st === 404 || st >= 500) fails.push(`href ${url.pathname} -> HTTP ${st}`);
        else info.push(`href ${external ? url.host : url.pathname} ${st}`);
      }
    }
  }
  return { fails, info };
}

export function writeRows(info: TestInfo, screen: string, rows: Row[]) {
  const dir = join(OUT, "rows");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, `${TARGET}-${info.project.name}-${screen}.json`), JSON.stringify(rows, null, 1));
}

export async function shot(page: Page, info: TestInfo, screen: string) {
  mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: join(SHOTS, `${TARGET}-${info.project.name}-${screen}.png`), animations: "disabled", caret: "hide", scale: "css" });
}

// ---- shared action helpers --------------------------------------------------------------

export const thread = (p: Page) => p.getByTestId("thread");
export const userBubble = (p: Page, text: string) => thread(p).locator('[data-from="user"]').filter({ hasText: text });
export const composerField = (p: Page) => p.locator("form.composer textarea");

/** The user's words land in the thread (and, on the live driver, a turn is posted). */
export async function expectSent(p: Page, text: string, act: () => Promise<void>) {
  await act();
  await expect(userBubble(p, text).last()).toBeVisible({ timeout: 15_000 });
}

export async function typeAndSend(p: Page, text: string, via: "enter" | "button") {
  const f = composerField(p);
  await f.fill(text);
  if (via === "enter") await f.press("Enter");
  else await p.locator("form.composer").getByRole("button", { name: "Send" }).click();
  await expect(userBubble(p, text).last()).toBeVisible({ timeout: 15_000 });
  await expect(f).toHaveValue("");
}

export async function expectFocused(p: Page, loc: Locator) {
  await expect.poll(() => loc.evaluate((el) => document.activeElement === el)).toBe(true);
}

export { expect };
