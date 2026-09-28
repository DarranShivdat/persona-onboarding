import { test } from "@playwright/test";
import { TARGET, byIdx, enumerate, expect, find, generic, shot, writeRows, type Ctx, type Expectation, type Row } from "./audit";
import { SCREENS } from "./screens";

// AUDIT-001: for every screen x viewport, every visible control must have an expectation and
// pass it. Unknown controls FAIL (a new button needs an audit entry). `fixme` expectations
// (GRAD-001-owned) are still exercised: a failure is recorded FIXME, a pass is recorded PASS.
const LIVE = TARGET === "live";

for (const screen of SCREENS) {
  test(`${screen.name}: every control does something correct`, async ({ page, context, browser, baseURL }, info) => {
    const viewport = info.project.name as "desktop" | "mobile";
    test.skip(!!screen.targets && !screen.targets.includes(TARGET), `${TARGET} target not applicable`);
    test.skip(!!screen.viewports && !screen.viewports.includes(viewport), `${viewport} not applicable`);
    const mobile = viewport === "mobile";
    const rows: Row[] = [];
    const row = (control: string, expected: string, result: Row["result"], note = "") => rows.push({ target: TARGET, viewport, screen: screen.name, control, expected, result, note });

    const mkCtx = (p: typeof page): Ctx & { done: () => Promise<void> } => {
      const fns: (() => Promise<unknown>)[] = [];
      return {
        page: p, context, browser, baseURL: baseURL!, target: TARGET, mobile, data: {},
        cleanup: (fn) => void fns.push(fn),
        done: async () => { for (const fn of fns.reverse()) await fn().catch(() => null); },
      };
    };

    const ctx = mkCtx(page);
    try {
      await screen.setup(ctx);
    } catch (e) {
      row("(screen setup)", screen.title, "FAIL", String((e as Error).message).replace(/\x1b\[[0-9;]*m/g, "").split("\n")[0]);
      writeRows(info, screen.name, rows);
      throw e;
    }
    await page.keyboard.press("Tab").catch(() => null); // keyboard modality -> :focus-visible applies to .focus()
    await page.mouse.move(0, 0);
    const shotP = shot(page, info, screen.name);
    await shotP;
    if (screen.check) {
      try {
        await screen.check(ctx);
        row("(screen)", screen.title, "PASS");
      } catch (e) {
        row("(screen)", screen.title, "FAIL", String((e as Error).message).replace(/\x1b\[[0-9;]*m/g, "").split("\n")[0]);
      }
    }

    const ctls = await enumerate(page);
    const pick = (key: string) => screen.controls.find((e) => (typeof e.match === "string" ? e.match === key : e.match.test(key)));
    const used = new Set<Expectation>();
    const todo: { key: string; e: Expectation; fails: string[]; info: string[] }[] = [];
    for (const c of ctls) {
      const e = pick(c.key);
      const g = await generic(page, c, e, ctx);
      if (!e) {
        row(c.key, "(none)", "FAIL", `no expectation: new control? ${[...g.fails, ...g.info].join("; ")}`.trim());
        continue;
      }
      used.add(e);
      todo.push({ key: c.key, e, ...g });
    }
    for (const e of screen.controls) {
      if (!used.has(e) && !e.optional) row(String(e.match), e.expected, "FAIL", "expected control is missing");
    }

    for (const t of todo) {
      const { key, e } = t;
      let actionErr: string | null = null;
      let skipped: string | null = null;
      if (e.run && LIVE && e.skipLive) skipped = e.skipLive;
      else if (e.run) {
        let run = ctx;
        let fresh: (Ctx & { done: () => Promise<void> }) | null = null;
        try {
          if (!screen.sequential) {
            fresh = mkCtx(await context.newPage());
            await screen.setup(fresh);
            run = fresh;
          }
          const loc = screen.sequential ? byIdx(page, ctls.find((c) => c.key === key)!) : await find(run.page, key, e);
          await e.run(loc, run);
        } catch (err) {
          actionErr = String((err as Error).message).replace(/\x1b\[[0-9;]*m/g, "").replace(/\s+/g, " ").slice(0, 220);
        } finally {
          if (fresh) {
            await fresh.done();
            await fresh.page.close().catch(() => null);
          }
        }
      }
      const fails = [...t.fails, ...(actionErr ? [`action: ${actionErr}`] : [])];
      const notes = [...fails, ...t.info, ...(skipped ? [`action SKIP on LIVE: ${skipped}`] : [])].join("; ");
      if (!fails.length) row(key, e.expected, "PASS", notes);
      else if (e.fixme) row(key, e.expected, "FIXME", `${e.fixme} — ${notes}`);
      else row(key, e.expected, "FAIL", notes);
    }
    await ctx.done();
    writeRows(info, screen.name, rows);

    const failed = rows.filter((r) => r.result === "FAIL");
    expect(failed.map((r) => `${r.control}: ${r.note}`), `${screen.name} (${viewport}) failures`).toEqual([]);
  });
}
